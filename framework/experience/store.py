#!/usr/bin/env python3
"""
SHIELD experience store (step S3.1) -- the memory the learner learns from.

A tiny SQLite log of every recovery decision + its measured outcome, so the S3.2
learner can periodically re-fit the Resolver (and hold out a validation set to
catch forgetting). One row per recovery:

    ts          -- wall clock (epoch seconds)
    topology    -- which topology the fault happened on (e.g. 'mesh')
    fault       -- the localized failed element (e.g. 's1-s2')
    state       -- observed link-state features at decision time (JSON)
    candidates  -- the k candidate paths considered (JSON)
    chosen      -- the path SHIELD installed (JSON)
    outcome     -- measured post-recovery QoS: throughput/post_loss/recovery_time (JSON)
    reward      -- scalar from reward.compute_reward(outcome) (or caller-supplied)

The JSON columns must be given JSON-serializable structures (lists/dicts/str/num).
Convert frozenset/tuple link keys to strings before storing (see stringify_state).

Design: dependency-free (stdlib sqlite3 + json). `db_path=':memory:'` for tests.
"""
import json
import os
import sqlite3
import sys
import time

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from framework.experience.reward import compute_reward

_SCHEMA = """
CREATE TABLE IF NOT EXISTS recoveries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,
    topology    TEXT,
    fault       TEXT,
    state       TEXT,
    candidates  TEXT,
    chosen      TEXT,
    outcome     TEXT,
    reward      REAL
);
"""

_JSON_COLS = ('state', 'candidates', 'chosen', 'outcome')


def stringify_state(link_state):
    """Convert a {frozenset({a,b}): value} link-state map to JSON-safe {'a-b': value}."""
    out = {}
    for k, v in (link_state or {}).items():
        if isinstance(k, (frozenset, set, tuple, list)):
            out['-'.join(sorted(map(str, k)))] = v
        else:
            out[str(k)] = v
    return out


class ExperienceStore:
    def __init__(self, db_path=':memory:'):
        self.db_path = db_path
        if db_path != ':memory:':
            d = os.path.dirname(db_path)
            if d:
                os.makedirs(d, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(_SCHEMA)
        self.conn.commit()

    # -- write ---------------------------------------------------------------
    def record(self, topology, fault, state, candidates, chosen, outcome,
               reward=None, ts=None):
        """Insert one recovery record; returns its row id. Reward is computed from
        `outcome` when not supplied."""
        if reward is None:
            reward = compute_reward(outcome)
        row = (float(ts if ts is not None else time.time()),
               topology, fault,
               json.dumps(state), json.dumps(candidates),
               json.dumps(chosen), json.dumps(outcome), float(reward))
        cur = self.conn.execute(
            "INSERT INTO recoveries "
            "(ts, topology, fault, state, candidates, chosen, outcome, reward) "
            "VALUES (?,?,?,?,?,?,?,?)", row)
        self.conn.commit()
        return cur.lastrowid

    # -- read ----------------------------------------------------------------
    @staticmethod
    def _to_dict(r):
        d = dict(r)
        for c in _JSON_COLS:
            d[c] = json.loads(d[c]) if d[c] is not None else None
        return d

    def get(self, row_id):
        r = self.conn.execute("SELECT * FROM recoveries WHERE id=?",
                              (row_id,)).fetchone()
        return self._to_dict(r) if r else None

    def all(self):
        return [self._to_dict(r) for r in
                self.conn.execute("SELECT * FROM recoveries ORDER BY id")]

    def recent(self, n):
        rows = self.conn.execute(
            "SELECT * FROM recoveries ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        return [self._to_dict(r) for r in reversed(rows)]

    def by_fault(self, fault):
        return [self._to_dict(r) for r in self.conn.execute(
            "SELECT * FROM recoveries WHERE fault=? ORDER BY id", (fault,))]

    def count(self):
        return self.conn.execute("SELECT COUNT(*) FROM recoveries").fetchone()[0]

    def mean_reward(self):
        v = self.conn.execute("SELECT AVG(reward) FROM recoveries").fetchone()[0]
        return v if v is not None else 0.0

    def close(self):
        self.conn.close()


if __name__ == '__main__':
    # Offline demo: write two recoveries and read them back.
    s = ExperienceStore()
    s.record('mesh', 's1-s2',
             state=stringify_state({frozenset({'s1', 's3'}): {'bw': 2, 'loss': 0.0}}),
             candidates=[['s1', 's3', 's4', 's6'], ['s1', 's5', 's7', 's6']],
             chosen=['s1', 's5', 's7', 's6'],
             outcome={'throughput': 17.4, 'post_loss': 0.0, 'recovery_time': 0.25})
    s.record('mesh', 's1-s2',
             state=stringify_state({}),
             candidates=[['s1', 's3', 's4', 's6']],
             chosen=['s1', 's3', 's4', 's6'],
             outcome={'throughput': 0.8, 'post_loss': 50.0, 'recovery_time': 0.25})
    print("count:", s.count(), "mean_reward:", round(s.mean_reward(), 2))
    print("row 1:", s.get(1))
