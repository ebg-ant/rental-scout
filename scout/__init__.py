"""Rental Scout: deterministic core for an agent-driven rental search.

The agent (Claude driving a logged-in browser) does the looking and the judging.
Everything that must be repeatable -- config validation, dedupe, prefilters, the
gate, risk checks, score maths, verdicts, checkpoints, reports -- lives here.
"""

__version__ = "1.0.0"
