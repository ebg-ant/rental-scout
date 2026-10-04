"""Build a demo report from the made-up listings:  python3 -m tests.demo [folder] [--inline]

--inline embeds the placeholder pictures in report.html, so the one file can be shared.
"""

from __future__ import annotations

import base64
import hashlib
import os
import shutil
import sys

from scout import cli, summary
from scout.store import opened
from tests.fixtures import nyc_demo
from tests.sim import Clock, ScriptedAgent

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLE = os.path.join(ROOT, "configs", "nyc.example.yaml")
ROOMS = ["Living room", "Living room", "Kitchen", "Bedroom", "Bedroom", "Closet", "Bath", "Hall", "View", "Outside"]

ROOM_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 300">
<rect width="400" height="300" fill="hsl({hue},28%,{wall}%)"/>
<polygon points="0,300 400,300 330,205 70,205" fill="hsl({floor},32%,46%)"/>
<rect x="{wx}" y="52" width="110" height="112" fill="hsl(205,70%,{sky}%)" stroke="#fff" stroke-width="7"/>
<line x1="{wm}" y1="52" x2="{wm}" y2="164" stroke="#fff" stroke-width="4"/>
<polygon points="{wx},205 {wr},205 {sr},300 {sl},300" fill="#fff" opacity=".16"/>
<text x="16" y="30" font-family="sans-serif" font-size="15" fill="#fff" opacity=".9">Demo photo {n}: {room}</text>
</svg>"""

PLAN_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 300">
<rect width="400" height="300" fill="#fbfaf7"/>
<g fill="none" stroke="#333" stroke-width="5"><rect x="40" y="40" width="320" height="220"/>
<line x1="215" y1="40" x2="215" y2="170"/><line x1="215" y1="170" x2="360" y2="170"/><line x1="130" y1="190" x2="130" y2="260"/></g>
<g font-family="sans-serif" font-size="14" fill="#333"><text x="85" y="120">LIVING</text><text x="255" y="110">BEDROOM</text>
<text x="255" y="222">KITCHEN</text><text x="62" y="232">BATH</text><text x="16" y="24">Demo floor plan</text></g>
</svg>"""


def _save(store, inline, rec_id, name, art):
    if inline:
        return "data:image/svg+xml;base64," + base64.b64encode(art.encode("utf-8")).decode("ascii")
    folder = os.path.join(store.dir, "photos", rec_id)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, name), "w", encoding="utf-8") as handle:
        handle.write(art)
    return "photos/%s/%s" % (rec_id, name)


def _placeholder_images(store, inline=False):
    """The invented listings have no real photos, so draw some next to the report."""
    for rec in store.listings.values():
        seed = int(hashlib.sha256(rec["id"].encode("utf-8")).hexdigest()[:6], 16)
        count = len(rec.get("photos") or []) or (1 if rec["card"].get("first_photo") else 0)
        local = []
        for n in range(1, count + 1):
            wx = 60 + (seed + n * 67) % 170
            art = ROOM_SVG.format(hue=(seed + n * 23) % 360, wall=58 + (n * 7) % 20, floor=20 + (seed + n) % 25,
                                  sky=62 + (seed + n * 5) % 24, wx=wx, wm=wx + 55, wr=wx + 110, sl=wx - 60, sr=wx + 170,
                                  n=n, room=ROOMS[(n - 1) % len(ROOMS)])
            local.append({"url": _save(store, inline, rec["id"], "%d.svg" % n, art), "caption": ROOMS[(n - 1) % len(ROOMS)]})
        drawn = {}
        if rec.get("photos"):
            drawn.update(zip([photo["url"] for photo in rec["photos"]], [photo["url"] for photo in local]))
            rec["photos"] = local
        elif local:
            rec["card"]["first_photo"] = local[0]["url"]
        if rec.get("floor_plans"):
            plan = _save(store, inline, rec["id"], "plan.svg", PLAN_SVG)
            drawn[rec["floor_plans"][0]["url"]] = plan
            rec["floor_plans"] = [{"url": plan}]
        for entry in ((rec.get("rubric") or {}).get("scores") or {}).values():
            for item in entry.get("evidence") or []:     # the pictures each score cites
                if item.get("urls"):
                    item["urls"] = [drawn.get(url, url) for url in item["urls"]]


MARKER = ".rental-scout-demo"


def _clear(folder):
    """Empty the target, but only if it is new, empty, or a demo this script built before.
    Anything else (a real data folder, a home directory, /tmp) is refused."""
    if os.path.isdir(folder) and os.listdir(folder):
        if not os.path.exists(os.path.join(folder, MARKER)):
            raise SystemExit("%s is not empty and was not made by this demo; choose a new folder" % folder)
        shutil.rmtree(folder)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, MARKER), "w") as handle:
        handle.write("Made by python3 -m tests.demo. Safe to delete.\n")


def build(folder, days=2, inline=False):
    _clear(folder)
    clock = Clock("2026-10-05T13:00:00")
    agent = ScriptedAgent(EXAMPLE, folder, clock)
    for index, day in enumerate([nyc_demo.DAY1, nyc_demo.DAY2, nyc_demo.DAY3][:days]):
        clock.jump_to("2026-10-%02dT13:00:00" % (5 + index))
        agent.run(day)
    with opened(folder) as store:
        _placeholder_images(store, inline)
        paths = cli._write_outputs(agent.cfg, store)
        return paths, summary.build(store)


if __name__ == "__main__":
    arguments = [a for a in sys.argv[1:] if a != "--inline"]
    target = arguments[0] if arguments else os.path.join(ROOT, "data-demo")
    written, text = build(target, inline="--inline" in sys.argv)
    print(text)
    print(written)
