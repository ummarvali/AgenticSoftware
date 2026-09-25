"""Tests for the performance-optimization toolkit: service logic and HTTP API."""

import json
import threading
import unittest
import urllib.error
import urllib.request

from perf_toolkit.api import build_server
from perf_toolkit.service import NotFoundError, PerfToolkitService
from perf_toolkit.workload import DemoWorkload


class PerfToolkitServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = PerfToolkitService(workload=DemoWorkload())

    def test_create_and_get_run(self):
        run = self.service.create_run(iterations=5)
        self.assertIn("id", run)
        self.assertEqual(run["iterations"], 5)
        fetched = self.service.get_run(run["id"])
        self.assertEqual(fetched["id"], run["id"])
        self.assertIsInstance(fetched["bottlenecks"], list)
        self.assertGreater(len(fetched["bottlenecks"]), 0)

    def test_get_missing_run_raises(self):
        with self.assertRaises(NotFoundError):
            self.service.get_run(9999)

    def test_bottlenecks_endpoint_data(self):
        run = self.service.create_run(iterations=5)
        bottlenecks = self.service.get_bottlenecks(run["id"])
        self.assertEqual(bottlenecks, run["bottlenecks"])

    def test_list_optimizations(self):
        opts = self.service.list_optimizations()
        self.assertTrue(any(o["id"] == "sqlite-batched-commits" for o in opts))
        self.assertFalse(any(o["applied"] for o in opts))

    def test_apply_and_rollback_optimization(self):
        result = self.service.apply_optimization("sqlite-batched-commits")
        self.assertEqual(result["status"], "applied")
        self.assertFalse(self.service.workload.store.autocommit)
        opts = self.service.list_optimizations()
        applied_opt = next(o for o in opts if o["id"] == "sqlite-batched-commits")
        self.assertTrue(applied_opt["applied"])

        rollback_result = self.service.rollback_optimization("sqlite-batched-commits")
        self.assertEqual(rollback_result["status"], "rolled_back")
        self.assertTrue(self.service.workload.store.autocommit)
        opts = self.service.list_optimizations()
        applied_opt = next(o for o in opts if o["id"] == "sqlite-batched-commits")
        self.assertFalse(applied_opt["applied"])

    def test_apply_unknown_optimization_raises(self):
        with self.assertRaises(NotFoundError):
            self.service.apply_optimization("does-not-exist")

    def test_rollback_unknown_optimization_raises(self):
        with self.assertRaises(NotFoundError):
            self.service.rollback_optimization("does-not-exist")

    def test_comparisons_recorded(self):
        self.service.apply_optimization("sqlite-batched-commits")
        comparisons = self.service.list_comparisons()
        self.assertGreaterEqual(len(comparisons), 1)
        last = comparisons[-1]
        self.assertEqual(last["optimization_id"], "sqlite-batched-commits")
        self.assertIn("improvement_pct", last)

    def test_regression_tests_pass(self):
        result = self.service.run_regression_tests()
        self.assertTrue(result["passed"])
        self.assertTrue(all(c["passed"] for c in result["checks"]))

    def test_regression_tests_pass_after_optimization_cycle(self):
        self.service.apply_optimization("sqlite-batched-commits")
        self.service.rollback_optimization("sqlite-batched-commits")
        result = self.service.run_regression_tests()
        self.assertTrue(result["passed"])


class PerfToolkitApiTests(unittest.TestCase):
    def setUp(self):
        self.service = PerfToolkitService(workload=DemoWorkload())
        self.server = build_server("127.0.0.1", 0, self.service)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def _request(self, method, path, body=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_full_flow(self):
        status, run = self._request("POST", "/runs", {"iterations": 5})
        self.assertEqual(status, 201)
        run_id = run["id"]

        status, fetched = self._request("GET", f"/runs/{run_id}")
        self.assertEqual(status, 200)
        self.assertEqual(fetched["id"], run_id)

        status, bottlenecks = self._request("GET", f"/runs/{run_id}/bottlenecks")
        self.assertEqual(status, 200)
        self.assertIsInstance(bottlenecks, list)

        status, opts = self._request("GET", "/optimizations")
        self.assertEqual(status, 200)
        self.assertTrue(any(o["id"] == "sqlite-batched-commits" for o in opts))

        status, applied = self._request("POST", "/optimizations", {"id": "sqlite-batched-commits"})
        self.assertEqual(status, 200)
        self.assertEqual(applied["status"], "applied")

        status, rolled_back = self._request(
            "POST", "/optimizations/sqlite-batched-commits/rollback"
        )
        self.assertEqual(status, 200)
        self.assertEqual(rolled_back["status"], "rolled_back")

        status, regression = self._request("POST", "/regression-tests")
        self.assertEqual(status, 200)
        self.assertTrue(regression["passed"])

        status, comparisons = self._request("GET", "/comparisons")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(len(comparisons), 2)

    def test_unknown_run_returns_404(self):
        status, _ = self._request("GET", "/runs/9999")
        self.assertEqual(status, 404)

    def test_unknown_route_returns_404(self):
        status, _ = self._request("GET", "/nope")
        self.assertEqual(status, 404)

    def test_missing_optimization_id_returns_400(self):
        status, _ = self._request("POST", "/optimizations", {})
        self.assertEqual(status, 400)

    def test_unknown_optimization_returns_404(self):
        status, _ = self._request("POST", "/optimizations", {"id": "nope"})
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
