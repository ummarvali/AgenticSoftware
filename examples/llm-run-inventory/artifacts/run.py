"""Entrypoint to start the inventory HTTP API server."""
import os

from inventory import db
from inventory.app import create_server

if __name__ == "__main__":
    db_path = os.environ.get("INVENTORY_DB", "inventory.db")
    db.init_db(db_path)
    port = int(os.environ.get("PORT", "8000"))
    server = create_server(db_path, port=port)
    print(f"Serving on port {port} (db={db_path})")
    server.serve_forever()
