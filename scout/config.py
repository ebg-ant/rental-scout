"""Load and validate scout.config.yaml.

All search criteria live in that file. Validation is strict and reports every
problem at once in plain English, because the person fixing it may not be
technical and the agent fixing it should not have to guess.
"""

from __future__ import annotations

import datetime as dt
import os
import re

from ._vendor import yaml
from .fields import BOOL, DATE, ENUM, LIST, NUMBER, TEXT, build_registry, check_value
from .util import ScoutError, is_number, is_real_date, money, stable_hash

DEFAULT_CONFIG = "scout.config.yaml"

OP_ALIASES = {
    "lte": "lte", "<=": "lte", "at_most": "lte", "max": "lte", "on_or_before": "lte",
    "gte": "gte", ">=": "gte", "at_least": "gte", "min": "gte", "on_or_after": "gte",
    "lt": "lt", "<": "lt", "below": "lt", "before": "lt",
    "gt": "gt", ">": "gt", "above": "gt", "after": "gt",
    "eq": "eq", "==": "eq", "=": "eq", "is": "eq", "equals": "eq",
    "ne": "ne", "!=": "ne", "is_not": "ne",
    "in": "in", "one_of": "in",
    "not_in": "not_in", "none_of": "not_in",
}
OP_WORDS = {"lte": "at most", "gte": "at least", "lt": "below", "gt": "above",
            "eq": "is", "ne": "is not", "in": "is one of", "not_in": "is none of"}
ORDER_OPS = ("lte", "gte", "lt", "gt")
ON_UNKNOWN = ("fail", "pass", "flag")
EVIDENCE_TYPES = ("photos", "floor_plan", "satellite", "street_view", "map", "text", "records")
URL_STYLES = ("streeteasy", "zillow", "craigslist", "manual", "inbox")
CARD_FIELDS = ("rent_advertised", "rent_gross", "rent_net_effective", "beds", "baths", "sqft",
               "neighborhood", "zip", "borough", "in_target_area")
CUSTOM_TYPES = (NUMBER, BOOL, ENUM, TEXT, DATE, LIST)


def _plain(value):
    """YAML turns 2026-12-01 into a date object; the store is JSON, so use text."""
    if isinstance(value, dict):
        return dict((k, _plain(v)) for k, v in value.items())
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()[:10]
    return value


def compile_phrase(phrase):
    """A plain phrase matches on word boundaries; prefix with `re:` for a regex."""
    phrase = str(phrase)
    if phrase.startswith("re:"):
        return re.compile(phrase[3:], re.IGNORECASE)
    words = [re.escape(word) for word in phrase.lower().split()]
    return re.compile(r"(?<!\w)" + r"\s+".join(words) + r"(?!\w)", re.IGNORECASE)


class Config(object):
    def __init__(self, raw, path):
        self.raw = raw
        self.path = path
        self.registry = build_registry(raw.get("custom_fields"))
        self.profile = raw.get("profile") or {}
        self.search = raw["search"]
        self.areas = self.search.get("areas") or {}
        self.sources = self.search["sources"]
        self.prefilters = raw.get("prefilters") or []
        self.gate = raw["gate"]
        self.capture = (raw.get("capture") or {}).get("always") or []
        self.rubric = raw["rubric"]
        self.verdicts = raw["verdicts"]
        self.risk = raw.get("risk") or {}
        self.costs = raw.get("costs") or {}
        self.limits = raw["limits"]
        self.refresh = raw["refresh"]
        self.calibration = raw.get("calibration") or {}

    def enabled_sources(self):
        return [sid for sid, spec in self.sources.items() if spec.get("enabled")]

    def judge_hash(self):
        """Changes whenever anything that affects a non-rubric decision changes."""
        return stable_hash([self.profile, self.areas, self.prefilters, self.gate, self.verdicts,
                            self.risk, self.costs, self.capture,
                            [(c["id"], c["weight"]) for c in self.rubric]])

    def criterion_hashes(self):
        """Changes for a criterion when its meaning changes. A weight is not meaning."""
        return dict((c["id"], stable_hash([c["id"], c["guidance"], c["anchors"], c["evidence_required"]]))
                    for c in self.rubric)


