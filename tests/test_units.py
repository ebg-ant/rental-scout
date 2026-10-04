"""Address keys, rent maths, prefilters, gate, flags, rubric, verdicts, sweeps."""

import unittest
from urllib.parse import parse_qs, unquote, urlsplit

from scout import address, derive, flags, gate, pipeline, prefilter, rubric, sweeps, verdict
from tests.helpers import make_config, rule

ALIASES = {"avenue of the americas": "6th avenue"}


def key(text, **kw):
    return address.parse(text, street_aliases=ALIASES, **kw)


class AddressTests(unittest.TestCase):
    def test_same_unit_written_differently(self):
        pairs = [
            ("245 East 10th Street, Apt. 4-B, New York NY 10003", "245 E 10th St #4b"),
            ("30-15 31st Avenue, Unit 2F, Astoria, NY 11106", "3015 31 Ave #2f"),
            ("120 Avenue of the Americas #12", "120 6th Ave Apt 12, Manhattan"),
            ("55 Saint Marks Place 3rd Floor, New York, NY", "55 St. Marks Pl FL 3"),
            ("1 Fifth Avenue PH", "1 5th Ave #PH"),
            ("1 North 4th Place #30A", "1 N 4th Pl Apt 30A, Brooklyn, NY 11249"),
        ]
        for first, second in pairs:
            self.assertEqual(address.match(key(first), key(second)), "same", (first, second))

    def test_lettered_avenues_keep_their_letter(self):
        self.assertEqual(key("100 Avenue A")["street"], "100 ave a")
        self.assertIsNone(key("100 Avenue A")["unit"])

    def test_no_street_number_is_not_an_address(self):
        for text in ("E 10th St near Ave A", "Williamsburg", "", None, "Bedford Ave & N 7th St"):
            self.assertIsNone(key(text), text)

    def test_different_units_and_boroughs_do_not_match(self):
        self.assertEqual(address.match(key("245 E 10th St #4B"), key("245 E 10th St #5B")), "different")
        self.assertEqual(address.match(key("100 Broadway #2, Brooklyn, NY 11211"),
                                       key("100 Broadway #2, New York, NY 10005")), "different")

    def test_missing_unit_is_only_a_maybe(self):
        self.assertEqual(address.match(key("245 E 10th St"), key("245 E 10th St #4B")), "maybe")

    def test_explicit_parts_win(self):
        parsed = key("245 E 10th St", unit="Apt 4B", zip_code=10003, borough="New York")
        self.assertEqual((parsed["unit"], parsed["zip"], parsed["borough"]), ("4B", "10003", "manhattan"))


class RentTests(unittest.TestCase):
    def test_no_concession(self):
        self.assertEqual(derive.rents({"rent_advertised": 4000, "free_months": 0})[:2], (4000, 4000))

    def test_unknown_concession_leaves_the_other_figure_unknown(self):
        self.assertEqual(derive.rents({"rent_advertised": 3700, "rent_advertised_is": "net_effective"})[:2], (None, 3700))
        self.assertEqual(derive.rents({"rent_advertised": 3700, "rent_advertised_is": "gross"})[:2], (3700, None))
        self.assertEqual(derive.rents({"rent_advertised": 3700})[:2], (None, None))

    def test_gross_advertised_with_free_month(self):
        gross, net, _ = derive.rents({"rent_advertised": 4300, "rent_advertised_is": "gross",
                                      "free_months": 1, "lease_term_months": 13})
        self.assertEqual(gross, 4300)
        self.assertAlmostEqual(net, 4300 * 12 / 13.0)

    def test_net_advertised_with_free_month(self):
        gross, net, _ = derive.rents({"rent_advertised": 3300, "rent_advertised_is": "net_effective",
                                      "free_months": 1, "lease_term_months": 12})
        self.assertEqual(net, 3300)
        self.assertAlmostEqual(gross, 3600)

    def test_missing_pieces_stay_unknown(self):
        self.assertEqual(derive.rents({})[:2], (None, None))
        self.assertEqual(derive.rents({"rent_advertised": 4000, "free_months": 1})[:2], (None, None))
        self.assertEqual(derive.rents({"rent_advertised": 4000, "free_months": 1,
                                       "rent_advertised_is": "gross"})[:2], (4000, None))

    def test_broker_fee_is_spread_over_the_lease(self):
        cfg = make_config()
        base = {"rent_advertised": 3000, "free_months": 0, "lease_term_months": 12}
        all_in = lambda **more: derive.derive(dict(base, **more), None, cfg)["monthly_all_in"]
        self.assertEqual(all_in(tenant_pays_broker_fee=True, broker_fee_amount=3600), 3300)
        self.assertEqual(all_in(tenant_pays_broker_fee=False), 3000)
        self.assertIsNone(all_in())                                  # who pays is unknown
        self.assertIsNone(all_in(tenant_pays_broker_fee=True))       # how much is unknown

    def test_target_area_uses_aliases_and_zips(self):
        areas = {"neighborhoods": ["East Village"], "zips": [11211], "aliases": {"East Village": ["Alphabet City"]}}
        self.assertIs(derive.in_target_area({"neighborhood": "alphabet city"}, areas), True)
        self.assertIs(derive.in_target_area({"neighborhood": "Harlem", "zip": "11211"}, areas), True)
        self.assertIs(derive.in_target_area({"neighborhood": "Harlem"}, areas), False)
        self.assertIsNone(derive.in_target_area({}, areas))


