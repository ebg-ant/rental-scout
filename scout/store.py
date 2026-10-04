"""One JSON file holds everything the scout knows. All access goes through here.

Writes are atomic (temp file, then rename), so a run killed mid-write leaves the
previous good copy. A lock file stops two runs from overwriting each other.
"""

from __future__ import annotations

import contextlib
import glob
import json
import os
import shutil
import tempfile

from .util import ScoutError, now

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None

DEFAULT_DIR = "data"
KEEP_BACKUPS = 10


def data_dir(path=None):
    return path or os.environ.get("SCOUT_DATA") or DEFAULT_DIR


def empty():
    return {"version": 1, "seq": 0, "judge_hash": None, "listings": {}, "run": None, "runs": []}


class Store(object):
    def __init__(self, directory=None):
        self.dir = data_dir(directory)
        self.path = os.path.join(self.dir, "store.json")
        self.data = empty()

    def load(self):
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as handle:
                try:
                    self.data = json.load(handle)
                except json.JSONDecodeError as exc:
                    raise ScoutError("%s is damaged (%s)" % (self.path, exc),
                                     hint="Copy the newest file from %s/backups over it." % self.dir)
            if self.data.get("version") != 1:
                raise ScoutError("%s was written by a different version of the scout" % self.path)
        return self

    def save(self):
        os.makedirs(self.dir, exist_ok=True)
        handle, temp = tempfile.mkstemp(dir=self.dir, prefix=".store-", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                json.dump(self.data, out, indent=1, sort_keys=True, ensure_ascii=False)
                out.write("\n")
                out.flush()
                os.fsync(out.fileno())
            os.replace(temp, self.path)
        except BaseException:
            if os.path.exists(temp):
                os.unlink(temp)
            raise

    def backup(self):
        if not os.path.exists(self.path):
            return None
        folder = os.path.join(self.dir, "backups")
        os.makedirs(folder, exist_ok=True)
        target = os.path.join(folder, "store-%s.json" % now().strftime("%Y%m%d-%H%M%S"))
        shutil.copyfile(self.path, target)
        for old in sorted(glob.glob(os.path.join(folder, "store-*.json")))[:-KEEP_BACKUPS]:
            os.unlink(old)
        return target

    @property
    def listings(self):
        return self.data["listings"]

    @property
    def run(self):
        return self.data.get("run")

    def next_seq(self):
        self.data["seq"] += 1
        return self.data["seq"]


@contextlib.contextmanager
def opened(directory=None, write=True):
    """Load the store under a lock; save on a clean exit when `write` is set."""
    store = Store(directory)
    os.makedirs(store.dir, exist_ok=True)
    lock = open(os.path.join(store.dir, ".lock"), "w")
    try:
        if fcntl:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise ScoutError("Another scout command is running against %s" % store.dir,
                                 hint="Wait for it to finish, then try again.")
        store.load()
        yield store
        if write:
            store.save()
    finally:
        lock.close()
