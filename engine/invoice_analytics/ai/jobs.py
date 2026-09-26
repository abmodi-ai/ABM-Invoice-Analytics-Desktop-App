"""Background job queue (spec 7.5). LLM jobs run one at a time; other jobs run on a small pool.

Priority (lower first): user-initiated > EXPLAIN for flags open in the UI > background TRIAGE >
background EXPLAIN. Jobs can be cancelled; failures retry with backoff, up to 2 attempts total.
No UI request ever waits for a job: endpoints enqueue and return a job id.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from invoice_analytics.context import Engine

log = logging.getLogger("invoice_analytics.jobs")

PRIORITY = {"USER": 10, "UI_EXPLAIN": 20, "BACKGROUND_TRIAGE": 50, "BACKGROUND_EXPLAIN": 60, "SYSTEM": 30}
LLM_KINDS = {"AI_EXPLAIN", "AI_TRIAGE", "AI_ASK", "AI_EXTRACT"}
MAX_ATTEMPTS = 2

Handler = Callable[["Engine", dict[str, Any], "JobContext"], Any]
_HANDLERS: dict[str, Handler] = {}


def handler(kind: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        _HANDLERS[kind] = fn
        return fn

    return deco


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class JobContext:
    def __init__(self, queue: JobQueue, job_id: int) -> None:
        self.queue = queue
        self.job_id = job_id

    def cancelled(self) -> bool:
        return self.queue.is_cancelled(self.job_id)


class JobQueue:
    def __init__(self, eng: Engine, *, other_workers: int = 2) -> None:
        self.eng = eng
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._threads: list[threading.Thread] = []
        self._cancel: set[int] = set()
        self._claim_lock = threading.Lock()
        self.current: dict[str, Any] = {}
        self.other_workers = other_workers
        # Jobs left RUNNING by a crash go back to the queue.
        with eng.db.tx() as c:
            c.execute("UPDATE jobs SET status='QUEUED', started_at=NULL WHERE status='RUNNING'")

    def start(self) -> None:
        import invoice_analytics.ai.handlers  # noqa: F401  (registers handlers)

        if self._threads:
            return
        self._threads.append(threading.Thread(target=self._loop, args=(True,), daemon=True, name="job-llm"))
        for i in range(self.other_workers):
            self._threads.append(threading.Thread(target=self._loop, args=(False,), daemon=True, name=f"job-{i}"))
        for t in self._threads:
            t.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        for t in self._threads:
            t.join(timeout=5)
        self._threads.clear()

    # ------------------------------------------------------------ API
    def enqueue(self, kind: str, payload: dict[str, Any], priority: int | None = None, dedupe: bool = True) -> int:
        p = PRIORITY["SYSTEM"] if priority is None else priority
        body = json.dumps(payload, sort_keys=True)
        with self.eng.db.tx() as c:
            if dedupe:
                row = c.execute(
                    "SELECT id, priority FROM jobs WHERE kind=? AND payload=? AND status IN ('QUEUED','RUNNING')",
                    (kind, body),
                ).fetchone()
                if row:
                    if p < row["priority"]:
                        c.execute("UPDATE jobs SET priority=? WHERE id=?", (p, row["id"]))
                    return int(row["id"])
            jid = int(c.execute("INSERT INTO jobs(kind, payload, priority) VALUES (?,?,?)", (kind, body, p)).lastrowid)
        self._wake.set()
        return jid

    def cancel(self, job_id: int) -> bool:
        with self.eng.db.tx() as c:
            n = c.execute(
                "UPDATE jobs SET status='CANCELLED', finished_at=? WHERE id=? AND status IN ('QUEUED','RUNNING')",
                (_now(), job_id),
            ).rowcount
        self._cancel.add(job_id)
        return bool(n)

    def is_cancelled(self, job_id: int) -> bool:
        return job_id in self._cancel

    def get(self, job_id: int) -> dict[str, Any] | None:
        r = self.eng.db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if r:
            r["payload"] = json.loads(r["payload"])
            r["result"] = json.loads(r["result"]) if r["result"] else None
        return r

    def status(self) -> dict[str, Any]:
        rows = self.eng.db.query(
            "SELECT kind, status, COUNT(*) AS n FROM jobs WHERE status IN ('QUEUED','RUNNING')" " GROUP BY kind, status"
        )
        return {
            "queued": sum(r["n"] for r in rows if r["status"] == "QUEUED"),
            "running": [v for v in self.current.values()],
            "by_kind": rows,
        }

    def run_pending(self, *, llm: bool | None = None, limit: int = 1000) -> int:
        """Synchronously drain the queue (tests and CLI)."""
        import invoice_analytics.ai.handlers  # noqa: F401

        n = 0
        while n < limit:
            job = self._claim(llm)
            if job is None:
                return n
            self._execute(job)
            n += 1
        return n

    # ------------------------------------------------------------ internals
    def _claim(self, llm: bool | None) -> dict[str, Any] | None:
        kinds = ",".join(f"'{k}'" for k in LLM_KINDS)
        cond = "" if llm is None else (f" AND kind IN ({kinds})" if llm else f" AND kind NOT IN ({kinds})")
        with self._claim_lock, self.eng.db.tx() as c:
            row = c.execute(
                f"SELECT * FROM jobs WHERE status='QUEUED' AND (not_before IS NULL OR not_before<=?){cond}"
                " ORDER BY priority, id LIMIT 1",
                (_now(),),
            ).fetchone()
            if row is None:
                return None
            c.execute(
                "UPDATE jobs SET status='RUNNING', started_at=?, attempts=attempts+1 WHERE id=?", (_now(), row["id"])
            )
            row["attempts"] += 1
            return dict(row)

    def _execute(self, job: dict[str, Any]) -> None:
        jid = job["id"]
        fn = _HANDLERS.get(job["kind"])
        self.current[threading.current_thread().name] = {"id": jid, "kind": job["kind"], "started_at": _now()}
        try:
            if fn is None:
                raise RuntimeError(f"no handler for job kind {job['kind']}")
            result = fn(self.eng, json.loads(job["payload"]), JobContext(self, jid))
            if self.is_cancelled(jid):
                return
            with self.eng.db.tx() as c:
                c.execute(
                    "UPDATE jobs SET status='DONE', finished_at=?, result=?, error=NULL WHERE id=?",
                    (_now(), json.dumps(result, default=str) if result is not None else None, jid),
                )
        except Exception as e:  # noqa: BLE001
            log.warning("job %s (%s) failed: %s", jid, job["kind"], type(e).__name__)
            retry = job["attempts"] < MAX_ATTEMPTS and not self.is_cancelled(jid)
            with self.eng.db.tx() as c:
                if retry:
                    nb = (datetime.now(UTC) + timedelta(seconds=5 * 2 ** job["attempts"])).strftime(
                        "%Y-%m-%dT%H:%M:%S.%fZ"
                    )
                    c.execute(
                        "UPDATE jobs SET status='QUEUED', not_before=?, error=? WHERE id=?",
                        (nb, f"{type(e).__name__}: {e}"[:500], jid),
                    )
                else:
                    c.execute(
                        "UPDATE jobs SET status='FAILED', finished_at=?, error=? WHERE id=?",
                        (_now(), f"{type(e).__name__}: {e}"[:500], jid),
                    )
        finally:
            self.current.pop(threading.current_thread().name, None)

    def _loop(self, llm: bool) -> None:
        while not self._stop.is_set():
            try:
                job = self._claim(llm)
            except Exception:  # noqa: BLE001
                log.exception("job claim failed")
                job = None
            if job is None:
                self._wake.wait(1.0)
                self._wake.clear()
                continue
            self._execute(job)
            time.sleep(0)
