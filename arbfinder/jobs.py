"""Background scan jobs with SQLite persistence and a status pipeline.

The scan is long-running (30s–2min) and drives Playwright, so it can't run inside
an HTTP request on a hosted server. This runs each scan on a single background
worker thread (Playwright's sync API is created and used entirely within that one
thread), records progress through named stages, and persists jobs + results to
SQLite so any device can poll status and see the latest completed run after a
refresh — even across restarts.
"""

from __future__ import annotations

import json
import logging
import queue
import sqlite3
import threading
import time
import uuid
from pathlib import Path

log = logging.getLogger(__name__)

# Ordered pipeline the UI lights up. "queued"/"done"/"error" are terminal-ish.
STAGES = ["discovering", "searching_retailer", "collecting_prices",
          "matching", "calculating"]


class JobStore:
    """SQLite-backed job queue + status store. One worker, one scan at a time."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()          # a connection per thread
        self._q: queue.Queue[str] = queue.Queue()
        self._runner = None
        self._worker: threading.Thread | None = None
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, timeout=30)
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    def _init_db(self) -> None:
        self._conn().executescript(
            """CREATE TABLE IF NOT EXISTS jobs(
                 id TEXT PRIMARY KEY, status TEXT, stage TEXT, detail TEXT,
                 error TEXT, params TEXT, result_html TEXT, csv_path TEXT,
                 created_at REAL, updated_at REAL);"""
        )
        self._conn().commit()

    # -- worker -------------------------------------------------------------

    def start_worker(self, runner) -> None:
        """runner(job_id, params, progress) -> (result_html, csv_path)."""
        self._runner = runner
        if self._worker and self._worker.is_alive():
            return
        self._worker = threading.Thread(target=self._loop, name="scan-worker", daemon=True)
        self._worker.start()

    def _loop(self) -> None:
        while True:
            job_id = self._q.get()
            try:
                job = self.get(job_id)
                params = json.loads(job["params"]) if job else {}

                def progress(stage: str, detail: str = "", _id=job_id) -> None:
                    self.update(_id, status="running", stage=stage, detail=detail)

                html, csv_path = self._runner(job_id, params, progress)
                self.update(job_id, status="done", stage="done", detail="",
                            result_html=html, csv_path=csv_path or "")
            except Exception as exc:  # noqa: BLE001 - surface, don't crash the worker
                log.exception("job %s failed", job_id)
                self.update(job_id, status="error", error=str(exc))
            finally:
                self._q.task_done()

    # -- CRUD ---------------------------------------------------------------

    def enqueue(self, params: dict) -> str:
        job_id = uuid.uuid4().hex[:12]
        now = time.time()
        self._conn().execute(
            "INSERT INTO jobs(id,status,stage,detail,error,params,result_html,"
            "csv_path,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (job_id, "queued", "queued", "", None, json.dumps(params), None, None, now, now),
        )
        self._conn().commit()
        self._q.put(job_id)
        return job_id

    def update(self, job_id: str, **fields) -> None:
        fields["updated_at"] = time.time()
        cols = ", ".join(f"{k}=?" for k in fields)
        self._conn().execute(f"UPDATE jobs SET {cols} WHERE id=?",
                             (*fields.values(), job_id))
        self._conn().commit()

    def get(self, job_id: str) -> dict | None:
        row = self._conn().execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def latest(self, status: str | None = None) -> dict | None:
        sql = "SELECT * FROM jobs"
        args: tuple = ()
        if status:
            sql += " WHERE status=?"
            args = (status,)
        sql += " ORDER BY created_at DESC LIMIT 1"
        row = self._conn().execute(sql, args).fetchone()
        return dict(row) if row else None
