# Generated Service

A REST-based Inventory Service that manages product stock levels across multiple warehouses. It supports creating products/warehouses, recording stock additions and adjustments (with audit trail), querying current stock by warehouse/product, and automatically raising alerts when quantities fall below configured thresholds. The system is built for consistency on writes (adjustments) and fast reads (queries), with asynchronous alert generation triggered by stock change events.

## Tests

`python -m unittest discover -s tests`
