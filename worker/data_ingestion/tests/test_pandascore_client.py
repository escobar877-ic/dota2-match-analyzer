from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from worker.data_ingestion.base_client import ClientResponse
from worker.data_ingestion.sources.pandascore_client import PandaScoreSourceClient


class PandaScoreClientTests(unittest.TestCase):
    def test_pandascore_key_present_enabled(self):
        with patch.dict(os.environ, {"PANDASCORE_API_KEY": "secret"}, clear=False):
            client = PandaScoreSourceClient()
            self.assertTrue(client.is_enabled())
            self.assertTrue(client.has_api_key())

    def test_health_check_called_and_token_not_leaked(self):
        with patch.dict(os.environ, {"PANDASCORE_API_KEY": "secret-token"}, clear=False), patch(
            "worker.data_ingestion.pandascore_client.PandaScoreClient.health_check",
            return_value=ClientResponse(ok=False, error="PandaScore request failed"),
        ):
            client = PandaScoreSourceClient()
            result = client.health_check()

        self.assertFalse(result.ok)
        self.assertNotIn("secret-token", str(result.to_dict()))

    def test_fetch_upcoming_matches_uses_safe_endpoint(self):
        with patch.dict(os.environ, {"PANDASCORE_API_KEY": "secret"}, clear=False), patch(
            "worker.data_ingestion.pandascore_client.PandaScoreClient._get",
            return_value=ClientResponse(ok=True, data=[]),
        ) as get:
            client = PandaScoreSourceClient()
            result = client.fetch_upcoming_matches(limit=25)

        self.assertTrue(result.ok)
        self.assertEqual(get.call_args.args[0], "/dota2/matches/upcoming")
        self.assertEqual(get.call_args.kwargs["query"]["per_page"], 25)

    def test_fetch_historical_matches_uses_past_endpoint_with_date_range(self):
        with patch.dict(os.environ, {"PANDASCORE_API_KEY": "secret"}, clear=False), patch(
            "worker.data_ingestion.pandascore_client.PandaScoreClient._get",
            return_value=ClientResponse(ok=True, data=[]),
        ) as get:
            client = PandaScoreSourceClient()
            result = client.fetch_matches(start_date="2026-01-01", end_date="2026-06-27")

        self.assertTrue(result.ok)
        self.assertEqual(get.call_args.args[0], "/dota2/matches/past")
        self.assertEqual(get.call_args.kwargs["query"]["range[begin_at]"], "2026-01-01,2026-06-27")

    def test_fetch_historical_matches_paginates_past_endpoint(self):
        def fake_get(path, query=None):
            page = query["page"]
            per_page = query["per_page"]
            if page == 1:
                return ClientResponse(ok=True, data=[{"id": f"p1-{i}"} for i in range(per_page)])
            if page == 2:
                return ClientResponse(ok=True, data=[{"id": f"p2-{i}"} for i in range(per_page)])
            return ClientResponse(ok=True, data=[{"id": "p3-0"}])

        with patch.dict(os.environ, {"PANDASCORE_API_KEY": "secret"}, clear=False), patch(
            "worker.data_ingestion.pandascore_client.PandaScoreClient._get",
            side_effect=fake_get,
        ) as get:
            client = PandaScoreSourceClient()
            result = client.fetch_matches(start_date="2026-01-01", end_date="2026-06-27", limit=250)

        self.assertTrue(result.ok)
        self.assertEqual(len(result.records), 201)
        self.assertEqual(get.call_count, 3)
        self.assertEqual([call.kwargs["query"]["page"] for call in get.call_args_list], [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
