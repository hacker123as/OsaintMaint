"""Canonical data models and normalization helpers.

The normalizer is deliberately conservative: it preserves the original payload,
never fabricates location or timestamps, and treats unknown values as unknown.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Iterable


SEVERITY_RANK = {"unknown": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
SEVERITIES = tuple(SEVERITY_RANK)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _pick(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in item and item[key] not in (None, ""):
            return item[key]
    lowered = {str(k).lower(): v for k, v in item.items()}
    for key in keys:
        if key.lower() in lowered and lowered[key.lower()] not in (None, ""):
            return lowered[key.lower()]
    return None


def normalize_timestamp(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        except (OverflowError, OSError, ValueError):
            return ""
    raw = _text(value)
    if not raw:
        return ""
    candidate = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return raw
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_severity(value: Any) -> str:
    value = _text(value).lower()
    aliases = {"info": "low", "notice": "low", "moderate": "medium", "urgent": "high", "emergency": "critical"}
    if value in SEVERITY_RANK:
        return value
    return aliases.get(value, "unknown")


def _float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _location_fields(item: dict[str, Any]) -> tuple[float | None, float | None, str]:
    location = _pick(item, "location")
    if not isinstance(location, dict):
        location = {}
    nested_latitude = _float(_pick(location, "latitude", "lat"))
    nested_longitude = _float(_pick(location, "longitude", "lon", "lng"))
    latitude = nested_latitude if nested_latitude is not None else _float(_pick(item, "latitude", "lat", "y"))
    longitude = nested_longitude if nested_longitude is not None else _float(_pick(item, "longitude", "lon", "lng", "long", "x"))
    parts = [
        _text(_pick(location, "address", "street")),
        _text(_pick(location, "city")),
        _text(_pick(location, "county")),
        _text(_pick(location, "state")),
        _text(_pick(location, "postalCode", "postal_code", "zip")),
    ]
    return latitude, longitude, ", ".join(part for part in parts if part) or _text(_pick(item, "address", "place", "street"))


@dataclass(slots=True)
class Event:
    id: str
    source: str
    source_id: str
    title: str
    description: str = ""
    category: str = "unknown"
    severity: str = "unknown"
    status: str = ""
    occurred_at: str = ""
    updated_at: str = ""
    latitude: float | None = None
    longitude: float | None = None
    address: str = ""
    url: str = ""
    observed_at: str = field(default_factory=utc_now)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, item: dict[str, Any], *, source: str) -> "Event":
        if not isinstance(item, dict):
            raise TypeError("event must be a JSON object")
        provenance = _pick(item, "source")
        if not isinstance(provenance, dict):
            provenance = {}
        resolved_source = _text(_pick(provenance, "provider")) or source
        source_id = _text(_pick(provenance, "sourceRecordId")) or _text(_pick(item, "id", "event_id", "incident_id", "case_id", "uuid"))
        kind = _text(_pick(item, "kind")).lower()
        title = _text(_pick(item, "title", "name", "headline", "summary", "incidentType", "dispatchType", "indicator", "matterName", "docketNumber", "bookingId", "custodyId", "incidentNumber", "reportType", "type")) or "Untitled event"
        description = _text(_pick(item, "description", "details", "narrative", "notes", "message", "warningMessage", "rewardText", "topCharge", "disposition"))
        occurred = normalize_timestamp(_pick(item, "occurred_at", "occurredAt", "dispatchedAt", "arrestedAt", "admittedAt", "filedAt", "reportedAt", "timestamp", "created_at", "modifiedAt", "date"))
        url = _text(_pick(provenance, "sourceUrl")) or _text(_pick(item, "url", "link", "source_url"))
        canonical = {"source": resolved_source, "source_id": source_id, "title": title, "occurred_at": occurred, "url": url}
        latitude, longitude, address = _location_fields(item)
        category_aliases = {"incident": "incident", "dispatch": "dispatch", "fire_ems": "fire_ems", "wanted": "wanted", "missing_person": "missing", "arrest": "arrest", "jail": "jail", "court": "court", "stolen_vehicle": "stolen_vehicle", "registry": "registry", "crime_statistic": "statistics"}
        event_id = hashlib.sha256(json.dumps(canonical, sort_keys=True).encode("utf-8")).hexdigest()[:24]
        return cls(
            id=event_id,
            source=resolved_source,
            source_id=source_id,
            title=title,
            description=description,
            category=category_aliases.get(kind, _text(_pick(item, "category", "type", "event_type", "incident_type")).lower() or "unknown"),
            severity=normalize_severity(_pick(item, "severity", "priority", "urgency", "level")),
            status=_text(_pick(item, "status", "state", "disposition")).lower(),
            occurred_at=occurred,
            updated_at=normalize_timestamp(_pick(item, "updated_at", "updatedAt", "modified_at", "modifiedAt")),
            latitude=latitude,
            longitude=longitude,
            address=address,
            url=url,
            raw=item,
        )

    def searchable_text(self) -> str:
        return " ".join((self.title, self.description, self.category, self.status, self.address)).lower()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class WatchRule:
    id: int | None
    name: str
    query: str = ""
    categories: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    min_severity: str = "unknown"
    bbox: tuple[float, float, float, float] | None = None
    enabled: bool = True

    def matches(self, event: Event) -> bool:
        if self.query and not re.search(re.escape(self.query.lower()), event.searchable_text()):
            return False
        if self.categories and event.category.lower() not in {c.lower() for c in self.categories}:
            return False
        if self.sources and event.source.lower() not in {s.lower() for s in self.sources}:
            return False
        if SEVERITY_RANK.get(event.severity, 0) < SEVERITY_RANK.get(self.min_severity, 0):
            return False
        if self.bbox:
            if event.latitude is None or event.longitude is None:
                return False
            min_lat, min_lon, max_lat, max_lon = self.bbox
            if not (min_lat <= event.latitude <= max_lat and min_lon <= event.longitude <= max_lon):
                return False
        return True


def extract_records(payload: Any) -> list[dict[str, Any]]:
    """Extract event objects from common JSON envelope shapes."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        raise ValueError("API payload must be a JSON array or object")
    for key in ("events", "results", "incidents", "data", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    if all(not isinstance(value, (list, dict)) for value in payload.values()):
        return [payload]
    raise ValueError("Could not find an event list in the API payload")
