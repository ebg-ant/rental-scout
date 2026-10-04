"""Plain-English read-back of a config, for the user to confirm."""

from __future__ import annotations

from .gate import expected_text
from .util import money, plural

_IF_UNKNOWN = {"fail": "counts as a miss", "pass": "is let through", "flag": "is flagged for you to check"}
_RENT_WORDS = {"rent_net_effective": " (averaged over the lease, after any free months)",
               "rent_gross": " (the actual monthly payment)",
               "monthly_all_in": " (including any broker fee, spread over the lease)"}


def explain(cfg):
    lines = []
    name = cfg.profile.get("name")
    lines.append("Search%s in %s" % (" for %s" % name if name else "", cfg.search["city"]))

    lines += ["", "Where I will look"]
    for sid, spec in cfg.sources.items():
        if not spec.get("enabled"):
            continue
        count = len(spec.get("saved_search_urls") or []) or len(spec.get("subareas") or [])
        lines.append("  - %s (%s)" % (spec.get("label") or sid, "your inbox" if spec["url_style"] == "inbox"
                                      else plural(count, "search", "searches")))
    lines.append("  - advertised rent from %s to %s" % (money(cfg.search["price_min"]), money(cfg.search["price_max"])))
    hoods, zips = cfg.areas.get("neighborhoods") or [], cfg.areas.get("zips") or []
    if hoods:
        lines.append("  - neighborhoods: %s" % ", ".join(str(h) for h in hoods))
    if zips:
        lines.append("  - zips: %s" % ", ".join(str(z) for z in zips))
    anchor = cfg.search.get("commute_anchor") or {}
    if anchor.get("address"):
        lines.append("  - commute measured to %s (%s) by %s" % (anchor.get("label") or "your anchor", anchor["address"],
                                                                 anchor.get("mode", "transit")))

    lines += ["", "Thrown out on sight, before opening the page"]
    lines += ["  - %s" % rule["reason"] for rule in cfg.prefilters if rule.get("enabled")]

    lines += ["", "Must-haves"]
    for rule in cfg.gate["rules"]:
        if rule["enabled"]:
            lines.append("  - %s%s: %s%s. If the listing does not say, it %s." % (
                rule["label"], _RENT_WORDS.get(rule["field"], ""), expected_text(rule),
                " [dealbreaker]" if rule["dealbreaker"] else "", _IF_UNKNOWN[rule["on_unknown"]]))
    allowed = cfg.gate["max_fails"]
    lines.append("  Missing a dealbreaker means PASS. So does missing more than %s."
                 % plural(allowed, "other must-have"))

    lines += ["", "What I score, for listings that get through"]
    for crit in sorted(cfg.rubric, key=lambda c: -c["weight"]):
        lines.append("  - %s: %d%%" % (crit["label"], round(crit["weight"] * 100)))

    tour, watch = cfg.verdicts["tour"], cfg.verdicts["watch"]
    limits = []
    limits.append("no missed must-haves" if not tour["max_gate_fails"]
                  else "at most %s" % plural(tour["max_gate_fails"], "missed must-have"))
    limits.append("none still unknown" if not tour["max_gate_unknowns"]
                  else "at most %d still unknown" % tour["max_gate_unknowns"])
    limits.append("at most %d%% of the score unverified" % round(tour["max_unknown_weight"] * 100))
    lines += ["", "Verdicts",
              "  - TOUR: score %g or more, %s" % (tour["min_score"], ", ".join(limits)),
              "  - WATCH: score %g or more, or TOUR-level with something still open" % watch["min_score"],
              "  - PASS: everything else, always with the reason",
              "",
              "Each run: up to %s opened, at least %g seconds between pages, %g minutes at most."
              % (plural(cfg.limits["max_listings_per_run"], "listing"), cfg.limits["throttle_seconds"],
                 cfg.limits["max_runtime_minutes"])]
    return "\n".join(lines)
