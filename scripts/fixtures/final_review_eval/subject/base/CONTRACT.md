# Record Publication Contract v1

## 1. Settings resolution
Effective settings are resolved by first match wins over three sources, highest first:
  1. explicit override (call argument)   2. project defaults
  3. built-in defaults
A higher source overrides a lower one. A lower source never overrides a higher one.

## 2. Quota
A publication is rejected when it would **exceed** `max_items`.
A publication of exactly `max_items` records is accepted.

## 3. Quota applies to every publication path
Every path that publishes records -- `publish_one`, `publish_batch` -- evaluates
quota against the effective settings.

## 4. Validation scope
Every path that writes a record to the store validates it first with `validate_record()`.
There is no exempt path.
