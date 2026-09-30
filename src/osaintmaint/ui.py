"""Keyboard-driven terminal dashboard for OsaintMaint.

This intentionally uses only the Python standard library so the dashboard works
in a normal terminal without a UI framework or a network connection.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import sys
import time
from typing import Callable
import webbrowser

from .config import AppConfig, load_config
from .connectors.public_safety import PublicSafetyClient, load_fixture
from .models import Event, WatchRule, utc_now
from .sites import open_site, validate_site_url
from .storage import Store
from .tool_vault import (
    VaultError,
    install_selected_tool,
    remove_selected_tool,
    sync_catalog,
    update_selected_tool,
)
from .watchdog import run_once as run_watchdog_once


ACCENT = "\x1b[38;5;81m"
GLOW = "\x1b[38;5;213m"
MUTED = "\x1b[38;5;245m"
GOOD = "\x1b[38;5;120m"
RESET = "\x1b[0m"
SLOGAN = "UNHIDE WHAT'S PUBLIC // VERIFY WHAT MATTERS"

LARGE_EYE = (
    "                  __..------------------------------------------------------..__",
    "           _..-''    0x01   [ O5A1NTM4INT ] // PUBLIC DATA CONSOLE   0x10    ''-.._",
    "       .-'                     .-''''''''''''''''''''''''-.                     '-.",
    "    .-'                    .-'     0  0  0   [###]   0  0  0     '-.                  '-.",
    "  .'                    .-'             .-=========-.             '-.                  '.",
    " /                    .'               .'   1  ^  1   '.               '.                  \\",
    ";                    |                |      111      |                |                  ;",
    " \\                    '.               '.    _____    .'               .'                  /",
    "  '.                    '-.               '---     ---'               .-'                  .'",
    "    '-._                    '-._______________________________.-'                    _.-'",
    "        '---.._______________________________________________________________..---'",
)

STANDARD_EYE = (
    "              .-=====================================================-.",
    "       .--''    0x01 [ O5A1NTM4INT ] // PUBLIC DATA 0x10       ''--.",
    "   .-'                         .-=================-.                 '-.",
    " .'                           /   0  0  [###]  0  0   \\               '.",
    "/                             |       1  ^  1        |                 \\",
    "\\                             \\_____1__1__1_______/                 /",
    " '.                            '-------------------'                 .'",
    "   '-._                                                          _.-'",
    "       '----..____________________________________________..----'",
)

COMPACT_EYE = (
    "[ O5A1NTM4INT ]  < 0 0 [###] 0 0 >",
    "UNHIDE WHAT'S PUBLIC // VERIFY WHAT MATTERS",
)


_ANSI_READY: bool | None = None
_ALT_SCREEN_ACTIVE = False


@dataclass(slots=True)
class Tool:
    key: str
    label: str
    summary: str
    action: Callable[[], None]


def _clear() -> None:
    if _ansi_enabled():
        print("\x1b]0;OsaintMaint - Public Data Console\x07\x1b[2J\x1b[H", end="")
    elif sys.stdout.isatty():
        os.system("cls" if os.name == "nt" else "clear")


def _paint(value: str, color: str) -> str:
    if not _ansi_enabled():
        return value
    return f"{color}{value}{RESET}"


def _ansi_enabled() -> bool:
    """Enable ANSI colour on modern Windows and never print raw escape codes."""
    global _ANSI_READY
    if _ANSI_READY is not None:
        return _ANSI_READY
    if os.getenv("NO_COLOR") or not sys.stdout.isatty():
        _ANSI_READY = False
        return False
    if os.name != "nt":
        _ANSI_READY = True
        return True
    try:
        import ctypes

        handle = ctypes.windll.kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if handle in (0, -1) or not ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            _ANSI_READY = False
            return False
        enable_virtual_terminal_processing = 0x0004
        _ANSI_READY = bool(ctypes.windll.kernel32.SetConsoleMode(handle, mode.value | enable_virtual_terminal_processing))
        return _ANSI_READY
    except Exception:
        _ANSI_READY = False
        return False


def _terminal_size() -> tuple[int, int]:
    size = shutil.get_terminal_size((100, 30))
    return max(40, size.columns), max(12, size.lines)


def _clip(value: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(value) <= width:
        return value
    return value[: max(0, width - 3)] + "..."


def _center(value: str, width: int) -> str:
    return value if len(value) >= width else value.center(width)


def _place_art(lines: tuple[str, ...], width: int) -> list[str]:
    """Center one block as a whole, preserving the eye's internal geometry."""
    art_width = max(map(len, lines))
    left = max(0, (width - art_width) // 2)
    return [" " * left + _clip(line, max(1, width - left)) for line in lines]


def _eye_for(width: int, height: int) -> tuple[str, ...]:
    """Choose artwork that fits the current Command Prompt viewport."""
    if width >= 108 and height >= 32:
        return LARGE_EYE
    if width >= 72 and height >= 22:
        return STANDARD_EYE
    return COMPACT_EYE


def _begin_dashboard_session() -> None:
    """Use the alternate screen so navigation never fills the scrollback buffer."""
    global _ALT_SCREEN_ACTIVE
    if _ansi_enabled():
        print("\x1b[?1049h\x1b[?25l", end="", flush=True)
        _ALT_SCREEN_ACTIVE = True
    elif sys.stdout.isatty():
        os.system("cls" if os.name == "nt" else "clear")


def _end_dashboard_session() -> None:
    global _ALT_SCREEN_ACTIVE
    if _ALT_SCREEN_ACTIVE:
        print("\x1b[?25h\x1b[?1049l", end="", flush=True)
        _ALT_SCREEN_ACTIVE = False


def _read_key() -> str:
    if os.name == "nt":
        import msvcrt

        value = msvcrt.getwch()
        if value in ("\x00", "\xe0"):
            extended = msvcrt.getwch()
            return {"H": "up", "P": "down", "K": "left", "M": "right"}.get(extended, extended)
        return {"\r": "enter", "\x1b": "esc", "\x08": "backspace"}.get(value, value)
    if not sys.stdin.isatty():
        return "q"
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        value = sys.stdin.read(1)
        if value == "\x1b":
            next_one = sys.stdin.read(1)
            if next_one == "[":
                arrow = sys.stdin.read(1)
                return {"A": "up", "B": "down", "C": "right", "D": "left"}.get(arrow, "esc")
            return "esc"
        return {"\n": "enter", "\r": "enter", "\x7f": "backspace"}.get(value, value)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


class Dashboard:
    def __init__(self, config: AppConfig | None = None, *, db_path: str | None = None):
        self.config = config or load_config(None)
        if db_path:
            self.config.db_path = db_path
        self.store = Store(self.config.db_path)
        self.selected = 0
        self.menu_filter = ""
        self.running = True

    def close(self) -> None:
        self.store.close()

    def tools(self) -> list[Tool]:
        all_tools = [
            Tool("1", "Overview", "Live database counters, quality, recent events, and watchdog status.", self.show_overview),
            Tool("2", "Search events", "Search normalized records by text, category, severity, or time.", self.search_events),
            Tool("3", "Pull TuerSS", "Query the documented TuerSS public-safety resources.", self.pull_tuerss),
            Tool("4", "Watch rules", "Create, review, or remove deduplicated alert rules.", self.manage_watches),
            Tool("5", "Run Watchdog", "Poll a source once, record health, and trigger watch rules.", self.run_watchdog),
            Tool("6", "Source catalog", "Inspect TuerSS source verification and capabilities.", self.source_catalog),
            Tool("7", "OSINT Tool Manager", "Manage sync, installs, updates, folders, project pages, and removal.", self.tool_manager),
            Tool("8", "Sync OSINT Catalog", "Fetch the latest curated GitHub source-tool catalogs.", self.sync_osint_catalog),
            Tool("9", "Browse & Install OSINT", "Search the catalog, inspect a tool, and save its source checkout.", self.browse_osint_tools),
            Tool("A", "My OSINT Tools", "Open, update, inspect, or remove your saved source checkouts.", self.my_osint_tools),
            Tool("B", "Site workspace", "Save, review, and open user-approved public-source bookmarks.", self.site_workspace),
            Tool("C", "Event detail", "Inspect one stored event and its preserved provenance payload.", self.event_detail),
            Tool("D", "Export report", "Write JSON, CSV, or a self-contained HTML report.", self.export_report),
            Tool("E", "Ingest local JSON", "Load a fixture or authorized local public-data export.", self.ingest_local),
            Tool("F", "Data quality", "Find missing locations, URLs, timestamps, and unknown severity.", self.data_quality),
            Tool("G", "Audit trail", "Review operations performed by this local console.", self.audit_trail),
            Tool("H", "Watchdog history", "Review previous poll status, latency, and errors.", self.watchdog_history),
            Tool("I", "Help & safety", "Show keyboard controls, Tool Manager behavior, and operating boundaries.", self.help_screen),
            Tool("L", "Favorite OSINT Tools", "Jump straight to locally pinned source tools, shown first across the manager.", self.favorite_osint_tools),
        ]
        if not self.menu_filter:
            return all_tools
        needle = self.menu_filter.lower()
        return [tool for tool in all_tools if needle in f"{tool.key} {tool.label} {tool.summary}".lower()]

    def render(self) -> None:
        _clear()
        width, rows = _terminal_size()
        eye = _eye_for(width, rows)
        for line in _place_art(eye, width):
            print(_paint(line, GLOW))
        if eye is not COMPACT_EYE:
            print(_paint(_center(SLOGAN, width), GOOD))
        stats = self.store.stats()
        database_name = Path(self.config.db_path).name
        status = (
            f"DB {database_name} | EVENTS {stats['events']} | WATCHES {stats['watch_rules']} | "
            f"HITS {stats['watch_hits']} | WDOG {stats['watchdog_runs']} | "
            f"VAULT {stats['tool_catalog']} | FAVORITES {stats['favorite_tools']} | INSTALLED {stats['installed_tools']}"
        )
        print(_paint(_clip("  " + status, width), ACCENT))
        print(_paint("  " + "=" * max(1, width - 2), MUTED))
        controls = "OSAINT TOOL DECK | UP/DOWN or J/K move | ENTER open | / filter | ESC clear | Q quit"
        print(_paint(_clip("  " + controls, width), GOOD))
        tools = self.tools()
        if not tools:
            print("\n  No tools match the current filter. Press / to change it or ESC to clear.")
        else:
            reserved_rows = len(eye) + (1 if eye is not COMPACT_EYE else 0) + 7
            height = max(1, rows - reserved_rows)
            start = max(0, min(self.selected - height + 1, len(tools) - height))
            self.selected = min(self.selected, len(tools) - 1)
            label_width = max(12, min(24, width // 4))
            for index, tool in enumerate(tools[start : start + height], start=start):
                marker = ">" if index == self.selected else " "
                label = _clip(tool.label, label_width)
                prefix = f"  {marker} [{tool.key}] {label:<{label_width}} "
                line = prefix + _clip(tool.summary, max(8, width - len(prefix)))
                print(_paint(line, GLOW if index == self.selected else MUTED))
        if tools:
            selected_tool = tools[self.selected]
            print(_paint(_clip(f"  SELECTED: {selected_tool.label} - {selected_tool.summary}", width), ACCENT))
        print(_paint("  " + "=" * max(1, width - 2), MUTED))
        footer = f"Filter: {self.menu_filter or '(none)'} | Source: {self.config.source.resource} @ {self.config.source.base_url}"
        print(_paint(_clip("  " + footer, width), MUTED))

    def pause(self, message: str = "Press ENTER to return to the menu...") -> None:
        try:
            input(f"\n  {message}")
        except (EOFError, KeyboardInterrupt):
            pass

    def prompt(self, label: str, default: str = "", *, interrupt_to_caller: bool = False) -> str:
        suffix = f" [{default}]" if default else ""
        try:
            value = input(f"  {label}{suffix}: ").strip()
        except (EOFError, KeyboardInterrupt):
            if interrupt_to_caller:
                raise
            return default
        return value or default

    def invoke(self, tool: Tool) -> None:
        try:
            tool.action()
        except KeyboardInterrupt:
            _clear()
            print("  Cancelled. You are back at the main OsaintMaint menu.")
            self.pause()
        except Exception as exc:
            _clear()
            print(f"  Tool error: {exc}")
            self.pause()

    def run(self) -> None:
        if not sys.stdin.isatty() and os.name != "nt":
            print("OsaintMaint dashboard needs an interactive terminal. Use `osaint --help` for commands.")
            return
        try:
            _begin_dashboard_session()
            while self.running:
                try:
                    self.render()
                    key = _read_key()
                    tools = self.tools()
                    if key in ("up", "k"):
                        self.selected = max(0, self.selected - 1)
                    elif key in ("down", "j"):
                        self.selected = min(max(0, len(tools) - 1), self.selected + 1)
                    elif key in ("q", "Q", "esc"):
                        if self.menu_filter:
                            self.menu_filter = ""
                            self.selected = 0
                        else:
                            self.running = False
                    elif key == "/":
                        self.menu_filter = self.prompt("Filter tools", self.menu_filter)
                        self.selected = 0
                    elif key == "backspace":
                        self.menu_filter = self.menu_filter[:-1]
                        self.selected = 0
                    elif key.upper() in {tool.key for tool in tools}:
                        selected_key = key.upper()
                        self.selected = next(index for index, tool in enumerate(tools) if tool.key == selected_key)
                        self.invoke(tools[self.selected])
                    elif key == "enter" and tools:
                        self.invoke(tools[self.selected])
                except KeyboardInterrupt:
                    self.menu_filter = ""
                    self.selected = 0
                    _clear()
                    print("  Cancelled. You are back at the main OsaintMaint menu.")
                    self.pause()
        finally:
            _end_dashboard_session()
            print("OsaintMaint closed. Public-data alerts are leads; verify important information at the source.")

    def show_overview(self) -> None:
        _clear()
        width, height = _terminal_size()
        for line in _place_art(_eye_for(width, height), width):
            print(_paint(line, GLOW))
        print("  OVERVIEW\n")
        print(json.dumps({"stats": self.store.stats(), "quality": self.store.quality()}, indent=2))
        print("\n  RECENT EVENTS")
        for event in self.store.search(limit=8):
            print(f"  {event.occurred_at or event.observed_at:<22} {event.category:<16} {event.title[:70]}")
        self.pause()

    def search_events(self) -> None:
        _clear()
        print("  SEARCH EVENTS\n")
        query = self.prompt("Text")
        category = self.prompt("Category")
        severity = self.prompt("Severity", "")
        events = self.store.search(query, category, severity, limit=100)
        print(f"\n  {len(events)} matching events")
        for event in events[:30]:
            print(f"  {event.id}  {event.category:<16} {event.severity:<8} {event.title[:75]}")
        self.pause()

    def _query_params(self) -> dict[str, str | int]:
        values = {
            "state": self.prompt("State"),
            "city": self.prompt("City"),
            "q": self.prompt("Text query"),
            "provider": self.prompt("Provider ID"),
            "limit": self.prompt("Limit", "25"),
        }
        return {key: (int(value) if key == "limit" else value) for key, value in values.items() if value}

    def pull_tuerss(self) -> None:
        _clear()
        print("  PULL TUERSS RESOURCE\n")
        try:
            self.config.source.resource = self.prompt("Resource endpoint", self.config.source.resource)
            events = PublicSafetyClient(self.config.source).fetch_events(self._query_params())
            count = self.store.upsert_events(events)
            self.store.audit("ui_source_pull", {"resource": self.config.source.resource, "events": count}, utc_now())
            print(f"\n  Imported {count} normalized records from {self.config.source.resource}.")
        except Exception as exc:
            print(f"\n  Source error: {exc}")
        self.pause()

    def manage_watches(self) -> None:
        _clear()
        print("  WATCH RULES\n")
        for rule in self.store.list_rules():
            print(f"  [{rule.id}] {rule.name}  query={rule.query or '-'}  categories={','.join(rule.categories) or '-'}  min={rule.min_severity}")
        print("\n  [A] add  [R] remove  [ENTER] return")
        choice = self.prompt("Action").lower()
        if choice == "a":
            name = self.prompt("Rule name")
            query = self.prompt("Text query")
            category = self.prompt("Categories (comma-separated)")
            severity = self.prompt("Minimum severity", "unknown")
            rule = WatchRule(None, name, query, tuple(part.strip() for part in category.split(",") if part.strip()), (), severity)
            self.store.add_rule(rule)
            self.store.audit("ui_watch_add", {"name": name}, utc_now())
        elif choice == "r":
            name = self.prompt("Rule name to remove")
            self.store.remove_rule(name)
            self.store.audit("ui_watch_remove", {"name": name}, utc_now())
        self.pause()

    def run_watchdog(self) -> None:
        _clear()
        print("  WATCHDOG POLL\n")
        hits: list[tuple[WatchRule, Event]] = []

        def notify(rule: WatchRule, event: Event) -> None:
            hits.append((rule, event))

        try:
            self.config.source.resource = self.prompt("Resource endpoint", self.config.source.resource)
            result = run_watchdog_once(PublicSafetyClient(self.config.source), self.store, params=self._query_params(), notify=notify)
            print(json.dumps(result.to_dict(), indent=2))
            for rule, event in hits:
                print(f"  ALERT {rule.name}: {event.title} [{event.severity}]")
        except Exception as exc:
            print(f"  Watchdog error: {exc}")
        self.pause()

    def source_catalog(self) -> None:
        _clear()
        print("  TUERSS SOURCE CATALOG\n")
        try:
            client = PublicSafetyClient(self.config.source)
            payload = client.fetch_metadata("sources")
            records = payload.get("data", []) if isinstance(payload, dict) else []
            print(f"  Sources returned: {len(records)}")
            for source in records[:40]:
                print(f"  {source.get('id', '?'):<28} {source.get('publisher', '')[:45]}")
        except Exception as exc:
            print(f"  Source catalog error: {exc}")
        self.pause()

    def _manager_header(self, title: str) -> int:
        _clear()
        width, _ = _terminal_size()
        summary = self.store.osint_tool_summary(6)
        print(_paint(_clip(f"  {title}", width), GLOW))
        print(_paint("  " + "=" * max(1, width - 2), MUTED))
        print(_clip(f"  CATALOG {summary['catalog_tools']} | FAVORITES {summary['favorite_tools']} | SAVED {summary['installed_tools']} | FAILED {summary['failed_installs']}", width))
        if summary["last_synced_at"]:
            print(_clip(f"  Last catalog sync: {summary['last_synced_at']}", width))
        print(_clip("  Source checkouts only: no third-party setup scripts, dependencies, or tools are auto-run.", width))
        return width

    def _open_tool_folder(self, location: str) -> bool:
        folder = Path(location)
        if not folder.is_dir():
            return False
        try:
            if os.name == "nt":
                os.startfile(str(folder))
            else:
                webbrowser.open(folder.resolve().as_uri())
            return True
        except OSError:
            return False

    def _toggle_tool_favorite(self, tool: dict) -> bool:
        if tool["is_favorite"]:
            changed = self.store.unfavorite_osint_tool(tool["id"])
            action = "tool_manager_unfavorite"
            message = "Removed from favorites" if changed else "Already not favorited"
        else:
            changed = self.store.favorite_osint_tool(tool["id"], utc_now())
            action = "tool_manager_favorite"
            message = "Pinned to favorites" if changed else "Already favorited"
        self.store.audit(action, {"tool_id": tool["id"], "name": tool["name"], "changed": changed}, utc_now())
        print(_paint(f"  {message}: {tool['name']}", GOOD))
        return not bool(tool["is_favorite"])

    def _sync_osint_catalog(self) -> None:
        width = self._manager_header("SYNC OSINT CATALOGS")
        print("\n  Updating curated GitHub indexes. This does not install or run any OSINT tool.")
        result = sync_catalog(self.store)
        print(_paint(_clip(f"\n  READY: {result.tools} unique, selectable GitHub OSINT source tools.", width), GOOD))
        for catalog in result.catalogs:
            print(_clip(f"  {catalog.name:<24} {catalog.tools:>4} tools  {catalog.commit[:10]}", width))

    def _browse_and_install_osint(self) -> None:
        width = self._manager_header("DISCOVER & INSTALL OSINT")
        if not self.store.stats()["tool_catalog"]:
            print("\n  No catalog is ready. Use [8] Sync OSINT Catalog first.")
            return
        query = self.prompt("Search name, category, or description (blank shows first 25)", interrupt_to_caller=True)
        tools = self.store.list_osint_tools(query, limit=25)
        if not tools:
            print("\n  No matching tools.")
            return
        print("\n  SELECT A SOURCE TOOL")
        for index, tool in enumerate(tools, start=1):
            state = "SAVED" if tool["install_status"] == "installed" else "READY"
            favorite = "FAV" if tool["is_favorite"] else "   "
            print(_clip(f"  [{index:02}] {favorite:<3} {state:<5} {tool['name']:<28} {tool['category']}", width))
        selected = self.prompt("Tool number (ENTER returns)", interrupt_to_caller=True)
        if not selected:
            return
        try:
            tool = tools[int(selected) - 1]
        except (ValueError, IndexError):
            raise VaultError("Choose a tool number shown above") from None
        print(_paint(_clip(f"\n  {tool['name']} - {tool['category']}", width), GLOW))
        print(_clip(f"  {tool['description']}", width))
        print(_clip(f"  {tool['repository_url']}", width))
        state = "already saved" if tool["install_status"] == "installed" else "not saved"
        favorite = "favorited" if tool["is_favorite"] else "not favorited"
        print(f"  Status: {state} | {favorite}")
        action = self.prompt("Action: [F] favorite  [I] save source  [P] project page  [ENTER] manager", interrupt_to_caller=True).lower()
        if action == "p":
            accepted = open_site(tool["repository_url"])
            print("  Opening the project page." if accepted else "  The operating system did not accept the browser request.")
        elif action == "f":
            self._toggle_tool_favorite(tool)
        elif action == "i":
            if tool["install_status"] != "installed":
                confirm = self.prompt("Type YES to save this source checkout", interrupt_to_caller=True)
                if confirm != "YES":
                    print("  Nothing changed.")
                    return
            result = install_selected_tool(self.store, tool_id=tool["id"])
            verb = "Already saved" if result.reused else "Saved"
            print(_paint(_clip(f"  {verb}: {result.name} -> {result.path}", width), GOOD))

    def _manage_saved_osint_tools(self) -> None:
        width = self._manager_header("MY OSINT TOOLS")
        installed = self.store.list_installed_tools(100)
        if not installed:
            print("\n  No saved source tools. Choose Browse & Install to add one.")
            return
        query = self.prompt("Filter saved tools (blank shows all)", interrupt_to_caller=True).lower()
        if query:
            installed = [
                tool for tool in installed
                if query in f"{tool['name']} {tool['category']} {tool['description']}".lower()
            ]
        if not installed:
            print("\n  No saved tools match that filter.")
            return
        installed = installed[:40]
        print("\n  SELECT A SAVED SOURCE TOOL")
        for index, tool in enumerate(installed, start=1):
            favorite = "FAV" if tool["is_favorite"] else "   "
            print(_clip(f"  [{index:02}] {favorite:<3} {tool['name']:<30} {tool['category']}", width))
        selected = self.prompt("Tool number (ENTER returns)", interrupt_to_caller=True)
        if not selected:
            return
        try:
            tool = installed[int(selected) - 1]
        except (ValueError, IndexError):
            raise VaultError("Choose a saved-tool number shown above") from None
        print(_paint(_clip(f"\n  {tool['name']} - {tool['repository_url']}", width), GLOW))
        print(_clip(f"  Folder: {tool['installed_path']}", width))
        print(_clip(f"  Revision: {tool['commit_sha']}", width))
        action = self.prompt(
            "Action: [F] favorite  [O] folder  [P] project page  [U] update  [R] remove  [ENTER] manager",
            interrupt_to_caller=True,
        ).lower()
        if action == "f":
            self._toggle_tool_favorite(tool)
        elif action == "o":
            print("  Opening the saved source folder." if self._open_tool_folder(tool["installed_path"]) else "  Saved folder is unavailable.")
        elif action == "p":
            accepted = open_site(tool["repository_url"])
            print("  Opening the project page." if accepted else "  The operating system did not accept the browser request.")
        elif action == "u":
            result = update_selected_tool(self.store, tool_id=tool["id"])
            state = "Updated" if result.changed else "Already current"
            print(_paint(_clip(f"  {state}: {result.commit}", width), GOOD))
        elif action == "r":
            confirm = self.prompt("Type REMOVE to delete this saved source checkout", interrupt_to_caller=True)
            if confirm != "REMOVE":
                print("  Nothing changed.")
                return
            result = remove_selected_tool(self.store, tool_id=tool["id"])
            message = "Removed files and record" if result.removed_files else "Removed stale install record"
            print(_paint(_clip(f"  {message}: {result.name}", width), GOOD))

    def _manage_favorite_osint_tools(self) -> None:
        width = self._manager_header("FAVORITE OSINT TOOLS")
        favorites = self.store.list_favorite_osint_tools(limit=100)
        if not favorites:
            print("\n  No favorites yet. Use Discover & Install, then press F on a reviewed source tool.")
            return
        query = self.prompt("Filter favorites (blank shows all)", interrupt_to_caller=True).lower()
        if query:
            favorites = [
                tool for tool in favorites
                if query in f"{tool['name']} {tool['category']} {tool['description']}".lower()
            ]
        if not favorites:
            print("\n  No favorites match that filter.")
            return
        favorites = favorites[:40]
        print("\n  PINNED TOOLS")
        for index, tool in enumerate(favorites, start=1):
            state = "SAVED" if tool["install_status"] == "installed" else "READY"
            print(_clip(f"  [{index:02}] FAV {state:<5} {tool['name']:<28} {tool['category']}", width))
        selected = self.prompt("Tool number (ENTER returns)", interrupt_to_caller=True)
        if not selected:
            return
        try:
            tool = favorites[int(selected) - 1]
        except (ValueError, IndexError):
            raise VaultError("Choose a favorite-tool number shown above") from None
        print(_paint(_clip(f"\n  {tool['name']} - {tool['category']}", width), GLOW))
        print(_clip(f"  {tool['description']}", width))
        print(_clip(f"  {tool['repository_url']}", width))
        if tool["install_status"] == "installed":
            print(_clip(f"  Folder: {tool['installed_path']}", width))
            action = self.prompt(
                "Action: [F] unpin  [O] folder  [P] project page  [U] update  [R] remove  [ENTER] manager",
                interrupt_to_caller=True,
            ).lower()
            if action == "o":
                print("  Opening the saved source folder." if self._open_tool_folder(tool["installed_path"]) else "  Saved folder is unavailable.")
            elif action == "p":
                accepted = open_site(tool["repository_url"])
                print("  Opening the project page." if accepted else "  The operating system did not accept the browser request.")
            elif action == "u":
                result = update_selected_tool(self.store, tool_id=tool["id"])
                state = "Updated" if result.changed else "Already current"
                print(_paint(_clip(f"  {state}: {result.commit}", width), GOOD))
            elif action == "r":
                confirm = self.prompt("Type REMOVE to delete this saved source checkout", interrupt_to_caller=True)
                if confirm != "REMOVE":
                    print("  Nothing changed.")
                    return
                result = remove_selected_tool(self.store, tool_id=tool["id"])
                message = "Removed files and record" if result.removed_files else "Removed stale install record"
                print(_paint(_clip(f"  {message}: {result.name}", width), GOOD))
            elif action == "f":
                self._toggle_tool_favorite(tool)
        else:
            action = self.prompt(
                "Action: [F] unpin  [I] save source  [P] project page  [ENTER] manager",
                interrupt_to_caller=True,
            ).lower()
            if action == "f":
                self._toggle_tool_favorite(tool)
            elif action == "p":
                accepted = open_site(tool["repository_url"])
                print("  Opening the project page." if accepted else "  The operating system did not accept the browser request.")
            elif action == "i":
                confirm = self.prompt("Type YES to save this source checkout", interrupt_to_caller=True)
                if confirm != "YES":
                    print("  Nothing changed.")
                    return
                result = install_selected_tool(self.store, tool_id=tool["id"])
                print(_paint(_clip(f"  Saved: {result.name} -> {result.path}", width), GOOD))

    def _show_osint_manager_status(self) -> None:
        width = self._manager_header("OSINT MANAGER STATUS")
        summary = self.store.osint_tool_summary(20)
        print("\n  TOP CATEGORIES")
        if not summary["categories"]:
            print("  No catalog data yet. Sync the catalog to populate this view.")
        for category in summary["categories"]:
            print(_clip(f"  {category['category']:<30} {category['tools']:>4} available | {category['favorites']:>3} fav | {category['installed']:>3} saved", width))

    def sync_osint_catalog(self) -> None:
        try:
            self._sync_osint_catalog()
        except VaultError as exc:
            print(f"\n  OSINT Manager error: {exc}")
        self.pause("Press ENTER to return to the main menu...")

    def browse_osint_tools(self) -> None:
        try:
            self._browse_and_install_osint()
        except VaultError as exc:
            print(f"\n  OSINT Manager error: {exc}")
        self.pause("Press ENTER to return to the main menu...")

    def my_osint_tools(self) -> None:
        try:
            self._manage_saved_osint_tools()
        except VaultError as exc:
            print(f"\n  OSINT Manager error: {exc}")
        self.pause("Press ENTER to return to the main menu...")

    def favorite_osint_tools(self) -> None:
        try:
            self._manage_favorite_osint_tools()
        except VaultError as exc:
            print(f"\n  OSINT Manager error: {exc}")
        self.pause("Press ENTER to return to the main menu...")

    def tool_manager(self) -> None:
        """Full manager: sync, discover, install, open, update, and remove source checkouts."""
        notice = ""
        last_interrupt = 0.0
        while True:
            width = self._manager_header("OSINT TOOL MANAGER")
            if notice:
                print(_paint(_clip(f"\n  {notice}", width), ACCENT))
            print("\n  [1] Sync curated catalogs")
            print("  [2] Discover & install a source tool")
            print("  [3] My saved tools (open / project page / update / remove)")
            print("  [4] Favorite tools (pinned to the top of manager lists)")
            print("  [5] Catalog status & categories")
            print("  [ENTER] Back to main menu")
            print(_clip("  Ctrl+C cancels an action and returns here. Press Ctrl+C twice quickly here to leave the manager.", width))
            try:
                action = self.prompt("Manager action", interrupt_to_caller=True).lower()
                if not action:
                    return
                notice = ""
                if action in {"1", "s", "sync"}:
                    self._sync_osint_catalog()
                elif action in {"2", "b", "browse", "install"}:
                    self._browse_and_install_osint()
                elif action in {"3", "m", "my"}:
                    self._manage_saved_osint_tools()
                elif action in {"4", "f", "favorite", "favorites", "pin"}:
                    self._manage_favorite_osint_tools()
                elif action in {"5", "status", "stats"}:
                    self._show_osint_manager_status()
                else:
                    notice = "Choose one of the listed manager actions."
                    continue
                self.pause("Press ENTER to return to the OSINT Tool Manager...")
            except KeyboardInterrupt:
                now = time.monotonic()
                if now - last_interrupt <= 1.5:
                    return
                last_interrupt = now
                notice = "Cancelled. You are back in the OSINT Tool Manager; press Ctrl+C again within 2 seconds for the main menu."
            except VaultError as exc:
                notice = f"OSINT Manager error: {exc}"

    def site_workspace(self) -> None:
        _clear()
        print("  SITE WORKSPACE\n")
        sites = self.store.list_sites()
        if sites:
            for site in sites:
                print(f"  [{site['id']}] {site['name']:<24} {site['url']}")
        else:
            print("  No saved sites yet. Add official or otherwise authorized public sources here.")
        print("\n  [A] add  [O] open in browser  [R] remove  [ENTER] return")
        choice = self.prompt("Action").lower()
        try:
            if choice == "a":
                name = self.prompt("Site name")
                url = validate_site_url(self.prompt("Public site URL"))
                self.store.add_site(name, url, utc_now())
                self.store.audit("ui_site_add", {"name": name, "url": url}, utc_now())
                print(f"  Saved {name}.")
            elif choice == "o":
                name = self.prompt("Saved site name")
                site = self.store.get_site(name)
                if not site:
                    print("  No saved site with that name.")
                elif open_site(site["url"]):
                    self.store.audit("ui_site_open", {"name": name, "url": site["url"]}, utc_now())
                    print(f"  Opening {name} in your browser.")
                else:
                    print("  The operating system did not accept the browser request.")
            elif choice == "r":
                name = self.prompt("Saved site name to remove")
                removed = self.store.remove_site(name)
                self.store.audit("ui_site_remove", {"name": name, "removed": removed}, utc_now())
                print("  Removed." if removed else "  No saved site with that name.")
        except Exception as exc:
            print(f"  Site workspace error: {exc}")
        self.pause()

    def event_detail(self) -> None:
        _clear()
        needle = self.prompt("Event ID or search text")
        events = self.store.search(needle, limit=10)
        if events:
            print(json.dumps(events[0].to_dict(), indent=2, ensure_ascii=False))
        else:
            print("  No matching event.")
        self.pause()

    def export_report(self) -> None:
        _clear()
        fmt = self.prompt("Format (json/csv/html)", "html").lower()
        output = self.prompt("Output path", "osaint-report." + ("html" if fmt == "html" else fmt))
        events = self.store.search(limit=5000)
        if fmt == "csv":
            self.store.export_csv(events, output)
        elif fmt == "html":
            self.store.export_html(events, output)
        else:
            Path(output).write_text(json.dumps([event.to_dict() for event in events], indent=2, ensure_ascii=False), encoding="utf-8")
        self.store.audit("ui_export", {"format": fmt, "output": output, "events": len(events)}, utc_now())
        print(f"  Wrote {len(events)} events to {output}")
        self.pause()

    def ingest_local(self) -> None:
        _clear()
        path = self.prompt("JSON file", "tests/fixtures/events.json")
        try:
            events = load_fixture(path, source="local-fixture")
            count = self.store.upsert_events(events)
            self.store.audit("ui_ingest", {"input": path, "events": count}, utc_now())
            print(f"  Imported {count} events.")
        except Exception as exc:
            print(f"  Ingest error: {exc}")
        self.pause()

    def data_quality(self) -> None:
        _clear()
        print("  DATA QUALITY\n")
        print(json.dumps(self.store.quality(), indent=2))
        self.pause()

    def audit_trail(self) -> None:
        _clear()
        print("  AUDIT TRAIL\n")
        print(json.dumps(self.store.recent_audit(30), indent=2, ensure_ascii=False))
        self.pause()

    def watchdog_history(self) -> None:
        _clear()
        print("  WATCHDOG HISTORY\n")
        print(json.dumps(self.store.recent_watchdogs(30), indent=2, ensure_ascii=False))
        self.pause()

    def help_screen(self) -> None:
        _clear()
        width, height = _terminal_size()
        for line in _place_art(_eye_for(width, height), width):
            print(_paint(line, GLOW))
        print(_paint(_center(SLOGAN, width), GOOD))
        print("  HELP & SAFETY\n")
        print("  Navigation: ARROWS or J/K move, ENTER opens, / filters, BACKSPACE edits, ESC clears, Q exits.")
        print("  Data: TuerSS public endpoints are queried with bounded limits and timeouts.")
        print("  Accuracy: unknown fields stay unknown; provenance is preserved; alerts are leads.")
        print("  OSINT Manager: sync, browse, save, open, update, and remove selected source checkouts.")
        print("  Saved tools are source only: third-party setup scripts and dependencies are never auto-run.")
        print("  Ctrl+C cancels the current Manager action and returns to the Manager; press it twice there to return here.")
        print("  Sites: save and open user-approved public sources from the Site Workspace.")
        print("  Boundaries: use authorized public sources only. No private-system access,")
        print("  evasion, credential abuse, identity inference, or tactical targeting.")
        self.pause()


def launch_dashboard(config_path: str | None = None, db_path: str | None = None) -> None:
    dashboard = Dashboard(load_config(config_path), db_path=db_path)
    try:
        dashboard.run()
    finally:
        dashboard.close()