def _lookup(raw, dotted):
    node = raw
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None, False
        node = node[part]
    return node, True


class _Checker(object):
    def __init__(self, raw):
        self.raw = raw
        self.errors = []
        self.warnings = []

    def err(self, where, message):
        self.errors.append("%s: %s" % (where, message))

    def warn(self, where, message):
        self.warnings.append("%s: %s" % (where, message))

    def section(self, name, kind=dict, required=True):
        value = self.raw.get(name)
        if value is None:
            if required:
                self.err(name, "this section is missing")
            return None
        if not isinstance(value, kind):
            self.err(name, "should be a %s" % ("list" if kind is list else "set of key: value lines"))
            return None
        return value

    def mapping(self, where, value):
        """`value` if it is a set of key: value lines, {} if absent; reports anything else."""
        if value is None:
            return {}
        if not isinstance(value, dict):
            self.err(where, "should be a set of key: value lines")
            return {}
        return value

    def number(self, where, value, low=None, high=None, whole=False, optional=False):
        if value is None and optional:
            return True
        if not is_number(value) or (whole and int(value) != value):
            self.err(where, "should be a %snumber, got %r" % ("whole " if whole else "", value))
            return False
        if low is not None and value < low:
            self.err(where, "should be %s or more, got %s" % (low, value))
            return False
        if high is not None and value > high:
            self.err(where, "should be %s or less, got %s" % (high, value))
            return False
        return True

    def text_list(self, where, value, allow_empty=True):
        if value is None and allow_empty:
            return True
        if not isinstance(value, list) or not all(isinstance(v, (str, int)) for v in value):
            self.err(where, "should be a list of text items")
            return False
        if not value and not allow_empty:
            self.err(where, "needs at least one item")
            return False
        return True

    def phrases(self, where, value, allow_empty=True):
        if not self.text_list(where, value, allow_empty):
            return
        for phrase in value or []:
            try:
                compile_phrase(phrase)
            except re.error as exc:
                self.err(where, "%r is not a valid pattern (%s)" % (phrase, exc))


def _find_todos(node, path, found):
    if isinstance(node, dict):
        if node.get("enabled") is False:
            return  # switched-off rules and sources may keep their placeholders
        for key, value in node.items():
            _find_todos(value, "%s.%s" % (path, key) if path else str(key), found)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            label = value.get("id") if isinstance(value, dict) and value.get("id") else index
            _find_todos(value, "%s[%s]" % (path, label), found)
    elif isinstance(node, str) and node.strip().upper().startswith("TODO"):
        found.append(path)


def _check_condition(chk, where, cond, registry, raw):
    """Validate {field, op, value|value_from} and normalise op and value in place."""
    if not isinstance(cond, dict):
        chk.err(where, "should have field, op and value")
        return False
    name = cond.get("field")
    if name not in registry:
        chk.err(where, "unknown field %r. Run `python3 -m scout fields` to list valid fields, "
                       "or add it under custom_fields" % name)
        return False
    field = registry[name]
    op = OP_ALIASES.get(str(cond.get("op")).lower())
    if not op:
        chk.err(where, "unknown op %r. Use at_most, at_least, is, is_not, in or not_in" % cond.get("op"))
        return False
    cond["op"] = op
    if "value_from" in cond:
        value, found = _lookup(raw, str(cond["value_from"]))
        if not found or value is None:
            chk.err(where, "value_from points at %s, which is empty" % cond["value_from"])
            return False
        cond["value"] = value
    if "value" not in cond or cond["value"] is None:
        chk.err(where, "needs a value")
        return False
    value = cond["value"]
    if op in ORDER_OPS and not (field.type in (NUMBER, DATE) or (field.type == ENUM and field.ordered)):
        chk.err(where, "%s cannot be compared with %r because it has no order" % (name, OP_WORDS[op]))
        return False
    if field.type == LIST:
        chk.err(where, "%s is a list and cannot be used in a rule" % name)
        return False
    if op in ("in", "not_in"):
        if not isinstance(value, list) or not value:
            chk.err(where, "op %r needs a list of values" % cond.get("op"))
            return False
        candidates = value
    else:
        candidates = [value]
    ok = True
    for candidate in candidates:
        if field.type == TEXT and is_number(candidate):
            continue  # zips written without quotes
        problem = check_value(name, field, candidate)
        if problem:
            chk.err(where, problem)
            ok = False
    return ok


