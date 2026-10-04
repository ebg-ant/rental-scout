"""Values calculated from recorded facts. Never stored as facts, never guessed.

If an input is missing the result is None (unknown), and the gate's `on_unknown`
setting decides what that means.
"""

from __future__ import annotations

from .util import is_number, norm_text


def rents(facts):
    """Return (gross, net_effective, note).

    net effective = gross x (lease months - free months) / lease months, where the
    lease length includes the free months.
    """
    advertised = facts.get("rent_advertised")
    if not is_number(advertised):
        return None, None, "no advertised rent"
    free, basis, term = facts.get("free_months"), facts.get("rent_advertised_is"), facts.get("lease_term_months")
    if free == 0:
        return advertised, advertised, None
    if free is None:
        note = "it is not known whether a concession applies, so only the advertised figure is certain"
        return (advertised if basis == "gross" else None), (advertised if basis == "net_effective" else None), note
    if basis is None:
        return None, None, "a concession is offered but it is unclear whether the headline rent is gross or net"
    if not is_number(term) or term <= free:
        if basis == "gross":
            return advertised, None, "lease length unknown, so net effective rent cannot be worked out"
        return None, advertised, "lease length unknown, so gross rent cannot be worked out"
    if basis == "gross":
        return advertised, advertised * (term - free) / float(term), None
    return advertised * term / float(term - free), advertised, None


def canonical_area(name, aliases):
    key = norm_text(name)
    for canonical, others in (aliases or {}).items():
        if key == norm_text(canonical) or key in [norm_text(o) for o in others or []]:
            return norm_text(canonical)
    return key


def in_target_area(facts, areas):
    """True on any match; False if something is known and nothing matches; else None."""
    aliases = areas.get("aliases")
    targets = set(canonical_area(n, aliases) for n in areas.get("neighborhoods") or [])
    zips = set(str(z).strip() for z in areas.get("zips") or [])
    known = False
    hood, zip_code = facts.get("neighborhood"), facts.get("zip")
    if targets and hood:
        known = True
        if canonical_area(hood, aliases) in targets:
            return True
    if zips and zip_code:
        known = True
        if str(zip_code).strip()[:5] in zips:
            return True
    return False if known else None


def derive(facts, address_key, cfg):
    gross, net, rent_note = rents(facts)
    pays_fee = facts.get("tenant_pays_broker_fee")
    all_in = None
    if net is not None and pays_fee is False:
        all_in = net
    elif net is not None and pays_fee:
        fee, term = facts.get("broker_fee_amount"), facts.get("lease_term_months")
        if is_number(fee) and is_number(term) and term > 0:
            all_in = net + fee / float(term)
    income = cfg.profile.get("annual_income")
    return {
        "rent_gross": gross,
        "rent_net_effective": net,
        "monthly_all_in": all_in,
        "rent_note": rent_note,
        "in_target_area": in_target_area(facts, cfg.areas),
        "address_disclosed": bool(address_key),
        "income_multiple_have": income / float(gross) if is_number(income) and gross else None,
    }


def values_for(facts, address_key, cfg):
    """Facts and derived values in one lookup table for the gate."""
    merged = dict(facts)
    merged.update(derive(facts, address_key, cfg))
    return merged
