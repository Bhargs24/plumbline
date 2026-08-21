"""
Persistence.

JSONL files in a directory are fine for one study and wrong for a product.
Nobody queries "show me every run where the duplicate control failed, across
projects, in the last quarter" against a folder.

SQLite because it needs no server, the whole store is one file an auditor can
be handed, and the query surface is a superset of anything a run directory can
answer. The schema is Postgres-compatible in shape, so moving to a server is a
connection string rather than a rewrite.

Trajectories are stored as JSON blobs with their queryable facets promoted to
columns. Full relational normalisation of every step would buy nothing here:
steps are only ever read as a complete trajectory, never joined across runs.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from ..core.trajectory import Trajectory

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS projects (
    project_id   TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    domain       TEXT NOT NULL,
    policy_name  TEXT,
    created_utc  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    run_id       TEXT PRIMARY KEY,
    project_id   TEXT NOT NULL REFERENCES projects(project_id),
    label        TEXT,
    model        TEXT,
    started_utc  TEXT NOT NULL,
    finished_utc TEXT,
    status       TEXT NOT NULL DEFAULT 'running',
    n_runs       INTEGER DEFAULT 0,
    n_errors     INTEGER DEFAULT 0,
    cost_usd     REAL DEFAULT 0,
    wall_seconds REAL DEFAULT 0,
    config       TEXT
);

CREATE TABLE IF NOT EXISTS trajectories (
    trial_id     TEXT NOT NULL,
    run_id       TEXT NOT NULL REFERENCES runs(run_id),
    arm          TEXT NOT NULL,
    task_id      TEXT NOT NULL,
    perturbation TEXT NOT NULL,
    variant_id   TEXT NOT NULL,
    conformant   INTEGER,
    outcome_ok   INTEGER,
    n_steps      INTEGER,
    failed_steps INTEGER,
    error        TEXT,
    payload      TEXT NOT NULL,
    PRIMARY KEY (run_id, trial_id)
);
CREATE INDEX IF NOT EXISTS ix_traj_run  ON trajectories(run_id, arm);
CREATE INDEX IF NOT EXISTS ix_traj_pert ON trajectories(run_id, perturbation);
CREATE INDEX IF NOT EXISTS ix_traj_task ON trajectories(run_id, task_id);

CREATE TABLE IF NOT EXISTS certificates (
    certificate_id TEXT PRIMARY KEY,
    run_id         TEXT NOT NULL REFERENCES runs(run_id),
    arm            TEXT NOT NULL,
    kind           TEXT NOT NULL,           -- conformance | parity
    grade          TEXT,
    bound          REAL,
    counterpart    TEXT,                    -- the incumbent, for parity
    payload        TEXT NOT NULL,
    created_utc    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_cert_run ON certificates(run_id);

CREATE TABLE IF NOT EXISTS violations (
    violation_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        TEXT NOT NULL REFERENCES runs(run_id),
    arm           TEXT NOT NULL,
    invariant_id  TEXT NOT NULL,
    severity      TEXT NOT NULL,
    perturbation  TEXT NOT NULL,
    task_id       TEXT NOT NULL,
    trial_id      TEXT NOT NULL,
    step_index    INTEGER,
    step_name     TEXT,
    detail        TEXT
);
CREATE INDEX IF NOT EXISTS ix_viol_run ON violations(run_id, severity);
CREATE INDEX IF NOT EXISTS ix_viol_inv ON violations(invariant_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | Path = "plumbline.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    @contextmanager
    def conn(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def _init(self):
        with self.conn() as c:
            c.executescript(SCHEMA)

    # ---- projects -------------------------------------------------------
    def ensure_project(self, name: str, domain: str, policy_name: str = "") -> str:
        with self.conn() as c:
            row = c.execute("SELECT project_id FROM projects WHERE name = ?",
                            (name,)).fetchone()
            if row:
                return row["project_id"]
            pid = f"prj_{uuid.uuid4().hex[:10]}"
            c.execute("INSERT INTO projects VALUES (?,?,?,?,?)",
                      (pid, name, domain, policy_name, _now()))
            return pid

    def projects(self) -> list[dict]:
        with self.conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT p.*, COUNT(r.run_id) AS run_count FROM projects p "
                "LEFT JOIN runs r ON r.project_id = p.project_id "
                "GROUP BY p.project_id ORDER BY p.created_utc DESC")]

    # ---- runs -----------------------------------------------------------
    def start_run(self, project_id: str, label: str, model: str,
                  config: dict | None = None) -> str:
        rid = f"run_{uuid.uuid4().hex[:12]}"
        with self.conn() as c:
            c.execute("INSERT INTO runs (run_id, project_id, label, model, "
                      "started_utc, status, config) VALUES (?,?,?,?,?,?,?)",
                      (rid, project_id, label, model, _now(), "running",
                       json.dumps(config or {})))
        return rid

    def finish_run(self, run_id: str, *, n_runs: int, n_errors: int,
                   cost_usd: float, wall_seconds: float,
                   status: str = "complete") -> None:
        with self.conn() as c:
            c.execute("UPDATE runs SET finished_utc=?, status=?, n_runs=?, "
                      "n_errors=?, cost_usd=?, wall_seconds=? WHERE run_id=?",
                      (_now(), status, n_runs, n_errors, cost_usd,
                       wall_seconds, run_id))

    def runs(self, project_id: str | None = None, limit: int = 100) -> list[dict]:
        sql = ("SELECT r.*, p.name AS project_name, p.domain FROM runs r "
               "JOIN projects p ON p.project_id = r.project_id ")
        args: tuple = ()
        if project_id:
            sql += "WHERE r.project_id = ? "
            args = (project_id,)
        sql += "ORDER BY r.started_utc DESC LIMIT ?"
        with self.conn() as c:
            return [dict(r) for r in c.execute(sql, args + (limit,))]

    def run(self, run_id: str) -> dict | None:
        with self.conn() as c:
            row = c.execute(
                "SELECT r.*, p.name AS project_name, p.domain, p.policy_name "
                "FROM runs r JOIN projects p ON p.project_id = r.project_id "
                "WHERE r.run_id = ?", (run_id,)).fetchone()
            return dict(row) if row else None

    # ---- trajectories ---------------------------------------------------
    def save_trajectories(self, run_id: str, trajectories: list[Trajectory],
                          conformant: dict | None = None,
                          outcome_ok: dict | None = None) -> None:
        conformant = conformant or {}
        outcome_ok = outcome_ok or {}
        rows = []
        for t in trajectories:
            rows.append((
                t.trial_id, run_id, t.arm, t.task_id, t.perturbation, t.variant_id,
                int(bool(conformant.get(t.trial_id))) if t.trial_id in conformant else None,
                int(bool(outcome_ok.get(t.trial_id))) if t.trial_id in outcome_ok else None,
                len(t.steps), sum(1 for s in t.steps if s.failed), t.error,
                json.dumps(t.to_dict(), default=str)))
        with self.conn() as c:
            c.executemany(
                "INSERT OR REPLACE INTO trajectories (trial_id, run_id, arm, "
                "task_id, perturbation, variant_id, conformant, outcome_ok, "
                "n_steps, failed_steps, error, payload) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)

    def trajectories(self, run_id: str, *, arm: str | None = None,
                     perturbation: str | None = None, task_id: str | None = None,
                     only_failing: bool = False, limit: int = 500) -> list[dict]:
        sql = ("SELECT trial_id, arm, task_id, perturbation, variant_id, "
               "conformant, outcome_ok, n_steps, failed_steps, error "
               "FROM trajectories WHERE run_id = ?")
        args: list = [run_id]
        for col, val in (("arm", arm), ("perturbation", perturbation),
                         ("task_id", task_id)):
            if val:
                sql += f" AND {col} = ?"
                args.append(val)
        if only_failing:
            sql += " AND (conformant = 0 OR outcome_ok = 0 OR error IS NOT NULL)"
        sql += " ORDER BY arm, task_id, variant_id LIMIT ?"
        args.append(limit)
        with self.conn() as c:
            return [dict(r) for r in c.execute(sql, args)]

    def trajectory(self, run_id: str, trial_id: str) -> Trajectory | None:
        with self.conn() as c:
            row = c.execute("SELECT payload FROM trajectories WHERE run_id=? "
                            "AND trial_id=?", (run_id, trial_id)).fetchone()
        return Trajectory.from_dict(json.loads(row["payload"])) if row else None

    def all_trajectories(self, run_id: str) -> list[Trajectory]:
        with self.conn() as c:
            rows = c.execute("SELECT payload FROM trajectories WHERE run_id=?",
                             (run_id,)).fetchall()
        return [Trajectory.from_dict(json.loads(r["payload"])) for r in rows]

    # ---- certificates ---------------------------------------------------
    def save_certificate(self, run_id: str, arm: str, kind: str, payload: dict,
                         grade: str = "", bound: float = 0.0,
                         counterpart: str = "") -> str:
        cid = f"cert_{uuid.uuid4().hex[:12]}"
        with self.conn() as c:
            c.execute("INSERT INTO certificates VALUES (?,?,?,?,?,?,?,?,?)",
                      (cid, run_id, arm, kind, grade, bound, counterpart,
                       json.dumps(payload, default=str), _now()))
        return cid

    def certificates(self, run_id: str) -> list[dict]:
        with self.conn() as c:
            out = []
            for r in c.execute("SELECT * FROM certificates WHERE run_id=? "
                               "ORDER BY kind, arm", (run_id,)):
                d = dict(r)
                d["payload"] = json.loads(d["payload"])
                out.append(d)
            return out

    # ---- violations -----------------------------------------------------
    def save_violations(self, run_id: str, rows: list[dict]) -> None:
        with self.conn() as c:
            c.executemany(
                "INSERT INTO violations (run_id, arm, invariant_id, severity, "
                "perturbation, task_id, trial_id, step_index, step_name, detail) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                [(run_id, r["arm"], r["invariant_id"], r["severity"],
                  r["perturbation"], r["task_id"], r["trial_id"],
                  r.get("step_index"), r.get("step_name"), r.get("detail"))
                 for r in rows])

    def violation_summary(self, run_id: str) -> list[dict]:
        """Grouped for the dashboard: what broke, how badly, how often, where."""
        with self.conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT arm, invariant_id, severity, COUNT(*) AS occurrences, "
                "COUNT(DISTINCT task_id) AS tasks, "
                "GROUP_CONCAT(DISTINCT perturbation) AS perturbations, "
                "MIN(detail) AS example "
                "FROM violations WHERE run_id=? "
                "GROUP BY arm, invariant_id, severity "
                "ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
                "WHEN 'medium' THEN 2 ELSE 3 END, occurrences DESC", (run_id,))]

    def failing_invariants_across_runs(self, project_id: str,
                                       limit: int = 20) -> list[dict]:
        """Which controls fail most often across the project's history.

        This is the query a folder of JSONL cannot answer, and the reason a
        store exists at all.
        """
        with self.conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT v.invariant_id, v.severity, COUNT(*) AS occurrences, "
                "COUNT(DISTINCT v.run_id) AS runs_affected "
                "FROM violations v JOIN runs r ON r.run_id = v.run_id "
                "WHERE r.project_id = ? GROUP BY v.invariant_id, v.severity "
                "ORDER BY occurrences DESC LIMIT ?", (project_id, limit))]

    def trend(self, project_id: str, arm: str | None = None) -> list[dict]:
        """Certified bound over time, for regression detection."""
        sql = ("SELECT r.run_id, r.label, r.started_utc, c.arm, c.kind, "
               "c.grade, c.bound FROM certificates c "
               "JOIN runs r ON r.run_id = c.run_id "
               "WHERE r.project_id = ? AND c.kind = 'conformance'")
        args: list = [project_id]
        if arm:
            sql += " AND c.arm = ?"
            args.append(arm)
        sql += " ORDER BY r.started_utc"
        with self.conn() as c:
            return [dict(r) for r in c.execute(sql, args)]
