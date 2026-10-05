"""End-to-end runs with the scripted agent, plus the awkward cases."""

import copy
import json
import os
import shutil
import tempfile
import unittest

from scout import config, pipeline, summary
from scout.store import Store, opened
from scout.util import ScoutError
from tests.fixtures import nyc_demo as demo
from tests.helpers import EXAMPLE, make_config, rule
from tests.sim import Clock, ScriptedAgent

E7 = "streeteasy-demo-212-east-7-street-4b"
BERRY = "streeteasy-demo-88-berry-street-6c"
LIC = "streeteasy-demo-41-15-29-street-12f"
FRANKLIN = "streeteasy-demo-150-franklin-street-2r"
BROOME = "streeteasy-demo-305-broome-street-1a"
NORMAN = "streeteasy-demo-77-norman-avenue-2a"
ASTORIA = "zillow-900002"
SCAM = "craigslist-7900000001"
NO_ADDRESS = "craigslist-7900000003"


def unchanged_since_day_one():
    """Newest-first searches that return exactly what day 1 found."""
    found = demo.DAY1["sweeps"]
    return {"sweeps": {
        "streeteasy:0:new": {"cards": found["streeteasy:0:2800-4400"]["cards"]},
        "zillow:0:new": {"cards": found["zillow:0:2800-3600"]["cards"] + found["zillow:0:3601-4400"]["cards"]},
        "craigslist:0:new": {"cards": found["craigslist:0:2800-4400"]["cards"]},
        "craigslist:1:new": {"cards": found["craigslist:1:2800-4400"]["cards"]}}}


class PipelineCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="scout-test-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.addCleanup(os.environ.pop, "SCOUT_NOW", None)
        self.clock = Clock("2026-10-05T13:00:00")
        self.agent = ScriptedAgent(EXAMPLE, self.dir, self.clock)

    def listings(self):
        return Store(self.dir).load().listings

    def day(self, number, fixture, **kw):
        self.clock.jump_to("2026-10-%02dT13:00:00" % (4 + number))
        self.agent.log = []
        return self.agent.run(fixture, **kw)

    def coverage(self):
        return Store(self.dir).load().data["coverage"]


class DayOne(PipelineCase):
    def setUp(self):
        super(DayOne, self).setUp()
        self.result = self.day(1, demo.DAY1)
        self.recs = self.listings()

    def test_every_listing_has_a_verdict_and_a_reason(self):
        self.assertEqual(len(self.recs), 11)
        for rec in self.recs.values():
            self.assertIn(rec["verdict"], ("TOUR", "WATCH", "PASS", "PASS (risk)"), rec["id"])
            self.assertTrue(rec["verdict_reason"], rec["id"])
            self.assertEqual(rec["stage"], "done", rec["id"])

    def test_verdicts(self):
        got = dict((rec_id, rec["verdict"]) for rec_id, rec in self.recs.items())
        self.assertEqual(got[E7], "WATCH")
        self.assertEqual(got[BERRY], "WATCH")        # 83, but the cat policy is unknown
        self.assertEqual(got[FRANKLIN], "WATCH")     # one soft miss
        self.assertEqual(got[ASTORIA], "WATCH")
        self.assertEqual(got[NO_ADDRESS], "WATCH")   # 40% unverifiable
        self.assertEqual(got[LIC], "PASS")
        self.assertEqual(got[BROOME], "PASS")
        self.assertEqual(got[SCAM], "PASS (risk)")
        self.assertIn("unknown must-haves: Cats", self.recs[BERRY]["verdict_reason"])
        self.assertIn("Laundry none", self.recs[FRANKLIN]["verdict_reason"])

    def test_no_browser_time_on_prefiltered_or_card_rejected(self):
        opened_ids = set(task["id"] for task in self.agent.tasks("enrich"))
        self.assertEqual(len(opened_ids), 8)
        for rec in self.recs.values():
            if rec["verdict_reason"].startswith(("Filtered out", "Search card")):
                self.assertNotIn(rec["id"], opened_ids)
                self.assertIsNone(rec["facts"])
        self.assertEqual(self.result["finished"]["counts"]["prefiltered"], 2)
        self.assertEqual(self.result["finished"]["counts"]["card_rejected"], 1)

    def test_only_gate_passers_are_scored(self):
        scored = set(task["id"] for task in self.agent.tasks("score"))
        self.assertEqual(scored, set([E7, BERRY, FRANKLIN, BROOME, ASTORIA, NO_ADDRESS]))

    def test_same_unit_on_two_sites_is_one_record(self):
        rec = self.recs[E7]
        self.assertEqual(sorted(s["source"] for s in rec["sources"]), ["streeteasy", "zillow"])
        self.assertNotIn("zillow-900001", self.recs)
        self.assertIn("price_differs_by_site", [f["id"] for f in rec["flags"]])

    def test_capped_sweep_is_split_and_both_halves_swept(self):
        keys = [task["sweep"] for task in self.agent.tasks("sweep") if task["source"] == "zillow"]
        self.assertEqual(keys, ["zillow:0:2800-4400", "zillow:0:2800-3600", "zillow:0:3601-4400"])
        last = [task for task in self.agent.tasks("sweep") if task["sweep"] == "zillow:0:3601-4400"][0]
        self.assertIn("%22min%22%3A3601", last["url"])

    def test_first_run_is_a_full_sweep_and_it_finishes(self):
        self.assertEqual(set(task["mode"] for task in self.agent.tasks("sweep")), set(["full"]))
        self.assertEqual(self.result["started"]["plan"]["streeteasy"], "full sweep")
        for source, cover in self.coverage().items():
            self.assertIsNone(cover["cycle"], source)
            self.assertTrue(cover["last_full_at"], source)

    def test_net_and_gross_are_both_kept(self):
        derived = self.recs[BERRY]["derived"]
        self.assertEqual(derived["rent_gross"], 4300)
        self.assertEqual(round(derived["rent_net_effective"]), 3969)

    def test_unknowns_are_explicit(self):
        rec = self.recs[NO_ADDRESS]
        self.assertIsNone(rec["address_key"])
        self.assertEqual(rec["address_unknown_reason"], "Post says to email for the address")
        self.assertIsNone(rec["rubric"]["scores"]["light"]["score"])
        self.assertEqual(rec["rubric"]["unknown_weight"], 0.4)
        self.assertIn("commute_minutes", rec["unknown"])

    def test_each_listing_is_finished_before_the_next_is_opened(self):
        order = [(t["task"], t["id"]) for t in self.agent.log if t["task"] in ("enrich", "score")]
        for index, (kind, rec_id) in enumerate(order):
            if kind == "score":
                self.assertEqual(order[index - 1], ("enrich", rec_id))

    def test_summary_puts_the_shortlist_first(self):
        text = summary.build(Store(self.dir).load())
        self.assertLess(text.index("## TOUR"), text.index("## WATCH"))
        self.assertLess(text.index("## WATCH"), text.index("## PASS"))
        self.assertIn("To settle it: Confirm: Cats", text)


