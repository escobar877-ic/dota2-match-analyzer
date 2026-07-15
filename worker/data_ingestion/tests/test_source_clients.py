from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from worker.data_ingestion.sources.opendota_client import OpenDotaSourceClient
from worker.data_ingestion.sources.pandascore_client import PandaScoreSourceClient
from worker.data_ingestion.sources.stratz_client import StratzSourceClient
from worker.data_ingestion.base_client import ClientResponse


class SourceClientsTests(unittest.TestCase):
    def test_opendota_enabled_without_key(self):
        with patch.dict(os.environ, {"OPENDOTA_API_KEY": ""}, clear=False):
            client = OpenDotaSourceClient()
            self.assertTrue(client.is_enabled())
            self.assertFalse(client.requires_api_key)

    def test_stratz_disabled_without_key(self):
        with patch.dict(os.environ, {"STRATZ_API_KEY": ""}, clear=False):
            client = StratzSourceClient()
            result = client.fetch_matches()
            self.assertFalse(client.is_enabled())
            self.assertFalse(result.ok)
            self.assertIn("STRATZ_API_KEY missing", result.error)

    def test_pandascore_disabled_without_key(self):
        with patch.dict(os.environ, {"PANDASCORE_API_KEY": ""}, clear=False):
            client = PandaScoreSourceClient()
            self.assertFalse(client.is_enabled())

    def test_no_api_key_leaks_in_output(self):
        with patch.dict(os.environ, {"STRATZ_API_KEY": "abc-secret"}, clear=False):
            client = StratzSourceClient()
            text = str(client.get_status())
            self.assertNotIn("abc-secret", text)

    def test_stratz_date_range_fetch_is_clean_unsupported(self):
        with patch.dict(os.environ, {"STRATZ_API_KEY": "abc-secret"}, clear=False):
            client = StratzSourceClient()
            result = client.fetch_matches()

        self.assertFalse(result.ok)
        self.assertIn("date-range historical fetch is not implemented", result.error)
        self.assertNotIn("abc-secret", str(result.to_dict()))

    def test_stratz_health_check_uses_safe_query(self):
        with patch.dict(os.environ, {"STRATZ_API_KEY": "abc-secret"}, clear=False), patch(
            "worker.data_ingestion.stratz_client.StratzClient._graphql",
            return_value=ClientResponse(ok=True, data={"data": {"__typename": "DotaQuery"}}),
        ) as graphql:
            client = StratzSourceClient()
            result = client.health_check()

        self.assertTrue(result.ok)
        self.assertIn("__typename", graphql.call_args.args[0])
        self.assertNotIn("matches(request", graphql.call_args.args[0])

    def test_stratz_match_details_uses_ids_query(self):
        with patch.dict(os.environ, {"STRATZ_API_KEY": "abc-secret"}, clear=False), patch(
            "worker.data_ingestion.stratz_client.StratzClient._graphql",
            return_value=ClientResponse(ok=True, data={"data": {"matches": []}}),
        ) as graphql:
            client = StratzSourceClient()
            result = client.fetch_match_details("123")

        self.assertTrue(result.ok)
        self.assertIn("matches(ids: $ids)", graphql.call_args.args[0])
        self.assertEqual(graphql.call_args.args[1], {"ids": [123]})

    def test_stratz_match_details_records_are_unwrapped(self):
        raw_match = {"id": 123, "radiantTeam": {"id": 1, "name": "Team Liquid"}}
        with patch.dict(os.environ, {"STRATZ_API_KEY": "abc-secret"}, clear=False), patch(
            "worker.data_ingestion.stratz_client.StratzClient._graphql",
            return_value=ClientResponse(ok=True, data={"data": {"matches": [raw_match]}}),
        ):
            client = StratzSourceClient()
            result = client.fetch_match_details("123")

        self.assertEqual(result.records, [raw_match])


if __name__ == "__main__":
    unittest.main()
