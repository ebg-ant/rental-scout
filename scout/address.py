"""Canonical address keys, so one unit listed on three sites is one record.

A key has three parts: street ("245 e 10 st"), unit ("4B") and locality (zip, or
borough when no zip is known). "245 East 10th Street, Apt. 4-B, New York NY 10003"
and "245 E 10th St #4b" produce the same street and unit.
"""

from __future__ import annotations

import re

from .util import norm_text

_SUFFIX = {
    "street": "st", "str": "st", "avenue": "ave", "av": "ave", "boulevard": "blvd", "place": "pl",
    "road": "rd", "drive": "dr", "court": "ct", "lane": "ln", "terrace": "ter", "parkway": "pkwy",
    "square": "sq", "highway": "hwy", "plaza": "plz", "circle": "cir", "crescent": "cres",
    "concourse": "conc", "turnpike": "tpke", "expressway": "expy", "saint": "st", "fort": "ft",
    "mount": "mt", "heights": "hts", "junior": "jr",
}
_DIRECTION = {"east": "e", "west": "w", "north": "n", "south": "s"}
_ORDINAL_WORDS = {
    "first": "1", "second": "2", "third": "3", "fourth": "4", "fifth": "5", "sixth": "6",
    "seventh": "7", "eighth": "8", "ninth": "9", "tenth": "10", "eleventh": "11", "twelfth": "12",
}
_UNIT_WORD = r"(?:apartment|apt|unit|suite|ste)"
_UNIT_RE = re.compile(r"(?:(?<!\w)%s(?![a-z])\s*#?\s*|#\s*)([a-z0-9][a-z0-9\-/]*)" % _UNIT_WORD)
_FLOOR_RE = re.compile(r"(?<!\w)(?:(\d+)(?:st|nd|rd|th)?\s*(?:floor|fl)|(?:floor|fl)\.?\s*(\d+))(?!\w)")
_ZIP_RE = re.compile(r"(?<![\d-])(\d{5})(?:-\d{4})?(?![\d-])")
_HOUSE_RE = re.compile(r"^\d+(?:-\d+)?[a-z]?$")
_BOROUGHS = {
    "manhattan": "manhattan", "new york": "manhattan", "nyc": None, "brooklyn": "brooklyn",
    "bklyn": "brooklyn", "queens": "queens", "bronx": "bronx", "the bronx": "bronx",
    "staten island": "staten island",
}


def normalize_unit(unit):
    if unit is None:
        return None
    text = re.sub(r"^%s(?![a-z])\.?\s*" % _UNIT_WORD, "", norm_text(unit))
    text = re.sub(r"[^a-z0-9]", "", text).upper().lstrip("0")
    return text or None


def normalize_borough(text):
    key = norm_text(text)
    if not key:
        return None
    return _BOROUGHS.get(key, key)


def _street_tokens(text):
    tokens = []
    for token in re.sub(r"[^a-z0-9\- ]", " ", text).split():
        token = _ORDINAL_WORDS.get(token, token)
        match = re.match(r"^(\d+)(?:st|nd|rd|th)$", token)
        if match:
            token = match.group(1)
        token = _DIRECTION.get(token, token)
        tokens.append(_SUFFIX.get(token, token))
    return tokens


_SUFFIX_TOKENS = set(_SUFFIX.values()) - set(["ft", "mt", "jr", "hts"])
_BARE_UNIT_RE = re.compile(r"^(?:ph[a-z0-9]*|\d+[a-z]?|[a-z]\d+)$")


def _split_trailing_unit(tokens):
    """"1 5 ave ph" -> unit PH. Leaves "100 ave a" and "10 park ave s" alone."""
    if len(tokens) >= 4 and tokens[-2] in _SUFFIX_TOKENS and _BARE_UNIT_RE.match(tokens[-1]):
        return tokens[:-1], tokens[-1]
    return tokens, None


def _apply_street_aliases(text, aliases):
    for other, canonical in (aliases or {}).items():
        pattern = r"(?<!\w)" + r"\s+".join(re.escape(w) for w in norm_text(other).split()) + r"(?!\w)"
        text = re.sub(pattern, norm_text(canonical), text)
    return text


def parse(raw, unit=None, zip_code=None, borough=None, street_aliases=None):
    """Split a free-text address. Returns None when there is no street number.

    An address without a street number ("E 10th St near Ave A") is treated as not
    disclosed: it cannot be matched across sites or checked on a map.
    """
    text = norm_text(raw)
    if not text:
        return None
    text = text.replace(".", " ")

    found_zip = None
    zips = list(_ZIP_RE.finditer(text))
    if zips and zips[-1].start() > 0:
        found_zip = zips[-1].group(1)
        text = text[:zips[-1].start()] + " " + text[zips[-1].end():]

    found_unit = None
    match = _UNIT_RE.search(text)
    if match:
        found_unit = match.group(1)
        text = text[:match.start()] + " , " + text[match.end():]
    else:
        match = _FLOOR_RE.search(text)
        if match:
            found_unit = "FL" + (match.group(1) or match.group(2))
            text = text[:match.start()] + " , " + text[match.end():]

    parts = [part.strip() for part in text.split(",") if part.strip()]
    if not parts:
        return None
    found_borough = None
    for part in parts[1:]:
        cleaned = re.sub(r"\b(?:ny|new york state|usa|united states)\b", " ", part).strip()
        for candidate in (part, cleaned):
            if candidate in _BOROUGHS and _BOROUGHS[candidate]:
                found_borough = _BOROUGHS[candidate]

    tokens = _street_tokens(_apply_street_aliases(parts[0], street_aliases))
    if len(tokens) < 2 or not _HOUSE_RE.match(tokens[0]):
        return None
    if not found_unit:
        tokens, found_unit = _split_trailing_unit(tokens)
    house = tokens[0].replace("-", "")  # Queens: 30-15 and 3015 are the same door
    street = " ".join([house] + tokens[1:])

    return {
        "street": street,
        "unit": normalize_unit(unit) or normalize_unit(found_unit),
        "zip": str(zip_code).strip()[:5] if zip_code else found_zip,
        "borough": normalize_borough(borough) or found_borough,
    }


def localities_conflict(first, second):
    """True only when both sides state a locality and they differ."""
    if first.get("zip") and second.get("zip"):
        return first["zip"] != second["zip"]
    if first.get("borough") and second.get("borough"):
        return first["borough"] != second["borough"]
    return False


def match(first, second):
    """Compare two parsed addresses: 'same', 'maybe' or 'different'.

    'same' needs the same door and the same stated unit. The same door with a unit
    missing on either side is only 'maybe': big buildings list many similar units.
    """
    if not first or not second or first["street"] != second["street"]:
        return "different"
    if localities_conflict(first, second):
        return "different"
    if first.get("unit") and second.get("unit"):
        return "same" if first["unit"] == second["unit"] else "different"
    return "maybe"


def display_key(parsed):
    if not parsed:
        return None
    return "|".join([parsed["street"], parsed.get("unit") or "?",
                     parsed.get("zip") or parsed.get("borough") or "?"])