def _check_profile(chk):
    profile = chk.section("profile", required=False) or {}
    chk.number("profile.annual_income", profile.get("annual_income"), low=0, optional=True)
    for key in ("move_in_earliest", "move_in_latest"):
        value = profile.get(key)
        if value is not None and not is_real_date(value):
            chk.err("profile." + key, "should be a real date like 2026-12-01")
    first, last = profile.get("move_in_earliest"), profile.get("move_in_latest")
    if first and last and str(first) > str(last):
        chk.err("profile", "move_in_earliest is after move_in_latest")


def _check_search(chk):
    search = chk.section("search")
    if search is None:
        return
    if not search.get("city"):
        chk.err("search.city", "is missing")
    low_ok = chk.number("search.price_min", search.get("price_min"), low=0)
    high_ok = chk.number("search.price_max", search.get("price_max"), low=1)
    if low_ok and high_ok and search["price_min"] >= search["price_max"]:
        chk.err("search", "price_min should be below price_max")
    areas = chk.mapping("search.areas", search.get("areas"))
    search["areas"] = areas
    chk.text_list("search.areas.neighborhoods", areas.get("neighborhoods"))
    if chk.text_list("search.areas.zips", areas.get("zips")):
        clean_zips = []
        for value in areas.get("zips") or []:
            # YAML reads an unquoted 07030 as the octal number 3608.
            if not re.match(r"^\d{5}$", str(value)):
                chk.err("search.areas.zips", "%r is not a 5-digit zip. Put quotes around zips that start with 0" % value)
            clean_zips.append(str(value))
        areas["zips"] = clean_zips
    for name, others in chk.mapping("search.areas.aliases", areas.get("aliases")).items():
        chk.text_list("search.areas.aliases.%s" % name, others)
    chk.mapping("search.street_aliases", search.get("street_aliases"))
    search["commute_anchor"] = chk.mapping("search.commute_anchor", search.get("commute_anchor"))
    for index, link in enumerate(search.get("helper_links") or []):
        if not isinstance(link, dict) or not link.get("id") or not link.get("url"):
            chk.err("search.helper_links[%d]" % index, "needs id and url")

    sources = search.get("sources")
    if not isinstance(sources, dict) or not sources:
        chk.err("search.sources", "needs at least one source")
        return
    enabled = 0
    for sid, spec in sources.items():
        where = "search.sources.%s" % sid
        if not isinstance(spec, dict):
            chk.err(where, "should be a set of key: value lines")
            continue
        if not re.match(r"^[a-z][a-z0-9_]*$", str(sid)):
            chk.err(where, "source names use lowercase letters, digits and underscores only")
        style = spec.get("url_style")
        if style not in URL_STYLES:
            chk.err(where, "url_style should be one of %s" % ", ".join(URL_STYLES))
        chk.text_list(where + ".saved_search_urls", spec.get("saved_search_urls"))
        chk.number(where + ".max_pages", spec.get("max_pages"), low=1, whole=True, optional=True)
        chk.number(where + ".throttle_seconds", spec.get("throttle_seconds"), low=0, optional=True)
        chk.number(where + ".band_width", spec.get("band_width"), low=50, whole=True, optional=True)
        chk.mapping(where + ".params", spec.get("params"))
        if not spec.get("enabled"):
            continue
        enabled += 1
        urls = spec.get("saved_search_urls") or []
        for url in urls:
            if not str(url).startswith("https://"):
                chk.err(where + ".saved_search_urls", "%r should start with https://. If this is a piece of a longer URL, "
                                                      "put the whole URL in \"quotes\": commas split it otherwise" % url)
        if style == "craigslist" and not urls and not (spec.get("site") and spec.get("subareas")):
            chk.err(where, "needs either saved_search_urls, or site plus subareas")
        if style in ("streeteasy", "zillow", "manual") and not urls:
            chk.err(where, "is enabled but has no saved_search_urls. Add one, or set enabled: false")
    if not enabled:
        chk.err("search.sources", "no source is enabled")


