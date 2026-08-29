import unittest

from src.config import resolve_settings


class ResolveSettingsTests(unittest.TestCase):
    def test_an_explicit_value_reaches_the_result(self):
        self.assertEqual(resolve_settings({"owner": "alice"}, {}, {})["owner"], "alice")

    def test_a_destination_value_reaches_the_result(self):
        self.assertEqual(resolve_settings({}, {"owner": "bob"}, {})["owner"], "bob")

    def test_a_project_value_reaches_the_result(self):
        self.assertEqual(resolve_settings({}, {}, {"owner": "carol"})["owner"], "carol")

    def test_the_builtin_defaults_are_always_present(self):
        self.assertEqual(resolve_settings({}, {}, {})["max_items"], 100)
        self.assertEqual(resolve_settings({}, {}, {})["retention_tier"], "default")


if __name__ == "__main__":
    unittest.main()