class PrefilterTests(unittest.TestCase):
    def setUp(self):
        self.cfg = make_config()

    def rejected_by(self, *texts):
        return prefilter.run(self.cfg, *texts).get("rule")

    def test_noise_is_rejected(self):
        self.assertEqual(self.rejected_by("Fully FURNISHED 1BR"), "furnished")
        self.assertEqual(self.rejected_by("Sunny 1BR", "6 month lease only"), "short_term")
        self.assertEqual(self.rejected_by("Sublet my place Jan-June"), "sublet")
        self.assertEqual(self.rejected_by("Private room in 3BR"), "room_share")
        self.assertEqual(self.rejected_by("Great 1BR, sorry no pets"), "no_pets")

    def test_lookalikes_are_not(self):
        for text in ("Unfurnished 1BR", "1 month free on a 13 month lease", "12 month lease", "Huge living room in a prewar",
                     "Perfect for roommates", "2 months free!", "1 month security deposit"):
            self.assertIsNone(self.rejected_by(text), text)

    def test_disabled_rule_is_skipped(self):
        cfg = make_config(lambda raw: raw["prefilters"][-1].update(enabled=False))
        self.assertEqual(prefilter.run(cfg, "no pets")["result"], "pass")

    def test_reject_carries_its_evidence(self):
        result = prefilter.run(self.cfg, "Lovely place, fully furnished, bring a suitcase")
        self.assertEqual(result["matched"], "furnished")
        self.assertIn("fully furnished", result["excerpt"])


GOOD = {"rent_advertised": 3900, "free_months": 0, "beds": 1, "neighborhood": "East Village", "cats": "allowed", "lease_term_months": 12,
        "furnished": False, "sublet": False, "room_share": False, "floor": 2, "elevator": False,
        "laundry": "in_building", "commute_minutes": 30, "available_date": "2026-12-01"}


