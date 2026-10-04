"""The run pipeline: discover, dedupe, prefilter, enrich, gate, score, verdict.

Progress is stored on each listing (`stage`) and on the open run, and saved after
every command. The agent only ever asks `next` what to do, so a dropped browser
session resumes exactly where it stopped.
"""

from __future__ import annotations

import re
from urllib.parse import quote_plus, urlsplit

from . import address, derive, flags, gate, prefilter, rubric, sweeps, verdict
from .fields import base_inputs, check_value
from .util import (ScoutError, is_number, median, norm_text, now_iso, parse_time, seconds_between,
                   today_iso)

NEEDS_ENRICH, NEEDS_SCORE, DONE = "needs_enrich", "needs_score", "done"
USER_STATUSES = ("active", "toured", "applied", "rejected")
STALE_RUN_HOURS = 12
MAX_PAGE_TEXT = 20000
MAX_IMAGES = 40
CARD_KEYS = ("title", "snippet", "rent", "beds", "baths", "sqft", "address", "unit", "neighborhood",
             "borough", "zip", "first_photo", "lat", "lng", "listed_date")


# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------

def origin_source(cfg, url, declared):
    """Which site a URL belongs to, so an email alert for a StreetEasy listing
    dedupes against the same listing found by sweeping StreetEasy."""
    host = urlsplit(url or "").netloc.lower()
    for source in cfg.sources:
        if source.replace("_", "") in host.replace("-", ""):
            return source
    return declared


def derive_source_id(url):
    parts = urlsplit(url or "")
    path = parts.path.rstrip("/")
    for pattern in (r"(\d+)_zpid", r"/(\d{8,})\.html$", r"/rental/(\d+)$", r"/building/(.+)$"):
        found = re.search(pattern, path)
        if found:
            return found.group(1)
    return (parts.netloc + path) or None


def _record_id(source, source_id):
    return "%s-%s" % (source, re.sub(r"[^a-z0-9]+", "-", str(source_id).lower()).strip("-"))


def _address_key(cfg, raw, unit=None, zip_code=None, borough=None):
    return address.parse(raw, unit, zip_code, borough, cfg.search.get("street_aliases"))


def _source_entry(rec, source, source_id):
    for entry in rec["sources"]:
        if entry["source"] == source and str(entry["source_id"]) == str(source_id):
            return entry
    return None


def _event(rec, event, detail=None):
    rec["history"].append({"at": now_iso(), "event": event, "detail": detail})


def _close_rent(first, second, pct=5):
    if not (is_number(first) and is_number(second)):
        return True
    return abs(first - second) <= max(first, second) * pct / 100.0


def find_match(store, source, source_id, key, card):
    """Return (record, how) for a card already in the store, else (None, None).

    how: 'source' same site and id; 'address' same door and unit on any site;
    'repost' same site, new id, same photo or title at the same rent.
    """
    for rec in store.listings.values():
        if _source_entry(rec, source, source_id):
            return rec, "source"
    for rec in store.listings.values():
        if address.match(key, rec.get("address_key")) == "same":
            return rec, "address"
    title = norm_text(card.get("title"))
    for rec in store.listings.values():
        if not any(entry["source"] == source for entry in rec["sources"]):
            continue
        old = rec["card"]
        if not is_number(card.get("rent")) or card.get("rent") != old.get("rent"):
            continue
        # A stated, different address beats a look-alike photo or title.
        if key and rec.get("address_key") and address.match(key, rec["address_key"]) == "different":
            continue
        if card.get("first_photo") and card["first_photo"] == old.get("first_photo"):
            return rec, "repost"
        if len(title) >= 15 and title == norm_text(old.get("title")) and card.get("beds") == old.get("beds"):
            return rec, "repost"
    return None, None


def _link_possible_duplicates(store, rec):
    """Same door, unit missing on one side, similar size and rent: link, never merge."""
    for other in store.listings.values():
        if other["id"] == rec["id"] or address.match(rec.get("address_key"), other.get("address_key")) != "maybe":
            continue
        beds = (rec["card"].get("beds"), other["card"].get("beds"))
        if None not in beds and beds[0] != beds[1]:
            continue
        if not _close_rent(rec["card"].get("rent"), other["card"].get("rent")):
            continue
        for one, two in ((rec, other), (other, rec)):
            if two["id"] not in one["possible_duplicates"]:
                one["possible_duplicates"].append(two["id"])


# --------------------------------------------------------------------------
# Judging: everything deterministic about one listing, recomputed from scratch
# --------------------------------------------------------------------------

def _comps(store):
    def lookup(beds):
        rents = []
        for rec in store.listings.values():
            if rec.get("verdict") == verdict.PASS_RISK or rec.get("delisted_at"):
                continue
            facts = rec.get("facts") or {}
            if (facts.get("beds") if facts.get("beds") is not None else rec["card"].get("beds")) != beds:
                continue
            rent = (rec.get("derived") or {}).get("rent_net_effective") or rec["card"].get("rent")
            if is_number(rent):
                rents.append(rent)
        return median(rents), len(rents)
    return lookup


def _card_values(cfg, rec):
    card = rec["card"]
    facts = {"rent_advertised": card.get("rent"), "beds": card.get("beds"), "baths": card.get("baths"),
             "sqft": card.get("sqft"), "neighborhood": card.get("neighborhood"), "zip": card.get("zip"),
             "borough": card.get("borough")}
    return derive.values_for(facts, rec.get("address_key"), cfg)


