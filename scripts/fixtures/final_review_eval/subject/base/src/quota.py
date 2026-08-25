"""Quota enforcement for one publication."""


class QuotaExceeded(Exception):
    """Raised when a publication would exceed the configured limit."""


def enforce_quota(store, settings):
    """CONTRACT.md 2: reject only when the publication would EXCEED max_items.

    `store` is the resulting store -- the records already held plus the ones this
    publication would add -- so a publication of exactly max_items is accepted.
    """
    return len(store) <= settings["max_items"]
