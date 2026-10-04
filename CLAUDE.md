# CLAUDE.md

Rental Scout: an agent-driven rental search. Claude browses and judges; the Python package in
`scout/` does everything that must be repeatable.

## If you are here to run the scout for a user

Read `agent/RUNBOOK.md` and follow it. If there is no `scout.config.yaml`, start with
`agent/SETUP_INTERVIEW.md`. The hard rules in the runbook are not negotiable: never contact anyone
or submit anything, never enter credentials, never invent a fact, stop on any block page or CAPTCHA.

## If you are here to change the code

- Python 3.8+, standard library only. The user installs nothing, so add no dependencies. PyYAML is
  vendored in `scout/_vendor/yaml`; do not edit it.
- Tests: `python3 -m unittest discover -s tests -t .` Demo report: `python3 -m tests.demo`.
- `docs/DESIGN.md` has the architecture, the record shape and the reasoning behind the rules.

Lines that must hold:

- **Criteria are data.** Anything a user might want to change belongs in the config, not in code or
  in `agent/*.md`. City-specific knowledge belongs in `configs/<city>.template.yaml` and
  `agent/sources/`.
- **Deterministic logic is code; judgment is the rubric only.** Gate, dedupe, risk checks, score
  arithmetic and verdicts never depend on the model.
- **Unknown is a value.** No default may stand in for a missing fact. New facts go in
  `scout/fields.py`; new derived values in `scout/derive.py` return None when an input is missing.
- **`pipeline.judge` recomputes a listing from scratch.** Keep it pure with respect to the record, so
  `rejudge` after a config change always equals a fresh evaluation.
- **Every command saves or fails whole.** Validate the entire payload before changing the store.
- `configs/nyc.example.yaml` is the template with the TODOs answered. Change both together; a test
  compares them. `agent/examples/*.json` are run through the real commands by the tests.
- Fixtures are invented listings. Never commit real listing data, a real `scout.config.yaml` or `data/`.