def criteria_to_score(cfg, rec):
    """Criterion ids with no current score: never scored, or their wording changed."""
    scores = (rec.get("rubric") or {}).get("scores") or {}
    hashes = cfg.criterion_hashes()
    return [c["id"] for c in cfg.rubric
            if c["id"] not in scores or scores[c["id"]].get("hash") != hashes[c["id"]]]


def judge(cfg, store, rec):
    """Recompute prefilter, gate, flags, totals, verdict and stage for one listing."""
    card = rec["card"]
    enriched = rec.get("facts") is not None
    # Prefilters read the search card only. Once the page has been read, recorded
    # facts decide, so "furnished roof deck" in a description cannot sink a listing.
    rec["prefilter"] = {"result": "pass"} if enriched else prefilter.run(cfg, card.get("title"), card.get("snippet"))
    rec["card_reject"] = None
    totals = None
    if enriched:
        values = derive.values_for(rec["facts"], rec.get("address_key"), cfg)
        rec["derived"] = derive.derive(rec["facts"], rec.get("address_key"), cfg)
        rec["gate"] = gate.run(cfg, values)
        rec["flags"] = flags.run(cfg, rec, values, _comps(store))
    else:
        rec["derived"], rec["gate"], rec["flags"] = None, None, []
        if rec["prefilter"]["result"] == "pass":
            rec["card_reject"] = gate.card_check(cfg, _card_values(cfg, rec))

    scores = (rec.get("rubric") or {}).get("scores") or {}
    pending = criteria_to_score(cfg, rec)
    known = set(c["id"] for c in cfg.rubric)
    for cid, entry in scores.items():
        entry["stale"] = cid in pending or cid not in known
    if scores:
        totals = rubric.totals(cfg, scores)
        rec["rubric"].update(totals)

    new_verdict, reason = verdict.decide(cfg, rec["prefilter"], rec["card_reject"], rec["gate"],
                                         rec["flags"], scores, totals)
    survives = enriched and rec["gate"]["result"] == gate.PASS and not flags.risk_verdict(cfg, rec["flags"])
    if rec.get("refresh") or (new_verdict is None and not enriched):
        rec["stage"] = NEEDS_ENRICH
    elif survives and pending:
        rec["stage"] = NEEDS_SCORE
    else:
        rec["stage"] = DONE

    old = rec.get("verdict")
    if new_verdict != old and new_verdict is not None:
        if old is not None:
            _event(rec, "verdict", "%s -> %s" % (old, new_verdict))
        rec["verdict_changed_at"] = now_iso()
        if store.run:
            rec["verdict_changed_run"] = store.run["id"]
    rec["verdict"], rec["verdict_reason"] = new_verdict, reason  # None means not judged yet
    rec["judged_at"] = now_iso()
    return rec


def rejudge_all(cfg, store):
    """Apply a changed config to every stored listing. No browser needed."""
    changed, requeued = [], 0
    for rec in store.listings.values():
        before, stage_before = rec.get("verdict"), rec.get("stage")
        judge(cfg, store, rec)
        if rec.get("verdict") != before:
            changed.append({"id": rec["id"], "from": before, "to": rec.get("verdict")})
        if rec["stage"] != DONE and stage_before == DONE:
            requeued += 1
            rec["queue_reason"] = "config_changed"
    store.data["judge_hash"] = _full_hash(cfg)
    return {"listings": len(store.listings), "verdicts_changed": changed, "requeued": requeued}


def _full_hash(cfg):
    return cfg.judge_hash() + ":" + ",".join(sorted(cfg.criterion_hashes().values()))


# --------------------------------------------------------------------------
# Run lifecycle
# --------------------------------------------------------------------------

def _require_run(store):
    if not store.run:
        raise ScoutError("No run is open", hint="Start one with `python3 -m scout start`.")
    return store.run


def start(cfg, store, sweep=True):
    """Open a run, or resume the one left open by a dropped session.

    sweep=False opens a run with no discovery, for judging hand-picked listings.
    """
    result = {}
    run = store.run
    if run and seconds_between(run["started_at"], now_iso()) > STALE_RUN_HOURS * 3600:
        result["closed_stale_run"] = run["id"]
        _close_run(cfg, store, abandoned=True)
        run = None
    if store.data.get("judge_hash") != _full_hash(cfg) and store.listings:
        result["config_changed"] = rejudge_all(cfg, store)
    store.data["judge_hash"] = _full_hash(cfg)
    if run:
        run["resumed_at"] = now_iso()
        run["resumes"] = run.get("resumes", 0) + 1
        result.update({"run": run["id"], "resumed": True})
        return result
    result["backup"] = store.backup()
    store.data["run"] = {
        "id": "run-" + parse_time(now_iso()).strftime("%Y%m%d-%H%M%S"),
        "started_at": now_iso(), "resumed_at": now_iso(), "resumes": 0,
        "sweeps": sweeps.plan(cfg) if sweep else [], "blocked": {}, "last_page_at": {},
        "evaluated": 0, "scored": 0, "touched": [], "notes": [],
        "counts": {"cards": 0, "new": 0, "unchanged": 0, "price_changed": 0, "relisted": 0,
                   "merged": 0, "prefiltered": 0, "card_rejected": 0},
    }
    result.update({"run": store.run["id"], "resumed": False, "sweeps_planned": len(store.run["sweeps"])})
    return result


def _touch(store, rec):
    run = store.run
    if run and rec["id"] not in run["touched"]:
        run["touched"].append(rec["id"])


def _usable_sources(store, rec):
    blocked = (store.run or {}).get("blocked", {})
    return [entry for entry in rec["sources"] if entry["active"] and entry["source"] not in blocked]


