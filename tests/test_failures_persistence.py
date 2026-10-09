"""CLI/owned-process restart tests. Execute only under the M2 integration lease."""

import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path

from permissioned_research.__main__ import main
from permissioned_research.agent import ResearchAgent
from permissioned_research.cache import RuntimeCaches
from permissioned_research.domain import ResearchRequest
from permissioned_research.providers import ScriptedResearchProvider
from permissioned_research.store import CorpusStore, load_fixture


CRASH_AFTER_READ = """
import os
import sys
from pathlib import Path
from permissioned_research.agent import ResearchAgent
from permissioned_research.domain import ResearchRequest
from permissioned_research.providers import ScriptedResearchProvider
from permissioned_research.store import CorpusStore, load_fixture
store = CorpusStore(Path(sys.argv[1]) / 'corpus.sqlite3', load_fixture('vendor-review'))
def crash(actor):
    last = actor.observations[-1]
    if last['action'] == 'read' and 'PROCUREMENT_ONLY_CEDAR' in last['result'].get('body', ''):
        os._exit(23)
actor = ResearchAgent(store, ResearchRequest('priya', 'CedarBridge', '2026-10-08'),
                      ScriptedResearchProvider(), request_key='restart-1', hooks={'after_tool': crash})
actor.run()
sys.exit(24)
"""

HOLD_ACTIVE = """
import sys
from pathlib import Path
from permissioned_research.cache import POLICY_VERSION
from permissioned_research.domain import ResearchRequest
from permissioned_research.store import CorpusStore, load_fixture
store = CorpusStore(Path(sys.argv[1]) / 'corpus.sqlite3', load_fixture('vendor-review'))
with store.attempt_session():
    store.begin_attempt('active-1', ResearchRequest('priya','CedarBridge','2026-10-08'), POLICY_VERSION, 'a'*32)
    print('READY', flush=True)
    sys.stdin.readline()
store.close()
"""


