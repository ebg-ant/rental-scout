"""The run pipeline: discover, dedupe, prefilter, enrich, gate, score, verdict.

Progress is stored on each listing (`stage`), on the open run, and per site in
`coverage`, and saved after every command. The agent only ever asks `next` what to
do, so a dropped browser session resumes exactly where it stopped.
"""

from __future__ import annotations

import re
from urllib.parse import quote_plus, urlsplit

from . import address, derive, flags, gate, prefilter, rubric, sweeps, verdict
from .fields import RENT_INPUTS, base_inputs, check_value
from .util import (ScoutError, add_seconds, days_between, is_number, median, norm_text, now_iso, parse_time, seconds_between,
                   today_iso)

NEEDS_ENRICH, NEEDS_SCORE, DONE = "needs_enrich", "needs_score", "done"
USER_STATUSES = ("active", "toured", "applied", "rejected")
SHORTLIST = (verdict.TOUR, verdict.WATCH)
STALE_RUN_HOURS = 12
MAX_PAGE_TEXT = 20000
MAX_IMAGES = 40
DAY = 86400
SHORT_READ = 0.8   # a finished sweep must have read at least this share of the results the site reported
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
    # Craigslist moved from .../<digits>.html to .../view/d/<slug>/<post id>; the slug can change on a repost.
    for pattern in (r"(\d+)_zpid", r"/(\d{8,})\.html$", r"/view/d/[^/]+/([A-Za-z0-9_-]{16,})$", r"/rental/(\d+)$",
                    r"/building/(.+)$"):
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
    """Same door, unit missing on one side, similar size and rent: link, never merge.

    Rebuilt from scratch each time, so a link disappears once the units are known.
    """
    for other in store.listings.values():
        if rec["id"] in other["possible_duplicates"]:
            other["possible_duplicates"].remove(rec["id"])
    rec["possible_duplicates"] = []
    for other in store.listings.values():
        if other["id"] == rec["id"] or address.match(rec.get("address_key"), other.get("address_key")) != "maybe":
            continue
        beds = (rec["card"].get("beds"), other["card"].get("beds"))
        if None not in beds and beds[0] != beds[1]:
            continue
        if not _close_rent(rec["card"].get("rent"), other["card"].get("rent")):
            continue
        rec["possible_duplicates"].append(other["id"])
        other["possible_duplicates"].append(rec["id"])


def checked_at(rec):
    """When the listing was last known to be up: seen in results, or its page read."""
    return max(rec["last_seen"], rec.get("enriched_at") or "")


# --------------------------------------------------------------------------
# Judging: everything deterministic about one listing, recomputed from scratch
# --------------------------------------------------------------------------

def _comps(store):
    """Advertised rents seen for a bedroom count. Reads only what was observed, never
    an earlier judgment, so judging twice gives the same answer."""
    def lookup(beds):
        rents = []
        for rec in store.listings.values():
            facts = rec.get("facts") or {}
            if (facts.get("beds") if facts.get("beds") is not None else rec["card"].get("beds")) != beds:
                continue
            rent = facts.get("rent_advertised") if is_number(facts.get("rent_advertised")) else rec["card"].get("rent")
            if is_number(rent):
                rents.append(rent)
        return median(rents), len(rents)
    return lookup


def _card_values(cfg, rec):
    """What the search card shows, read as if it were the whole story. Only used for
    the card check, whose margin allows for a concession the card does not show."""
    card = rec["card"]
    facts = {"rent_advertised": card.get("rent"), "rent_advertised_is": "gross", "free_months": 0,
             "beds": card.get("beds"), "baths": card.get("baths"), "sqft": card.get("sqft"),
             "neighborhood": card.get("neighborhood"), "zip": card.get("zip"), "borough": card.get("borough")}
    return derive.values_for(facts, rec.get("address_key"), cfg)


def criteria_to_score(cfg, rec):
    """Criterion ids with no current score: never scored, or their wording changed.
    A criterion weighted 0 is switched off: it cannot move the total, so it is never asked for."""
    scores = (rec.get("rubric") or {}).get("scores") or {}
    hashes = cfg.criterion_hashes()
    return [c["id"] for c in cfg.rubric if c["weight"] > 0
            and (c["id"] not in scores or scores[c["id"]].get("hash") != hashes[c["id"]])]


def _incomplete(cfg, rec):
    """Reading stopped early because the listing was ruled out, and what was recorded no longer
    rules it out. Facts nobody read are not held against it: the rest of the page must be read."""
    return bool(rec.get("partial")) and rec.get("gate") is not None \
        and not gate.known_to_fail(cfg, rec["gate"]) and not flags.risk_verdict(cfg, rec["flags"])


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
    if _incomplete(cfg, rec):
        new_verdict, reason, survives = None, None, False
    if rec.get("delisted_at"):
        rec["stage"] = DONE      # nothing more can be learned about a home that is gone
    elif rec.get("refresh") or _incomplete(cfg, rec) or (new_verdict is None and not enriched):
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
    if new_verdict is None and rec.get("delisted_at"):
        reason = rec.get("verdict_reason")   # gone before it could be judged: keep saying so
    rec["verdict"], rec["verdict_reason"] = new_verdict, reason  # None means not judged yet
    rec["judged_at"] = now_iso()
    return rec


def _rent_could_matter(cfg, store, rec):
    """After a rent change: is the listing out of the running whatever the rent is?

    One that fails on pets, or scored too low, does not earn a browser slot.
    """
    if rec.get("facts") is None or rec["status"] == "rejected":
        return False
    facts = dict((k, v) for k, v in rec["facts"].items() if k not in RENT_INPUTS)
    values = derive.values_for(facts, rec.get("address_key"), cfg)
    without_rent = gate.run(cfg, values, ignoring=RENT_INPUTS)
    other_flags = flags.run(cfg, dict(rec, facts=facts), values, _comps(store))
    if rec.get("partial"):     # only part of the page was read: unread facts cannot keep it out
        return not gate.known_to_fail(cfg, without_rent) and not flags.risk_verdict(cfg, other_flags)
    scores = (rec.get("rubric") or {}).get("scores") or {}
    outcome, _ = verdict.decide(cfg, {"result": "pass"}, None, without_rent, other_flags, scores,
                                rubric.totals(cfg, scores) if scores else None)
    return outcome not in (verdict.PASS, verdict.PASS_RISK)


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
# Coverage: what each site has been swept for, across runs
# --------------------------------------------------------------------------