def _queue(cfg, store, stage):
    """Listings waiting at a stage, best use of a browser slot first."""
    order = list(cfg.sources)
    waiting = []
    for rec in store.listings.values():
        if rec["stage"] != stage or rec["status"] == "rejected" or rec.get("delisted_at"):
            continue
        if not _usable_sources(store, rec):
            continue
        shortlisted = rec.get("verdict") in (verdict.TOUR, verdict.WATCH)
        waiting.append(((0 if shortlisted else 1, 0 if rec.get("address_key") else 1, rec["seq"]), rec))
    waiting.sort(key=lambda pair: pair[0])
    for _, rec in waiting:
        rec["sources"].sort(key=lambda e: order.index(e["source"]) if e["source"] in order else len(order))
    return [rec for _, rec in waiting]


def helper_links(cfg, rec):
    addr = rec.get("address") or {}
    anchor = (cfg.search.get("commute_anchor") or {}).get("address")
    raw = addr.get("raw")
    if raw and not re.search(r"\d{5}|,", raw):
        raw = "%s, %s" % (raw, cfg.search["city"])
    values = {"address": raw, "anchor": anchor, "lat": addr.get("lat"), "lng": addr.get("lng"),
              "zip": (rec.get("address_key") or {}).get("zip")}
    links = []
    for link in cfg.search.get("helper_links") or []:
        needed = re.findall(r"\{(\w+)\}", link["url"])
        if any(values.get(name) in (None, "") for name in needed):
            continue
        url = link["url"]
        for name in needed:
            url = url.replace("{%s}" % name, quote_plus(str(values[name])))
        links.append({"id": link["id"], "label": link.get("label", link["id"]), "note": link.get("note"), "url": url})
    return links


def required_fields(cfg):
    """Facts every enrichment must either record or explicitly mark unknown."""
    names = []
    for rule in cfg.gate["rules"]:
        if rule["enabled"]:
            names += base_inputs(rule["field"], cfg.registry)
            if "unless" in rule:
                names += base_inputs(rule["unless"]["field"], cfg.registry)
    names += list(cfg.capture)
    ordered = []
    for name in names:
        if name not in ordered:
            ordered.append(name)
    return ordered


def _wait_seconds(cfg, store, source):
    pause = cfg.sources.get(source, {}).get("throttle_seconds", cfg.limits["throttle_seconds"])
    last = store.run["last_page_at"].get(source)
    if not last:
        return 0
    return max(0, int(round(pause - seconds_between(last, now_iso()))))


def _budget(cfg, store):
    run = store.run
    used = seconds_between(run["resumed_at"], now_iso()) / 60.0
    return {"listings_opened": run["evaluated"], "listings_max": cfg.limits["max_listings_per_run"],
            "minutes_used": round(used, 1), "minutes_max": cfg.limits["max_runtime_minutes"]}


def _queue_rechecks(cfg, store):
    """Shortlisted listings no sweep has seen for a while get their page re-opened,
    so the shortlist does not fill up with homes that are already gone."""
    limit = cfg.limits["recheck_after_days"] * 86400
    for rec in store.listings.values():
        if rec.get("verdict") in (verdict.TOUR, verdict.WATCH) and rec["stage"] == DONE \
                and not rec.get("delisted_at") and rec["status"] != "rejected" \
                and seconds_between(max(rec["last_seen"], rec.get("enriched_at") or ""), now_iso()) >= limit:
            rec["refresh"] = "recheck"
            rec["stage"] = NEEDS_ENRICH


def next_task(cfg, store):
    """The single question the agent asks: what should I do now?"""
    run = store.run
    if not run:
        return {"task": "start", "do": "Run `python3 -m scout start`."}
    budget = _budget(cfg, store)
    out_of_time = budget["minutes_used"] >= budget["minutes_max"]

    to_score = _queue(cfg, store, NEEDS_SCORE)
    if to_score and not out_of_time:
        rec = to_score[0]
        pending = criteria_to_score(cfg, rec)
        return {
            "task": "score", "id": rec["id"], "read": "agent/RUBRIC_SCORING.md",
            "urls": [entry["url"] for entry in _usable_sources(store, rec)],
            "address": (rec.get("address") or {}).get("raw"),
            "address_disclosed": bool(rec.get("address_key")),
            "facts": rec["facts"], "flags": [f["message"] for f in rec["flags"]],
            "photos_saved": len(rec.get("photos") or []), "floor_plans_saved": len(rec.get("floor_plans") or []),
            "criteria": [{"id": c["id"], "label": c["label"], "guidance": c["guidance"], "anchors": c["anchors"],
                          "evidence_required": c["evidence_required"]} for c in cfg.rubric if c["id"] in pending],
            "helper_links": helper_links(cfg, rec),
            "submit": "python3 -m scout score <file.json>", "budget": budget,
        }

    if not out_of_time:
        for sweep in run["sweeps"]:
            if sweep["status"] != "pending" or sweep["source"] in run["blocked"]:
                continue
            spec = cfg.sources[sweep["source"]]
            task = {"task": "sweep", "sweep": sweep["key"], "source": sweep["source"],
                    "read": spec.get("instructions") or "agent/sources/%s.md" % sweep["source"],
                    "submit": "python3 -m scout ingest <file.json>", "budget": budget}
            if spec["url_style"] == "inbox":
                task["do"] = "Read new saved-search alert emails and turn each listing into a card."
                return task
            url = sweeps.band_url(spec["url_style"], sweep["base_url"], sweep["min"], sweep["max"])
            task.update({
                "url": url or sweep["base_url"], "price_min": sweep["min"], "price_max": sweep["max"],
                "price_filter": "in the URL; confirm the page shows it" if url else
                                "NOT in the URL; set the price filter on the page by hand",
                "resume_after_page": sweep["pages_read"], "max_pages": spec.get("max_pages"),
                "wait_seconds": _wait_seconds(cfg, store, sweep["source"]),
                "wait_between_pages_seconds": spec.get("throttle_seconds", cfg.limits["throttle_seconds"]),
            })
            run["last_page_at"][sweep["source"]] = now_iso()
            return task

    if run["sweeps"] and not run.get("rechecks_queued"):
        run["rechecks_queued"] = True
        _queue_rechecks(cfg, store)
    to_enrich = _queue(cfg, store, NEEDS_ENRICH)
    if to_enrich and not out_of_time and run["evaluated"] < cfg.limits["max_listings_per_run"]:
        rec = to_enrich[0]
        entry = _usable_sources(store, rec)[0]
        task = {
            "task": "enrich", "id": rec["id"], "why": rec.get("refresh") or rec.get("queue_reason") or "new",
            "read": "agent/ENRICH.md", "source": entry["source"], "url": entry["url"],
            "other_urls": [e["url"] for e in _usable_sources(store, rec)[1:]],
            "card": rec["card"],
            "capture": [{"field": name, "type": cfg.registry[name].describe(), "what": cfg.registry[name].help}
                        for name in required_fields(cfg)],
            "commute_anchor": cfg.search.get("commute_anchor"),
            "helper_links": helper_links(cfg, rec),
            "wait_seconds": _wait_seconds(cfg, store, entry["source"]),
            "submit": "python3 -m scout enrich <file.json>", "budget": budget,
        }
        if rec.get("facts"):
            task["previous_facts"] = rec["facts"]
        run["last_page_at"][entry["source"]] = now_iso()
        return task

    why = "time limit reached" if out_of_time else \
        "listing limit reached; %d left for next run" % len(to_enrich) if to_enrich else "nothing left to do"
    return {"task": "finish", "why": why, "do": "Run `python3 -m scout finish`.", "budget": budget}


