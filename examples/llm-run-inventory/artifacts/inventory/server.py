"""Runnable entry point: python -m inventory.server

Configuration via environment variables:
  INVENTORY_HOST     - bind host (default 0.0.0.0)
  INVENTORY_PORT      - bind port (default 8000)
  INVENTORY_DB_PATH   - sqlite file path or ':memory:' (default inventory.db)
"""
import logging
import os
from http.server import ThreadingHTTPServer

from .api import InventoryRequestHandler
from .db import Database
from .events import EventBus, Metrics
from .service import InventoryService


def build_server(host, port, db_path):
    db = Database(db_path)
    metrics = Metrics()
    event_bus = EventBus()
    service = InventoryService(db, event_bus, metrics)
    httpd = ThreadingHTTPServer((host, port), InventoryRequestHandler)
    httpd.db = db
    httpd.service = service
    httpd.metrics = metrics
    httpd.event_bus = event_bus
    return httpd


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    host = os.environ.get("INVENTORY_HOST", "0.0.0.0")
    port = int(os.environ.get("INVENTORY_PORT", "8000"))
    db_path = os.environ.get("INVENTORY_DB_PATH", "inventory.db")
    httpd = build_server(host, port, db_path)
    logging.getLogger("inventory").info(
        "Inventory service listening on %s:%s (db=%s)", host, port, db_path
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
