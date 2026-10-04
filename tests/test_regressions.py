"""One test per bug found in review, so none of them comes back."""

import copy
import csv
import io
import json
import os
import re
import shutil
import subprocess
import sys
import unittest

from scout import export, pipeline, report
from scout.store import Store, opened
from scout.util import ScoutError
from tests.fixtures import nyc_demo as demo
from tests.helpers import EXAMPLE, ROOT, make_config
from tests.test_pipeline import ASTORIA, BERRY, BROOME as BROOME_ID, E7, FRANKLIN, LIC, NO_ADDRESS, SCAM, PipelineCase


def card(number=0):
    return demo.DAY1["sweeps"]["streeteasy:0:2800-4400"]["cards"][number]


class HandPickedCase(PipelineCase):
    """A run with no searching, fed by hand. Holds no tests of its own, so it is safe to build on."""

    def setUp(self):
        super(HandPickedCase, self).setUp()
        self.agent.call(pipeline.start, False)

    def ingest(self, *cards):
        return self.agent.call(pipeline.ingest, {"source": "manual", "cards": list(cards)})


class HandPicked(HandPickedCase):

    def test_new_post_for_a_delisted_unit_brings_the_unit_back(self):
        self.ingest(card())
        self.agent.call(pipeline.enrich, demo.DAY1["pages"][E7])
        self.agent.call(pipeline.score, demo.DAY1["scores"][E7])
        self.agent.call(pipeline.enrich, {"id": E7, "gone": True})
        self.assertEqual(self.listings()[E7]["status"], "delisted")

        self.clock.tick(14 * 86400 - 13 * 3600)
        self.agent.call(pipeline.start, False)
        self.ingest({"url": demo.CL % ("mnh", "7955555555"), "title": "East Village one bedroom by owner", "rent": 3800, "beds": 1})
        page = copy.deepcopy(demo.DAY1["pages"][E7])
        page["id"] = "craigslist-7955555555"
        page["facts"]["rent_advertised"] = 3800
        reply = self.agent.call(pipeline.enrich, page)
        rec = self.listings()[E7]
        self.assertEqual((reply["result"], reply["id"]), ("merged", E7))
        self.assertEqual((rec["status"], rec["delisted_at"]), ("active", None))
        self.assertEqual(rec["facts"]["rent_advertised"], 3800)              # the fresh reading wins
        self.assertEqual(rec["rubric"]["scores"]["light"]["score"], 9)       # the old scores carry on
        self.assertEqual(rec["verdict"], "WATCH")

    def test_captcha_on_a_pasted_link_from_an_unlisted_site_can_be_reported(self):
        self.ingest({"url": "https://www.apartments.example.com/some-place/abc123", "rent": 3000, "beds": 1})
        task = self.agent.call(pipeline.next_task)
        self.assertEqual((task["task"], task["source"]), ("enrich", "manual"))
        self.agent.call(pipeline.blocked, "manual", "CAPTCHA")
        self.assertEqual(self.agent.call(pipeline.next_task)["task"], "finish")
        with self.assertRaises(ScoutError):
            self.agent.call(pipeline.blocked, "nonsense", "x")

    def test_lookups_use_the_borough_not_just_the_city(self):
        self.ingest({"url": demo.SE % "212-east-7-street-brooklyn/2", "address": "212 East 7th Street #2", "borough": "Brooklyn",
                     "neighborhood": "Kensington", "rent": 3000, "beds": 1})
        links = dict((l["id"], l["url"]) for l in self.agent.call(pipeline.next_task)["helper_links"])
        self.assertIn("origin=212+East+7th+Street+%232%2C+Brooklyn%2C+NY&", links["directions"])

    def test_lookups_use_the_zip_when_there_is_one(self):
        self.ingest({"url": demo.SE % "5-some-street/2", "address": "5 Some Street #2", "zip": "11218", "rent": 3000, "beds": 1})
        links = dict((l["id"], l["url"]) for l in self.agent.call(pipeline.next_task)["helper_links"])
        self.assertIn("query=5+Some+Street+%232%2C+NY+11218", links["map"])

    def test_quotes_are_checked_against_the_text_that_is_kept(self):
        self.ingest(card())
        page = copy.deepcopy(demo.DAY1["pages"][E7])
        page["raw_text"] = page["raw_text"] + " " + "x" * pipeline.MAX_PAGE_TEXT + " Dogs allowed too."
        page["facts"]["dogs"] = "allowed"
        page["provenance"]["dogs"] = {"where": "end of the description", "quote": "Dogs allowed too"}
        with self.assertRaises(ScoutError) as caught:
            self.agent.call(pipeline.enrich, page)
        self.assertIn("is not in raw_text", caught.exception.errors[0])

    def test_possible_duplicate_link_goes_once_the_units_are_known_to_differ(self):
        self.ingest({"url": demo.SE % "500-grand-street/3a", "address": "500 Grand Street #3A", "rent": 3500, "beds": 1},
                    {"url": demo.CL % ("mnh", "7922222222"), "address": "500 Grand St", "title": "One bedroom on Grand St",
                     "rent": 3550, "beds": 1})
        self.assertEqual(self.listings()["craigslist-7922222222"]["possible_duplicates"], ["streeteasy-demo-500-grand-street-3a"])
        page = copy.deepcopy(demo.DAY1["pages"][E7])
        page.update(id="craigslist-7922222222", address={"raw": "500 Grand Street #7C, New York, NY 10002"})
        self.agent.call(pipeline.enrich, page)
        recs = self.listings()
        self.assertEqual(recs["craigslist-7922222222"]["possible_duplicates"], [])
        self.assertEqual(recs["streeteasy-demo-500-grand-street-3a"]["possible_duplicates"], [])

    def test_evidence_keeps_the_pictures_it_was_based_on(self):
        self.ingest(card())
        self.agent.call(pipeline.enrich, demo.DAY1["pages"][E7])
        self.agent.call(pipeline.score, demo.DAY1["scores"][E7])
        with opened(self.dir) as store:
            pipeline.queue_refresh(store, ids=[E7])
        page = copy.deepcopy(demo.DAY1["pages"][E7])
        page["photos"] = ["https://photos.example.com/demo/new-gallery/1.jpg"]
        page["floor_plans"] = []
        self.agent.call(pipeline.enrich, page)
        rec = self.listings()[E7]
        self.assertEqual(len(rec["photos"]), 1)
        light = rec["rubric"]["scores"]["light"]["evidence"][0]
        self.assertEqual(light["photos"], [2, 3])
        self.assertEqual(light["urls"], [demo.PHOTO % (E7, 2), demo.PHOTO % (E7, 3)])
        plan = [e for e in rec["rubric"]["scores"]["layout"]["evidence"] if e["type"] == "floor_plan"][0]
        self.assertEqual(plan["urls"], [demo.PHOTO % (E7, "plan")])


