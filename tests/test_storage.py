import tempfile
import unittest
from pathlib import Path

from osaintmaint.models import Event, WatchRule
from osaintmaint.storage import Store
from osaintmaint.watch import evaluate


class StorageTests(unittest.TestCase):
    def test_upsert_search_and_deduplicated_watch_hits(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "test.db")
            event = Event.from_mapping({"id": "1", "title": "Power outage", "category": "utility", "severity": "medium"}, source="test")
            store.upsert_events([event])
            rule_id = store.add_rule(WatchRule(None, "utility alerts", query="outage", min_severity="low"))
            rule = store.list_rules()[0]
            self.assertEqual(rule.id, rule_id)
            self.assertEqual(len(store.search(query="outage")), 1)
            self.assertEqual(len(evaluate(store, [event])), 1)
            self.assertEqual(len(evaluate(store, [event])), 0)
            self.assertEqual(store.stats()["watch_hits"], 1)
            store.record_watchdog("2026-09-29T00:00:00Z", "incidents", "ok", 1, 1, 42)
            self.assertEqual(store.stats()["watchdog_runs"], 1)
            self.assertEqual(store.recent_watchdogs(1)[0]["latency_ms"], 42)
            self.assertEqual(store.quality()["events"], 1)
            store.close()


if __name__ == "__main__":
    unittest.main()
