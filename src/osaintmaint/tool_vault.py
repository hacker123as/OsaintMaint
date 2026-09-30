"""Persistent, opt-in source checkouts from curated OSINT repositories.

The manager downloads only a selected repository's source checkout. It never
executes a project's setup scripts, installs packages, or launches its code.
That makes discovery, storage, updating, and removal explicit user choices.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
import hashlib
from io import StringIO
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
from typing import Callable
from urllib.parse import urlparse

from .models import utc_now
from .storage import Store


AWESOME_OSINT_REPOSITORY = "https://github.com/jivoi/awesome-osint.git"
AWESOME_OSINT_PAGE = "https://github.com/jivoi/awesome-osint"
OSINT_REPOSITORIES_REPOSITORY = "https://github.com/osintshifu/awesome-osint-repos.git"
OSINT_REPOSITORIES_PAGE = "https://github.com/osintshifu/awesome-osint-repos"
_ENTRY = re.compile(
    r"^\s*[*-]\s+\[(?P<name>[^\]]+)\]\((?P<url>https://github\.com/[^)\s]+)(?:\s+\"[^\"]*\")?\)\s*(?:-\s*(?P<description>.*))?$",
    re.IGNORECASE,
)


class VaultError(RuntimeError):
    """A user-facing OSINT Tool Manager error."""


@dataclass(slots=True, frozen=True)
class CatalogSync:
    name: str
    tools: int
    commit: str
    repository: str


@dataclass(slots=True, frozen=True)
class SyncResult:
    tools: int
    catalogs: tuple[CatalogSync, ...]
    registry_path: str


@dataclass(slots=True, frozen=True)
class InstallResult:
    tool_id: str
    name: str
    path: str
    commit: str
    reused: bool


@dataclass(slots=True, frozen=True)
class UpdateResult:
    tool_id: str
    name: str
    path: str
    previous_commit: str
    commit: str

    @property
    def changed(self) -> bool:
        return self.previous_commit != self.commit


@dataclass(slots=True, frozen=True)
class RemoveResult:
    tool_id: str
    name: str
    path: str
    removed_files: bool


@dataclass(slots=True, frozen=True)
class CatalogDefinition:
    name: str
    repository: str
    page: str
    filename: str
    parser: Callable[[str, str, str], list[dict[str, str]]]
    minimum_tools: int


def _git(arguments: list[str], *, timeout: int = 120) -> str:
    if not shutil.which("git"):
        raise VaultError("Git is required for the OSINT Tool Manager but was not found on PATH")
    environment = dict(os.environ)
    environment["GIT_TERMINAL_PROMPT"] = "0"
    try:
        result = subprocess.run(
            ["git", *arguments],
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=environment,
        )
    except OSError as exc:
        raise VaultError(f"Could not start Git: {exc}") from exc
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "Git command failed").strip().splitlines()[-1]
        raise VaultError(message)
    return result.stdout.strip()


def _heading(value: str) -> str:
    value = value.lstrip("#").strip()
    value = re.sub(r"\[[^\]]+\]\([^)]+\)", "", value)
    value = re.sub(r"[^A-Za-z0-9 /&+._-]", "", value).strip()
    return value or "General"


def _repository_urls(value: str) -> tuple[str, str] | None:
    parsed = urlparse(value.strip())
    if parsed.scheme != "https" or parsed.netloc.lower() not in {"github.com", "www.github.com"}:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return None
    owner, repository = parts[0], parts[1].removesuffix(".git")
    if owner.lower() in {"search", "topics", "settings"} or not repository:
        return None
    page = f"https://github.com/{owner}/{repository}"
    return page, f"{page}.git"


def _tool_id(repository_url: str) -> str:
    """Keep IDs stable even if a catalog later changes a display name."""
    parsed = urlparse(repository_url)
    slug = re.sub(r"[^a-z0-9]+", "-", parsed.path.strip("/").lower()).strip("-") or "tool"
    digest = hashlib.sha256(repository_url.encode("utf-8")).hexdigest()[:8]
    return f"{slug[:48]}-{digest}"


def _entry(
    *,
    name: str,
    repository_url: str,
    category: str,
    description: str,
    catalog_source: str,
    catalog_commit: str,
    synced_at: str,
) -> dict[str, str]:
    page, clone = _repository_urls(repository_url) or ("", "")
    if not page:
        raise ValueError("Only complete https://github.com/owner/repository URLs are supported")
    return {
        "id": _tool_id(page),
        "name": name.strip() or page.rsplit("/", 1)[-1],
        "repository_url": page,
        "clone_url": clone,
        "category": category.strip() or "General",
        "description": description.strip()[:1000] or "Curated GitHub OSINT source.",
        "catalog_source": catalog_source,
        "catalog_commit": catalog_commit,
        "synced_at": synced_at,
    }


def parse_awesome_osint(markdown: str, commit: str, synced_at: str) -> list[dict[str, str]]:
    """Parse GitHub repository entries from the maintained awesome-osint README."""
    category = "General"
    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for line in markdown.splitlines():
        if line.startswith("##"):
            candidate = _heading(line)
            if candidate.lower() not in {"table of contents", "contributing", "credits"}:
                category = candidate
            continue
        match = _ENTRY.match(line)
        if not match:
            continue
        repository = _repository_urls(match.group("url"))
        if not repository:
            continue
        page, _ = repository
        if page in seen:
            continue
        seen.add(page)
        entries.append(
            _entry(
                name=match.group("name"),
                repository_url=page,
                category=category,
                description=match.group("description") or "Curated GitHub OSINT source.",
                catalog_source=AWESOME_OSINT_PAGE,
                catalog_commit=commit,
                synced_at=synced_at,
            )
        )
    return entries


def parse_osint_repositories_csv(contents: str, commit: str, synced_at: str) -> list[dict[str, str]]:
    """Parse the implementation-only awesome-osint-repos CSV catalog."""
    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in csv.DictReader(StringIO(contents)):
        if row.get("Hosting", "").strip().lower() != "github":
            continue
        if row.get("Repository Status", "").strip().lower() != "active":
            continue
        if row.get("Archived", "").strip().lower() == "true" or row.get("Fork", "").strip().lower() == "true":
            continue
        repository = _repository_urls(row.get("Repository", ""))
        if not repository:
            continue
        page, _ = repository
        if page in seen:
            continue
        seen.add(page)
        context = []
        if row.get("Target Input", "").strip():
            context.append(f"Input: {row['Target Input'].strip()}")
        if row.get("Type", "").strip():
            context.append(f"Type: {row['Type'].strip()}")
        description = row.get("Description", "").strip()
        if context:
            description = f"{description} {' | '.join(context)}".strip()
        entries.append(
            _entry(
                name=row.get("Project", ""),
                repository_url=page,
                category=row.get("Categories", "") or "General",
                description=description,
                catalog_source=OSINT_REPOSITORIES_PAGE,
                catalog_commit=commit,
                synced_at=synced_at,
            )
        )
    return entries


CATALOGS = (
    CatalogDefinition(
        "awesome-osint",
        AWESOME_OSINT_REPOSITORY,
        AWESOME_OSINT_PAGE,
        "README.md",
        parse_awesome_osint,
        100,
    ),
    CatalogDefinition(
        "awesome-osint-repos",
        OSINT_REPOSITORIES_REPOSITORY,
        OSINT_REPOSITORIES_PAGE,
        "osint-repositories.csv",
        parse_osint_repositories_csv,
        300,
    ),
)


def _sync_registry(definition: CatalogDefinition, registry_root: Path, synced_at: str) -> tuple[CatalogSync, list[dict[str, str]]]:
    checkout = registry_root / definition.name
    if not checkout.exists():
        _git(["clone", "--depth", "1", "--no-tags", definition.repository, str(checkout)], timeout=240)
    elif (checkout / ".git").is_dir():
        _git(["-C", str(checkout), "pull", "--ff-only"], timeout=240)
    else:
        raise VaultError(f"Registry path exists but is not a Git checkout: {checkout}")
    commit = _git(["-C", str(checkout), "rev-parse", "HEAD"])
    source_file = checkout / definition.filename
    if not source_file.is_file():
        raise VaultError(f"{definition.name} did not include {definition.filename}")
    entries = definition.parser(source_file.read_text(encoding="utf-8-sig"), commit, synced_at)
    if len(entries) < definition.minimum_tools:
        raise VaultError(
            f"{definition.name} contains only {len(entries)} usable GitHub tools; expected at least {definition.minimum_tools}"
        )
    return CatalogSync(definition.name, len(entries), commit, definition.page), entries


def sync_catalog(store: Store, *, registry_dir: str | Path = "data/registries") -> SyncResult:
    """Synchronize multiple curated indexes into one large, deduplicated Tool Manager catalog."""
    registry_root = Path(registry_dir).resolve()
    registry_root.mkdir(parents=True, exist_ok=True)
    synced_at = utc_now()
    catalog_syncs: list[CatalogSync] = []
    merged: dict[str, dict[str, str]] = {}
    for definition in CATALOGS:
        catalog, entries = _sync_registry(definition, registry_root, synced_at)
        catalog_syncs.append(catalog)
        for entry in entries:
            merged[entry["repository_url"]] = entry
    if len(merged) < 400:
        raise VaultError(f"Combined catalog contains only {len(merged)} unique GitHub tools; expected at least 400")
    count = store.upsert_osint_tools(merged.values())
    store.audit(
        "tool_manager_sync",
        {
            "tools": count,
            "catalogs": [
                {"name": item.name, "tools": item.tools, "commit": item.commit, "repository": item.repository}
                for item in catalog_syncs
            ],
            "registry": str(registry_root),
        },
        synced_at,
    )
    return SyncResult(count, tuple(catalog_syncs), str(registry_root))


def _managed_root(tools_dir: str | Path) -> Path:
    root = Path(tools_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _managed_checkout(store: Store, tool_id: str, tools_dir: str | Path) -> tuple[dict, Path, Path]:
    tool = store.get_osint_tool(tool_id)
    if not tool:
        raise VaultError(f"Tool not found: {tool_id}. Run `osaint tools sync` first.")
    if tool.get("install_status") != "installed" or not tool.get("installed_path"):
        raise VaultError(f"{tool['name']} is not saved locally yet.")
    root = _managed_root(tools_dir)
    destination = Path(tool["installed_path"]).resolve()
    try:
        destination.relative_to(root)
    except ValueError as exc:
        raise VaultError("Saved tool is outside the selected managed-tools folder") from exc
    if destination == root:
        raise VaultError("Invalid managed-tool destination")
    return tool, root, destination


def _retry_readonly_remove(function: Callable, path: str, _exc_info: object) -> None:
    """Allow removal of Git's read-only packed files on Windows, within a verified checkout."""
    os.chmod(path, stat.S_IWRITE)
    function(path)


