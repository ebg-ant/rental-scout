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

    first, last = cfg.profile.get("move_in_earliest"), cfg.profile.get("move_in_latest")
    if first or last:
        lines.append("Moving %s" % " and ".join(filter(None, ["no earlier than %s" % first if first else None,
                                                             "no later than %s" % last if last else None])))

    lines += ["", "Where I will look"]
    for sid, spec in cfg.sources.items():
        if not spec.get("enabled"):
            continue
        count = len(spec.get("saved_search_urls") or []) or len(spec.get("subareas") or [])
        detail = "your inbox" if spec["url_style"] == "inbox" else plural(count, "search", "searches")
        if not spec.get("saved_search_urls") and spec.get("subareas"):
            detail += ": " + ", ".join(str(sub) for sub in spec["subareas"])
        lines.append("  - %s (%s)" % (spec.get("label") or sid, detail))
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
    off = [rule["label"] for rule in cfg.gate["rules"] if not rule["enabled"]]
    if off:
        lines.append("  Not checked (switched off): %s" % ", ".join(off))
    allowed = cfg.gate["max_fails"]
    lines.append("  Missing a dealbreaker means PASS. So does missing more than %s."
                 % plural(allowed, "other must-have"))

    lines += ["", "What I score, for listings that get through"]
    for crit in sorted(cfg.rubric, key=lambda c: -c["weight"]):
        lines.append("  - %s: %d%%" % (crit["label"], round(crit["weight"] * 100)))

    income = cfg.profile.get("annual_income")
    lines += ["", "Money warnings"]
    multiple = cfg.costs.get("default_income_multiple")
    if income:
        lines.append("  - I will flag listings that need more than %s a year (%s)%s"
                     % (money(income), "%gx the rent unless the listing says otherwise" % multiple if multiple
                        else "only where the listing states its income rule",
                        "; you have a guarantor" if cfg.profile.get("has_guarantor") else "; no guarantor"))
    else:
        lines.append("  - No income given, so I will not check the income rule")
    lines.append("  - Also flagged: broker fees charged to you, free-month maths, high deposits and application fees")

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
              "Each run: up to %s opened in all, up to %s read on each site, at least %g seconds between pages, "
              "%g minutes at most."
              % (plural(cfg.limits["max_listings_per_run"], "listing"),
                 plural(cfg.limits["max_search_pages_per_run"], "results page"), cfg.limits["throttle_seconds"],
                 cfg.limits["max_runtime_minutes"]),
              "Keeping it fresh: newest listings every run, every page of every search each %g days, and a second "
              "look at any TOUR not seen for %g days or WATCH not seen for %g."
              % (cfg.refresh["full_sweep_every_days"], cfg.refresh["recheck_tour_after_days"],
                 cfg.refresh["recheck_watch_after_days"])]
    return "\n".join(lines)
