"""Retention policy: which tier a publication is stored under."""


def resolve_tier(settings):
    """v1 had no per-destination tier: everything used the built-in default."""
    return "default"
