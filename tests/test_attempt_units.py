"""Pure request-binding/recovery components: in-memory SQLite, no actor loops."""

import json
import sqlite3
import unittest

from evaluation.inspect import SurfaceInspector
from permissioned_research.attempts import binding_metadata
from permissioned_research.cache import POLICY_VERSION
from permissioned_research.domain import ResearchError, ResearchRequest
from permissioned_research.store import CorpusStore, load_fixture


class AttemptUnits(unittest.TestCase):
    def setUp(self):
        self.fixture = load_fixture("vendor-review")
        self.store = CorpusStore(":memory:", self.fixture)
        self.addCleanup(self.store.close)
        self.request = ResearchRequest("priya", "CedarBridge", "2026-10-08")
        self.zero = {"decisions": 0, "searches": 0, "reads": 0}

    def completed(self, key="review-1", attempt="1" * 32):
        with self.store.attempt_session():
            self.assertEqual(self.store.begin_attempt(key, self.request, POLICY_VERSION, attempt), 0)
            self.store.finish_attempt(attempt, "priya", "needs_follow_up", self.zero)

    def test_normalized_request_binding_hashes_key_and_text(self):
        one = binding_metadata("review-1", ResearchRequest("priya", "Cedar  Bridge", "2026-10-08"), POLICY_VERSION)
        two = binding_metadata("review-1", ResearchRequest("priya", "CEDAR BRIDGE", "2026-10-08"), POLICY_VERSION)
        self.assertEqual(one, two)
        self.assertEqual(set(one), {"key_hash", "principal", "request_hash", "policy_version"})
        self.assertEqual((len(one["key_hash"]), len(one["request_hash"])), (64, 64))
        self.assertNotIn("Cedar", json.dumps(one))

    def test_changed_body_date_principal_and_policy_are_rejected_without_mutation(self):
        self.completed()
        before = [tuple(row) for row in self.store.connection.execute("SELECT * FROM run_attempts")]
        collisions = [(ResearchRequest("priya", "OtherVendor", "2026-10-08"), POLICY_VERSION),
                      (ResearchRequest("priya", "CedarBridge", "2026-10-09"), POLICY_VERSION),
                      (ResearchRequest("alex", "CedarBridge", "2026-10-08"), POLICY_VERSION),
                      (self.request, "different-policy")]
        for request, policy in collisions:
            with self.subTest(request=request, policy=policy):
                with self.store.attempt_session():
                    with self.assertRaisesRegex(ResearchError, "request_key_conflict"):
                        self.store.begin_attempt("review-1", request, policy, "2" * 32)
        self.assertEqual([tuple(row) for row in self.store.connection.execute("SELECT * FROM run_attempts")], before)
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM request_bindings").fetchone()[0], 1)

    def test_same_completed_key_starts_new_attempt_and_never_returns_a_memo(self):
        self.completed()
        with self.store.attempt_session():
            recovered = self.store.begin_attempt("review-1", self.request, POLICY_VERSION, "2" * 32)
            self.assertEqual(recovered, 0)
            self.assertEqual(self.store.connection.execute("SELECT outcome FROM run_attempts WHERE attempt_id=?", ("2" * 32,)).fetchone()[0], "running")
            self.store.finish_attempt("2" * 32, "priya", "failed", self.zero)
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM run_receipts").fetchone()[0], 2)
        self.assertEqual(self.store.connection.execute("SELECT COUNT(DISTINCT key_hash) FROM run_attempts").fetchone()[0], 1)

    def test_abandoned_attempt_becomes_interrupted_with_counters_and_current_new_epoch(self):
        counts = {"decisions": 4, "searches": 2, "reads": 1}
        with self.store.attempt_session():
            self.store.begin_attempt("review-1", self.request, POLICY_VERSION, "1" * 32)
            self.store.progress_attempt("1" * 32, counts)
        self.store.revoke_membership("priya", "procurement", "recovery-revoke")
        with self.store.attempt_session():
            self.assertEqual(self.store.begin_attempt("review-1", self.request, POLICY_VERSION, "2" * 32), 1)
            old = dict(self.store.connection.execute("SELECT * FROM run_attempts WHERE attempt_id=?", ("1" * 32,)).fetchone())
            self.assertEqual(old["outcome"], "interrupted")
            self.assertIsNotNone(old["ended_at"])
            receipt = dict(self.store.connection.execute("SELECT * FROM run_receipts WHERE run_id=?", ("1" * 32,)).fetchone())
            self.assertEqual((receipt["outcome"], receipt["decisions"], receipt["searches"], receipt["reads"]), ("interrupted", 4, 2, 1))
            new = dict(self.store.connection.execute("SELECT * FROM run_attempts WHERE attempt_id=?", ("2" * 32,)).fetchone())
            self.assertEqual((new["outcome"], new["epoch_started"], new["decisions"]), ("running", self.store.epoch, 0))
            self.assertIsNone(self.store.source_for("priya", "cedar-contract"))
            self.store.finish_attempt("2" * 32, "priya", "needs_follow_up", self.zero)
        with self.store.attempt_session():
            self.assertEqual(self.store.begin_attempt("review-1", self.request, POLICY_VERSION, "3" * 32), 0)

    def test_terminal_receipt_binding_and_attempt_are_immutable(self):
        self.completed()
        with self.store.attempt_session():
            with self.assertRaisesRegex(ResearchError, "attempt_state_conflict"):
                self.store.finish_attempt("1" * 32, "priya", "failed", self.zero)
        for statement in ("UPDATE run_receipts SET outcome='failed'", "DELETE FROM run_receipts",
                          "UPDATE run_attempts SET outcome='failed'", "DELETE FROM run_attempts",
                          "UPDATE request_bindings SET principal='alex'", "DELETE FROM request_bindings"):
            with self.subTest(statement=statement):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.store.connection.execute(statement)
        self.assertEqual(self.store.connection.execute("SELECT outcome FROM run_receipts").fetchone()[0], "needs_follow_up")

    def test_live_in_memory_owner_and_second_begin_cannot_be_recovered(self):
        with self.store.attempt_session():
            self.store.begin_attempt("review-1", self.request, POLICY_VERSION, "1" * 32)
            with self.assertRaisesRegex(ResearchError, "attempt_in_progress"):
                with self.store.attempt_session():
                    pass
            with self.assertRaisesRegex(ResearchError, "attempt_in_progress"):
                self.store.begin_attempt("review-1", self.request, POLICY_VERSION, "2" * 32)
            self.assertEqual(self.store.connection.execute("SELECT outcome FROM run_attempts").fetchone()[0], "running")
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM run_receipts").fetchone()[0], 0)

    def test_changed_binding_does_not_recover_an_abandoned_attempt(self):
        with self.store.attempt_session():
            self.store.begin_attempt("review-1", self.request, POLICY_VERSION, "1" * 32)
        with self.store.attempt_session():
            with self.assertRaisesRegex(ResearchError, "request_key_conflict"):
                self.store.begin_attempt("review-1", ResearchRequest("alex", "CedarBridge", "2026-10-08"), POLICY_VERSION, "2" * 32)
        self.assertEqual(self.store.connection.execute("SELECT outcome FROM run_attempts").fetchone()[0], "running")
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM run_receipts").fetchone()[0], 0)

    def test_metadata_only_records_do_not_persist_literal_key_or_question(self):
        self.completed(key="cedar-contract")
        inspector = SurfaceInspector(self.store, self.fixture)
        inspector.check_audit()
        self.assertEqual(inspector.violations, [])
        operational = [dict(row) for table in ("request_bindings", "run_attempts", "run_receipts", "audit_events")
                       for row in self.store.connection.execute("SELECT * FROM " + table)]
        self.assertNotIn("cedar-contract", json.dumps(operational))
        self.assertNotIn("CedarBridge", json.dumps(operational))

    def test_attempt_components_require_owner_and_bounded_counters(self):
        with self.assertRaisesRegex(ResearchError, "attempt_state_conflict"):
            self.store.begin_attempt("review-1", self.request, POLICY_VERSION, "1" * 32)
        with self.store.attempt_session():
            self.store.begin_attempt("review-1", self.request, POLICY_VERSION, "1" * 32)
            for counts in ({"decisions": True, "searches": 0, "reads": 0},
                           {"decisions": 25, "searches": 0, "reads": 0}):
                with self.assertRaisesRegex(ResearchError, "invalid_request"):
                    self.store.progress_attempt("1" * 32, counts)
            self.store.progress_attempt("1" * 32, {"decisions": 2, "searches": 1, "reads": 0})
            with self.assertRaises(sqlite3.IntegrityError):
                self.store.progress_attempt("1" * 32, self.zero)

    def test_invalid_key_and_policy_reject_without_metadata(self):
        for key in ("", "bad key", "k" * 65):
            with self.assertRaisesRegex(ResearchError, "invalid_request"):
                binding_metadata(key, self.request, POLICY_VERSION)
        with self.assertRaisesRegex(ResearchError, "invalid_request"):
            binding_metadata("review-1", self.request, "bad policy")
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM request_bindings").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