class LaterDays(PipelineCase):
    def test_day_two_reads_newest_first_and_only_evaluates_new_or_changed(self):
        self.day(1, demo.DAY1)
        result = self.day(2, demo.DAY2)
        self.assertEqual(set(t["mode"] for t in self.agent.tasks("sweep")), set(["incremental"]))
        self.assertEqual([t["id"] for t in self.agent.tasks("enrich")], [BERRY, NORMAN])
        self.assertEqual([t["why"] for t in self.agent.tasks("enrich")], ["price_changed", "new"])
        self.assertEqual([t["id"] for t in self.agent.tasks("score")], [NORMAN])
        counts = result["finished"]["counts"]
        self.assertEqual((counts["new"], counts["price_changed"]), (1, 2))
        recs = self.listings()
        self.assertEqual(recs[NORMAN]["verdict"], "TOUR")
        self.assertEqual(recs[BERRY]["verdict"], "TOUR")   # cats now stated, old scores reused
        self.assertEqual([(p["rent"], p["where"]) for p in recs[BERRY]["price_history"]],
                         [(4300, "card"), (4300, "page"), (4150, "card"), (4150, "page")])
        self.assertIn({"event": "verdict", "detail": "WATCH -> TOUR"},
                      [{"event": h["event"], "detail": h["detail"]} for h in recs[BERRY]["history"]])

    def test_price_drop_that_cannot_help_costs_no_browser_time(self):
        self.day(1, demo.DAY1)
        self.day(2, demo.DAY2)
        rec = self.listings()[LIC]
        self.assertEqual((rec["verdict"], rec["stage"]), ("PASS", "done"))
        self.assertEqual(rec["sources"][0]["rent"], 3450)            # the new figure is on record
        self.assertEqual(rec["facts"]["rent_advertised"], 3600)      # but facts stay what the page said

    def test_price_drop_on_a_listing_that_failed_only_on_rent_reopens_it(self):
        self.agent.cfg = make_config(lambda raw: rule(raw, "rent").update(value=3500))
        self.day(1, demo.DAY1)
        self.assertIn("Rent", self.listings()[E7]["verdict_reason"])
        cheaper = {"sweeps": {"streeteasy:0:new": {"cards": [
            dict(demo.DAY1["sweeps"]["streeteasy:0:2800-4400"]["cards"][0], rent=3400)]}},
            "pages": {E7: copy.deepcopy(demo.DAY1["pages"][E7])}, "scores": demo.DAY1["scores"]}
        cheaper["pages"][E7]["facts"]["rent_advertised"] = 3400
        self.day(2, cheaper)
        self.assertEqual([t["why"] for t in self.agent.tasks("enrich")], ["price_changed"])
        self.assertEqual(self.listings()[E7]["verdict"], "WATCH")

    def test_card_and_page_rents_that_differ_are_not_a_daily_price_change(self):
        page = copy.deepcopy(demo.DAY1["pages"][ASTORIA])
        page["facts"]["rent_advertised"] = 3250        # the card says "from $3,100"
        day1 = dict(demo.DAY1, pages=dict(demo.DAY1["pages"], **{ASTORIA: page}))
        self.day(1, day1)
        for number in (2, 3, 4):
            result = self.day(number, unchanged_since_day_one())
            self.assertEqual(result["finished"]["counts"]["price_changed"], 0, number)
            self.assertEqual(self.agent.tasks("enrich"), [], number)
        self.assertEqual([(p["rent"], p["where"]) for p in self.listings()[ASTORIA]["price_history"]],
                         [(3100, "card"), (3250, "page")])

    def test_repost_joins_the_original(self):
        self.day(1, demo.DAY1)
        self.day(2, demo.DAY2)
        recs = self.listings()
        self.assertNotIn("craigslist-7900000099", recs)
        self.assertEqual(sorted(s["source_id"] for s in recs[NO_ADDRESS]["sources"]), ["7900000003", "7900000099"])

    def test_block_stops_the_site_and_is_reported(self):
        self.day(1, demo.DAY1)
        result = self.day(2, demo.DAY2)
        self.assertEqual(len([t for t in self.agent.tasks("sweep") if t["source"] == "zillow"]), 1)
        self.assertEqual(result["finished"]["blocked"]["zillow"]["what"], "press-and-hold CAPTCHA")
        self.assertIn("did not retry", summary.build(Store(self.dir).load()))

    def test_quiet_day_stops_at_the_first_page_with_nothing_new(self):
        self.day(1, demo.DAY1)
        self.day(2, demo.DAY2)
        self.day(3, demo.DAY3)
        streeteasy = [t for t in self.agent.tasks("sweep") if t["source"] == "streeteasy"]
        self.assertEqual([t["page"] for t in streeteasy], [1])      # the fixture has a second page; it is never read
        self.assertEqual(self.agent.tasks("enrich"), [])

    def test_newest_first_sweeps_never_delist_by_absence(self):
        self.day(1, demo.DAY1)
        for number in (2, 3):
            self.day(number, {"sweeps": {}})     # every search comes back empty
        for rec in self.listings().values():
            self.assertIsNone(rec["delisted_at"], rec["id"])
            self.assertEqual([e["missed_sweeps"] for e in rec["sources"]], [0] * len(rec["sources"]))


