"""The vocabulary of listing facts.

Every fact the agent may record, and every field a gate rule may test, is named
here (or added under `custom_fields` in the config). Keeping one registry means a
typo in a config or in an agent's JSON is an error, not a silently ignored key.
"""

from __future__ import annotations

import re

from .util import is_number

NUMBER, BOOL, ENUM, TEXT, DATE, LIST = "number", "bool", "enum", "text", "date", "list"


class Field(object):
    def __init__(self, type_, help_, values=None, ordered=False, derived_from=None):
        self.type = type_
        self.help = help_
        self.values = values or []
        self.ordered = ordered          # enum values run worst -> best
        self.derived_from = derived_from  # computed by derive.py from these facts

    def describe(self):
        text = self.type
        if self.values:
            text += " (%s)" % (" < " if self.ordered else " | ").join(self.values)
        return text


PET_LEVELS = ["not_allowed", "case_by_case", "allowed"]

BASE_FIELDS = {
    # Money
    "rent_advertised": Field(NUMBER, "Monthly rent exactly as the listing headline shows it"),
    "rent_advertised_is": Field(ENUM, "Whether the headline rent is the gross (lease) rent or a net effective rent",
                                ["gross", "net_effective"]),
    "free_months": Field(NUMBER, "Months free offered as a concession; 0 only if the page shows no concession"),
    "lease_term_months": Field(NUMBER, "Lease length in months that the advertised rent applies to"),
    "tenant_pays_broker_fee": Field(BOOL, "True if the listing asks the tenant to pay a broker fee"),
    "broker_fee_amount": Field(NUMBER, "Broker fee in dollars the tenant is asked to pay"),
    "security_deposit": Field(NUMBER, "Security deposit in dollars"),
    "application_fee": Field(NUMBER, "Application or credit-check fee in dollars, per applicant"),
    "rent_stabilized": Field(BOOL, "True if the listing says the unit is rent-stabilized"),
    "income_multiple_required": Field(NUMBER, "Required annual income as a multiple of monthly rent, e.g. 40"),
    "guarantor_accepted": Field(BOOL, "True if guarantors are accepted"),
    # Size and layout
    "beds": Field(NUMBER, "Bedrooms as listed; 0 for a studio"),
    "true_bedrooms": Field(NUMBER, "Bedrooms with a window that fit a queen bed, judged from floor plan or photos"),
    "flex_walls": Field(BOOL, "True if a bedroom relies on a flex or pressurized wall, or is 'convertible'"),
    "baths": Field(NUMBER, "Bathrooms; a half bath counts 0.5"),
    "sqft": Field(NUMBER, "Interior square feet"),
    "unit_type": Field(ENUM, "Kind of home", ["room", "apartment", "condo", "coop", "townhouse", "house"]),
    # Location
    "neighborhood": Field(TEXT, "Neighborhood name as the listing gives it"),
    "borough": Field(TEXT, "Borough, or city district"),
    "zip": Field(TEXT, "5-digit zip code"),
    "commute_minutes": Field(NUMBER, "Door-to-door minutes to the commute anchor by the configured mode"),
    "commute_transfers": Field(NUMBER, "Transfers on that route"),
    "walk_to_station_minutes": Field(NUMBER, "Walking minutes to the nearest useful station"),
    "transit_lines": Field(LIST, "Lines at that station"),
    # Building
    "floor": Field(NUMBER, "Floor the unit is on; ground floor is 1"),
    "level": Field(ENUM, "Where the unit sits relative to the street", ["basement", "garden", "ground", "upper"]),
    "elevator": Field(BOOL, "True if the building has an elevator"),
    "doorman": Field(BOOL, "True if there is a doorman or concierge"),
    "package_room": Field(BOOL, "True if there is a package room or lockers"),
    "year_built": Field(NUMBER, "Year the building was built"),
    # Lease type
    "furnished": Field(BOOL, "True if rented furnished"),
    "sublet": Field(BOOL, "True if this is a sublet or lease assignment"),
    "short_term": Field(BOOL, "True if offered only for less than 12 months"),
    "room_share": Field(BOOL, "True if this is a room in a shared home, not the whole unit"),
    "available_date": Field(DATE, "Date available, YYYY-MM-DD; today's date if 'available now'"),
    # Pets
    "cats": Field(ENUM, "Cat policy. 'allowed' needs explicit wording", PET_LEVELS, ordered=True),
    "dogs": Field(ENUM, "Dog policy. 'allowed' needs explicit wording", PET_LEVELS, ordered=True),
    # Amenities
    "laundry": Field(ENUM, "Best laundry option", ["none", "in_building", "in_unit"], ordered=True),
    "dishwasher": Field(BOOL, "True if there is a dishwasher"),
    "ac": Field(ENUM, "Best cooling option", ["none", "window", "through_wall", "split", "central"], ordered=True),
    "outdoor": Field(ENUM, "Best outdoor space", ["none", "shared", "private"], ordered=True),
    "storage": Field(BOOL, "True if storage beyond closets is included or offered"),
    "parking": Field(BOOL, "True if parking is included or offered"),
    "gym": Field(BOOL, "True if the building has a gym"),
    # Environment
    "flood_zone": Field(ENUM, "Flood risk from an official flood map", ["none", "moderate", "high"], ordered=False),
    "noise_sources": Field(LIST, "Named noise sources nearby, e.g. 'elevated J/M/Z one block south'"),
    # Listing quality and scam signals
    "photo_count": Field(NUMBER, "Number of photos on the listing"),
    "photos_recency": Field(ENUM, "Whether the photos look current", ["stale", "recent"]),
    "photos_reused_elsewhere": Field(BOOL, "True if the same photos appear on a different address or listing"),
    "asks_deposit_before_viewing": Field(BOOL, "True if money is requested before a viewing"),
    "owner_remote": Field(BOOL, "True if the poster says they are away and cannot show the unit"),
    "listed_date": Field(DATE, "Date the listing was posted, YYYY-MM-DD"),
}

