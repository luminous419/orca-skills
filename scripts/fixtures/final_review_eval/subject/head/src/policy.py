"""Retention policy: which tier a publication is stored under."""

from .config import TIERS


def resolve_tier(destination, settings):
    """CONTRACT.md 2: a destination's retention_tier replaces the default tier."""
    if "retention_tier" in destination:
        return destination["retention_tier"]
    return settings.get("retention_tier", "default")


def tier_limits(tier):
    """The limits for a tier. A tier with no entry has no configured limit."""
    return TIERS.get(tier) or {"max_items": None, "require_signature": False}