class FullSweepCycle(PipelineCase):
    def weeks(self):
        self.day(1, demo.DAY1)
        self.day(2, demo.DAY2)
        self.day(3, demo.DAY3)
        return self.day(8, demo.WEEK2)

    def test_full_sweep_comes_round_after_a_week(self):
        result = self.weeks()
        self.assertEqual(set(t["mode"] for t in self.agent.tasks("sweep")), set(["full"]))
        self.assertEqual(result["started"]["plan"]["zillow"], "full sweep")

    def test_missing_shortlisted_listing_gets_its_page_opened_not_dropped(self):
        result = self.weeks()
        franklin = [t for t in self.agent.tasks("enrich") if t["id"] == FRANKLIN]
        self.assertEqual([t["why"] for t in franklin], ["recheck"])
        self.assertEqual(self.listings()[FRANKLIN]["status"], "delisted")     # the page really was gone
        self.assertIn(FRANKLIN, result["finished"]["delisted"])
        self.assertIn("150 Franklin Street #2R, Brooklyn, NY 11222: off the market (was WATCH)",
                      summary.build(Store(self.dir).load()))

    def test_missing_pass_listing_is_dropped_after_two_full_sweeps(self):
        self.weeks()
        rec = self.listings()[BROOME]
        self.assertEqual((rec["sources"][0]["missed_sweeps"], rec["delisted_at"]), (1, None))
        result = self.day(15, demo.WEEK2)
        self.assertIn(BROOME, result["finished"]["delisted"])
        self.assertEqual(self.listings()[BROOME]["status"], "delisted")
        self.assertNotIn(BROOME, [t["id"] for t in self.agent.tasks("enrich")])   # no browser time on a PASS

    def test_listings_the_sweep_just_saw_are_not_rechecked(self):
        self.weeks()
        self.assertEqual([t["id"] for t in self.agent.tasks("enrich")], [FRANKLIN])

    def test_relisting_brings_it_back(self):
        self.weeks()
        back = copy.deepcopy(demo.DAY3)
        back["sweeps"]["streeteasy:0:new"] = {"cards": [demo.DAY1["sweeps"]["streeteasy:0:2800-4400"]["cards"][5]]}
        back["pages"][FRANKLIN] = demo.DAY1["pages"][FRANKLIN]
        result = self.day(9, back)
        self.assertEqual(result["finished"]["counts"]["relisted"], 1)
        rec = self.listings()[FRANKLIN]
        self.assertEqual((rec["status"], rec["delisted_at"], rec["verdict"]), ("active", None, "WATCH"))
        self.assertEqual([t["why"] for t in self.agent.tasks("enrich")], ["relisted"])

    def test_pasted_listing_outside_the_saved_search_is_never_delisted_by_absence(self):
        self.day(1, demo.DAY1)
        self.clock.jump_to("2026-10-05T20:00:00")
        self.agent.call(pipeline.start, False)
        self.agent.call(pipeline.ingest, {"source": "manual", "cards": [
            {"url": demo.SE % "9-outside-the-search/1", "address": "9 Elsewhere Street #1", "rent": 3000, "beds": 1}]})
        self.agent.call(pipeline.finish)
        pasted = "streeteasy-demo-9-outside-the-search-1"
        self.assertFalse(self.listings()[pasted]["sources"][0]["swept"])
        gone = {"id": pasted, "gone": False}
        for number in (8, 15, 22):
            self.agent.cfg = make_config(lambda raw: raw["limits"].update(max_listings_per_run=1))
            self.clock.jump_to("2026-10-%02dT13:00:00" % (4 + number))
            self.agent.call(pipeline.start)
            while True:
                task = self.agent.call(pipeline.next_task)
                if task["task"] != "sweep":
                    break
                self.agent.call(pipeline.ingest, self.agent.results_page(demo.WEEK2, task))
            self.agent.call(pipeline.finish)
        rec = self.listings()[pasted]
        self.assertEqual((rec["delisted_at"], rec["sources"][0]["missed_sweeps"]), (None, 0), gone)

    def test_changing_the_search_forces_a_new_full_sweep(self):
        self.day(1, demo.DAY1)
        self.agent.cfg = make_config(lambda raw: raw["search"].update(price_max=4600))
        self.clock.jump_to("2026-10-06T13:00:00")
        self.assertEqual(self.agent.call(pipeline.start)["plan"]["streeteasy"], "full sweep")
        self.assertEqual(self.agent.call(pipeline.next_task)["sweep"], "streeteasy:0:2800-4600")

    def test_full_can_be_forced(self):
        self.day(1, demo.DAY1)
        self.clock.jump_to("2026-10-06T13:00:00")
        started = self.agent.call(lambda cfg, store: pipeline.start(cfg, store, full=True))
        self.assertEqual(set(started["plan"].values()), set(["full sweep"]))

    def test_repost_seen_by_the_sweep_keeps_the_home_alive(self):
        self.weeks()
        rec = self.listings()[NO_ADDRESS]
        self.assertEqual(dict((e["source_id"], e["missed_sweeps"]) for e in rec["sources"]),
                         {"7900000003": 1, "7900000099": 0})
        self.assertEqual((rec["stage"], rec["delisted_at"]), ("done", None))

    def test_blocked_full_sweep_carries_over_and_proves_nothing(self):
        self.day(1, demo.DAY1)
        self.day(2, demo.DAY2)
        self.day(8, dict(demo.WEEK2, blocks={"streeteasy": "access denied"}))
        self.assertTrue(self.coverage()["streeteasy"]["cycle"])
        self.assertEqual(self.listings()[BROOME]["sources"][0]["missed_sweeps"], 0)
        result = self.day(9, demo.WEEK2)
        self.assertEqual(result["started"]["plan"]["streeteasy"], "newest first, then carry on with the full sweep")
        self.assertIsNone(self.coverage()["streeteasy"]["cycle"])
        self.assertEqual(self.listings()[BROOME]["sources"][0]["missed_sweeps"], 1)


def many_pages(count, per_page=3, start=0):
    """A long made-up StreetEasy search: `count` pages of listings that fail the card check."""
    return [[{"url": demo.SE % ("long-%d/%d" % (page, n)), "address": "%d Long Street #%d" % (100 + page, n),
              "rent": 9000, "beds": 1} for n in range(per_page)] for page in range(start, start + count)]


