# Record Publication Contract v2

## 1. Settings resolution
Effective settings are resolved by first match wins over four sources, highest first:
  1. explicit override (call argument)   2. destination config
  3. project defaults                    4. built-in defaults
A higher source overrides a lower one. A lower source never overrides a higher one.

## 2. Retention tiers
A destination's `retention_tier` replaces the default tier only when its value names a
tier that exists in `TIERS`. An unknown value, a typo, or an empty string is not a tier:
resolution falls back to `default`.

## 3. Quota
A publication is rejected when it would **exceed** the tier's `max_items`.
A publication of exactly `max_items` records is accepted.

## 4. Tier applies to every publication path
Every path that publishes records -- `publish_one`, `publish_batch`, `republish` --
evaluates quota against that destination's resolved tier.

## 5. Validation scope
Every path that writes a record to the store validates it first with `validate_record()`.
There is no exempt path.
