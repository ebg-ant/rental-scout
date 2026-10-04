"""Cheap text rules that reject noise before any browser time is spent."""

from __future__ import annotations

from .config import compile_phrase
from .util import norm_text


def _excerpt(text, start, end, pad=40):
    left, right = max(0, start - pad), min(len(text), end + pad)
    return ("..." if left else "") + text[left:right] + ("..." if right < len(text) else "")


def run(cfg, *texts):
    """Return {"result": "pass"} or a reject with the rule, phrase and excerpt."""
    text = norm_text(" \n ".join(t for t in texts if t))
    if not text:
        return {"result": "pass"}
    for rule in cfg.prefilters:
        if not rule.get("enabled", True):
            continue
        if any(compile_phrase(p).search(text) for p in rule.get("unless_any") or []):
            continue
        for phrase in rule["reject_if_any"]:
            found = compile_phrase(phrase).search(text)
            if found:
                return {"result": "reject", "rule": rule["id"], "reason": rule["reason"],
                        "matched": found.group(0), "excerpt": _excerpt(text, found.start(), found.end())}
    return {"result": "pass"}
