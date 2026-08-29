import unittest

from src.pipeline import publish_batch, publish_one, republish
from src.quota import QuotaExceeded
from src.validation import InvalidRecord

SETTINGS = {"max_items": 100}
DESTINATION = {"name": "primary"}


def _record(identifier):
    return {"id": identifier, "payload": "x", "created_at": "2026-01-01"}


class PublishOneTests(unittest.TestCase):
    def test_a_record_is_written(self):
        store = []
        self.assertEqual(publish_one(store, _record("r1"), SETTINGS, DESTINATION), "r1")
        self.assertEqual(store[0]["retention_tier"], "default")

    def test_an_invalid_record_is_refused(self):
        with self.assertRaises(InvalidRecord):
            publish_one([], {"id": "r1"}, SETTINGS, DESTINATION)

    def test_a_publication_past_the_limit_is_refused(self):
        store = [_record(str(index)) for index in range(150)]
        with self.assertRaises(QuotaExceeded):
            publish_one(store, _record("r1"), SETTINGS, DESTINATION)


class PublishBatchTests(unittest.TestCase):
    def test_every_record_in_the_batch_is_written(self):
        store = []
        written = publish_batch(
            store, [_record("r1"), _record("r2")], SETTINGS, DESTINATION
        )
        self.assertEqual(written, ["r1", "r2"])
        self.assertEqual(len(store), 2)

    def test_a_batch_past_the_limit_is_refused(self):
        store = [_record(str(index)) for index in range(150)]
        with self.assertRaises(QuotaExceeded):
            publish_batch(store, [_record("r1")], SETTINGS, DESTINATION)


class RepublishTests(unittest.TestCase):
    def test_a_retried_record_is_written(self):
        store = []
        self.assertEqual(republish(store, _record("r1"), SETTINGS, DESTINATION), "r1")
        self.assertEqual(len(store), 1)


if __name__ == "__main__":
    unittest.main()
