"""In-memory ingest jobs and watched folders.

Uploaded bytes stay in memory (never written to a temp file) while a background thread ingests
them, so the UI can show per-file status without waiting. Files that need a CSV mapping wait
in memory (up to an hour) for the user to finish the mapping wizard.
"""

from __future__ import annotations

import hashlib
import itertools
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from verismo_engine.context import Engine
from verismo_engine.ingest.service import NeedsMapping, ingest_bytes

log = logging.getLogger("verismo.ingest.jobs")
KEEP_SECONDS = 3600


@dataclass
class IngestJob:
    id: int
    filename: str
    size: int
    user_id: int | None
    options: dict[str, Any]
    status: str = "QUEUED"  # QUEUED | RUNNING | DONE | NEEDS_MAPPING | NEEDS_REVIEW | FAILED
    result: dict[str, Any] | None = None
    error: str | None = None
    created: float = field(default_factory=time.time)
    data: bytes | None = None
    source: str = "upload"

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "filename": self.filename,
            "size": self.size,
            "status": self.status,
            "result": self.result,
            "error": self.error,
            "created": self.created,
            "source": self.source,
        }


class IngestJobs:
    def __init__(self, eng: Engine) -> None:
        self.eng = eng
        self._jobs: dict[int, IngestJob] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        # one worker: SQLite has a single writer and ingest order matters for incremental detection
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ingest")
        self._watch_stop = threading.Event()
        self._watch_thread: threading.Thread | None = None
        self._seen: dict[str, tuple[float, int]] = {}

    def submit(
        self,
        filename: str,
        data: bytes,
        *,
        user_id: int | None,
        options: dict[str, Any] | None = None,
        mapping: dict[str, str] | None = None,
        template_id: int | None = None,
        source: str = "upload",
    ) -> IngestJob:
        job = IngestJob(
            next(self._ids), Path(filename).name, len(data), user_id, dict(options or {}), data=data, source=source
        )
        with self._lock:
            self._jobs[job.id] = job
            self._gc()
        self._pool.submit(self._run, job, mapping, template_id)
        return job

    def resubmit_with_mapping(
        self, job_id: int, mapping: dict[str, str], options: dict[str, Any] | None, template_id: int | None = None
    ) -> IngestJob:
        job = self.get(job_id)
        if job is None or job.data is None:
            raise KeyError("job not found or expired")
        job.status = "QUEUED"
        job.options.update(options or {})
        self._pool.submit(self._run, job, mapping, template_id)
        return job

    def get(self, job_id: int) -> IngestJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [j.public() for j in sorted(self._jobs.values(), key=lambda j: -j.id)][:200]

    def _run(self, job: IngestJob, mapping: dict[str, str] | None, template_id: int | None) -> None:
        job.status = "RUNNING"
        try:
            assert job.data is not None
            out = ingest_bytes(
                self.eng,
                job.filename,
                job.data,
                user_id=job.user_id,
                mapping=mapping,
                template_id=template_id,
                options=job.options,
            )
            job.result = out.as_dict()
            if out.parse.meta.get("needs_review_draft_id"):
                job.status = "NEEDS_REVIEW"
            elif out.persist is None and out.parse.errors:
                job.status, job.error = "FAILED", "; ".join(i.message for i in out.parse.errors[:5])
            else:
                job.status = "DONE"
            job.data = None  # release the bytes
        except NeedsMapping as e:
            job.status = "NEEDS_MAPPING"
            job.result = {"headers": e.headers, "suggestion": e.suggestion, "preview": e.preview}
        except Exception as e:  # noqa: BLE001
            log.warning("ingest job %s failed: %s", job.id, type(e).__name__)
            job.status, job.error, job.data = "FAILED", f"{type(e).__name__}: {e}"[:300], None

    def _gc(self) -> None:
        cutoff = time.time() - KEEP_SECONDS
        for jid in [j.id for j in self._jobs.values() if j.created < cutoff]:
            self._jobs.pop(jid, None)

    # ------------------------------------------------------------ watched folders
    def start_watching(self, interval: float = 10.0) -> None:
        if self._watch_thread is not None:
            return
        self._watch_thread = threading.Thread(target=self._watch_loop, args=(interval,), daemon=True, name="watch")
        self._watch_thread.start()

    def stop(self) -> None:
        self._watch_stop.set()
        self._pool.shutdown(wait=False, cancel_futures=True)

    def scan_once(self) -> int:
        n = 0
        for folder in self.eng.settings.get("ingest.watched_folders", []) or []:
            p = Path(folder)
            if not p.is_dir():
                continue
            for f in sorted(p.iterdir()):
                if (
                    not f.is_file()
                    or f.name.startswith(".")
                    or f.suffix.lower()
                    not in (
                        ".csv",
                        ".tsv",
                        ".txt",
                        ".xlsx",
                        ".xlsm",
                        ".x12",
                        ".edi",
                        ".837",
                        ".835",
                        ".pdf",
                        ".png",
                        ".jpg",
                        ".jpeg",
                        ".tif",
                        ".tiff",
                    )
                ):
                    continue
                st = f.stat()
                key = str(f.resolve())
                if self._seen.get(key) == (st.st_mtime, st.st_size):
                    continue
                if time.time() - st.st_mtime < 3:  # still being written
                    continue
                self._seen[key] = (st.st_mtime, st.st_size)
                data = f.read_bytes()
                sha = hashlib.sha256(data).hexdigest()
                if self.eng.db.scalar("SELECT 1 FROM documents WHERE sha256=? AND source_path=?", (sha, f.name)):
                    continue
                self.submit(f.name, data, user_id=None, source=f"watch:{p.name}")
                n += 1
        return n

    def _watch_loop(self, interval: float) -> None:
        while not self._watch_stop.wait(interval):
            try:
                self.scan_once()
            except Exception:  # noqa: BLE001
                log.exception("watched-folder scan failed")
