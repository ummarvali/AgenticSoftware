"""Entry point to run the inventory API with wsgiref."""
import sys
from wsgiref.simple_server import make_server

from inventory.api import InventoryAPI


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    app = InventoryAPI()
    server = make_server("0.0.0.0", port, app)
    print(f"Serving on port {port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