def _check_custom_fields(chk):
    specs = chk.section("custom_fields", list, required=False) or []
    good = []
    for index, spec in enumerate(specs):
        where = "custom_fields[%d]" % index
        if not isinstance(spec, dict) or not re.match(r"^[a-z][a-z0-9_]*$", str(spec.get("id"))):
            chk.err(where, "needs an id made of lowercase letters, digits and underscores")
            continue
        if spec.get("type") not in CUSTOM_TYPES:
            chk.err(where, "type should be one of %s" % ", ".join(CUSTOM_TYPES))
            continue
        if spec["type"] == ENUM and not spec.get("values"):
            chk.err(where, "an enum field needs a list of values")
            continue
        good.append(spec)
    return good


def _check_prefilters(chk):
    seen = set()
    for index, rule in enumerate(chk.section("prefilters", list, required=False) or []):
        where = "prefilters[%s]" % (rule.get("id", index) if isinstance(rule, dict) else index)
        if not isinstance(rule, dict) or not rule.get("id"):
            chk.err(where, "needs an id")
            continue
        if rule["id"] in seen:
            chk.err(where, "id is used twice")
        seen.add(rule["id"])
        chk.phrases(where + ".reject_if_any", rule.get("reject_if_any"), allow_empty=False)
        chk.phrases(where + ".unless_any", rule.get("unless_any"))
        if not rule.get("reason"):
            chk.err(where, "needs a reason to show in the report")
        rule.setdefault("enabled", True)


def _check_gate(chk, registry):
    gate = chk.section("gate")
    if gate is None:
        return
    chk.number("gate.max_fails", gate.get("max_fails"), low=0, whole=True)
    rules = gate.get("rules")
    if not isinstance(rules, list) or not rules:
        chk.err("gate.rules", "needs at least one rule")
        return
    seen = set()
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            chk.err("gate.rules[%d]" % index, "should have field, op and value")
            continue
        rule["id"] = str(rule.get("id") or rule.get("field"))
        where = "gate.rules[%s]" % rule["id"]
        if rule["id"] in seen:
            chk.err(where, "id is used twice; give each rule its own id")
        seen.add(rule["id"])
        rule.setdefault("enabled", True)
        rule.setdefault("on_unknown", "flag")
        rule.setdefault("dealbreaker", False)
        rule.setdefault("label", rule["id"].replace("_", " "))
        if not rule["enabled"]:
            continue
        ok = _check_condition(chk, where, rule, registry, chk.raw)
        if rule["on_unknown"] not in ON_UNKNOWN:
            # YAML reads a bare `on_unknown: pass` fine, but `yes`/`no` become booleans.
            chk.err(where, "on_unknown should be fail, pass or flag, got %r" % rule["on_unknown"])
        if "unless" in rule:
            _check_condition(chk, where + ".unless", rule["unless"], registry, chk.raw)
        if rule.get("trust_card"):
            if rule.get("field") not in CARD_FIELDS:
                chk.err(where, "trust_card only works for fields shown on search cards: %s"
                        % ", ".join(CARD_FIELDS))
            if "unless" in rule:
                chk.err(where, "trust_card cannot be combined with unless")
        if "card_margin_pct" in rule:
            chk.number(where + ".card_margin_pct", rule["card_margin_pct"], low=0, high=100)
            if ok and registry[rule["field"]].type != NUMBER:
                chk.err(where, "card_margin_pct only applies to number fields")