def install_selected_tool(store: Store, *, tool_id: str, tools_dir: str | Path = "managed-tools") -> InstallResult:
    """Clone one selected repository exactly once; never execute its code."""
    tool = store.get_osint_tool(tool_id)
    if not tool:
        raise VaultError(f"Tool not found: {tool_id}. Run `osaint tools sync` first.")
    root = _managed_root(tools_dir)
    destination = (root / tool_id).resolve()
    try:
        destination.relative_to(root)
    except ValueError as exc:
        raise VaultError("Invalid managed-tool destination") from exc
    if destination.exists():
        if not (destination / ".git").is_dir():
            raise VaultError(f"Managed path already exists and is not a Git checkout: {destination}")
        commit = _git(["-C", str(destination), "rev-parse", "HEAD"])
        store.record_tool_install(tool_id, str(destination), commit, utc_now(), "installed")
        return InstallResult(tool_id, tool["name"], str(destination), commit, True)
    try:
        _git(["clone", "--depth", "1", "--no-tags", tool["clone_url"], str(destination)], timeout=240)
        commit = _git(["-C", str(destination), "rev-parse", "HEAD"])
        store.record_tool_install(tool_id, str(destination), commit, utc_now(), "installed")
        store.audit("tool_manager_install", {"tool_id": tool_id, "name": tool["name"], "path": str(destination), "commit": commit}, utc_now())
        return InstallResult(tool_id, tool["name"], str(destination), commit, False)
    except Exception as exc:
        store.record_tool_install(tool_id, str(destination), "", utc_now(), "error", str(exc))
        raise


