#!/usr/bin/env bash
# Pack a clean copy to hand to someone else: code, templates, guides and tests, and nothing personal.
# Only the files named below are packed, so your scout.config.yaml, your data (wherever it lives), local
# settings and stray backups cannot slip in. They start from their own setup interview.
#
# Usage:  scripts/package.sh [output.zip]        (default: dist/rental-scout.zip)
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

CONTENTS=(README.md CLAUDE.md .gitignore .claude/settings.json agent configs docs scout scripts tests)

out="${1:-dist/rental-scout.zip}"
case "$out" in /*) ;; *) out="$ROOT/$out" ;; esac
mkdir -p "$(dirname "$out")"
rm -f "$out"

echo "==> 1/2 run the tests"
"$PY" -m unittest discover -s tests -t . 2>&1 | tail -3

echo "==> 2/2 zip"
name="$(basename "$ROOT")"
paths=()
for item in "${CONTENTS[@]}"; do paths+=("$name/$item"); done
( cd "$ROOT/.." && zip -qr "$out" "${paths[@]}" -x "*/__pycache__/*" "*.pyc" "*/.DS_Store" "*.bak" "*~" )
echo "==> $out"
echo "Send it with: unzip it, open the folder in the Claude desktop app, and say"
echo '  "Read agent/SETUP_INTERVIEW.md and set up the rental scout with me."'
