import unittest

from osaintmaint.models import Event, WatchRule, extract_records


class ModelTests(unittest.TestCase):
    def test_normalizes_aliases_and_epoch(self):
        event = Event.from_mapping({"incident_id": "7", "summary": "Medical call", "priority": "urgent", "timestamp": 0, "lat": "1.5", "lng": "-2"}, source="test")
        self.assertEqual(event.source_id, "7")
        self.assertEqual(event.title, "Medical call")
        self.assertEqual(event.severity, "high")
        self.assertEqual(event.occurred_at, "1970-01-01T00:00:00Z")
        self.assertEqual(event.latitude, 1.5)

    def test_watch_rule_matches_keywords_and_bbox(self):
        event = Event.from_mapping({"id": "1", "title": "Brush fire", "category": "fire", "severity": "high", "latitude": 40, "longitude": -74}, source="test")
        rule = WatchRule(1, "nearby fire", query="fire", categories=("fire",), min_severity="high", bbox=(39, -75, 41, -73))
        self.assertTrue(rule.matches(event))
        outside = WatchRule(2, "outside", query="fire", bbox=(41, -75, 42, -73))
        self.assertFalse(outside.matches(event))

    def test_extracts_common_envelopes(self):
        self.assertEqual(len(extract_records({"results": [{"id": "1"}]})), 1)
        self.assertEqual(len(extract_records([{"id": "1"}])), 1)

    def test_normalizes_tuerss_unified_record_and_provenance(self):
        event = Event.from_mapping({
            "kind": "incident",
            "id": "unified-1",
            "incidentType": "THEFT",
            "description": "Public incident record",
            "occurredAt": "2026-09-29T12:00:00.000Z",
            "location": {"city": "Chicago", "state": "IL", "latitude": 41.88, "longitude": -87.63},
            "source": {"provider": "chicago-crimes", "sourceRecordId": "123", "sourceUrl": "https://data.example/123"},
        }, source="https://psp.tuerss.dev")
        self.assertEqual(event.source, "chicago-crimes")
        self.assertEqual(event.source_id, "123")
        self.assertEqual(event.title, "THEFT")
        self.assertEqual(event.category, "incident")
        self.assertEqual(event.address, "Chicago, IL")
        self.assertEqual(event.url, "https://data.example/123")


if __name__ == "__main__":
    unittest.main()