# --------------------------------------------------------------------------
# Discover + dedupe + diff + prefilter
# --------------------------------------------------------------------------

def _clean_card(raw, errors, where):
    card = {}
    for key in CARD_KEYS:
        if raw.get(key) not in (None, ""):
            card[key] = raw[key]
    for key in ("rent", "beds", "baths", "sqft", "lat", "lng"):
        if key in card and not is_number(card[key]):
            errors.append("%s.%s must be a plain number (no $ or commas), got %r" % (where, key, card[key]))
    if "zip" in card:
        card["zip"] = str(card["zip"])
    return card


def _new_record(store, rec_id, card, key):
    return {
        "id": rec_id, "seq": store.next_seq(), "card": card, "address_key": key,
        "address": {"raw": card["address"], "lat": card.get("lat"), "lng": card.get("lng")}
        if card.get("address") else None,
        "sources": [], "price_history": [], "history": [], "possible_duplicates": [],
        "facts": None, "provenance": {}, "unknown": {}, "raw_text": None, "rubric": None,
        "photos": [], "floor_plans": [],
        "prefilter": None, "card_reject": None, "gate": None, "flags": [], "derived": None,
        "verdict": None, "verdict_reason": None, "stage": NEEDS_ENRICH, "queue_reason": "new", "refresh": None,
        "status": "active", "delisted_at": None, "feedback": [],
        "first_seen": now_iso(), "last_seen": now_iso(),
        "first_seen_run": store.run["id"] if store.run else None,
    }


def _record_price(rec, source, rent):
    """Append to the price history when this site's rent differs from its last one."""
    if not is_number(rent):
        return None
    previous = [p for p in rec["price_history"] if p["source"] == source]
    if previous and previous[-1]["rent"] == rent:
        return None
    rec["price_history"].append({"date": today_iso(), "source": source, "rent": rent})
    return previous[-1]["rent"] if previous else None


