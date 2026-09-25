"""Unit tests for the core InventoryService business logic."""
import unittest

from inventory.db import Database
from inventory.events import EventBus, Metrics
from inventory.service import InventoryService, ValidationError, NotFoundError


class ServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.service = InventoryService(self.db, EventBus(), Metrics())
        self.wh = self.service.create_warehouse("Main", "City A")
        self.product = self.service.create_product("SKU-1", "Widget", "A widget")

    def test_add_stock_creates_item(self):
        result = self.service.add_stock(self.wh["id"], self.product["id"], 10)
        self.assertEqual(result["quantity"], 10)
        self.assertEqual(result["version"], 1)

    def test_add_stock_accumulates(self):
        self.service.add_stock(self.wh["id"], self.product["id"], 5)
        result = self.service.add_stock(self.wh["id"], self.product["id"], 7)
        self.assertEqual(result["quantity"], 12)
        self.assertEqual(result["version"], 2)

    def test_add_stock_rejects_non_positive_quantity(self):
        with self.assertRaises(ValidationError):
            self.service.add_stock(self.wh["id"], self.product["id"], 0)

    def test_adjust_stock_increment_and_decrement(self):
        self.service.add_stock(self.wh["id"], self.product["id"], 10)
        result = self.service.adjust_stock(self.wh["id"], self.product["id"], -3, actor="alice", reason="sale")
        self.assertEqual(result["quantity"], 7)
        result = self.service.adjust_stock(self.wh["id"], self.product["id"], 4, actor="bob", reason="restock")
        self.assertEqual(result["quantity"], 11)

    def test_adjust_stock_rejects_negative_result(self):
        self.service.add_stock(self.wh["id"], self.product["id"], 2)
        with self.assertRaises(ValidationError):
            self.service.adjust_stock(self.wh["id"], self.product["id"], -5)

    def test_adjust_stock_unknown_pair_raises_not_found(self):
        with self.assertRaises(NotFoundError):
            self.service.adjust_stock(self.wh["id"], self.product["id"], -1)

    def test_threshold_triggers_and_resolves_alert(self):
        self.service.set_threshold(self.wh["id"], self.product["id"], 5)
        self.service.add_stock(self.wh["id"], self.product["id"], 10)
        result = self.service.adjust_stock(self.wh["id"], self.product["id"], -8)
        self.assertTrue(result["alert_triggered"])
        alerts = self.service.list_alerts(status="ACTIVE")
        self.assertEqual(len(alerts), 1)
        self.service.resolve_alert(alerts[0]["id"])
        self.assertEqual(self.service.list_alerts(status="ACTIVE"), [])

        result = self.service.adjust_stock(self.wh["id"], self.product["id"], 20)
        self.assertFalse(result["alert_triggered"])

    def test_history_records_adjustments(self):
        self.service.add_stock(self.wh["id"], self.product["id"], 10, actor="alice", reason="intake")
        self.service.adjust_stock(self.wh["id"], self.product["id"], -2, actor="bob", reason="sale")
        history = self.service.get_history(self.wh["id"], self.product["id"])
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["delta"], -2)

    def test_default_threshold_fallback(self):
        self.service.set_default_threshold(3)
        self.service.add_stock(self.wh["id"], self.product["id"], 5)
        result = self.service.adjust_stock(self.wh["id"], self.product["id"], -3)
        self.assertTrue(result["alert_triggered"])


if __name__ == "__main__":
    unittest.main()
