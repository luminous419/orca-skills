"""Effective settings for one publication call."""

BUILTIN_DEFAULTS = {
    "max_items": 100,
    "require_signature": False,
}


def resolve_settings(explicit, project):
    """CONTRACT.md 1: explicit > project > builtin."""
    merged = dict(BUILTIN_DEFAULTS)
    merged.update(project)
    merged.update(explicit)
    return merged