class PersistenceIntegrationTests(unittest.TestCase):
    def setUp(self):
        root = Path(".local/m2-tests").resolve()
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.directory = Path(self.temp.name).resolve()
        self.assertTrue(self.directory.is_relative_to(root))
        self.addCleanup(self.temp.cleanup)

    def cli(self, key, principal="priya"):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["demo", "--state-dir", str(self.directory), "--request-key", key, "--principal", principal])
        return code, stdout.getvalue(), stderr.getvalue()

    def rows(self, table):
        with closing(sqlite3.connect(self.directory / "corpus.sqlite3")) as connection:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute("SELECT * FROM " + table)]

    def test_same_completed_cli_key_researches_in_new_attempt(self):
        code, first, _ = self.cli("completed-1")
        self.assertEqual(code, 0)
        initial = json.loads(first)
        old_receipt = self.rows("run_receipts")[0]
        code, second, _ = self.cli("completed-1")
        current = json.loads(second)
        self.assertEqual(code, 0)
        self.assertEqual(current["outcome"], "ready_for_review")
        self.assertNotEqual(initial["run_id"], current["run_id"])
        self.assertGreater(current["counters"]["provider_requests"], 0)
        self.assertEqual(current["recovered_attempts"], 0)
        self.assertIn(old_receipt, self.rows("run_receipts"))
        self.assertEqual((len(self.rows("run_receipts")), len(self.rows("request_bindings"))), (2, 1))

    def test_cli_key_collision_has_no_new_attempt_or_private_output(self):
        self.assertEqual(self.cli("collision-1")[0], 0)
        before = self.rows("run_attempts")
        code, output, errors = self.cli("collision-1", "alex")
        self.assertEqual((code, output), (2, ""))
        self.assertIn('"error": "request_key_conflict"', errors)
        self.assertNotIn("PROCUREMENT", output + errors)
        self.assertEqual(self.rows("run_attempts"), before)

    def test_same_completed_api_key_drops_warm_cache_and_researches_afresh(self):
        store = CorpusStore(":memory:", load_fixture("vendor-review"))
        self.addCleanup(store.close)
        caches = RuntimeCaches()
        request = ResearchRequest("priya", "CedarBridge", "2026-10-08")
        first = ResearchAgent(store, request, ScriptedResearchProvider(), caches=caches, request_key="shared-api-1").run()
        self.assertTrue(caches.memos.entries)
        second = ResearchAgent(store, request, ScriptedResearchProvider(), caches=caches, request_key="shared-api-1").run()
        self.assertEqual((first["outcome"], second["outcome"]), ("ready_for_review", "ready_for_review"))
        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertGreater(second["counters"]["provider_requests"], 0)
        self.assertEqual(second["counters"]["cache_hits"], 0)

    def test_abrupt_owned_process_exit_recovers_without_private_context_after_revoke(self):
        child = subprocess.run([sys.executable, "-c", CRASH_AFTER_READ, str(self.directory)],
                               capture_output=True, text=True, timeout=5)
        self.assertEqual(child.returncode, 23)
        old = self.rows("run_attempts")[0]
        self.assertEqual(old["outcome"], "running")
        self.assertGreater(old["reads"], 0)
        self.assertEqual(self.rows("run_receipts"), [])
        store = CorpusStore(self.directory / "corpus.sqlite3", load_fixture("vendor-review"))
        try:
            store.revoke_membership("priya", "procurement", "restart-revoke")
            current_epoch = store.epoch
        finally:
            store.close()
        code, output, _ = self.cli("restart-1")
        current = json.loads(output)
        self.assertEqual((code, current["outcome"], current["recovered_attempts"]), (0, "needs_follow_up", 1))
        self.assertNotEqual(old["attempt_id"], current["run_id"])
        self.assertNotIn("PROCUREMENT", output)
        self.assertEqual([topic["state"] for topic in current["memo"]["topics"][-2:]], ["unknown", "unknown"])
        attempts = {row["attempt_id"]: row for row in self.rows("run_attempts")}
        self.assertEqual(attempts[old["attempt_id"]]["outcome"], "interrupted")
        self.assertEqual(attempts[current["run_id"]]["epoch_started"], current_epoch)
        self.assertEqual({row["outcome"] for row in self.rows("run_receipts")}, {"interrupted", "needs_follow_up"})
        operational = [row for table in ("request_bindings", "run_attempts", "run_receipts", "audit_events") for row in self.rows(table)]
        for forbidden in ("PROCUREMENT", "cedar-contract", "CedarBridge", "Priority support", "restart-1"):
            self.assertNotIn(forbidden, json.dumps(operational))

    @staticmethod
    def stop_owned(child):
        try:
            child.communicate(input="\n" if child.poll() is None else None, timeout=3)
        except subprocess.TimeoutExpired:
            child.kill()
            child.communicate(timeout=3)

    def test_live_process_owner_is_rejected_without_false_interruption(self):
        child = subprocess.Popen([sys.executable, "-c", HOLD_ACTIVE, str(self.directory)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        ready, lines = threading.Event(), []
        def read_ready():
            lines.append(child.stdout.readline())
            ready.set()
        reader = threading.Thread(target=read_ready, daemon=True)
        reader.start()
        self.addCleanup(lambda: reader.join(1))
        self.addCleanup(self.stop_owned, child)
        self.assertTrue(ready.wait(3), "Owned process did not reach its ready barrier")
        reader.join(1)
        self.assertEqual(lines, ["READY\n"])
        code, output, errors = self.cli("active-1")
        self.assertEqual((code, output), (2, ""))
        self.assertIn('"error": "attempt_in_progress"', errors)
        self.assertEqual(self.rows("run_attempts")[0]["outcome"], "running")
        self.assertEqual(self.rows("run_receipts"), [])
        self.assertIsNone(child.poll())


if __name__ == "__main__":
    unittest.main()
