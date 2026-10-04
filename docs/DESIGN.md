# Design

## The split

| Claude (the agent) | `scout/` (the code) |
| --- | --- |
| Drives the user's logged-in browser | Validates the config |
| Reads search results into cards | Dedupes across sites, diffs against yesterday |
| Reads a listing page into facts, each with where it was seen | Prefilters, gates, runs risk and cost checks |
| Scores the rubric, citing evidence | Checks the evidence, does the arithmetic, decides the verdict |
| Talks to the user | Checkpoints, resumes, writes the report and summary |

The agent asks one question, `python3 -m scout next`, and gets one task back. All state is in
`data/store.json`, saved atomically after every command.

```
start -> [sweep -> ingest]* -> [enrich -> (score)]* -> finish
            |                     |          |
        dedupe, diff,          gate, risk   verdict
        prefilter, card check   checks
```

## Modules

| File | Job |
| --- | --- |
| `config.py`, `explain.py` | Load, validate (all errors at once, plain English); read back |
| `fields.py` | The vocabulary of facts; typos are errors |
| `address.py` | Canonical address keys; `same` / `maybe` / `different` |
| `derive.py` | Net and gross rent, all-in cost, target area. None when an input is missing |
| `prefilter.py` | Text rules on the search card |
| `gate.py` | Three-valued rules, `unless`, `on_unknown`, dealbreakers, search-card check |
| `flags.py` | Scam, cost and information flags |
| `rubric.py` | Validates scores and evidence; weighted score, floor, ceiling |
| `verdict.py` | One verdict, one reason |
| `sweeps.py` | Price-band URLs; splits a band when a site caps results |
| `pipeline.py` | The state machine and everything that touches a record |
| `store.py` | Atomic JSON store, lock, backups |
| `summary.py`, `report.py`, `calibrate.py` | Outputs |

## Decisions worth knowing

**Prefilters read the search card only.** They exist to save browser time. Once a page has been read,
recorded facts decide, so "furnished roof deck" in a description cannot sink a listing.

**The search card can reject only on rules marked `trust_card`,** only when that miss alone would be
fatal, and for numbers only beyond `card_margin_pct`. Cards hide and misstate facts; a card showing
gross rent 8% over the ceiling may net out under it.

**The gate is three-valued.** Each rule is pass, fail or unknown, and `on_unknown` says what unknown
means for that rule: `fail`, `pass`, or `flag` (keep the listing, but it cannot be a TOUR until a
human settles it). `A unless B` passes if either holds, fails only if both fail, and is otherwise unknown.

**Facts need provenance.** Every fact the gate or the report depends on must arrive with where it was
seen, or be listed as unknown with where the agent looked. Quotes are checked against the saved page
text, for facts and for rubric evidence alike. This is the mechanical half of "never invent a fact".

**Unknown rubric criteria do not count as zero.** The score is the weighted mean of what could be
verified. `floor` and `ceiling` show the range if the unknowns were settled, and
`verdicts.tour.max_unknown_weight` stops a thinly evidenced listing from being a TOUR. A low score
that could still reach TOUR level stays on WATCH.

**"Too cheap" has a definition.** `risk.price_floor_pct` times a typical rent for that bedroom count,
taken from `risk.reference_rents`, or else from the median of listings seen once there are
`risk.min_comps` of them. With neither, the check stays silent.

**Dedupe is conservative.** Same site and id; or same door and same stated unit on any site; or, on one
site, the same first photo or title at the same rent (a repost). The same door with a unit missing is
only linked as a possible duplicate: big buildings list many similar units. Addresses found on the
detail page trigger a second look and a merge.

**What re-enters the queue.** New listings; price changes and relistings, but only if the listing is
still in the running (a price drop on a no-cats building is recorded, not re-opened); listings a
config change let through the gate; criteria whose wording changed; and shortlisted listings no sweep
has seen for `limits.recheck_after_days`.

**Delisting needs proof of absence.** A listing is marked off-market when its page is gone, or when it is
missing from `limits.delist_after_missed_sweeps` sweeps that fully covered that site. Blocked, capped
or early-stopped sweeps prove nothing and are not counted.

