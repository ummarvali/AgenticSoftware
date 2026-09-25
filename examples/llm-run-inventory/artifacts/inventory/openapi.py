"""Static OpenAPI 3.0 document."""

OPENAPI_DOC = {
    "openapi": "3.0.0",
    "info": {"title": "Inventory Management API", "version": "1.0.0"},
    "paths": {
        "/items": {"get": {"summary": "List items"}, "post": {"summary": "Create item"}},
        "/items/{item_id}": {"get": {"summary": "Get item"}},
        "/warehouses": {"get": {"summary": "List warehouses"}, "post": {"summary": "Create warehouse"}},
        "/stock/add": {"post": {"summary": "Add stock"}},
        "/stock/adjust": {"post": {"summary": "Adjust stock"}},
        "/stock/{item_id}/{warehouse_id}": {"get": {"summary": "Get stock for item/warehouse"}},
        "/stock/{item_id}": {"get": {"summary": "Get aggregated stock for item"}},
        "/stock": {"get": {"summary": "List stock records"}},
        "/stock/{item_id}/{warehouse_id}/threshold": {"put": {"summary": "Set threshold"}},
        "/alerts/low-stock": {"get": {"summary": "List open low-stock alerts"}},
        "/alerts": {"get": {"summary": "List all alerts"}},
        "/audit/adjustments": {"get": {"summary": "List audit records"}},
    },
}
