"""Runnable entry point: python -m urlshortener.server

Host, port and storage location are configurable via environment
variables (see config.py) or command-line arguments.
"""
import argparse
import logging
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer, make_server

from .app import Application
from .config import load_config
from .db import Database


class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


def build_server(config, app):
    return make_server(config.host, config.port, app, server_class=ThreadingWSGIServer)


def create_app(config):
    db = Database(config.db_path)
    db.seed_api_keys(config.api_keys)
    return Application(db, config)


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = load_config()

    parser = argparse.ArgumentParser(description="URL shortener service")
    parser.add_argument("--host", default=config.host)
    parser.add_argument("--port", type=int, default=config.port)
    parser.add_argument("--db-path", default=config.db_path)
    args = parser.parse_args(argv)

    config.host = args.host
    config.port = args.port
    config.db_path = args.db_path

    app = create_app(config)
    httpd = build_server(config, app)
    logging.info("urlshortener listening on %s:%s (db=%s)", config.host, config.port, config.db_path)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logging.info("shutting down")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
