"""Bounded HTTP connector for compatible public-safety JSON APIs."""

from __future__ import annotations

import json
import os
from pathlib import Path
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

from ..config import SourceConfig
from ..models import Event, extract_records


class ConnectorError(RuntimeError):
    """A safe, user-facing source connector error."""


class PublicSafetyClient:
    RESOURCE_PATHS = {
        "incidents": "/api/v1/incidents",
        "dispatch": "/api/v1/dispatch",
        "wanted": "/api/v1/wanted",
        "missing": "/api/v1/missing",
        "arrests": "/api/v1/arrests",
        "jail": "/api/v1/jail",
        "courts": "/api/v1/courts",
        "stolen-vehicles": "/api/v1/stolen-vehicles",
        "registry": "/api/v1/registry",
        "statistics": "/api/v1/statistics",
        "fire-ems": "/api/v1/fire-ems",
        "sources": "/api/v1/sources",
        "capabilities": "/api/v1/capabilities",
    }

    def __init__(self, config: SourceConfig):
        self.config = config
        self._last_request = 0.0

    def _url(self) -> str:
        if not self.config.base_url:
            raise ConnectorError("No API base_url is configured")
        if not self.config.base_url.startswith(("https://", "http://")):
            raise ConnectorError("API base_url must use http:// or https://")
        events_path = self.config.events_path or self.RESOURCE_PATHS.get(self.config.resource)
        if not events_path:
            raise ConnectorError(f"Unsupported resource: {self.config.resource}")
        if events_path.startswith(("https://", "http://")):
            raise ConnectorError("events_path must be a relative path")
        return urljoin(self.config.base_url.rstrip("/") + "/", events_path.lstrip("/"))

    def fetch_payload(self, extra_params: dict[str, Any] | None = None) -> Any:
        elapsed = time.monotonic() - self._last_request
        wait_for = self.config.min_request_interval_seconds - elapsed
        if wait_for > 0:
            time.sleep(min(wait_for, 60.0))
        params = dict(self.config.params)
        params.update(extra_params or {})
        if "limit" in params:
            try:
                params["limit"] = max(1, min(int(params["limit"]), self.config.max_records, 100))
            except (TypeError, ValueError) as exc:
                raise ConnectorError("limit must be an integer") from exc
        url = self._url()
        if params:
            url = f"{url}?{urlencode(params, doseq=True)}"
        headers = {"Accept": "application/json", "User-Agent": self.config.user_agent}
        api_key = os.getenv(self.config.api_key_env, "") if self.config.api_key_env else ""
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        request = Request(url, headers=headers, method="GET")
        try:
            with urlopen(request, timeout=self.config.timeout_seconds) as response:
                self._last_request = time.monotonic()
                content_length = int(response.headers.get("Content-Length", "0") or 0)
                if content_length > self.config.max_response_bytes:
                    raise ConnectorError("API response exceeds configured size limit")
                chunks: list[bytes] = []
                total = 0
                while True:
                    chunk = response.read(min(64 * 1024, self.config.max_response_bytes - total + 1))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > self.config.max_response_bytes:
                        raise ConnectorError("API response exceeds configured size limit")
                return json.loads(b"".join(chunks).decode("utf-8"))
        except HTTPError as exc:
            raise ConnectorError(f"API returned HTTP {exc.code}") from exc
        except URLError as exc:
            raise ConnectorError(f"Could not reach API: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise ConnectorError("API did not return valid JSON") from exc

    def fetch_events(self, extra_params: dict[str, Any] | None = None) -> list[Event]:
        payload = self.fetch_payload(extra_params)
        records = extract_records(payload)
        bounded = records[: max(1, self.config.max_records)]
        return [Event.from_mapping(record, source=self.config.base_url) for record in bounded]

    def fetch_metadata(self, resource: str = "sources") -> Any:
        original = self.config.resource
        try:
            self.config.resource = resource
            return self.fetch_payload()
        finally:
            self.config.resource = original


def load_fixture(path: str | Path, *, source: str = "fixture") -> list[Event]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return [Event.from_mapping(record, source=source) for record in extract_records(payload)]