class TwinMerges(HandPickedCase):
    """The merge used to fold the keeper back into the record it had just absorbed, losing both."""

    def twins(self, first_url, second_url):
        self.ingest({"url": first_url, "title": "First post of the East Village place", "rent": 3950, "beds": 1})
        first_id = list(self.listings())[0]
        page = copy.deepcopy(demo.DAY1["pages"][E7])
        page["id"] = first_id
        self.agent.call(pipeline.enrich, page)
        scores = copy.deepcopy(demo.DAY1["scores"][E7])
        scores["id"] = first_id
        self.agent.call(pipeline.score, scores)
        self.ingest({"url": second_url, "title": "Second, different post of the same place", "rent": 3900, "beds": 1})
        second_id = [i for i in self.listings() if i != first_id][0]
        page = copy.deepcopy(page)
        page["id"] = second_id
        reply = self.agent.call(pipeline.enrich, page)
        return first_id, second_id, reply

    def check(self, first_url, second_url):
        first_id, second_id, reply = self.twins(first_url, second_url)
        recs = self.listings()
        self.assertEqual(list(recs), [first_id])
        self.assertEqual((reply["result"], reply["id"], reply["merged_from"]), ("merged", first_id, second_id))
        self.assertEqual(len(recs[first_id]["sources"]), 2)
        self.assertEqual(recs[first_id]["rubric"]["scores"]["light"]["score"], 9)
        self.assertEqual(Store(self.dir).load().run["touched"], [first_id])

    def test_older_id_sorts_first(self):
        self.check(demo.CL % ("mnh", "7900000111"), demo.CL % ("mnh", "7900000222"))

    def test_older_id_sorts_last(self):
        self.check(demo.ZL % "900777", demo.CL % ("mnh", "7900000333"))

    def test_three_posts_of_one_home_end_as_one_record(self):
        first_id, _, _ = self.twins(demo.CL % ("mnh", "7900000111"), demo.CL % ("mnh", "7900000222"))
        self.ingest({"url": demo.SE % "mystery/9", "title": "Third listing, no address on the card", "rent": 3925, "beds": 1})
        page = copy.deepcopy(demo.DAY1["pages"][E7])
        page["id"] = "streeteasy-demo-mystery-9"
        self.agent.call(pipeline.enrich, page)
        recs = self.listings()
        self.assertEqual(list(recs), [first_id])
        self.assertEqual(len(recs[first_id]["sources"]), 3)


