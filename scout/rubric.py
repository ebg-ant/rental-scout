"""Check the agent's rubric scores and do the arithmetic.

The scores are model judgment. Whether they are complete, evidenced and honest
about unknowns is checked here, and a payload that is not gets rejected whole.
"""

from __future__ import annotations

import re

from .config import EVIDENCE_TYPES
from .util import is_number, norm_text

MAX_RATIONALE = 300
LINKED_TYPES = ("satellite", "street_view", "map")  # evidence that must be a deep link


def quote_found(quote, *texts):
    needle = norm_text(quote).strip('"\' .')
    return bool(needle) and any(needle in norm_text(text) for text in texts if text)


def is_url(value):
    return bool(re.match(r"^https?://\S+$", str(value or "")))


def validate(cfg, payload, page_text, only=None, photo_count=0, floor_plan_count=0):
    """Return (clean scores by criterion id, errors).

    photo_count and floor_plan_count are how many images were saved for the listing,
    so evidence can point at them by number.
    """
    errors, clean = [], {}
    given = payload.get("criteria")
    if not isinstance(given, dict):
        return {}, ["`criteria` must be an object keyed by criterion id"]
    wanted = [c for c in cfg.rubric if only is None or c["id"] in only]
    known_ids = set(c["id"] for c in cfg.rubric)
    for extra in sorted(set(given) - known_ids):
        errors.append("%s: not a criterion in the config (expected %s)" % (extra, ", ".join(sorted(known_ids))))

    for crit in wanted:
        cid = crit["id"]
        entry = given.get(cid)
        if not isinstance(entry, dict):
            errors.append("%s: missing. Score it, or set score to null with an unknown_reason" % cid)
            continue
        score = entry.get("score")
        if score is None:
            reason = str(entry.get("unknown_reason") or "").strip()
            if len(reason) < 10:
                errors.append("%s: score is null, so unknown_reason must say what could not be verified and why" % cid)
                continue
            clean[cid] = {"score": None, "unknown_reason": reason, "evidence": [], "limited_evidence": False}
            continue
        if not is_number(score) or not 0 <= score <= 10:
            errors.append("%s: score must be a number from 0 to 10, or null" % cid)
            continue
        rationale = " ".join(str(entry.get("rationale") or "").split())
        if not rationale:
            errors.append("%s: needs a one-line rationale" % cid)
        elif len(rationale) > MAX_RATIONALE:
            errors.append("%s: rationale is %d characters; keep it under %d" % (cid, len(rationale), MAX_RATIONALE))

        evidence, seen_types = [], set()
        for index, item in enumerate(entry.get("evidence") or []):
            where = "%s.evidence[%d]" % (cid, index)
            if not isinstance(item, dict) or item.get("type") not in EVIDENCE_TYPES:
                errors.append("%s: type must be one of %s" % (where, ", ".join(EVIDENCE_TYPES)))
                continue
            if item["type"] == "text":
                if not item.get("quote"):
                    errors.append("%s: text evidence needs a `quote` copied from the listing" % where)
                    continue
                if not quote_found(item["quote"], page_text):
                    errors.append('%s: the quote "%s" is not in the saved listing text. Quote exactly, or '
                                  "re-run enrich with the full text" % (where, item["quote"]))
                    continue
            elif item["type"] in LINKED_TYPES:
                if not is_url(item.get("ref")):
                    errors.append("%s: %s evidence needs `ref` to be the URL of the exact view you looked at"
                                  % (where, item["type"]))
                    continue
            elif item["type"] == "photos" and item.get("photos") is not None:
                numbers = item["photos"]
                if not isinstance(numbers, list) or not numbers or not all(
                        isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= photo_count for n in numbers):
                    errors.append("%s: `photos` must list photo numbers between 1 and %d (the photos saved by enrich)"
                                  % (where, photo_count))
                    continue
            elif item["type"] == "floor_plan" and floor_plan_count and not item.get("ref"):
                pass  # points at the saved floor plan
            elif not str(item.get("ref") or "").strip():
                errors.append("%s: needs %s" % (where, "`photos`, e.g. [2, 3]" if item["type"] == "photos"
                                                else "a `ref` (a URL, or words a person can follow)"))
                continue
            seen_types.add(item["type"])
            evidence.append(dict((k, item[k]) for k in ("type", "ref", "photos", "quote", "note") if item.get(k)))
        if not evidence:
            errors.append("%s: a score needs at least one piece of evidence. If there is none, score null" % cid)

        unavailable = {}
        for item in entry.get("unavailable") or []:
            if isinstance(item, dict) and item.get("type") and str(item.get("reason") or "").strip():
                unavailable[item["type"]] = item["reason"]
        missing = [t for t in crit["evidence_required"] if t not in seen_types and t not in unavailable]
        if missing:
            errors.append("%s: required evidence missing: %s. Add it, or list it under `unavailable` with a reason"
                          % (cid, ", ".join(missing)))
        skipped = [t for t in crit["evidence_required"] if t not in seen_types and t in unavailable]
        clean[cid] = {"score": score, "rationale": rationale, "evidence": evidence,
                      "unavailable": [{"type": t, "reason": unavailable[t]} for t in skipped],
                      "limited_evidence": bool(skipped)}
    return clean, errors


def totals(cfg, scores):
    """Weighted 0-100 score plus how much of it rests on unknowns.

    score    known criteria only, weights rescaled to fill the gap
    floor    every unknown scored 0
    ceiling  every unknown scored 10
    """
    earned, known_weight, unknown_weight = 0.0, 0.0, 0.0
    for crit in cfg.rubric:
        entry = scores.get(crit["id"])
        if entry is None or entry.get("score") is None or entry.get("stale"):
            unknown_weight += crit["weight"]
        else:
            earned += crit["weight"] * entry["score"]
            known_weight += crit["weight"]
    floor = earned * 10
    return {
        "score": round(floor / known_weight, 1) if known_weight > 0 else None,
        "floor": round(floor, 1),
        "ceiling": round(floor + unknown_weight * 100, 1),
        "unknown_weight": round(unknown_weight, 3),
    }