def _ingest_card(cfg, store, declared_source, raw, index, errors, counts):
    where = "cards[%d]" % index
    if not isinstance(raw, dict) or not raw.get("url"):
        errors.append("%s: needs a url" % where)
        return
    source = origin_source(cfg, raw["url"], raw.get("source") or declared_source)
    source_id = raw.get("source_id") or derive_source_id(raw["url"])
    before = len(errors)
    card = _clean_card(raw, errors, where)
    if len(errors) > before:
        return
    key = _address_key(cfg, card.get("address"), card.get("unit"), card.get("zip"), card.get("borough"))
    rec, how = find_match(store, source, source_id, key, card)
    counts["cards"] += 1

    if rec is None:
        rec = _new_record(store, _record_id(source, source_id), card, key)
        if rec["id"] in store.listings:  # two ids that sanitise alike
            rec["id"] = "%s-%d" % (rec["id"], rec["seq"])
        store.listings[rec["id"]] = rec
        how = "new"

    entry = _source_entry(rec, source, source_id)
    if entry is None:
        entry = {"source": source, "source_id": str(source_id), "url": raw["url"], "rent": card.get("rent"),
                 "first_seen": now_iso(), "last_seen": now_iso(), "active": True, "missed_sweeps": 0}
        rec["sources"].append(entry)
        if how in ("address", "repost"):
            _event(rec, "also_listed" if how == "address" else "reposted", "%s %s" % (source, raw["url"]))
    was_inactive = not entry["active"]
    entry.update({"last_seen": now_iso(), "active": True, "missed_sweeps": 0, "url": raw["url"],
                  "seen_in_run": store.run["id"] if store.run else None})
    rec["last_seen"] = now_iso()

    old_rent = _record_price(rec, source, card.get("rent"))
    if is_number(card.get("rent")):
        entry["rent"] = card["rent"]
    merged_card = dict(rec["card"])
    merged_card.update(card if how in ("source", "new", "repost") else
                       dict((k, v) for k, v in card.items() if k not in merged_card))
    rec["card"] = merged_card
    if key and not rec.get("address_key"):
        rec["address_key"] = key
        rec["address"] = {"raw": card["address"], "lat": card.get("lat"), "lng": card.get("lng")}

    change = None
    if how == "new":
        counts["new"] += 1
    elif rec.get("delisted_at") or (was_inactive and how == "source"):
        change = "relisted"
        rec["delisted_at"] = None
        if rec["status"] == "delisted":
            rec["status"] = "active"
        _event(rec, "relisted", source)
    elif old_rent is not None:
        change = "price_changed"
        _event(rec, "price", "%s: %s -> %s" % (source, old_rent, card["rent"]))
    elif how in ("address", "repost"):
        counts["merged"] += 1
    else:
        counts["unchanged"] += 1

    if change:
        counts[change] += 1
        rec["last_change"] = {"what": change, "at": now_iso(), "run": store.run["id"] if store.run else None,
                              "from": old_rent, "to": card.get("rent")}
        enriched_here = (rec.get("enriched_from") or {}).get("source") == source
        if rec.get("facts") is not None and old_rent is not None and enriched_here:
            rec["facts"]["rent_advertised"] = card["rent"]
            rec["provenance"]["rent_advertised"] = {"where": "search card on %s" % today_iso()}

    _link_possible_duplicates(store, rec)
    judge(cfg, store, rec)
    # A changed listing earns a fresh look at the page only if it is still in the
    # running. One that fails on pets at any price does not need a browser slot.
    if change and rec.get("facts") is not None and rec["status"] != "rejected" \
            and rec["verdict"] not in (verdict.PASS, verdict.PASS_RISK):
        rec["refresh"] = change
        judge(cfg, store, rec)
    if how == "new" or change:
        _touch(store, rec)
        if how == "new" and rec["prefilter"]["result"] == "reject":
            counts["prefiltered"] += 1
        elif how == "new" and rec["card_reject"]:
            counts["card_rejected"] += 1


def ingest(cfg, store, payload):
    """Take the cards from one sweep (or one page of it) and work out what is new."""
    run = _require_run(store)
    if not isinstance(payload, dict) or not isinstance(payload.get("cards"), list):
        raise ScoutError("Expected an object with a `cards` list")
    sweep = None
    if payload.get("sweep"):
        sweep = next((s for s in run["sweeps"] if s["key"] == payload["sweep"]), None)
        if sweep is None:
            raise ScoutError("No sweep called %r in this run" % payload["sweep"],
                             hint="Use the `sweep` value from `python3 -m scout next`.")
    declared = sweep["source"] if sweep else payload.get("source") or "manual"
    if declared != "manual" and declared not in cfg.sources:
        raise ScoutError("Unknown source %r; expected one of %s or manual" % (declared, ", ".join(cfg.sources)))

    errors = []
    counts = dict((name, 0) for name in run["counts"])
    for index, raw in enumerate(payload["cards"]):
        _ingest_card(cfg, store, declared, raw, index, errors, counts)
    if errors:
        raise ScoutError(errors, hint="Nothing was saved. Fix the cards and send the file again.")
    for name, value in counts.items():
        run["counts"][name] += value

    result = {"counts": counts}
    if sweep:
        sweep["cards"] += len(payload["cards"])
        sweep["pages_read"] = max(sweep["pages_read"], int(payload.get("pages_read") or 0))
        if is_number(payload.get("total_results")):
            sweep["total_results"] = payload["total_results"]
        if payload.get("capped"):
            halves = sweeps.split(sweep, cfg.limits["min_band_width"])
            if halves:
                sweep["status"] = "split"
                position = run["sweeps"].index(sweep)
                run["sweeps"][position + 1:position + 1] = halves
                result["split_into"] = [half["key"] for half in halves]
            else:
                sweep["status"] = "capped"
                run["notes"].append("%s hit the site's result cap and the band is too narrow to split; "
                                    "some listings may be missed" % sweep["key"])
        elif payload.get("stopped_early"):
            # Finished for today, but older results were not re-read, so this sweep
            # says nothing about whether an unseen listing is still up.
            sweep["status"] = "partial"
        elif payload.get("complete", True):
            sweep["status"] = "done"
        result["sweep_status"] = sweep["status"]
    result["waiting_to_open"] = len(_queue(cfg, store, NEEDS_ENRICH))
    return result


# --------------------------------------------------------------------------
# Enrich + gate
# --------------------------------------------------------------------------

def _get(store, rec_id):
    rec = store.listings.get(str(rec_id))
    if rec is None:
        raise ScoutError("No listing with id %r" % rec_id, hint="`python3 -m scout find <text>` searches by address.")
    return rec


def _mark_delisted(rec, why):
    for entry in rec["sources"]:
        entry["active"] = False
    rec["delisted_at"] = now_iso()
    if rec["status"] == "active":
        rec["status"] = "delisted"
    rec["refresh"] = None
    if rec["stage"] != DONE:
        rec["stage"] = DONE
    _event(rec, "delisted", why)