def update_selected_tool(store: Store, *, tool_id: str, tools_dir: str | Path = "managed-tools") -> UpdateResult:
    """Fast-forward one managed source checkout without executing its contents."""
    tool, _, destination = _managed_checkout(store, tool_id, tools_dir)
    if not (destination / ".git").is_dir():
        raise VaultError(f"Saved path is not a Git checkout: {destination}")
    previous_commit = _git(["-C", str(destination), "rev-parse", "HEAD"])
    _git(["-C", str(destination), "pull", "--ff-only"], timeout=240)
    commit = _git(["-C", str(destination), "rev-parse", "HEAD"])
    store.record_tool_install(tool_id, str(destination), commit, utc_now(), "installed")
    store.audit(
        "tool_manager_update",
        {"tool_id": tool_id, "name": tool["name"], "path": str(destination), "from": previous_commit, "to": commit},
        utc_now(),
    )
    return UpdateResult(tool_id, tool["name"], str(destination), previous_commit, commit)


def remove_selected_tool(store: Store, *, tool_id: str, tools_dir: str | Path = "managed-tools") -> RemoveResult:
    """Remove one explicitly selected managed checkout and its install record."""
    tool, _, destination = _managed_checkout(store, tool_id, tools_dir)
    removed_files = False
    if destination.exists():
        if not (destination / ".git").is_dir():
            raise VaultError(f"Refusing to remove a non-Git managed path: {destination}")
        try:
            shutil.rmtree(destination, onerror=_retry_readonly_remove)
        except OSError as exc:
            raise VaultError(f"Could not remove the saved source checkout: {exc}") from exc
        removed_files = True
    store.remove_tool_install(tool_id)
    store.audit(
        "tool_manager_remove",
        {"tool_id": tool_id, "name": tool["name"], "path": str(destination), "removed_files": removed_files},
        utc_now(),
    )
    return RemoveResult(tool_id, tool["name"], str(destination), removed_files)
