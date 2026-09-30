import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from osaintmaint.cli import main
from osaintmaint.storage import Store
from osaintmaint.tool_vault import parse_awesome_osint


class CliTests(unittest.TestCase):
    def test_cli_ingest_search_and_export(self):
        fixture = Path(__file__).parent / "fixtures" / "events.json"
        with tempfile.TemporaryDirectory() as directory:
            db = str(Path(directory) / "events.db")
            output = str(Path(directory) / "events.csv")
            self.assertEqual(main(["ingest", "--db", db, "--input", str(fixture)]), 0)
            buffer = StringIO()
            with redirect_stdout(buffer):
                self.assertEqual(main(["search", "--db", db, "--query", "fire", "--json"]), 0)
            self.assertIn("Brush fire", buffer.getvalue())
            self.assertEqual(main(["export", "--db", db, "--format", "csv", "--out", output]), 0)
            self.assertTrue(Path(output).exists())

    def test_cli_favorite_commands_pin_and_unpin_catalog_tools(self):
        sample = """## Search
* [Alpha Search](https://github.com/example/alpha-search) - Search public pages.
"""
        with tempfile.TemporaryDirectory() as directory:
            db = str(Path(directory) / "tools.db")
            tools = parse_awesome_osint(sample, commit="abc123", synced_at="2026-09-29T00:00:00Z")
            store = Store(db)
            try:
                store.upsert_osint_tools(tools)
            finally:
                store.close()

            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["tools", "favorite", "--db", db, "--id", tools[0]["id"], "--json"]), 0)
                self.assertEqual(main(["tools", "favorites", "--db", db, "--json"]), 0)
                self.assertEqual(main(["tools", "unfavorite", "--db", db, "--id", tools[0]["id"], "--json"]), 0)
            text = output.getvalue()
            self.assertIn('"status": "favorited"', text)
            self.assertIn("Alpha Search", text)
            self.assertIn('"status": "unfavorited"', text)


if __name__ == "__main__":
    unittest.main()
