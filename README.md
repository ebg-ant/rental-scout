# Rental Scout

Tell Claude what you want in an apartment once. Each day it reads the new listings in your own
browser, throws out the junk, checks your must-haves, scores what is left, and gives you a short
list: **TOUR**, **WATCH** or **PASS**, each with a reason and links to the evidence.

It ships set up for New York City (StreetEasy, Zillow, Craigslist). It never contacts anyone,
fills in a form or applies for anything. That part stays yours.

## What you need

- The Claude desktop app, with Claude able to control your Chrome (the Claude in Chrome extension).
- Chrome logged in to StreetEasy and Zillow, as you.
- This folder on your computer: on GitHub, press **Code**, then **Download ZIP**, and unzip it.

There is nothing to install. If Claude says Python is missing, let it tell you how to add it.

## 1. Set it up (about 10 minutes, once)

Open this folder in Claude and say:

> Read agent/SETUP_INTERVIEW.md and set up the rental scout with me.

Claude asks about budget, bedrooms, neighborhoods, commute, pets, must-haves and what matters most
to you. It writes your answers to `scout.config.yaml` and reads them back for you to confirm.

## 2. Run it once by hand

> Read agent/RUNBOOK.md and run the rental scout.

Leave Chrome open while it works. A run opens at most 10 listings and takes up to an hour, because
it moves at a human pace on purpose. When it finishes, open **`data/report.html`** (double-click
it). Everything is there: photos and floor plans, scores with the reasoning, what could not be
verified, warnings about fees and scams, price history, and questions worth asking. Every claim links
to its evidence: the photo it is based on, the exact map or Street View, the words on the listing page.

If the connection drops partway, say the same thing again. It picks up where it stopped.

## 3. Then schedule it

Once a run has gone well, set up a daily scheduled task in Claude using the prompt in
`agent/SCHEDULED_TASK_PROMPT.md`. From the second run on, it only looks at listings that are new,
re-priced or re-listed.

## Talking to it

| Say | What happens |
| --- | --- |
| "Raise my budget to $4,500" / "I got a dog" / "Light matters more than commute" | Claude edits your config and re-ranks everything already seen, without re-browsing |
| "What about this one?" plus a link | It evaluates that listing on its own |
| "I toured 88 Berry St. Loved it, because ..." | Recorded. After 10 tours, ask "how well is the scout matching my taste?" |
| "Cross off 212 E 7th" | It will not come back |
| "Refresh my shortlist" | It re-opens your TOURs and WATCHes: still up? same rent? new photos? |

## How to read a verdict

- **TOUR**: passes every must-have, scores 75 or more, and nothing important is unverified.
- **WATCH**: scores 60 to 74, or would be a TOUR except for one soft miss or something nobody could
  confirm. The report says exactly what to ask.
- **PASS**: everything else, always with the reason.
- **PASS (risk)**: looks like a scam or an unreliable listing.

"Unknown" is a real answer here. If the listing does not say whether cats are allowed, the scout
says so instead of guessing.

## Privacy

Your criteria (`scout.config.yaml`) and everything the scout has seen (`data/`) stay on your
computer and are excluded from git.

## Scripts, if you like a terminal

| Script | What it does |
| --- | --- |
| `scripts/run.sh` | The full daily run |
| `scripts/refresh.sh` | Re-check listings already on file (shortlist by default; `--all`, `--older-than-days N`, or ids) |
| `scripts/rebuild.sh` | No browser: re-apply your config to everything seen and rewrite the report |

The first two hand the browsing to Claude through the Claude Code command line (`claude --chrome`) if
it is installed. If not, they print the prompt to paste into the desktop app.

## Want to see it first?

```
python3 -m tests.demo
```

builds `data-demo/report.html` from made-up listings.

## For the curious

`docs/DESIGN.md` explains how it works and how to adapt it to another city.
`python3 -m unittest discover -s tests -t .` runs the tests.
The bundled YAML parser in `scout/_vendor/yaml` is PyYAML (MIT licence, included).