def _coverage(store, source):
    return store.data.setdefault("coverage", {}).setdefault(
        source, {"last_full_at": None, "search_hash": None, "cycle": None})


def _plan_discovery(cfg, store, run, force_full):
    """Decide, per site, between a full sweep and a newest-first top-up.

    A full sweep is due on first use, when the search itself changed, every
    `refresh.full_sweep_every_days`, or on request. It lives in `coverage`, not in the
    run, so a long one carries over from run to run until it is finished.
    """
    run["discovery"], run["search_paused"] = True, False
    run["sweeps"] = [s for s in run["sweeps"] if s["status"] != "pending"]
    done_keys = set(s["key"] for s in run["sweeps"])
    for source in cfg.enabled_sources():
        if cfg.sources[source]["url_style"] == "inbox":
            run["sweeps"] += [s for s in sweeps.plan(cfg, source, sweeps.INBOX) if s["key"] not in done_keys]
            continue
        cover = _coverage(store, source)
        fingerprint = sweeps.search_hash(cfg, source)
        if cover["search_hash"] != fingerprint:
            cover.update({"search_hash": fingerprint, "cycle": None, "last_full_at": None})
        due = force_full or cover["last_full_at"] is None or \
            days_between(cover["last_full_at"], now_iso()) >= cfg.refresh["full_sweep_every_days"]
        if cover["cycle"] and cover["cycle"]["begun_in"] != run["id"]:
            # Carried over from an earlier run: catch today's newest first, then carry on with it.
            run["sweeps"] += [s for s in sweeps.plan(cfg, source, sweeps.INCREMENTAL) if s["key"] not in done_keys]
        elif cover["cycle"]:
            pass   # begun in this very run: it starts from the newest page itself
        elif due:
            cover["cycle"] = {"started_at": now_iso(), "begun_in": run["id"], "sweeps": sweeps.plan(cfg, source, sweeps.FULL)}
        else:
            run["sweeps"] += [s for s in sweeps.plan(cfg, source, sweeps.INCREMENTAL) if s["key"] not in done_keys]


def _cycle_sweeps(cfg, store):
    found = []
    for source in cfg.enabled_sources():
        cycle = store.data.get("coverage", {}).get(source, {}).get("cycle")
        if cycle:
            found += cycle["sweeps"]
    return found


def _in_sweep_range(cfg, entry):
    rent = entry.get("rent")
    return not is_number(rent) or cfg.search["price_min"] <= rent <= cfg.search["price_max"]


def _finish_cycle_if_done(cfg, store, source):
    """When every band of a full sweep has been read to its last page, anything that
    sweeps used to find on this site and this one did not is missing."""
    cover = _coverage(store, source)
    cycle = cover["cycle"]
    if not cycle or any(s["status"] == "pending" for s in cycle["sweeps"]):
        return None
    cover["cycle"] = None
    cover["last_full_at"] = now_iso()
    if not sum(s["cards"] for s in cycle["sweeps"]):
        if store.run:
            store.run["notes"].append("%s: the full sweep found no listings at all. Open the search yourself to check it "
                                      "still works and is not too narrow" % source)
        return {"source": source, "complete": False}
    if any(s["status"] in ("capped", "short") for s in cycle["sweeps"]):
        return {"source": source, "complete": False}   # holes in the coverage: absence proves nothing
    missing, delisted = [], []
    for rec in store.listings.values():
        if rec.get("delisted_at"):
            continue
        # Seen under another link since the sweep began (a repost, or another site): the home is still up.
        alive = any(e["active"] and e["last_seen"] >= cycle["started_at"] for e in rec["sources"])
        for entry in rec["sources"]:
            if entry["source"] != source or not entry["active"] or not entry.get("swept") \
                    or not _in_sweep_range(cfg, entry) or entry["last_seen"] >= cycle["started_at"]:
                continue
            entry["missed_sweeps"] += 1
            missing.append(rec["id"])
            if not alive and rec.get("verdict") in SHORTLIST and rec["status"] != "rejected":
                # Too important to drop on an absence: open the page and see.
                rec["refresh"] = rec.get("refresh") or "recheck"
                rec["stage"] = NEEDS_ENRICH
            elif entry["missed_sweeps"] >= cfg.refresh["delist_after_missed_sweeps"]:
                entry["active"] = False
        if not any(entry["active"] for entry in rec["sources"]):
            _mark_delisted(rec, "missing from %d full sweeps" % cfg.refresh["delist_after_missed_sweeps"])
            delisted.append(rec["id"])
    if store.run:
        store.run["delisted"] += delisted
    return {"source": source, "complete": True, "missing": len(set(missing)), "delisted": delisted}


# --------------------------------------------------------------------------
# Run lifecycle
# --------------------------------------------------------------------------

def _searching(run):
    return run["discovery"] and not run["search_paused"]


def _require_run(store):
    if not store.run:
        raise ScoutError("No run is open", hint="Start one with `python3 -m scout start`.")
    return store.run


def start(cfg, store, sweep=True, full=False):
    """Open a run, or resume the one left open by a dropped session.

    sweep=False opens a run with no discovery, for judging hand-picked listings or
    refreshing ones already on file. full=True forces a full sweep of every site.
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
        if sweep and (full or not _searching(run)):
            _plan_discovery(cfg, store, run, full)   # a daily run must not be swallowed by a leftover no-sweep run
        elif not sweep and _searching(run):
            # Asked not to search this time: drop what is left of the searching, keep what it found.
            run["search_paused"] = True
            run["sweeps"] = [s for s in run["sweeps"] if s["status"] != "pending"]
        result.update({"run": run["id"], "resumed": True})
    else:
        backup = store.backup()
        if backup:
            result["backup"] = backup
        store.data["run"] = run = {
            "id": "run-" + parse_time(now_iso()).strftime("%Y%m%d-%H%M%S"),
            "started_at": now_iso(), "resumed_at": now_iso(), "resumes": 0, "discovery": False, "search_paused": False,
            "sweeps": [], "pages_read": {}, "blocked": {}, "next_allowed_at": {}, "issued": None,
            "kind_pages": {}, "sweep_pages": {}, "browser_at": {},
            "evaluated": 0, "scored": 0, "touched": [], "delisted": [], "notes": [],
            "rechecks_queued": False,
            "counts": {"cards": 0, "new": 0, "unchanged": 0, "price_changed": 0, "relisted": 0,
                       "merged": 0, "prefiltered": 0, "card_rejected": 0},
        }
        _inherit_unreported(store, run)
        if sweep:
            _plan_discovery(cfg, store, run, full)
        result.update({"run": run["id"], "resumed": False})
    result["plan"] = dict((source, _sweep_mode(store, run, source)) for source in cfg.enabled_sources()) \
        if _searching(run) else "no searching: only listings already queued or handed in by hand"
    return result


def _inherit_unreported(store, run):
    """Take over what an abandoned run found, so this run's summary reports it as its own."""
    left = store.data.pop("unreported", None)
    if not left:
        return
    run["touched"] = [i for i in left["touched"] if i in store.listings]
    run["delisted"] = [i for i in left["delisted"] if i in store.listings]
    for rec_id in run["touched"]:
        rec = store.listings[rec_id]
        if (rec.get("last_change") or {}).get("run") == left["run"]:
            rec["last_change"]["run"] = run["id"]
        for stamp in ("verdict_changed_run", "first_seen_run"):
            if rec.get(stamp) == left["run"]:
                rec[stamp] = run["id"]


