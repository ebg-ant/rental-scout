import unittest

from scout import config
from scout.explain import explain
from scout._vendor import yaml
from tests.helpers import EXAMPLE, TEMPLATE, errors_from, make_config, raw_example, rule


class ConfigTests(unittest.TestCase):
    def test_example_is_valid_and_reads_back(self):
        cfg, warnings = config.load(EXAMPLE)
        self.assertEqual(warnings, [])
        text = explain(cfg)
        self.assertIn("$4,000", text)
        self.assertIn("Light and exposure: 25%", text)

    def test_template_fails_only_on_todos(self):
        with self.assertRaises(config.ScoutError) as caught:
            config.load(TEMPLATE)
        self.assertTrue(all(e.startswith("Still marked TODO") for e in caught.exception.errors))
        self.assertIn("Still marked TODO: gate.rules[rent].value", caught.exception.errors)

    def test_template_and_example_share_everything_but_the_answers(self):
        with open(TEMPLATE, encoding="utf-8") as handle:
            template = yaml.safe_load(handle)
        example = raw_example()
        for section in ("rubric", "verdicts", "risk", "costs", "limits", "capture", "calibration"):
            self.assertEqual(template[section], example[section], section)
        self.assertEqual([r["id"] for r in template["gate"]["rules"]], [r["id"] for r in example["gate"]["rules"]])
        self.assertEqual([r["id"] for r in template["prefilters"]], [r["id"] for r in example["prefilters"]])

    def test_todo_inside_a_disabled_block_is_ignored(self):
        def change(raw):
            raw["search"]["sources"]["zillow"].update(enabled=False, saved_search_urls=["TODO"])
        self.assertFalse(make_config(change).sources["zillow"]["enabled"])

    def test_weights_must_sum_to_one(self):
        errors = errors_from(lambda raw: raw["rubric"][0].update(weight=0.3))
        self.assertTrue(any("add up to 1.050" in e for e in errors), errors)

    def test_all_problems_are_reported_at_once(self):
        def change(raw):
            rule(raw, "beds")["field"] = "bedroms"
            rule(raw, "laundry")["op"] = "roughly"
            rule(raw, "cats")["value"] = "sure"
            rule(raw, "commute")["on_unknown"] = True   # what YAML makes of a bare `yes`
            raw["limits"]["max_listings_per_run"] = 0
        errors = "\n".join(errors_from(change))
        for expected in ("unknown field 'bedroms'", "unknown op 'roughly'", "cats must be one of",
                         "on_unknown should be fail, pass or flag", "max_listings_per_run"):
            self.assertIn(expected, errors)

    def test_symbol_and_word_ops_are_the_same(self):
        cfg = make_config(lambda raw: rule(raw, "rent").update(op="<="))
        self.assertEqual(cfg.gate["rules"][0]["op"], "lte")

    def test_order_ops_need_an_ordered_field(self):
        errors = errors_from(lambda raw: rule(raw, "unfurnished").update(op="at_least"))
        self.assertTrue(any("has no order" in e for e in errors), errors)

    def test_value_from_is_resolved_and_dates_become_text(self):
        cfg = make_config()
        available = next(r for r in cfg.gate["rules"] if r["id"] == "available")
        self.assertEqual(available["value"], "2026-12-15")

    def test_anchors_need_three_concrete_levels(self):
        errors = errors_from(lambda raw: raw["rubric"][0].update(anchors={10: "great", 0: "bad"}))
        self.assertTrue(any("at least 3 anchors" in e for e in errors), errors)

    def test_cross_checks(self):
        self.assertTrue(any("neighborhoods or zips" in e for e in errors_from(
            lambda raw: raw["search"]["areas"].update(neighborhoods=[], zips=[]))))
        self.assertTrue(any("needs an address" in e for e in errors_from(
            lambda raw: raw["search"]["commute_anchor"].update(address=None))))

    def test_enabled_source_needs_a_search(self):
        errors = errors_from(lambda raw: raw["search"]["sources"]["streeteasy"].update(saved_search_urls=[]))
        self.assertTrue(any("no saved_search_urls" in e for e in errors), errors)

    def test_bad_regex_is_caught(self):
        errors = errors_from(lambda raw: raw["prefilters"][0].update(reject_if_any=["re:("]))
        self.assertTrue(any("not a valid pattern" in e for e in errors), errors)

    def test_sweep_narrower_than_the_gate_warns(self):
        raw = raw_example()
        raw["search"]["price_max"] = 3500
        _, warnings = config.validate(raw)
        self.assertTrue(any("will miss" in w for w in warnings), warnings)

    def test_custom_field_can_be_gated_on(self):
        def change(raw):
            raw["custom_fields"] = [{"id": "gas_stove", "type": "bool", "help": "True if the stove is gas"}]
            raw["gate"]["rules"].append({"field": "gas_stove", "op": "is", "value": True})
        self.assertIn("gas_stove", make_config(change).registry)

    def test_weight_change_alters_judge_hash_but_not_criterion_hashes(self):
        def change(raw):
            raw["rubric"][0]["weight"], raw["rubric"][1]["weight"] = 0.30, 0.15
        before, after = make_config(), make_config(change)
        self.assertNotEqual(before.judge_hash(), after.judge_hash())
        self.assertEqual(before.criterion_hashes(), after.criterion_hashes())


if __name__ == "__main__":
    unittest.main()
