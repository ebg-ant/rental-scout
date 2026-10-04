#!/usr/bin/env bash
# Rebuild everything that can be rebuilt without a browser. Use it after editing scout.config.yaml
# or updating the code: it re-applies the gate, weights, risk checks and verdicts to every listing
# already seen, then rewrites the report and the latest summary. Backs up the store first.
#
# Usage:  scripts/rebuild.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

echo "==> 1/2 check the config"
scout validate

echo
echo "==> 2/2 re-judge every stored listing and rewrite the report"
scout rejudge
open_report
