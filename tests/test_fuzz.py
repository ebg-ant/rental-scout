"""A made-up market that churns for weeks, read by a scripted agent that dawdles and drops sessions.

No single scenario is checked here. What is checked is what must hold whatever happens: nothing is lost,
nothing live is written off, the sites are never hurried, long sweeps finish, and news is told once.
"""

import os
import random
import shutil
import tempfile
import unittest

from scout import pipeline
from scout.store import Store, opened
from tests.fixtures.nyc_demo import CL, MAPS, SE, ZL, crit, ev, page
from tests.helpers import make_config
from tests.sim import Clock

SITES = ("streeteasy", "zillow", "craigslist:0", "craigslist:1", "craigslist:2")
HOODS = ("East Village", "Williamsburg", "Greenpoint", "Astoria", "Harlem")
TEXT = "A one bedroom with a window. Laundry in building. Cats allowed. Dishwasher. Elevator building with a doorman."
PER_PAGE = 5


class Market(object):
    def __init__(self, seed):
        self.random = random.Random(seed)
        self.homes, self.posts, self.serial = {}, [], 0
        for _ in range(60):
            self.add_home()

    def add_home(self):
        self.serial += 1
        home = {"n": self.serial, "address": "%d Fuzz Street #%d" % (100 + self.serial, self.serial % 9 + 1),
                "rent": self.random.randrange(2800, 4400, 50), "hood": self.random.choice(HOODS),
                "cats": self.random.choice(["allowed", "allowed", "allowed", "not_allowed", None]),
                "quality": self.random.choice([3, 5, 6, 7, 8, 9])}
        self.homes[home["n"]] = home
        for site in self.random.sample(SITES, self.random.choice([1, 1, 1, 2])):
            self.add_post(home, site)

    def add_post(self, home, site):
        self.serial += 1
        name = site.split(":")[0]
        url = {"streeteasy": SE % ("fuzz-%d/1" % self.serial), "zillow": ZL % (800000 + self.serial),
               "craigslist": CL % ("sub" + site[-1], 7800000000 + self.serial)}[name]
        self.posts.append({"serial": self.serial, "home": home["n"], "site": site, "url": url, "live": True})

    def overnight(self):
        for post in self.posts:
            if post["live"] and self.random.random() < 0.06:
                post["live"] = False
        for home in self.homes.values():
            if self.random.random() < 0.04:
                home["rent"] = max(2800, min(4400, home["rent"] + self.random.choice([-150, -100, 100])))
        for _ in range(self.random.randrange(0, 5)):
            self.add_home()
        gone = [p for p in self.posts if not p["live"] and p["site"].startswith("craigslist")]
        if gone and self.random.random() < 0.5:       # a repost: same home, new id
            self.add_post(self.homes[self.random.choice(gone)["home"]], self.random.choice(SITES[2:]))

    def card(self, post):
        home = self.homes[post["home"]]
        card = {"url": post["url"], "rent": home["rent"], "beds": 1, "neighborhood": home["hood"],
                "title": "Fuzz home %d, a one bedroom worth a look" % home["n"]}
        if not post["site"].startswith("craigslist"):   # Craigslist cards carry no address
            card["address"] = home["address"]
        return card

    def results(self, task):
        site = task["source"] if task["source"] != "craigslist" else "craigslist:" + task["sweep"].split(":")[1]
        found = [p for p in sorted(self.posts, key=lambda p: -p["serial"]) if p["live"] and p["site"] == site
                 and task["price_min"] <= self.homes[p["home"]]["rent"] <= task["price_max"]]
        start = (task["page"] - 1) * PER_PAGE
        return {"sweep": task["sweep"], "page": task["page"], "total_results": len(found),
                "cards": [self.card(p) for p in found[start:start + PER_PAGE]], "last_page": start + PER_PAGE >= len(found)}

    def listing_page(self, task):
        post = next(p for p in self.posts if p["url"] == task["url"])
        if not post["live"]:
            return {"id": task["id"], "url": task["url"], "gone": True}
        home = self.homes[post["home"]]
        if home["cats"] == "not_allowed" and self.random.random() < 0.5:
            return {"id": task["id"], "url": task["url"], "stop_early": True, "raw_text": TEXT + " No pets.",
                    "facts": {"cats": "not_allowed"}, "provenance": {"cats": {"where": "policies", "quote": "No pets"}}}
        facts = {"rent_advertised": home["rent"], "beds": 1, "baths": 1, "neighborhood": home["hood"], "zip": "10009",
                 "floor": 2, "elevator": True, "laundry": "in_building", "commute_minutes": 30, "available_date": "2026-12-01",
                 "dishwasher": True, "ac": "window", "outdoor": "none", "cats": home["cats"]}
        unknown = {} if home["cats"] else {"cats": "the page says nothing about pets"}
        return page(task["id"], home["address"] + ", New York, NY 10009", TEXT, facts, unknown=unknown, url=task["url"])

    def scores(self, task):
        home = next(h for h in self.homes.values() if task["address"].startswith(h["address"]))
        q = home["quality"]
        return {"id": task["id"], "criteria": {
            "light": crit(q, "ok", ev("photos", [1]), ev("satellite", MAPS)),
            "layout": crit(q, "ok", ev("photos", [2]), ev("floor_plan")),
            "building": crit(q, "ok", ev("text", quote="Elevator building with a doorman"), ev("records", "HPD lookup")),
            "commute": crit(q, "ok", ev("map", MAPS)), "practical": crit(q, "ok", ev("text", quote="Laundry in building")),
            "outdoor": crit(q, "ok", ev("photos", "all photos"))}}