class LongSweeps(PipelineCase):
    def only_streeteasy(self, **limits):
        def change(raw):
            for name in ("zillow", "craigslist"):
                raw["search"]["sources"][name]["enabled"] = False
            raw["search"]["sources"]["streeteasy"]["max_pages"] = 50
            raw["limits"].update(limits)
        self.agent.cfg = make_config(change)

    def test_long_full_sweep_is_rationed_and_carries_over(self):
        self.only_streeteasy(max_search_pages_per_run=4)
        search = {"sweeps": {"streeteasy:0:2800-4400": {"pages": many_pages(10)}, "streeteasy:0:new": {"pages": many_pages(10)}}}
        result = self.day(1, search)
        self.assertEqual([t["page"] for t in self.agent.tasks("sweep")], [1, 2, 3, 4])
        self.assertTrue(result["finished"]["coverage"]["streeteasy"]["full_sweep_in_progress"])
        self.assertIn("carries on next run", summary.build(Store(self.dir).load()))

        # Each later run: one newest-first page (nothing new), then the full sweep, re-reading the last
        # page it reached in case listings have shifted across the boundary overnight.
        expected = {2: [4, 5, 6], 3: [6, 7, 8], 4: [8, 9, 10]}
        for number in (2, 3, 4):
            result = self.day(number, search)
            self.assertEqual([(t["mode"], t["page"]) for t in self.agent.tasks("sweep")],
                             [("incremental", 1)] + [("full", page) for page in expected[number]], number)
        self.assertFalse(result["finished"]["coverage"]["streeteasy"]["full_sweep_in_progress"])
        self.assertEqual(len(self.listings()), 30)

    def test_busy_top_ups_cannot_starve_the_full_sweep(self):
        """Three boroughs, each with something new every day, used to eat the whole page budget."""
        def change(raw):
            for name in ("zillow", "streeteasy"):
                raw["search"]["sources"][name]["enabled"] = False
            raw["search"]["sources"]["craigslist"]["max_pages"] = 50
        self.agent.cfg = make_config(change)
        post = lambda tag, day, p, n: {"url": demo.CL % (tag, "7%d%04d%02d" % (day, p, n)), "rent": 9000, "beds": 1,
                                       "title": "%s day %d page %d post %d with a long title" % (tag, day, p, n)}
        tags = ("mnh", "brk", "que")
        old = dict((tag, [[post(tag, 0, p, n) for n in range(2)] for p in range(4)]) for tag in tags)
        left = []
        for number in range(1, 9):
            searches = {}
            for i, tag in enumerate(tags):
                searches["craigslist:%d:2800-4400" % i] = {"pages": old[tag]}
                searches["craigslist:%d:new" % i] = {"pages": [[post(tag, number, 0, n) for n in range(2)]] + old[tag]}
            left.append(self.day(number, {"sweeps": searches})["finished"]["coverage"]["craigslist"]["searches_left"])
            if not left[-1]:
                break
        self.assertEqual(left[-1], 0, left)

    def test_top_up_cut_short_by_the_page_limit_says_so(self):
        self.only_streeteasy(max_search_pages_per_run=3)
        self.day(1, {"sweeps": {"streeteasy:0:2800-4400": {"cards": many_pages(1)[0]}}})
        result = self.day(2, {"sweeps": {"streeteasy:0:new": {"pages": many_pages(9, start=50)}}})
        self.assertEqual(len(self.agent.tasks("sweep")), 3)
        self.assertTrue(any("page limit was reached" in note for note in result["finished"]["notes"]), result["finished"]["notes"])
        self.assertIn("page limit was reached", summary.build(Store(self.dir).load()))

    def test_under_read_sweep_is_not_proof_of_absence(self):
        self.only_streeteasy()
        self.day(1, {"sweeps": {"streeteasy:0:2800-4400": {"cards": many_pages(1, per_page=10)[0]}}})
        for number, search in ((8, {"cards": many_pages(1, per_page=2)[0], "total_results": 10}), (15, {"cards": []})):
            result = self.day(number, {"sweeps": {"streeteasy:0:2800-4400": search}})
            self.assertEqual(result["finished"]["delisted"], [])
            self.assertEqual(set(e["missed_sweeps"] for r in self.listings().values() for e in r["sources"]), set([0]), number)
        self.assertTrue(any("only 2 cards were read" in note for run in Store(self.dir).load().data["runs"] for note in run["notes"]))

    def test_later_pages_have_their_own_url(self):
        self.only_streeteasy()
        self.day(1, {"sweeps": {"streeteasy:0:2800-4400": {"pages": many_pages(3)}}})
        self.assertTrue(self.agent.tasks("sweep")[2]["url"].endswith("&page=3"))

    def test_manual_style_hands_over_the_users_own_url_and_says_so(self):
        self.only_streeteasy()
        self.agent.cfg.sources["streeteasy"]["url_style"] = "manual"
        self.day(1, {"sweeps": {"streeteasy:0:2800-4400": {"pages": many_pages(2)}}})
        first, second = self.agent.tasks("sweep")
        self.assertEqual(first["url"], self.agent.cfg.sources["streeteasy"]["saved_search_urls"][0])
        self.assertTrue(first["filters"].startswith("NOT in the URL"))
        self.assertIn("click to the next page", second["reach_page"])

    def test_boroughs_take_turns(self):
        def change(raw):
            for name in ("zillow", "streeteasy"):
                raw["search"]["sources"][name]["enabled"] = False
            raw["search"]["sources"]["craigslist"]["max_pages"] = 50
        self.agent.cfg = make_config(change)
        pages = lambda tag: [[{"url": demo.CL % (tag, "79%06d%02d" % (p, n)), "title": "%s post %d %d long title" % (tag, p, n),
                               "rent": 9000, "beds": 1} for n in range(2)] for p in range(5)]
        self.day(1, {"sweeps": dict(("craigslist:%d:2800-4400" % i, {"pages": pages(tag)})
                                    for i, tag in enumerate(("mnh", "brk", "que")))})
        tasks = self.agent.tasks("sweep")
        self.assertEqual([(t["sweep"].split(":")[1], t["page"]) for t in tasks],
                         [("0", 1), ("0", 2), ("1", 1), ("1", 2), ("2", 1), ("2", 2)])
        # Consecutive pages, so on a site with only a pager the next page is one click away.
        self.assertIn("click to the next page", tasks[1]["reach_page"])
        self.assertEqual(tasks[1]["wait_between_clicks_seconds"], 30)     # stated in case the tab has moved

    def test_reaching_a_deep_page_by_pager_is_paced_and_paid_for(self):
        def change(raw):
            for name in ("zillow", "streeteasy"):
                raw["search"]["sources"][name]["enabled"] = False
            raw["search"]["sources"]["craigslist"].update(max_pages=50, subareas=["brk"])
            raw["limits"]["max_search_pages_per_run"] = 3
        self.agent.cfg = make_config(change)
        pages = [[{"url": demo.CL % ("brk", "79%06d" % p), "title": "brk post number %d long title" % p, "rent": 9000, "beds": 1}]
                 for p in range(8)]
        self.day(1, {"sweeps": {"craigslist:0:2800-4400": {"pages": pages}}})
        self.day(2, {"sweeps": {"craigslist:0:2800-4400": {"pages": pages}, "craigslist:0:new": {"pages": pages}}})
        deep = [t for t in self.agent.tasks("sweep") if t["mode"] == "full"][0]
        self.assertEqual((deep["page"], deep["wait_between_clicks_seconds"]), (3, 30))
        self.assertIn("click through the pager to page 3", deep["reach_page"])

    def test_site_cap_reached_mid_sweep_splits_the_band(self):
        self.only_streeteasy()
        self.agent.cfg.sources["streeteasy"]["max_pages"] = 2
        self.day(1, {"sweeps": {"streeteasy:0:2800-4400": {"pages": many_pages(5)}}})
        self.assertEqual([t["sweep"] for t in self.agent.tasks("sweep")],
                         ["streeteasy:0:2800-4400"] * 2 + ["streeteasy:0:2800-3600", "streeteasy:0:3601-4400"])

    def test_band_too_narrow_to_split_is_reported_and_proves_nothing(self):
        self.only_streeteasy(min_band_width=5000)
        self.agent.cfg.sources["streeteasy"]["max_pages"] = 2
        result = self.day(1, {"sweeps": {"streeteasy:0:2800-4400": {"pages": many_pages(5)}}})
        self.assertTrue(any("some listings may be missed" in note for note in result["finished"]["notes"]))

    def test_busy_newest_first_sweep_stops_at_the_cap_with_a_note(self):
        self.only_streeteasy()
        self.day(1, {"sweeps": {}})
        self.agent.cfg.sources["streeteasy"]["max_pages"] = 2
        result = self.day(2, {"sweeps": {"streeteasy:0:new": {"pages": many_pages(5)}}})
        self.assertEqual(len(self.agent.tasks("sweep")), 2)
        self.assertTrue(any("next full sweep will pick up the rest" in note for note in result["finished"]["notes"]))

    def test_pages_must_arrive_in_order_and_once(self):
        self.only_streeteasy()
        self.agent.call(pipeline.start)
        task = self.agent.call(pipeline.next_task)
        page = {"sweep": task["sweep"], "page": 1, "cards": [], "last_page": False}
        for bad in (dict(page, page=2), dict(page, page="1"), dict(page, page=None), dict(page, last_page="no")):
            with self.assertRaises(ScoutError):
                self.agent.call(pipeline.ingest, bad)
        self.assertTrue(self.agent.call(pipeline.ingest, page)["go_on"])
        with self.assertRaises(ScoutError) as caught:
            self.agent.call(pipeline.ingest, page)      # the reply was lost and the page is sent again
        self.assertIn("Expected page 2", caught.exception.errors[0])