def _sweep_mode(store, run, source):
    cycle = store.data.get("coverage", {}).get(source, {}).get("cycle")
    topping_up = any(s["source"] == source for s in run["sweeps"])
    if cycle and topping_up:
        return "newest first, then carry on with the full sweep"
    if cycle:
        return "full sweep"
    return "newest first"


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
        shortlisted = rec.get("verdict") in SHORTLIST
        waiting.append(((0 if shortlisted else 1, 0 if rec.get("address_key") else 1, rec["seq"]), rec))
    waiting.sort(key=lambda pair: pair[0])
    for _, rec in waiting:   # preferred site first; within a site, the link seen most recently
        rec["sources"].sort(key=lambda e: e["last_seen"], reverse=True)
        rec["sources"].sort(key=lambda e: order.index(e["source"]) if e["source"] in order else len(order))
    return [rec for _, rec in waiting]


def _full_address(cfg, rec):
    """The address as a map search needs it. "212 East 7th Street" exists in Manhattan
    and in Brooklyn, so add whatever locality is known before falling back to the city."""
    raw = (rec.get("address") or {}).get("raw")
    if not raw or re.search(r"\d{5}|,", raw):
        return raw
    key = rec.get("address_key") or {}
    city = cfg.search["city"]
    state = city.split(",")[-1].strip() if "," in city else ""
    if key.get("zip"):
        return "%s, %s %s" % (raw, state, key["zip"])
    if key.get("borough"):
        return "%s, %s, %s" % (raw, key["borough"].title(), state)
    hood = (rec.get("facts") or {}).get("neighborhood") or rec["card"].get("neighborhood")
    return "%s, %s%s" % (raw, hood + ", " if hood else "", city)


def helper_links(cfg, rec):
    addr = rec.get("address") or {}
    values = {"address": _full_address(cfg, rec), "anchor": (cfg.search.get("commute_anchor") or {}).get("address"),
              "lat": addr.get("lat"), "lng": addr.get("lng"), "zip": (rec.get("address_key") or {}).get("zip")}
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


def _dealbreaker_fields(cfg):
    """Facts that can rule a listing out on their own, so they are worth reading first."""
    names = []
    for rule in cfg.gate["rules"]:
        if rule["enabled"] and rule["dealbreaker"]:
            names += [n for n in base_inputs(rule["field"], cfg.registry) if n not in names]
    return names


def _pause(cfg, source):
    own = cfg.sources.get(source, {}).get("throttle_seconds")
    return cfg.limits["throttle_seconds"] if own is None else own


def _wait_for(store, source):
    allowed = store.run["next_allowed_at"].get(source)
    return max(0, int(round(seconds_between(now_iso(), allowed)))) if allowed else 0


def _issue(cfg, store, source, identity, loads=1):
    """Hand out one task's page loads and return how long to wait before the first.

    The clock for the next task on this site starts when these loads happen (now plus
    the wait), not when they were handed out. Asking again before then changes nothing;
    asking again later means the loading is only happening now, so the clock restarts.
    """
    run = store.run
    issued = run.get("issued")
    if issued and issued["id"] == identity and seconds_between(now_iso(), issued["load_at"]) >= 0:
        return int(round(seconds_between(now_iso(), issued["load_at"])))
    wait = 0 if issued and issued["id"] == identity else _wait_for(store, source)
    load_at = add_seconds(now_iso(), wait)
    run["issued"] = {"id": identity, "load_at": load_at}
    run["next_allowed_at"][source] = add_seconds(load_at, _pause(cfg, source) * loads)
    return wait


def _landed(cfg, store, source):
    """A page has just been handed in, so it was loaded no later than now. Counting the
    pause from here guarantees the gap however long the agent took to get to it."""
    allowed = add_seconds(now_iso(), _pause(cfg, source))
    store.run["next_allowed_at"][source] = max(allowed, store.run["next_allowed_at"].get(source) or "")


def _budget(cfg, store):
    run = store.run
    used = seconds_between(run["resumed_at"], now_iso()) / 60.0
    return {"listings_opened": run["evaluated"], "listings_max": cfg.limits["max_listings_per_run"],
            "minutes_used": round(used, 1), "minutes_max": cfg.limits["max_runtime_minutes"]}


def _queue_rechecks(cfg, store):
    """Shortlisted listings nobody has seen for a while get their page re-opened, so the
    shortlist does not fill up with homes that are gone. TOURs first, stalest first."""
    due = []
    for rec in store.listings.values():
        if rec.get("verdict") not in SHORTLIST or rec["stage"] != DONE or rec.get("delisted_at") \
                or rec["status"] == "rejected":
            continue
        tour = rec["verdict"] == verdict.TOUR
        limit = cfg.refresh["recheck_tour_after_days" if tour else "recheck_watch_after_days"]
        if days_between(checked_at(rec), now_iso()) >= limit:
            due.append((0 if tour else 1, checked_at(rec), rec["seq"], rec))
    for _, _, _, rec in sorted(due, key=lambda item: item[:3])[:cfg.refresh["max_rechecks_per_run"]]:
        rec["refresh"] = "recheck"
        rec["stage"] = NEEDS_ENRICH


