"""Images, deep links, the refresh queue and the shell scripts."""

import copy
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from scout import pipeline, report, rubric, summary
from scout.store import Store, opened
from scout.util import ScoutError
from tests import demo as demo_builder
from tests.fixtures import nyc_demo as demo
from tests.helpers import EXAMPLE, ROOT, make_config
from tests.sim import Clock, ScriptedAgent
from tests.test_pipeline import ASTORIA, BERRY, BROOME, E7, FRANKLIN, LIC, NO_ADDRESS, PipelineCase


class PhotoCapture(PipelineCase):
    def setUp(self):
        super(PhotoCapture, self).setUp()
        self.agent.call(pipeline.start, False)
        self.agent.call(pipeline.ingest, {"source": "manual", "cards": [
            demo.DAY1["sweeps"]["streeteasy:0:2800-4400"]["cards"][0]]})
        self.page = copy.deepcopy(demo.DAY1["pages"][E7])

    def errors(self, payload):
        with self.assertRaises(ScoutError) as caught:
            self.agent.call(pipeline.enrich, payload)
        return "\n".join(caught.exception.errors)

    def test_photos_and_plans_are_saved_in_order_with_captions(self):
        self.page["photos"][1] = {"url": self.page["photos"][1], "caption": "bedroom"}
        self.agent.call(pipeline.enrich, self.page)
        rec = self.listings()[E7]
        self.assertEqual(len(rec["photos"]), 14)
        self.assertTrue(rec["photos"][0]["url"].endswith("/1.jpg"))
        self.assertEqual(rec["photos"][1]["caption"], "bedroom")
        self.assertEqual(len(rec["floor_plans"]), 1)

    def test_photos_cannot_be_silently_skipped(self):
        del self.page["photos"]
        self.assertIn("photos: list the listing's photo URLs", self.errors(self.page))
        self.page["photos_unavailable_reason"] = "gallery images are drawn on a canvas; no addresses in the page"
        self.agent.call(pipeline.enrich, self.page)
        self.assertEqual(self.listings()[E7]["photos"], [])

    def test_a_listing_with_no_photos_needs_none(self):
        self.page["photos"] = []
        self.page["facts"]["photo_count"] = 0
        self.agent.call(pipeline.enrich, self.page)
        self.assertIn("no_photos", [f["id"] for f in self.listings()[E7]["flags"]])

    def test_only_web_addresses_are_accepted(self):
        self.page["photos"] = ["javascript:alert(1)", "photo 1", {"caption": "no url"}]
        text = self.errors(self.page)
        for index in range(3):
            self.assertIn("photos[%d]: must be an http(s) image URL" % index, text)

    def test_long_galleries_are_capped(self):
        self.page["photos"] = [demo.PHOTO % ("x", n) for n in range(60)]
        self.agent.call(pipeline.enrich, self.page)
        self.assertEqual(len(self.listings()[E7]["photos"]), pipeline.MAX_IMAGES)

    def test_score_task_says_how_many_images_are_on_file(self):
        self.agent.call(pipeline.enrich, self.page)
        task = self.agent.call(pipeline.next_task)
        self.assertEqual((task["photos_saved"], task["floor_plans_saved"]), (14, 1))


class EvidenceLinks(unittest.TestCase):
    def setUp(self):
        self.cfg = make_config()

    def check(self, cid, *evidence, **kw):
        entry = {"score": 7, "rationale": "ok", "evidence": list(evidence),
                 "unavailable": [{"type": t, "reason": "not relevant to this test"} for t in
                                 ("photos", "floor_plan", "satellite", "map", "text", "records")]}
        return rubric.validate(self.cfg, {"criteria": {cid: entry}}, "some text", [cid],
                               kw.get("photos", 5), kw.get("plans", 0))

    def test_photo_numbers_must_exist(self):
        self.assertEqual(self.check("light", {"type": "photos", "photos": [1, 5]})[1], [])
        for bad in ([0], [6], [], ["2"], [True], 3):
            errors = self.check("light", {"type": "photos", "photos": bad})[1]
            self.assertTrue(any("between 1 and 5" in e for e in errors), bad)

    def test_photo_numbers_are_kept_for_the_report(self):
        clean, _ = self.check("light", {"type": "photos", "photos": [2, 3], "note": "shadows"})
        self.assertEqual(clean["light"]["evidence"], [{"type": "photos", "photos": [2, 3], "note": "shadows"}])

    def test_a_claim_about_all_photos_may_use_words(self):
        self.assertEqual(self.check("outdoor", {"type": "photos", "ref": "all 5 photos"})[1], [])

    def test_map_views_must_be_links(self):
        for kind in ("satellite", "street_view", "map"):
            errors = self.check("light", {"type": kind, "ref": "Google Maps satellite view"})[1]
            self.assertTrue(any("URL of the exact view" in e for e in errors), kind)
            self.assertEqual(self.check("light", {"type": kind, "ref": "https://www.google.com/maps/@40.7,-73.9,120m"})[1], [])

    def test_floor_plan_can_point_at_the_saved_plan(self):
        self.assertEqual(self.check("layout", {"type": "floor_plan"}, plans=1)[1], [])
        self.assertTrue(self.check("layout", {"type": "floor_plan"}, plans=0)[1])