class RoundTwo(PipelineCase):
    def test_news_from_a_run_nobody_finished_is_told_by_the_next_one(self):
        self.agent.run(demo.DAY1, stop_after=12)             # a WATCH or two found, then the session is lost for good
        found = [i for i, r in self.listings().items() if r["verdict"] == "WATCH"]
        self.assertTrue(found)
        result = self.day(2, dict(demo.DAY1, sweeps={}))
        self.assertTrue(set(found) <= set(result["finished"]["announce"]["WATCH"]))
        html = report.build(self.agent.cfg, Store(self.dir).load())
        data = json.loads(re.search(r"var DATA = (.*);\n", html).group(1))
        self.assertTrue(all(item["is_new"] for item in data["listings"] if item["id"] in found))

    def test_one_home_posted_twice_at_two_rents_is_not_a_daily_price_change(self):
        two = [{"url": demo.ZL % "900100", "address": "9 Twin Street #1, Brooklyn, NY 11222", "rent": 3500, "beds": 1},
               {"url": demo.ZL % "900200", "address": "9 Twin Street #1, Brooklyn, NY 11222", "rent": 3600, "beds": 1}]
        self.agent.call(pipeline.start, False)
        for _ in range(4):
            counts = self.agent.call(pipeline.ingest, {"source": "manual", "cards": two})["counts"]
            self.assertEqual(counts["price_changed"], 0)
        rec = list(self.listings().values())[0]
        self.assertEqual(sorted(p["rent"] for p in rec["price_history"]), [3500, 3600])

    def test_full_can_be_asked_for_in_the_middle_of_a_run(self):
        self.day(1, demo.DAY1)
        self.clock.jump_to("2026-10-06T13:00:00")
        self.assertEqual(self.agent.call(pipeline.start)["plan"]["streeteasy"], "newest first")
        started = self.agent.call(lambda cfg, store: pipeline.start(cfg, store, full=True))
        self.assertEqual(set(started["plan"].values()), set(["full sweep"]))
        self.assertEqual(self.agent.call(pipeline.next_task)["mode"], "full")

    def test_no_sweep_on_a_leftover_daily_run_stops_the_searching(self):
        self.day(1, demo.DAY1)
        self.clock.jump_to("2026-10-06T13:00:00")
        self.agent.call(pipeline.start)
        self.assertEqual(self.agent.call(pipeline.next_task)["task"], "sweep")
        self.agent.call(pipeline.start, False)
        self.assertEqual(self.agent.call(pipeline.next_task)["task"], "finish")
        self.agent.call(pipeline.start)                     # and searching can be picked up again
        self.assertEqual(self.agent.call(pipeline.next_task)["task"], "sweep")

    def test_rebuilt_summary_agrees_with_itself(self):
        from scout import summary
        self.day(1, demo.DAY1)
        self.assertIn("## WATCH: 5 new", summary.build(Store(self.dir).load()))
        self.agent.cfg = make_config(lambda raw: next(r for r in raw["gate"]["rules"] if r["id"] == "commute").update(
            value=10, dealbreaker=True))
        self.agent.call(pipeline.rejudge_all)
        with opened(self.dir) as store:
            pipeline.set_status(store, NO_ADDRESS, "rejected")
        text = summary.build(Store(self.dir).load())
        self.assertIn("## WATCH: 0 new", text)
        self.assertEqual(text.count("88 Berry Street"), 1)   # once, under PASS

    def test_unknown_net_rent_is_blank_not_borrowed_from_the_card(self):
        page = copy.deepcopy(demo.DAY1["pages"][ASTORIA])
        del page["facts"]["free_months"], page["provenance"]["free_months"]
        page["unknown"]["free_months"] = "concession banner was cut off"
        self.day(1, dict(demo.DAY1, pages=dict(demo.DAY1["pages"], **{ASTORIA: page})))
        table = list(csv.reader(io.StringIO(export.to_csv(self.agent.cfg, Store(self.dir).load()))))
        row = next(dict(zip(table[0], r)) for r in table[1:] if r[-7] == ASTORIA or ASTORIA in r)
        self.assertEqual((row["rent_advertised"], row["rent_net_effective"], row["rent_gross"]), ("3100", "", "3100"))
        store = Store(self.dir).load()
        view = report.listing_view(self.agent.cfg, store.listings[ASTORIA], None)
        self.assertEqual((view["rent_net"], view["rent_gross"], view["rent_advertised"]), (None, 3100, 3100))

    def test_a_score_of_zero_still_outranks_no_score(self):
        self.day(1, demo.DAY1)
        with opened(self.dir) as store:
            store.listings[LIC]["rubric"] = {"score": 0, "scores": {}}
            ids = [row[-7] for row in export.rows(self.agent.cfg, store)[1:]]
            passes = [i for i in ids if store.listings[i]["verdict"] == "PASS"]
        self.assertLess(passes.index(LIC), len(passes) - 1)

    def test_impossible_dates_are_refused_in_plain_words(self):
        self.agent.call(pipeline.start, False)
        self.agent.call(pipeline.ingest, {"source": "manual", "cards": [card()]})
        page = copy.deepcopy(demo.DAY1["pages"][E7])
        page["facts"]["available_date"] = "2026-09-31"
        with self.assertRaises(ScoutError) as caught:
            self.agent.call(pipeline.enrich, page)
        self.assertIn("must be a real date", caught.exception.errors[0])

    def test_evening_runs_count_days_where_the_user_is(self):
        import time
        from scout import util
        if not hasattr(time, "tzset"):
            self.skipTest("needs tzset")
        before = os.environ.get("TZ")
        def restore():
            os.environ.pop("TZ", None) if before is None else os.environ.__setitem__("TZ", before)
            time.tzset()
        self.addCleanup(restore)
        os.environ["TZ"] = "America/New_York"
        time.tzset()
        # 7:30pm and 8:30pm New York on the same evening straddle midnight UTC.
        self.assertEqual(util.days_between("2026-10-05T23:30:00Z", "2026-10-06T00:30:00Z"), 0)
        self.assertEqual(util.days_between("2026-10-05T23:30:00Z", "2026-10-06T23:30:00Z"), 1)

    def test_settings_that_moved_are_not_silently_ignored(self):
        from tests.helpers import errors_from
        errors = errors_from(lambda raw: raw["limits"].update(recheck_after_days=3))
        self.assertTrue(any("live under `refresh`" in e for e in errors), errors)

    def test_many_searches_and_few_pages_is_warned_about(self):
        from scout import config
        from tests.helpers import raw_example
        raw = raw_example()
        raw["limits"]["max_search_pages_per_run"] = 4
        self.assertTrue(any("slow to finish" in w for w in config.validate(raw)[1]))


