"""Turn prefilter, gate, risk flags and rubric totals into one verdict and one reason."""

from __future__ import annotations

from . import flags as flags_mod
from . import gate as gate_mod

TOUR, WATCH, PASS, PASS_RISK = "TOUR", "WATCH", "PASS", "PASS (risk)"
ORDER = {TOUR: 0, WATCH: 1, PASS_RISK: 2, PASS: 3}


def _highlights(cfg, scores):
    scored = [(scores[c["id"]]["score"], c["label"]) for c in cfg.rubric
              if scores.get(c["id"]) and scores[c["id"]].get("score") is not None
              and not scores[c["id"]].get("stale")]
    if not scored:
        return ""
    best = sorted(scored, key=lambda pair: -pair[0])[:2]
    worst = min(scored, key=lambda pair: pair[0])
    text = "Best: " + ", ".join("%s %g" % (label, score) for score, label in best)
    if worst not in best:
        text += ". Weakest: %s %g" % (worst[1], worst[0])
    return text + "."


def decide(cfg, prefilter, card_reject, gate, flags, scores, totals):
    """Return (verdict, reason), or (None, None) if the listing is not judged yet."""
    if prefilter and prefilter.get("result") == "reject":
        return PASS, 'Filtered out: %s ("%s")' % (prefilter["reason"], prefilter["matched"])
    if card_reject:
        return PASS, "Search card: %s %s (needs %s)" % (card_reject["label"], card_reject["actual"],
                                                       card_reject["expected"])
    if gate is None:
        return None, None
    risk = flags_mod.risk_verdict(cfg, flags or [])
    if risk:
        return PASS_RISK, "Risk: " + risk
    if gate["result"] == gate_mod.FAIL:
        return PASS, "Must-haves: " + gate_mod.fail_text(gate)
    if totals is None:
        return None, None

    tour, watch = cfg.verdicts["tour"], cfg.verdicts["watch"]
    score, unknown_weight = totals["score"], totals["unknown_weight"]
    highlights = _highlights(cfg, scores)
    if score is None:
        return WATCH, "Passed the must-haves but nothing could be scored. See the unknowns."

    holds = []
    if gate["fails"] > tour["max_gate_fails"]:
        holds.append("a missed must-have: " + gate_mod.fail_text(gate))
    if gate["unknowns"] > tour["max_gate_unknowns"]:
        holds.append("unknown must-haves: " + ", ".join(gate_mod.unknown_labels(gate)))
    if unknown_weight > tour["max_unknown_weight"] + 1e-9:
        holds.append("%d%% of the score unverified" % round(unknown_weight * 100))
    caveat = " Note %s." % "; ".join(holds) if holds else ""

    if score >= tour["min_score"]:
        if not holds:
            return TOUR, "Score %g. %s" % (score, highlights)
        return WATCH, "Score %g would be a TOUR; held back by %s. %s" % (score, "; ".join(holds), highlights)
    if score >= watch["min_score"]:
        return WATCH, "Score %g, %g short of a TOUR.%s %s" % (score, round(tour["min_score"] - score, 1), caveat, highlights)
    if unknown_weight > 0 and totals["ceiling"] >= tour["min_score"]:
        return WATCH, "Score %g on what could be checked, but it could reach %g once the unknowns are settled.%s %s" \
            % (score, totals["ceiling"], caveat, highlights)
    if unknown_weight > 0:
        return PASS, "Score %g, and %g at best if every unknown turned out perfect, which is short of a TOUR. %s" \
            % (score, totals["ceiling"], highlights)
    return PASS, "Score %g is below %g. %s" % (score, watch["min_score"], highlights)
