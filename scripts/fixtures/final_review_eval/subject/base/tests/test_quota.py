import unittest

from src.quota import enforce_quota

SETTINGS = {"max_items": 100}


def _store(count):
    return [{"id": str(index)} for index in range(count)]


class EnforceQuotaTests(unittest.TestCase):
    def test_a_store_well_under_the_limit_is_accepted(self):
        self.assertTrue(enforce_quota(_store(50), SETTINGS))

    def test_a_store_well_over_the_limit_is_rejected(self):
        self.assertFalse(enforce_quota(_store(150), SETTINGS))


if __name__ == "__main__":
    unittest.main()