def _merge_into(store, keeper, dupe):
    """Fold `dupe` into `keeper` once their addresses prove they are one unit."""
    for entry in dupe["sources"]:
        if not _source_entry(keeper, entry["source"], entry["source_id"]):
            keeper["sources"].append(entry)
    keeper["price_history"] = sorted(keeper["price_history"] + dupe["price_history"], key=lambda p: p["date"])
    keeper["first_seen"] = min(keeper["first_seen"], dupe["first_seen"])
    keeper["last_seen"] = max(keeper["last_seen"], dupe["last_seen"])
    keeper["feedback"] += dupe["feedback"]
    for images in ("photos", "floor_plans"):
        if not keeper.get(images):
            keeper[images] = dupe.get(images) or []
    if keeper["status"] in ("active", "delisted") and dupe["status"] not in ("active", "delisted"):
        keeper["status"] = dupe["status"]
    _event(keeper, "merged", "%s is the same unit" % dupe["id"])
    del store.listings[dupe["id"]]
    for rec in store.listings.values():
        if dupe["id"] in rec["possible_duplicates"]:
            rec["possible_duplicates"].remove(dupe["id"])
    if keeper["id"] in keeper["possible_duplicates"]:
        keeper["possible_duplicates"].remove(keeper["id"])
    run = store.run
    if run and dupe["id"] in run["touched"]:
        run["touched"].remove(dupe["id"])


def _check_facts(cfg, rec, payload):
    errors = []
    facts, unknown = payload.get("facts"), payload.get("unknown") or {}
    provenance = payload.get("provenance") or {}
    if not isinstance(facts, dict):
        return ["`facts` must be an object"], {}, {}, {}
    if not isinstance(unknown, dict) or not isinstance(provenance, dict):
        return ["`unknown` and `provenance` must be objects keyed by field name"], {}, {}, {}
    page_text = payload.get("raw_text")
    if not isinstance(page_text, str) or len(page_text.strip()) < 20:
        errors.append("raw_text: paste the listing's full description and amenities text, word for word")
        page_text = ""

    clean = {}
    for name, value in facts.items():
        field = cfg.registry.get(name)
        if field is None:
            errors.append("facts.%s: not a known field. `python3 -m scout fields` lists them" % name)
        elif field.derived_from is not None:
            errors.append("facts.%s: this is calculated for you; leave it out" % name)
        else:
            problem = check_value("facts." + name, field, value)
            if problem:
                errors.append(problem)
            elif value is not None:
                clean[name] = value

    needed = required_fields(cfg)
    for name in needed:
        if name in clean and name in unknown:
            errors.append("%s: has a value and is also listed as unknown; pick one" % name)
        elif name not in clean and len(str(unknown.get(name) or "").strip()) < 5:
            errors.append("%s: record it in `facts`, or list it in `unknown` with where you looked" % name)

    sources = {}
    card = rec["card"]
    for name, value in clean.items():
        entry = provenance.get(name)
        if isinstance(entry, str):
            entry = {"where": entry}
        if not isinstance(entry, dict) or not str(entry.get("where") or "").strip():
            if name in needed:
                errors.append("provenance.%s: say where you saw it, e.g. "
                              '{"where": "amenities list", "quote": "Cats allowed"}' % name)
            continue
        if entry.get("quote") and not rubric.quote_found(entry["quote"], page_text, card.get("title"),
                                                         card.get("snippet")):
            errors.append('provenance.%s: the quote "%s" is not in raw_text. Quote exactly, or drop the quote and '
                          "describe where you saw it" % (name, entry["quote"]))
            continue
        sources[name] = dict((k, entry[k]) for k in ("where", "quote", "url") if entry.get(k))
    reasons = dict((name, str(why)) for name, why in unknown.items() if name not in clean)
    return errors, clean, sources, reasons


def _check_images(items, where, errors):
    """Normalise a list of image URLs (plain, or {"url", "caption"}) for the report."""
    clean = []
    if items is None:
        return clean
    if not isinstance(items, list):
        errors.append("%s: must be a list of image URLs" % where)
        return clean
    for index, item in enumerate(items[:MAX_IMAGES]):
        if isinstance(item, str):
            item = {"url": item}
        if not isinstance(item, dict) or not rubric.is_url(item.get("url")):
            errors.append("%s[%d]: must be an http(s) image URL" % (where, index))
            continue
        clean.append(dict((k, str(item[k])) for k in ("url", "caption") if item.get(k)))
    return clean