class Rechecks(PipelineCase):
    QUIET = {"sweeps": {}}

    def gone_pages(self):
        return dict((rec_id, {"id": rec_id, "gone": True}) for rec_id in self.listings())

    def test_tours_are_rechecked_sooner_than_watches(self):
        self.day(1, demo.DAY1)
        self.day(2, demo.DAY2)      # Norman and Berry become TOURs on day 2
        self.day(3, self.QUIET)
        self.assertEqual(self.agent.tasks("enrich"), [])
        self.day(4, dict(self.QUIET, pages={NORMAN: demo.DAY2["pages"][NORMAN], BERRY: demo.DAY2["pages"][BERRY]}))
        self.assertEqual([(t["id"], t["why"]) for t in self.agent.tasks("enrich")],
                         [(BERRY, "recheck"), (NORMAN, "recheck")])

    def test_rechecks_are_rationed_stalest_first(self):
        self.agent.cfg = make_config(lambda raw: raw["refresh"].update(max_rechecks_per_run=2, full_sweep_every_days=99))
        self.day(1, demo.DAY1)
        self.day(6, dict(self.QUIET, pages=self.gone_pages()))
        first = set(t["id"] for t in self.agent.tasks("enrich"))
        self.day(7, dict(self.QUIET, pages=self.gone_pages()))
        second = set(t["id"] for t in self.agent.tasks("enrich"))
        self.assertEqual((len(first), len(second), len(first & second)), (2, 2, 0))

    def test_a_page_read_live_counts_as_seen(self):
        self.agent.cfg = make_config(lambda raw: raw["refresh"].update(full_sweep_every_days=99))
        self.day(1, demo.DAY1)
        self.day(5, dict(self.QUIET, pages=demo.DAY1["pages"]))
        self.assertEqual(len(self.agent.tasks("enrich")), 4)
        rec = self.listings()[E7]
        self.assertTrue(rec["last_seen"].startswith("2026-10-09"))
        self.day(6, dict(self.QUIET, pages=demo.DAY1["pages"]))
        self.assertEqual([t["id"] for t in self.agent.tasks("enrich")], [NO_ADDRESS])   # the one left over

    def test_recheck_that_changes_nothing_is_not_announced_again(self):
        self.agent.cfg = make_config(lambda raw: raw["refresh"].update(full_sweep_every_days=99))
        first = self.day(1, demo.DAY1)
        self.assertEqual(len(first["finished"]["announce"]["WATCH"]), 5)
        again = self.day(5, dict(self.QUIET, pages=demo.DAY1["pages"]))
        self.assertEqual(again["finished"]["announce"], {"TOUR": [], "WATCH": []})
        self.assertIn("## WATCH: 0 new", summary.build(Store(self.dir).load()))


