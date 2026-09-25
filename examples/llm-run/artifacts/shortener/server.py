"""Runnable entry point: python -m shortener.server

Host, port, database path and public base URL are configurable via
environment variables: SHORTENER_HOST, SHORTENER_PORT, SHORTENER_DB_PATH,
SHORTENER_BASE_URL, RATE_LIMIT_CAPACITY, RATE_LIMIT_REFILL.
"""
import os
import signal
import threading
from http.server import ThreadingHTTPServer

from .storage import Database
from .analytics import AnalyticsQueue, analytics_worker, scheduler_worker
from .auth import RateLimiter
from .service import URLShortenerService
from .handler import make_handler


def build_server(host, port, db_path, base_url):
    """Construct all components and an HTTP server bound to (host, port).

    Returns (httpd, stop_event, background_threads, db, service, analytics_queue).
    Background worker threads are started but the HTTP server is not yet serving;
    call httpd.serve_forever() (typically in a separate thread for tests).
    """
    db = Database(db_path)
    queue = AnalyticsQueue()
    service = URLShortenerService(db, queue, base_url=base_url)
    rate_limiter = RateLimiter(
        capacity=int(os.environ.get("RATE_LIMIT_CAPACITY", 50)),
        refill_rate=float(os.environ.get("RATE_LIMIT_REFILL", 20)),
    )
    stop_event = threading.Event()
    worker = threading.Thread(target=analytics_worker, args=(db, queue, stop_event), daemon=True)
    scheduler = threading.Thread(target=scheduler_worker, args=(db, stop_event), daemon=True)
    worker.start()
    scheduler.start()
    handler_cls = make_handler(service, db, rate_limiter)
    httpd = ThreadingHTTPServer((host, port), handler_cls)
    return httpd, stop_event, (worker, scheduler), db, service, queue


def main():
    host = os.environ.get("SHORTENER_HOST", "0.0.0.0")
    port = int(os.environ.get("SHORTENER_PORT", "8080"))
    db_path = os.environ.get("SHORTENER_DB_PATH", "shortener.db")
    base_url = os.environ.get("SHORTENER_BASE_URL", f"http://{host}:{port}")

    httpd, stop_event, threads, db, service, queue = build_server(host, port, db_path, base_url)

    def shutdown(*_args):
        stop_event.set()
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    try:
        signal.signal(signal.SIGINT, shutdown)
        signal.signal(signal.SIGTERM, shutdown)
    except ValueError:
        pass  # signals unavailable (e.g. non-main thread); rely on Ctrl+C only

    print(f"shortener listening on {host}:{port}, db={db_path}, base_url={base_url}")
    try:
        httpd.serve_forever()
    finally:
        stop_event.set()
        httpd.server_close()


if __name__ == "__main__":
    main()
