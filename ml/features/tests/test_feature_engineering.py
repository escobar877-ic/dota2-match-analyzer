import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from ml.features.feature_schema import ALL_FEATURE_FIELDS, SAFE_DEFAULTS, assert_complete_feature_set
from ml.features.leakage_guard import LeakageError, assert_no_forbidden_fields, validate_prematch_inputs


class FeatureSchemaTests(unittest.TestCase):
    def test_safe_defaults_contain_all_fields(self):
        assert_complete_feature_set(SAFE_DEFAULTS)
        self.assertEqual(set(ALL_FEATURE_FIELDS), set(SAFE_DEFAULTS.keys()))

    def test_missing_feature_field_fails(self):
        features = dict(SAFE_DEFAULTS)
        features.pop("elo_diff")
        with self.assertRaises(ValueError):
            assert_complete_feature_set(features)


class LeakageGuardTests(unittest.TestCase):
    def test_forbidden_fields_are_blocked(self):
        with self.assertRaises(LeakageError):
            assert_no_forbidden_fields(["winner_team_id", "duration"])

    def test_current_match_is_blocked(self):
        current = SimpleNamespace(id=10, start_time=datetime(2026, 1, 10, tzinfo=timezone.utc))
        historical = [SimpleNamespace(id=10, start_time=datetime(2026, 1, 9, tzinfo=timezone.utc))]
        with self.assertRaises(LeakageError):
            validate_prematch_inputs(current, historical)

    def test_future_match_is_blocked(self):
        current = SimpleNamespace(id=10, start_time=datetime(2026, 1, 10, tzinfo=timezone.utc))
        historical = [SimpleNamespace(id=9, start_time=datetime(2026, 1, 11, tzinfo=timezone.utc))]
        with self.assertRaises(LeakageError):
            validate_prematch_inputs(current, historical)


class SafeDefaultTests(unittest.TestCase):
    def test_recent_form_defaults_to_null_when_data_is_missing(self):
        self.assertIsNone(SAFE_DEFAULTS["team_a_winrate_last_5"])
        self.assertIsNone(SAFE_DEFAULTS["form_diff_10"])


if __name__ == "__main__":
    unittest.main()