class Resilience(PipelineCase):
    def test_dropped_session_resumes_where_it_stopped(self):
        uninterrupted = ScriptedAgent(EXAMPLE, self.dir + "-b", Clock("2026-10-05T13:00:00"))
        self.addCleanup(shutil.rmtree, self.dir + "-b", ignore_errors=True)
        uninterrupted.run(demo.DAY1)
        expected = [(t["task"], t.get("id") or t.get("sweep")) for t in uninterrupted.log]

        self.clock.jump_to("2026-10-05T13:00:00")
        for stop in (3, 4, 5, 2):  # four dropped sessions in a row
            self.assertTrue(self.agent.run(demo.DAY1, stop_after=stop)["interrupted"])
        result = self.agent.run(demo.DAY1)
        self.assertTrue(result["started"]["resumed"])
        self.assertEqual(result["finished"]["resumes"], 4)
        self.assertEqual([(t["task"], t.get("id") or t.get("sweep")) for t in self.agent.log], expected)

    def test_asking_twice_returns_the_same_task(self):
        self.agent.call(pipeline.start)
        first, second = self.agent.call(pipeline.next_task), self.agent.call(pipeline.next_task)
        self.assertEqual(first["sweep"], second["sweep"])

    def test_page_by_page_sweep_remembers_its_place(self):
        self.agent.call(pipeline.start)
        key = self.agent.call(pipeline.next_task)["sweep"]
        for page in (1, 2):
            self.agent.call(pipeline.ingest, {"sweep": key, "page": page, "cards": [], "last_page": False})
        self.agent.call(pipeline.start)      # the session dropped and came back
        pages = {}
        for _ in range(10):      # searches read less far go first
            task = self.agent.call(pipeline.next_task)
            pages[task["sweep"]] = task["page"]
            self.clock.tick(60)
            if task["sweep"] == key:
                break
            self.agent.call(pipeline.ingest, {"sweep": task["sweep"], "page": task["page"], "cards": [], "last_page": True})
        self.assertEqual(pages[key], 3)

    def test_daily_start_is_not_swallowed_by_a_leftover_no_sweep_run(self):
        self.agent.call(pipeline.start, False)       # "what about this listing?", never finished
        self.clock.tick(3600)
        started = self.agent.call(pipeline.start)
        self.assertTrue(started["resumed"])
        self.assertEqual(self.agent.call(pipeline.next_task)["task"], "sweep")

    def test_stale_run_is_closed_and_a_new_one_started(self):
        self.agent.run(demo.DAY1, stop_after=9)
        waiting = [r["id"] for r in self.listings().values() if r["stage"] != "done"]
        self.assertTrue(waiting)
        self.clock.jump_to("2026-10-06T13:00:00")
        started = self.agent.call(pipeline.start)
        self.assertEqual(started["closed_stale_run"], "run-20261005-130000")
        self.assertFalse(started["resumed"])
        self.assertEqual([r["id"] for r in self.listings().values() if r["stage"] != "done"], waiting)

    def test_rejected_payload_saves_nothing(self):
        self.agent.call(pipeline.start)
        before = json.dumps(Store(self.dir).load().data, sort_keys=True)
        with self.assertRaises(ScoutError) as caught:
            self.agent.call(pipeline.ingest, {"source": "manual", "cards": [
                {"url": "https://streeteasy.com/building/demo-a/1", "rent": 3000},
                {"url": "https://streeteasy.com/building/demo-b/2", "rent": "$3,100"}]})
        self.assertIn("plain number", caught.exception.errors[0])
        self.assertEqual(json.dumps(Store(self.dir).load().data, sort_keys=True), before)

    def test_listing_limit_defers_the_rest(self):
        self.agent.cfg = make_config(lambda raw: raw["limits"].update(max_listings_per_run=3))
        result = self.agent.run(demo.DAY1)
        self.assertEqual(len(self.agent.tasks("enrich")), 3)
        self.assertEqual(result["finished"]["left_to_open"], 5)
        self.assertIn("listing limit reached", result["why"])
        self.clock.jump_to("2026-10-06T13:00:00")
        self.agent.log = []
        self.agent.run(demo.DAY1)
        self.assertEqual(len(self.agent.tasks("enrich")), 3)   # picks up the backlog

    def test_time_limit_ends_the_run(self):
        self.agent.call(pipeline.start)
        self.clock.tick(61 * 60)
        self.assertEqual(self.agent.call(pipeline.next_task)["why"], "time limit reached")

    def one_site(self):
        def change(raw):
            for name in ("zillow", "craigslist"):
                raw["search"]["sources"][name]["enabled"] = False
        self.agent.cfg = make_config(change)
        self.agent.call(pipeline.start)

    def test_pages_on_one_site_are_never_closer_than_the_throttle(self):
        self.one_site()
        loads, clock = [], self.clock
        for page, (lag, reading) in enumerate(((0, 10), (12, 3), (1, 40), (9, 1), (0, 5)), 1):
            task = self.agent.call(pipeline.next_task)
            clock.tick(task["wait_seconds"] + lag)       # the agent waits as told, dawdles, then loads the page
            loads.append(clock.now)
            clock.tick(reading)
            self.agent.call(pipeline.ingest, {"sweep": task["sweep"], "page": page, "cards": [], "last_page": False})
        gaps = [(later - earlier).total_seconds() for earlier, later in zip(loads, loads[1:])]
        self.assertTrue(all(gap >= 20 for gap in gaps), gaps)

    def test_a_task_picked_up_again_after_a_stall_restarts_the_clock(self):
        self.one_site()
        self.agent.call(pipeline.next_task)
        self.clock.tick(600)                              # the session stalls before the page is loaded
        task = self.agent.call(pipeline.next_task)
        self.assertEqual((task["page"], task["wait_seconds"]), (1, 0))
        self.clock.tick(2)
        self.agent.call(pipeline.ingest, {"sweep": task["sweep"], "page": 1, "cards": [], "last_page": False})
        self.assertEqual(self.agent.call(pipeline.next_task)["wait_seconds"], 20)

    def test_asking_again_does_not_push_the_wait_back(self):
        self.agent.call(pipeline.start)
        first = self.agent.call(pipeline.next_task)
        self.agent.call(pipeline.ingest, {"sweep": first["sweep"], "page": 1, "cards": [], "last_page": False})
        self.agent.cfg.sources["zillow"]["enabled"] = self.agent.cfg.sources["craigslist"]["enabled"] = False
        self.assertEqual(self.agent.call(pipeline.next_task)["wait_seconds"], 20)
        self.clock.tick(5)
        self.assertEqual(self.agent.call(pipeline.next_task)["wait_seconds"], 15)

    def test_the_site_that_has_waited_longest_goes_next(self):
        self.agent.call(pipeline.start)
        order = []
        for _ in range(3):
            task = self.agent.call(pipeline.next_task)
            order.append((task["source"], task["wait_seconds"]))
            self.agent.call(pipeline.ingest, {"sweep": task["sweep"], "page": task["page"], "cards": [], "last_page": False})
        self.assertEqual(order, [("streeteasy", 0), ("zillow", 0), ("craigslist", 0)])

    def test_null_site_throttle_means_the_default(self):
        self.agent.cfg = make_config(lambda raw: raw["search"]["sources"]["craigslist"].update(throttle_seconds=None))
        self.assertEqual(pipeline._pause(self.agent.cfg, "craigslist"), 20)

    def test_second_command_at_once_is_refused(self):
        with opened(self.dir):
            with self.assertRaises(ScoutError):
                with opened(self.dir):
                    pass

    def test_store_file_is_valid_json_with_one_listing_per_line(self):
        self.day(1, demo.DAY1)
        with open(os.path.join(self.dir, "store.json"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertEqual(json.loads(text), Store(self.dir).load().data)
        self.assertEqual(sum(1 for line in text.split("\n") if line.startswith('  "')), 11)

    def test_backups_are_kept(self):
        self.day(1, demo.DAY1)
        self.day(2, demo.DAY2)
        self.assertEqual(len(os.listdir(os.path.join(self.dir, "backups"))), 1)


class HonestyChecks(PipelineCase):
    """The scout refuses payloads that skip a fact or cite text that is not there."""

    def setUp(self):
        super(HonestyChecks, self).setUp()
        self.agent.call(pipeline.start, False)
        self.agent.call(pipeline.ingest, {"source": "manual", "cards": [
            demo.DAY1["sweeps"]["streeteasy:0:2800-4400"]["cards"][0]]})
        self.page = copy.deepcopy(demo.DAY1["pages"][E7])

    def errors(self, function, payload):
        with self.assertRaises(ScoutError) as caught:
            self.agent.call(function, payload)
        return "\n".join(caught.exception.errors)

    def test_a_required_fact_cannot_be_silently_skipped(self):
        del self.page["facts"]["cats"]
        self.assertIn("cats: record it in `facts`, or list it in `unknown`", self.errors(pipeline.enrich, self.page))

    def test_a_fact_needs_provenance(self):
        del self.page["provenance"]["laundry"]
        self.assertIn("provenance.laundry", self.errors(pipeline.enrich, self.page))

    def test_a_quote_must_exist_in_the_page_text(self):
        self.page["provenance"]["cats"] = {"where": "description", "quote": "All pets welcome"}
        self.assertIn('"All pets welcome" is not in raw_text', self.errors(pipeline.enrich, self.page))

    def test_wrong_types_unknown_fields_and_calculated_fields(self):
        self.page["facts"].update(cats="yes", vibes="good", rent_gross=3950)
        text = self.errors(pipeline.enrich, self.page)
        for expected in ("facts.cats must be one of", "facts.vibes: not a known field", "facts.rent_gross: this is calculated"):
            self.assertIn(expected, text)

    def test_missing_address_needs_a_reason(self):
        del self.page["address"]
        self.assertIn("address_unknown_reason", self.errors(pipeline.enrich, self.page))

    def test_value_and_unknown_together_is_a_contradiction(self):
        self.page["unknown"]["cats"] = "not stated"
        self.assertIn("pick one", self.errors(pipeline.enrich, self.page))

    def test_scoring_a_gate_failure_is_refused(self):
        self.page["facts"]["cats"] = "not_allowed"
        self.page["provenance"]["cats"] = {"where": "policies"}
        self.assertEqual(self.agent.call(pipeline.enrich, self.page)["verdict"], "PASS")
        self.assertIn("did not get through the gate", self.errors(pipeline.score, demo.DAY1["scores"][E7]))

    def test_scoring_before_enriching_is_refused(self):
        self.assertIn("has not been enriched", self.errors(pipeline.score, demo.DAY1["scores"][E7]))

    def test_gone_page_is_marked_off_market(self):
        self.assertEqual(self.agent.call(pipeline.enrich, {"id": E7, "gone": True})["result"], "delisted")
        self.assertEqual(self.agent.call(pipeline.next_task)["task"], "finish")


class Dedupe(PipelineCase):
    def setUp(self):
        super(Dedupe, self).setUp()
        self.agent.call(pipeline.start, False)

    def ingest(self, *cards):
        return self.agent.call(pipeline.ingest, {"source": "manual", "cards": list(cards)})["counts"]

    def test_address_found_on_the_detail_page_merges_twins(self):
        self.ingest(demo.DAY1["sweeps"]["streeteasy:0:2800-4400"]["cards"][0])
        self.agent.call(pipeline.enrich, demo.DAY1["pages"][E7])
        self.ingest({"url": demo.CL % ("mnh", "7911111111"), "title": "East Village gem by owner", "rent": 3900, "beds": 1})
        page = copy.deepcopy(demo.DAY1["pages"][E7])
        page["id"] = "craigslist-7911111111"
        result = self.agent.call(pipeline.enrich, page)
        self.assertEqual((result["result"], result["id"]), ("merged", E7))
        recs = self.listings()
        self.assertEqual(list(recs), [E7])
        self.assertEqual(len(recs[E7]["sources"]), 2)

    def test_same_building_without_a_unit_is_linked_not_merged(self):
        self.ingest({"url": demo.SE % "500-grand-street/3a", "address": "500 Grand Street #3A", "rent": 3500, "beds": 1},
                    {"url": demo.CL % ("mnh", "7922222222"), "address": "500 Grand St", "title": "1BR on Grand", "rent": 3550,
                     "beds": 1},
                    {"url": demo.SE % "500-grand-street/9f", "address": "500 Grand Street #9F", "rent": 3500, "beds": 1})
        recs = self.listings()
        self.assertEqual(len(recs), 3)
        self.assertEqual(sorted(recs["craigslist-7922222222"]["possible_duplicates"]),
                         ["streeteasy-demo-500-grand-street-3a", "streeteasy-demo-500-grand-street-9f"])
        self.assertEqual(recs["streeteasy-demo-500-grand-street-3a"]["possible_duplicates"], ["craigslist-7922222222"])

    def test_email_alert_dedupes_against_the_site_it_came_from(self):
        card = demo.DAY1["sweeps"]["streeteasy:0:2800-4400"]["cards"][0]
        self.ingest(card)
        counts = self.ingest(dict(card, source="email_alerts"))
        self.assertEqual((counts["new"], counts["unchanged"]), (0, 1))

    def test_same_card_twice_in_one_sweep(self):
        card = demo.DAY1["sweeps"]["streeteasy:0:2800-4400"]["cards"][0]
        counts = self.ingest(card, card)
        self.assertEqual((counts["new"], counts["unchanged"]), (1, 1))

    def test_lookalike_titles_at_different_addresses_stay_apart(self):
        self.ingest({"url": demo.CL % ("brk", "7933333331"), "title": "Renovated 1BR no fee great light", "rent": 3300,
                     "beds": 1, "address": "10 Jay St #2"},
                    {"url": demo.CL % ("brk", "7933333332"), "title": "Renovated 1BR no fee great light", "rent": 3300,
                     "beds": 1, "address": "99 Jay St #5"})
        self.assertEqual(len(self.listings()), 2)


class ConfigChanges(PipelineCase):
    def setUp(self):
        super(ConfigChanges, self).setUp()
        self.day(1, demo.DAY1)

    def rejudge(self, change):
        self.agent.cfg = make_config(change)
        return self.agent.call(pipeline.rejudge_all)

    def test_loosening_the_gate_requeues_for_scoring_without_reopening_pages(self):
        result = self.rejudge(lambda raw: rule(raw, "cats").update(op="at_least", value="case_by_case"))
        rec = self.listings()[LIC]
        self.assertEqual((rec["stage"], rec["verdict"], rec["queue_reason"]), ("needs_score", None, "config_changed"))
        self.assertEqual(result["requeued"], 1)

    def test_tightening_the_gate_needs_no_browser(self):
        self.rejudge(lambda raw: rule(raw, "rent").update(value=3500))
        recs = self.listings()
        self.assertEqual((recs[E7]["verdict"], recs[E7]["stage"]), ("PASS", "done"))
        self.assertEqual(recs[ASTORIA]["verdict"], "WATCH")

    def test_new_weights_rerank_without_rescoring(self):
        def change(raw):
            weights = {"light": 0.40, "layout": 0.20, "building": 0.10, "commute": 0.15, "practical": 0.10, "outdoor": 0.05}
            for crit in raw["rubric"]:
                crit["weight"] = weights[crit["id"]]
        self.rejudge(change)
        rec = self.listings()[E7]
        self.assertEqual((rec["verdict"], rec["stage"], rec["rubric"]["score"]), ("TOUR", "done", 78.5))

    def test_rewording_a_criterion_rescans_only_that_criterion(self):
        self.rejudge(lambda raw: raw["rubric"][5].update(guidance="Private outdoor space only."))
        self.assertEqual(self.listings()[E7]["stage"], "needs_score")
        self.clock.jump_to("2026-10-06T13:00:00")
        self.agent.call(pipeline.start, False)
        task = self.agent.call(pipeline.next_task)
        self.assertEqual((task["task"], [c["id"] for c in task["criteria"]]), ("score", ["outdoor"]))

    def test_switched_off_criterion_is_not_asked_for(self):
        def change(raw):
            raw["rubric"][0]["weight"] = 0.35
            raw["rubric"][5].update(weight=0, guidance="Private outdoor space only.")
        self.rejudge(change)
        self.assertEqual(self.listings()[E7]["stage"], "done")
        # Turning it back on asks for it, because it has never been scored with these words.
        self.rejudge(lambda raw: raw["rubric"][5].update(guidance="Private outdoor space only."))
        self.assertEqual(self.listings()[E7]["stage"], "needs_score")

    def test_start_notices_a_changed_config_by_itself(self):
        self.agent.cfg = make_config(lambda raw: raw["verdicts"]["tour"].update(min_score=70))
        self.clock.jump_to("2026-10-06T13:00:00")
        started = self.agent.call(pipeline.start)
        self.assertIn({"id": E7, "from": "WATCH", "to": "TOUR"}, started["config_changed"]["verdicts_changed"])


class HumanSide(PipelineCase):
    def setUp(self):
        super(HumanSide, self).setUp()
        self.day(1, demo.DAY1)

    def store_call(self, function, *args):
        with opened(self.dir) as store:
            return function(store, *args)

    def test_find_by_loose_address(self):
        self.assertEqual([h["id"] for h in self.store_call(pipeline.find, "88 berry")], [BERRY])

    def test_feedback_marks_it_toured(self):
        result = self.store_call(pipeline.add_feedback, BERRY, True, "Loved the balcony")
        self.assertEqual((result["status"], result["tours_recorded"]), ("toured", 1))
        self.assertEqual(self.listings()[BERRY]["feedback"][0]["scout_verdict"], "WATCH")

    def test_rejected_listing_is_never_reopened(self):
        self.store_call(pipeline.set_status, BERRY, "rejected", "too far")
        self.day(2, demo.DAY2)
        self.assertEqual([t["id"] for t in self.agent.tasks("enrich")], [NORMAN])


if __name__ == "__main__":
    unittest.main()
