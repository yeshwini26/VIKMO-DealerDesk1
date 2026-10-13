import pytest
from decimal import Decimal
from django.db import IntegrityError
from core.models import Product, Inventory, Dealer, Order, OrderItem, IdempotencyRecord


@pytest.mark.django_db
class TestModels:
    def test_product_creation(self):
        prod = Product.objects.create(
            sku="TST-1001",
            name="Test Brake Pad",
            category="Brakes",
            brand="Brembo",
            vehicle_fitment="Yamaha R15",
            price=Decimal("1250.00"),
            description="High performance brake pads"
        )
        assert prod.sku == "TST-1001"
        assert prod.stock == 0

        # Duplicate SKU must raise IntegrityError
        with pytest.raises(IntegrityError):
            Product.objects.create(
                sku="TST-1001",
                name="Duplicate Pad",
                category="Brakes",
                brand="Brembo",
                vehicle_fitment="Yamaha R15",
                price=Decimal("1250.00"),
            )

    def test_inventory_non_negative_constraint(self):
        prod = Product.objects.create(
            sku="TST-1002",
            name="Test Filter",
            category="Filters",
            brand="Bosch",
            vehicle_fitment="Universal",
            price=Decimal("450.00"),
        )
        inv = Inventory.objects.create(product=prod, quantity=10)
        assert prod.stock == 10

        # Negative quantity must fail
        with pytest.raises(IntegrityError):
            Inventory.objects.create(
                product=Product.objects.create(
                    sku="TST-1003",
                    name="Bad Filter",
                    category="Filters",
                    brand="Bosch",
                    vehicle_fitment="Universal",
                    price=Decimal("100.00"),
                ),
                quantity=-5
            )

    def test_dealer_creation(self):
        dealer = Dealer.objects.create(
            dealer_code="DLR-999",
            name="Apex Motors",
            city="Bengaluru",
            credit_limit=Decimal("50000.00")
        )
        assert dealer.dealer_code == "DLR-999"

        with pytest.raises(IntegrityError):
            Dealer.objects.create(
                dealer_code="DLR-999",
                name="Duplicate Motors"
            )

    def test_order_and_items(self):
        dealer = Dealer.objects.create(
            dealer_code="DLR-888",
            name="Speedway Spares"
        )
        prod = Product.objects.create(
            sku="TST-1004",
            name="Chain Lube",
            category="Lubricants",
            brand="Motul",
            vehicle_fitment="Universal",
            price=Decimal("350.00"),
        )
        order = Order.objects.create(
            order_number="ORD-20261012-9999",
            dealer=dealer,
            status="DRAFT",
            subtotal=Decimal("700.00"),
            tax_total=Decimal("126.00"),
            grand_total=Decimal("826.00")
        )
        item = OrderItem.objects.create(
            order=order,
            product=prod,
            sku=prod.sku,
            name=prod.name,
            quantity=2,
            unit_price=Decimal("350.00"),
            line_subtotal=Decimal("700.00"),
            line_tax=Decimal("126.00"),
            line_total=Decimal("826.00")
        )
        assert order.items.count() == 1
        assert item.line_total == Decimal("826.00")
