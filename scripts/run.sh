#!/usr/bin/env bash
# The full daily run: sweep every enabled site, judge what is new or changed, write the report.
# Safe to re-run: an interrupted run resumes where it stopped.
#
# Usage:  scripts/run.sh            (works from any directory)
#         OPEN_REPORT=0 scripts/run.sh     do not open the report afterwards (for cron)
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

echo "==> 1/2 check the config"
check_config

echo "==> 2/2 run"
hand_to_claude agent/prompts/daily_run.txt
open_report
