import pytest
from decimal import Decimal
from rest_framework.test import APIClient
from core.models import Product, Inventory, Dealer, Order


@pytest.mark.django_db
class TestOrderAPIs:
    def setup_method(self):
        self.client = APIClient()
        self.dealer = Dealer.objects.create(
            dealer_code="DLR-ORD",
            name="Speed Spares",
            credit_limit=Decimal("500000.00")
        )
        self.p1 = Product.objects.create(
            sku="BRK-1044",
            name="Brake Pad Set - Bajaj Pulsar 150",
            category="Brakes",
            brand="Endurance",
            vehicle_fitment="Bajaj Pulsar 150",
            price=Decimal("1459.50")
        )
        self.inv1 = Inventory.objects.create(product=self.p1, quantity=50)

        self.p2 = Product.objects.create(
            sku="OIL-1001",
            name="Engine Oil 10W-40 1L",
            category="Lubricants",
            brand="Castrol",
            vehicle_fitment="Universal",
            price=Decimal("450.00")
        )
        self.inv2 = Inventory.objects.create(product=self.p2, quantity=10)

    def test_create_draft_order_and_money_calculations(self):
        # 3 units of BRK-1044:
        # unit_price = 1459.50
        # line_subtotal = 1459.50 * 3 = 4378.50
        # line_tax = 4378.50 * 0.18 = 788.13 (rounded half up)
        # line_total = 4378.50 + 788.13 = 5166.63
        payload = {
            "dealer_id": self.dealer.id,
            "items": [
                {"sku": "BRK-1044", "quantity": 3}
            ]
        }
        res = self.client.post("/api/orders/", payload, format="json")
        assert res.status_code == 201
        data = res.json()
        assert data["status"] == "DRAFT"
        assert data["order_number"].startswith("ORD-")
        assert data["subtotal"] == "4378.50"
        assert data["tax_total"] == "788.13"
        assert data["grand_total"] == "5166.63"

        item = data["items"][0]
        assert item["sku"] == "BRK-1044"
        assert item["quantity"] == 3
        assert item["unit_price"] == "1459.50"
        assert item["line_subtotal"] == "4378.50"
        assert item["line_tax"] == "788.13"
        assert item["line_total"] == "5166.63"

        # DRAFT has NO effect on inventory
        self.inv1.refresh_from_db()
        assert self.inv1.quantity == 50

    def test_order_validation_errors(self):
        # Empty items list
        res = self.client.post("/api/orders/", {"dealer_id": self.dealer.id, "items": []}, format="json")
        assert res.status_code == 400
        assert res.json()["error"]["code"] == "VALIDATION_ERROR"

        # Unknown dealer
        res = self.client.post("/api/orders/", {"dealer_id": 99999, "items": [{"sku": "BRK-1044", "quantity": 1}]}, format="json")
        assert res.status_code == 400
        assert res.json()["error"]["code"] == "VALIDATION_ERROR"

        # Unknown SKU
        res = self.client.post("/api/orders/", {"dealer_id": self.dealer.id, "items": [{"sku": "NOPE-0000", "quantity": 1}]}, format="json")
        assert res.status_code == 400
        assert res.json()["error"]["code"] == "VALIDATION_ERROR"

        # Duplicate SKU in request
        res = self.client.post("/api/orders/", {
            "dealer_id": self.dealer.id,
            "items": [
                {"sku": "BRK-1044", "quantity": 1},
                {"sku": "BRK-1044", "quantity": 2}
            ]
        }, format="json")
        assert res.status_code == 400
        assert res.json()["error"]["code"] == "VALIDATION_ERROR"

        # Invalid quantity (< 1)
        res = self.client.post("/api/orders/", {"dealer_id": self.dealer.id, "items": [{"sku": "BRK-1044", "quantity": 0}]}, format="json")
        assert res.status_code == 400
        assert res.json()["error"]["code"] == "VALIDATION_ERROR"

    def test_idempotency_key(self):
        payload = {
            "dealer_id": self.dealer.id,
            "items": [{"sku": "BRK-1044", "quantity": 2}]
        }
        headers = {"HTTP_IDEMPOTENCY_KEY": "test-key-12345"}

        # First request
        res1 = self.client.post("/api/orders/", payload, format="json", **headers)
        assert res1.status_code == 201
        order_num = res1.json()["order_number"]

        # Duplicate request with same body returns the same order without creating a new one
        res2 = self.client.post("/api/orders/", payload, format="json", **headers)
        assert res2.status_code in (200, 201)
        assert res2.json()["order_number"] == order_num

        # Same key with different body -> 409 IDEMPOTENCY_CONFLICT
        different_payload = {
            "dealer_id": self.dealer.id,
            "items": [{"sku": "BRK-1044", "quantity": 5}]
        }
        res3 = self.client.post("/api/orders/", different_payload, format="json", **headers)
        assert res3.status_code == 409
        assert res3.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

    def test_order_lifecycle_confirm_cancel(self):
        # Create order with 10 units
        res = self.client.post("/api/orders/", {
            "dealer_id": self.dealer.id,
            "items": [{"sku": "BRK-1044", "quantity": 10}]
        }, format="json")
        assert res.status_code == 201
        order_no = res.json()["order_number"]

        # Confirm order: deducts stock from 50 to 40
        res_conf = self.client.post(f"/api/orders/{order_no}/confirm/")
        assert res_conf.status_code == 200
        assert res_conf.json()["status"] == "CONFIRMED"
        self.inv1.refresh_from_db()
        assert self.inv1.quantity == 40

        # Cancel confirmed order: restores stock back to 50 exactly once
        res_cancel = self.client.post(f"/api/orders/{order_no}/cancel/")
        assert res_cancel.status_code == 200
        assert res_cancel.json()["status"] == "CANCELLED"
        self.inv1.refresh_from_db()
        assert self.inv1.quantity == 50

        # Invalid transition: confirm from CANCELLED -> 409 INVALID_TRANSITION
        res_inv = self.client.post(f"/api/orders/{order_no}/confirm/")
        assert res_inv.status_code == 409
        assert res_inv.json()["error"]["code"] == "INVALID_TRANSITION"

    def test_insufficient_stock_on_confirm(self):
        # Create order requesting 100 units when only 50 exist
        res = self.client.post("/api/orders/", {
            "dealer_id": self.dealer.id,
            "items": [{"sku": "BRK-1044", "quantity": 100}]
        }, format="json")
        assert res.status_code == 201
        order_no = res.json()["order_number"]

        res_conf = self.client.post(f"/api/orders/{order_no}/confirm/")
        assert res_conf.status_code == 409
        assert res_conf.json()["error"]["code"] == "INSUFFICIENT_STOCK"
        assert len(res_conf.json()["error"]["details"]) == 1
        assert res_conf.json()["error"]["details"][0]["sku"] == "BRK-1044"
        assert res_conf.json()["error"]["details"][0]["available"] == 50
        assert res_conf.json()["error"]["details"][0]["requested"] == 100
        # Stock untouched
        self.inv1.refresh_from_db()
        assert self.inv1.quantity == 50
