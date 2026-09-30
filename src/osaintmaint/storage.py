"""SQLite persistence, search, watch rules, and audit logging."""

from __future__ import annotations

from dataclasses import asdict
import csv
from html import escape
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable

from .models import Event, WatchRule


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.initialize()

    def initialize(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY, source TEXT NOT NULL, source_id TEXT NOT NULL,
                title TEXT NOT NULL, description TEXT NOT NULL, category TEXT NOT NULL,
                severity TEXT NOT NULL, status TEXT NOT NULL, occurred_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, latitude REAL, longitude REAL, address TEXT NOT NULL,
                url TEXT NOT NULL, observed_at TEXT NOT NULL, raw_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_events_occurred ON events(occurred_at);
            CREATE INDEX IF NOT EXISTS idx_events_category ON events(category);
            CREATE TABLE IF NOT EXISTS watch_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
                query TEXT NOT NULL, categories_json TEXT NOT NULL, sources_json TEXT NOT NULL,
                min_severity TEXT NOT NULL, bbox_json TEXT, enabled INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS watch_hits (
                rule_id INTEGER NOT NULL REFERENCES watch_rules(id) ON DELETE CASCADE,
                event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                first_seen_at TEXT NOT NULL, PRIMARY KEY(rule_id, event_id)
            );
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
                action TEXT NOT NULL, details_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS watchdog_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
                resource TEXT NOT NULL, status TEXT NOT NULL, fetched INTEGER NOT NULL,
                new_hits INTEGER NOT NULL, latency_ms INTEGER NOT NULL, error TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS site_bookmarks (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
                url TEXT NOT NULL UNIQUE, added_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS osint_tools (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, repository_url TEXT NOT NULL UNIQUE,
                clone_url TEXT NOT NULL, category TEXT NOT NULL, description TEXT NOT NULL,
                catalog_source TEXT NOT NULL, catalog_commit TEXT NOT NULL, synced_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tool_installs (
                tool_id TEXT PRIMARY KEY REFERENCES osint_tools(id) ON DELETE CASCADE,
                installed_path TEXT NOT NULL, commit_sha TEXT NOT NULL, installed_at TEXT NOT NULL,
                status TEXT NOT NULL, error TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tool_favorites (
                tool_id TEXT PRIMARY KEY REFERENCES osint_tools(id) ON DELETE CASCADE,
                favorited_at TEXT NOT NULL
            );
            """
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def upsert_events(self, events: Iterable[Event]) -> int:
        count = 0
        for event in events:
            self.connection.execute(
                """INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET title=excluded.title, description=excluded.description,
                category=excluded.category, severity=excluded.severity, status=excluded.status,
                occurred_at=excluded.occurred_at, updated_at=excluded.updated_at, latitude=excluded.latitude,
                longitude=excluded.longitude, address=excluded.address, url=excluded.url,
                observed_at=excluded.observed_at, raw_json=excluded.raw_json""",
                (event.id, event.source, event.source_id, event.title, event.description, event.category,
                 event.severity, event.status, event.occurred_at, event.updated_at, event.latitude,
                 event.longitude, event.address, event.url, event.observed_at, json.dumps(event.raw, sort_keys=True)),
            )
            count += 1
        self.connection.commit()
        return count

    def _event(self, row: sqlite3.Row) -> Event:
        data = dict(row)
        data["raw"] = json.loads(data.pop("raw_json"))
        return Event(**data)

    def search(self, query: str = "", category: str = "", severity: str = "", since: str = "", limit: int = 100) -> list[Event]:
        clauses: list[str] = []
        args: list[Any] = []
        if query:
            like = f"%{query.lower()}%"
            clauses.append("lower(title || ' ' || description || ' ' || category || ' ' || status || ' ' || address) LIKE ?")
            args.append(like)
        if category:
            clauses.append("lower(category) = lower(?)")
            args.append(category)
        if severity:
            clauses.append("lower(severity) = lower(?)")
            args.append(severity)
        if since:
            clauses.append("occurred_at >= ?")
            args.append(since)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.connection.execute(f"SELECT * FROM events {where} ORDER BY coalesce(occurred_at, observed_at) DESC LIMIT ?", (*args, max(1, min(limit, 5000)))).fetchall()
        return [self._event(row) for row in rows]

    def add_rule(self, rule: WatchRule) -> int:
        cursor = self.connection.execute(
            "INSERT INTO watch_rules(name, query, categories_json, sources_json, min_severity, bbox_json, enabled) VALUES(?,?,?,?,?,?,?)",
            (rule.name, rule.query, json.dumps(rule.categories), json.dumps(rule.sources), rule.min_severity, json.dumps(rule.bbox) if rule.bbox else None, int(rule.enabled)),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def list_rules(self) -> list[WatchRule]:
        rows = self.connection.execute("SELECT * FROM watch_rules ORDER BY name").fetchall()
        return [WatchRule(id=row["id"], name=row["name"], query=row["query"], categories=tuple(json.loads(row["categories_json"])), sources=tuple(json.loads(row["sources_json"])), min_severity=row["min_severity"], bbox=tuple(json.loads(row["bbox_json"])) if row["bbox_json"] else None, enabled=bool(row["enabled"])) for row in rows]

    def remove_rule(self, name: str) -> bool:
        cursor = self.connection.execute("DELETE FROM watch_rules WHERE name = ?", (name,))
        self.connection.commit()
        return cursor.rowcount > 0

    def record_hit(self, rule: WatchRule, event: Event, seen_at: str) -> bool:
        cursor = self.connection.execute("INSERT OR IGNORE INTO watch_hits VALUES(?,?,?)", (rule.id, event.id, seen_at))
        self.connection.commit()
        return cursor.rowcount > 0

    def audit(self, action: str, details: dict[str, Any], created_at: str) -> None:
        self.connection.execute("INSERT INTO audit_log(created_at, action, details_json) VALUES(?,?,?)", (created_at, action, json.dumps(details, sort_keys=True)))
        self.connection.commit()

    def recent_audit(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (max(1, min(limit, 500)),)).fetchall()
        return [{"id": row["id"], "created_at": row["created_at"], "action": row["action"], "details": json.loads(row["details_json"])} for row in rows]

    def stats(self) -> dict[str, int]:
        return {
            "events": self.connection.execute("SELECT count(*) FROM events").fetchone()[0],
            "watch_rules": self.connection.execute("SELECT count(*) FROM watch_rules").fetchone()[0],
            "watch_hits": self.connection.execute("SELECT count(*) FROM watch_hits").fetchone()[0],
            "audit_entries": self.connection.execute("SELECT count(*) FROM audit_log").fetchone()[0],
            "watchdog_runs": self.connection.execute("SELECT count(*) FROM watchdog_runs").fetchone()[0],
            "tool_catalog": self.connection.execute("SELECT count(*) FROM osint_tools").fetchone()[0],
            "installed_tools": self.connection.execute("SELECT count(*) FROM tool_installs WHERE status = 'installed'").fetchone()[0],
            "favorite_tools": self.connection.execute("SELECT count(*) FROM tool_favorites").fetchone()[0],
        }

    def quality(self) -> dict[str, Any]:
        total = self.connection.execute("SELECT count(*) FROM events").fetchone()[0]
        missing_location = self.connection.execute("SELECT count(*) FROM events WHERE latitude IS NULL OR longitude IS NULL").fetchone()[0]
        missing_source_url = self.connection.execute("SELECT count(*) FROM events WHERE url = ''").fetchone()[0]
        unknown_severity = self.connection.execute("SELECT count(*) FROM events WHERE severity = 'unknown'").fetchone()[0]
        categories = {row["category"]: row["count"] for row in self.connection.execute("SELECT category, count(*) AS count FROM events GROUP BY category ORDER BY count DESC").fetchall()}
        return {"events": total, "missing_location": missing_location, "missing_source_url": missing_source_url, "unknown_severity": unknown_severity, "categories": categories}

    def record_watchdog(self, created_at: str, resource: str, status: str, fetched: int, new_hits: int, latency_ms: int, error: str = "") -> None:
        self.connection.execute("INSERT INTO watchdog_runs(created_at, resource, status, fetched, new_hits, latency_ms, error) VALUES(?,?,?,?,?,?,?)", (created_at, resource, status, fetched, new_hits, latency_ms, error))
        self.connection.commit()

    def recent_watchdogs(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM watchdog_runs ORDER BY id DESC LIMIT ?", (max(1, min(limit, 200)),)).fetchall()
        return [dict(row) for row in rows]

    def add_site(self, name: str, url: str, added_at: str) -> int:
        cursor = self.connection.execute("INSERT INTO site_bookmarks(name, url, added_at) VALUES(?,?,?)", (name, url, added_at))
        self.connection.commit()
        return int(cursor.lastrowid)

    def list_sites(self) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM site_bookmarks ORDER BY name COLLATE NOCASE").fetchall()
        return [dict(row) for row in rows]

    def get_site(self, name: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM site_bookmarks WHERE name = ?", (name,)).fetchone()
        return dict(row) if row else None

    def remove_site(self, name: str) -> bool:
        cursor = self.connection.execute("DELETE FROM site_bookmarks WHERE name = ?", (name,))
        self.connection.commit()
        return cursor.rowcount > 0

    def upsert_osint_tools(self, tools: Iterable[dict[str, str]]) -> int:
        count = 0
        for tool in tools:
            existing = self.connection.execute(
                "SELECT id FROM osint_tools WHERE repository_url = ?", (tool["repository_url"],)
            ).fetchone()
            if existing and existing["id"] != tool["id"]:
                tool = {**tool, "id": existing["id"]}
            self.connection.execute(
                """INSERT INTO osint_tools(id, name, repository_url, clone_url, category, description, catalog_source, catalog_commit, synced_at)
                VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name, repository_url=excluded.repository_url,
                clone_url=excluded.clone_url, category=excluded.category, description=excluded.description,
                catalog_source=excluded.catalog_source, catalog_commit=excluded.catalog_commit, synced_at=excluded.synced_at""",
                (tool["id"], tool["name"], tool["repository_url"], tool["clone_url"], tool["category"], tool["description"], tool["catalog_source"], tool["catalog_commit"], tool["synced_at"]),
            )
            count += 1
        self.connection.commit()
        return count

    def list_osint_tools(
        self,
        query: str = "",
        limit: int = 50,
        offset: int = 0,
        favorites_only: bool = False,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        args: list[Any] = []
        if query:
            clauses.append("lower(t.name || ' ' || t.category || ' ' || t.description) LIKE ?")
            args.append(f"%{query.lower()}%")
        if favorites_only:
            clauses.append("f.tool_id IS NOT NULL")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.connection.execute(
            f"""SELECT t.*, i.installed_path, i.commit_sha, i.installed_at, i.status AS install_status, i.error AS install_error,
            f.favorited_at, CASE WHEN f.tool_id IS NULL THEN 0 ELSE 1 END AS is_favorite
            FROM osint_tools t LEFT JOIN tool_installs i ON t.id = i.tool_id
            LEFT JOIN tool_favorites f ON t.id = f.tool_id
            {where} ORDER BY CASE WHEN f.tool_id IS NULL THEN 0 ELSE 1 END DESC, lower(t.name), t.id LIMIT ? OFFSET ?""",
            (*args, max(1, min(limit, 200)), max(0, offset)),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_osint_tool(self, tool_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """SELECT t.*, i.installed_path, i.commit_sha, i.installed_at, i.status AS install_status, i.error AS install_error,
            f.favorited_at, CASE WHEN f.tool_id IS NULL THEN 0 ELSE 1 END AS is_favorite
            FROM osint_tools t LEFT JOIN tool_installs i ON t.id = i.tool_id
            LEFT JOIN tool_favorites f ON t.id = f.tool_id WHERE t.id = ?""",
            (tool_id,),
        ).fetchone()
        return dict(row) if row else None

    def favorite_osint_tool(self, tool_id: str, favorited_at: str) -> bool:
        if not self.connection.execute("SELECT 1 FROM osint_tools WHERE id = ?", (tool_id,)).fetchone():
            raise ValueError(f"Tool not found: {tool_id}")
        cursor = self.connection.execute(
            "INSERT OR IGNORE INTO tool_favorites(tool_id, favorited_at) VALUES(?,?)",
            (tool_id, favorited_at),
        )
        self.connection.commit()
        return cursor.rowcount > 0

    def unfavorite_osint_tool(self, tool_id: str) -> bool:
        cursor = self.connection.execute("DELETE FROM tool_favorites WHERE tool_id = ?", (tool_id,))
        self.connection.commit()
        return cursor.rowcount > 0

    def list_favorite_osint_tools(self, query: str = "", limit: int = 100) -> list[dict[str, Any]]:
        return self.list_osint_tools(query=query, limit=limit, favorites_only=True)

    def record_tool_install(self, tool_id: str, installed_path: str, commit_sha: str, installed_at: str, status: str, error: str = "") -> None:
        self.connection.execute(
            """INSERT INTO tool_installs(tool_id, installed_path, commit_sha, installed_at, status, error) VALUES(?,?,?,?,?,?)
            ON CONFLICT(tool_id) DO UPDATE SET installed_path=excluded.installed_path, commit_sha=excluded.commit_sha,
            installed_at=excluded.installed_at, status=excluded.status, error=excluded.error""",
            (tool_id, installed_path, commit_sha, installed_at, status, error),
        )
        self.connection.commit()

    def remove_tool_install(self, tool_id: str) -> bool:
        cursor = self.connection.execute("DELETE FROM tool_installs WHERE tool_id = ?", (tool_id,))
        self.connection.commit()
        return cursor.rowcount > 0

    def list_installed_tools(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """SELECT t.*, i.installed_path, i.commit_sha, i.installed_at, i.status AS install_status, i.error AS install_error,
            f.favorited_at, CASE WHEN f.tool_id IS NULL THEN 0 ELSE 1 END AS is_favorite
            FROM tool_installs i JOIN osint_tools t ON t.id = i.tool_id
            LEFT JOIN tool_favorites f ON t.id = f.tool_id
            WHERE i.status = 'installed'
            ORDER BY CASE WHEN f.tool_id IS NULL THEN 0 ELSE 1 END DESC, i.installed_at DESC LIMIT ?""",
            (max(1, min(limit, 500)),),
        ).fetchall()
        return [dict(row) for row in rows]

    def osint_tool_summary(self, category_limit: int = 12) -> dict[str, Any]:
        categories = self.connection.execute(
            """SELECT t.category, count(*) AS tools,
            sum(CASE WHEN i.status = 'installed' THEN 1 ELSE 0 END) AS installed,
            sum(CASE WHEN f.tool_id IS NOT NULL THEN 1 ELSE 0 END) AS favorites
            FROM osint_tools t LEFT JOIN tool_installs i ON t.id = i.tool_id
            LEFT JOIN tool_favorites f ON t.id = f.tool_id
            GROUP BY t.category ORDER BY tools DESC, lower(t.category) LIMIT ?""",
            (max(1, min(category_limit, 100)),),
        ).fetchall()
        last_synced = self.connection.execute("SELECT max(synced_at) FROM osint_tools").fetchone()[0]
        failed = self.connection.execute("SELECT count(*) FROM tool_installs WHERE status = 'error'").fetchone()[0]
        return {
            "catalog_tools": self.connection.execute("SELECT count(*) FROM osint_tools").fetchone()[0],
            "installed_tools": self.connection.execute("SELECT count(*) FROM tool_installs WHERE status = 'installed'").fetchone()[0],
            "favorite_tools": self.connection.execute("SELECT count(*) FROM tool_favorites").fetchone()[0],
            "failed_installs": failed,
            "last_synced_at": last_synced or "",
            "categories": [dict(row) for row in categories],
        }

    def export_csv(self, events: Iterable[Event], output: str | Path) -> None:
        fields = ["id", "source", "source_id", "title", "description", "category", "severity", "status", "occurred_at", "updated_at", "latitude", "longitude", "address", "url", "observed_at"]
        with Path(output).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for event in events:
                row = event.to_dict()
                row.pop("raw", None)
                writer.writerow({field: row.get(field, "") for field in fields})

    def export_html(self, events: Iterable[Event], output: str | Path, *, title: str = "OsaintMaint Event Report") -> None:
        rows = []
        for event in events:
            rows.append("<tr>" + "".join(f"<td>{escape(str(value or ''))}</td>" for value in (event.occurred_at, event.category, event.severity, event.title, event.address, event.source, event.url)) + "</tr>")
        document = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title><style>body{{font-family:system-ui;background:#0b1220;color:#eef2ff;padding:2rem}}table{{border-collapse:collapse;width:100%;background:#111a2e}}th,td{{border:1px solid #334155;padding:.55rem;text-align:left;vertical-align:top}}th{{background:#1d4ed8}}a{{color:#93c5fd}}</style></head><body><h1>{title}</h1><p>Generated by OsaintMaint. Confirm important information against the original source.</p><table><thead><tr><th>Occurred</th><th>Category</th><th>Severity</th><th>Title</th><th>Location</th><th>Source</th><th>URL</th></tr></thead><tbody>{rows}</tbody></table></body></html>""".format(title=escape(title), rows="".join(rows))
        Path(output).write_text(document, encoding="utf-8")