def _check_capture(chk, registry):
    capture = chk.section("capture", required=False) or {}
    chk.raw["capture"] = capture
    always = capture.get("always") or []
    if not chk.text_list("capture.always", always):
        return
    for name in always:
        if name not in registry:
            chk.err("capture.always", "unknown field %r" % name)
        elif registry[name].derived_from is not None:
            chk.err("capture.always", "%s is calculated, not captured; remove it" % name)


def _check_rubric(chk):
    rubric = chk.section("rubric", list)
    if rubric is None:
        return
    if not rubric:
        chk.err("rubric", "needs at least one criterion")
        return
    seen, total, weights_ok = set(), 0.0, True
    for index, crit in enumerate(rubric):
        if not isinstance(crit, dict) or not crit.get("id"):
            chk.err("rubric[%d]" % index, "needs an id")
            weights_ok = False
            continue
        crit["id"] = str(crit["id"])
        where = "rubric[%s]" % crit["id"]
        if crit["id"] in seen:
            chk.err(where, "id is used twice")
        seen.add(crit["id"])
        crit.setdefault("label", crit["id"].replace("_", " ").capitalize())
        if chk.number(where + ".weight", crit.get("weight"), low=0, high=1):
            total += crit["weight"]
        else:
            weights_ok = False
        if not crit.get("guidance"):
            chk.err(where, "needs guidance: one or two sentences on what to judge")
        anchors = crit.get("anchors")
        if not isinstance(anchors, dict) or len(anchors) < 3:
            chk.err(where, "needs at least 3 anchors, e.g. 10, 7, 5 and 2, each with a concrete example")
        else:
            clean = {}
            for level, example in anchors.items():
                if not is_number(level) or not 0 <= level <= 10:
                    chk.err(where + ".anchors", "%r is not a score between 0 and 10" % level)
                elif not example or not isinstance(example, str):
                    chk.err(where + ".anchors", "score %s needs a text example" % level)
                else:
                    clean[str(int(level) if int(level) == level else level)] = example
            crit["anchors"] = clean
        required = crit["evidence_required"] = crit.get("evidence_required") or []
        if chk.text_list(where + ".evidence_required", required):
            for kind in required:
                if kind not in EVIDENCE_TYPES:
                    chk.err(where + ".evidence_required",
                            "%r is not one of %s" % (kind, ", ".join(EVIDENCE_TYPES)))
    if weights_ok and abs(total - 1.0) > 0.001:
        chk.err("rubric", "weights add up to %.3f but must add up to 1.0" % total)


def _check_verdicts(chk):
    verdicts = chk.section("verdicts")
    if verdicts is None:
        return
    tour = verdicts.get("tour")
    watch = verdicts.get("watch")
    if not isinstance(tour, dict) or not isinstance(watch, dict):
        chk.err("verdicts", "needs tour and watch sections")
        return
    tour.setdefault("max_gate_fails", 0)
    tour.setdefault("max_gate_unknowns", 0)
    tour.setdefault("max_unknown_weight", 0.25)
    first = chk.number("verdicts.tour.min_score", tour.get("min_score"), low=0, high=100)
    second = chk.number("verdicts.watch.min_score", watch.get("min_score"), low=0, high=100)
    if first and second and watch["min_score"] >= tour["min_score"]:
        chk.err("verdicts", "watch.min_score should be below tour.min_score")
    chk.number("verdicts.tour.max_gate_fails", tour["max_gate_fails"], low=0, whole=True)
    chk.number("verdicts.tour.max_gate_unknowns", tour["max_gate_unknowns"], low=0, whole=True)
    chk.number("verdicts.tour.max_unknown_weight", tour["max_unknown_weight"], low=0, high=1)