class ReportMedia(PipelineCase):
    def view(self, rec_id):
        store = Store(self.dir).load()
        return report.listing_view(self.agent.cfg, store.listings[rec_id], store.data["runs"][-1]["id"])

    def setUp(self):
        super(ReportMedia, self).setUp()
        self.day(1, demo.DAY1)

    def test_listing_carries_photos_plans_and_lookup_links(self):
        view = self.view(E7)
        self.assertEqual((len(view["photos"]), len(view["floor_plans"])), (14, 1))
        links = dict((link["id"], link) for link in view["links"])
        self.assertIn("212+East+7th+Street", links["directions"]["url"])
        self.assertIn("AddressQuery=212+East+7th", links["fema_flood"]["url"])
        self.assertEqual(links["hpd"]["label"], "HPD violations")
        self.assertEqual(view["page_url"], demo.SE % "212-east-7-street/4b")

    def test_address_links_are_left_out_when_there_is_no_address(self):
        ids = [link["id"] for link in self.view(NO_ADDRESS)["links"]]
        self.assertNotIn("directions", ids)
        self.assertNotIn("fema_flood", ids)
        self.assertIn("hpd", ids)     # a plain lookup page needs no address

    def test_unopened_listing_still_shows_its_card_photo(self):
        rejected = next(r for r in self.listings().values() if r["verdict_reason"].startswith("Filtered out"))
        self.assertEqual(len(self.view(rejected["id"])["photos"]), 1)

    def test_summary_links_to_the_listing_and_to_its_scorecard(self):
        text = summary.build(Store(self.dir).load())
        self.assertIn("[streeteasy](%s)" % (demo.SE % "88-berry-street/6c"), text)
        self.assertIn("[scorecard](../report.html#%s)" % BERRY, text)

    def test_street_view_link_appears_once_coordinates_are_known(self):
        with opened(self.dir) as store:
            store.listings[E7]["address"].update(lat=40.7262, lng=-73.9818)
        links = dict((link["id"], link["url"]) for link in self.view(E7)["links"])
        self.assertTrue(links["street_view"].endswith("viewpoint=40.7262,-73.9818"), links["street_view"])


class RefreshQueue(PipelineCase):
    def setUp(self):
        super(RefreshQueue, self).setUp()
        self.day(1, demo.DAY1)
        self.clock.jump_to("2026-10-06T09:00:00")

    def refresh(self, **kw):
        with opened(self.dir) as store:
            return pipeline.queue_refresh(store, **kw)

    def test_default_is_the_live_shortlist(self):
        self.assertEqual(sorted(self.refresh()["queued"]), sorted([E7, BERRY, FRANKLIN, ASTORIA, NO_ADDRESS]))

    def test_all_adds_opened_passes_but_never_unopened_ones(self):
        queued = self.refresh(everything=True)["queued"]
        self.assertIn(LIC, queued)
        self.assertIn(BROOME, queued)
        self.assertEqual(len(queued), 8)

    def test_older_than_days(self):
        self.assertEqual(self.refresh(older_than_days=2)["count"], 0)
        self.assertEqual(self.refresh(older_than_days=0.5)["count"], 5)

    def test_rejected_listings_are_left_alone(self):
        with opened(self.dir) as store:
            pipeline.set_status(store, BERRY, "rejected")
        self.assertNotIn(BERRY, self.refresh()["queued"])

    def test_unknown_id_is_an_error(self):
        with self.assertRaises(ScoutError):
            self.refresh(ids=["nope"])

    def test_refresh_run_rereads_pages_without_sweeping(self):
        self.refresh(ids=[BERRY, E7])
        pages = {BERRY: demo.DAY2["pages"][BERRY], E7: {"id": E7, "gone": True}}
        self.agent.log = []
        self.agent.call(pipeline.start, False)
        while True:
            task = self.agent.call(pipeline.next_task)
            self.agent.log.append(task)
            if task["task"] == "finish":
                break
            self.assertEqual((task["task"], task["why"]), ("enrich", "recheck"))
            self.assertIn("previous_facts", task)
            self.agent.call(pipeline.enrich, pages[task["id"]])
        self.agent.call(pipeline.finish)
        recs = self.listings()
        self.assertEqual(len(self.agent.log), 3)
        self.assertEqual(recs[BERRY]["verdict"], "TOUR")         # cats now stated, rent lower
        self.assertEqual(recs[BERRY]["price_history"][-1]["rent"], 4150)
        self.assertEqual(recs[E7]["status"], "delisted")

    def test_naming_a_delisted_listing_gives_it_another_look(self):
        self.agent.call(pipeline.start, False)
        self.agent.call(pipeline.enrich, {"id": E7, "gone": True})
        self.agent.call(pipeline.finish)
        self.assertNotIn(E7, self.refresh()["queued"])
        self.assertEqual(self.refresh(ids=[E7])["queued"], [E7])
        self.agent.call(pipeline.start, False)
        self.assertEqual(self.agent.call(pipeline.next_task)["id"], E7)