RENT_INPUTS = ["rent_advertised", "rent_advertised_is", "free_months", "lease_term_months"]

DERIVED_FIELDS = {
    "rent_gross": Field(NUMBER, "Monthly rent written on the lease", derived_from=RENT_INPUTS),
    "rent_net_effective": Field(NUMBER, "Gross rent averaged over the lease after free months",
                                derived_from=RENT_INPUTS),
    "monthly_all_in": Field(NUMBER, "Net effective rent plus any tenant-paid broker fee spread over the lease",
                            derived_from=RENT_INPUTS + ["tenant_pays_broker_fee", "broker_fee_amount"]),
    "in_target_area": Field(BOOL, "True if the neighborhood or zip is on the target list",
                            derived_from=["neighborhood", "zip"]),
    "address_disclosed": Field(BOOL, "True if a street number and street name are known", derived_from=[]),
    "income_multiple_have": Field(NUMBER, "profile.annual_income divided by gross monthly rent",
                                  derived_from=RENT_INPUTS),
}

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def build_registry(custom_fields=None):
    """All known fields, including any `custom_fields` from the config."""
    registry = dict(BASE_FIELDS)
    registry.update(DERIVED_FIELDS)
    for spec in custom_fields or []:
        registry[spec["id"]] = Field(spec["type"], spec.get("help", ""), spec.get("values"),
                                     bool(spec.get("ordered")))
    return registry


def check_value(name, field, value):
    """Return an error string if `value` is the wrong shape for `field`, else None."""
    if value is None:
        return None
    if field.type == NUMBER and not is_number(value):
        return "%s must be a number, got %r" % (name, value)
    if field.type == BOOL and not isinstance(value, bool):
        return "%s must be true or false, got %r" % (name, value)
    if field.type == ENUM and value not in field.values:
        return "%s must be one of %s, got %r" % (name, ", ".join(field.values), value)
    if field.type == TEXT and not isinstance(value, str):
        return "%s must be text, got %r" % (name, value)
    if field.type == DATE and not (isinstance(value, str) and _DATE_RE.match(value)):
        return "%s must be a date like 2026-12-01, got %r" % (name, value)
    if field.type == LIST and not (isinstance(value, list) and all(isinstance(v, str) for v in value)):
        return "%s must be a list of text items, got %r" % (name, value)
    return None


def base_inputs(name, registry):
    """The recorded facts a field depends on (itself, unless it is derived)."""
    field = registry[name]
    if field.derived_from is None:
        return [name]
    return list(field.derived_from)
