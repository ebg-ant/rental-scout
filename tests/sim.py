"""A scripted stand-in for the browsing agent.

It does what agent/RUNBOOK.md tells Claude to do -- call `next`, do the task, hand
in the result -- but reads its "web pages" from a fixture. Each step opens the
store afresh, exactly like a separate command-line call.
"""

from __future__ import annotations

import datetime as dt
import os

from scout import config, pipeline
from scout.store import opened


class Clock(object):
    """Drives SCOUT_NOW so runs are repeatable and throttles can be observed."""

    def __init__(self, start):
        self.now = dt.datetime.fromisoformat(start).replace(tzinfo=dt.timezone.utc)
        self.apply()

    def apply(self):
        os.environ["SCOUT_NOW"] = self.now.isoformat()

    def tick(self, seconds=30):
        self.now += dt.timedelta(seconds=seconds)
        self.apply()

    def jump_to(self, iso):
        self.now = dt.datetime.fromisoformat(iso).replace(tzinfo=dt.timezone.utc)
        self.apply()


class ScriptedAgent(object):
    def __init__(self, config_path, data_dir, clock):
        self.cfg, _ = config.load(config_path)
        self.data_dir = data_dir
        self.clock = clock
        self.log = []

    def call(self, function, *args):
        with opened(self.data_dir) as store:
            return function(self.cfg, store, *args)

    def run(self, day, stop_after=None):
        """Play one run from a fixture day. `stop_after` simulates a dropped session."""
        started = self.call(pipeline.start)
        steps = 0
        while True:
            if stop_after is not None and steps >= stop_after:
                return {"started": started, "interrupted": True}
            task = self.call(pipeline.next_task)
            self.log.append(task)
            self.clock.tick(max(30, task.get("wait_seconds", 0)))
            steps += 1
            kind = task["task"]
            if kind == "sweep":
                if task["source"] in day.get("blocks", {}):
                    self.call(pipeline.blocked, task["source"], day["blocks"][task["source"]])
                    continue
                payload = dict(day.get("sweeps", {}).get(task["sweep"], {"cards": []}))
                payload["sweep"] = task["sweep"]
                self.call(pipeline.ingest, payload)
            elif kind == "enrich":
                self.call(pipeline.enrich, day["pages"][task["id"]])
            elif kind == "score":
                self.call(pipeline.score, day["scores"][task["id"]])
            elif kind == "finish":
                return {"started": started, "finished": self.call(pipeline.finish), "why": task["why"]}
            else:
                raise AssertionError("unexpected task %r" % task)

    def tasks(self, kind):
        return [task for task in self.log if task["task"] == kind]
