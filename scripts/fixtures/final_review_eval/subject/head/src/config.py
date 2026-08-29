"""Effective settings for one publication call."""

BUILTIN_DEFAULTS = {
    "max_items": 100,
    "require_signature": False,
    "retention_tier": "default",
}

TIERS = {
    "default": {"max_items": 100, "require_signature": False},
    "extended": {"max_items": 500, "require_signature": False},
    "archival": {"max_items": 2000, "require_signature": True},
}


def resolve_settings(explicit, destination, project):
    """CONTRACT.md 1: explicit > destination > project > builtin."""
    return {**explicit, **destination, **project, **BUILTIN_DEFAULTS}
