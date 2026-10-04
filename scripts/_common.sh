# Shared by the scripts in this folder. Not meant to be run on its own.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-python3}"

scout() { "$PY" -m scout "$@"; }

# Stop early, showing what is wrong, if the config is missing or invalid.
check_config() {
  if ! scout validate --json >/dev/null; then
    scout validate || true
    exit 1
  fi
}

# Hand a prompt file to Claude, plus an optional extra line. With the Claude Code CLI installed this
# runs it, using your logged-in Chrome. Without it, it prints the prompt to paste into the Claude
# desktop app. Extra CLI flags (for example a permission mode) can be passed in CLAUDE_FLAGS.
hand_to_claude() {
  local prompt
  prompt="$(cat "$1")"
  if [ -n "${2:-}" ]; then
    prompt="$prompt"$'\n\n'"$2"
  fi
  if command -v claude >/dev/null 2>&1; then
    echo "==> handing over to Claude (needs Chrome open and logged in to the listing sites)"
    # shellcheck disable=SC2086
    claude --chrome ${CLAUDE_FLAGS:-} -p "$prompt"
  else
    echo "==> The 'claude' command is not installed. Open this folder in the Claude desktop app and paste:"
    echo
    echo "$prompt"
  fi
}

open_report() {
  local report="${SCOUT_DATA:-data}/report.html"
  [ -f "$report" ] || return 0
  echo "==> report: $report"
  if [ "${OPEN_REPORT:-1}" = "1" ]; then
    if command -v open >/dev/null 2>&1; then open "$report"
    elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$report" >/dev/null 2>&1 || true
    fi
  fi
}
