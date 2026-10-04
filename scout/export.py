"""Flatten the store to a spreadsheet: one row per listing, ready for Google Sheets or Excel."""

from __future__ import annotations

import csv
import io

from . import gate as gate_mod
from . import verdict as v
from .pipeline import checked_at
from .util import local_date
from .summary import advertised_rent, fact, open_questions, title_of

BASE_COLUMNS = ["verdict", "score", "address", "neighborhood", "beds", "baths", "sqft", "rent_advertised",
                "rent_net_effective", "rent_gross",
                "commute_minutes", "available_date", "reason", "headline", "unverified_pct", "missed_must_haves",
                "unknown_must_haves", "warnings", "notes", "questions", "status", "off_market", "first_seen", "last_checked", "sites",
                "url", "other_urls", "first_photo", "id"]


def _score(rec):
    score = (rec.get("rubric") or {}).get("score")
    return -1 if score is None else score


def rows(cfg, store):
    criteria = [c["id"] for c in cfg.rubric]
    table = [BASE_COLUMNS + ["score_" + cid for cid in criteria]]
    ordered = sorted(store.listings.values(), key=lambda r: (
        v.ORDER.get(r.get("verdict"), 9), -_score(r), r["seq"]))
    for rec in ordered:
        derived, rubric, gate = rec.get("derived") or {}, rec.get("rubric") or {}, rec.get("gate")
        links = [e["url"] for e in rec["sources"] if e["active"]] or [e["url"] for e in rec["sources"]]
        photos = rec.get("photos") or []
        money = lambda x: round(x) if x is not None else ""
        row = [rec.get("verdict") or ("gone" if rec.get("delisted_at") else "waiting"), rubric.get("score", ""), title_of(rec), fact(rec, "neighborhood"),
               fact(rec, "beds"), fact(rec, "baths"), fact(rec, "sqft"),
               money(advertised_rent(rec)), money(derived.get("rent_net_effective")), money(derived.get("rent_gross")),
               fact(rec, "commute_minutes"), fact(rec, "available_date"), rec.get("verdict_reason"), rubric.get("headline"),
               round((rubric.get("unknown_weight") or 0) * 100) if rubric else "",
               gate_mod.fail_text(gate) if gate else "", ", ".join(gate_mod.unknown_labels(gate)) if gate else "",
               " | ".join(f["message"] for f in rec.get("flags") or [] if f["kind"] in ("risk", "cost")),
               " | ".join(f["message"] for f in rec.get("flags") or [] if f["kind"] in ("info", "good")),
               " | ".join(open_questions(rec)), rec["status"], "yes" if rec.get("delisted_at") else "",
               local_date(rec["first_seen"]).isoformat(), local_date(checked_at(rec)).isoformat(), ", ".join(sorted(set(e["source"] for e in rec["sources"]))),
               links[0], " ".join(links[1:]), photos[0]["url"] if photos else rec["card"].get("first_photo"), rec["id"]]
        for cid in criteria:
            entry = (rubric.get("scores") or {}).get(cid) or {}
            row.append("" if entry.get("score") is None or entry.get("stale") else entry["score"])
        table.append(["" if cell is None else cell for cell in row])
    return table


def _safe(cell):
    """Listing text is untrusted: stop a spreadsheet from running a cell as a formula."""
    if isinstance(cell, str) and cell[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + cell
    return cell


def to_csv(cfg, store):
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    for row in rows(cfg, store):
        writer.writerow([_safe(cell) for cell in row])
    return out.getvalue()