def enrich(cfg, store, payload):
    """Record what the detail page says, then gate it."""
    run = _require_run(store)
    if not isinstance(payload, dict):
        raise ScoutError("Expected a JSON object")
    rec = _get(store, payload.get("id"))

    if payload.get("gone"):
        _mark_delisted(rec, "page gone when opened")
        run["evaluated"] += 1
        _touch(store, rec)
        return {"id": rec["id"], "result": "delisted"}

    errors, facts, provenance, unknown = _check_facts(cfg, rec, payload)
    addr = payload.get("address")
    key = None
    if addr:
        if isinstance(addr, str):
            addr = {"raw": addr}
        if not isinstance(addr, dict) or not addr.get("raw"):
            errors.append("address: give {\"raw\": \"245 East 10th Street #4B, New York, NY 10003\"}")
        else:
            key = _address_key(cfg, addr["raw"], addr.get("unit"), addr.get("zip") or facts.get("zip"),
                               addr.get("borough") or facts.get("borough"))
    if not key and len(str(payload.get("address_unknown_reason") or "").strip()) < 5:
        errors.append("address: no street number found. Give the full address, or set address_unknown_reason")
    photos = _check_images(payload.get("photos"), "photos", errors)
    floor_plans = _check_images(payload.get("floor_plans"), "floor_plans", errors)
    if not photos and facts.get("photo_count") != 0 \
            and len(str(payload.get("photos_unavailable_reason") or "").strip()) < 5:
        errors.append("photos: list the listing's photo URLs in order, or set photos_unavailable_reason")
    if errors:
        raise ScoutError(errors, hint="Nothing was saved. Fix these and send the file again. "
                                      "Never fill a gap with a guess: use `unknown`.")

    was_refresh = rec.get("refresh")
    if rec.get("rubric") and rec.get("facts") != facts:
        # The agent's one-liner and questions were written about the old facts.
        rec["rubric"]["headline"], rec["rubric"]["questions"] = None, []
    rec.update({"facts": facts, "provenance": provenance, "unknown": unknown,
                "raw_text": payload["raw_text"][:MAX_PAGE_TEXT], "refresh": None,
                "enriched_at": now_iso(),
                "enriched_from": {"source": payload.get("source") or rec["sources"][0]["source"],
                                  "url": payload.get("url") or rec["sources"][0]["url"]}})
    if key:
        rec["address_key"] = key
        rec["address"] = {"raw": addr["raw"], "lat": addr.get("lat"), "lng": addr.get("lng")}
        rec.pop("address_unknown_reason", None)
    else:
        rec["address_key"], rec["address"] = None, None
        rec["address_unknown_reason"] = payload["address_unknown_reason"]
    rec["photos"], rec["floor_plans"] = photos, floor_plans
    rec["photos_unavailable_reason"] = None if photos else payload.get("photos_unavailable_reason")
    source = rec["enriched_from"]["source"]
    if _record_price(rec, source, facts.get("rent_advertised")) is not None:
        _event(rec, "price", "%s: detail page shows %s" % (source, facts["rent_advertised"]))
    for entry in rec["sources"]:
        if entry["source"] == source and is_number(facts.get("rent_advertised")):
            entry["rent"] = facts["rent_advertised"]
    run["evaluated"] += 1

    # The address is often only on the detail page, so look for twins again now.
    for other in list(store.listings.values()):
        if other["id"] != rec["id"] and address.match(key, other.get("address_key")) == "same":
            if other.get("facts") is not None and not was_refresh:
                _merge_into(store, other, rec)
                judge(cfg, store, other)
                _touch(store, other)
                return {"id": other["id"], "result": "merged", "merged_from": rec["id"],
                        "verdict": other["verdict"], "reason": "Same unit as %s, already evaluated" % other["id"]}
            _merge_into(store, rec, other)
    _link_possible_duplicates(store, rec)
    judge(cfg, store, rec)
    _touch(store, rec)
    return {"id": rec["id"], "result": "gated", "gate": rec["gate"]["result"],
            "gate_fails": gate.fail_text(rec["gate"]) or None, "gate_unknowns": gate.unknown_labels(rec["gate"]),
            "flags": [f["message"] for f in rec["flags"]], "stage": rec["stage"],
            "verdict": rec["verdict"], "reason": rec["verdict_reason"]}


# --------------------------------------------------------------------------
# Rubric + verdict
# --------------------------------------------------------------------------

def score(cfg, store, payload):
    run = _require_run(store)
    if not isinstance(payload, dict):
        raise ScoutError("Expected a JSON object")
    rec = _get(store, payload.get("id"))
    if rec.get("facts") is None:
        raise ScoutError("%s has not been enriched yet, so it cannot be scored" % rec["id"])
    if rec["gate"]["result"] != gate.PASS or flags.risk_verdict(cfg, rec["flags"]):
        raise ScoutError("%s did not get through the gate (%s); do not spend time scoring it"
                         % (rec["id"], rec["verdict_reason"]))
    given = payload.get("criteria") if isinstance(payload.get("criteria"), dict) else {}
    only = set(criteria_to_score(cfg, rec)) | set(given)
    clean, errors = rubric.validate(cfg, payload, rec.get("raw_text"), only, len(rec.get("photos") or []),
                                    len(rec.get("floor_plans") or []))
    headline = " ".join(str(payload.get("headline") or "").split())
    if len(headline) > 200:
        errors.append("headline: keep it under 200 characters")
    questions = payload.get("questions") or []
    if not isinstance(questions, list) or not all(isinstance(q, str) for q in questions):
        errors.append("questions: must be a list of text items")
    if errors:
        raise ScoutError(errors, hint="Nothing was saved. Fix these and send the file again. "
                                      "If something cannot be verified, score it null with an unknown_reason.")
    hashes = cfg.criterion_hashes()
    if not rec.get("rubric"):
        rec["rubric"] = {"scores": {}}
    for cid, entry in clean.items():
        entry.update({"hash": hashes[cid], "scored_at": now_iso()})
        rec["rubric"]["scores"][cid] = entry
    rec["rubric"]["headline"] = headline or rec["rubric"].get("headline")
    rec["rubric"]["questions"] = questions
    run["scored"] += 1
    judge(cfg, store, rec)
    _touch(store, rec)
    return {"id": rec["id"], "score": rec["rubric"]["score"], "floor": rec["rubric"]["floor"],
            "ceiling": rec["rubric"]["ceiling"], "unknown_weight": rec["rubric"]["unknown_weight"],
            "verdict": rec["verdict"], "reason": rec["verdict_reason"]}


# --------------------------------------------------------------------------
# Blocks, finish
# --------------------------------------------------------------------------

def blocked(cfg, store, source, what):
    """A block page or CAPTCHA: stop using that site for the rest of the run."""
    run = _require_run(store)
    if source not in cfg.sources:
        raise ScoutError("Unknown source %r; expected one of %s" % (source, ", ".join(cfg.sources)))
    run["blocked"][source] = {"at": now_iso(), "what": what}
    for sweep in run["sweeps"]:
        if sweep["source"] == source and sweep["status"] == "pending":
            sweep["status"] = "blocked"
    return {"blocked": source, "do": "Do not retry or work around it. Carry on with `python3 -m scout next`."}