def _check_risk_costs(chk):
    risk = chk.section("risk", required=False)
    if risk is not None:
        risk.setdefault("low_flags_to_pass", 2)
        risk.setdefault("min_comps", 8)
        chk.number("risk.price_floor_pct", risk.get("price_floor_pct"), low=0, high=1, optional=True)
        chk.number("risk.min_comps", risk["min_comps"], low=3, whole=True)
        chk.number("risk.long_listed_days", risk.get("long_listed_days"), low=1, optional=True)
        chk.number("risk.low_flags_to_pass", risk["low_flags_to_pass"], low=1, whole=True)
        chk.phrases("risk.scam_phrases", risk.get("scam_phrases"))
        chk.text_list("risk.disable", risk.get("disable"))
        reference = risk.get("reference_rents") or {}
        if not isinstance(reference, dict):
            chk.err("risk.reference_rents", "should map bedrooms to a typical monthly rent")
        else:
            for beds, rent in reference.items():
                chk.number("risk.reference_rents.%s" % beds, rent, low=1, optional=True)
    costs = chk.section("costs", required=False)
    if costs is not None:
        chk.number("costs.max_deposit_months", costs.get("max_deposit_months"), low=0, optional=True)
        chk.number("costs.max_application_fee", costs.get("max_application_fee"), low=0, optional=True)
        chk.number("costs.default_income_multiple", costs.get("default_income_multiple"), low=0, optional=True)


def _check_limits(chk):
    limits = chk.section("limits")
    if limits is None:
        return
    limits.setdefault("min_band_width", 100)
    limits.setdefault("max_search_pages_per_run", 6)
    chk.number("limits.max_listings_per_run", limits.get("max_listings_per_run"), low=1, whole=True)
    chk.number("limits.max_search_pages_per_run", limits["max_search_pages_per_run"], low=1, whole=True)
    chk.number("limits.throttle_seconds", limits.get("throttle_seconds"), low=0)
    chk.number("limits.max_runtime_minutes", limits.get("max_runtime_minutes"), low=1)
    chk.number("limits.min_band_width", limits["min_band_width"], low=1)
    known = ("max_listings_per_run", "max_search_pages_per_run", "throttle_seconds", "max_runtime_minutes", "min_band_width")
    for name in limits:
        if name not in known:
            chk.err("limits." + str(name), "is not a setting. Expected %s. Re-check and delisting settings live under "
                                           "`refresh`" % ", ".join(known))
    if is_number(limits.get("throttle_seconds")) and limits["throttle_seconds"] < 3:
        chk.warn("limits.throttle_seconds", "under 3 seconds is likely to trip bot detection")
    calibration = chk.section("calibration", required=False)
    if calibration is not None:
        chk.number("calibration.min_tours", calibration.get("min_tours"), low=1, whole=True, optional=True)


REFRESH_DEFAULTS = {"full_sweep_every_days": 7, "recheck_tour_after_days": 2, "recheck_watch_after_days": 4,
                    "max_rechecks_per_run": 4, "delist_after_missed_sweeps": 2}


def _check_refresh(chk):
    refresh = chk.section("refresh", required=False) or {}
    chk.raw["refresh"] = refresh
    for name, default in REFRESH_DEFAULTS.items():
        refresh.setdefault(name, default)
        chk.number("refresh." + name, refresh[name], low=0 if name == "max_rechecks_per_run" else 1,
                   whole=name in ("max_rechecks_per_run", "delist_after_missed_sweeps"))
    for name in refresh:
        if name not in REFRESH_DEFAULTS:
            chk.err("refresh." + str(name), "is not a setting. Expected %s" % ", ".join(sorted(REFRESH_DEFAULTS)))


