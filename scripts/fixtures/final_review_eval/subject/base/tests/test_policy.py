import unittest

from src.policy import resolve_tier


class ResolveTierTests(unittest.TestCase):
    def test_every_publication_uses_the_builtin_default(self):
        self.assertEqual(resolve_tier({}), "default")
        self.assertEqual(resolve_tier({"retention_tier": "extended"}), "default")


if __name__ == "__main__":
    unittest.main()