def _fully_swept(cfg, run):
    """Sites whose every sweep finished, so an unseen listing really is missing."""
    complete = []
    for source in cfg.enabled_sources():
        if cfg.sources[source]["url_style"] == "inbox" or source in run["blocked"]:
            continue
        mine = [s for s in run["sweeps"] if s["source"] == source]
        if mine and all(s["status"] in ("done", "split") for s in mine):
            complete.append(source)
    return complete


def _in_sweep_range(cfg, entry):
    rent = entry.get("rent")
    return not is_number(rent) or cfg.search["price_min"] <= rent <= cfg.search["price_max"]


def _close_run(cfg, store, abandoned=False):
    run = store.run
    delisted = []
    if not abandoned:
        complete = _fully_swept(cfg, run)
        for rec in store.listings.values():
            if rec.get("delisted_at"):
                continue
            for entry in rec["sources"]:
                if entry["source"] in complete and entry["active"] and entry.get("seen_in_run") != run["id"] \
                        and _in_sweep_range(cfg, entry):
                    entry["missed_sweeps"] += 1
                    if entry["missed_sweeps"] >= cfg.limits["delist_after_missed_sweeps"]:
                        entry["active"] = False
            if not any(entry["active"] for entry in rec["sources"]):
                _mark_delisted(rec, "not seen in %d full sweeps" % cfg.limits["delist_after_missed_sweeps"])
                delisted.append(rec["id"])

    tally = {}
    for rec_id in run["touched"]:
        rec = store.listings.get(rec_id)
        if rec and rec.get("verdict"):
            tally[rec["verdict"]] = tally.get(rec["verdict"], 0) + 1
    summary = {
        "id": run["id"], "started_at": run["started_at"], "finished_at": now_iso(), "abandoned": abandoned,
        "resumes": run["resumes"], "counts": run["counts"], "evaluated": run["evaluated"], "scored": run["scored"],
        "touched": run["touched"], "verdicts": tally, "blocked": run["blocked"], "notes": run["notes"],
        "delisted": delisted,
        "sweeps": dict((status, sum(1 for s in run["sweeps"] if s["status"] == status))
                       for status in set(s["status"] for s in run["sweeps"])),
        "left_to_open": len(_queue(cfg, store, NEEDS_ENRICH)),
        "left_to_score": len(_queue(cfg, store, NEEDS_SCORE)),
    }
    store.data["runs"].append(summary)
    store.data["run"] = None
    return summary


def finish(cfg, store):
    _require_run(store)
    return _close_run(cfg, store)


# --------------------------------------------------------------------------
# The human's side: status, tour feedback, lookup
# --------------------------------------------------------------------------

def queue_refresh(store, ids=None, everything=False, older_than_days=0):
    """Queue listings for a fresh look at their page: still up? same price? new photos?

    Default: every live TOUR and WATCH. `everything` widens that to every live listing
    that has been opened before. Explicit ids override both.
    """
    if ids:
        chosen = [_get(store, rec_id) for rec_id in ids]
    else:
        chosen = [rec for rec in store.listings.values()
                  if rec.get("facts") is not None and not rec.get("delisted_at") and rec["status"] != "rejected"
                  and (everything or rec.get("verdict") in (verdict.TOUR, verdict.WATCH))]
    queued = []
    for rec in chosen:
        if seconds_between(rec.get("enriched_at") or rec["first_seen"], now_iso()) < older_than_days * 86400:
            continue
        rec["refresh"] = rec.get("refresh") or "recheck"
        rec["stage"] = NEEDS_ENRICH
        if ids and rec.get("delisted_at"):   # asked for by name: look even though it seemed gone
            rec["delisted_at"] = None
            rec["status"] = "active" if rec["status"] == "delisted" else rec["status"]
            for entry in rec["sources"]:
                entry["active"] = True
        queued.append(rec["id"])
    return {"queued": queued, "count": len(queued)}


def find(store, text):
    needle = norm_text(text)
    hits = []
    for rec in store.listings.values():
        haystack = norm_text(" ".join(filter(None, [
            rec["id"], (rec.get("address") or {}).get("raw"), rec["card"].get("address"), rec["card"].get("title"),
            address.display_key(rec.get("address_key"))] + [entry["url"] for entry in rec["sources"]])))
        if needle in haystack:
            hits.append({"id": rec["id"], "address": (rec.get("address") or {}).get("raw") or rec["card"].get("title"),
                         "verdict": rec.get("verdict"), "status": rec["status"]})
    return hits


def set_status(store, rec_id, status, note=None):
    if status not in USER_STATUSES:
        raise ScoutError("Status must be one of %s" % ", ".join(USER_STATUSES))
    rec = _get(store, rec_id)
    rec["status"] = status
    _event(rec, "status", status + (": " + note if note else ""))
    return {"id": rec["id"], "status": status}


def add_feedback(store, rec_id, would_rent, why):
    """Record the human's own verdict after a tour, for the calibration report."""
    rec = _get(store, rec_id)
    if not str(why or "").strip():
        raise ScoutError("Say why in a few words; the calibration report depends on it")
    rec["feedback"].append({"at": now_iso(), "would_rent": bool(would_rent), "why": str(why).strip(),
                            "scout_verdict": rec.get("verdict"), "scout_score": (rec.get("rubric") or {}).get("score")})
    if rec["status"] in ("active", "delisted"):
        rec["status"] = "toured"
    _event(rec, "feedback", "would rent" if would_rent else "would not rent")
    return {"id": rec["id"], "status": rec["status"], "tours_recorded": sum(
        1 for other in store.listings.values() if other["feedback"])}