**Config changes apply retroactively.** `start` compares a hash of the config with the one last used and
re-judges every stored listing if it changed. Weights re-rank without re-scoring; each criterion
carries a hash of its guidance and anchors, so only reworded criteria are re-scored.

**Evidence is one click away.** Enrich saves the listing's photo and floor-plan URLs in gallery order, so
photo evidence is a list of numbers and the report shows those pictures beside the rationale.
Satellite, Street View and map evidence must be URLs of the exact view. Quotes become
`#:~:text=` links that scroll the listing page to the words. `helper_links` from the config appear as
buttons on every listing. `report.html#<id>` opens one scorecard, and the summary links to it.

**Images are linked, not copied.** The report loads photos from the listing sites' image servers, with no
referrer, and shows a placeholder if one is gone. It also accepts local paths under `photos/` next to
the report (the demo uses this), so a local cache could be added without touching the page.

**Refreshing is the same path as reading.** There is no separate scraper. `scout refresh` marks listings
for a fresh look and they flow through `enrich` again, in the browser, under the same limits.
`scripts/rebuild.sh` covers everything that needs no browser.

**Blocks end the site's day.** `blocked` marks the source unusable for the rest of the run. There is no
retry path.

## A listing record

```
id, seq                      stable id from the first site it was seen on
card                         latest search-card data
address, address_key         raw text; {street, unit, zip, borough} or null
sources[]                    {source, source_id, url, rent, first_seen, last_seen, active, missed_sweeps}
price_history[]              {date, source, rent}
facts, provenance, unknown   what the page said, where, and what could not be found
raw_text                     the page text quotes are checked against
photos[], floor_plans[]      {url, caption} in gallery order; evidence points at photos by number
prefilter, card_reject       why it was dropped before being opened, if it was
derived                      rent_gross, rent_net_effective, monthly_all_in, in_target_area, ...
gate                         {result, fails, unknowns, rules[{id, status, counted_as, actual, expected}]}
flags[]                      {id, kind: risk|cost|info|good, severity, message}
rubric                       {scores{id: {score, rationale, evidence[], unknown_reason, hash}},
                              score, floor, ceiling, unknown_weight, headline, questions[]}
verdict, verdict_reason      TOUR | WATCH | PASS | PASS (risk) | null while pending
stage, refresh, queue_reason needs_enrich | needs_score | done, and why it is queued
status, delisted_at          active | delisted | toured | applied | rejected
feedback[]                   the human's verdict after a tour
history[]                    price, verdict, relist, merge and status events
```

`derived`, `gate`, `flags`, the rubric totals, `verdict` and `stage` are all recomputed by
`pipeline.judge` from `card`, `facts`, `rubric.scores` and the config. They are stored for the record
and for the report, not as a source of truth.

## Adapting to another city

1. Copy `configs/nyc.template.yaml`. Replace sources, neighborhood and street aliases, helper links,
   `costs` (these encode New York rules) and any rubric anchors that assume New York housing.
2. Add `agent/sources/<source>.md` for any new site. If its search URL can carry a price range, add a
   rewriter in `sweeps.py`; otherwise use `url_style: manual` and the agent sets the filter by hand.
3. `address.py` knows US street grammar plus Queens-style hyphenated house numbers and the five
   boroughs. Elsewhere in the US it should work as it is.

## Where this came from

A Seattle version ran for a week in July 2026 as one long prompt plus a Google Doc log. What went wrong
there shaped this design:

| Then | Now |
| --- | --- |
| Gate decisions made on search-card data that was missing or wrong | Gate runs on detail-page facts; the card can reject only on trusted fields with a margin |
| Undisclosed addresses blocked the exposure check | Explicit unknown path: null score, reason, WATCH, and a question for the human |
| Photos from 2006 | `photos_recency` fact and a `stale_photos` flag |
| Furnished, short-term and "pets case by case" ate evaluation slots | Prefilters before any page is opened; `case_by_case` is its own value and not a yes |
| Result caps forced improvised price splits | Built-in adaptive band splitting |
| Free-text log nobody could re-score | Structured store; prose summary generated from it |
| Dropped browser sessions lost work | Checkpoint after every command; `next` resumes |
| "Value crater" with no threshold | `risk.price_floor_pct` against a stated reference |
| Criteria loosened mid-search by editing a prompt | Edit the config; everything already seen is re-judged |
