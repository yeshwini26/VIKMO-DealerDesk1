import re
from decimal import Decimal
from rest_framework import serializers
from core.models import Product, Dealer, Order, OrderItem
from core.exceptions import DuplicateError, CustomAPIException

MONEY_REGEX = re.compile(r"^\d+(\.\d{1,2})?$")


class ProductSerializer(serializers.ModelSerializer):
    price = serializers.SerializerMethodField()
    stock = serializers.IntegerField(read_only=True)

    class Meta:
        model = Product
        fields = [
            "sku",
            "name",
            "category",
            "brand",
            "vehicle_fitment",
            "price",
            "stock",
            "description",
        ]

    def get_price(self, obj) -> str:
        return f"{obj.price:.2f}"

    def validate_price(self, value):
        if value <= Decimal("0.00"):
            raise serializers.ValidationError("Price must be greater than 0.")
        return value


class ProductCreateSerializer(serializers.ModelSerializer):
    price = serializers.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        model = Product
        fields = [
            "sku",
            "name",
            "category",
            "brand",
            "vehicle_fitment",
            "price",
            "description",
        ]
        extra_kwargs = {
            "sku": {"validators": []}
        }

    def validate_sku(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError("SKU cannot be empty.")
        if Product.objects.filter(sku=value.strip()).exists():
            raise DuplicateError(f"Product with SKU '{value}' already exists.")
        return value.strip()

    def validate_price(self, value):
        if value <= Decimal("0.00"):
            raise serializers.ValidationError("Price must be greater than 0.")
        # Check decimal places
        if value.as_tuple().exponent < -2:
            raise serializers.ValidationError("Price must have at most 2 decimal places.")
        return value


class ProductUpdateSerializer(serializers.ModelSerializer):
    price = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)

    class Meta:
        model = Product
        fields = [
            "name",
            "category",
            "brand",
            "vehicle_fitment",
            "price",
            "description",
        ]

    def validate(self, attrs):
        if "sku" in self.initial_data:
            raise CustomAPIException(
                code="VALIDATION_ERROR",
                message="SKU cannot be changed via PATCH.",
                details=["sku is immutable"]
            )
        return attrs

    def validate_price(self, value):
        if value <= Decimal("0.00"):
            raise serializers.ValidationError("Price must be greater than 0.")
        if value.as_tuple().exponent < -2:
            raise serializers.ValidationError("Price must have at most 2 decimal places.")
        return value


class DealerSerializer(serializers.ModelSerializer):
    credit_limit = serializers.SerializerMethodField()

    class Meta:
        model = Dealer
        fields = [
            "id",
            "dealer_code",
            "name",
            "city",
            "phone",
            "email",
            "gstin",
            "credit_limit",
        ]

    def get_credit_limit(self, obj) -> str:
        return f"{obj.credit_limit:.2f}"


class DealerCreateSerializer(serializers.ModelSerializer):
    credit_limit = serializers.DecimalField(max_digits=14, decimal_places=2, required=False, default=Decimal("0.00"))

    class Meta:
        model = Dealer
        fields = [
            "dealer_code",
            "name",
            "city",
            "phone",
            "email",
            "gstin",
            "credit_limit",
        ]
        extra_kwargs = {
            "dealer_code": {"validators": []}
        }

    def validate_dealer_code(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError("dealer_code is required.")
        if Dealer.objects.filter(dealer_code=value.strip()).exists():
            raise DuplicateError(f"Dealer with code '{value}' already exists.")
        return value.strip()


class OrderItemSerializer(serializers.ModelSerializer):
    unit_price = serializers.SerializerMethodField()
    line_subtotal = serializers.SerializerMethodField()
    line_tax = serializers.SerializerMethodField()
    line_total = serializers.SerializerMethodField()

    class Meta:
        model = OrderItem
        fields = [
            "sku",
            "name",
            "quantity",
            "unit_price",
            "line_subtotal",
            "line_tax",
            "line_total",
        ]

    def get_unit_price(self, obj) -> str:
        return f"{obj.unit_price:.2f}"

    def get_line_subtotal(self, obj) -> str:
        return f"{obj.line_subtotal:.2f}"

    def get_line_tax(self, obj) -> str:
        return f"{obj.line_tax:.2f}"

    def get_line_total(self, obj) -> str:
        return f"{obj.line_total:.2f}"


class OrderSerializer(serializers.ModelSerializer):
    dealer_id = serializers.IntegerField(source="dealer.id")
    items = OrderItemSerializer(many=True, read_only=True)
    subtotal = serializers.SerializerMethodField()
    tax_total = serializers.SerializerMethodField()
    grand_total = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField(format="%Y-%m-%dT%H:%M:%S%z")
    updated_at = serializers.DateTimeField(format="%Y-%m-%dT%H:%M:%S%z")

    class Meta:
        model = Order
        fields = [
            "order_number",
            "dealer_id",
            "status",
            "items",
            "subtotal",
            "tax_total",
            "grand_total",
            "created_at",
            "updated_at",
        ]

    def get_subtotal(self, obj) -> str:
        return f"{obj.subtotal:.2f}"

    def get_tax_total(self, obj) -> str:
        return f"{obj.tax_total:.2f}"

    def get_grand_total(self, obj) -> str:
        return f"{obj.grand_total:.2f}"
