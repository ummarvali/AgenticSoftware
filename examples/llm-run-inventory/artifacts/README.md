# Generated Service

A single-tenant inventory management service exposing a versioned REST API for managing warehouses, products, stock levels, and adjustments. Stock quantities are tracked per warehouse/product pair with optimistic-locking based concurrency control, per-pair (or global default) low-stock thresholds, automatic alert generation on threshold breach, and a full audit trail of adjustments. The prototype runs as a single Python process using only the standard library (http.server for HTTP, sqlite3 for persistence), while the target production architecture is a horizontally scalable REST service backed by a relational database such as PostgreSQL.

## Tests

`python -m unittest discover -s tests`
