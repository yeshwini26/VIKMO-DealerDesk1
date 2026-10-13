from decimal import Decimal
from django.db import models
from django.db.models import CheckConstraint, Q


class Product(models.Model):
    """
    Catalog product.
    SKU is the unique human-readable part code (e.g. BRK-1044).
    Descriptions are untrusted supplier inputs.
    """
    sku = models.CharField(max_length=64, unique=True, db_index=True)
    name = models.CharField(max_length=255)
    category = models.CharField(max_length=100, db_index=True)
    brand = models.CharField(max_length=100, db_index=True)
    vehicle_fitment = models.CharField(max_length=255, db_index=True)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    description = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sku"]
        indexes = [
            models.Index(fields=["sku"]),
            models.Index(fields=["category"]),
            models.Index(fields=["brand"]),
        ]

    def __str__(self):
        return f"{self.sku} - {self.name}"

    @property
    def stock(self) -> int:
        if hasattr(self, "inventory"):
            return self.inventory.quantity
        return 0


class Inventory(models.Model):
    """
    Live stock quantity associated 1-to-1 with a product.
    Database check constraint enforces non-negative available quantity.
    """
    product = models.OneToOneField(
        Product,
        on_delete=models.CASCADE,
        related_name="inventory",
        primary_key=True
    )
    quantity = models.IntegerField(default=0)
    reason = models.CharField(max_length=255, blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            CheckConstraint(
                condition=Q(quantity__gte=0),
                name="inventory_quantity_non_negative"
            )
        ]

    def __str__(self):
        return f"{self.product.sku}: {self.quantity}"


class Dealer(models.Model):
    """
    Authorized dealer. Dealer code is unique (e.g. DLR-007).
    """
    dealer_code = models.CharField(max_length=64, unique=True, db_index=True)
    name = models.CharField(max_length=255)
    city = models.CharField(max_length=100, blank=True, default="")
    phone = models.CharField(max_length=50, blank=True, default="")
    email = models.CharField(max_length=255, blank=True, default="")
    gstin = models.CharField(max_length=50, blank=True, default="")
    credit_limit = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.dealer_code} - {self.name}"


class Order(models.Model):
    """
    Order with financial totals and status workflow.
    Foreign key to dealer uses PROTECT so dealer deletion does not orphan financial transactions.
    """
    STATUS_CHOICES = [
        ("DRAFT", "DRAFT"),
        ("CONFIRMED", "CONFIRMED"),
        ("DISPATCHED", "DISPATCHED"),
        ("DELIVERED", "DELIVERED"),
        ("CANCELLED", "CANCELLED"),
    ]

    order_number = models.CharField(max_length=64, unique=True, db_index=True)
    dealer = models.ForeignKey(Dealer, on_delete=models.PROTECT, related_name="orders")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="DRAFT", db_index=True)
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    tax_total = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    grand_total = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["order_number"]),
            models.Index(fields=["dealer", "status"]),
        ]

    def __str__(self):
        return f"{self.order_number} ({self.status})"


class OrderItem(models.Model):
    """
    Order line item.
    Foreign key to Order uses CASCADE (order lines belong entirely to the order).
    Product uses PROTECT to prevent deleting catalogue products that have historical order lines.
    SKU and name are denormalized to freeze what was purchased.
    """
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, null=True, blank=True, related_name="order_items")
    sku = models.CharField(max_length=64, db_index=True)
    name = models.CharField(max_length=255)
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    line_subtotal = models.DecimalField(max_digits=14, decimal_places=2)
    line_tax = models.DecimalField(max_digits=14, decimal_places=2)
    line_total = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        constraints = [
            CheckConstraint(
                condition=Q(quantity__gte=1),
                name="order_item_quantity_gte_1"
            ),
            models.UniqueConstraint(
                fields=["order", "sku"],
                name="unique_sku_per_order"
            )
        ]

    def __str__(self):
        return f"{self.order.order_number}: {self.sku} x {self.quantity}"


class IdempotencyRecord(models.Model):
    """
    Records Idempotency-Key and canonical SHA-256 fingerprint of requests
    to ensure exact idempotency semantics and prevent duplicate orders.
    """
    idempotency_key = models.CharField(max_length=255, unique=True, db_index=True)
    request_hash = models.CharField(max_length=64)
    status_code = models.IntegerField()
    response_body = models.JSONField()
    order = models.ForeignKey(Order, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.idempotency_key} ({self.status_code})"


class ConversationMessage(models.Model):
    """
    Database-backed conversation memory per session_id for the Dealer Assistant.
    """
    session_id = models.CharField(max_length=128, db_index=True)
    dealer = models.ForeignKey(Dealer, on_delete=models.CASCADE, null=True, blank=True)
    role = models.CharField(max_length=20)  # user, assistant, system, tool
    content = models.TextField()
    tool_calls = models.JSONField(default=list, blank=True)
    actions = models.JSONField(default=list, blank=True)
    cited_skus = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["session_id", "created_at"]),
        ]

    def __str__(self):
        return f"{self.session_id} [{self.role}]: {self.content[:30]}..."
