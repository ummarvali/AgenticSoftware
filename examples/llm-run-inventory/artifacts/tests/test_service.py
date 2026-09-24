"""Unit tests for the inventory business logic layer."""
import unittest
from inventory import db
from inventory.service import InventoryService, ServiceError


class TestService(unittest.TestCase):
    def setUp(self):
        self.conn = db.get_conn(":memory:")
        db.init_db(self.conn)
        self.svc = InventoryService(self.conn)
        self.wh = self.svc.create_warehouse("Main", "NYC")
        self.pr = self.svc.create_product("Widget", "pcs")

    def test_add_and_get_stock(self):
        r = self.svc.add_stock(self.wh["id"], self.pr["id"], 100, threshold=20)
        self.assertEqual(r["quantity"], 100)
        got = self.svc.get_stock(self.wh["id"], self.pr["id"])
        self.assertEqual(got["quantity"], 100)

    def test_duplicate_stock_rejected(self):
        self.svc.add_stock(self.wh["id"], self.pr["id"], 10)
        with self.assertRaises(ServiceError):
            self.svc.add_stock(self.wh["id"], self.pr["id"], 10)

    def test_adjust_increment_decrement_and_alert(self):
        self.svc.add_stock(self.wh["id"], self.pr["id"], 50, threshold=10)
        r = self.svc.adjust_stock(self.wh["id"], self.pr["id"], "DECREMENT", 20, reason="sale")
        self.assertEqual(r["quantity_after"], 30)
        self.assertFalse(r["alert_triggered"])
        r2 = self.svc.adjust_stock(self.wh["id"], self.pr["id"], "DECREMENT", 25, reason="sale")
        self.assertEqual(r2["quantity_after"], 5)
        self.assertTrue(r2["alert_triggered"])
        alerts = self.svc.list_alerts()
        self.assertEqual(len(alerts), 1)

    def test_negative_stock_rejected(self):
        self.svc.add_stock(self.wh["id"], self.pr["id"], 10)
        with self.assertRaises(ServiceError):
            self.svc.adjust_stock(self.wh["id"], self.pr["id"], "DECREMENT", 20)

    def test_idempotency(self):
        self.svc.add_stock(self.wh["id"], self.pr["id"], 10)
        r1 = self.svc.adjust_stock(self.wh["id"], self.pr["id"], "INCREMENT", 5, idempotency_key="k1")
        r2 = self.svc.adjust_stock(self.wh["id"], self.pr["id"], "INCREMENT", 5, idempotency_key="k1")
        self.assertEqual(r1, r2)
        got = self.svc.get_stock(self.wh["id"], self.pr["id"])
        self.assertEqual(got["quantity"], 15)

    def test_audit_log_and_threshold(self):
        self.svc.add_stock(self.wh["id"], self.pr["id"], 10)
        self.svc.set_threshold(self.wh["id"], self.pr["id"], 5)
        self.svc.adjust_stock(self.wh["id"], self.pr["id"], "SET", 3, reason="correction")
        audit = self.svc.list_audit()
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]["quantity_after"], 3)
        alerts = self.svc.list_alerts()
        self.assertEqual(len(alerts), 1)


if __name__ == "__main__":
    unittest.main()
