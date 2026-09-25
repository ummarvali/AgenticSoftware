# Generated Service

> Synthesized by the Repair agent from the design (the model's code bundle did not include a README).

A single-process inventory management backend exposing REST APIs for managing items, warehouses, stock levels, adjustments, thresholds, and low-stock alerts. The prototype is built with Python's standard library only (http.server for HTTP, sqlite3 for persistence), using SQLite transactions with explicit row locking semantics (BEGIN IMMEDIATE + application-level locking) to guarantee atomic stock adjustments. Alerts are generated synchronously when a stock adjustment crosses the configured threshold and are recorded in an in-process alert log/table, retrievable via API and dispatched to a pluggable notifier (console/log stub) mimicking a queue publish for future evolution to Kafka/SNS. API-key based auth guards all write endpoints; idempotency is enforced via a client-supplied idempotency key stored per adjustment. The target production architecture is PostgreSQL + Kafka/SNS + JWT/API-key auth as specified in requirements; this slice implements the same contracts against SQLite/in-memory equivalents.

## Endpoints served by this slice

- `POST /items` — Register a new inventory item
- `GET /items` — List items with pagination/filtering by sku/name
- `GET /items/{item_id}` — Get item details
- `POST /warehouses` — Register a new warehouse
- `GET /warehouses` — List all warehouses with inventory summary (total qty, distinct items, low-stock count)
- `POST /stock/add` — Add stock quantity for item in a warehouse (creates stock row if absent)
- `POST /stock/adjust` — Adjust stock (positive or negative delta) with audit trail and idempotency
- `GET /stock/{item_id}/{warehouse_id}` — Query current stock level for an item in a specific warehouse
- `GET /stock/{item_id}` — Query aggregated stock levels across all warehouses for an item
- `GET /stock` — List stock records with pagination and filtering by item_id/warehouse_id/low_stock_only
- `PUT /stock/{item_id}/{warehouse_id}/threshold` — Configure low-stock threshold for an item/warehouse pair
- `GET /alerts/low-stock` — List current low-stock conditions (stock below threshold) across items/warehouses
- `GET /alerts` — List historical triggered alerts with pagination/filtering by status/date
- `GET /audit/adjustments` — Query audit history of stock adjustments with pagination/filtering

Full contract: `openapi.yaml`.

## Run

`python run.py` — see that file for the default host, port and storage path.

## Tests

`python -m unittest discover -s tests`

The engineering record for this run is `ENGINEERING_SUMMARY.md`.
