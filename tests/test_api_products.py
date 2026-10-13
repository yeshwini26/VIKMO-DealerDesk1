import pytest
from decimal import Decimal
from rest_framework.test import APIClient
from core.models import Product, Inventory


@pytest.mark.django_db
class TestProductAndInventoryAPIs:
    def setup_method(self):
        self.client = APIClient()

    def test_products_pagination_and_filters(self):
        # Create test products
        p1 = Product.objects.create(
            sku="PRD-001",
            name="Disc Brake Pad Front",
            category="Brakes",
            brand="Brembo",
            vehicle_fitment="Bajaj Pulsar 150",
            price=Decimal("850.00")
        )
        Inventory.objects.create(product=p1, quantity=15)

        p2 = Product.objects.create(
            sku="PRD-002",
            name="Air Filter",
            category="Filters",
            brand="Bosch",
            vehicle_fitment="KTM Duke 390",
            price=Decimal("450.00")
        )
        Inventory.objects.create(product=p2, quantity=8)

        # 1. Standard pagination check
        res = self.client.get("/api/products/?page_size=1")
        assert res.status_code == 200
        data = res.json()
        assert "count" in data
        assert "next" in data
        assert "previous" in data
        assert "results" in data
        assert len(data["results"]) == 1

        # 2. Search filter
        res = self.client.get("/api/products/?search=Brembo")
        assert res.status_code == 200
        assert res.json()["count"] == 1
        assert res.json()["results"][0]["sku"] == "PRD-001"

        # 3. Category filter
        res = self.client.get("/api/products/?category=Filters")
        assert res.status_code == 200
        assert res.json()["count"] == 1
        assert res.json()["results"][0]["sku"] == "PRD-002"

        # 4. Vehicle filter
        res = self.client.get("/api/products/?vehicle=pulsar")
        assert res.status_code == 200
        assert res.json()["count"] == 1
        assert res.json()["results"][0]["sku"] == "PRD-001"

    def test_product_detail_and_404(self):
        p = Product.objects.create(
            sku="PRD-003",
            name="Chain Cleaner",
            category="Lubricants",
            brand="Motul",
            vehicle_fitment="Universal",
            price=Decimal("499.00")
        )
        Inventory.objects.create(product=p, quantity=25)

        # Found
        res = self.client.get("/api/products/PRD-003/")
        assert res.status_code == 200
        assert res.json()["sku"] == "PRD-003"
        assert res.json()["price"] == "499.00"
        assert res.json()["stock"] == 25

        # Not found
        res = self.client.get("/api/products/NON-EXISTENT/")
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "NOT_FOUND"

    def test_product_create_and_duplicate(self):
        payload = {
            "sku": "PRD-NEW",
            "name": "Spark Plug",
            "category": "Electrical",
            "brand": "NGK",
            "vehicle_fitment": "Yamaha FZ",
            "price": "220.00",
            "description": "High performance spark plug"
        }
        res = self.client.post("/api/products/", payload, format="json")
        assert res.status_code == 201
        data = res.json()
        assert data["sku"] == "PRD-NEW"
        assert data["stock"] == 0

        # Duplicate SKU must return 409 DUPLICATE
        res2 = self.client.post("/api/products/", payload, format="json")
        assert res2.status_code == 409
        assert res2.json()["error"]["code"] == "DUPLICATE"

    def test_product_patch(self):
        p = Product.objects.create(
            sku="PRD-PATCH",
            name="Old Name",
            category="Brakes",
            brand="OEM",
            vehicle_fitment="Universal",
            price=Decimal("100.00")
        )
        Inventory.objects.create(product=p, quantity=5)

        res = self.client.patch("/api/products/PRD-PATCH/", {"price": "150.50", "name": "New Name"}, format="json")
        assert res.status_code == 200
        assert res.json()["price"] == "150.50"
        assert res.json()["name"] == "New Name"

        # Attempt to change SKU must fail
        res2 = self.client.patch("/api/products/PRD-PATCH/", {"sku": "CHANGED"}, format="json")
        assert res2.status_code == 400
        assert res2.json()["error"]["code"] == "VALIDATION_ERROR"

    def test_inventory_update(self):
        p = Product.objects.create(
            sku="PRD-INV",
            name="Horn Set",
            category="Electrical",
            brand="Roots",
            vehicle_fitment="Universal",
            price=Decimal("600.00")
        )
        Inventory.objects.create(product=p, quantity=10)

        res = self.client.put("/api/inventory/PRD-INV/", {"quantity": 42, "reason": "stock take"}, format="json")
        assert res.status_code == 200
        assert res.json() == {"sku": "PRD-INV", "quantity": 42}

        # Check product detail reflects it
        res_prod = self.client.get("/api/products/PRD-INV/")
        assert res_prod.json()["stock"] == 42

        # Negative quantity must return 400
        res_neg = self.client.put("/api/inventory/PRD-INV/", {"quantity": -5}, format="json")
        assert res_neg.status_code == 400
        assert res_neg.json()["error"]["code"] == "VALIDATION_ERROR"
