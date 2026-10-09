"""Trusted fixture storage, identity-bound source reads and safe operational audit."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .domain import CONNECTORS, MAX_SOURCE_BYTES, ResearchError, Source, iso_date
from .attempts import SingleAttemptOwner, binding_metadata

SCHEMA_VERSION = 3
SCENARIOS = ("vendor-review", "conflicting-review", "stale-review", "missing-review")
AUDIT_ACTIONS = frozenset({"plan", "revise_plan", "search", "read", "finish", "result", "failure",
                           "dispatch", "invalidate", "cache", "release", "retry", "recover"})
AUDIT_STATUSES = frozenset({"ok", "not_available", "ready_for_review", "needs_follow_up", "failed", "cancelled", "invalidated", "interrupted"})


def load_fixture(scenario: str) -> dict[str, Any]:
    if scenario not in SCENARIOS:
        raise ResearchError("invalid_request")
    path = Path(__file__).resolve().parent.parent / "fixtures" / "dev" / f"{scenario}.json"
    try:
        fixture = json.loads(path.read_text(encoding="utf-8"))
        validate_fixture(fixture)
        return fixture
    except (OSError, ValueError, KeyError, TypeError):
        raise ResearchError("invalid_fixture") from None


def validate_fixture(fixture: dict[str, Any]) -> None:
    if fixture.get("fictional") is not True or fixture.get("schema_version") != 1:
        raise ResearchError("invalid_fixture")
    iso_date(fixture["as_of"])
    principals = fixture["principals"]
    if set(principals) != {"alex", "priya", "sam"}:
        raise ResearchError("invalid_fixture")
    if not 1 <= len(fixture["sources"]) <= 20:
        raise ResearchError("invalid_fixture")
    ids: set[str] = set()
    for source in fixture["sources"]:
        object_id = source["object_id"]
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", object_id) or object_id in ids:
            raise ResearchError("invalid_fixture")
        ids.add(object_id)
        if source["connector"] not in CONNECTORS or type(source["revision"]) is not int or source["revision"] < 1:
            raise ResearchError("invalid_fixture")
        if source["subject"] != fixture["vendor"] or len(source["body"].encode("utf-8")) > MAX_SOURCE_BYTES:
            raise ResearchError("invalid_fixture")
        if not source["allow_groups"] or len(source["title"]) > 128:
            raise ResearchError("invalid_fixture")
        iso_date(source["effective_at"])
        iso_date(source["review_until"])
        if source["effective_at"] > source["review_until"]:
            raise ResearchError("invalid_fixture")
        if source["connector"] == "teamchat" and not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", source["channel"]):
            raise ResearchError("invalid_fixture")
    channel_grants: dict[str, tuple[str, ...]] = {}
    for source in fixture["sources"]:
        if source["connector"] == "teamchat":
            groups = tuple(sorted(source["allow_groups"]))
            prior = channel_grants.setdefault(source["channel"], groups)
            if prior != groups:
                raise ResearchError("invalid_fixture")


class CorpusStore:
    def __init__(self, path: str | Path, fixture: dict[str, Any]):
        validate_fixture(fixture)
        self.connection = sqlite3.connect(str(path), timeout=0.25, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.cache_namespace = uuid.uuid4().hex if str(path) == ":memory:" else str(Path(path).resolve())
        self._attempt_owner = SingleAttemptOwner(None if str(path) == ":memory:" else Path(path).resolve())
        self._attempt_session_active = False
        self._active_attempt_id = None
        self.request_key_reused = False
        digest = hashlib.sha256(json.dumps(fixture, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        try:
            tables = {r[0] for r in self.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables:
                if self.connection.execute("PRAGMA user_version").fetchone()[0] not in {1, 2, SCHEMA_VERSION} or "metadata" not in tables:
                    raise ResearchError("state_incompatible")
                row = self.connection.execute("SELECT value FROM metadata WHERE key='fixture_sha256'").fetchone()
                if row is None or row[0] != digest:
                    raise ResearchError("state_incompatible")
            else:
                self._initialize(fixture, digest)
            self._epoch_schema()
            self._attempt_schema()
        except (ResearchError, sqlite3.Error):
            self.connection.close()
            raise

    def _initialize(self, fixture: dict[str, Any], digest: str) -> None:
        self.connection.executescript("""
            BEGIN;
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE principals (principal TEXT PRIMARY KEY);
            CREATE TABLE memberships (principal TEXT REFERENCES principals, group_name TEXT,
                                      PRIMARY KEY(principal, group_name));
            CREATE TABLE objects (object_id TEXT PRIMARY KEY, connector TEXT NOT NULL,
                                  subject TEXT NOT NULL, title TEXT NOT NULL,
                                  current_revision INTEGER NOT NULL, acl_resource TEXT NOT NULL);
            CREATE TABLE versions (object_id TEXT REFERENCES objects, revision INTEGER,
                                   body TEXT NOT NULL, effective_at TEXT NOT NULL, review_until TEXT NOT NULL,
                                   PRIMARY KEY(object_id, revision));
            CREATE TABLE grants (resource TEXT, group_name TEXT, PRIMARY KEY(resource, group_name));
            CREATE TABLE audit_events (sequence INTEGER PRIMARY KEY, created_at TEXT NOT NULL,
                                       run_id TEXT NOT NULL, action TEXT NOT NULL,
                                       status TEXT NOT NULL, item_count INTEGER NOT NULL);
            CREATE TABLE run_receipts (run_id TEXT PRIMARY KEY, principal TEXT NOT NULL,
                                       outcome TEXT NOT NULL, decisions INTEGER NOT NULL,
                                       searches INTEGER NOT NULL, reads INTEGER NOT NULL);
            PRAGMA user_version=1;
        """)
        try:
            self.connection.execute("INSERT INTO metadata VALUES (?, ?)", ("fixture_sha256", digest))
            for principal, groups in fixture["principals"].items():
                self.connection.execute("INSERT INTO principals VALUES (?)", (principal,))
                self.connection.executemany("INSERT INTO memberships VALUES (?, ?)", [(principal, group) for group in groups])
            for source in fixture["sources"]:
                resource = ("channel:" + source["channel"]) if source["connector"] == "teamchat" else ("object:" + source["object_id"])
                self.connection.execute("INSERT INTO objects VALUES (?, ?, ?, ?, ?, ?)",
                    (source["object_id"], source["connector"], source["subject"], source["title"], source["revision"], resource))
                self.connection.execute("INSERT INTO versions VALUES (?, ?, ?, ?, ?)",
                    (source["object_id"], source["revision"], source["body"], source["effective_at"], source["review_until"]))
                self.connection.executemany("INSERT OR IGNORE INTO grants VALUES (?, ?)",
                    [(resource, group) for group in source["allow_groups"]])
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def close(self) -> None:
        self.connection.close()

    def _epoch_schema(self) -> None:
        """Additive migration after validating an owned M1 fixture store."""
        if self.connection.execute("PRAGMA user_version").fetchone()[0] >= 2:
            return
        with self.boundary():
            self.connection.execute("CREATE TABLE control_epoch (singleton INTEGER PRIMARY KEY CHECK(singleton=1), epoch INTEGER NOT NULL)")
            self.connection.execute("INSERT INTO control_epoch VALUES (1,1)")
            self.connection.execute("CREATE TABLE scenario_events (event_id TEXT PRIMARY KEY)")
            self.connection.execute("ALTER TABLE audit_events ADD COLUMN epoch INTEGER NOT NULL DEFAULT 1")
            # Triggers also catch trusted test/admin SQL, avoiding a forgotten
            # version bump in a second connection. Audit/receipts are excluded.
            for table in ("principals", "memberships", "grants", "objects", "versions"):
                for operation in ("INSERT", "UPDATE", "DELETE"):
                    self.connection.execute(f"CREATE TRIGGER epoch_{table}_{operation.lower()} AFTER {operation} ON {table} "
                                            "BEGIN UPDATE control_epoch SET epoch=epoch+1 WHERE singleton=1; END")
            self.connection.execute("PRAGMA user_version=2")

    def _attempt_schema(self) -> None:
        if self.connection.execute("PRAGMA user_version").fetchone()[0] >= 3:
            return
        with self.boundary():
            self.connection.execute("""CREATE TABLE request_bindings (
                key_hash TEXT PRIMARY KEY, principal TEXT NOT NULL, request_hash TEXT NOT NULL,
                policy_version TEXT NOT NULL)""")
            self.connection.execute("""CREATE TABLE run_attempts (
                attempt_id TEXT PRIMARY KEY, key_hash TEXT NOT NULL REFERENCES request_bindings,
                outcome TEXT NOT NULL CHECK(outcome IN ('running','interrupted','ready_for_review','needs_follow_up','failed','cancelled')),
                created_at TEXT NOT NULL, ended_at TEXT, epoch_started INTEGER NOT NULL,
                schema_version INTEGER NOT NULL, decisions INTEGER NOT NULL CHECK(decisions BETWEEN 0 AND 24),
                searches INTEGER NOT NULL CHECK(searches BETWEEN 0 AND 8), reads INTEGER NOT NULL CHECK(reads BETWEEN 0 AND 12),
                CHECK((outcome='running') = (ended_at IS NULL)))""")
            self.connection.execute("CREATE UNIQUE INDEX one_running_attempt ON run_attempts(outcome) WHERE outcome='running'")
            for table in ("request_bindings", "run_receipts"):
                for operation in ("UPDATE", "DELETE"):
                    self.connection.execute(f"CREATE TRIGGER immutable_{table}_{operation.lower()} BEFORE {operation} ON {table} "
                                            "BEGIN SELECT RAISE(ABORT,'immutable operational record'); END")
            self.connection.execute("""CREATE TRIGGER immutable_terminal_attempt BEFORE UPDATE ON run_attempts
                WHEN OLD.outcome != 'running' OR NEW.attempt_id != OLD.attempt_id OR NEW.key_hash != OLD.key_hash
                OR NEW.created_at != OLD.created_at OR NEW.epoch_started != OLD.epoch_started
                OR NEW.schema_version != OLD.schema_version OR NEW.decisions < OLD.decisions
                OR NEW.searches < OLD.searches OR NEW.reads < OLD.reads
                BEGIN SELECT RAISE(ABORT,'immutable attempt metadata'); END""")
            self.connection.execute("CREATE TRIGGER keep_attempt_history BEFORE DELETE ON run_attempts "
                                    "BEGIN SELECT RAISE(ABORT,'immutable attempt history'); END")
            self.connection.execute("PRAGMA user_version=3")

    @contextmanager
    def attempt_session(self):
        # This lock spans inference, but is separate from SQLite so trusted ACL
        # writers remain able to commit during response collection.
        with self._attempt_owner.hold():
            self._attempt_session_active = True
            try:
                yield
            finally:
                self._attempt_session_active = False
                self._active_attempt_id = None
                self.request_key_reused = False

    def _require_attempt_session(self) -> None:
        if not self._attempt_session_active:
            raise ResearchError("attempt_state_conflict")

    def begin_attempt(self, request_key: str, request, policy_version: str, attempt_id: str) -> int:
        self._require_attempt_session()
        if self._active_attempt_id is not None:
            raise ResearchError("attempt_in_progress")
        metadata = binding_metadata(request_key, request, policy_version)
        if type(attempt_id) is not str or not re.fullmatch(r"[0-9a-f]{32}", attempt_id):
            raise ResearchError("invalid_request")
        with self.boundary():
            self.require_principal(request.principal)
            prior = self.connection.execute("SELECT * FROM request_bindings WHERE key_hash=?", (metadata["key_hash"],)).fetchone()
            if prior is not None and dict(prior) != metadata:
                raise ResearchError("request_key_conflict")
            if (self.connection.execute("SELECT 1 FROM run_attempts WHERE attempt_id=?", (attempt_id,)).fetchone() or
                    self.connection.execute("SELECT 1 FROM run_receipts WHERE run_id=?", (attempt_id,)).fetchone()):
                raise ResearchError("attempt_state_conflict")
            if prior is None:
                self.connection.execute("INSERT INTO request_bindings VALUES (?,?,?,?)", tuple(metadata.values()))
            abandoned = self.connection.execute("""SELECT a.*, b.principal FROM run_attempts a
                JOIN request_bindings b USING(key_hash) WHERE a.outcome='running'""").fetchall()
            for row in abandoned:
                self.finish_attempt(row["attempt_id"], row["principal"], "interrupted", dict(row))
                self.audit(row["attempt_id"], "recover", "interrupted")
            self.connection.execute("""INSERT INTO run_attempts VALUES (?,?, 'running',?,NULL,?,?,0,0,0)""",
                (attempt_id, metadata["key_hash"], datetime.now(timezone.utc).isoformat(), self.epoch, SCHEMA_VERSION))
            self._active_attempt_id = attempt_id
            self.request_key_reused = prior is not None
            return len(abandoned)

    @staticmethod
    def _attempt_counts(counters: dict) -> tuple[int, int, int]:
        values = tuple(counters.get(key) for key in ("decisions", "searches", "reads"))
        if any(type(value) is not int or not 0 <= value <= limit for value, limit in zip(values, (24, 8, 12))):
            raise ResearchError("invalid_request")
        return values

    def progress_attempt(self, attempt_id: str, counters: dict) -> None:
        self._require_attempt_session()
        if self._active_attempt_id != attempt_id:
            raise ResearchError("attempt_state_conflict")
        counts = self._attempt_counts(counters)
        with self.boundary():
            changed = self.connection.execute("""UPDATE run_attempts SET decisions=?,searches=?,reads=?
                WHERE attempt_id=? AND outcome='running'""", (*counts, attempt_id)).rowcount
            if changed != 1:
                raise ResearchError("attempt_state_conflict")

    def finish_attempt(self, attempt_id: str, principal: str, outcome: str, counters: dict) -> None:
        self._require_attempt_session()
        if self._active_attempt_id is not None and self._active_attempt_id != attempt_id:
            raise ResearchError("attempt_state_conflict")
        if outcome not in {"ready_for_review", "needs_follow_up", "failed", "cancelled", "interrupted"}:
            raise ResearchError("invalid_request")
        counts = self._attempt_counts(counters)
        with self.boundary():
            row = self.connection.execute("""SELECT a.outcome,b.principal FROM run_attempts a
                JOIN request_bindings b USING(key_hash) WHERE attempt_id=?""", (attempt_id,)).fetchone()
            if row is None or row["outcome"] != "running" or row["principal"] != principal:
                raise ResearchError("attempt_state_conflict")
            self.connection.execute("UPDATE run_attempts SET outcome=?,ended_at=?,decisions=?,searches=?,reads=? WHERE attempt_id=?",
                (outcome, datetime.now(timezone.utc).isoformat(), *counts, attempt_id))
            self.connection.execute("INSERT INTO run_receipts VALUES (?,?,?,?,?,?)", (attempt_id, principal, outcome, *counts))

    @property
    def epoch(self) -> int:
        return self.connection.execute("SELECT epoch FROM control_epoch WHERE singleton=1").fetchone()[0]

    @contextmanager
    def boundary(self):
        """Serialize permission changes, dispatch bytes and caller handoff.

        Nested trusted operations join the enclosing transaction; inference
        collection must happen after this context exits.
        """
        owns_transaction = not self.connection.in_transaction
        if owns_transaction:
            self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            if owns_transaction:
                self.connection.execute("COMMIT")
        except BaseException:
            if owns_transaction and self.connection.in_transaction:
                self.connection.execute("ROLLBACK")
            raise

    def _once(self, event_id: str, operation) -> bool:
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", event_id):
            raise ResearchError("invalid_request")
        with self.boundary():
            if self.connection.execute("SELECT 1 FROM scenario_events WHERE event_id=?", (event_id,)).fetchone():
                return False
            operation()
            self.connection.execute("INSERT INTO scenario_events VALUES (?)", (event_id,))
            return True

    def revoke_membership(self, principal: str, group: str, event_id: str) -> bool:
        return self._once(event_id, lambda: self.connection.execute(
            "DELETE FROM memberships WHERE principal=? AND group_name=?", (principal, group)))

    def delete_object(self, object_id: str, event_id: str) -> bool:
        def operation():
            self.connection.execute("DELETE FROM versions WHERE object_id=?", (object_id,))
            self.connection.execute("DELETE FROM objects WHERE object_id=?", (object_id,))
        return self._once(event_id, operation)

    def replace_source(self, object_id: str, body: str, effective_at: str, review_until: str, event_id: str) -> bool:
        iso_date(effective_at)
        iso_date(review_until)
        if len(body.encode("utf-8")) > MAX_SOURCE_BYTES or effective_at > review_until:
            raise ResearchError("invalid_request")
        def operation():
            row = self.connection.execute("SELECT current_revision FROM objects WHERE object_id=?", (object_id,)).fetchone()
            if row is None:
                raise ResearchError("invalid_request")
            revision = row[0] + 1
            self.connection.execute("INSERT INTO versions VALUES (?, ?, ?, ?, ?)", (object_id, revision, body, effective_at, review_until))
            self.connection.execute("UPDATE objects SET current_revision=? WHERE object_id=?", (revision, object_id))
        return self._once(event_id, operation)

    def require_principal(self, principal: str) -> None:
        if self.connection.execute("SELECT 1 FROM principals WHERE principal=?", (principal,)).fetchone() is None:
            raise ResearchError("invalid_request")

    def authorized_sources(self, principal: str, connector: str | None = None,
                           object_id: str | None = None) -> list[Source]:
        # ACLs are in the SQL predicate before titles or bodies leave the store.
        rows = self.connection.execute("""
            SELECT o.object_id, o.connector, v.revision, o.subject, o.title,
                   v.body, v.effective_at, v.review_until
            FROM objects o JOIN versions v
              ON v.object_id=o.object_id AND v.revision=o.current_revision
            WHERE EXISTS (
                SELECT 1 FROM grants g JOIN memberships m ON g.group_name=m.group_name
                WHERE g.resource=o.acl_resource AND m.principal=?
            ) AND (? IS NULL OR o.connector=?) AND (? IS NULL OR o.object_id=?)
            ORDER BY o.object_id
        """, (principal, connector, connector, object_id, object_id)).fetchall()
        return [Source(**dict(row)) for row in rows]

    def source_for(self, principal: str, object_id: str) -> Source | None:
        sources = self.authorized_sources(principal, object_id=object_id)
        return sources[0] if sources else None

    def audit(self, run_id: str, action: str, status: str, item_count: int = 0) -> None:
        if action not in AUDIT_ACTIONS or status not in AUDIT_STATUSES or type(item_count) is not int or not 0 <= item_count <= 24:
            raise ValueError("Invalid operational audit field")
        if not re.fullmatch(r"[0-9a-f]{32}", run_id):
            raise ValueError("Invalid run ID")
        self.connection.execute("INSERT INTO audit_events(created_at,run_id,action,status,item_count,epoch) VALUES (?, ?, ?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), run_id, action, status, item_count, self.epoch))

