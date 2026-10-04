"""Plan the discovery sweeps and split them when a site caps its results.

Sites stop paging after a few hundred results. Rather than lose the tail, a capped
sweep is cut into two narrower price bands and each half is swept again.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit


def _streeteasy(url, low, high):
    parts = urlsplit(url)
    segments = parts.path.split("/")
    if len(segments) < 3 or segments[1] != "for-rent":
        return None
    band = "price:%d-%d" % (low, high)
    last = unquote(segments[-1])
    if ":" in last:
        filters = [f for f in last.split("|") if f and not f.startswith("price:")]
        segments[-1] = quote("|".join([band] + filters), safe=":,-_")
    else:
        if not segments[-1]:
            segments.pop()
        segments.append(quote(band, safe=":,-_"))
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if k != "page"])
    return urlunsplit((parts.scheme, parts.netloc, "/".join(segments), query, ""))


def _zillow(url, low, high):
    parts = urlsplit(url)
    query = parse_qsl(parts.query)
    state_text = dict(query).get("searchQueryState")
    if not state_text:
        return None
    try:
        state = json.loads(state_text)
    except ValueError:
        return None
    state.pop("pagination", None)
    state.setdefault("filterState", {})["mp"] = {"min": low, "max": high}
    state["filterState"].pop("price", None)
    rebuilt = [(k, json.dumps(state, separators=(",", ":")) if k == "searchQueryState" else v) for k, v in query]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(rebuilt), ""))


def _craigslist(url, low, high):
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k not in ("min_price", "max_price", "s")]
    query += [("min_price", str(low)), ("max_price", str(high))]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


_REWRITERS = {"streeteasy": _streeteasy, "zillow": _zillow, "craigslist": _craigslist}


def band_url(style, base_url, low, high):
    """The search URL narrowed to a price band, or None if it must be set by hand."""
    rewriter = _REWRITERS.get(style)
    return rewriter(base_url, int(low), int(high)) if rewriter and base_url else None


def _base_urls(spec):
    urls = list(spec.get("saved_search_urls") or [])
    if not urls and spec["url_style"] == "craigslist":
        params = urlencode(sorted((spec.get("params") or {}).items()))
        for sub in spec["subareas"]:
            urls.append("https://%s.craigslist.org/search/%s/apa%s" % (spec["site"], sub, "?" + params if params else ""))
    return urls


def _sweep(source, index, base_url, low, high):
    return {"key": "%s:%d:%d-%d" % (source, index, low, high), "source": source, "index": index,
            "base_url": base_url, "min": int(low), "max": int(high), "status": "pending",
            "pages_read": 0, "cards": 0, "total_results": None}


def plan(cfg):
    """One sweep per saved search over the whole price range (or per band_width slice)."""
    low, high = int(cfg.search["price_min"]), int(cfg.search["price_max"])
    sweeps = []
    for source in cfg.enabled_sources():
        spec = cfg.sources[source]
        if spec["url_style"] == "inbox":
            sweep = _sweep(source, 0, None, low, high)
            sweep["key"] = "%s:inbox" % source
            sweeps.append(sweep)
            continue
        width = spec.get("band_width")
        for index, url in enumerate(_base_urls(spec)):
            start = low
            while start <= high:
                end = min(high, start + int(width) - 1) if width else high
                sweeps.append(_sweep(source, index, url, start, end))
                start = end + 1
    return sweeps


def split(sweep, min_width):
    """Two half-width sweeps, or None if the band is already as narrow as allowed."""
    low, high = sweep["min"], sweep["max"]
    if high - low < max(2, min_width):
        return None
    middle = (low + high) // 2
    return [_sweep(sweep["source"], sweep["index"], sweep["base_url"], low, middle),
            _sweep(sweep["source"], sweep["index"], sweep["base_url"], middle + 1, high)]
