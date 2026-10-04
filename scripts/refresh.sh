#!/usr/bin/env bash
# Refresh listings already on file, without looking for new ones: still up? same rent? new photos?
# Pages are re-read through your browser like any other, at the same gentle pace, and at most
# limits.max_listings_per_run per go. Run it again to continue a long list.
#
# Usage:  scripts/refresh.sh                      every live TOUR and WATCH
#         scripts/refresh.sh --all                every live listing that has been opened before
#         scripts/refresh.sh --older-than-days 2  skip anything read in the last 2 days
#         scripts/refresh.sh <id> [<id> ...]      just these (python3 -m scout find "<address>" gives ids)
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

echo "==> 1/3 check the config"
check_config

echo "==> 2/3 queue listings for a fresh look"
if ! queued="$(scout refresh "$@")"; then
  echo "$queued"    # the scout explains itself on stdout
  exit 1
fi
echo "$queued"
if echo "$queued" | grep -q '"count": 0'; then
  exit 0
fi

echo "==> 3/3 re-read the pages"
hand_to_claude agent/prompts/refresh.txt "The listings are already queued, so skip the \`python3 -m scout refresh\` step."
open_report
