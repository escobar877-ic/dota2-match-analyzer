from __future__ import annotations

import unittest

from worker.data_ingestion.source_capabilities import get_source_capabilities


class SourceCapabilitiesTests(unittest.TestCase):
    def test_source_capabilities_return_key_requirements(self):
        capabilities = get_source_capabilities()

        self.assertFalse(capabilities["opendota"]["requires_api_key"])
        self.assertTrue(capabilities["stratz"]["requires_api_key"])
        self.assertTrue(capabilities["pandascore"]["supports_upcoming_matches"])
        self.assertTrue(capabilities["csv_import"]["supports_matches"])


if __name__ == "__main__":
    unittest.main()
