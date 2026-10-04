"""Regenerate agent/examples/*.json from the fixtures:  python3 -m tests.make_examples"""

from __future__ import annotations

import json
import os

from tests.fixtures import nyc_demo as demo

FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent", "examples")


def _dump(name, value):
    with open(os.path.join(FOLDER, name), "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2)
        handle.write("\n")


def main():
    key = "streeteasy:0:2800-4400"
    sweep = demo.DAY1["sweeps"][key]
    _dump("cards.json", {"sweep": key, "total_results": sweep["total_results"], "pages_read": 1, "complete": True,
                         "capped": False, "cards": sweep["cards"][:3]})
    page = dict(demo.DAY1["pages"]["streeteasy-demo-88-berry-street-6c"])
    ordered = {"id": page.pop("id"), "source": "streeteasy", "url": sweep["cards"][1]["url"]}
    ordered.update(page)
    ordered["photos"] = ordered["photos"][:3] + [{"url": ordered["photos"][3], "caption": "balcony"}]
    ordered["facts"] = dict(ordered["facts"], photo_count=4)
    _dump("enrich.json", ordered)
    _dump("score.json", demo.DAY1["scores"]["craigslist-7900000003"])


if __name__ == "__main__":
    main()
