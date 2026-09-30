"""Reusable watchdog polling service for TuerSS and compatible sources."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Callable

from .models import Event, WatchRule, utc_now
from .storage import Store
from .watch import evaluate


@dataclass(slots=True)
class WatchdogResult:
    resource: str
    status: str
    fetched: int
    new_hits: int
    latency_ms: int
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource": self.resource,
            "status": self.status,
            "fetched": self.fetched,
            "new_hits": self.new_hits,
            "latency_ms": self.latency_ms,
            "error": self.error,
        }


def run_once(client, store: Store, *, params: dict[str, Any] | None = None, notify: Callable[[WatchRule, Event], None] | None = None) -> WatchdogResult:
    started = time.perf_counter()
    resource = client.config.resource
    try:
        events = client.fetch_events(params or {})
        hits = evaluate(store, events, notify=notify)
        result = WatchdogResult(resource, "ok", len(events), len(hits), int((time.perf_counter() - started) * 1000))
    except Exception as exc:
        result = WatchdogResult(resource, "error", 0, 0, int((time.perf_counter() - started) * 1000), str(exc))
    store.record_watchdog(utc_now(), result.resource, result.status, result.fetched, result.new_hits, result.latency_ms, result.error)
    store.audit("watchdog_run", result.to_dict(), utc_now())
    return result