def _cross_checks(chk):
    """Problems that only show up when sections are read together."""
    raw = chk.raw
    search = raw.get("search") if isinstance(raw.get("search"), dict) else {}
    gate = raw.get("gate") if isinstance(raw.get("gate"), dict) else {}
    rules = [r for r in gate.get("rules") or [] if isinstance(r, dict) and r.get("enabled", True)]
    used = set(r.get("field") for r in rules)
    used.update((raw.get("capture") or {}).get("always") or [])
    areas = search.get("areas") or {}
    if "in_target_area" in used and not (areas.get("neighborhoods") or areas.get("zips")):
        chk.err("search.areas", "the gate checks in_target_area, so list some neighborhoods or zips")
    anchor = search.get("commute_anchor") or {}
    if "commute_minutes" in used and not anchor.get("address"):
        chk.err("search.commute_anchor.address", "the scout measures commute time, so it needs an address")
    if "income_multiple_have" in used and not (raw.get("profile") or {}).get("annual_income"):
        chk.err("profile.annual_income", "the gate checks income_multiple_have, so this cannot be empty")
    pages = (raw.get("limits") or {}).get("max_search_pages_per_run")
    for sid, spec in (search.get("sources") or {}).items():
        if isinstance(spec, dict) and spec.get("enabled") and is_number(pages):
            searches = len(spec.get("saved_search_urls") or []) or len(spec.get("subareas") or [])
            if searches * 2 > pages:
                chk.warn("limits.max_search_pages_per_run", "%s has %d searches but only %d results pages per run, so a full "
                         "sweep of it will be slow to finish. Allow at least %d" % (sid, searches, pages, searches * 2))
    for rule in rules:
        if rule.get("field") in ("rent_gross", "rent_net_effective", "monthly_all_in") \
                and rule.get("op") in ("lte", "lt") and is_number(rule.get("value")) \
                and is_number(search.get("price_max")) and search["price_max"] < rule["value"]:
            chk.warn("search.price_max", "is below the gate's rent ceiling of %s, so the sweep will miss "
                                         "listings you would accept" % money(rule["value"]))


def _guard(chk, section, check):
    """Run one section's checks. A shape nobody anticipated is still a plain-English
    error about that section, and the other sections still get checked."""
    try:
        return check(chk)
    except (TypeError, AttributeError, KeyError, ValueError, IndexError) as exc:
        chk.err(section, "has an unexpected shape, so it could not be checked (%s). Compare it with "
                         "configs/nyc.template.yaml" % exc)
        return None


def validate(raw, path="config"):
    """Return (Config, warnings). Raises ScoutError listing every problem found."""
    if not isinstance(raw, dict):
        raise ScoutError("%s is empty or is not a set of key: value sections" % path)
    raw = _plain(raw)
    chk = _Checker(raw)
    todos = []
    _find_todos(raw, "", todos)
    if todos:
        raise ScoutError(["Still marked TODO: %s" % item for item in todos],
                         hint="Fill these in (agent/SETUP_INTERVIEW.md walks through them), "
                              "or set `enabled: false` on a rule or source you do not want.")
    if raw.get("version") != 1:
        chk.err("version", "should be 1")
    registry = build_registry(_guard(chk, "custom_fields", _check_custom_fields) or [])
    for name, check in (("profile", _check_profile), ("search", _check_search), ("prefilters", _check_prefilters),
                        ("gate", lambda c: _check_gate(c, registry)), ("capture", lambda c: _check_capture(c, registry)),
                        ("rubric", _check_rubric), ("verdicts", _check_verdicts), ("risk", _check_risk_costs),
                        ("limits", _check_limits), ("refresh", _check_refresh)):
        _guard(chk, name, check)
    _guard(chk, "config", _cross_checks)
    if chk.errors:
        raise ScoutError(chk.errors, hint="Fix these in %s and run `python3 -m scout validate` again." % path)
    return Config(raw, path), chk.warnings


def load(path=None):
    path = path or os.environ.get("SCOUT_CONFIG") or DEFAULT_CONFIG
    if not os.path.exists(path):
        raise ScoutError("No config found at %s" % path,
                         hint="First run? Follow agent/SETUP_INTERVIEW.md to create it from "
                              "configs/nyc.template.yaml.")
    with open(path, "r", encoding="utf-8") as handle:
        try:
            raw = yaml.safe_load(handle)
        except yaml.YAMLError as exc:
            raise ScoutError("%s is not valid YAML: %s" % (path, exc))
    return validate(raw, path)
