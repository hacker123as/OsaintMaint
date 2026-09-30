import tempfile
import unittest
from pathlib import Path

from osaintmaint.storage import Store
from osaintmaint.tool_vault import parse_awesome_osint, parse_osint_repositories_csv, remove_selected_tool


SAMPLE_INDEX = """# Example index

## General Search
* [Alpha Search](https://github.com/example/alpha-search) - Search public pages.
* [Alpha Documentation](https://github.com/example/alpha-search/blob/main/README.md) - Duplicate repository.
* [Not a repository](https://github.com/topics/osint) - Ignore category pages.

## Media Analysis
* [Beta Lens](https://github.com/example/beta-lens \"homepage\") - Review public images.
"""

SAMPLE_REPOSITORIES_CSV = """Project,Repository,Description,Target Input,Categories,Type,Hosting,Archived,Fork,Repository Status
Gamma Lens,https://github.com/example/gamma-lens,Analyze public images,Image,Media,Python,GitHub,false,false,active
Archived Tool,https://github.com/example/archived,Should be excluded,URL,Web,Python,GitHub,true,false,active
Unavailable Tool,https://github.com/example/unavailable,Should be excluded,Domain,Infrastructure,Go,GitHub,false,false,unavailable
"""


class ToolVaultTests(unittest.TestCase):
    def test_parse_index_normalizes_and_deduplicates_repository_entries(self):
        tools = parse_awesome_osint(SAMPLE_INDEX, commit="abc123", synced_at="2026-09-29T00:00:00Z")

        self.assertEqual(len(tools), 2)
        self.assertEqual(tools[0]["name"], "Alpha Search")
        self.assertEqual(tools[0]["category"], "General Search")
        self.assertEqual(tools[0]["repository_url"], "https://github.com/example/alpha-search")
        self.assertEqual(tools[1]["category"], "Media Analysis")
        self.assertTrue(tools[1]["clone_url"].endswith("/beta-lens.git"))

    def test_repository_first_csv_only_keeps_active_non_fork_github_tools(self):
        tools = parse_osint_repositories_csv(
            SAMPLE_REPOSITORIES_CSV,
            commit="def456",
            synced_at="2026-09-29T00:00:00Z",
        )

        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["name"], "Gamma Lens")
        self.assertEqual(tools[0]["category"], "Media")
        self.assertIn("Input: Image", tools[0]["description"])

    def test_tool_catalog_and_install_records_are_persistent(self):
        tools = parse_awesome_osint(SAMPLE_INDEX, commit="abc123", synced_at="2026-09-29T00:00:00Z")
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "vault.db")
            try:
                self.assertEqual(store.upsert_osint_tools(tools), 2)
                listed = store.list_osint_tools("alpha")
                self.assertEqual(len(listed), 1)
                self.assertEqual(listed[0]["install_status"], None)
                store.record_tool_install(
                    listed[0]["id"],
                    str(Path(directory) / "managed-tools" / listed[0]["id"]),
                    "deadbeef",
                    "2026-09-29T00:01:00Z",
                    "installed",
                )
                self.assertEqual(store.stats()["tool_catalog"], 2)
                self.assertEqual(store.stats()["installed_tools"], 1)
                self.assertEqual(store.list_installed_tools()[0]["name"], "Alpha Search")
            finally:
                store.close()

    def test_catalog_name_changes_keep_the_existing_managed_checkout(self):
        tool = parse_awesome_osint(SAMPLE_INDEX, commit="abc123", synced_at="2026-09-29T00:00:00Z")[0]
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "vault.db")
            try:
                original = {**tool, "id": "old-alpha"}
                store.upsert_osint_tools([original])
                store.record_tool_install("old-alpha", "C:/managed/alpha", "deadbeef", "2026-09-29T00:01:00Z", "installed")
                store.favorite_osint_tool("old-alpha", "2026-09-29T00:02:00Z")
                renamed = {**tool, "id": "new-alpha", "name": "Alpha Search Renamed"}
                store.upsert_osint_tools([renamed])

                current = store.get_osint_tool("old-alpha")
                self.assertIsNotNone(current)
                self.assertEqual(current["name"], "Alpha Search Renamed")
                self.assertEqual(current["install_status"], "installed")
                self.assertEqual(current["is_favorite"], 1)
                self.assertIsNone(store.get_osint_tool("new-alpha"))
            finally:
                store.close()

    def test_favorites_are_persistent_and_sorted_first(self):
        tools = parse_awesome_osint(SAMPLE_INDEX, commit="abc123", synced_at="2026-09-29T00:00:00Z")
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "vault.db")
            try:
                store.upsert_osint_tools(tools)
                beta = next(tool for tool in tools if tool["name"] == "Beta Lens")
                self.assertTrue(store.favorite_osint_tool(beta["id"], "2026-09-29T00:01:00Z"))
                self.assertFalse(store.favorite_osint_tool(beta["id"], "2026-09-29T00:02:00Z"))

                listed = store.list_osint_tools()
                self.assertEqual(listed[0]["id"], beta["id"])
                self.assertEqual(listed[0]["is_favorite"], 1)
                self.assertEqual(store.list_favorite_osint_tools()[0]["id"], beta["id"])
                self.assertEqual(store.stats()["favorite_tools"], 1)
                self.assertEqual(store.osint_tool_summary()["favorite_tools"], 1)

                self.assertTrue(store.unfavorite_osint_tool(beta["id"]))
                self.assertFalse(store.unfavorite_osint_tool(beta["id"]))
                self.assertEqual(store.stats()["favorite_tools"], 0)
            finally:
                store.close()

    def test_remove_selected_tool_removes_only_the_managed_checkout(self):
        tool = parse_awesome_osint(SAMPLE_INDEX, commit="abc123", synced_at="2026-09-29T00:00:00Z")[0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "managed-tools"
            checkout = root / tool["id"]
            (checkout / ".git").mkdir(parents=True)
            store = Store(Path(directory) / "vault.db")
            try:
                store.upsert_osint_tools([tool])
                store.record_tool_install(tool["id"], str(checkout), "deadbeef", "2026-09-29T00:01:00Z", "installed")
                result = remove_selected_tool(store, tool_id=tool["id"], tools_dir=root)

                self.assertTrue(result.removed_files)
                self.assertFalse(checkout.exists())
                self.assertFalse(store.list_installed_tools())
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
