"""Trusted request digests and one local active-attempt owner; no saved context."""

import errno
import hashlib
import json
import os
import re
import threading
from contextlib import contextmanager
from pathlib import Path

from .domain import TOPICS, ResearchError, ResearchRequest


def binding_metadata(request_key: str, request: ResearchRequest, policy_version: str) -> dict:
    if (type(request_key) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", request_key) or
            type(policy_version) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", policy_version) or
            not isinstance(request, ResearchRequest)):
        raise ResearchError("invalid_request")
    normalized = {"vendor": " ".join(request.vendor.casefold().split()), "as_of": request.as_of,
                  "topics": list(TOPICS)}
    wire = json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()
    return {"key_hash": hashlib.sha256(request_key.encode()).hexdigest(), "principal": request.principal,
            "request_hash": hashlib.sha256(wire).hexdigest(), "policy_version": policy_version}


class SingleAttemptOwner:
    """Nonblocking local-file ownership; source-admin connections remain free."""
    def __init__(self, database_path: Path | None):
        self.path = database_path.with_name(database_path.name + ".attempt.lock") if database_path else None
        self.mutex = threading.Lock()

    @contextmanager
    def hold(self):
        if not self.mutex.acquire(blocking=False):
            raise ResearchError("attempt_in_progress")
        handle, locked = None, False
        try:
            if self.path is not None:
                handle = self.path.open("a+b")
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                try:
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                except OSError as exc:
                    code = "attempt_in_progress" if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK} else "storage_error"
                    raise ResearchError(code) from None
            yield
        finally:
            try:
                if locked:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                if handle is not None:
                    handle.close()
                self.mutex.release()
        # Keep the constant lock file; unlinking it could create two owners.
