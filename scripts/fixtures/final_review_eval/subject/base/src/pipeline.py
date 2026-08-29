"""The publication entry points."""

from .policy import resolve_tier
from .quota import QuotaExceeded, enforce_quota
from .validation import validate_record


def _write_record(store, record, tier):
    """The single sink every publication path reaches."""
    store.append({**record, "retention_tier": tier})
    return record["id"]


def publish_one(store, record, settings):
    validate_record(record)
    if not enforce_quota(store + [record], settings):
        raise QuotaExceeded(record["id"])
    return _write_record(store, record, resolve_tier(settings))


def publish_batch(store, records, settings):
    for record in records:
        validate_record(record)
    if not enforce_quota(store + list(records), settings):
        raise QuotaExceeded("batch")
    tier = resolve_tier(settings)
    return [_write_record(store, record, tier) for record in records]
