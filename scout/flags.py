"""Deterministic checks that attach warnings to a listing.

kind:      risk (possible scam or unreliable listing), cost, info, good
severity:  high or low for risk flags; None otherwise
Risk flags can force the verdict `PASS (risk)`; the rest only inform.
"""

from __future__ import annotations

from .config import compile_phrase
from .util import is_number, money, norm_text


def _flag(flag_id, kind, message, severity=None):
    return {"id": flag_id, "kind": kind, "severity": severity, "message": message}


def reference_rent(cfg, beds, comps):
    """Typical rent for this bedroom count: (rent, where it came from) or (None, why not).

    The config value wins. Otherwise the median of listings already seen, but only
    once there are enough of them to mean something.
    """
    if beds is None:
        return None, "bedroom count unknown"
    table = cfg.risk.get("reference_rents") or {}
    key = int(beds) if float(beds) == int(beds) else beds
    for candidate in (key, str(key)):
        if is_number(table.get(candidate)):
            return table[candidate], "risk.reference_rents in your config"
    rent, count = comps(beds)
    if rent is not None and count >= cfg.risk.get("min_comps", 8):
        return rent, "median of %d listings seen so far with %s bedroom%s" % (count, key, "" if key == 1 else "s")
    return None, "not enough comparable listings yet (%d of %d)" % (count, cfg.risk.get("min_comps", 8))


def run(cfg, record, values, comps):
    facts = record.get("facts") or {}
    flags = []
    disabled = set(cfg.risk.get("disable") or [])

    # --- risk -------------------------------------------------------------
    floor_pct = cfg.risk.get("price_floor_pct")
    rent = values.get("rent_net_effective") or values.get("rent_advertised")
    if floor_pct and rent:
        typical, basis = reference_rent(cfg, facts.get("beds"), comps)
        if typical and rent < typical * floor_pct:
            flags.append(_flag("price_too_low", "risk",
                               "%s is under %d%% of the typical %s (%s)"
                               % (money(rent), round(floor_pct * 100), money(typical), basis), "high"))
    text = norm_text(" \n ".join(filter(None, [record.get("raw_text"), (record.get("card") or {}).get("title"),
                                               (record.get("card") or {}).get("snippet")])))
    for phrase in cfg.risk.get("scam_phrases") or []:
        found = compile_phrase(phrase).search(text)
        if found:
            flags.append(_flag("scam_language", "risk", 'Listing text says "%s"' % found.group(0), "high"))
            break
    if facts.get("asks_deposit_before_viewing"):
        flags.append(_flag("deposit_before_viewing", "risk", "Asks for money before a viewing", "high"))
    if facts.get("owner_remote"):
        flags.append(_flag("owner_remote", "risk", "Poster says they are away and cannot show the unit", "high"))
    if facts.get("photos_reused_elsewhere"):
        flags.append(_flag("photos_reused", "risk", "Same photos appear on a different listing or address", "high"))
    if record.get("facts") is not None and not values.get("address_disclosed"):
        flags.append(_flag("no_address", "risk",
                           "No street address, so light, noise, flood and duplicates cannot be checked", "low"))
    if facts.get("photos_recency") == "stale":
        flags.append(_flag("stale_photos", "risk", "Photos look old or recycled; condition may differ", "low"))
    if facts.get("photo_count") == 0:
        flags.append(_flag("no_photos", "risk", "No photos", "low"))

    # --- cost -------------------------------------------------------------
    gross, net = values.get("rent_gross"), values.get("rent_net_effective")
    if facts.get("tenant_pays_broker_fee"):
        amount = facts.get("broker_fee_amount")
        flags.append(_flag("tenant_broker_fee", "cost",
                           "Asks the tenant to pay a broker fee%s. %s"
                           % (" of %s" % money(amount) if is_number(amount) else "",
                              cfg.costs.get("broker_fee_note") or "Check who hired the broker.")))
    if gross and net and gross - net >= 1:
        flags.append(_flag("concession", "cost",
                           "Net effective %s, but the monthly payment is %s and renewals usually start from it"
                           % (money(net), money(gross))))
    if values.get("rent_note"):
        flags.append(_flag("rent_unclear", "cost", values["rent_note"].capitalize()))
    deposit_cap, deposit = cfg.costs.get("max_deposit_months"), facts.get("security_deposit")
    if deposit_cap and is_number(deposit) and gross and deposit > gross * deposit_cap + 1:
        flags.append(_flag("deposit_high", "cost", "Deposit %s is more than %s month%s of rent"
                           % (money(deposit), deposit_cap, "" if deposit_cap == 1 else "s")))
    fee_cap, fee = cfg.costs.get("max_application_fee"), facts.get("application_fee")
    if fee_cap is not None and is_number(fee) and fee > fee_cap:
        flags.append(_flag("application_fee_high", "cost",
                           "Application fee %s is above %s" % (money(fee), money(fee_cap))))
    income = cfg.profile.get("annual_income")
    multiple = facts.get("income_multiple_required") or cfg.costs.get("default_income_multiple")
    if is_number(income) and multiple and gross and income < multiple * gross:
        stated = "stated on the listing" if facts.get("income_multiple_required") else "assumed, not stated"
        extra = " You said you have a guarantor." if cfg.profile.get("has_guarantor") else ""
        if facts.get("guarantor_accepted") is False:
            extra = " The listing says guarantors are not accepted."
        flags.append(_flag("income_short", "cost", "Needs about %s a year (%sx rent, %s); you entered %s.%s"
                           % (money(multiple * gross), ("%g" % multiple), stated, money(income), extra)))

    # --- info -------------------------------------------------------------
    if facts.get("level") in ("basement", "garden") or facts.get("flood_zone") in ("moderate", "high"):
        bits = []
        if facts.get("level") in ("basement", "garden"):
            bits.append("%s-level unit" % facts["level"])
        if facts.get("flood_zone") in ("moderate", "high"):
            bits.append("%s flood zone" % facts["flood_zone"])
        flags.append(_flag("flood_risk", "info", "Flood risk: " + ", ".join(bits)))
    if facts.get("noise_sources"):
        flags.append(_flag("noise", "info", "Noise: " + "; ".join(facts["noise_sources"])))
    if facts.get("flex_walls"):
        flags.append(_flag("flex_walls", "info", "A bedroom relies on a flex wall or is 'convertible'"))
    prices = sorted(set(s["rent"] for s in record.get("sources", []) if s.get("active") and is_number(s.get("rent"))))
    if len(prices) > 1:
        flags.append(_flag("price_differs_by_site", "info", "Advertised at %s on different sites"
                           % " and ".join(money(p) for p in prices)))
    if record.get("possible_duplicates"):
        flags.append(_flag("possible_duplicate", "info",
                           "Same building as %s; may be the same unit" % ", ".join(record["possible_duplicates"])))
    if facts.get("rent_stabilized"):
        flags.append(_flag("rent_stabilized", "good", "Listed as rent-stabilized"))

    return [flag for flag in flags if flag["id"] not in disabled]


def risk_verdict(cfg, flags):
    """Return the reason text if the risk flags are enough to reject, else None."""
    high = [f for f in flags if f["kind"] == "risk" and f["severity"] == "high"]
    low = [f for f in flags if f["kind"] == "risk" and f["severity"] == "low"]
    if high:
        return "; ".join(f["message"] for f in high)
    if len(low) >= cfg.risk.get("low_flags_to_pass", 2):
        return "; ".join(f["message"] for f in low)
    return None
