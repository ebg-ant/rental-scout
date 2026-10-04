from __future__ import annotations

import copy
import os

from scout import config
from scout._vendor import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLE = os.path.join(ROOT, "configs", "nyc.example.yaml")
TEMPLATE = os.path.join(ROOT, "configs", "nyc.template.yaml")


def raw_example():
    with open(EXAMPLE, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def make_config(change=None):
    """The example config, optionally altered by `change(raw)` before validation."""
    raw = copy.deepcopy(raw_example())
    if change:
        change(raw)
    return config.validate(raw)[0]


def rule(raw, rule_id):
    return next(r for r in raw["gate"]["rules"] if r["id"] == rule_id)


def errors_from(change):
    try:
        make_config(change)
    except config.ScoutError as exc:
        return exc.errors
    return []