class GateTests(unittest.TestCase):
    def setUp(self):
        self.cfg = make_config()

    def run_gate(self, cfg=None, **changes):
        cfg = cfg or self.cfg
        facts = dict(GOOD)
        facts.update(changes)
        facts = dict((k, v) for k, v in facts.items() if v is not None)
        return gate.run(cfg, derive.values_for(facts, None, cfg))

    def row(self, result, rule_id):
        return next(r for r in result["rules"] if r["id"] == rule_id)

    def test_clean_pass(self):
        result = self.run_gate()
        self.assertEqual((result["result"], result["fails"], result["unknowns"]), ("pass", 0, 0))

    def test_dealbreaker_fails_alone(self):
        result = self.run_gate(cats="case_by_case")
        self.assertEqual(result["result"], "fail")
        self.assertIn("Cats case by case (needs allowed)", gate.fail_text(result))

    def test_one_soft_miss_survives_two_do_not(self):
        self.assertEqual(self.run_gate(laundry="none")["result"], "pass")
        self.assertEqual(self.run_gate(laundry="none")["fails"], 1)
        self.assertEqual(self.run_gate(laundry="none", commute_minutes=60)["result"], "fail")

    def test_unknown_handling_follows_the_rule(self):
        flagged = self.run_gate(cats=None)
        self.assertEqual((flagged["result"], flagged["unknowns"]), ("pass", 1))
        self.assertEqual(gate.unknown_labels(flagged), ["Cats"])
        let_through = self.run_gate(furnished=None)
        self.assertEqual(self.row(let_through, "unfurnished")["status"], "unknown")
        self.assertEqual(self.row(let_through, "unfurnished")["counted_as"], "pass")
        strict = make_config(lambda raw: rule(raw, "cats").update(on_unknown="fail"))
        self.assertEqual(self.run_gate(strict, cats=None)["result"], "fail")

    def test_unless_is_three_valued(self):
        status = lambda **kw: self.row(self.run_gate(**kw), "walkup")["status"]
        self.assertEqual(status(floor=6, elevator=True), "pass")
        self.assertEqual(status(floor=6, elevator=False), "fail")
        self.assertEqual(status(floor=6, elevator=None), "unknown")
        self.assertEqual(status(floor=2, elevator=None), "pass")
        self.assertEqual(status(floor=None, elevator=True), "pass")
        self.assertEqual(status(floor=None, elevator=False), "unknown")

    def test_ordered_enums_and_dates(self):
        self.assertEqual(self.row(self.run_gate(laundry="in_unit"), "laundry")["status"], "pass")
        self.assertEqual(self.row(self.run_gate(available_date="2027-01-01"), "available")["status"], "fail")

    def test_rent_rule_uses_net_effective(self):
        result = self.run_gate(rent_advertised=4300, rent_advertised_is="gross", free_months=1, lease_term_months=13)
        self.assertEqual(self.row(result, "rent")["status"], "pass")
        self.assertEqual(self.row(result, "rent")["actual"], "$3,969")

    def test_card_check_only_rejects_clear_trusted_misses(self):
        check = lambda **card: gate.card_check(self.cfg, pipeline._card_values(self.cfg, {"card": card, "address_key": None}))
        self.assertIsNone(check(rent=4500, beds=1))       # within the 15% margin
        self.assertEqual(check(rent=4700, beds=1)["rule"], "rent")
        self.assertEqual(check(rent=3000, beds=0)["rule"], "beds")
        self.assertIsNone(check(rent=3000))                # missing is not a miss
        self.assertIsNone(check(rent=3000, beds=1, neighborhood="Harlem"))  # not trust_card

    def test_rules_can_be_left_out_by_the_facts_they_need(self):
        facts = dict(GOOD, rent_advertised=9000)
        values = derive.values_for(facts, None, self.cfg)
        self.assertEqual(gate.run(self.cfg, values)["result"], "fail")
        without = gate.run(self.cfg, values, ignoring=["rent_advertised"])
        self.assertEqual(without["result"], "pass")
        self.assertNotIn("rent", [row["id"] for row in without["rules"]])


def record(facts, **extra):
    rec = {"facts": facts, "card": {}, "sources": [], "possible_duplicates": [], "raw_text": ""}
    rec.update(extra)
    return rec


