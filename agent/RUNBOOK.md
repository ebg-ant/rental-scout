# Runbook: how to run the scout

You are the browsing and judging half of Rental Scout. The Python package in `scout/` is the other
half: it remembers, dedupes, gates, does the arithmetic and decides verdicts. You look at web pages
through the user's own logged-in browser, report what you see as JSON, and score the rubric.

Criteria live in `scout.config.yaml`. Never carry criteria in your head or in a prompt: if the user
wants something different, change the config (see "Other things the user may ask").

## Hard rules

1. Never submit a tour request, contact form, application or payment, and never message a landlord
   or broker. You produce shortlists. Every contact is started by the human.
2. Never type credentials. If a site is logged out, stop using it and tell the user.
3. Never invent a fact. If you did not see it, it is unknown, with where you looked.
4. If you hit a block page, CAPTCHA, "unusual traffic" notice or forced login: stop using that site
   at once, run `python3 -m scout blocked <source> "<what you saw>"`, and carry on with the rest.
   Do not retry, solve, refresh around, switch browsers or otherwise work around it.
5. Use the real browser only. No headless fetching, scraping scripts or `curl` against listing sites.
6. Respect `wait_seconds` and the page limits in every task.
7. Do not edit `data/store.json` by hand. Do not do the gate's or the verdict's job yourself.

## 1. Before the first task

Run everything from the folder that contains this repo's `README.md`.

```
python3 --version                 # needs 3.8 or newer; nothing else to install
python3 -m scout validate
```

- No `scout.config.yaml` yet, or validate lists TODOs: do `agent/SETUP_INTERVIEW.md` first.
- Validate reports errors: fix them with the user, do not guess values.

## 2. The loop

```
python3 -m scout start
python3 -m scout next      # do what it says, hand in the result, then ask again
```

Repeat `next` until it returns `"task": "finish"`. It always returns exactly one task:

| task | what to do | guide | hand in with |
| --- | --- | --- | --- |
| `sweep` | read one page of search results into cards | the file named in `read` | `python3 -m scout ingest <file>` |
| `enrich` | open one listing, record its facts | `agent/ENRICH.md` | `python3 -m scout enrich <file>` |
| `score` | score the rubric with evidence | `agent/RUBRIC_SCORING.md` | `python3 -m scout score <file>` |
| `finish` | close the run | below | `python3 -m scout finish` |

Mechanics:

- Write each JSON payload to `data/tmp/<something>.json` and pass the path. Working examples are in
  `agent/examples/`.
- If `wait_seconds` is above 0, wait that long (`sleep N`) before loading the page.
- Keep one tab per site and leave it where it is between tasks. The scout moves between sites so that one
  site's wait is spent reading another, and on a site with only a pager it counts on the tab still being on
  the page you last read.
- A command that exits with `"ok": false` saved nothing. Read `errors`, fix the payload, send it
  again. If the fix would mean making something up, mark it unknown instead.
- The scout decides how far to read each search, when to re-open a listing it already knows, and when
  the run is over. If a decision seems to be yours, ask `next` instead.
- Do not reorder, skip or batch tasks. Everything is saved after each command, so finishing one
  listing before starting the next is what makes a dropped session cheap.

## 3. If a site does not behave as the task expects

Sites change. The scout builds search URLs that carry the price range, the newest-first sort and the
page number, and each task asks you to confirm the page shows them.

- The page ignores the price range or the sort: set them by hand on the page, then read it as usual.
- It happens again on the next task for that site: edit `scout.config.yaml`, set that source's
  `url_style: manual`, and tell the user at the end of the run. From then on the scout gives you the
  user's own search URL and asks you to set the filters and turn the pages by hand.
- The layout differs from the guide in `agent/sources/`: trust the page. The guide describes what to
  collect; where it sits on the page may have moved.
- Photo addresses cannot be read from the page: use `photos_unavailable_reason` and carry on.

None of this is a reason to fetch pages any other way than through the user's browser.

## 4. If the session drops

Start again from step 2. `start` resumes the open run and `next` hands back the task that was in
flight. A run left open for more than 12 hours is closed automatically and a fresh one starts;
listings already queued stay queued.

## 5. Finishing

`python3 -m scout finish` prints the summary and writes:

- `data/report.html`: the interactive shortlist. Tell the user to open it.
- `data/shortlist.csv`: the same listings as a spreadsheet, for Google Sheets or Excel.
- `data/summaries/summary-<date>-<time>.md`: the same summary you just saw.

Then tell the user, briefly: the new TOURs with their one-line reasons and listing links, the new WATCHes
and what would settle each, what changed on listings they already knew about, the counts, and anything under "Worth knowing" (blocks, listings left for next time).
Do not pad it and do not restate PASS listings one by one.

## Other things the user may ask

**"I toured X" / "I loved it" / "not for me because ..."**
`python3 -m scout find "<part of the address>"`, then
`python3 -m scout feedback <id> yes|no "<their reason, in their words>"`.

**"I applied to X" / "cross X off"**
`python3 -m scout status <id> applied|rejected|toured|active --note "..."`. A rejected listing is
never re-opened.

**"Raise my budget" / "dogs too" / "I care more about light"**
Edit `scout.config.yaml`, then `python3 -m scout validate` and read the summary back to them, then
`python3 -m scout rejudge`. That re-applies the gate, weights and verdicts to everything already
seen, with no browsing. Listings that now pass the gate are queued for scoring on the next run.
Rewording a criterion's guidance or anchors queues it for re-scoring; changing a weight does not.

**"What about this listing?" (a pasted URL)**
```
python3 -m scout start --no-sweep
echo '{"source": "manual", "cards": [{"url": "<the url>"}]}' > data/tmp/manual.json
python3 -m scout ingest data/tmp/manual.json
python3 -m scout next        # loop as usual
```

**"Refresh my shortlist" / "Are these still available?"**
```
python3 -m scout refresh            # every live TOUR and WATCH; --all, --older-than-days N or ids also work
python3 -m scout start --no-sweep
python3 -m scout next               # loop as usual
```
Each page is re-read like any other `enrich` task. Report what changed: gone, re-priced, verdict moved.
(The daily run already does this on a timer for anything it has not seen lately; see `refresh` in the config.)

**"Check everything again" / "I think it is missing listings"**
`python3 -m scout start --full` reads every page of every search instead of stopping at the first page
with nothing new. It happens by itself about once a week.

**"Where do things stand?"**
`python3 -m scout overview`: counts, what is waiting, how fresh the shortlist is, and how far each
site has been read.

**"How well is it matching my taste?"**
`python3 -m scout calibrate`. It needs 10 recorded tours. It only suggests; change the config only
if the user agrees.

**"Show me the report again"**
`python3 -m scout report`.
