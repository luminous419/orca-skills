"""Quota enforcement for one publication."""

from .policy import tier_limits


class QuotaExceeded(Exception):
    """Raised when a publication would exceed the configured limit."""


def enforce_quota(store, settings, tier="default"):
    """CONTRACT.md 3: reject only when the publication would EXCEED max_items.

    `store` is the resulting store -- the records already held plus the ones this
    publication would add -- and the limit now comes from the resolved tier.
    """
    limit = tier_limits(tier).get("max_items")
    if limit is None:
        return True
    return len(store) < limit
