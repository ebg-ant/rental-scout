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
        self.assertIn("%22min%22%3A3601", self.agent.tasks("sweep")[3]["url"])

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
    def test_day_two_only_evaluates_new_or_changed(self):
        self.day(1, demo.DAY1)
        result = self.day(2, demo.DAY2)
        self.assertEqual([t["id"] for t in self.agent.tasks("enrich")], [BERRY, NORMAN])
        self.assertEqual([t["why"] for t in self.agent.tasks("enrich")], ["price_changed", "new"])
        self.assertEqual([t["id"] for t in self.agent.tasks("score")], [NORMAN])
        counts = result["finished"]["counts"]
        self.assertEqual((counts["new"], counts["price_changed"]), (1, 2))
        recs = self.listings()
        self.assertEqual(recs[NORMAN]["verdict"], "TOUR")
        self.assertEqual(recs[BERRY]["verdict"], "TOUR")   # cats now stated, old scores reused
        self.assertEqual([p["rent"] for p in recs[BERRY]["price_history"]], [4300, 4150])
        self.assertIn({"event": "verdict", "detail": "WATCH -> TOUR"},
                      [{"event": h["event"], "detail": h["detail"]} for h in recs[BERRY]["history"]])

    def test_price_drop_that_cannot_help_costs_no_browser_time(self):
        self.day(1, demo.DAY1)
        self.day(2, demo.DAY2)
        rec = self.listings()[LIC]
        self.assertEqual(rec["facts"]["rent_advertised"], 3450)
        self.assertEqual((rec["verdict"], rec["stage"]), ("PASS", "done"))

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
        # A blocked site proves nothing about what is still listed there.
        self.assertEqual(self.listings()[ASTORIA]["sources"][0]["missed_sweeps"], 0)

    def test_delisted_after_two_full_sweeps_without_it(self):
        self.day(1, demo.DAY1)
        self.day(2, demo.DAY2)
        self.assertIsNone(self.listings()[FRANKLIN]["delisted_at"])
        result = self.day(3, demo.DAY3)
        self.assertEqual(result["finished"]["delisted"], [FRANKLIN])
        self.assertEqual(self.listings()[FRANKLIN]["status"], "delisted")
        self.assertEqual(self.agent.tasks("enrich"), [])

    def test_relisting_brings_it_back(self):
        for number, fixture in ((1, demo.DAY1), (2, demo.DAY2), (3, demo.DAY3)):
            self.day(number, fixture)
        day4 = copy.deepcopy(demo.DAY3)
        day4["sweeps"]["streeteasy:0:2800-4400"]["cards"].append(demo.DAY1["sweeps"]["streeteasy:0:2800-4400"]["cards"][5])
        day4["pages"][FRANKLIN] = demo.DAY1["pages"][FRANKLIN]
        result = self.day(4, day4)
        self.assertEqual(result["finished"]["counts"]["relisted"], 1)
        rec = self.listings()[FRANKLIN]
        self.assertEqual((rec["status"], rec["delisted_at"], rec["verdict"]), ("active", None, "WATCH"))
        self.assertEqual([t["why"] for t in self.agent.tasks("enrich")], ["relisted"])

    def test_shortlisted_listing_unseen_for_days_is_rechecked(self):
        self.day(1, demo.DAY1)
        quiet = {"sweeps": dict((key, {"cards": [], "stopped_early": True}) for key in (
            "streeteasy:0:2800-4400", "zillow:0:2800-4400", "craigslist:0:2800-4400", "craigslist:1:2800-4400",
            "craigslist:2:2800-4400")), "pages": dict((i, {"id": i, "gone": True}) for i in self.listings())}
        self.day(2, quiet)
        self.assertEqual(self.agent.tasks("enrich"), [])     # one day is too soon
        result = self.day(5, quiet)
        self.assertEqual(set(t["why"] for t in self.agent.tasks("enrich")), set(["recheck"]))
        self.assertEqual(len(self.agent.tasks("enrich")), 5)  # the five WATCH listings, nothing else
        self.assertEqual(result["finished"]["delisted"], [])  # partial sweeps never delist by absence
        self.assertEqual(self.listings()[E7]["status"], "delisted")  # the page itself was gone


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
        self.agent.call(pipeline.ingest, {"sweep": key, "cards": [], "pages_read": 2, "complete": False})
        task = self.agent.call(pipeline.next_task)
        self.assertEqual((task["sweep"], task["resume_after_page"]), (key, 2))

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

    def test_throttle_is_per_site(self):
        self.agent.call(pipeline.start)
        self.assertEqual(self.agent.call(pipeline.next_task)["wait_seconds"], 0)
        self.clock.tick(5)
        self.assertEqual(self.agent.call(pipeline.next_task)["wait_seconds"], 15)

    def test_second_command_at_once_is_refused(self):
        with opened(self.dir):
            with self.assertRaises(ScoutError):
                with opened(self.dir):
                    pass

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
