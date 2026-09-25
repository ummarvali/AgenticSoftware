# Generated Service

> Synthesized by the Repair agent from the design (the model's code bundle did not include a README).

A self-contained transaction validation service that accepts card transaction payloads via a synchronous JSON REST API and returns an approve/decline decision with reason codes. It applies deterministic validation rules (Luhn/format, expiry, CVV, amount limits) plus lightweight fraud heuristics (blacklist, velocity, suspicious amount patterns) using rules loaded from an external config file that can be hot-reloaded without redeploying. All sensitive data (PAN, CVV) is masked before logging or storage. The first runnable slice is built as a single Python process using only the standard library (http.server-based REST API, in-memory dict/queue structures for velocity and idempotency state, optional SQLite file for durable blacklist/audit persistence), designed so the same interfaces map cleanly onto the target Go microservice architecture.

## Endpoints served by this slice

- `POST /v1/validate` — Submit a card transaction for validation; returns decision and reason codes. Idempotent via Idempotency-Key header.
- `POST /v1/rules/reload` — Hot-reload validation rule configuration from disk without restarting the service
- `GET /v1/rules` — Return the currently active rule configuration (non-sensitive) for observability
- `POST /v1/blacklist` — Add a card token (or raw PAN, hashed server-side) to the blacklist store
- `GET /healthz` — Liveness probe: process is running
- `GET /readyz` — Readiness probe: rules config loaded and stores reachable
- `GET /metrics` — Prometheus-compatible text exposition of request counts, decision counts, latency histogram

Full contract: `openapi.yaml`.

## Run

`python -m cardvalidator.server` — see that module for the default host, port and storage path.

## Tests

`python -m unittest discover -s tests`

The engineering record for this run is `ENGINEERING_SUMMARY.md`.
