# Generated Service

A rules-based card transaction validation service exposing a single synchronous REST endpoint that checks format (Luhn, expiry, CVV, amount, currency) and configurable business rules (per-card daily/monthly limits, blacklist), returning approved/rejected/flagged with reasons. The service is designed to be stateless and horizontally scalable, with idempotent request handling and PCI-aware logging (no raw PAN/CVV persisted or logged). The production target is a Go microservice; the first runnable slice implements the same contract and rule engine in Python using only the standard library, with SQLite used strictly as a local cache for idempotency and velocity-limit bookkeeping (not as a transaction ledger).

## Tests

`python -m unittest discover -s tests`
