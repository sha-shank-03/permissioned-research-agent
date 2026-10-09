"""One bounded loop with epoch reset and serialized dispatch/release checks."""

from __future__ import annotations

import copy
import json
import sqlite3
import threading
import time
import uuid
from typing import Any

from .cache import POLICY_VERSION, RuntimeCaches
from .attempts import binding_metadata
from .connectors import ConnectorTools
from .domain import Action, Evidence, PROTOCOL, TOOLS, ResearchError, ResearchRequest
from .guard import validate_memo
from .providers import DecisionProvider, LocalProviderAdapter, ProviderResponse
from .store import CorpusStore


class MemorySink:
    """Caller-owned delivery buffer; handed-off snapshots cannot be recalled."""
    def __init__(self):
        self.memo = None

    def release(self, memo: dict) -> None:
        self.memo = copy.deepcopy(memo)


class ResearchAgent:
    def __init__(self, store: CorpusStore, request: ResearchRequest, provider: DecisionProvider,
                 max_decisions: int = 24, *, caches: RuntimeCaches | None = None,
                 cancellation: threading.Event | None = None, sink=None, observer=None,
                 hooks=None, clock=time.monotonic, timeout_seconds: float = 180,
                 request_key: str | None = None):
        if type(max_decisions) is not int or not 1 <= max_decisions <= 24 or not 0 < timeout_seconds <= 180:
            raise ResearchError("invalid_request")
        self.store, self.request = store, request
        self.provider = provider if hasattr(provider, "submit") and hasattr(provider, "collect") else LocalProviderAdapter(provider)
        self.caches = caches or RuntimeCaches()
        self.observer, self.hooks = observer, hooks or {}
        self.tools = self._new_tools()
        self.cancellation = cancellation or threading.Event()
        self.sink = sink or MemorySink()
        self.clock, self.timeout_seconds = clock, timeout_seconds
        self.max_decisions = max_decisions
        self.run_id = uuid.uuid4().hex
        self.request_key = request_key if request_key is not None else "rq_" + uuid.uuid4().hex
        binding_metadata(self.request_key, request, POLICY_VERSION)
        self._has_run = False
        self.recovered_attempts = 0
        self.phase = "CREATED"
        self.observations: list[dict[str, Any]] = []
        self.counters = {"decisions": 0, "searches": 0, "reads": 0, "retries": 0,
                         "restarts": 0, "repairs": 0, "cache_hits": 0, "provider_requests": 0}
        self.usage = {"prompt_tokens": None, "output_tokens": None}
        self.live_response_received = False
        self.last_error = None

    def _new_tools(self):
        return ConnectorTools(self.store, self.request.principal, self.caches, self.provider.identity, self.observer)

    def _hook(self, name: str) -> None:
        callback = self.hooks.get(name)
        if callback:
            callback(self)

    def _emit(self, surface: str, payload: Any) -> None:
        if self.observer:
            self.observer(surface, {"principal": self.request.principal, "epoch": self.store.epoch, "payload": copy.deepcopy(payload)})

    def _check_budget(self) -> None:
        if self.cancellation.is_set():
            raise ResearchError("cancelled")
        if self.clock() >= self.deadline:
            raise ResearchError("budget_exhausted")

    def _reset(self) -> None:
        self.observations.clear()
        self.tools.clear()
        self.caches.clear()
        self.last_error = None
        if self.counters["restarts"] >= 1:
            raise ResearchError("source_state_changed")
        self.counters["restarts"] += 1
        self.tools = self._new_tools()
        self.phase = "PLANNING"
        self.store.audit(self.run_id, "invalidate", "invalidated")
        self._emit("invalidated", {"restart": self.counters["restarts"]})

    def _memo_key(self) -> tuple:
        return self.caches.key(self.store.cache_namespace, self.request.principal, self.store.epoch,
                               self.provider.identity, self.request.vendor.casefold(), self.request.as_of)

    def _restore_cached(self) -> dict | None:
        self.caches.align(self.store.cache_namespace, self.store.epoch)
        entry = self.caches.memos.get(self._memo_key())
        if entry is None:
            self._emit("cache_lookup", {"status": "miss"})
            return None
        sources = {source.object_id: source for source in entry["dependencies"]}
        if any(self.store.source_for(self.request.principal, source.object_id) != source for source in sources.values()):
            self.caches.clear()
            raise ResearchError("source_state_changed")
        proposals = []
        for topic in entry["memo"]["topics"]:
            claims = []
            for claim in topic["claims"]:
                citation = claim["citation"]
                source = sources.get(citation["source_id"])
                if source is None:
                    raise ResearchError("invalid_memo")
                handle = "e_" + uuid.uuid4().hex
                self.tools.evidence[handle] = Evidence(handle, source, self.store.epoch)
                self.tools.read_handles.add(handle)
                claims.append({"value": claim["value"], "handle": handle,
                               "start": citation["start"], "end": citation["end"]})
            proposals.append({"topic": topic["topic"], "state": topic["state"], "reason": topic["reason"], "claims": claims})
        self.counters["cache_hits"] += 1
        memo = validate_memo(proposals, self.request, self.tools)
        self._emit("cache_lookup", memo)
        self.store.audit(self.run_id, "cache", "ok")
        return memo

    def _release(self, memo: dict, cached: bool = False) -> None:
        self._check_budget()
        self.tools.check_context()
        if len(json.dumps(memo).encode()) > 16 * 1024:
            raise ResearchError("budget_exhausted")
        if not cached:
            dependencies = tuple({e.source.object_id: e.source for e in self.tools.evidence.values()}.values())
            self.caches.memos.put(self._memo_key(), {"memo": memo, "dependencies": dependencies})
        # Caller handoff occurs inside the write transaction. A subsequent
        # visual display is an already released snapshot, not revocable export.
        self.sink.release(memo)
        self._emit("output", memo)
        self.store.audit(self.run_id, "release", "ok", len(memo["topics"]))
        self._hook("inside_release")

    def _dispatch(self) -> ProviderResponse:
        self._hook("before_dispatch")
        pending = None
        try:
            with self.store.boundary():
                self._check_budget()
                self.tools.check_context()
                context = {"protocol": PROTOCOL, "request": {"vendor": self.request.vendor, "as_of": self.request.as_of},
                           "phase": self.phase, "tools": TOOLS, "observations": self.observations,
                           "remaining_decisions": self.max_decisions - self.counters["decisions"],
                           "last_error": self.last_error}
                wire = json.dumps(context)
                if len(wire.encode()) > 48 * 1024:
                    raise ResearchError("budget_exhausted")
                self.counters["decisions"] += 1
                self.counters["provider_requests"] += 1
                self.store.progress_attempt(self.run_id, self.counters)
                pending = self.provider.submit(json.loads(wire), self.deadline)
                self._emit("provider_request", context)
                self.store.audit(self.run_id, "dispatch", "ok")
            self._hook("after_dispatch")
            self._check_budget()
            response = self.provider.collect(pending, self.deadline)
            self.live_response_received = self.live_response_received or self.provider.live_model_executed
            self._check_budget()
            with self.store.boundary():
                # Reject the whole response before parsing if inference saw
                # evidence invalidated while it was outstanding.
                self.tools.check_context()
                for name, value in (("prompt_tokens", response.prompt_tokens), ("output_tokens", response.output_tokens)):
                    if value is not None:
                        if type(value) is not int or value < 0:
                            raise ResearchError("invalid_decision")
                        self.usage[name] = (self.usage[name] or 0) + value
                return response
        except ResearchError:
            raise
        except (TimeoutError, OSError):
            raise ResearchError("provider_timeout") from None
        except sqlite3.Error:
            raise
        except Exception:
            raise ResearchError("provider_error") from None
        finally:
            if pending is not None:
                pending.close()

    def _tool(self, action: Action) -> dict:
        began = self.clock()
        counter, limit = ("searches", 8) if action.name == "search" else ("reads", 12)
        for attempt in range(2):
            self._check_budget()
            if self.counters[counter] >= limit or self.clock() - began >= 1:
                raise ResearchError("budget_exhausted")
            self.counters[counter] += 1
            try:
                self._hook("before_tool")
                with self.store.boundary():
                    self.tools.check_context()
                    self.store.progress_attempt(self.run_id, self.counters)
                    result = getattr(self.tools, action.name)(**action.arguments)
                    self._observe(action, result, len(result.get("hits", [])) if action.name == "search" else int(result["status"] == "ok"))
                self._hook("after_tool")
                if self.clock() - began >= 1:
                    raise ResearchError("budget_exhausted")
                return result
            except sqlite3.OperationalError as exc:
                if attempt or not ("locked" in str(exc).lower() or "busy" in str(exc).lower()):
                    raise ResearchError("storage_error") from None
            except ResearchError as exc:
                if exc.code != "tool_unavailable" or attempt:
                    raise
            self.counters["retries"] += 1
            self.store.audit(self.run_id, "retry", "ok")
        raise ResearchError("tool_unavailable")

    def run(self) -> dict[str, Any]:
        if self._has_run:
            raise ResearchError("attempt_state_conflict")
        self._has_run = True
        with self.store.attempt_session():
            self.recovered_attempts = self.store.begin_attempt(self.request_key, self.request, POLICY_VERSION, self.run_id)
            if self.recovered_attempts or self.store.request_key_reused:
                # Explicit key reuse is a new research attempt, including for
                # API callers that supplied a warm process-local memo cache.
                self.observations.clear()
                self.tools.clear()
                self.caches.clear()
                self.tools = self._new_tools()
            return self._run_attempt()

    def _run_attempt(self) -> dict[str, Any]:
        started = self.clock()
        self.deadline = started + self.timeout_seconds
        memo, error, outcome = None, None, "failed"
        self.phase = "PLANNING"
        try:
            with self.store.boundary():
                self._check_budget()
                memo = self._restore_cached()
                if memo is not None:
                    self._release(memo, cached=True)
            while memo is None and self.counters["decisions"] < self.max_decisions:
                try:
                    self._check_budget()
                    response = self._dispatch()
                    action = Action.parse(response.decision)
                    self.last_error = None
                    if self.phase == "PLANNING":
                        if action.name != "plan":
                            raise ResearchError("invalid_decision")
                        self.phase = "RESEARCHING"
                        self._observe(action, {"status": "ok"})
                    elif action.name in {"search", "read"}:
                        self._tool(action)
                    elif action.name == "revise_plan":
                        if any(o["action"] == "revise_plan" for o in self.observations):
                            raise ResearchError("invalid_decision")
                        self._observe(action, {"status": "ok"})
                    elif action.name == "finish":
                        self._hook("before_release")
                        with self.store.boundary():
                            self.phase = "VALIDATING"
                            accepted = validate_memo(action.arguments["topics"], self.request, self.tools)
                            self._release(accepted)
                            self.store.audit(self.run_id, "finish", "ok", len(accepted["topics"]))
                            memo = accepted
                    else:
                        raise ResearchError("invalid_decision")
                except ResearchError as exc:
                    if exc.code == "source_state_changed":
                        self._reset()
                    elif exc.code in {"invalid_memo", "invalid_decision"} and self.counters["repairs"] < 1:
                        self.counters["repairs"] += 1
                        self.last_error = exc.code
                        self.phase = "PLANNING" if not self.observations else "RESEARCHING"
                    else:
                        raise
            if memo is None:
                raise ResearchError("budget_exhausted")
            outcome = memo["outcome"]
            self.phase = "COMPLETED" if outcome == "ready_for_review" else "NEEDS_FOLLOW_UP"
        except ResearchError as exc:
            error = exc.code
            if error == "cancelled":
                outcome, self.phase = "cancelled", "CANCELLED"
            else:
                self.phase = "BUDGET_EXHAUSTED" if error == "budget_exhausted" else "FAILED"
        except KeyboardInterrupt:
            error, outcome, self.phase = "cancelled", "cancelled", "CANCELLED"
        except (sqlite3.Error, UnicodeError, TypeError, KeyError):
            error, self.phase = "storage_error", "FAILED"
        if error:
            memo = None
            self.observations.clear()
            self.tools.clear()
            self.caches.clear()
            self.store.audit(self.run_id, "failure", outcome)
        self.counters["cache_hits"] += self.tools.cache_hits
        self.store.audit(self.run_id, "result", outcome)
        self.store.finish_attempt(self.run_id, self.request.principal, outcome, self.counters)
        return {"protocol": PROTOCOL, "evidence_kind": self.provider.evidence_kind,
                "live_model_executed": self.live_response_received, "run_id": self.run_id,
                "principal": self.request.principal, "outcome": outcome, "phase": self.phase,
                "recovered_attempts": self.recovered_attempts,
                "error": error, "memo": memo, "counters": dict(self.counters),
                "model_tokens": None if self.usage["prompt_tokens"] is None and self.usage["output_tokens"] is None else dict(self.usage),
                "elapsed_seconds": round(self.clock() - started, 6)}

    def _observe(self, action: Action, result: dict[str, Any], count: int = 0) -> None:
        self.observations.append({"action": action.name, "arguments": action.arguments, "result": result})
        self.store.audit(self.run_id, action.name, result["status"], count)
