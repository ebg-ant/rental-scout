"""Command line. Every command prints JSON (or markdown where noted) and exits
0 on success, 2 on a problem the caller can fix."""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import calibrate, config, pipeline, report, summary
from .explain import explain
from .store import opened
from .util import ScoutError, read_json_input


def _print(value):
    print(json.dumps(value, indent=2, ensure_ascii=False))


def _write(folder, name, text):
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


def _write_outputs(cfg, store):
    """report.html plus a dated summary, both regenerated from the store."""
    paths = {"report": _write(store.dir, "report.html", report.build(cfg, store))}
    runs = store.data["runs"]
    if runs:
        text = summary.build(store)
        paths["summary"] = _write(os.path.join(store.dir, "summaries"),
                                  "summary-%s.md" % runs[-1]["id"].replace("run-", ""), text)
    return paths


def cmd_validate(args):
    cfg, warnings = config.load(args.config)
    if args.json:
        _print({"ok": True, "warnings": warnings})
        return
    print("Config OK: %s\n" % cfg.path)
    print(explain(cfg))
    for warning in warnings:
        print("\nWarning: %s" % warning)


def cmd_fields(args):
    try:
        registry = config.load(args.config)[0].registry
    except ScoutError:
        from .fields import build_registry
        registry = build_registry()
    _print(dict((name, {"type": field.describe(), "what": field.help,
                        "calculated": field.derived_from is not None}) for name, field in registry.items()))


def _with_store(args, action, write=True):
    cfg, warnings = config.load(args.config)
    with opened(args.data, write=write) as store:
        return cfg, warnings, action(cfg, store)


def cmd_start(args):
    _, warnings, result = _with_store(args, lambda cfg, store: pipeline.start(cfg, store, sweep=not args.no_sweep))
    if warnings:
        result["config_warnings"] = warnings
    result["do"] = "Run `python3 -m scout next` and do what it says. Repeat until it says finish."
    _print(result)


def cmd_next(args):
    _print(_with_store(args, pipeline.next_task)[2])


def _payload_command(function):
    def run(args):
        payload = read_json_input(args.file)
        _print(dict(_with_store(args, lambda cfg, store: function(cfg, store, payload))[2], ok=True))
    return run


def cmd_blocked(args):
    _print(_with_store(args, lambda cfg, store: pipeline.blocked(cfg, store, args.source, args.what))[2])


def cmd_finish(args):
    def action(cfg, store):
        run = pipeline.finish(cfg, store)
        store.save()
        return run, _write_outputs(cfg, store), summary.build(store)
    run, paths, text = _with_store(args, action)[2]
    print(text)
    print("---\nReport: %s\nSummary: %s" % (paths["report"], paths["summary"]))


def cmd_report(args):
    _print(_with_store(args, _write_outputs, write=False)[2])


def cmd_summary(args):
    print(_with_store(args, lambda cfg, store: summary.build(store), write=False)[2])


def cmd_rejudge(args):
    def action(cfg, store):
        store.backup()
        result = pipeline.rejudge_all(cfg, store)
        store.save()
        result["outputs"] = _write_outputs(cfg, store)
        return result
    _print(_with_store(args, action)[2])


def cmd_refresh(args):
    result = _with_store(args, lambda cfg, store: pipeline.queue_refresh(
        store, args.ids, args.all, args.older_than_days))[2]
    result["do"] = "Run `python3 -m scout start --no-sweep`, then loop on `python3 -m scout next` as usual." \
        if result["count"] else "Nothing to refresh."
    _print(result)


def cmd_find(args):
    _print(_with_store(args, lambda cfg, store: pipeline.find(store, args.text), write=False)[2])


def cmd_show(args):
    _print(_with_store(args, lambda cfg, store: pipeline._get(store, args.id), write=False)[2])


def cmd_status(args):
    def action(cfg, store):
        result = pipeline.set_status(store, args.id, args.status, args.note)
        store.save()
        _write_outputs(cfg, store)
        return result
    _print(_with_store(args, action)[2])


def cmd_feedback(args):
    def action(cfg, store):
        result = pipeline.add_feedback(store, args.id, args.would_rent == "yes", args.why)
        store.save()
        _write_outputs(cfg, store)
        return result
    _print(_with_store(args, action)[2])


def cmd_calibrate(args):
    print(_with_store(args, calibrate.report, write=False)[2])


def build_parser():
    parser = argparse.ArgumentParser(prog="python3 -m scout", description="Rental Scout")
    parser.add_argument("--config", help="path to the config (default scout.config.yaml, or $SCOUT_CONFIG)")
    parser.add_argument("--data", help="folder for the store and reports (default data/, or $SCOUT_DATA)")
    commands = parser.add_subparsers(dest="command")
    commands.required = True

    def add(name, function, help_text):
        sub = commands.add_parser(name, help=help_text)
        sub.set_defaults(function=function)
        return sub

    add("validate", cmd_validate, "check the config and read it back in plain English") \
        .add_argument("--json", action="store_true")
    add("fields", cmd_fields, "list every fact the scout can record or gate on")
    add("start", cmd_start, "open a run, or resume an interrupted one") \
        .add_argument("--no-sweep", action="store_true", help="skip discovery; only judge listings handed in by hand")
    add("next", cmd_next, "what to do now")
    for name, function, text in (("ingest", pipeline.ingest, "hand in search cards from a sweep"),
                                 ("enrich", pipeline.enrich, "hand in facts from a listing page"),
                                 ("score", pipeline.score, "hand in rubric scores")):
        add(name, _payload_command(function), text).add_argument("file", help="JSON file, or - for stdin")
    sub = add("blocked", cmd_blocked, "report a block page or CAPTCHA; stops using that site this run")
    sub.add_argument("source")
    sub.add_argument("what", help="what you saw, e.g. 'press and hold CAPTCHA'")
    add("finish", cmd_finish, "close the run and write the report and summary")
    add("report", cmd_report, "rebuild report.html and the latest summary")
    add("summary", cmd_summary, "print the latest summary")
    add("rejudge", cmd_rejudge, "re-apply the config to everything already seen (no browser)")
    sub = add("refresh", cmd_refresh, "queue listings for a fresh look (default: every live TOUR and WATCH)")
    sub.add_argument("ids", nargs="*", help="specific listing ids")
    sub.add_argument("--all", action="store_true", help="every live listing opened before, not just the shortlist")
    sub.add_argument("--older-than-days", type=float, default=0, help="skip listings read more recently than this")
    add("find", cmd_find, "look up a listing id by address, title or URL").add_argument("text")
    add("show", cmd_show, "print one listing's full record").add_argument("id")
    sub = add("status", cmd_status, "set a listing's status")
    sub.add_argument("id")
    sub.add_argument("status", choices=pipeline.USER_STATUSES)
    sub.add_argument("--note")
    sub = add("feedback", cmd_feedback, "record your own verdict after a tour")
    sub.add_argument("id")
    sub.add_argument("would_rent", choices=["yes", "no"])
    sub.add_argument("why")
    add("calibrate", cmd_calibrate, "compare the scout's verdicts with yours")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.function(args)
        return 0
    except ScoutError as exc:
        _print({"ok": False, "errors": exc.errors, "hint": exc.hint})
        return 2
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    sys.exit(main())
