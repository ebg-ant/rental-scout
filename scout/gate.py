"""The quantitative gate. Pure code, three-valued: pass, fail or unknown."""

from __future__ import annotations

import re

from .config import OP_WORDS
from .fields import ENUM, NUMBER, TEXT, base_inputs
from .util import is_number, money, norm_text

PASS, FAIL, UNKNOWN = "pass", "fail", "unknown"
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONEY_FIELDS = ("rent_advertised", "rent_gross", "rent_net_effective", "monthly_all_in")


def _comparable(field, value):
    if field.type == ENUM and field.ordered:
        return field.values.index(value)
    if field.type == TEXT or (field.type == ENUM and isinstance(value, str)):
        return norm_text(value)
    return value


def evaluate(cond, values, registry, margin_pct=0):
    """Test one {field, op, value}. A missing value is UNKNOWN, never a guess.

    margin_pct loosens a numeric limit; it is used only for search-card checks,
    where the number shown may not be the number that counts.
    """
    actual = values.get(cond["field"])
    if actual is None:
        return UNKNOWN
    field, op, expected = registry[cond["field"]], cond["op"], cond["value"]
    if op in ("in", "not_in"):
        hit = _comparable(field, str(actual) if field.type == TEXT else actual) in \
            [_comparable(field, str(v) if field.type == TEXT else v) for v in expected]
        return PASS if hit == (op == "in") else FAIL
    if field.type == TEXT:
        actual, expected = str(actual), str(expected)
    left, right = _comparable(field, actual), _comparable(field, expected)
    if margin_pct and field.type == NUMBER:
        slack = abs(right) * margin_pct / 100.0
        right = right + slack if op in ("lte", "lt") else right - slack if op in ("gte", "gt") else right
    result = {"lte": left <= right, "gte": left >= right, "lt": left < right, "gt": left > right,
              "eq": left == right, "ne": left != right}[op]
    return PASS if result else FAIL


def _evaluate_rule(rule, values, registry):
    """Apply `unless`: the rule is satisfied if either the test or the exception holds."""
    main = evaluate(rule, values, registry)
    if "unless" not in rule or main == PASS:
        return main
    exception = evaluate(rule["unless"], values, registry)
    if exception == PASS:
        return PASS
    if main == FAIL and exception == FAIL:
        return FAIL
    return UNKNOWN


def show(name, value):
    if value is None:
        return "unknown"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if name in _MONEY_FIELDS and is_number(value):
        return money(value)
    if isinstance(value, float):
        return ("%.1f" % value).rstrip("0").rstrip(".")
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value).replace("_", " ")


def _condition_text(cond):
    value = show(cond["field"], cond["value"])
    if cond["op"] == "eq":
        return value
    if cond["op"] == "ne":
        return "not " + value
    words = OP_WORDS[cond["op"]].replace("is ", "")
    if cond["op"] in ("lte", "gte") and _DATE_RE.match(str(cond["value"])):
        words = "on or before" if cond["op"] == "lte" else "on or after"
    return "%s %s" % (words, value)


def expected_text(rule):
    """What a rule asks for, in words: "at most 3, unless elevator is yes"."""
    text = _condition_text(rule)
    if "unless" in rule:
        text += ", unless %s is %s" % (rule["unless"]["field"].replace("_", " "), _condition_text(rule["unless"]))
    return text


def run(cfg, values, ignoring=()):
    """Evaluate every enabled rule and roll the results up.

    `ignoring` names facts to leave out of it: rules that depend on any of them are
    skipped. It answers "would this still fail whatever the rent is now?".
    """
    rows, fails, unknowns, dealbreaker = [], 0, 0, False
    for rule in cfg.gate["rules"]:
        if not rule["enabled"]:
            continue
        if ignoring and set(base_inputs(rule["field"], cfg.registry)) & set(ignoring):
            continue
        status = _evaluate_rule(rule, values, cfg.registry)
        counted = status
        if status == UNKNOWN:
            counted = {"fail": FAIL, "pass": PASS, "flag": UNKNOWN}[rule["on_unknown"]]
        if counted == FAIL:
            fails += 1
            dealbreaker = dealbreaker or rule["dealbreaker"]
        elif counted == UNKNOWN:
            unknowns += 1
        actual = show(rule["field"], values.get(rule["field"]))
        if "unless" in rule and status == PASS and evaluate(rule, values, cfg.registry) != PASS:
            other = rule["unless"]["field"]     # it passed on the exception, so show the exception
            actual += " (%s %s)" % (other.replace("_", " "), show(other, values.get(other)))
        rows.append({"id": rule["id"], "label": rule["label"], "field": rule["field"], "status": status,
                     "counted_as": counted, "actual": actual,
                     "expected": expected_text(rule), "dealbreaker": rule["dealbreaker"]})
    failed = dealbreaker or fails > cfg.gate["max_fails"]
    return {"result": FAIL if failed else PASS, "fails": fails, "unknowns": unknowns,
            "dealbreaker": dealbreaker, "rules": rows}


def known_to_fail(cfg, gate):
    """True only if facts that were actually recorded sink the listing. An unknown that
    `on_unknown: fail` counts as a miss does not qualify: nobody has looked yet."""
    misses = [row for row in gate["rules"] if row["status"] == FAIL]
    return any(row["dealbreaker"] for row in misses) or len(misses) > cfg.gate["max_fails"]


def card_check(cfg, values):
    """Reject from the search card only on rules marked `trust_card`, and only when
    that failure alone would sink the listing. Everything else waits for the detail
    page, because cards hide or misstate facts.
    """
    for rule in cfg.gate["rules"]:
        if not (rule["enabled"] and rule.get("trust_card")):
            continue
        if not (rule["dealbreaker"] or cfg.gate["max_fails"] == 0):
            continue
        if evaluate(rule, values, cfg.registry, rule.get("card_margin_pct", 0)) == FAIL:
            return {"rule": rule["id"], "label": rule["label"],
                    "actual": show(rule["field"], values.get(rule["field"])),
                    "expected": expected_text(rule)}
    return None


def fail_text(gate):
    """One line naming each miss, e.g. "rent $4,600 (needs at most $4,000)"."""
    parts = []
    for row in gate["rules"]:
        if row["counted_as"] == FAIL:
            parts.append("%s %s (needs %s)" % (row["label"], row["actual"], row["expected"]))
    return "; ".join(parts)


def unknown_labels(gate):
    return [row["label"] for row in gate["rules"] if row["counted_as"] == UNKNOWN]
