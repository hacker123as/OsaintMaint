import tempfile
import unittest
from pathlib import Path

from osaintmaint.sites import SiteError, validate_site_url
from osaintmaint.storage import Store


class SiteTests(unittest.TestCase):
    def test_validates_complete_public_http_urls(self):
        self.assertEqual(validate_site_url("https://example.org/source"), "https://example.org/source")
        with self.assertRaises(SiteError):
            validate_site_url("file:///C:/private.txt")
        with self.assertRaises(SiteError):
            validate_site_url("https://user:pass@example.org")

    def test_persists_sites_locally(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "sites.db")
            store.add_site("Official portal", "https://example.org", "2026-09-30T00:00:00Z")
            self.assertEqual(store.get_site("Official portal")["url"], "https://example.org")
            self.assertEqual(len(store.list_sites()), 1)
            self.assertTrue(store.remove_site("Official portal"))
            store.close()


if __name__ == "__main__":
    unittest.main()