class RoundThree(PipelineCase):
    def test_overlap_pages_are_not_counted_twice_towards_a_complete_read(self):
        from tests.test_pipeline import many_pages
        def change(raw):
            for name in ("zillow", "craigslist"):
                raw["search"]["sources"][name]["enabled"] = False
            raw["search"]["sources"]["streeteasy"]["max_pages"] = 50
            raw["limits"]["max_search_pages_per_run"] = 4
        self.agent.cfg = make_config(change)
        seen = many_pages(12, per_page=7)        # the site says 120, the agent only ever reads 7 of each 10
        search = {"sweeps": {"streeteasy:0:2800-4400": {"pages": seen, "total_results": 120}, "streeteasy:0:new": {"pages": seen}}}
        for number in range(1, 9):
            result = self.day(number, search)
            if not result["finished"]["coverage"]["streeteasy"]["full_sweep_in_progress"]:
                break
        notes = [note for run in Store(self.dir).load().data["runs"] for note in run["notes"]]
        self.assertTrue(any("reported 120 results but only 84 cards were read" in note for note in notes), notes)

    def test_everything_an_abandoned_run_found_is_reported_by_the_next(self):
        from scout import summary
        self.day(1, demo.DAY1)
        self.clock.jump_to("2026-10-06T13:00:00")
        self.agent.call(pipeline.start, False)
        self.agent.call(pipeline.enrich, {"id": FRANKLIN, "gone": True})
        with opened(self.dir) as store:
            pipeline.queue_refresh(store, ids=[BERRY])
        self.agent.call(pipeline.enrich, demo.DAY2["pages"][BERRY])       # rent down, cats now allowed
        result = self.day(3, {"sweeps": {}})                               # the session above was never finished
        self.assertIn("closed_stale_run", result["started"])
        text = summary.build(Store(self.dir).load())
        self.assertIn("150 Franklin Street #2R, Brooklyn, NY 11222: off the market (was WATCH)", text)
        self.assertIn("rent $4,300 -> $4,150 (now TOUR)", text)
        self.assertIn("## TOUR: 1 new", text)

    def test_pausing_the_search_does_not_rewrite_what_the_run_did(self):
        from scout import summary
        self.agent.run(demo.DAY1, stop_after=7)         # every results page read, then the session drops
        self.agent.call(pipeline.start, False)          # scripts/refresh.sh lands on the same run
        finished = self.agent.call(pipeline.finish)
        self.assertTrue(finished["discovery"])
        self.assertIn("13 cards seen, 11 new", summary.build(Store(self.dir).load()))

    def test_read_back_promises_only_the_income_check_that_runs(self):
        from scout.explain import explain
        text = explain(make_config(lambda raw: raw["costs"].update(default_income_multiple=None)))
        self.assertIn("only where the listing states its income rule", text)
        self.assertNotIn("40x", text)

    def test_dates_shown_to_people_are_the_users_dates(self):
        import time
        from scout import summary
        if not hasattr(time, "tzset"):
            self.skipTest("needs tzset")
        before = os.environ.get("TZ")
        def restore():
            os.environ.pop("TZ", None) if before is None else os.environ.__setitem__("TZ", before)
            time.tzset()
        self.addCleanup(restore)
        os.environ["TZ"] = "America/New_York"
        time.tzset()
        self.clock.jump_to("2026-10-06T01:00:00")        # 9pm on October 5 in New York
        self.agent.run(demo.DAY1)
        store = Store(self.dir).load()
        self.assertTrue(summary.build(store).startswith("# Rental Scout: 2026-10-05"))
        view = report.listing_view(self.agent.cfg, store.listings[E7], None)
        self.assertEqual((view["first_seen"], view["price_history"][0]["date"]), ("2026-10-05", "2026-10-05"))
        table = list(csv.reader(io.StringIO(export.to_csv(self.agent.cfg, store))))
        self.assertEqual(set(dict(zip(table[0], row))["first_seen"] for row in table[1:]), set(["2026-10-05"]))