class FlagTests(unittest.TestCase):
    def setUp(self):
        self.cfg = make_config()

    def ids(self, facts, cfg=None, comps=lambda beds: (None, 0), key={"street": "1 a st"}, **extra):
        cfg = cfg or self.cfg
        facts = dict({"free_months": 0}, **facts)
        rec = record(facts, **extra)
        return dict((f["id"], f) for f in flags.run(cfg, rec, derive.values_for(facts, key, cfg), comps))

    def test_quiet_listing_has_no_flags(self):
        self.assertEqual(self.ids({"rent_advertised": 3500, "beds": 1}), {})

    def test_price_floor_uses_config_then_comps_then_stays_silent(self):
        facts = {"rent_advertised": 2000, "beds": 1}
        cfg = make_config(lambda raw: raw["risk"].update(reference_rents={1: 4000}))
        self.assertIn("your config", self.ids(facts, cfg)["price_too_low"]["message"])
        self.assertIn("median of 9", self.ids(facts, comps=lambda beds: (4000, 9))["price_too_low"]["message"])
        self.assertNotIn("price_too_low", self.ids(facts, comps=lambda beds: (4000, 3)))
        self.assertNotIn("price_too_low", self.ids({"rent_advertised": 2500, "beds": 1}, cfg))

    def test_scam_signals_are_high_risk(self):
        found = self.ids({"rent_advertised": 3000}, raw_text="I am out of the country, will mail you the keys")
        self.assertEqual(found["scam_language"]["severity"], "high")
        self.assertTrue(flags.risk_verdict(self.cfg, list(found.values())))

    def test_one_low_flag_is_a_caution_two_are_a_pass(self):
        one = list(self.ids({"rent_advertised": 3000}, key=None).values())
        self.assertEqual([f["id"] for f in one], ["no_address"])
        self.assertIsNone(flags.risk_verdict(self.cfg, one))
        two = list(self.ids({"rent_advertised": 3000, "photos_recency": "stale"}, key=None).values())
        self.assertTrue(flags.risk_verdict(self.cfg, two))

    def test_cost_flags(self):
        found = self.ids({"rent_advertised": 4300, "rent_advertised_is": "gross", "free_months": 1,
                          "lease_term_months": 13, "tenant_pays_broker_fee": True, "broker_fee_amount": 5000,
                          "security_deposit": 8600, "application_fee": 100})
        for flag_id in ("tenant_broker_fee", "concession", "deposit_high", "application_fee_high", "income_short"):
            self.assertIn(flag_id, found)
        self.assertIn("FARE Act", found["tenant_broker_fee"]["message"])
        self.assertIn("$172,000", found["income_short"]["message"])

    def test_unclear_rent_is_said_out_loud(self):
        self.assertIn("rent_unclear", self.ids({"rent_advertised": 3000, "free_months": None}))

    def test_available_before_you_want_to_move(self):
        self.assertIn("14 days before", self.ids({"rent_advertised": 3000, "available_date": "2026-11-01"})["available_early"]["message"])
        self.assertNotIn("available_early", self.ids({"rent_advertised": 3000, "available_date": "2026-11-20"}))

    def test_long_listed(self):
        import os
        os.environ["SCOUT_NOW"] = "2026-10-05T13:00:00"
        self.addCleanup(os.environ.pop, "SCOUT_NOW", None)
        self.assertIn("Listed 65 days ago", self.ids({"rent_advertised": 3000, "listed_date": "2026-08-01"})["long_listed"]["message"])
        self.assertNotIn("long_listed", self.ids({"rent_advertised": 3000, "listed_date": "2026-09-20"}))

    def test_info_flags_and_disable(self):
        facts = {"rent_advertised": 3000, "level": "garden", "flood_zone": "high", "noise_sources": ["elevated J/M/Z"],
                 "rent_stabilized": True}
        self.assertEqual(set(self.ids(facts)), set(["flood_risk", "noise", "rent_stabilized"]))
        cfg = make_config(lambda raw: raw["risk"].update(disable=["noise"]))
        self.assertNotIn("noise", self.ids(facts, cfg))


def full_scores(cfg, value=8):
    return dict((c["id"], {"score": value}) for c in cfg.rubric)


class RubricTests(unittest.TestCase):
    def setUp(self):
        self.cfg = make_config()
        self.text = "Laundry in building. Dishwasher."

    def check(self, entry, cid="practical"):
        return rubric.validate(self.cfg, {"criteria": {cid: entry}}, self.text, only=[cid])

    def test_good_score(self):
        clean, errors = self.check({"score": 6, "rationale": "ok", "evidence": [
            {"type": "text", "quote": "laundry in  BUILDING"}]})
        self.assertEqual(errors, [])
        self.assertFalse(clean["practical"]["limited_evidence"])

    def test_invented_quote_is_rejected(self):
        _, errors = self.check({"score": 9, "rationale": "ok", "evidence": [
            {"type": "text", "quote": "washer and dryer in unit"}]})
        self.assertTrue(any("not in the saved listing text" in e for e in errors), errors)

    def test_score_without_evidence_or_rationale_is_rejected(self):
        _, errors = self.check({"score": 5})
        self.assertTrue(any("rationale" in e for e in errors))
        self.assertTrue(any("at least one piece of evidence" in e for e in errors))

    def test_null_needs_a_reason(self):
        self.assertTrue(self.check({"score": None})[1])
        clean, errors = self.check({"score": None, "unknown_reason": "Listing text is two lines long"})
        self.assertEqual(errors, [])
        self.assertIsNone(clean["practical"]["score"])

    def test_required_evidence_can_be_declared_unavailable(self):
        entry = {"score": 6, "rationale": "ok", "evidence": [{"type": "photos", "ref": "photo 1"}]}
        self.assertTrue(any("floor_plan" in e for e in self.check(entry, "layout")[1]))
        entry["unavailable"] = [{"type": "floor_plan", "reason": "none on the listing"}]
        clean, errors = self.check(entry, "layout")
        self.assertEqual(errors, [])
        self.assertTrue(clean["layout"]["limited_evidence"])

    def test_missing_and_unexpected_criteria(self):
        _, errors = rubric.validate(self.cfg, {"criteria": {"vibes": {"score": 10}}}, self.text)
        self.assertTrue(any(e.startswith("vibes: not a criterion") for e in errors))
        self.assertTrue(any(e.startswith("light: missing") for e in errors))

    def test_totals_with_unknowns(self):
        scores = full_scores(self.cfg, 8)
        self.assertEqual(rubric.totals(self.cfg, scores),
                         {"score": 80.0, "floor": 80.0, "ceiling": 80.0, "unknown_weight": 0.0})
        scores["light"] = {"score": None}
        self.assertEqual(rubric.totals(self.cfg, scores),
                         {"score": 80.0, "floor": 60.0, "ceiling": 85.0, "unknown_weight": 0.25})
        self.assertIsNone(rubric.totals(self.cfg, {})["score"])


