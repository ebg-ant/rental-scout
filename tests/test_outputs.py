"""The command line, the report page, the calibration report and the shipped examples."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from scout import calibrate, pipeline, report
from scout.store import Store, opened
from tests.fixtures import nyc_demo as demo
from tests.helpers import EXAMPLE, ROOT, TEMPLATE, make_config
from tests.sim import Clock, ScriptedAgent

EXAMPLES = os.path.join(ROOT, "agent", "examples")


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="scout-test-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.addCleanup(os.environ.pop, "SCOUT_NOW", None)


class CommandLine(TempDirCase):
    def scout(self, *args, **kw):
        env = dict(os.environ, SCOUT_NOW=kw.get("now", "2026-10-05T13:00:00"))
        done = subprocess.run([sys.executable, "-m", "scout", "--config", kw.get("config", EXAMPLE), "--data", self.dir]
                              + list(args), cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, input=kw.get("stdin"))
        return done.returncode, done.stdout, done.stderr

    def json(self, *args, **kw):
        code, out, err = self.scout(*args, **kw)
        self.assertEqual(err, "")
        return code, json.loads(out)

    def test_shipped_examples_run_through_the_real_commands(self):
        self.assertEqual(self.json("start")[0], 0)
        code, task = self.json("next")
        self.assertEqual((task["task"], task["sweep"]), ("sweep", "streeteasy:0:2800-4400"))
        code, result = self.json("ingest", os.path.join(EXAMPLES, "cards.json"))
        self.assertEqual((code, result["counts"]["new"]), (0, 3))
        code, result = self.json("enrich", os.path.join(EXAMPLES, "enrich.json"))
        self.assertEqual((code, result["gate"], result["gate_unknowns"]), (0, "pass", ["Cats"]))
        code, task = self.json("next")
        self.assertEqual((task["task"], task["id"]), ("score", "streeteasy-demo-88-berry-street-6c"))
        self.assertEqual(len(task["criteria"]), 6)
        self.assertTrue(all(c["anchors"] and c["guidance"] for c in task["criteria"]))

    def test_score_example(self):
        self.json("start", "--no-sweep")
        card = demo.DAY1["sweeps"]["craigslist:1:2800-4400"]["cards"][0]
        self.json("ingest", "-", stdin=json.dumps({"source": "manual", "cards": [card]}))
        self.json("enrich", "-", stdin=json.dumps(demo.DAY1["pages"]["craigslist-7900000003"]))
        code, result = self.json("score", os.path.join(EXAMPLES, "score.json"))
        self.assertEqual((code, result["verdict"], result["unknown_weight"]), (0, "WATCH", 0.4))
        code, out, _ = self.scout("finish")
        self.assertEqual(code, 0)
        self.assertIn("## WATCH: 1 new", out)
        self.assertIn("1 listing handed in by hand", out)
        self.assertTrue(os.path.exists(os.path.join(self.dir, "shortlist.csv")))
        self.assertTrue(os.path.exists(os.path.join(self.dir, "report.html")))
        self.assertEqual(len(os.listdir(os.path.join(self.dir, "summaries"))), 1)

    def test_problems_exit_2_with_plain_errors(self):
        code, result = self.json("validate", config=TEMPLATE)
        self.assertEqual((code, result["ok"]), (2, False))
        code, result = self.json("next", config=os.path.join(self.dir, "missing.yaml"))
        self.assertEqual(code, 2)
        self.assertIn("SETUP_INTERVIEW", result["hint"])
        code, result = self.json("ingest", "-", stdin="{not json")
        self.assertEqual(code, 2)
        code, result = self.json("finish")
        self.assertEqual(result["errors"], ["No run is open"])

    def test_enrich_task_lists_what_to_capture_and_where_to_look(self):
        self.json("start", "--no-sweep")
        self.json("ingest", "-", stdin=json.dumps({"source": "manual", "cards": [
            demo.DAY1["sweeps"]["streeteasy:0:2800-4400"]["cards"][0]]}))
        code, task = self.json("next")
        self.assertEqual(task["task"], "enrich")
        fields = [item["field"] for item in task["capture"]]
        for name in ("rent_advertised", "free_months", "cats", "elevator", "tenant_pays_broker_fee", "flood_zone"):
            self.assertIn(name, fields)
        self.assertNotIn("dogs", fields)              # that rule is switched off
        self.assertNotIn("rent_net_effective", fields)  # calculated
        links = dict((link["id"], link["url"]) for link in task["helper_links"])
        self.assertIn("origin=212+East+7th+Street+%234B%2C+East+Village%2C+New+York%2C+NY", links["directions"])
        self.assertIn("Grand+Central", links["directions"])
        self.assertNotIn("street_view", links)        # no coordinates yet

    def test_fields_and_validate_are_readable(self):
        code, fields = self.json("fields")
        self.assertEqual(fields["laundry"]["type"], "enum (none < in_building < in_unit)")
        code, out, _ = self.scout("validate")
        self.assertIn("Must-haves", out)


class ReportPage(TempDirCase):
    def build(self, mutate=None):
        agent = ScriptedAgent(EXAMPLE, self.dir, Clock("2026-10-05T13:00:00"))
        agent.run(demo.DAY1)
        with opened(self.dir) as store:
            if mutate:
                mutate(store)
            return report.build(agent.cfg, store)

    def data(self, html):
        blob = re.search(r"var DATA = (.*);\n", html).group(1)
        return json.loads(blob)

    def test_every_listing_is_in_the_page_with_reason_and_links(self):
        data = self.data(self.build())
        self.assertEqual(len(data["listings"]), 11)
        for item in data["listings"]:
            self.assertTrue(item["reason"])
            self.assertTrue(item["sources"][0]["url"].startswith("https://"))
        berry = next(i for i in data["listings"] if "Berry" in i["title"])
        self.assertEqual((round(berry["rent_net"]), berry["rent_gross"]), (3969, 4300))
        self.assertEqual(len(berry["criteria"]), 6)
        self.assertTrue(all(c["evidence"] for c in berry["criteria"]))
        self.assertIn("Confirm: Cats", berry["questions"])

    def test_page_is_self_contained(self):
        html = self.build()
        self.assertNotIn("__SCOUT_DATA__", html)
        self.assertEqual(re.findall(r'<(?:script|link)[^>]+(?:src|href)=', html), [])

    def test_listing_text_cannot_break_out_of_the_script_block(self):
        def mutate(store):
            rec = next(iter(store.listings.values()))
            rec["card"]["title"] = rec["verdict_reason"] = "</script><img src=x onerror=alert(1)>"
            rec["address"] = None
            rec["card"].pop("address", None)
        html = self.build(mutate)
        self.assertEqual(html.count("</script>"), 1)
        self.assertTrue(any(i["title"].startswith("</script>") for i in self.data(html)["listings"]))


class Calibration(TempDirCase):
    def setUp(self):
        super(Calibration, self).setUp()
        self.cfg = make_config(lambda raw: raw["calibration"].update(min_tours=4))
        agent = ScriptedAgent(EXAMPLE, self.dir, Clock("2026-10-05T13:00:00"))
        agent.run(demo.DAY1)

    def feedback(self, *rows):
        with opened(self.dir) as store:
            for rec_id, liked, why in rows:
                pipeline.add_feedback(store, rec_id, liked, why)

    def test_waits_for_enough_tours(self):
        self.feedback(("zillow-900002", True, "nice"))
        text = calibrate.report(self.cfg, Store(self.dir).load())
        self.assertIn("1 of 4 tours recorded", text)
        self.assertNotIn("Suggestion", text)

    def test_reports_disagreements_and_suggests_without_applying(self):
        self.feedback(("streeteasy-demo-88-berry-street-6c", True, "balcony and laundry"),
                      ("streeteasy-demo-212-east-7-street-4b", False, "window AC and stairs"),
                      ("streeteasy-demo-150-franklin-street-2r", False, "no laundry is a pain"),
                      ("zillow-900002", True, "easy living"))
        store = Store(self.dir).load()
        data = calibrate.analyse(self.cfg, store)
        self.assertEqual(len(data["disagreements"]), 2)   # both liked homes were only WATCH
        self.assertEqual(data["proposal"]["raise"], "practical")
        self.assertAlmostEqual(sum(data["proposal"]["weights"].values()), 1.0)
        self.assertIn("Nothing has been changed", calibrate.report(self.cfg, store))
        self.assertEqual([c["weight"] for c in self.cfg.rubric], [0.25, 0.20, 0.15, 0.15, 0.15, 0.10])


class Docs(unittest.TestCase):
    def test_files_named_by_tasks_and_docs_exist(self):
        cfg = make_config()
        for source in cfg.sources:
            self.assertTrue(os.path.exists(os.path.join(ROOT, "agent", "sources", source + ".md")), source)
        pattern = re.compile(r"`((?:agent|configs|docs)/[\w./-]+\.(?:md|yaml|json))`")
        for folder, _, names in os.walk(ROOT):
            if ".git" in folder or "_vendor" in folder:
                continue
            for name in names:
                if name.endswith(".md"):
                    with open(os.path.join(folder, name), encoding="utf-8") as handle:
                        for path in pattern.findall(handle.read()):
                            self.assertTrue(os.path.exists(os.path.join(ROOT, path)), "%s names %s" % (name, path))

    def test_commands_named_in_docs_exist(self):
        from scout.cli import build_parser
        known = set(build_parser()._subparsers._group_actions[0].choices)
        for folder, _, names in os.walk(ROOT):
            if ".git" in folder or "_vendor" in folder:
                continue
            for name in names:
                if name.endswith((".md", ".yaml")):
                    with open(os.path.join(folder, name), encoding="utf-8") as handle:
                        for command in re.findall(r"python3 -m scout (?:--\w+ \S+ )*([a-z]+)", handle.read()):
                            self.assertIn(command, known, "%s names `scout %s`" % (name, command))


if __name__ == "__main__":
    unittest.main()
