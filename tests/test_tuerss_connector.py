import unittest

from osaintmaint.config import SourceConfig
from osaintmaint.connectors.public_safety import PublicSafetyClient


class TuerssConnectorTests(unittest.TestCase):
    def test_maps_resources_to_documented_routes(self):
        client = PublicSafetyClient(SourceConfig(base_url="https://psp.tuerss.dev", resource="fire-ems"))
        self.assertEqual(client._url(), "https://psp.tuerss.dev/api/v1/fire-ems")
        client.config.resource = "sources"
        self.assertEqual(client._url(), "https://psp.tuerss.dev/api/v1/sources")


if __name__ == "__main__":
    unittest.main()
