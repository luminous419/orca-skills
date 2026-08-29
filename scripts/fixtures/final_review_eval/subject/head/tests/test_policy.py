import unittest

from src.policy import resolve_tier, tier_limits


class ResolveTierTests(unittest.TestCase):
    def test_a_destination_tier_replaces_the_default(self):
        self.assertEqual(resolve_tier({"retention_tier": "extended"}, {}), "extended")

    def test_a_destination_without_a_tier_falls_back_to_the_settings(self):
        self.assertEqual(resolve_tier({}, {"retention_tier": "archival"}), "archival")

    def test_a_destination_and_settings_without_a_tier_use_the_default(self):
        self.assertEqual(resolve_tier({}, {}), "default")


class TierLimitsTests(unittest.TestCase):
    def test_a_known_tier_reports_its_limits(self):
        self.assertEqual(tier_limits("extended")["max_items"], 500)
        self.assertTrue(tier_limits("archival")["require_signature"])


if __name__ == "__main__":
    unittest.main()
