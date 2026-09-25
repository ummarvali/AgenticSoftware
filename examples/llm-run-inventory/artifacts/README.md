# Inventory Service

A single-process REST inventory service (Python standard library only)
that tracks stock quantities per product/warehouse, supports stock
intake and adjustments with optimistic concurrency, configurable
low-stock thresholds, alert generation, and a full audit trail.
Persistence is SQLite (WAL mode); an in-memory pub/sub bus stands in
for a future Kafka/SNS alert topic.

## Features

- Warehouse and product/SKU CRUD (minimal reference data)
- Add stock / adjust stock (increment or decrement) with optimistic
  concurrency (version column) and validation (no negative stock)
- Query stock per product+warehouse, per product across warehouses,
  and per warehouse across products
- Per product/warehouse thresholds with an optional global default
- Automatic low-stock alert creation/resolution, queryable via API and
  published to an in-memory event bus
- Immutable audit trail of every stock adjustment (who/when/why/delta)
- Idempotency-Key support on `/stock/adjust` for safe client retries
- API-key based auth with READ / WRITE / ADMIN roles
- `/metrics` endpoint with simple in-memory counters

## Running

```
python -m inventory.server
```

Environment variables (all optional):

| Variable            | Default          | Meaning                          |
|---------------------|------------------|-----------------------------------|
| INVENTORY_HOST      | 0.0.0.0          | bind host                        |
| INVENTORY_PORT      | 8000             | bind port                        |
| INVENTORY_DB_PATH   | inventory.db     | sqlite file path or `:memory:`   |

On first start the service seeds three demo API keys (for local use):

- `demo-read-key`  -> role `READ`
- `demo-write-key` -> role `WRITE`
- `demo-admin-key` -> role `ADMIN` (read + write)

Pass the key via `X-Api-Key: <key>` header or `Authorization: Bearer <key>`.

## Tests

```
python -m unittest discover -s tests -v
```

Includes unit tests for `InventoryService` business logic and
integration tests that start the real HTTP server on an ephemeral
localhost port and drive every endpoint end to end.

## curl examples

Create a warehouse:
```
curl -s -X POST http://localhost:8000/warehouses \
  -H "X-Api-Key: demo-write-key" -H "Content-Type: application/json" \
  -d '{"name":"Central","location":"NYC"}'
```

List warehouses:
```
curl -s http://localhost:8000/warehouses -H "X-Api-Key: demo-read-key"
```

Get a warehouse:
```
curl -s http://localhost:8000/warehouses/<warehouse_id> -H "X-Api-Key: demo-read-key"
```

Create a product:
```
curl -s -X POST http://localhost:8000/products \
  -H "X-Api-Key: demo-write-key" -H "Content-Type: application/json" \
  -d '{"sku":"SKU-100","name":"Gadget","description":"A gadget"}'
```

List products:
```
curl -s http://localhost:8000/products -H "X-Api-Key: demo-read-key"
```

Get a product:
```
curl -s http://localhost:8000/products/<product_id> -H "X-Api-Key: demo-read-key"
```

Add stock:
```
curl -s -X POST http://localhost:8000/stock \
  -H "X-Api-Key: demo-write-key" -H "Content-Type: application/json" \
  -d '{"warehouse_id":"<wid>","product_id":"<pid>","quantity":10}'
```

Adjust stock (idempotent retry-safe):
```
curl -s -X POST http://localhost:8000/stock/adjust \
  -H "X-Api-Key: demo-write-key" -H "Content-Type: application/json" \
  -H "Idempotency-Key: order-123" \
  -d '{"warehouse_id":"<wid>","product_id":"<pid>","delta":-3,"reason":"sale"}'
```

Get stock for a product at a warehouse:
```
curl -s http://localhost:8000/stock/<wid>/<pid> -H "X-Api-Key: demo-read-key"
```

Get stock for a product across warehouses:
```
curl -s http://localhost:8000/products/<pid>/stock -H "X-Api-Key: demo-read-key"
```

Get all stock in a warehouse:
```
curl -s http://localhost:8000/warehouses/<wid>/stock -H "X-Api-Key: demo-read-key"
```

Set a per product/warehouse threshold:
```
curl -s -X PUT http://localhost:8000/thresholds/<wid>/<pid> \
  -H "X-Api-Key: demo-write-key" -H "Content-Type: application/json" \
  -d '{"threshold":5}'
```

Set the global default threshold:
```
curl -s -X PUT http://localhost:8000/thresholds/default \
  -H "X-Api-Key: demo-write-key" -H "Content-Type: application/json" \
  -d '{"default_threshold":2}'
```

List active alerts:
```
curl -s "http://localhost:8000/alerts?status=ACTIVE" -H "X-Api-Key: demo-read-key"
```

Resolve an alert:
```
curl -s -X POST http://localhost:8000/alerts/<alert_id>/resolve -H "X-Api-Key: demo-write-key"
```

Get stock adjustment history:
```
curl -s http://localhost:8000/stock/<wid>/<pid>/history -H "X-Api-Key: demo-read-key"
```

Metrics:
```
curl -s http://localhost:8000/metrics
```
