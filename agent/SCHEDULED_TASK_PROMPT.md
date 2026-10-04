# Scheduling

Schedule only after one run by hand has gone well.

| What | How often | Prompt to paste |
| --- | --- | --- |
| Daily run: find and judge new listings | Once a day; morning catches most new listings | `agent/prompts/daily_run.txt` |
| Shortlist refresh: are my TOURs and WATCHes still up, same price? | Optional. Before a day of tours, or every few days | `agent/prompts/refresh.txt` |

In the Claude desktop app, create a scheduled task pointed at this folder and paste the contents of the
prompt file. The prompts are deliberately short: criteria live in `scout.config.yaml` and the method
lives in `agent/`, so there is nothing in them to keep up to date.

The daily run already re-opens any shortlisted listing that no sweep has seen for
`limits.recheck_after_days`, so the separate refresh is a convenience, not a requirement.

If you use the Claude Code command line instead, `scripts/run.sh` and `scripts/refresh.sh` do the same
things and can be put in cron or launchd.
