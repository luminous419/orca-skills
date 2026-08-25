import unittest

from src.validation import InvalidRecord, validate_record

VALID = {"id": "r1", "payload": "x", "created_at": "2026-01-01"}


class ValidateRecordTests(unittest.TestCase):
    def test_a_valid_record_passes(self):
        self.assertEqual(validate_record(dict(VALID))["id"], "r1")

    def test_a_missing_field_is_rejected(self):
        record = dict(VALID)
        del record["payload"]
        with self.assertRaises(InvalidRecord):
            validate_record(record)

    def test_an_empty_id_is_rejected(self):
        record = dict(VALID, id="   ")
        with self.assertRaises(InvalidRecord):
            validate_record(record)


if __name__ == "__main__":
    unittest.main()
