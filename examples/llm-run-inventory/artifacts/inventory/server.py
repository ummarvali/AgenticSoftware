"""Application wiring and entry point for the inventory HTTP service."""
from http.server import HTTPServer
from . import db
from .service import InventoryService
from .api import make_handler


def create_server(db_path=":memory:", host="127.0.0.1", port=8000):
    conn = db.get_conn(db_path)
    db.init_db(conn)
    service = InventoryService(conn)
    handler = make_handler(service)
    server = HTTPServer((host, port), handler)
    return server, service


def main():
    server, _ = create_server(db_path="inventory.db", port=8000)
    print("Inventory service listening on port 8000")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
