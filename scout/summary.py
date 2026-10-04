"""The daily summary: a short markdown note generated from the store."""

from __future__ import annotations

from . import gate as gate_mod
from . import verdict as v
from .util import is_number, money, plural


def title_of(rec):
    return (rec.get("address") or {}).get("raw") or rec["card"].get("address") or rec["card"].get("title") or rec["id"]


def fact(rec, name):
    facts = rec.get("facts") or {}
    if facts.get(name) is not None:
        return facts[name]
    return rec["card"].get(name)


def rent_text(rec):
    derived = rec.get("derived") or {}
    net, gross = derived.get("rent_net_effective"), derived.get("rent_gross")
    if net and gross and gross - net >= 1:
        return "%s net (%s gross)" % (money(net), money(gross))
    rent = net or gross or rec["card"].get("rent")
    return money(rent) if is_number(rent) else "rent unknown"


def _size_text(rec):
    beds, baths = fact(rec, "beds"), fact(rec, "baths")
    bits = []
    if beds is not None:
        bits.append("studio" if beds == 0 else "%gbd" % beds)
    if baths is not None:
        bits.append("%gba" % baths)
    return "/".join(bits)


def _links(rec):
    live = [entry for entry in rec["sources"] if entry["active"]] or rec["sources"]
    return " ".join("[%s](%s)" % (entry["source"], entry["url"]) for entry in live)


def _entry(rec, detail=True):
    bits = [b for b in [fact(rec, "neighborhood"), rent_text(rec), _size_text(rec)] if b]
    score = (rec.get("rubric") or {}).get("score")
    if score is not None:
        bits.append("score %g" % score)
    lines = ["- **%s** · %s · %s · [scorecard](../report.html#%s)"
             % (title_of(rec), " · ".join(str(b) for b in bits), _links(rec), rec["id"])]
    lines.append("  %s" % rec.get("verdict_reason"))
    if not detail:
        return lines
    headline = (rec.get("rubric") or {}).get("headline")
    if headline:
        lines.append("  _%s_" % headline)
    warnings = [f["message"] for f in rec.get("flags") or [] if f["kind"] in ("risk", "cost")]
    if warnings:
        lines.append("  Watch out: " + " | ".join(warnings))
    return lines


def _sort_key(rec):
    score = (rec.get("rubric") or {}).get("score")
    rent = (rec.get("derived") or {}).get("rent_net_effective") or rec["card"].get("rent") or 0
    return (-(score if score is not None else -1), rent)


def pass_bucket(rec):
    reason = rec.get("verdict_reason") or ""
    for prefix, label in (("Filtered out", "filtered out on sight"), ("Search card", "ruled out from the search card"),
                          ("Must-haves", "missed a must-have"), ("Risk", "risk flags"), ("Score", "scored too low")):
        if reason.startswith(prefix):
            return label
    return "other"


def open_questions(rec):
    """What a human could ask to settle this listing. The scout never asks itself."""
    items = []
    if rec.get("gate"):
        items += ["Confirm: %s" % label for label in gate_mod.unknown_labels(rec["gate"])]
    items += list((rec.get("rubric") or {}).get("questions") or [])
    return items


def build(store, run=None):
    """Markdown for one finished run (default: the latest)."""
    runs = store.data["runs"]
    run = run or (runs[-1] if runs else None)
    if not run:
        return "# Rental Scout\n\nNo runs yet.\n"
    listings = store.listings
    touched = [listings[i] for i in run["touched"] if i in listings]
    counts = run["counts"]
    lines = ["# Rental Scout: %s" % run["finished_at"][:10], ""]
    lines.append("%s seen, %d new, %s, %d relisted. Opened %s, scored %d.%s" % (
        plural(counts["cards"], "card"), counts["new"], plural(counts["price_changed"], "price change"),
        counts["relisted"], plural(run["evaluated"], "listing"), run["scored"],
        " Resumed %s after an interruption." % plural(run["resumes"], "time") if run.get("resumes") else ""))
    lines.append("")

    for name in (v.TOUR, v.WATCH):
        group = sorted([r for r in touched if r.get("verdict") == name and not r.get("delisted_at")], key=_sort_key)
        lines.append("## %s: %d from this run" % (name, len(group)))
        if not group:
            lines.append("None.")
        for rec in group:
            lines += _entry(rec)
            questions = open_questions(rec)
            if name == v.WATCH and questions:
                lines.append("  To settle it: " + " | ".join(questions))
        lines.append("")

    changes = [r for r in touched if (r.get("last_change") or {}).get("run") == run["id"]]
    gone = [listings[i] for i in run.get("delisted", []) if i in listings
            and listings[i].get("verdict") in (v.TOUR, v.WATCH)]
    if changes or gone:
        lines.append("## Changes to listings already tracked")
        for rec in changes:
            change = rec["last_change"]
            what = "relisted" if change["what"] == "relisted" else \
                "rent %s -> %s" % (money(change["from"]), money(change["to"]))
            lines.append("- %s: %s (now %s)" % (title_of(rec), what, rec.get("verdict") or "waiting to be re-checked"))
        for rec in gone:
            lines.append("- %s: off the market (was %s)" % (title_of(rec), rec["verdict"]))
        lines.append("")

    standing = sorted([r for r in listings.values() if r.get("verdict") == v.TOUR and not r.get("delisted_at")
                       and r["status"] == "active" and r["id"] not in run["touched"]], key=_sort_key)
    if standing:
        lines.append("## Still standing: earlier TOURs not yet toured")
        for rec in standing:
            lines += _entry(rec, detail=False)
        lines.append("")

    passed = [r for r in touched if r.get("verdict") in (v.PASS, v.PASS_RISK)]
    lines.append("## PASS: %d from this run" % len(passed))
    buckets = {}
    for rec in passed:
        buckets.setdefault(pass_bucket(rec), []).append(rec)
    for label in sorted(buckets, key=lambda k: -len(buckets[k])):
        lines.append("- %d %s" % (len(buckets[label]), label))
    for rec in passed:
        if rec.get("facts") is not None:
            lines.append("  - %s: %s" % (title_of(rec), rec["verdict_reason"]))
    lines.append("")

    problems = []
    for source, info in sorted(run.get("blocked", {}).items()):
        problems.append("%s blocked the browser (%s). The scout stopped using it for this run and did not retry."
                        % (source, info["what"]))
    problems += run.get("notes", [])
    if run.get("left_to_open"):
        problems.append("%s waiting to be opened next run (per-run limit reached)."
                        % plural(run["left_to_open"], "listing"))
    if run.get("left_to_score"):
        problems.append("%s waiting to be scored next run." % plural(run["left_to_score"], "listing"))
    if run.get("abandoned"):
        problems.append("This run was never finished and was closed automatically.")
    if problems:
        lines.append("## Worth knowing")
        lines += ["- " + p for p in problems]
        lines.append("")
    return "\n".join(lines)