@unittest.skipUnless(shutil.which("bash"), "needs bash")
class Scripts(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="scout-test-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.addCleanup(os.environ.pop, "SCOUT_NOW", None)
        demo_builder.build(self.dir)
        # A stand-in for the Claude CLI that records how it was called.
        self.bin = os.path.join(self.dir, "bin")
        os.makedirs(self.bin)
        with open(os.path.join(self.bin, "claude"), "w") as handle:
            handle.write('#!/bin/sh\nprintf "%s\\n" "$@" > "$FAKE_CLAUDE_LOG"\n')
        os.chmod(os.path.join(self.bin, "claude"), 0o755)
        self.log = os.path.join(self.dir, "claude.log")

    def run_script(self, name, *args, **kw):
        path = os.pathsep.join(([self.bin] if kw.get("with_claude", True) else []) + ["/usr/bin", "/bin"])
        python = shutil.which("python3")
        env = {"PATH": path, "PYTHON": python, "SCOUT_CONFIG": EXAMPLE, "SCOUT_DATA": self.dir, "OPEN_REPORT": "0",
               "SCOUT_NOW": "2026-10-06T15:00:00", "FAKE_CLAUDE_LOG": self.log, "HOME": self.dir}
        done = subprocess.run(["bash", os.path.join(ROOT, "scripts", name)] + list(args), cwd=self.dir, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        return done.returncode, done.stdout

    def claude_args(self):
        with open(self.log) as handle:
            return handle.read()

    def test_run_hands_the_daily_prompt_to_claude_with_chrome(self):
        code, out = self.run_script("run.sh")
        self.assertEqual(code, 0, out)
        self.assertTrue(self.claude_args().startswith("--chrome\n-p\nRun the rental scout"))

    def test_refresh_queues_then_hands_over(self):
        code, out = self.run_script("refresh.sh")
        self.assertEqual(code, 0, out)
        self.assertIn('"count": 6', out)
        self.assertIn("already queued", self.claude_args())
        waiting = [r for r in Store(self.dir).load().listings.values() if r["refresh"] == "recheck"]
        self.assertEqual(len(waiting), 6)

    def test_refresh_with_nothing_to_do_does_not_call_claude(self):
        code, out = self.run_script("refresh.sh", "--older-than-days", "30")
        self.assertEqual(code, 0, out)
        self.assertFalse(os.path.exists(self.log))

    def test_without_the_cli_the_prompt_is_printed_to_paste(self):
        code, out = self.run_script("run.sh", with_claude=False)
        self.assertEqual(code, 0, out)
        self.assertIn("paste", out)
        self.assertIn("Read agent/RUNBOOK.md", out)

    def test_rebuild_needs_no_browser_and_rewrites_the_report(self):
        report_path = os.path.join(self.dir, "report.html")
        os.unlink(report_path)
        code, out = self.run_script("rebuild.sh")
        self.assertEqual(code, 0, out)
        self.assertTrue(os.path.exists(report_path))
        self.assertFalse(os.path.exists(self.log))

    def test_a_broken_config_stops_the_script_before_claude(self):
        env_config = os.path.join(ROOT, "configs", "nyc.template.yaml")
        path = os.pathsep.join([self.bin, "/usr/bin", "/bin"])
        done = subprocess.run(["bash", os.path.join(ROOT, "scripts", "run.sh")], cwd=self.dir, env={
            "PATH": path, "PYTHON": shutil.which("python3"), "SCOUT_CONFIG": env_config, "SCOUT_DATA": self.dir,
            "FAKE_CLAUDE_LOG": self.log, "HOME": self.dir}, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn(b"Still marked TODO", done.stdout)
        self.assertFalse(os.path.exists(self.log))


class DemoReport(unittest.TestCase):
    def test_demo_builds_with_local_placeholder_images(self):
        folder = tempfile.mkdtemp(prefix="scout-test-")
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        self.addCleanup(os.environ.pop, "SCOUT_NOW", None)
        paths, _ = demo_builder.build(folder)
        with open(paths["report"], encoding="utf-8") as handle:
            data = json.loads(re.search(r"var DATA = (.*);\n", handle.read()).group(1).replace("<\\/", "</"))
        for item in data["listings"]:
            for image in item["photos"] + item["floor_plans"]:
                self.assertTrue(os.path.exists(os.path.join(folder, image["url"])), image["url"])


if __name__ == "__main__":
    unittest.main()