class SafeTools(unittest.TestCase):
    def test_demo_refuses_to_clear_a_folder_it_did_not_make(self):
        import tempfile
        from tests import demo as demo_builder
        folder = tempfile.mkdtemp(prefix="scout-test-")
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        self.addCleanup(os.environ.pop, "SCOUT_NOW", None)
        precious = os.path.join(folder, "precious.txt")
        with open(precious, "w") as handle:
            handle.write("keep me")
        with self.assertRaises(SystemExit):
            demo_builder.build(folder)
        self.assertTrue(os.path.exists(precious))
        os.unlink(precious)
        demo_builder.build(folder, days=1)       # empty is fine
        demo_builder.build(folder, days=1)       # and so is its own earlier output

    @unittest.skipUnless(shutil.which("bash") and shutil.which("zip") and shutil.which("unzip"), "needs bash, zip and unzip")
    def test_package_contains_nothing_personal(self):
        import tempfile
        work = tempfile.mkdtemp(prefix="scout-test-")
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        copy_root = os.path.join(work, "rental-scout")
        shutil.copytree(ROOT, copy_root, ignore=shutil.ignore_patterns(".git", "__pycache__", "data", "data-demo", "dist"))
        for name in ("scout.config.yaml", "scout.config.yaml.bak", "my-data/store.json", "data/store.json", "notes.txt",
                     ".claude/settings.local.json", "configs/mine.yaml.bak"):
            path = os.path.join(copy_root, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as handle:
                handle.write("personal")
        # Skip the test run inside the script: this test is already part of it.
        with open(os.path.join(copy_root, "scripts", "package.sh")) as handle:
            script = handle.read().replace('"$PY" -m unittest discover -s tests -t . 2>&1 | tail -3', "true")
        with open(os.path.join(copy_root, "scripts", "package.sh"), "w") as handle:
            handle.write(script)
        out = os.path.join(work, "out.zip")
        done = subprocess.run(["bash", os.path.join(copy_root, "scripts", "package.sh"), out], stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, universal_newlines=True)
        self.assertEqual(done.returncode, 0, done.stdout)
        names = subprocess.run(["unzip", "-Z1", out], stdout=subprocess.PIPE, universal_newlines=True).stdout.split("\n")
        for personal in ("scout.config.yaml", "my-data", "/data/", "notes.txt", "settings.local", ".bak"):
            self.assertFalse([n for n in names if personal in n], personal)
        for needed in ("rental-scout/README.md", "rental-scout/scout/pipeline.py", "rental-scout/agent/SETUP_INTERVIEW.md",
                       "rental-scout/configs/nyc.template.yaml", "rental-scout/scout/_vendor/yaml/__init__.py",
                       "rental-scout/.claude/settings.json", "rental-scout/scripts/run.sh", "rental-scout/tests/sim.py"):
            self.assertIn(needed, names)


class StopEarly(HandPickedCase):
    def setUp(self):
        super(StopEarly, self).setUp()
        self.ingest(card(2))     # 41-15 29th Street: pets case by case
        self.short = {"id": LIC, "stop_early": True, "raw_text": demo.TEXT_LIC, "facts": {"cats": "case_by_case"},
                      "provenance": {"cats": {"where": "description", "quote": "Pets considered case by case"}}}

    def test_task_says_what_to_read_first(self):
        first = self.agent.call(pipeline.next_task)["check_first"]
        for name in ("rent_advertised", "beds", "cats", "furnished", "sublet"):
            self.assertIn(name, first)
        self.assertNotIn("laundry", first)      # a soft rule cannot rule a listing out alone

    def test_a_missed_dealbreaker_is_enough(self):
        reply = self.agent.call(pipeline.enrich, self.short)
        self.assertEqual((reply["verdict"], reply["stage"]), ("PASS", "done"))
        rec = self.listings()[LIC]
        self.assertTrue(rec["partial"])
        self.assertEqual(rec["facts"], {"cats": "case_by_case"})
        self.assertIn("Cats case by case", rec["verdict_reason"])

    def test_a_plain_scam_is_enough(self):
        self.ingest({"url": demo.CL % ("mnh", "7900000001"), "title": "HUGE East Village apartment all utilities included",
                     "rent": 2800, "beds": 1})
        reply = self.agent.call(pipeline.enrich, {"id": SCAM, "stop_early": True, "raw_text": demo.TEXT_SCAM,
                                                  "facts": {"owner_remote": True}, "provenance": {"owner_remote": "post text"}})
        self.assertEqual(reply["verdict"], "PASS (risk)")
        self.assertIn('"out of the country", "mail you the keys"', reply["reason"])

    def test_it_is_refused_when_nothing_rules_the_listing_out(self):
        for facts, where in (({"cats": "allowed"}, {"cats": "description"}), ({"laundry": "none"}, {"laundry": "amenities"}), ({}, {})):
            with self.assertRaises(ScoutError) as caught:
                self.agent.call(pipeline.enrich, dict(self.short, facts=facts, provenance=where))
            self.assertIn("does not rule the listing out", caught.exception.errors[0])
        self.assertIsNone(self.listings()[LIC]["facts"])

    def test_what_is_recorded_is_still_held_to_the_rules(self):
        bad = dict(self.short, provenance={"cats": {"where": "description", "quote": "Absolutely no animals"}})
        with self.assertRaises(ScoutError) as caught:
            self.agent.call(pipeline.enrich, bad)
        self.assertIn("is not in raw_text", caught.exception.errors[0])
        with self.assertRaises(ScoutError):
            self.agent.call(pipeline.enrich, dict(self.short, provenance={}))

    def test_back_in_the_running_means_read_the_rest(self):
        self.agent.call(pipeline.enrich, self.short)
        self.agent.cfg = make_config(lambda raw: next(r for r in raw["gate"]["rules"] if r["id"] == "cats").update(enabled=False))
        self.agent.call(pipeline.rejudge_all)
        task = self.agent.call(pipeline.next_task)
        self.assertEqual((task["task"], task["id"], task["why"]), ("enrich", LIC, "incomplete"))
        self.agent.call(pipeline.enrich, demo.DAY1["pages"][LIC])
        rec = self.listings()[LIC]
        self.assertEqual((rec["partial"], rec["stage"]), (False, "needs_score"))

    def test_the_address_the_card_gave_is_kept(self):
        self.agent.call(pipeline.enrich, self.short)
        rec = self.listings()[LIC]
        self.assertEqual(rec["address_key"]["street"], "4115 29 st")
        self.assertNotIn("no_address", [f["id"] for f in rec["flags"]])
        counts = self.ingest({"url": demo.ZL % "900555", "address": "41-15 29th St APT 12F, Long Island City, NY 11101",
                              "rent": 3600, "beds": 1})["counts"]
        self.assertEqual((counts["new"], counts["merged"]), (0, 1))      # the same home on Zillow joins it

    def strict_cats(self):
        self.agent.cfg = make_config(lambda raw: next(r for r in raw["gate"]["rules"] if r["id"] == "cats").update(on_unknown="fail"))

    def test_a_fact_nobody_read_is_not_a_reason_to_stop(self):
        self.strict_cats()
        with self.assertRaises(ScoutError) as caught:
            self.agent.call(pipeline.enrich, dict(self.short, facts={"beds": 1}, provenance={"beds": "headline"}))
        self.assertIn("does not rule the listing out", caught.exception.errors[0])

    def test_stopped_on_rent_alone_comes_back_when_the_rent_drops(self):
        self.strict_cats()
        dear = {"rent_advertised": 9000, "rent_advertised_is": "gross", "free_months": 0}
        self.agent.call(pipeline.enrich, dict(self.short, facts=dear, provenance=dict((k, "headline") for k in dear)))
        self.assertEqual(self.listings()[LIC]["verdict"], "PASS")
        self.ingest(dict(card(2), rent=3300))
        rec = self.listings()[LIC]
        self.assertEqual((rec["refresh"], rec["stage"]), ("price_changed", "needs_enrich"))

    def test_stopped_on_rent_alone_comes_back_when_the_budget_rises(self):
        self.strict_cats()
        dear = {"rent_advertised": 4500, "rent_advertised_is": "gross", "free_months": 0}
        self.agent.call(pipeline.enrich, dict(self.short, facts=dear, provenance=dict((k, "headline") for k in dear)))
        def richer(raw):
            for r in raw["gate"]["rules"]:
                if r["id"] == "cats":
                    r["on_unknown"] = "fail"
                if r["id"] == "rent":
                    r["value"] = 5000
        self.agent.cfg = make_config(richer)
        self.agent.call(pipeline.rejudge_all)
        rec = self.listings()[LIC]
        self.assertEqual((rec["verdict"], rec["stage"]), (None, "needs_enrich"))
        self.assertEqual(self.agent.call(pipeline.next_task)["why"], "incomplete")

    def test_switching_a_rule_off_and_on_again_leaves_no_trace(self):
        self.agent.call(pipeline.enrich, self.short)
        before = self.listings()[LIC]
        original = self.agent.cfg
        self.agent.cfg = make_config(lambda raw: next(r for r in raw["gate"]["rules"] if r["id"] == "cats").update(enabled=False))
        self.agent.call(pipeline.rejudge_all)
        self.assertEqual(self.listings()[LIC]["stage"], "needs_enrich")
        self.agent.cfg = original
        self.agent.call(pipeline.rejudge_all)
        after = self.listings()[LIC]
        self.assertEqual((after["verdict"], after["stage"], after["refresh"]), (before["verdict"], "done", None))
        self.assertEqual(self.agent.call(pipeline.next_task)["task"], "finish")

    def test_shipped_example_is_accepted(self):
        with open(os.path.join(ROOT, "agent", "examples", "enrich_stop_early.json")) as handle:
            self.assertEqual(self.agent.call(pipeline.enrich, json.load(handle))["verdict"], "PASS")


class RehearsalFindings(PipelineCase):
    def test_listing_gone_before_it_was_judged_is_accounted_for(self):
        from scout import summary
        pages = dict(demo.DAY1["pages"], **{BROOME_ID: {"id": BROOME_ID, "gone": True}})
        self.day(1, dict(demo.DAY1, pages=pages))
        store = Store(self.dir).load()
        rec = store.listings[BROOME_ID]
        self.assertEqual((rec["verdict"], rec["status"]), (None, "delisted"))
        self.assertIn("before it could be judged", rec["verdict_reason"])
        text = summary.build(store)
        self.assertIn("## Already gone: 1", text)
        self.assertIn("305 Broome Street #1A: the page was gone", text)
        self.assertEqual(report.listing_view(self.agent.cfg, rec, None)["verdict"], "GONE")
        table = list(csv.reader(io.StringIO(export.to_csv(self.agent.cfg, store))))
        self.assertEqual(next(r for r in table[1:] if BROOME_ID in r)[0], "gone")
        self.assertEqual(self.agent.call(pipeline.rejudge_all)["requeued"], 0)
        rec = self.listings()[BROOME_ID]
        self.assertIn("before it could be judged", rec["verdict_reason"])
        self.assertEqual(rec["stage"], "done")

    def test_a_site_that_returns_nothing_at_all_is_mentioned(self):
        from scout import summary
        self.day(1, dict(demo.DAY1, sweeps=dict((k, v) for k, v in demo.DAY1["sweeps"].items() if not k.startswith("zillow"))))
        self.assertIn("zillow: the full sweep found no listings at all", summary.build(Store(self.dir).load()))

    def test_lookup_links_reach_the_report(self):
        self.day(1, demo.DAY1)
        view = report.listing_view(self.agent.cfg, Store(self.dir).load().listings[LIC], None)   # never scored
        facts = dict((f["name"], f) for f in view["facts"])
        self.assertEqual(facts["commute minutes"]["url"], demo.MAPS)
        self.assertEqual(facts["flood zone"]["where"], "FEMA flood map for the address")

    def test_spreadsheet_carries_the_notes_too(self):
        self.day(1, demo.DAY1)
        table = list(csv.reader(io.StringIO(export.to_csv(self.agent.cfg, Store(self.dir).load()))))
        rows = dict((r[-7], dict(zip(table[0], r))) for r in table[1:])
        self.assertIn("Noise: Heavy traffic on Broome", rows[BROOME_ID]["notes"])
        self.assertIn("rent-stabilized", rows[ASTORIA]["notes"])

    def test_no_questions_are_raised_about_a_listing_that_is_ruled_out(self):
        from scout.summary import open_questions
        self.day(1, demo.DAY1)
        recs = self.listings()
        self.assertEqual(open_questions(recs[SCAM]), [])
        self.assertEqual(open_questions(recs[LIC]), [])
        self.assertIn("Confirm: Cats", open_questions(recs[BERRY]))

    def test_watch_says_how_far_from_a_tour(self):
        self.day(1, demo.DAY1)
        self.assertIn("Score 73.5, 1.5 short of a TOUR.", self.listings()[E7]["verdict_reason"])

    def test_gate_row_shows_the_exception_it_passed_on(self):
        self.day(1, demo.DAY1)
        row = next(r for r in self.listings()[BERRY]["gate"]["rules"] if r["id"] == "walkup")
        self.assertEqual((row["status"], row["actual"]), ("pass", "6 (elevator yes)"))
        row = next(r for r in self.listings()[E7]["gate"]["rules"] if r["id"] == "walkup")
        self.assertEqual(row["actual"], "3")

    def test_score_task_carries_the_text_to_quote(self):
        self.agent.call(pipeline.start, False)
        self.agent.call(pipeline.ingest, {"source": "manual", "cards": [card(1)]})
        self.agent.call(pipeline.enrich, demo.DAY1["pages"][BERRY])
        task = self.agent.call(pipeline.next_task)
        self.assertEqual(task["listing_text"], demo.TEXT_BERRY)
        self.assertIn("cats", task["unknown"])

    def test_read_back_covers_what_the_user_said(self):
        from scout.explain import explain
        text = explain(self.agent.cfg)
        for expected in ("Moving no earlier than 2026-11-15 and no later than 2026-12-15", "3 searches: mnh, brk, que",
                         "Not checked (switched off): Dogs", "need more than $160,000 a year (40x", "no guarantor",
                         "10 listings opened in all, up to 6 results pages read on each site"):
            self.assertIn(expected, text)

    def test_unquoted_url_error_says_to_quote_it(self):
        from tests.helpers import errors_from
        errors = errors_from(lambda raw: raw["search"]["sources"]["streeteasy"].update(
            saved_search_urls=["https://streeteasy.com/for-rent/nyc/area:117", 109, "301%7Cbeds%3E=1"]))
        self.assertTrue(any("quotes" in e for e in errors), errors)


class Purity(PipelineCase):
    def test_rejudging_equals_judging_afresh(self):
        """Learned comps must not depend on earlier verdicts (a PASS (risk) used to keep itself in place)."""
        def learned(raw):
            raw["risk"].update(min_comps=3, price_floor_pct=0.9, scam_phrases=["serious inquiries only"])
        def without_phrase(raw):
            learned(raw)
            raw["risk"]["scam_phrases"] = []
        self.agent.cfg = make_config(learned)
        self.day(1, demo.DAY1)
        self.assertEqual(self.listings()[SCAM]["verdict"], "PASS (risk)")
        self.agent.cfg = make_config(without_phrase)
        self.agent.call(pipeline.rejudge_all)
        once = dict((i, (r["verdict"], r["verdict_reason"])) for i, r in self.listings().items())
        self.agent.call(pipeline.rejudge_all)
        twice = dict((i, (r["verdict"], r["verdict_reason"])) for i, r in self.listings().items())
        self.assertEqual(once, twice)

        other = PipelineCase("setUp")
        other.setUp()
        self.addCleanup(other.doCleanups)
        other.agent.cfg = make_config(without_phrase)
        other.day(1, demo.DAY1)
        fresh = dict((i, (r["verdict"], r["verdict_reason"])) for i, r in other.listings().items())
        self.assertEqual(once, fresh)


class Outputs(PipelineCase):
    def setUp(self):
        super(Outputs, self).setUp()
        self.day(1, demo.DAY1)

    def html(self, mutate=None):
        with opened(self.dir) as store:
            if mutate:
                mutate(store)
            return report.build(self.agent.cfg, store)

    def test_no_listing_text_can_confuse_the_script_block(self):
        nasty = "<!--<script>alert(1)</script> </script><img src=x onerror=alert(1)>"
        def mutate(store):
            rec = store.listings[E7]
            rec["raw_text"] = rec["verdict_reason"] = rec["card"]["title"] = nasty
        html = self.html(mutate)
        blob = re.search(r"var DATA = (.*);\n", html).group(1)
        self.assertNotIn("<", blob)
        self.assertEqual(html.count("</script>"), 1)
        self.assertEqual(next(i for i in json.loads(blob)["listings"] if i["id"] == E7)["description"], nasty)

    def test_report_knows_how_fresh_each_listing_is(self):
        data = json.loads(re.search(r"var DATA = (.*);\n", self.html()).group(1))
        self.assertEqual(data["recheck_days"], {"TOUR": 2, "WATCH": 4})
        self.assertTrue(all(item["checked_at"].startswith("2026-10-05") for item in data["listings"]))
        self.assertTrue(data["coverage"]["streeteasy"]["last_full_sweep"].startswith("2026-10-05"))

    def test_price_history_in_the_report_is_one_row_per_change(self):
        self.day(2, demo.DAY2)
        data = json.loads(re.search(r"var DATA = (.*);\n", self.html()).group(1))
        berry = next(i for i in data["listings"] if i["id"] == BERRY)
        self.assertEqual([p["rent"] for p in berry["price_history"]], [4300, 4150])

    def test_spreadsheet(self):
        table = list(csv.reader(io.StringIO(export.to_csv(self.agent.cfg, Store(self.dir).load()))))
        head, rows = table[0], [dict(zip(table[0], row)) for row in table[1:]]
        self.assertEqual(len(rows), 11)
        self.assertEqual([r["verdict"] for r in rows[:5]], ["WATCH"] * 5)      # shortlist first, best score first
        self.assertEqual(rows[0]["id"], BERRY)
        self.assertEqual((rows[0]["rent_net_effective"], rows[0]["rent_gross"], rows[0]["score_light"]), ("3969", "4300", "8"))
        self.assertEqual(rows[0]["unknown_must_haves"], "Cats")
        self.assertTrue(rows[0]["url"].startswith("https://streeteasy.com/"))
        self.assertIn("score_outdoor", head)
        no_address = next(r for r in rows if r["id"] == NO_ADDRESS)
        self.assertEqual((no_address["score_light"], no_address["unverified_pct"]), ("", "40"))

    def test_spreadsheet_cells_cannot_be_formulas(self):
        with opened(self.dir) as store:
            store.listings[LIC]["address"]["raw"] = '=HYPERLINK("http://evil.example","click")'
            text = export.to_csv(self.agent.cfg, store)
        self.assertIn("'=HYPERLINK", text)

    def test_overview(self):
        self.clock.jump_to("2026-10-08T13:00:00")
        view = self.agent.call(pipeline.overview)
        self.assertEqual(view["listings"]["by_verdict"]["WATCH"], 5)
        self.assertEqual(view["shortlist_freshness"][0]["days_since_checked"], 3.0)
        self.assertEqual(view["coverage"]["zillow"]["full_sweep_in_progress"], False)
        self.assertEqual(view["last_run"]["evaluated"], 8)


class CommandLineSafety(PipelineCase):
    def scout(self, *args, **kw):
        done = subprocess.run([sys.executable, "-m", "scout", "--config", EXAMPLE, "--data", self.dir] + list(args), cwd=ROOT,
                              env=dict(os.environ, SCOUT_NOW="2026-10-05T13:00:00"), input=kw.get("stdin"),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        return done.returncode, done.stdout

    def test_malformed_payloads_get_a_json_answer_never_a_bare_traceback(self):
        self.scout("start", "--no-sweep")
        with open(os.path.join(self.dir, "store.json"), encoding="utf-8") as handle:
            before = handle.read()
        for command, payload in (
                ("ingest", {"cards": [{"url": 5}]}), ("ingest", {"cards": [[1, 2]]}), ("ingest", {"cards": "x"}),
                ("ingest", {"cards": [{"url": "https://x.example/1", "title": {"a": 1}}]}),
                ("ingest", {"sweep": ["x"], "cards": []}), ("enrich", {"id": {"a": 1}}), ("enrich", []),
                ("score", {"id": None}), ("score", "text")):
            code, out = self.scout(command, "-", stdin=json.dumps(payload))
            self.assertIn(code, (2, 3), (command, payload, out))
            self.assertFalse(json.loads(out)["ok"], (command, payload))
        with open(os.path.join(self.dir, "store.json"), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), before)

    @unittest.skipUnless(shutil.which("bash"), "needs bash")
    def test_refresh_script_shows_why_it_failed(self):
        self.day(1, demo.DAY1)
        done = subprocess.run(["bash", os.path.join(ROOT, "scripts", "refresh.sh"), "no-such-id"], cwd=self.dir, env={
            "PATH": "/usr/bin:/bin", "PYTHON": shutil.which("python3"), "SCOUT_CONFIG": EXAMPLE, "SCOUT_DATA": self.dir,
            "HOME": self.dir}, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("No listing with id", done.stdout)


if __name__ == "__main__":
    unittest.main()
