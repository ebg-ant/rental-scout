"""Small shared helpers: clock, text normalisation, JSON input, formatting."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import sys


class ScoutError(Exception):
    """A problem the caller can fix. `errors` is a list of plain-English lines."""

    def __init__(self, errors, hint=None):
        if isinstance(errors, str):
            errors = [errors]
        self.errors = list(errors)
        self.hint = hint
        super().__init__("; ".join(self.errors))


def now():
    """Current UTC time. SCOUT_NOW (ISO 8601) overrides it so tests are repeatable."""
    forced = os.environ.get("SCOUT_NOW")
    if forced:
        return parse_time(forced)
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def now_iso():
    return now().isoformat().replace("+00:00", "Z")


def today_iso():
    return now().date().isoformat()


def parse_time(value):
    parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def seconds_between(earlier_iso, later_iso):
    return (parse_time(later_iso) - parse_time(earlier_iso)).total_seconds()


_QUOTES = {"‘": "'", "’": "'", "“": '"', "”": '"',
           "–": "-", "—": "-", " ": " "}


def norm_text(text):
    """Lowercase, straighten quotes and dashes, collapse whitespace.

    Used wherever two pieces of text are compared, so that a quote copied out of
    a web page still matches the saved page text.
    """
    if not text:
        return ""
    text = str(text)
    for fancy, plain in _QUOTES.items():
        text = text.replace(fancy, plain)
    return re.sub(r"\s+", " ", text).strip().lower()


def read_json_input(path):
    """Load JSON from a file path, or from stdin when path is '-'."""
    try:
        if path == "-":
            return json.load(sys.stdin)
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        raise ScoutError("No such file: %s" % path)
    except json.JSONDecodeError as exc:
        raise ScoutError("%s is not valid JSON: %s" % ("stdin" if path == "-" else path, exc))


def stable_hash(value, length=12):
    blob = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:length]


def money(value):
    if value is None:
        return "unknown"
    return "${:,.0f}".format(round(value))


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def median(values):
    ordered = sorted(values)
    if not ordered:
        return None
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def plural(count, noun, nouns=None):
    return "%d %s" % (count, noun if count == 1 else (nouns or noun + "s"))
