"""Local connector tools. A provider receives handles, never store authority."""

from __future__ import annotations

import re
import uuid
from typing import Any

from .domain import CONNECTORS, Evidence, ResearchError, Source
from .cache import RuntimeCaches
from .store import CorpusStore


def tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.casefold()))


class LocalConnector:
    def __init__(self, name: str, store: CorpusStore, principal: str):
        if name not in CONNECTORS:
            raise ValueError("Unknown connector")
        self.name, self.store, self.principal = name, store, principal

    def search(self, query: str, limit: int) -> list[Source]:
        query_terms = tokens(query)
        ranked = []
        for source in self.store.authorized_sources(self.principal, connector=self.name):
            score = len(query_terms & tokens(source.subject + " " + source.title + " " + source.body))
            if score:
                ranked.append((score, source))
        ranked.sort(key=lambda item: (-item[0], item[1].object_id))
        return [source for _, source in ranked[:limit]]


class ConnectorTools:
    def __init__(self, store: CorpusStore, principal: str, caches: RuntimeCaches | None = None,
                 provider_id: str = "scripted-m2-v1", observer=None):
        self.store, self.principal = store, principal
        store.require_principal(principal)
        self.connectors = {name: LocalConnector(name, store, principal) for name in CONNECTORS}
        self.evidence: dict[str, Evidence] = {}
        self.read_handles: set[str] = set()
        self.epoch = store.epoch
        self.caches = caches or RuntimeCaches()
        self.provider_id, self.observer = provider_id, observer
        self.cache_hits = 0

    def clear(self) -> None:
        self.evidence.clear()
        self.read_handles.clear()

    def _observe(self, surface: str, payload: Any) -> None:
        if self.observer:
            self.observer(surface, {"principal": self.principal, "epoch": self.store.epoch, "payload": payload})

    def _synchronize(self) -> None:
        current_epoch = self.store.epoch
        self.caches.align(self.store.cache_namespace, current_epoch)
        if current_epoch != self.epoch:
            self.clear()
            raise ResearchError("source_state_changed")

    def check_context(self) -> None:
        """Includes search snippets, not just documents explicitly opened."""
        self._synchronize()
        for evidence in self.evidence.values():
            current = self.store.source_for(self.principal, evidence.source.object_id)
            if current != evidence.source:
                raise ResearchError("source_state_changed")

    def search(self, connector: str, query: str, limit: int) -> dict[str, Any]:
        with self.store.boundary():
            self._synchronize()
            return self._search(connector, query, limit)

    def _search(self, connector: str, query: str, limit: int) -> dict[str, Any]:
        normalized = " ".join(query.casefold().split())
        key = self.caches.key(self.store.cache_namespace, self.principal, self.epoch,
                              self.provider_id, "search", connector, normalized, limit)
        sources = self.caches.evidence.get(key)
        if sources is not None:
            if any(self.store.source_for(self.principal, s.object_id) != s for s in sources):
                self.caches.clear()
                raise ResearchError("source_state_changed")
            self.cache_hits += 1
            self._observe("cache_lookup", [s.__dict__ for s in sources])
        else:
            self._observe("cache_lookup", {"status": "miss"})
            sources = self.connectors[connector].search(query, limit)
            self.caches.evidence.put(key, sources)
        hits = []
        for source in sources:
            handle = "e_" + uuid.uuid4().hex
            self.evidence[handle] = Evidence(handle, source, self.epoch)
            hits.append({"handle": handle, "connector": connector, "title": source.title,
                         "excerpt": source.body[:160], "revision": source.revision,
                         "effective_at": source.effective_at, "review_until": source.review_until})
        result = {"status": "ok", "hits": hits}
        self._observe("tool_output", result)
        return result

    def read(self, handle: str) -> dict[str, Any]:
        with self.store.boundary():
            if self.store.epoch != self.epoch:
                self.caches.align(self.store.cache_namespace, self.store.epoch)
                self.clear()
                result = {"status": "not_available"}
                self._observe("tool_output", result)
                return result
            return self._read(handle)

    def _read(self, handle: str) -> dict[str, Any]:
        evidence = self.evidence.get(handle)
        if evidence is None:
            result = {"status": "not_available"}
            self._observe("tool_output", result)
            return result
        source = self.store.source_for(self.principal, evidence.source.object_id)
        if source != evidence.source or evidence.epoch != self.epoch:
            result = {"status": "not_available"}
            self._observe("tool_output", result)
            return result
        self.read_handles.add(handle)
        result = {"status": "ok", "handle": handle, "connector": source.connector,
                "title": source.title, "revision": source.revision, "subject": source.subject,
                "body": source.body, "effective_at": source.effective_at,
                "review_until": source.review_until}
        self._observe("tool_output", result)
        return result

    def resolve_citation(self, citation: dict[str, Any]) -> dict[str, Any]:
        """Authorization-gated local resolver, also used by adversarial tests."""
        with self.store.boundary():
            if self.store.epoch != self.epoch:
                self.caches.align(self.store.cache_namespace, self.store.epoch)
                self.clear()
                result = {"status": "not_available"}
                self._observe("citation_resolution", result)
                return result
            source = self.store.source_for(self.principal, citation.get("source_id"))
            start, end = citation.get("start"), citation.get("end")
            if (source is None or citation.get("revision") != source.revision or
                    type(start) is not int or type(end) is not int or not 0 <= start < end <= len(source.body) or
                    source.body[start:end] != citation.get("quote")):
                result = {"status": "not_available"}
            else:
                result = {"status": "ok", "quote": source.body[start:end]}
            self._observe("citation_resolution", result)
            return result
