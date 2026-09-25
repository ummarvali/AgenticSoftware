"""stdlib HTTP API: validate, rules, blacklist, health, readiness, metrics."""
import json
import time
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import urlparse

from .rules import RulesConfig
from .store import Store
from .engine import ValidationEngine
from .masking import sanitize_pan, card_token as make_card_token


class Metrics:
    def __init__(self):
        self.lock = threading.Lock()
        self.requests = {}
        self.decisions = {}
        self.latency_sum = 0.0
        self.latency_count = 0

    def inc_request(self, endpoint):
        with self.lock:
            self.requests[endpoint] = self.requests.get(endpoint, 0) + 1

    def inc_decision(self, decision):
        with self.lock:
            self.decisions[decision] = self.decisions.get(decision, 0) + 1

    def observe_latency(self, ms):
        with self.lock:
            self.latency_sum += ms
            self.latency_count += 1

    def render(self):
        lines = []
        for ep, cnt in self.requests.items():
            lines.append('cardvalidator_requests_total{{endpoint="{}"}} {}'.format(ep, cnt))
        for d, cnt in self.decisions.items():
            lines.append('cardvalidator_decisions_total{{decision="{}"}} {}'.format(d, cnt))
        lines.append("cardvalidator_latency_ms_sum {}".format(self.latency_sum))
        lines.append("cardvalidator_latency_ms_count {}".format(self.latency_count))
        return "\n".join(lines) + "\n"


class RateLimiter:
    def __init__(self, capacity=20, refill_rate=10.0):
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.buckets = {}
        self.lock = threading.Lock()

    def allow(self, key):
        now = time.time()
        with self.lock:
            tokens, last = self.buckets.get(key, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - last) * self.refill_rate)
            if tokens < 1:
                self.buckets[key] = (tokens, now)
                return False
            self.buckets[key] = (tokens - 1, now)
            return True


class AppContext:
    def __init__(self, rules_path=None, db_path=":memory:"):
        self.rules_config = RulesConfig(rules_path)
        self.store = Store(db_path)
        self.engine = ValidationEngine(self.rules_config, self.store)
        self.metrics = Metrics()
        self.rate_limiter = RateLimiter()


def make_handler(ctx):
    class Handler(BaseHTTPRequestHandler):
        server_version = "CardValidator/1.0"

        def log_message(self, fmt, *args):
            pass

        def _send_json(self, code, payload):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self):
            length = int(self.headers.get("Content-Length", 0))
            if length == 0:
                return {}
            raw = self.rfile.read(length)
            return json.loads(raw.decode("utf-8"))

        def do_GET(self):
            path = urlparse(self.path).path
            ctx.metrics.inc_request(path)
            if path == "/healthz":
                self._send_json(200, {"status": "ok"})
            elif path == "/readyz":
                store_ok = ctx.store.is_ok()
                self._send_json(200, {"status": "ready" if store_ok else "not_ready",
                                       "rules_loaded": bool(ctx.rules_config.rules),
                                       "store_ok": store_ok})
            elif path == "/v1/rules":
                self._send_json(200, ctx.rules_config.get())
            elif path == "/metrics":
                body = ctx.metrics.render().encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self._send_json(404, {"error": "not_found"})

        def do_POST(self):
            path = urlparse(self.path).path
            ctx.metrics.inc_request(path)
            if not ctx.rate_limiter.allow(self.client_address[0]):
                self._send_json(429, {"error": "rate_limited"})
                return
            try:
                body = self._read_json()
            except (ValueError, json.JSONDecodeError):
                self._send_json(400, {"error": "invalid_json"})
                return
            if path == "/v1/validate":
                self._handle_validate(body)
            elif path == "/v1/rules/reload":
                version = ctx.rules_config.reload()
                self._send_json(200, {"status": "reloaded", "rules_version": version,
                                       "loaded_at": ctx.rules_config.loaded_at})
            elif path == "/v1/blacklist":
                self._handle_blacklist(body)
            else:
                self._send_json(404, {"error": "not_found"})

        def _handle_validate(self, body):
            txn_id = body.get("transaction_id")
            if txn_id:
                cached = ctx.store.get_idempotent(txn_id)
                if cached:
                    self._send_json(200, cached)
                    return
            result = ctx.engine.validate(body)
            ctx.metrics.inc_decision(result["decision"])
            ctx.metrics.observe_latency(result["latency_ms"])
            audit = {
                "transaction_id": result["transaction_id"],
                "bin": result["masked_card"]["bin"],
                "last4": result["masked_card"]["last4"],
                "network": result["masked_card"]["network"],
                "amount": body.get("amount"),
                "decision": result["decision"],
                "reason_codes": result["reason_codes"],
                "timestamp": result["evaluated_at"],
            }
            ctx.store.add_audit_log(audit)
            print(json.dumps({"event": "validation", **audit}))
            response = {
                "transaction_id": result["transaction_id"],
                "decision": result["decision"],
                "reason_codes": result["reason_codes"],
                "masked_card": result["masked_card"],
                "latency_ms": result["latency_ms"],
            }
            if txn_id:
                ctx.store.set_idempotent(txn_id, response)
            self._send_json(200, response)

        def _handle_blacklist(self, body):
            token = body.get("card_token")
            if not token:
                pan = sanitize_pan(body.get("card_number", ""))
                if pan:
                    token = make_card_token(pan)
            if not token:
                self._send_json(400, {"error": "card_token_or_card_number_required"})
                return
            reason = body.get("reason", "manual")
            added_at = ctx.store.add_blacklist(token, reason)
            self._send_json(200, {"card_token": token, "reason": reason, "added_at": added_at})

    return Handler


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def run_server(host="0.0.0.0", port=8080, rules_path=None, db_path=":memory:"):
    ctx = AppContext(rules_path, db_path)
    handler_cls = make_handler(ctx)
    httpd = ThreadingHTTPServer((host, port), handler_cls)
    return httpd, ctx


if __name__ == "__main__":
    import os
    p = int(os.environ.get("PORT", "8080"))
    server, _ = run_server(port=p)
    print("Serving on port", p)
    server.serve_forever()
