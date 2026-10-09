"""Two bounded, epoch/identity-scoped in-memory caches; no disk serialization."""

from __future__ import annotations

import copy
import time
from collections import OrderedDict
from typing import Any, Callable

POLICY_VERSION = "permissioned-research-m2-v2"


class BoundedCache:
    def __init__(self, max_entries: int = 32, ttl_seconds: float = 60, clock: Callable[[], float] = time.monotonic):
        if not 1 <= max_entries <= 32 or not 0 < ttl_seconds <= 60:
            raise ValueError("Invalid cache limits")
        self.max_entries, self.ttl_seconds, self.clock = max_entries, ttl_seconds, clock
        self.entries: OrderedDict[tuple, tuple[float, Any]] = OrderedDict()

    def get(self, key: tuple) -> Any | None:
        entry = self.entries.get(key)
        if entry is None:
            return None
        expires, value = entry
        if self.clock() >= expires:
            del self.entries[key]
            return None
        self.entries.move_to_end(key)
        return copy.deepcopy(value)

    def put(self, key: tuple, value: Any) -> None:
        self.entries[key] = (self.clock() + self.ttl_seconds, copy.deepcopy(value))
        self.entries.move_to_end(key)
        while len(self.entries) > self.max_entries:
            self.entries.popitem(last=False)

    def clear(self) -> None:
        self.entries.clear()


class RuntimeCaches:
    def __init__(self):
        self.evidence = BoundedCache()
        self.memos = BoundedCache()
        self.observed_epochs: dict[str, int] = {}

    def align(self, namespace: str, epoch: int) -> bool:
        previous = self.observed_epochs.get(namespace)
        changed = previous is not None and previous != epoch
        if changed:
            # Coarse invalidation includes both caches and every principal.
            self.clear()
        if namespace not in self.observed_epochs and len(self.observed_epochs) >= 32:
            self.observed_epochs.clear()
            self.clear()
        self.observed_epochs[namespace] = epoch
        return changed

    def key(self, namespace: str, principal: str, epoch: int, provider_id: str, *query: Any) -> tuple:
        return (namespace, principal, epoch, POLICY_VERSION, provider_id, *query)

    def clear(self) -> None:
        self.evidence.clear()
        self.memos.clear()
