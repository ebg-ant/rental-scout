"""Build report.html: one self-contained page, no server, opens with a double-click."""

from __future__ import annotations

import json
import os

from . import verdict as v
from .gate import show
from .pipeline import checked_at, coverage_report, helper_links
from .summary import advertised_rent, fact, open_questions, pass_bucket, title_of
from .util import local_date, now_iso

TEMPLATE = os.path.join(os.path.dirname(__file__), "report_template.html")


def _criteria(cfg, rec):
    scores = (rec.get("rubric") or {}).get("scores") or {}
    rows = []
    for crit in cfg.rubric:
        entry = scores.get(crit["id"])
        row = {"id": crit["id"], "label": crit["label"], "weight": crit["weight"]}
        if entry and not entry.get("stale"):
            row.update({"score": entry.get("score"), "rationale": entry.get("rationale"),
                        "unknown_reason": entry.get("unknown_reason"), "evidence": entry.get("evidence") or [],
                        "unavailable": entry.get("unavailable") or [], "limited": entry.get("limited_evidence")})
        elif entry:
            row.update({"score": None, "unknown_reason": "This criterion was reworded; waiting to be re-scored"})
        rows.append(row)
    return rows if scores else []


def _facts(cfg, rec):
    rows = []
    for name, value in sorted((rec.get("facts") or {}).items()):
        source = (rec.get("provenance") or {}).get(name) or {}
        rows.append({"name": name.replace("_", " "), "value": show(name, value), "where": source.get("where"),
                     "quote": source.get("quote"), "url": source.get("url")})
    for name, why in sorted((rec.get("unknown") or {}).items()):
        rows.append({"name": name.replace("_", " "), "value": None, "where": why})
    return rows


def _price_steps(rec):
    """The price history a person wants: one row per change on each site. The same figure
    seen on the search card and again on the page is one row, not two."""
    steps, last = [], {}
    for point in rec["price_history"]:
        link = (point["source"], point["source_id"])
        if last.get(link) != point["rent"]:
            steps.append(point)
        last[link] = point["rent"]
    return steps


def _photos(rec):
    if rec.get("photos"):
        return rec["photos"]
    first = rec["card"].get("first_photo")
    return [{"url": first}] if first else []


def listing_view(cfg, rec, last_run_id, announced=()):
    derived = rec.get("derived") or {}
    rubric = rec.get("rubric") or {}
    change = rec.get("last_change") or {}
    return {
        "id": rec["id"], "title": title_of(rec), "neighborhood": fact(rec, "neighborhood"),
        "beds": fact(rec, "beds"), "baths": fact(rec, "baths"), "sqft": fact(rec, "sqft"),
        "rent_net": derived.get("rent_net_effective"), "rent_gross": derived.get("rent_gross"),
        "rent_advertised": advertised_rent(rec), "all_in": derived.get("monthly_all_in"),
        "commute": fact(rec, "commute_minutes"), "available": fact(rec, "available_date"),
        "verdict": rec.get("verdict") or ("GONE" if rec.get("delisted_at") else "PENDING"),
        "reason": rec.get("verdict_reason"), "partial": bool(rec.get("partial")),
        "bucket": pass_bucket(rec) if rec.get("verdict") in (v.PASS, v.PASS_RISK) else None,
        "headline": rubric.get("headline"), "score": rubric.get("score"), "floor": rubric.get("floor"),
        "ceiling": rubric.get("ceiling"), "unknown_weight": rubric.get("unknown_weight"),
        "criteria": _criteria(cfg, rec), "gate": (rec.get("gate") or {}).get("rules") or [],
        "flags": rec.get("flags") or [], "facts": _facts(cfg, rec), "questions": open_questions(rec),
        "sources": [{"source": e["source"], "url": e["url"], "rent": e.get("rent"), "active": e["active"]}
                    for e in rec["sources"]],
        "price_history": _price_steps(rec), "history": rec["history"][-12:],
        "first_seen": local_date(rec["first_seen"]).isoformat(), "last_seen": local_date(rec["last_seen"]).isoformat(),
        "checked_at": checked_at(rec),
        "status": rec["status"], "off_market": bool(rec.get("delisted_at")),
        "is_new": rec.get("first_seen_run") == last_run_id or rec["id"] in announced,
        "changed": change.get("what") if change.get("run") == last_run_id else None,
        "verdict_changed": rec.get("verdict_changed_run") == last_run_id and rec.get("first_seen_run") != last_run_id,
        "stage": rec["stage"], "photos": _photos(rec), "floor_plans": rec.get("floor_plans") or [],
        "photos_unavailable_reason": rec.get("photos_unavailable_reason"),
        "links": helper_links(cfg, rec), "page_url": (rec.get("enriched_from") or {}).get("url"),
        "address_unknown_reason": rec.get("address_unknown_reason"),
        "feedback": rec["feedback"], "description": (rec.get("raw_text") or "")[:3000],
    }


def build(cfg, store):
    runs = store.data["runs"]
    last = runs[-1] if runs else None
    data = {
        "generated_at": now_iso(), "name": cfg.profile.get("name"), "city": cfg.search["city"],
        "last_run": last, "runs": len(runs),
        "source_labels": dict((sid, spec.get("label") or sid) for sid, spec in cfg.sources.items()),
        "thresholds": {"tour": cfg.verdicts["tour"]["min_score"], "watch": cfg.verdicts["watch"]["min_score"]},
        "recheck_days": {"TOUR": cfg.refresh["recheck_tour_after_days"], "WATCH": cfg.refresh["recheck_watch_after_days"]},
        "coverage": coverage_report(cfg, store),
        "listings": [listing_view(cfg, rec, last["id"] if last else None,
                                  set(i for ids in last["announce"].values() for i in ids) if last else ())
                     for rec in store.listings.values()],
    }
    # No "<" survives into the page source, so listing text cannot close or confuse the script block.
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    with open(TEMPLATE, "r", encoding="utf-8") as handle:
        return handle.read().replace("/*__SCOUT_DATA__*/null", blob)
