"""Record validation."""

REQUIRED_FIELDS = ("id", "payload", "created_at")


class InvalidRecord(Exception):
    """Raised when a record is missing a required field or carries an empty id."""


def validate_record(record):
    """CONTRACT.md 4: every path that writes a record validates it first."""
    missing = [field for field in REQUIRED_FIELDS if field not in record]
    if missing:
        raise InvalidRecord("missing fields: " + ", ".join(missing))
    if not str(record["id"]).strip():
        raise InvalidRecord("id must be a non-empty string")
    return record