def _page_allowance(cfg, store, source, mode):
    """Results pages this kind of sweep may still read on this site in this run.

    While a long full sweep is under way, the daily top-up may use only half the site's
    pages, so the full sweep always moves forward and eventually finishes.
    """
    run, total = store.run, cfg.limits["max_search_pages_per_run"]
    left = total - run["pages_read"].get(source, 0)
    if mode == sweeps.INCREMENTAL and store.data.get("coverage", {}).get(source, {}).get("cycle"):
        left = min(left, max(1, total // 2) - run["kind_pages"].get("%s:%s" % (source, mode), 0))
    return left


def _pick_sweep(cfg, store, candidates):
    """The next results page to read.

    Among sites, the one that has waited longest. Within a site, each search gets an equal
    run of consecutive pages: consecutive, because on a site with only a pager the next
    page is then one click away; equal, so that every borough gets its newest pages read.
    """
    run = store.run
    ready = [s for s in candidates if s["status"] == "pending" and s["source"] not in run["blocked"]
             and _page_allowance(cfg, store, s["source"], s["mode"]) > 0]
    if not ready:
        return None
    for sweep in ready:   # a page already handed out and not yet handed in comes first: asking twice changes nothing
        if (run.get("issued") or {}).get("id") == "%s#%d" % (sweep["key"], sweep["page"] + 1):
            return sweep
    order = list(cfg.sources)

    def rank(sweep):
        here = run["browser_at"].get(sweep["source"]) != sweep["key"]
        place = (order.index(sweep["source"]), sweep["index"], sweep["min"])
        cycle = store.data.get("coverage", {}).get(sweep["source"], {}).get("cycle")
        if sweep["mode"] == sweeps.FULL and cycle and cycle["begun_in"] != run["id"]:
            # A full sweep carried over from an earlier run: finish one search before starting the next,
            # so that each day's few pages add up to progress.
            return (_wait_for(store, sweep["source"]), 0, here) + place
        rivals = [other for other in ready if other["source"] == sweep["source"]]
        pages = _page_allowance(cfg, store, sweep["source"], sweep["mode"]) + sum(
            run["sweep_pages"].get(other["key"], 0) for other in rivals)
        share = max(1, pages // len(rivals))
        return (_wait_for(store, sweep["source"]), run["sweep_pages"].get(sweep["key"], 0) // share, here) + place

    chosen = min(ready, key=rank)
    if chosen["mode"] == sweeps.FULL and chosen.get("read_in") != run["id"]:
        # Picked up again on a later day. Listings have come and gone since, shifting the pages, so read the
        # last page again rather than risk skipping what slid back across the boundary.
        if chosen["page"] > 0 and _page_allowance(cfg, store, chosen["source"], chosen["mode"]) >= 2:
            chosen["page"] -= 1
            chosen["cards"] -= chosen.get("last_page_cards", 0)     # it will be counted again when re-read
        chosen["read_in"] = run["id"]
    return chosen


def _sweep_task(cfg, store, sweep, budget):
    spec, run = cfg.sources[sweep["source"]], store.run
    task = {"task": "sweep", "sweep": sweep["key"], "source": sweep["source"], "mode": sweep["mode"],
            "page": sweep["page"] + 1,
            "read": spec.get("instructions") or "agent/sources/%s.md" % sweep["source"],
            "submit": "python3 -m scout ingest <file.json>", "budget": budget}
    if sweep["mode"] == sweeps.INBOX:
        task["do"] = "Read new saved-search alert emails and turn each listing into a card."
        return task
    first = sweeps.band_url(spec["url_style"], sweep["base_url"], sweep["min"], sweep["max"])
    url = sweeps.page_url(spec["url_style"], first, task["page"]) if first else None
    loads = 1
    if task["page"] > 1 and not url:
        task["wait_between_clicks_seconds"] = _pause(cfg, sweep["source"])
        if run["browser_at"].get(sweep["source"]) == sweep["key"]:
            task["reach_page"] = "this site's tab should still be on page %d of this search: click to the next page. If it " \
                                 "is not, open the URL (page 1) and click forward, waiting wait_between_clicks_seconds " \
                                 "before each click" % sweep["page"]
        else:
            loads = task["page"]
            task["reach_page"] = "the URL opens page 1; click through the pager to page %d, waiting " \
                                 "wait_between_clicks_seconds before each click" % task["page"]
    task.update({
        "url": url or first or sweep["base_url"], "price_min": sweep["min"], "price_max": sweep["max"],
        "filters": "price range and newest-first sort are in the URL; confirm the page shows them" if first else
                   "NOT in the URL: set the price range and sort by newest on the page by hand",
        "do": "Read this one results page into cards and hand it in. The reply says whether to go on.",
        "wait_seconds": _issue(cfg, store, sweep["source"], "%s#%d" % (sweep["key"], task["page"]), loads),
    })
    return task


def next_task(cfg, store):
    """The single question the agent asks: what should I do now?

    Order: finish scoring what is open; read today's newest results; carry on with any
    full sweep, up to the page limit; re-check the shortlist; open listings.
    """
    run = store.run
    if not run:
        return {"task": "start", "do": "Run `python3 -m scout start`."}
    budget = _budget(cfg, store)
    if budget["minutes_used"] >= budget["minutes_max"]:
        return {"task": "finish", "why": "time limit reached", "do": "Run `python3 -m scout finish`.", "budget": budget}

    to_score = _queue(cfg, store, NEEDS_SCORE)
    if to_score:
        rec = to_score[0]
        pending = criteria_to_score(cfg, rec)
        return {
            "task": "score", "id": rec["id"], "read": "agent/RUBRIC_SCORING.md",
            "urls": [entry["url"] for entry in _usable_sources(store, rec)],
            "address": (rec.get("address") or {}).get("raw"),
            "address_disclosed": bool(rec.get("address_key")),
            "facts": rec["facts"], "unknown": rec.get("unknown") or {}, "flags": [f["message"] for f in rec["flags"]],
            "listing_text": rec.get("raw_text"),
            "photos_saved": len(rec.get("photos") or []), "floor_plans_saved": len(rec.get("floor_plans") or []),
            "criteria": [{"id": c["id"], "label": c["label"], "guidance": c["guidance"], "anchors": c["anchors"],
                          "evidence_required": c["evidence_required"]} for c in cfg.rubric if c["id"] in pending],
            "helper_links": helper_links(cfg, rec),
            "submit": "python3 -m scout score <file.json>", "budget": budget,
        }

    sweep = _pick_sweep(cfg, store, run["sweeps"]) or \
        (_pick_sweep(cfg, store, _cycle_sweeps(cfg, store)) if _searching(run) else None)
    if sweep:
        return _sweep_task(cfg, store, sweep, budget)

    # Only now, so that nothing a sweep has just seen alive is re-opened for nothing.
    if _searching(run) and not run["rechecks_queued"]:
        run["rechecks_queued"] = True
        _queue_rechecks(cfg, store)
    to_enrich = _queue(cfg, store, NEEDS_ENRICH)
    if to_enrich and run["evaluated"] < cfg.limits["max_listings_per_run"]:
        rec = to_enrich[0]
        entry = _usable_sources(store, rec)[0]
        task = {
            "task": "enrich", "id": rec["id"],
            "why": rec.get("refresh") or ("incomplete" if rec.get("partial") else None) or rec.get("queue_reason") or "new",
            "read": "agent/ENRICH.md", "source": entry["source"], "url": entry["url"],
            "other_urls": [e["url"] for e in _usable_sources(store, rec)[1:]],
            "card": rec["card"],
            "capture": [{"field": name, "type": cfg.registry[name].describe(), "what": cfg.registry[name].help}
                        for name in required_fields(cfg)],
            "check_first": _dealbreaker_fields(cfg),
            "commute_anchor": cfg.search.get("commute_anchor"),
            "helper_links": helper_links(cfg, rec),
            "wait_seconds": _issue(cfg, store, entry["source"], "enrich:%s:%s" % (rec["id"], entry["url"])),
            "submit": "python3 -m scout enrich <file.json>", "budget": budget,
        }
        if rec.get("facts"):
            task["previous_facts"] = rec["facts"]
        return task

    why = "listing limit reached; %d left for next run" % len(to_enrich) if to_enrich else "nothing left to do"
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
    for key in ("title", "snippet", "address", "unit", "neighborhood", "borough", "first_photo", "listed_date"):
        if key in card and not isinstance(card[key], (str, int)):
            errors.append("%s.%s must be text, got %r" % (where, key, card[key]))
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
        "status": "active", "delisted_at": None, "feedback": [], "announced": None,
        "first_seen": now_iso(), "last_seen": now_iso(),
        "first_seen_run": store.run["id"] if store.run else None,
    }


def _record_price(rec, entry, rent, where):
    """Note a rent seen on one link's search card or on its listing page.

    Cards and pages are tracked apart: a card may show a from-price or a net rent while
    the page shows another figure, and that difference is not a price change. So are
    links: one home posted twice at two rents has not changed price either. Returns the
    previous figure from the same place if this one differs from it.
    """
    if not is_number(rent):
        return None
    previous = [p for p in rec["price_history"] if p["source"] == entry["source"] and p["where"] == where
                and p["source_id"] == entry["source_id"]]
    if previous and previous[-1]["rent"] == rent:
        return None
    rec["price_history"].append({"date": today_iso(), "source": entry["source"], "source_id": entry["source_id"],
                                 "rent": rent, "where": where})
    return previous[-1]["rent"] if previous else None


def _revive(rec, why):
    """A listing thought to be gone has turned up again."""
    rec["delisted_at"] = None
    if rec["status"] == "delisted":
        rec["status"] = "active"
    _event(rec, "relisted", why)


def _ingest_card(cfg, store, declared_source, swept, raw, index, errors, counts):
    where = "cards[%d]" % index
    if not isinstance(raw, dict) or not isinstance(raw.get("url"), str) or not rubric.is_url(raw["url"]):
        errors.append("%s: needs the listing's own http(s) url" % where)
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
                 "first_seen": now_iso(), "last_seen": now_iso(), "active": True, "missed_sweeps": 0, "swept": False}
        rec["sources"].append(entry)
        if how in ("address", "repost"):
            _event(rec, "also_listed" if how == "address" else "reposted", "%s %s" % (source, raw["url"]))
    was_inactive = not entry["active"]
    entry.update({"last_seen": now_iso(), "active": True, "missed_sweeps": 0, "url": raw["url"],
                  "swept": entry["swept"] or swept})
    rec["last_seen"] = now_iso()

    old_rent = _record_price(rec, entry, card.get("rent"), "card")
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
        _revive(rec, source)
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
        # Recorded facts stay what the page said when it was read. If the change could
        # matter, the page is read again; a card is never written into the facts.
        if _rent_could_matter(cfg, store, rec):
            rec["refresh"] = change
    elif rec.get("refresh") == "recheck":
        rec["refresh"] = None   # it was only queued to see whether it is still up, and here it is

    _link_possible_duplicates(store, rec)
    judge(cfg, store, rec)
    if how == "new" or change:
        _touch(store, rec)
        if how == "new" and rec["prefilter"]["result"] == "reject":
            counts["prefiltered"] += 1
        elif how == "new" and rec["card_reject"]:
            counts["card_rejected"] += 1


def _find_sweep(cfg, store, key):
    for sweep in store.run["sweeps"] + _cycle_sweeps(cfg, store):
        if sweep["key"] == key:
            return sweep
    raise ScoutError("No sweep called %r is open" % key, hint="Use the `sweep` value from `python3 -m scout next`.")


def _advance_sweep(cfg, store, sweep, payload, counts):
    """After one results page: go on, stop, or split. The code decides, not the agent."""
    run, spec = store.run, cfg.sources[sweep["source"]]
    sweep["page"] += 1
    sweep["cards"] += len(payload["cards"])
    sweep["last_page_cards"] = len(payload["cards"])
    kind = "%s:%s" % (sweep["source"], sweep["mode"])
    run["pages_read"][sweep["source"]] = run["pages_read"].get(sweep["source"], 0) + 1
    run["kind_pages"][kind] = run["kind_pages"].get(kind, 0) + 1
    run["sweep_pages"][sweep["key"]] = run["sweep_pages"].get(sweep["key"], 0) + 1
    run["browser_at"][sweep["source"]] = sweep["key"]
    if sweep["mode"] != sweeps.INBOX:
        _landed(cfg, store, sweep["source"])
    if is_number(payload.get("total_results")):
        sweep["total_results"] = payload["total_results"]
    max_pages = spec.get("max_pages")
    full = sweep["mode"] == sweeps.FULL
    nothing_new = not (counts["new"] or counts["price_changed"] or counts["relisted"])
    # Page 1 already shows the site will not serve all of this: split now, not after reading to the cap.
    too_many = full and sweep["page"] == 1 and max_pages and payload["cards"] \
        and is_number(sweep["total_results"]) and sweep["total_results"] > max_pages * len(payload["cards"])
    at_cap = bool(max_pages) and sweep["page"] >= max_pages and not payload["last_page"]
    halves = sweeps.split(sweep, cfg.limits["min_band_width"]) if full and (too_many or at_cap) else None
    result = {}

    if halves:
        holder = _coverage(store, sweep["source"])["cycle"]["sweeps"]
        sweep["status"] = "split"
        position = holder.index(sweep)
        holder[position + 1:position + 1] = halves
        result["split_into"] = [half["key"] for half in halves]
    elif payload["last_page"] and full and is_number(sweep["total_results"]) \
            and sweep["cards"] < SHORT_READ * sweep["total_results"]:
        sweep["status"] = "short"
        run["notes"].append("%s: the site reported %d results but only %d cards were read, so this sweep is not used to "
                            "decide what has gone" % (sweep["key"], sweep["total_results"], sweep["cards"]))
    elif payload["last_page"]:
        sweep["status"] = "done"
    elif sweep["mode"] == sweeps.INCREMENTAL and nothing_new:
        sweep["status"] = "stopped"
    elif at_cap and full:
        sweep["status"] = "capped"
        run["notes"].append("%s has more results than the site will show and the price band is too narrow to "
                            "split, so some listings may be missed" % sweep["key"])
    elif at_cap:
        sweep["status"] = "stopped"
        run["notes"].append("%s still had new listings after %d pages; the next full sweep will pick up the rest"
                            % (sweep["key"], sweep["page"]))
    result["sweep_status"] = sweep["status"]
    result["go_on"] = sweep["status"] == "pending"
    if sweep["mode"] == sweeps.FULL:
        finished = _finish_cycle_if_done(cfg, store, sweep["source"])
        if finished:
            result["full_sweep_finished"] = finished
    return result


def ingest(cfg, store, payload):
    """Take the cards from one results page, or from the user, and work out what is new."""
    run = _require_run(store)
    if not isinstance(payload, dict) or not isinstance(payload.get("cards"), list):
        raise ScoutError("Expected an object with a `cards` list")
    sweep = None
    if payload.get("sweep"):
        sweep = _find_sweep(cfg, store, payload["sweep"])
        if sweep["status"] != "pending":
            raise ScoutError("Sweep %s is already finished (%s)" % (sweep["key"], sweep["status"]),
                             hint="Run `python3 -m scout next` for the next task.")
        if payload.get("page") != sweep["page"] + 1:
            raise ScoutError("Expected page %d of %s, got %r" % (sweep["page"] + 1, sweep["key"], payload.get("page")),
                             hint="Hand in one results page at a time, with the `page` number from the task. "
                                  "If a page was already handed in, run `python3 -m scout next`.")
        if not isinstance(payload.get("last_page"), bool):
            raise ScoutError("`last_page` must be true or false: is this the final page of results?")
    declared = sweep["source"] if sweep else payload.get("source") or "manual"
    if declared != "manual" and declared not in cfg.sources:
        raise ScoutError("Unknown source %r; expected one of %s or manual" % (declared, ", ".join(cfg.sources)))
    swept = bool(sweep) and sweep["mode"] in (sweeps.FULL, sweeps.INCREMENTAL)

    errors = []
    counts = dict((name, 0) for name in run["counts"])
    for index, raw in enumerate(payload["cards"]):
        _ingest_card(cfg, store, declared, swept, raw, index, errors, counts)
    if errors:
        raise ScoutError(errors, hint="Nothing was saved. Fix the cards and send the file again.")
    for name, value in counts.items():
        run["counts"][name] += value

    result = {"counts": counts}
    if sweep:
        result.update(_advance_sweep(cfg, store, sweep, payload, counts))
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
    rec["stage"] = DONE
    if not rec.get("verdict"):
        rec["verdict_reason"] = "Off the market before it could be judged (%s)" % why
    _event(rec, "delisted", why)


def _reactivate(rec):
    """Treat every known link as worth trying again."""
    if rec.get("delisted_at"):
        _revive(rec, "asked for by name")
    for entry in rec["sources"]:
        entry.update({"active": True, "missed_sweeps": 0})


PAGE_FIELDS = ("facts", "provenance", "unknown", "raw_text", "partial", "photos", "floor_plans", "photos_unavailable_reason",
               "enriched_at", "enriched_from", "address", "address_key", "address_unknown_reason")


def _merge_into(store, keeper, dupe, take_page=False):
    """Fold `dupe` into `keeper` once their addresses prove they are one unit.

    take_page: `dupe` holds the fresher reading of the page, so its facts replace the
    keeper's. The keeper's id, scores, feedback and history carry on.
    """
    for entry in dupe["sources"]:
        if not _source_entry(keeper, entry["source"], entry["source_id"]):
            keeper["sources"].append(entry)
    keeper["price_history"] = sorted(keeper["price_history"] + dupe["price_history"], key=lambda p: p["date"])
    keeper["first_seen"] = min(keeper["first_seen"], dupe["first_seen"])
    keeper["last_seen"] = max(keeper["last_seen"], dupe["last_seen"])
    keeper["feedback"] += dupe["feedback"]
    if take_page:
        if keeper.get("rubric") and keeper.get("facts") != dupe.get("facts"):
            keeper["rubric"]["headline"], keeper["rubric"]["questions"] = None, []
        for name in PAGE_FIELDS:
            keeper[name] = dupe.get(name)
        keeper["refresh"] = None
    else:
        for images in ("photos", "floor_plans"):
            if not keeper.get(images):
                keeper[images] = dupe.get(images) or []
    if not keeper.get("rubric") and dupe.get("rubric"):
        keeper["rubric"] = dupe["rubric"]
    if keeper["status"] in ("active", "delisted") and dupe["status"] not in ("active", "delisted"):
        keeper["status"] = dupe["status"]
    if keeper.get("delisted_at") and not dupe.get("delisted_at"):
        _revive(keeper, "found again as %s" % dupe["id"])
    _event(keeper, "merged", "%s is the same unit" % dupe["id"])
    del store.listings[dupe["id"]]
    for rec in store.listings.values():
        if dupe["id"] in rec["possible_duplicates"]:
            rec["possible_duplicates"].remove(dupe["id"])
    run = store.run
    if run and dupe["id"] in run["touched"]:
        run["touched"].remove(dupe["id"])


def _check_facts(cfg, rec, payload, page_text, stop_early=False):
    errors = []
    facts, unknown = payload.get("facts"), payload.get("unknown") or {}
    provenance = payload.get("provenance") or {}
    if not isinstance(facts, dict):
        return ["`facts` must be an object"], {}, {}, {}
    if not isinstance(unknown, dict) or not isinstance(provenance, dict):
        return ["`unknown` and `provenance` must be objects keyed by field name"], {}, {}, {}
    if len(page_text.strip()) < 20:
        errors.append("raw_text: paste the listing's full description and amenities text, word for word")

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

    needed = [name for name in required_fields(cfg) if not stop_early or name in clean]
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


def _opened_entry(store, rec, payload):
    """The site link this payload is about: by URL, else by site, else the one handed out."""
    for entry in rec["sources"]:
        if payload.get("url") and entry["url"] == payload["url"]:
            return entry
    for entry in rec["sources"]:
        if payload.get("source") and entry["source"] == payload["source"] and entry["active"]:
            return entry
    usable = _usable_sources(store, rec)
    return usable[0] if usable else rec["sources"][0]


def enrich(cfg, store, payload):
    """Record what the detail page says, then gate it."""
    run = _require_run(store)
    if not isinstance(payload, dict):
        raise ScoutError("Expected a JSON object")
    rec = _get(store, payload.get("id"))
    entry = _opened_entry(store, rec, payload)
    run["browser_at"].pop(entry["source"], None)   # the tab has left the results page
    _landed(cfg, store, entry["source"])

    if payload.get("gone"):
        # Only the page that was opened is known to be gone.
        entry["active"] = False
        run["evaluated"] += 1
        _touch(store, rec)
        left = [e["url"] for e in rec["sources"] if e["active"]]
        if left:
            _event(rec, "link_gone", entry["url"])
            if rec["stage"] == DONE:
                rec["refresh"], rec["stage"] = rec.get("refresh") or "recheck", NEEDS_ENRICH
            return {"id": rec["id"], "result": "link_gone", "still_listed_at": left,
                    "do": "`python3 -m scout next` will offer the other link."}
        _mark_delisted(rec, "page gone when opened")
        run["delisted"].append(rec["id"])
        return {"id": rec["id"], "result": "delisted"}

    page_text = payload.get("raw_text") if isinstance(payload.get("raw_text"), str) else ""
    page_text = page_text[:MAX_PAGE_TEXT]   # quotes are checked against exactly what is kept
    stop_early = bool(payload.get("stop_early"))
    errors, facts, provenance, unknown = _check_facts(cfg, rec, payload, page_text, stop_early)
    addr = payload.get("address")
    key = None
    if addr:
        if isinstance(addr, str):
            addr = {"raw": addr}
        if not isinstance(addr, dict) or not isinstance(addr.get("raw"), str) or not addr["raw"].strip():
            errors.append("address: give {\"raw\": \"245 East 10th Street #4B, New York, NY 10003\"}")
        else:
            key = _address_key(cfg, addr["raw"], addr.get("unit"), addr.get("zip") or facts.get("zip"),
                               addr.get("borough") or facts.get("borough"))
            for part in ("lat", "lng"):
                if addr.get(part) is not None and not is_number(addr[part]):
                    errors.append("address.%s must be a number" % part)
    if not key and stop_early:
        key, addr = rec.get("address_key"), rec.get("address")     # keep whatever the card or an earlier reading gave
        payload = dict(payload, address_unknown_reason=payload.get("address_unknown_reason") or "reading stopped early")
    elif not key and len(str(payload.get("address_unknown_reason") or "").strip()) < 5:
        errors.append("address: no street number found. Give the full address, or set address_unknown_reason")
    photos = _check_images(payload.get("photos"), "photos", errors)
    floor_plans = _check_images(payload.get("floor_plans"), "floor_plans", errors)
    if stop_early and not errors:
        values = derive.values_for(facts, key, cfg)
        ruled_out = gate.known_to_fail(cfg, gate.run(cfg, values)) or flags.risk_verdict(
            cfg, flags.run(cfg, dict(rec, facts=facts, raw_text=page_text), dict(values, address_disclosed=True), _comps(store)))
        if not ruled_out:
            errors.append("stop_early: what you recorded does not rule the listing out (no dealbreaker is missed and there "
                          "is no high-risk flag), so read the rest of the page and hand in the full capture")
    if not stop_early and not photos and facts.get("photo_count") != 0 \
            and len(str(payload.get("photos_unavailable_reason") or "").strip()) < 5:
        errors.append("photos: list the listing's photo URLs in order, or set photos_unavailable_reason")
    if errors:
        raise ScoutError(errors, hint="Nothing was saved. Fix these and send the file again. "
                                      "Never fill a gap with a guess: use `unknown`.")

    first_reading = rec.get("facts") is None
    if rec.get("rubric") and rec.get("facts") != facts:
        # The agent's one-liner and questions were written about the old facts.
        rec["rubric"]["headline"], rec["rubric"]["questions"] = None, []
    rec.update({"facts": facts, "provenance": provenance, "unknown": unknown, "raw_text": page_text, "refresh": None,
                "partial": stop_early,
                "photos": photos, "floor_plans": floor_plans,
                "photos_unavailable_reason": None if photos else payload.get("photos_unavailable_reason"),
                "enriched_at": now_iso(), "enriched_from": {"source": entry["source"], "url": entry["url"]},
                "address_key": key, "address_unknown_reason": None if key else payload["address_unknown_reason"],
                "address": {"raw": addr["raw"], "lat": addr.get("lat"), "lng": addr.get("lng")} if key else None})
    # Reading the page is the best proof there is that the listing is up.
    entry.update({"active": True, "missed_sweeps": 0, "last_seen": now_iso()})
    rec["last_seen"] = now_iso()
    if rec.get("delisted_at"):
        _revive(rec, "page read")
    old_rent = _record_price(rec, entry, facts.get("rent_advertised"), "page")
    if old_rent is not None:
        _event(rec, "price", "%s page: %s -> %s" % (entry["source"], old_rent, facts["rent_advertised"]))
        if (rec.get("last_change") or {}).get("run") != run["id"]:
            rec["last_change"] = {"what": "price_changed", "at": now_iso(), "run": run["id"], "from": old_rent,
                                  "to": facts["rent_advertised"]}
            run["counts"]["price_changed"] += 1
    run["evaluated"] += 1

    # The address is often only on the detail page, so look for twins again now.
    merged_from = None
    for other_id in list(store.listings):
        other = store.listings.get(other_id)      # a merge below may already have removed it
        if other is None or other is rec or address.match(key, other.get("address_key")) != "same":
            continue
        if first_reading and merged_from is None and other.get("facts") is not None:
            _merge_into(store, other, rec, take_page=True)   # the older record keeps its id, scores and history
            merged_from, rec = rec["id"], other
        else:
            _merge_into(store, rec, other)
    _link_possible_duplicates(store, rec)
    judge(cfg, store, rec)
    _touch(store, rec)
    result = {"id": rec["id"], "result": "merged" if merged_from else "gated", "gate": rec["gate"]["result"],
              "gate_fails": gate.fail_text(rec["gate"]) or None, "gate_unknowns": gate.unknown_labels(rec["gate"]),
              "flags": [f["message"] for f in rec["flags"]], "stage": rec["stage"],
              "verdict": rec["verdict"], "reason": rec["verdict_reason"]}
    if merged_from:
        result["merged_from"] = merged_from
        result["note"] = "Same unit as %s, already on file from another listing" % rec["id"]
    return result


# --------------------------------------------------------------------------
# Rubric + verdict
# --------------------------------------------------------------------------

def _pin_images(rec, entry):
    """Keep the address of each picture a score cites, so the evidence still shows what
    the agent saw if the listing's gallery is later reordered or replaced."""
    for item in entry.get("evidence") or []:
        if item["type"] == "photos" and item.get("photos"):
            item["urls"] = [rec["photos"][number - 1]["url"] for number in item["photos"]]
        elif item["type"] == "floor_plan" and rec.get("floor_plans"):
            item["urls"] = [rec["floor_plans"][0]["url"]]


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
        _pin_images(rec, entry)
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
    known = set(cfg.sources) | set(e["source"] for rec in store.listings.values() for e in rec["sources"])
    if source not in known:
        raise ScoutError("Unknown source %r; expected one of %s" % (source, ", ".join(sorted(known))))
    run["blocked"][source] = {"at": now_iso(), "what": what}
    for sweep in run["sweeps"]:
        if sweep["source"] == source and sweep["status"] == "pending":
            sweep["status"] = "blocked"
    return {"blocked": source, "do": "Do not retry or work around it. Carry on with `python3 -m scout next`."}


def coverage_report(cfg, store):
    """Per site: when it was last fully swept and how much of a full sweep is left."""
    rows = {}
    for source in cfg.enabled_sources():
        if cfg.sources[source]["url_style"] == "inbox":
            continue
        cover = store.data.get("coverage", {}).get(source) or {}
        cycle = cover.get("cycle")
        rows[source] = {"last_full_sweep": cover.get("last_full_at"),
                        "full_sweep_in_progress": bool(cycle),
                        "searches_left": sum(1 for s in cycle["sweeps"] if s["status"] == "pending") if cycle else 0}
    return rows


def _close_run(cfg, store, abandoned=False):
    run = store.run
    tally, announce = {}, {verdict.TOUR: [], verdict.WATCH: []}
    if abandoned:
        # Nobody will read this run's summary, so what it found is news for the next one.
        store.data["unreported"] = {"run": run["id"], "touched": list(run["touched"]), "delisted": list(run["delisted"])}
    for rec_id in run["touched"]:
        rec = store.listings.get(rec_id)
        if not rec or not rec.get("verdict"):
            continue
        tally[rec["verdict"]] = tally.get(rec["verdict"], 0) + 1
        if abandoned:
            continue
        # Each listing is announced once per verdict; a re-check that changes nothing is not news.
        if rec["verdict"] in announce and rec.get("announced") != rec["verdict"] and not rec.get("delisted_at"):
            announce[rec["verdict"]].append(rec_id)
        rec["announced"] = rec["verdict"]
    for sweep in run["sweeps"]:
        if sweep["status"] == "pending" and sweep["page"] > 0 and sweep["source"] not in run["blocked"]:
            run["notes"].append("%s still had unread pages when this run's page limit was reached; the next full sweep "
                                "will pick up what was missed" % sweep["key"])
    summary = {
        "id": run["id"], "started_at": run["started_at"], "finished_at": now_iso(), "abandoned": abandoned,
        "resumes": run["resumes"], "discovery": run["discovery"], "counts": run["counts"],
        "evaluated": run["evaluated"], "scored": run["scored"], "touched": run["touched"], "verdicts": tally,
        "announce": announce, "blocked": run["blocked"], "notes": run["notes"], "delisted": run["delisted"],
        "pages_read": run["pages_read"], "coverage": coverage_report(cfg, store),
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
# The human's side: refresh, status, tour feedback, lookup
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
                  and (everything or rec.get("verdict") in SHORTLIST)]
    queued = []
    for rec in chosen:
        if seconds_between(rec.get("enriched_at") or rec["first_seen"], now_iso()) < older_than_days * DAY:
            continue
        if ids:
            _reactivate(rec)   # asked for by name: look even if it seemed gone
        rec["refresh"] = "refresh"   # asked for: read the page again even if a sweep sees the card meanwhile
        rec["stage"] = NEEDS_ENRICH
        queued.append(rec["id"])
    return {"queued": queued, "count": len(queued)}


def overview(cfg, store):
    """Where things stand, without changing anything."""
    live = [rec for rec in store.listings.values() if not rec.get("delisted_at")]
    by_verdict = {}
    for rec in live:
        name = rec.get("verdict") or "waiting"
        by_verdict[name] = by_verdict.get(name, 0) + 1
    stale = []
    for rec in live:
        if rec.get("verdict") in SHORTLIST and rec["status"] != "rejected":
            days = seconds_between(checked_at(rec), now_iso()) / DAY
            stale.append({"id": rec["id"], "verdict": rec["verdict"], "days_since_checked": round(days, 1)})
    runs = store.data["runs"]
    return {
        "listings": {"live": len(live), "off_market": len(store.listings) - len(live), "by_verdict": by_verdict},
        "waiting": {"to_open": len(_queue(cfg, store, NEEDS_ENRICH)), "to_score": len(_queue(cfg, store, NEEDS_SCORE))},
        "shortlist_freshness": sorted(stale, key=lambda row: -row["days_since_checked"]),
        "coverage": coverage_report(cfg, store),
        "open_run": store.run["id"] if store.run else None,
        "last_run": dict((k, runs[-1][k]) for k in ("id", "finished_at", "evaluated", "scored", "blocked")) if runs else None,
        "tours_recorded": sum(1 for rec in store.listings.values() if rec["feedback"]),
    }


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
    if status == "active":
        _reactivate(rec)
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
