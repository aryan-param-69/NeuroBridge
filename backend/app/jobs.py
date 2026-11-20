"""Minimal background-job runner (no Redis/Celery on this machine).

Jobs are documents in MongoDB (`jobs`), executed by a small in-process thread pool. This is enough for a
single-server deployment; the interface (`submit`, job documents with status/progress) is what a Celery
worker would implement later. Jobs that were running when the server stopped are marked failed on startup.
"""

import logging
import threading
import traceback
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from pymongo.database import Database

from app.core.config import get_settings
from app.db.base import utcnow
from app.db.mongo import get_db

log = logging.getLogger("neurobridge.jobs")
_executor: ThreadPoolExecutor | None = None
_lock = threading.Lock()


def _pool() -> ThreadPoolExecutor:
    global _executor
    with _lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=get_settings().job_workers, thread_name_prefix="nb-job")
        return _executor


def create_job(db: Database, *, job_type: str, user_id: uuid.UUID, target: dict) -> uuid.UUID:
    job_id = uuid.uuid4()
    db.jobs.insert_one({"_id": job_id, "type": job_type, "target": target, "status": "queued", "progress": 0,
                        "message": "Waiting to start", "created_by": user_id, "created_at": utcnow(),
                        "started_at": None, "finished_at": None, "error": None})
    return job_id


def _run(job_id: uuid.UUID, fn: Callable[..., None]) -> None:
    db = get_db()
    db.jobs.update_one({"_id": job_id}, {"$set": {"status": "running", "started_at": utcnow(), "message": "Running"}})

    def progress(pct: int, message: str) -> None:
        db.jobs.update_one({"_id": job_id}, {"$set": {"progress": int(pct), "message": message}})

    try:
        fn(db, progress)
        db.jobs.update_one({"_id": job_id}, {"$set": {"status": "succeeded", "progress": 100,
                                                       "message": "Finished", "finished_at": utcnow()}})
    except Exception as e:  # the failure is recorded on the job; never crash the worker
        log.error("job %s failed: %s", job_id, traceback.format_exc())
        db.jobs.update_one({"_id": job_id}, {"$set": {"status": "failed", "message": "Failed",
                                                       "error": str(e)[:300], "finished_at": utcnow()}})


def submit(job_id: uuid.UUID, fn: Callable[..., None]) -> None:
    """Run `fn(db, progress)` in the background (or immediately when settings.jobs_inline)."""
    if get_settings().jobs_inline:
        _run(job_id, fn)
    else:
        _pool().submit(_run, job_id, fn)


def fail_stale_jobs(db: Database) -> int:
    """Called at startup: anything still queued/running belongs to a process that no longer exists."""
    r = db.jobs.update_many({"status": {"$in": ["queued", "running"]}},
                            {"$set": {"status": "failed", "error": "Server restarted while the job was running",
                                      "finished_at": utcnow()}})
    db.processing_runs.update_many({"status": {"$in": ["queued", "running"]}},
                                   {"$set": {"status": "failed", "error": "Server restarted while the job was running"}})
    return r.modified_count
