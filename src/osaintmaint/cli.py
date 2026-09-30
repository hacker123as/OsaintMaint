"""Command-line interface for OsaintMaint."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

from .config import load_config
from .connectors.public_safety import ConnectorError, PublicSafetyClient, load_fixture
from .models import SEVERITIES, Event, WatchRule, utc_now
from .sites import open_site, validate_site_url
from .storage import Store
from .tool_vault import (
    VaultError,
    install_selected_tool,
    remove_selected_tool,
    sync_catalog,
    update_selected_tool,
)
from .watch import evaluate
from .watchdog import run_once as run_watchdog_once


def _store(args) -> Store:
    return Store(getattr(args, "db", None) or load_config(getattr(args, "config", None)).db_path)


def _print(value, as_json: bool = False) -> None:
    if as_json or isinstance(value, (list, dict)):
        print(json.dumps(value, indent=2, ensure_ascii=False, default=str))
    else:
        print(value)


def _event_rows(events: list[Event]) -> list[dict]:
    return [event.to_dict() for event in events]


def _source_params(args) -> dict[str, str | int]:
    mapping = {
        "state": getattr(args, "state", ""),
        "city": getattr(args, "city", ""),
        "county": getattr(args, "county", ""),
        "agency": getattr(args, "agency", ""),
        "type": getattr(args, "type", ""),
        "name": getattr(args, "name_filter", ""),
        "q": getattr(args, "q", ""),
        "startDate": getattr(args, "start_date", ""),
        "endDate": getattr(args, "end_date", ""),
        "provider": getattr(args, "provider", ""),
        "limit": getattr(args, "limit", ""),
        "sort": getattr(args, "sort", ""),
        "order": getattr(args, "order", ""),
        "cursor": getattr(args, "cursor", ""),
    }
    return {key: value for key, value in mapping.items() if value not in ("", None)}


def cmd_init(args) -> int:
    config = load_config(args.config)
    if args.db:
        config.db_path = args.db
    Store(config.db_path).close()
    Path(config.db_path).parent.mkdir(parents=True, exist_ok=True)
    print(f"Initialized {config.db_path}")
    return 0


def cmd_ingest(args) -> int:
    store = _store(args)
    try:
        events = load_fixture(args.input, source=args.source)
        count = store.upsert_events(events)
        store.audit("ingest", {"input": str(args.input), "events": count}, utc_now())
        print(f"Imported {count} events")
        return 0
    finally:
        store.close()


def cmd_source_pull(args) -> int:
    config = load_config(args.config)
    if args.resource:
        config.source.resource = args.resource
    store = Store(args.db or config.db_path)
    try:
        client = PublicSafetyClient(config.source)
        events = client.fetch_events(_source_params(args))
        count = store.upsert_events(events)
        store.audit("source_pull", {"source": config.source.base_url, "resource": config.source.resource, "events": count}, utc_now())
        print(f"Pulled {count} events")
        return 0
    finally:
        store.close()


def cmd_search(args) -> int:
    store = _store(args)
    try:
        events = store.search(args.query, args.category, args.severity, args.since, args.limit)
        _print(_event_rows(events), args.json)
        return 0
    finally:
        store.close()


def _parse_csv(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _parse_bbox(value: str | None) -> tuple[float, float, float, float] | None:
    if not value:
        return None
    parts = [float(part.strip()) for part in value.split(",")]
    if len(parts) != 4 or parts[0] > parts[2] or parts[1] > parts[3]:
        raise ValueError("bbox must be min_lat,min_lon,max_lat,max_lon")
    if not (-90 <= parts[0] <= 90 and -90 <= parts[2] <= 90 and -180 <= parts[1] <= 180 and -180 <= parts[3] <= 180):
        raise ValueError("bbox coordinates are out of range")
    return tuple(parts)


def cmd_watch_add(args) -> int:
    store = _store(args)
    try:
        rule = WatchRule(None, args.name, args.query, _parse_csv(args.category), _parse_csv(args.source), args.min_severity, _parse_bbox(args.bbox))
        rule_id = store.add_rule(rule)
        store.audit("watch_add", {"rule_id": rule_id, "name": args.name}, utc_now())
        print(f"Added watch rule {rule_id}: {args.name}")
        return 0
    finally:
        store.close()


def cmd_watch_list(args) -> int:
    store = _store(args)
    try:
        rules = [rule.__dict__ if hasattr(rule, "__dict__") else {slot: getattr(rule, slot) for slot in rule.__slots__} for rule in store.list_rules()]
        _print(rules, args.json)
        return 0
    finally:
        store.close()


def cmd_watch_remove(args) -> int:
    store = _store(args)
    try:
        removed = store.remove_rule(args.name)
        store.audit("watch_remove", {"name": args.name, "removed": removed}, utc_now())
        print("Removed" if removed else "No matching rule")
        return 0 if removed else 1
    finally:
        store.close()


def _notify(rule: WatchRule, event: Event) -> None:
    print(json.dumps({"watch": rule.name, "event_id": event.id, "severity": event.severity, "title": event.title, "url": event.url}, ensure_ascii=False))


def cmd_watch_run(args) -> int:
    config = load_config(args.config)
    if args.resource:
        config.source.resource = args.resource
    store = Store(args.db or config.db_path)
    try:
        client = PublicSafetyClient(config.source)
        total = 0
        while True:
            events = client.fetch_events(_source_params(args))
            total += len(evaluate(store, events, notify=_notify))
            if args.once:
                break
            time.sleep(max(1.0, args.interval))
        print(f"New watch hits: {total}")
        return 0
    finally:
        store.close()


def cmd_source_info(args) -> int:
    config = load_config(args.config)
    client = PublicSafetyClient(config.source)
    result = {
        "sources": client.fetch_metadata("sources"),
        "capabilities": client.fetch_metadata("capabilities"),
    }
    _print(result, args.json)
    return 0


def cmd_export(args) -> int:
    store = _store(args)
    try:
        events = store.search(args.query, args.category, args.severity, args.since, args.limit)
        output = Path(args.out)
        if args.format == "csv":
            store.export_csv(events, output)
        elif args.format == "html":
            store.export_html(events, output)
        else:
            output.write_text(json.dumps(_event_rows(events), indent=2, ensure_ascii=False), encoding="utf-8")
        store.audit("export", {"format": args.format, "output": str(output), "events": len(events)}, utc_now())
        print(f"Wrote {len(events)} events to {output}")
        return 0
    finally:
        store.close()


def cmd_status(args) -> int:
    store = _store(args)
    try:
        _print(store.stats(), args.json)
        return 0
    finally:
        store.close()


def cmd_audit(args) -> int:
    store = _store(args)
    try:
        _print(store.recent_audit(args.limit), args.json)
        return 0
    finally:
        store.close()


def cmd_quality(args) -> int:
    store = _store(args)
    try:
        _print(store.quality(), args.json)
        return 0
    finally:
        store.close()


def cmd_watchdogs(args) -> int:
    store = _store(args)
    try:
        _print(store.recent_watchdogs(args.limit), args.json)
        return 0
    finally:
        store.close()


def cmd_sites_add(args) -> int:
    store = _store(args)
    try:
        url = validate_site_url(args.url)
        site_id = store.add_site(args.name, url, utc_now())
        store.audit("site_add", {"site_id": site_id, "name": args.name, "url": url}, utc_now())
        print(f"Saved site {site_id}: {args.name}")
        return 0
    finally:
        store.close()


def cmd_sites_list(args) -> int:
    store = _store(args)
    try:
        _print(store.list_sites(), args.json)
        return 0
    finally:
        store.close()


def cmd_sites_remove(args) -> int:
    store = _store(args)
    try:
        removed = store.remove_site(args.name)
        store.audit("site_remove", {"name": args.name, "removed": removed}, utc_now())
        print("Removed" if removed else "No matching site")
        return 0 if removed else 1
    finally:
        store.close()


def cmd_sites_open(args) -> int:
    store = _store(args)
    try:
        site = store.get_site(args.name)
        if not site:
            print("No matching site", file=sys.stderr)
            return 1
        accepted = open_site(site["url"])
        store.audit("site_open", {"name": site["name"], "url": site["url"], "accepted": accepted}, utc_now())
        print(f"Opening {site['name']}" if accepted else "Browser request was not accepted")
        return 0 if accepted else 1
    finally:
        store.close()


def cmd_tools_sync(args) -> int:
    config = load_config(args.config)
    store = Store(args.db or config.db_path)
    try:
        result = sync_catalog(store, registry_dir=args.registry_dir)
        _print(
            {
                "status": "synced",
                "tools": result.tools,
                "catalogs": [
                    {"name": item.name, "tools": item.tools, "commit": item.commit, "repository": item.repository}
                    for item in result.catalogs
                ],
                "registry_path": result.registry_path,
            },
            args.json,
        )
        return 0
    finally:
        store.close()


def cmd_tools_list(args) -> int:
    store = _store(args)
    try:
        tools = store.list_osint_tools(args.query, args.limit, args.offset, args.favorites)
        if args.json:
            _print(tools, True)
        elif not tools:
            print("No tool records yet. Run `osaint tools sync` first.")
        else:
            for tool in tools:
                status = "installed" if tool["install_status"] == "installed" else "available"
                favorite = "favorite" if tool["is_favorite"] else "        "
                print(f"[{favorite} {status:<9}] {tool['id']}\n  {tool['name']} - {tool['category']}\n  {tool['repository_url']}")
        return 0
    finally:
        store.close()


def cmd_tools_favorite(args) -> int:
    store = _store(args)
    try:
        tool = store.get_osint_tool(args.id)
        if not tool:
            print("No matching tool. Run osaint tools sync first.", file=sys.stderr)
            return 1
        changed = store.favorite_osint_tool(args.id, utc_now())
        store.audit(
            "tool_manager_favorite",
            {"tool_id": args.id, "name": tool["name"], "changed": changed},
            utc_now(),
        )
        _print(
            {
                "status": "favorited" if changed else "already_favorite",
                "tool_id": args.id,
                "name": tool["name"],
                "is_favorite": True,
            },
            args.json,
        )
        return 0
    finally:
        store.close()


def cmd_tools_unfavorite(args) -> int:
    store = _store(args)
    try:
        tool = store.get_osint_tool(args.id)
        if not tool:
            print("No matching tool. Run osaint tools sync first.", file=sys.stderr)
            return 1
        changed = store.unfavorite_osint_tool(args.id)
        store.audit(
            "tool_manager_unfavorite",
            {"tool_id": args.id, "name": tool["name"], "changed": changed},
            utc_now(),
        )
        _print(
            {
                "status": "unfavorited" if changed else "not_favorite",
                "tool_id": args.id,
                "name": tool["name"],
                "is_favorite": False,
            },
            args.json,
        )
        return 0
    finally:
        store.close()


def cmd_tools_favorites(args) -> int:
    store = _store(args)
    try:
        tools = store.list_favorite_osint_tools(args.query, args.limit)
        if args.json:
            _print(tools, True)
        elif not tools:
            print("No favorite OSINT tools yet. Favorite a catalog tool after syncing.")
        else:
            for tool in tools:
                status = "installed" if tool["install_status"] == "installed" else "available"
                print(f"[favorite {status:<9}] {tool['id']}\n  {tool['name']} - {tool['category']}\n  {tool['repository_url']}")
        return 0
    finally:
        store.close()


def cmd_tools_install(args) -> int:
    store = _store(args)
    try:
        result = install_selected_tool(store, tool_id=args.id, tools_dir=args.tools_dir)
        _print(
            {
                "status": "already_installed" if result.reused else "installed",
                "tool_id": result.tool_id,
                "name": result.name,
                "path": result.path,
                "commit": result.commit,
                "executed": False,
            },
            args.json,
        )
        return 0
    finally:
        store.close()


def cmd_tools_installed(args) -> int:
    store = _store(args)
    try:
        tools = store.list_installed_tools(args.limit)
        if args.json:
            _print(tools, True)
        elif not tools:
            print("No managed source tools installed yet.")
        else:
            for tool in tools:
                print(f"{tool['name']}\n  {tool['installed_path']}\n  {tool['repository_url']}\n  {tool['commit_sha']}")
        return 0
    finally:
        store.close()


def cmd_tools_info(args) -> int:
    store = _store(args)
    try:
        tool = store.get_osint_tool(args.id)
        if not tool:
            print("No matching tool. Run `osaint tools sync` first.", file=sys.stderr)
            return 1
        _print(tool, args.json)
        return 0
    finally:
        store.close()


def cmd_tools_update(args) -> int:
    store = _store(args)
    try:
        result = update_selected_tool(store, tool_id=args.id, tools_dir=args.tools_dir)
        _print(
            {
                "status": "updated" if result.changed else "already_current",
                "tool_id": result.tool_id,
                "name": result.name,
                "path": result.path,
                "previous_commit": result.previous_commit,
                "commit": result.commit,
                "executed": False,
            },
            args.json,
        )
        return 0
    finally:
        store.close()


def cmd_tools_remove(args) -> int:
    if not args.yes:
        print("Removal changes local files. Re-run with --yes after checking the selected tool ID.", file=sys.stderr)
        return 2
    store = _store(args)
    try:
        result = remove_selected_tool(store, tool_id=args.id, tools_dir=args.tools_dir)
        _print(
            {
                "status": "removed",
                "tool_id": result.tool_id,
                "name": result.name,
                "path": result.path,
                "removed_files": result.removed_files,
            },
            args.json,
        )
        return 0
    finally:
        store.close()


def cmd_tools_status(args) -> int:
    store = _store(args)
    try:
        _print(store.osint_tool_summary(args.category_limit), args.json)
        return 0
    finally:
        store.close()


def cmd_watchdog_run(args) -> int:
    config = load_config(args.config)
    if args.resource:
        config.source.resource = args.resource
    store = Store(args.db or config.db_path)
    try:
        client = PublicSafetyClient(config.source)
        while True:
            result = run_watchdog_once(client, store, params=_source_params(args), notify=_notify)
            _print(result.to_dict(), args.json)
            if args.once:
                return 0 if result.status == "ok" else 2
            time.sleep(max(1.0, args.interval))
    finally:
        store.close()


def cmd_dashboard(args) -> int:
    from .ui import launch_dashboard

    launch_dashboard(args.config, args.db)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="osaint", description="Safety-first public-data intelligence and alerting")
    sub = parser.add_subparsers(dest="command", required=True)
    dashboard = sub.add_parser("dashboard", aliases=["ui", "menu"], help="open the interactive keyboard dashboard")
    dashboard.add_argument("--config", default="config/example.json")
    dashboard.add_argument("--db")
    dashboard.set_defaults(func=cmd_dashboard)
    init = sub.add_parser("init", help="initialize a database")
    init.add_argument("--config", default="config/example.json")
    init.add_argument("--db")
    init.set_defaults(func=cmd_init)
    ingest = sub.add_parser("ingest", help="ingest a local JSON payload")
    ingest.add_argument("--input", required=True)
    ingest.add_argument("--source", default="fixture")
    ingest.add_argument("--db", default="data/osaint.db")
    ingest.set_defaults(func=cmd_ingest)
    pull = sub.add_parser("source-pull", help="pull from configured API")
    pull.add_argument("--config", default="config/example.json")
    pull.add_argument("--db")
    pull.add_argument("--resource", choices=tuple(PublicSafetyClient.RESOURCE_PATHS), help="TuerSS resource endpoint")
    for argument, option in (("state", "--state"), ("city", "--city"), ("county", "--county"), ("agency", "--agency"), ("type", "--type"), ("name_filter", "--name"), ("q", "--q"), ("start_date", "--start-date"), ("end_date", "--end-date"), ("provider", "--provider"), ("cursor", "--cursor")):
        pull.add_argument(option, dest=argument)
    pull.add_argument("--limit", type=int)
    pull.add_argument("--sort", choices=("occurredAt", "reportedAt", "relevance"))
    pull.add_argument("--order", choices=("asc", "desc"))
    pull.set_defaults(func=cmd_source_pull)
    source_info = sub.add_parser("sources", help="show TuerSS source verification and capabilities")
    source_info.add_argument("--config", default="config/example.json")
    source_info.add_argument("--json", action="store_true")
    source_info.set_defaults(func=cmd_source_info)
    tools = sub.add_parser("tools", help="manage curated GitHub OSINT source tools without auto-executing them")
    tools_sub = tools.add_subparsers(dest="tools_command", required=True)
    tools_sync = tools_sub.add_parser("sync", help="synchronize the large multi-catalog GitHub tool index")
    tools_sync.add_argument("--config", default="config/example.json")
    tools_sync.add_argument("--db")
    tools_sync.add_argument("--registry-dir", default="data/registries")
    tools_sync.add_argument("--json", action="store_true")
    tools_sync.set_defaults(func=cmd_tools_sync)
    tools_list = tools_sub.add_parser("list", help="search the synchronized OSINT Tool Manager catalog")
    tools_list.add_argument("--db", default="data/osaint.db")
    tools_list.add_argument("--query", default="")
    tools_list.add_argument("--limit", type=int, default=30)
    tools_list.add_argument("--offset", type=int, default=0)
    tools_list.add_argument("--favorites", action="store_true", help="show only locally favorited tools")
    tools_list.add_argument("--json", action="store_true")
    tools_list.set_defaults(func=cmd_tools_list)
    tools_favorite = tools_sub.add_parser("favorite", aliases=["pin"], help="pin one catalog tool to the top of local Tool Manager lists")
    tools_favorite.add_argument("--db", default="data/osaint.db")
    tools_favorite.add_argument("--id", required=True, help="tool ID from the Tool Manager catalog")
    tools_favorite.add_argument("--json", action="store_true")
    tools_favorite.set_defaults(func=cmd_tools_favorite)
    tools_unfavorite = tools_sub.add_parser("unfavorite", aliases=["unpin"], help="remove one local Tool Manager favorite")
    tools_unfavorite.add_argument("--db", default="data/osaint.db")
    tools_unfavorite.add_argument("--id", required=True)
    tools_unfavorite.add_argument("--json", action="store_true")
    tools_unfavorite.set_defaults(func=cmd_tools_unfavorite)
    tools_favorites = tools_sub.add_parser("favorites", help="list locally favorited source tools")
    tools_favorites.add_argument("--db", default="data/osaint.db")
    tools_favorites.add_argument("--query", default="")
    tools_favorites.add_argument("--limit", type=int, default=100)
    tools_favorites.add_argument("--json", action="store_true")
    tools_favorites.set_defaults(func=cmd_tools_favorites)
    tools_install = tools_sub.add_parser("install", help="save one selected source checkout without running its code")
    tools_install.add_argument("--db", default="data/osaint.db")
    tools_install.add_argument("--id", required=True, help="tool ID from `osaint tools list`")
    tools_install.add_argument("--tools-dir", default="managed-tools")
    tools_install.add_argument("--json", action="store_true")
    tools_install.set_defaults(func=cmd_tools_install)
    tools_installed = tools_sub.add_parser("installed", help="list persistent managed source checkouts")
    tools_installed.add_argument("--db", default="data/osaint.db")
    tools_installed.add_argument("--limit", type=int, default=100)
    tools_installed.add_argument("--json", action="store_true")
    tools_installed.set_defaults(func=cmd_tools_installed)
    tools_info = tools_sub.add_parser("info", help="show a tool's source, category, install state, and provenance")
    tools_info.add_argument("--db", default="data/osaint.db")
    tools_info.add_argument("--id", required=True)
    tools_info.add_argument("--json", action="store_true")
    tools_info.set_defaults(func=cmd_tools_info)
    tools_update = tools_sub.add_parser("update", help="fast-forward one saved source checkout without executing it")
    tools_update.add_argument("--db", default="data/osaint.db")
    tools_update.add_argument("--id", required=True)
    tools_update.add_argument("--tools-dir", default="managed-tools")
    tools_update.add_argument("--json", action="store_true")
    tools_update.set_defaults(func=cmd_tools_update)
    tools_remove = tools_sub.add_parser("remove", help="remove one explicitly selected managed source checkout")
    tools_remove.add_argument("--db", default="data/osaint.db")
    tools_remove.add_argument("--id", required=True)
    tools_remove.add_argument("--tools-dir", default="managed-tools")
    tools_remove.add_argument("--yes", action="store_true", help="confirm local source removal")
    tools_remove.add_argument("--json", action="store_true")
    tools_remove.set_defaults(func=cmd_tools_remove)
    tools_status = tools_sub.add_parser("status", help="show Tool Manager counts, sync state, and top categories")
    tools_status.add_argument("--db", default="data/osaint.db")
    tools_status.add_argument("--category-limit", type=int, default=12)
    tools_status.add_argument("--json", action="store_true")
    tools_status.set_defaults(func=cmd_tools_status)
    sites = sub.add_parser("sites", help="manage approved public-source bookmarks")
    sites_sub = sites.add_subparsers(dest="sites_command", required=True)
    sites_add = sites_sub.add_parser("add", help="save a public HTTP(S) site")
    sites_add.add_argument("--db", default="data/osaint.db")
    sites_add.add_argument("--name", required=True)
    sites_add.add_argument("--url", required=True)
    sites_add.set_defaults(func=cmd_sites_add)
    sites_list = sites_sub.add_parser("list", help="list saved sites")
    sites_list.add_argument("--db", default="data/osaint.db")
    sites_list.add_argument("--json", action="store_true")
    sites_list.set_defaults(func=cmd_sites_list)
    sites_remove = sites_sub.add_parser("remove", help="remove a saved site")
    sites_remove.add_argument("--db", default="data/osaint.db")
    sites_remove.add_argument("--name", required=True)
    sites_remove.set_defaults(func=cmd_sites_remove)
    sites_open = sites_sub.add_parser("open", help="open a saved site in the default browser")
    sites_open.add_argument("--db", default="data/osaint.db")
    sites_open.add_argument("--name", required=True)
    sites_open.set_defaults(func=cmd_sites_open)
    search = sub.add_parser("search", help="search normalized events")
    search.add_argument("--db", default="data/osaint.db")
    search.add_argument("--query", default="")
    search.add_argument("--category", default="")
    search.add_argument("--severity", choices=SEVERITIES, default="")
    search.add_argument("--since", default="")
    search.add_argument("--limit", type=int, default=100)
    search.add_argument("--json", action="store_true")
    search.set_defaults(func=cmd_search)
    watch = sub.add_parser("watch", help="manage and run watch rules")
    watch_sub = watch.add_subparsers(dest="watch_command", required=True)
    add = watch_sub.add_parser("add", help="add a watch rule")
    add.add_argument("--db", default="data/osaint.db")
    add.add_argument("--name", required=True)
    add.add_argument("--query", default="")
    add.add_argument("--category", default="")
    add.add_argument("--source", default="")
    add.add_argument("--min-severity", choices=SEVERITIES, default="unknown")
    add.add_argument("--bbox", help="min_lat,min_lon,max_lat,max_lon")
    add.set_defaults(func=cmd_watch_add)
    listing = watch_sub.add_parser("list", help="list watch rules")
    listing.add_argument("--db", default="data/osaint.db")
    listing.add_argument("--json", action="store_true")
    listing.set_defaults(func=cmd_watch_list)
    remove = watch_sub.add_parser("remove", help="remove a watch rule")
    remove.add_argument("--db", default="data/osaint.db")
    remove.add_argument("--name", required=True)
    remove.set_defaults(func=cmd_watch_remove)
    run = watch_sub.add_parser("run", help="poll and evaluate watch rules")
    run.add_argument("--config", default="config/example.json")
    run.add_argument("--db")
    run.add_argument("--resource", choices=tuple(PublicSafetyClient.RESOURCE_PATHS), help="TuerSS resource endpoint")
    for argument, option in (("state", "--state"), ("city", "--city"), ("county", "--county"), ("agency", "--agency"), ("type", "--type"), ("name_filter", "--name"), ("q", "--q"), ("start_date", "--start-date"), ("end_date", "--end-date"), ("provider", "--provider"), ("cursor", "--cursor")):
        run.add_argument(option, dest=argument)
    run.add_argument("--limit", type=int)
    run.add_argument("--sort", choices=("occurredAt", "reportedAt", "relevance"))
    run.add_argument("--order", choices=("asc", "desc"))
    run.add_argument("--once", action="store_true", help="poll once and exit")
    run.add_argument("--interval", type=float, default=60.0)
    run.set_defaults(func=cmd_watch_run)
    export = sub.add_parser("export", help="export events")
    export.add_argument("--db", default="data/osaint.db")
    export.add_argument("--format", choices=("json", "csv", "html"), default="json")
    export.add_argument("--out", required=True)
    export.add_argument("--query", default="")
    export.add_argument("--category", default="")
    export.add_argument("--severity", choices=SEVERITIES, default="")
    export.add_argument("--since", default="")
    export.add_argument("--limit", type=int, default=5000)
    export.set_defaults(func=cmd_export)
    status = sub.add_parser("status", help="show database counts")
    status.add_argument("--db", default="data/osaint.db")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=cmd_status)
    audit = sub.add_parser("audit", help="show recent audit entries")
    audit.add_argument("--db", default="data/osaint.db")
    audit.add_argument("--limit", type=int, default=50)
    audit.add_argument("--json", action="store_true")
    audit.set_defaults(func=cmd_audit)
    quality = sub.add_parser("quality", help="scan stored event data quality")
    quality.add_argument("--db", default="data/osaint.db")
    quality.add_argument("--json", action="store_true")
    quality.set_defaults(func=cmd_quality)
    watchdogs = sub.add_parser("watchdogs", help="show watchdog run history")
    watchdogs.add_argument("--db", default="data/osaint.db")
    watchdogs.add_argument("--limit", type=int, default=20)
    watchdogs.add_argument("--json", action="store_true")
    watchdogs.set_defaults(func=cmd_watchdogs)
    watchdog = sub.add_parser("watchdog", help="run a resilient source/watch health poll")
    watchdog_sub = watchdog.add_subparsers(dest="watchdog_command", required=True)
    watchdog_run = watchdog_sub.add_parser("run", help="run one watchdog cycle or keep polling")
    watchdog_run.add_argument("--config", default="config/example.json")
    watchdog_run.add_argument("--db")
    watchdog_run.add_argument("--resource", choices=tuple(PublicSafetyClient.RESOURCE_PATHS), help="TuerSS resource endpoint")
    for argument, option in (("state", "--state"), ("city", "--city"), ("county", "--county"), ("agency", "--agency"), ("type", "--type"), ("name_filter", "--name"), ("q", "--q"), ("start_date", "--start-date"), ("end_date", "--end-date"), ("provider", "--provider"), ("cursor", "--cursor")):
        watchdog_run.add_argument(option, dest=argument)
    watchdog_run.add_argument("--limit", type=int)
    watchdog_run.add_argument("--sort", choices=("occurredAt", "reportedAt", "relevance"))
    watchdog_run.add_argument("--order", choices=("asc", "desc"))
    watchdog_run.add_argument("--once", action="store_true")
    watchdog_run.add_argument("--interval", type=float, default=60.0)
    watchdog_run.add_argument("--json", action="store_true")
    watchdog_run.set_defaults(func=cmd_watchdog_run)
    return parser


def main(argv: list[str] | None = None) -> int:
    raw_argv = sys.argv[1:] if argv is None else argv
    if not raw_argv:
        return cmd_dashboard(argparse.Namespace(config="config/example.json", db=None))
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (ConnectorError, VaultError, ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