class Fuzz(unittest.TestCase):
    DAYS = 24

    def simulate(self, seed):
        folder = tempfile.mkdtemp(prefix="scout-fuzz-")
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        self.addCleanup(os.environ.pop, "SCOUT_NOW", None)
        cfg = make_config(lambda raw: raw["search"]["sources"]["zillow"].update(max_pages=3))
        market, clock, chance = Market(seed), Clock("2026-10-05T13:00:00"), random.Random(seed + 1000)
        call = lambda function, *args: self._call(cfg, folder, function, *args)
        shown, loads, told, finished_full = set(), {}, [], set()

        for day in range(self.DAYS):
            clock.jump_to("2026-%02d-%02dT13:00:00" % (10 + (4 + day) // 31, (4 + day) % 31 + 1))
            call(pipeline.start)
            for _ in range(400):
                if chance.random() < 0.03:
                    call(pipeline.start)                      # the session dropped and came back
                task = call(pipeline.next_task)
                if chance.random() < 0.1:
                    clock.tick(chance.randrange(0, 90))       # asked, wandered off, asked again
                    again = call(pipeline.next_task)
                    self.assertEqual((again["task"], again.get("sweep"), again.get("page"), again.get("id")),
                                     (task["task"], task.get("sweep"), task.get("page"), task.get("id")))
                    task = again
                if task["task"] == "finish":
                    break
                if task["task"] in ("sweep", "enrich"):
                    clock.tick(task["wait_seconds"] + chance.randrange(0, 15))
                    loads.setdefault((day, task["source"]), []).append(clock.now)
                    for _ in range(1, task["page"] if "click through the pager" in task.get("reach_page", "") else 1):
                        clock.tick(task["wait_between_clicks_seconds"])
                        loads[(day, task["source"])].append(clock.now)
                clock.tick(chance.randrange(1, 40))
                if task["task"] == "sweep":
                    results = market.results(task)
                    shown.update(card["url"] for card in results["cards"])
                    reply = call(pipeline.ingest, results)
                    if reply.get("full_sweep_finished", {}).get("complete"):
                        finished_full.add(task["source"])
                elif task["task"] == "enrich":
                    call(pipeline.enrich, market.listing_page(task))
                else:
                    call(pipeline.score, market.scores(task))
            else:
                self.fail("day %d never reached finish" % day)
            run = call(pipeline.finish)
            store = Store(folder).load()
            self.assertTrue(all(i in store.listings for i in run["touched"]), "a touched id no longer exists")
            told += [(i, name) for name, ids in run["announce"].items() for i in ids]
            market.overnight()
        return cfg, market, Store(folder).load(), shown, loads, told, finished_full

    @staticmethod
    def _call(cfg, folder, function, *args):
        with opened(folder) as store:
            return function(cfg, store, *args)

    def check(self, seed):
        cfg, market, store, shown, loads, told, finished_full = self.simulate(seed)
        where = dict((entry["url"], rec) for rec in store.listings.values() for entry in rec["sources"])

        lost = [url for url in shown if url not in where]
        self.assertEqual(lost, [], "cards were handed in but no record holds them")

        for rec in store.listings.values():       # one home, one record, once its address is known
            self.assertEqual(len(set(e["url"] for e in rec["sources"])), len(rec["sources"]), rec["id"])
        keys = [tuple(sorted(r["address_key"].items())) for r in store.listings.values() if r.get("address_key")]
        self.assertEqual(len(keys), len(set(keys)), "two records share one address")

        live_now = set(p["url"] for p in market.posts if p["live"])
        wrongly_gone = [rec["id"] for rec in store.listings.values() if rec.get("delisted_at")
                        and any(entry["url"] in live_now for entry in rec["sources"])]
        self.assertEqual(wrongly_gone, [], "live homes were written off")

        for (day, source), times in loads.items():
            pause = pipeline._pause(cfg, source)
            gaps = [(b - a).total_seconds() for a, b in zip(times, times[1:])]
            self.assertTrue(all(gap >= pause for gap in gaps), (day, source, gaps))

        self.assertEqual(finished_full, set(["streeteasy", "zillow", "craigslist"]), "a full sweep never finished")

        changes = dict((rec["id"], sum(1 for h in rec["history"] if h["event"] == "verdict")) for rec in store.listings.values())
        for pair in set(told):
            self.assertLessEqual(told.count(pair), 1 + changes.get(pair[0], 0), "announced again with nothing changed")


def _add(seed):
    setattr(Fuzz, "test_seed_%d" % seed, lambda self: self.check(seed))


# Two seeds keep the everyday test run short. SCOUT_FUZZ_SEEDS=40 python3 -m unittest tests.test_fuzz digs deeper.
for _seed in range(int(os.environ.get("SCOUT_FUZZ_SEEDS", "2"))):
    _add(_seed)


if __name__ == "__main__":
    unittest.main()
