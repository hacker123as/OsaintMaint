"""Watch rule evaluation and bounded polling."""

from __future__ import annotations

from dataclasses import asdict
import time
from typing import Callable

from .models import Event, WatchRule, utc_now
from .storage import Store


def evaluate(store: Store, events: list[Event], *, notify: Callable[[WatchRule, Event], None] | None = None) -> list[tuple[WatchRule, Event]]:
    store.upsert_events(events)
    hits: list[tuple[WatchRule, Event]] = []
    for rule in store.list_rules():
        if not rule.enabled:
            continue
        for event in events:
            if rule.matches(event) and store.record_hit(rule, event, utc_now()):
                hits.append((rule, event))
                if notify:
                    notify(rule, event)
    store.audit("watch_evaluate", {"events": len(events), "new_hits": len(hits)}, utc_now())
    return hits


def poll(client, store: Store, *, interval_seconds: float = 0.0, once: bool = True, notify=None) -> int:
    total_hits = 0
    while True:
        events = client.fetch_events()
        total_hits += len(evaluate(store, events, notify=notify))
        if once:
            return total_hits
        time.sleep(max(1.0, interval_seconds))
