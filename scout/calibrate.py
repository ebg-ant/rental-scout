"""Compare the scout's verdicts with the human's after tours, and suggest changes.

Suggestions are only ever printed. Nothing here edits the config.
"""

from __future__ import annotations

from . import verdict as v
from .summary import title_of

STEP = 0.05
MIN_GAP_DIFFERENCE = 1.0  # rubric points; smaller differences are treated as noise


def _latest_feedback(store):
    rows = []
    for rec in store.listings.values():
        if rec["feedback"]:
            rows.append((rec, rec["feedback"][-1]))
    return rows


def _mean(values):
    return sum(values) / float(len(values)) if values else None


def analyse(cfg, store):
    rows = _latest_feedback(store)
    needed = cfg.calibration.get("min_tours", 10)
    result = {"tours": len(rows), "needed": needed, "ready": len(rows) >= needed}
    if not result["ready"]:
        return result

    result["disagreements"] = []
    for rec, feedback in rows:
        liked, verdict_now = feedback["would_rent"], rec.get("verdict")
        if (verdict_now == v.TOUR) != liked:
            result["disagreements"].append({
                "id": rec["id"], "title": title_of(rec), "scout": verdict_now,
                "score": (rec.get("rubric") or {}).get("score"), "you": "would rent" if liked else "would not rent",
                "why": feedback["why"]})

    gaps = []
    for crit in cfg.rubric:
        liked, disliked = [], []
        for rec, feedback in rows:
            entry = ((rec.get("rubric") or {}).get("scores") or {}).get(crit["id"]) or {}
            if entry.get("score") is not None and not entry.get("stale"):
                (liked if feedback["would_rent"] else disliked).append(entry["score"])
        gap = _mean(liked) - _mean(disliked) if liked and disliked else None
        gaps.append({"id": crit["id"], "label": crit["label"], "weight": crit["weight"], "gap": gap,
                     "liked_mean": _mean(liked), "disliked_mean": _mean(disliked),
                     "n": len(liked) + len(disliked)})
    result["criteria"] = gaps

    # Move one step of weight from the criterion that least separates the homes you
    # liked from the ones you did not, to the one that separates them best.
    usable = [g for g in gaps if g["gap"] is not None]
    donors = [g for g in usable if g["weight"] >= STEP]
    result["proposal"] = None
    if usable and donors:
        best = max(usable, key=lambda g: g["gap"])
        worst = min(donors, key=lambda g: g["gap"])
        if best["id"] != worst["id"] and best["gap"] - worst["gap"] >= MIN_GAP_DIFFERENCE:
            result["proposal"] = {"raise": best["id"], "lower": worst["id"], "step": STEP, "weights": dict(
                (g["id"], round(g["weight"] + (STEP if g["id"] == best["id"] else
                                               -STEP if g["id"] == worst["id"] else 0), 3)) for g in gaps)}
    return result


def report(cfg, store):
    data = analyse(cfg, store)
    lines = ["# Calibration report", ""]
    if not data["ready"]:
        lines.append("%d of %d tours recorded. Come back after %d more; fewer than that is noise."
                     % (data["tours"], data["needed"], data["needed"] - data["tours"]))
        return "\n".join(lines) + "\n"
    agree = data["tours"] - len(data["disagreements"])
    lines.append("%d tours recorded. The scout and you agreed on %d." % (data["tours"], agree))
    lines += ["", "## Where you disagreed"]
    if not data["disagreements"]:
        lines.append("Nowhere.")
    for item in data["disagreements"]:
        lines.append("- **%s**: scout said %s%s, you %s. \"%s\"" % (
            item["title"], item["scout"], " (%g)" % item["score"] if item["score"] is not None else "",
            item["you"], item["why"]))
    lines += ["", "## Which criteria track your taste",
              "Average score on homes you would rent, minus homes you would not. Bigger means it matters more to you.",
              "", "| Criterion | Weight | Would rent | Would not | Gap |", "| --- | --- | --- | --- | --- |"]
    for g in sorted(data["criteria"], key=lambda g: -(g["gap"] if g["gap"] is not None else -99)):
        show = lambda x: "%.1f" % x if x is not None else "n/a"
        lines.append("| %s | %d%% | %s | %s | %s |" % (g["label"], round(g["weight"] * 100), show(g["liked_mean"]),
                                                      show(g["disliked_mean"]), show(g["gap"])))
    lines += ["", "## Suggestion"]
    proposal = data["proposal"]
    if not proposal:
        lines.append("No weight change suggested: no criterion stands out clearly enough.")
    else:
        lines.append("Move %d points of weight from `%s` to `%s`. New weights would be:"
                     % (round(proposal["step"] * 100), proposal["lower"], proposal["raise"]))
        for cid, weight in proposal["weights"].items():
            lines.append("- %s: %.2f" % (cid, weight))
    lines += ["", "If a disagreement comes from how a criterion is described rather than how much it counts, "
                  "edit that criterion's anchors instead.",
              "", "Nothing has been changed. Edit `rubric` in scout.config.yaml if you agree; "
                  "the next run re-ranks everything already seen."]
    return "\n".join(lines) + "\n"