class VerdictTests(unittest.TestCase):
    def setUp(self):
        self.cfg = make_config()
        self.open_gate = {"result": "pass", "fails": 0, "unknowns": 0, "rules": []}

    def decide(self, value=8, gate_result=None, flag_list=(), unknown=()):
        scores = full_scores(self.cfg, value)
        for cid in unknown:
            scores[cid] = {"score": None}
        return verdict.decide(self.cfg, {"result": "pass"}, None, gate_result or self.open_gate, list(flag_list),
                              scores, rubric.totals(self.cfg, scores))[0]

    def test_thresholds(self):
        self.assertEqual(self.decide(7.5), "TOUR")
        self.assertEqual(self.decide(7.4), "WATCH")
        self.assertEqual(self.decide(6), "WATCH")
        self.assertEqual(self.decide(5.9), "PASS")

    def test_anything_open_holds_a_tour_at_watch(self):
        unknown_row = {"label": "Cats", "counted_as": "unknown"}
        miss_row = {"label": "Laundry", "counted_as": "fail", "actual": "none", "expected": "at least in building"}
        self.assertEqual(self.decide(9, dict(self.open_gate, unknowns=1, rules=[unknown_row])), "WATCH")
        self.assertEqual(self.decide(9, dict(self.open_gate, fails=1, rules=[miss_row])), "WATCH")
        self.assertEqual(self.decide(9, unknown=["light"]), "TOUR")             # exactly 25% is allowed
        self.assertEqual(self.decide(9, unknown=["light", "outdoor"]), "WATCH")  # 35% is not

    def test_low_score_with_room_to_grow_is_kept(self):
        self.assertEqual(self.decide(5, unknown=["light", "layout", "building"]), "WATCH")  # ceiling 80
        self.assertEqual(self.decide(3, unknown=["light"]), "PASS")                         # ceiling 47.5

    def test_order_of_precedence(self):
        risk = [{"id": "owner_remote", "kind": "risk", "severity": "high", "message": "away"}]
        failed = {"result": "fail", "fails": 1, "unknowns": 0, "rules": [
            {"label": "Cats", "counted_as": "fail", "actual": "not allowed", "expected": "allowed"}]}
        self.assertEqual(self.decide(9, failed, risk), "PASS (risk)")
        self.assertEqual(self.decide(9, failed), "PASS")
        rejected = verdict.decide(self.cfg, {"result": "reject", "reason": "Sublet", "matched": "sublet"},
                                  None, None, [], {}, None)
        self.assertEqual(rejected, ("PASS", 'Filtered out: Sublet ("sublet")'))

    def test_not_judged_yet(self):
        self.assertEqual(verdict.decide(self.cfg, {"result": "pass"}, None, None, [], {}, None), (None, None))
        self.assertEqual(verdict.decide(self.cfg, {"result": "pass"}, None, self.open_gate, [], {}, None), (None, None))


