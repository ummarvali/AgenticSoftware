import unittest

from inventory.service import InventoryService, NotFoundError, ValidationError, ConflictError


class TestInventoryService(unittest.TestCase):
    def setUp(self):
        self.service = InventoryService()
        self.product = self.service.create_product("SKU1", "Widget")
        self.warehouse = self.service.create_warehouse("WH1", "Main Warehouse")

    def test_create_and_get_product(self):
        fetched = self.service.get_product(self.product.id)
        self.assertEqual(fetched.sku, "SKU1")

    def test_get_missing_product_raises(self):
        with self.assertRaises(NotFoundError):
            self.service.get_product(999)

    def test_create_stock_item_and_duplicate(self):
        item = self.service.create_stock_item(self.product.id, self.warehouse.id, 10)
        self.assertEqual(item.quantity, 10)
        with self.assertRaises(ConflictError):
            self.service.create_stock_item(self.product.id, self.warehouse.id, 5)

    def test_adjust_stock_negative_raises(self):
        self.service.create_stock_item(self.product.id, self.warehouse.id, 5)
        with self.assertRaises(ValidationError):
            self.service.adjust_stock(self.product.id, self.warehouse.id, -10)

    def test_threshold_alert_triggering_and_resolution(self):
        self.service.create_stock_item(self.product.id, self.warehouse.id, 10)
        self.service.set_threshold(self.product.id, self.warehouse.id, 5)
        adjustment, item, triggered = self.service.adjust_stock(
            self.product.id, self.warehouse.id, -8, reason="sale")
        self.assertEqual(item.quantity, 2)
        self.assertTrue(triggered)
        alerts, total = self.service.list_alerts()
        self.assertEqual(total, 1)
        self.assertEqual(alerts[0].status, "ACTIVE")

        adjustment2, item2, triggered2 = self.service.adjust_stock(
            self.product.id, self.warehouse.id, 10, reason="restock")
        self.assertFalse(triggered2)
        alerts2, total2 = self.service.list_alerts(status="RESOLVED")
        self.assertEqual(total2, 1)

    def test_resolve_alert(self):
        self.service.create_stock_item(self.product.id, self.warehouse.id, 10)
        self.service.set_threshold(self.product.id, self.warehouse.id, 5)
        self.service.adjust_stock(self.product.id, self.warehouse.id, -8)
        alerts, _ = self.service.list_alerts()
        alert = self.service.resolve_alert(alerts[0].id, resolved_by="admin")
        self.assertEqual(alert.status, "RESOLVED")
        self.assertEqual(alert.resolved_by, "admin")


if __name__ == "__main__":
    unittest.main()