class SweepTests(unittest.TestCase):
    def test_streeteasy_band_is_newest_first(self):
        url = sweeps.band_url("streeteasy", "https://streeteasy.com/for-rent/nyc/price:2000-4000%7Carea:102,119%7Cbeds:1"
                                            "?sort_by=price_asc&page=3", 3000, 3500)
        self.assertEqual(unquote(urlsplit(url).path), "/for-rent/nyc/price:3000-3500|area:102,119|beds:1")
        self.assertEqual(urlsplit(url).query, "sort_by=listed_desc")
        kept = sweeps.band_url("streeteasy", "https://streeteasy.com/for-rent/nyc/beds%3E=1", 3000, 3500)
        self.assertIn("/price:3000-3500%7Cbeds%3E=1?", kept)      # the user's own filters come back as they were written
        self.assertIn("/for-rent/east-village/price:1-2?", sweeps.band_url("streeteasy", "https://streeteasy.com/for-rent/east-village", 1, 2))
        self.assertIsNone(sweeps.band_url("streeteasy", "https://streeteasy.com/saved_search/123", 1, 2))

    def test_zillow_band_is_newest_first(self):
        base = ("https://www.zillow.com/new-york-ny/rentals/?searchQueryState=%7B%22pagination%22%3A%7B%22currentPage"
                "%22%3A2%7D%2C%22filterState%22%3A%7B%22fr%22%3A%7B%22value%22%3Atrue%7D%7D%7D")
        state = parse_qs(urlsplit(sweeps.band_url("zillow", base, 3000, 3500)).query)["searchQueryState"][0]
        self.assertIn('"mp":{"min":3000,"max":3500}', state)
        self.assertIn('"sort":{"value":"days"}', state)
        self.assertNotIn("pagination", state)
        self.assertIsNone(sweeps.band_url("zillow", "https://www.zillow.com/new-york-ny/rentals/", 1, 2))

    def test_craigslist_plan(self):
        mine = sweeps.plan(make_config(), "craigslist", sweeps.FULL)
        self.assertEqual([s["key"] for s in mine], ["craigslist:%d:2800-4400" % n for n in range(3)])
        url = sweeps.band_url("craigslist", mine[1]["base_url"], mine[1]["min"], mine[1]["max"])
        self.assertEqual(url, "https://newyork.craigslist.org/search/brk/apa?sort=date&min_price=2800&max_price=4400")

    def test_incremental_and_inbox_plans(self):
        cfg = make_config(lambda raw: raw["search"]["sources"]["email_alerts"].update(enabled=True))
        self.assertEqual([s["key"] for s in sweeps.plan(cfg, "streeteasy", sweeps.INCREMENTAL)], ["streeteasy:0:new"])
        self.assertEqual([s["key"] for s in sweeps.plan(cfg, "email_alerts", sweeps.FULL)], ["email_alerts:0:inbox"])

    def test_later_pages(self):
        first = sweeps.band_url("streeteasy", "https://streeteasy.com/for-rent/nyc/beds:1", 1, 2)
        self.assertEqual(sweeps.page_url("streeteasy", first, 1), first)
        self.assertTrue(sweeps.page_url("streeteasy", first, 3).endswith("&page=3"))
        zillow = sweeps.band_url("zillow", "https://www.zillow.com/x/?searchQueryState=%7B%7D", 1, 2)
        self.assertIn("currentPage%22%3A4", sweeps.page_url("zillow", zillow, 4))
        self.assertIsNone(sweeps.page_url("craigslist", "https://newyork.craigslist.org/search/brk/apa", 2))

    def test_split_covers_the_band_without_overlap_or_slivers(self):
        sweep = sweeps.plan(make_config(), "streeteasy", sweeps.FULL)[0]
        low, high = sweeps.split(sweep, 100)
        self.assertEqual((low["min"], low["max"], high["min"], high["max"]), (2800, 3600, 3601, 4400))
        self.assertIsNone(sweeps.split(dict(sweep, min=3000, max=3150), 100))     # halves would be under 100 wide
        self.assertIsNotNone(sweeps.split(dict(sweep, min=3000, max=3199), 100))

    def test_band_width_pre_splits_full_sweeps_only(self):
        cfg = make_config(lambda raw: raw["search"]["sources"]["streeteasy"].update(band_width=800))
        self.assertEqual([(s["min"], s["max"]) for s in sweeps.plan(cfg, "streeteasy", sweeps.FULL)],
                         [(2800, 3599), (3600, 4399), (4400, 4400)])
        self.assertEqual(len(sweeps.plan(cfg, "streeteasy", sweeps.INCREMENTAL)), 1)

    def test_search_hash_follows_what_a_sweep_would_cover(self):
        before = sweeps.search_hash(make_config(), "streeteasy")
        self.assertEqual(before, sweeps.search_hash(make_config(lambda raw: raw["limits"].update(throttle_seconds=30)), "streeteasy"))
        self.assertNotEqual(before, sweeps.search_hash(make_config(lambda raw: raw["search"].update(price_max=5000)), "streeteasy"))


if __name__ == "__main__":
    unittest.main()
