import hashlib
import json
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime
import zoneinfo
from django.db import transaction
from django.db.models import Max
from core.models import Order, OrderItem, Product, Inventory, Dealer, IdempotencyRecord
from core.exceptions import (
    CustomAPIException,
    InsufficientStockError,
    InvalidTransitionError,
    OrderLockedError,
    IdempotencyConflictError
)

TWOPLACES = Decimal("0.01")
TAX_RATE = Decimal("0.18")
KOLKATA_TZ = zoneinfo.ZoneInfo("Asia/Kolkata")


def quantize_money(amount: Decimal) -> Decimal:
    return amount.quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def calculate_line_item(unit_price: Decimal, quantity: int) -> dict:
    unit_price = quantize_money(unit_price)
    line_subtotal = quantize_money(unit_price * quantity)
    line_tax = quantize_money(line_subtotal * TAX_RATE)
    line_total = line_subtotal + line_tax
    return {
        "unit_price": unit_price,
        "line_subtotal": line_subtotal,
        "line_tax": line_tax,
        "line_total": line_total,
    }


def generate_order_number() -> str:
    now_ist = datetime.now(KOLKATA_TZ)
    date_str = now_ist.strftime("%Y%m%d")
    prefix = f"ORD-{date_str}-"

    with transaction.atomic():
        # Lock existing orders for today to avoid race condition in sequential numbering
        latest_order = (
            Order.objects.select_for_update()
            .filter(order_number__startswith=prefix)
            .order_by("-order_number")
            .first()
        )
        if latest_order:
            try:
                seq = int(latest_order.order_number.split("-")[-1]) + 1
            except (ValueError, IndexError):
                seq = 1
        else:
            seq = 1
        return f"{prefix}{seq:04d}"


def compute_request_hash(payload: dict) -> str:
    canonical_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def create_draft_order(dealer_id: int, items_data: list, idempotency_key: str = None) -> tuple[Order, bool]:
    """
    Creates a draft order after validating dealer, items, quantities, and unique SKUs.
    Handles idempotency if key is provided.
    Returns (order, created: bool).
    """
    if not items_data or len(items_data) == 0:
        raise CustomAPIException(code="VALIDATION_ERROR", message="At least one item is required in the order", details=["items list cannot be empty"])

    try:
        dealer = Dealer.objects.get(id=dealer_id)
    except Dealer.DoesNotExist:
        raise CustomAPIException(code="VALIDATION_ERROR", message=f"Dealer with id {dealer_id} does not exist", details=[f"Dealer {dealer_id} not found"])

    # Check for duplicate SKUs in items_data
    skus_seen = set()
    for item in items_data:
        sku = item.get("sku")
        qty = item.get("quantity")
        if not sku:
            raise CustomAPIException(code="VALIDATION_ERROR", message="SKU is required for each item", details=["Missing sku"])
        if sku in skus_seen:
            raise CustomAPIException(code="VALIDATION_ERROR", message=f"Duplicate SKU '{sku}' in order request", details=[f"SKU {sku} appears multiple times"])
        skus_seen.add(sku)

        if qty is None or not isinstance(qty, int) or qty < 1:
            raise CustomAPIException(code="VALIDATION_ERROR", message=f"Quantity for {sku} must be an integer >= 1", details=[f"Invalid quantity: {qty}"])

    # Check if all SKUs exist
    products = {p.sku: p for p in Product.objects.filter(sku__in=list(skus_seen))}
    missing_skus = skus_seen - set(products.keys())
    if missing_skus:
        raise CustomAPIException(
            code="VALIDATION_ERROR",
            message=f"Unknown SKU(s): {', '.join(sorted(missing_skus))}",
            details=[f"Unknown SKU: {s}" for s in sorted(missing_skus)]
        )

    request_payload = {"dealer_id": dealer_id, "items": items_data}
    req_hash = compute_request_hash(request_payload)

    with transaction.atomic():
        if idempotency_key:
            idempotency_rec = IdempotencyRecord.objects.select_for_update().filter(idempotency_key=idempotency_key).first()
            if idempotency_rec:
                if idempotency_rec.request_hash != req_hash:
                    raise IdempotencyConflictError("Same Idempotency-Key reused with different request payload")
                if idempotency_rec.order:
                    return idempotency_rec.order, False

        order_no = generate_order_number()
        order = Order.objects.create(
            order_number=order_no,
            dealer=dealer,
            status="DRAFT",
            subtotal=Decimal("0.00"),
            tax_total=Decimal("0.00"),
            grand_total=Decimal("0.00"),
        )

        subtotal = Decimal("0.00")
        tax_total = Decimal("0.00")

        order_items = []
        for item in items_data:
            sku = item["sku"]
            qty = item["quantity"]
            product = products[sku]
            calc = calculate_line_item(product.price, qty)

            order_items.append(OrderItem(
                order=order,
                product=product,
                sku=product.sku,
                name=product.name,
                quantity=qty,
                unit_price=calc["unit_price"],
                line_subtotal=calc["line_subtotal"],
                line_tax=calc["line_tax"],
                line_total=calc["line_total"],
            ))
            subtotal += calc["line_subtotal"]
            tax_total += calc["line_tax"]

        OrderItem.objects.bulk_create(order_items)

        order.subtotal = subtotal
        order.tax_total = tax_total
        order.grand_total = subtotal + tax_total
        order.save(update_fields=["subtotal", "tax_total", "grand_total"])

        if idempotency_key:
            from core.serializers import OrderSerializer
            resp_body = OrderSerializer(order).data
            IdempotencyRecord.objects.create(
                idempotency_key=idempotency_key,
                request_hash=req_hash,
                status_code=201,
                response_body=resp_body,
                order=order
            )

        return order, True


def update_draft_order_items(order: Order, items_data: list) -> Order:
    """
    Replaces all items in a DRAFT order.
    Only allowed while DRAFT.
    """
    if order.status != "DRAFT":
        raise OrderLockedError(f"Cannot edit order {order.order_number} in {order.status} status")

    if not items_data or len(items_data) == 0:
        raise CustomAPIException(code="VALIDATION_ERROR", message="At least one item is required in the order", details=["items list cannot be empty"])

    skus_seen = set()
    for item in items_data:
        sku = item.get("sku")
        qty = item.get("quantity")
        if not sku:
            raise CustomAPIException(code="VALIDATION_ERROR", message="SKU is required for each item", details=["Missing sku"])
        if sku in skus_seen:
            raise CustomAPIException(code="VALIDATION_ERROR", message=f"Duplicate SKU '{sku}' in order request", details=[f"SKU {sku} appears multiple times"])
        skus_seen.add(sku)

        if qty is None or not isinstance(qty, int) or qty < 1:
            raise CustomAPIException(code="VALIDATION_ERROR", message=f"Quantity for {sku} must be an integer >= 1", details=[f"Invalid quantity: {qty}"])

    products = {p.sku: p for p in Product.objects.filter(sku__in=list(skus_seen))}
    missing_skus = skus_seen - set(products.keys())
    if missing_skus:
        raise CustomAPIException(
            code="VALIDATION_ERROR",
            message=f"Unknown SKU(s): {', '.join(sorted(missing_skus))}",
            details=[f"Unknown SKU: {s}" for s in sorted(missing_skus)]
        )

    with transaction.atomic():
        order.items.all().delete()
        subtotal = Decimal("0.00")
        tax_total = Decimal("0.00")

        order_items = []
        for item in items_data:
            sku = item["sku"]
            qty = item["quantity"]
            product = products[sku]
            calc = calculate_line_item(product.price, qty)

            order_items.append(OrderItem(
                order=order,
                product=product,
                sku=product.sku,
                name=product.name,
                quantity=qty,
                unit_price=calc["unit_price"],
                line_subtotal=calc["line_subtotal"],
                line_tax=calc["line_tax"],
                line_total=calc["line_total"],
            ))
            subtotal += calc["line_subtotal"]
            tax_total += calc["line_tax"]

        OrderItem.objects.bulk_create(order_items)
        order.subtotal = subtotal
        order.tax_total = tax_total
        order.grand_total = subtotal + tax_total
        order.save(update_fields=["subtotal", "tax_total", "grand_total", "updated_at"])

    return order


def confirm_order(order: Order) -> Order:
    """
    DRAFT -> CONFIRMED.
    Validates and locks stock across all lines with select_for_update.
    All or nothing.
    Locks current unit prices.
    """
    if order.status != "DRAFT":
        raise InvalidTransitionError(f"Cannot confirm order {order.order_number} from status {order.status}")

    with transaction.atomic():
        order_items = list(order.items.select_related("product").all())
        skus = [it.sku for it in order_items]

        # Lock inventory rows
        inventories = {
            inv.product_id: inv
            for inv in Inventory.objects.select_for_update().filter(product__sku__in=skus)
        }

        short_lines = []
        short_details = []
        for it in order_items:
            product = it.product
            inv = inventories.get(product.id if product else None)
            available = inv.quantity if inv else 0
            if available < it.quantity:
                prod_name = product.name if product else it.name
                short_lines.append(f"Insufficient stock for {prod_name}. Available: {available}, Requested: {it.quantity}")
                short_details.append({
                    "sku": it.sku,
                    "requested": it.quantity,
                    "available": available
                })

        if short_lines:
            raise InsufficientStockError(message=" ".join(short_lines), details=short_details)

        # Deduct stock and lock prices
        for it in order_items:
            product = it.product
            inv = inventories[product.id]
            inv.quantity -= it.quantity
            inv.save(update_fields=["quantity", "updated_at"])

        order.status = "CONFIRMED"
        order.save(update_fields=["status", "updated_at"])

    return order


def cancel_order(order: Order) -> Order:
    """
    DRAFT -> CANCELLED, or CONFIRMED -> CANCELLED (restores stock).
    DISPATCHED and DELIVERED orders cannot be cancelled.
    """
    if order.status not in ("DRAFT", "CONFIRMED"):
        raise InvalidTransitionError(f"Cannot cancel order {order.order_number} from status {order.status}")

    with transaction.atomic():
        if order.status == "CONFIRMED":
            # Restore stock
            order_items = list(order.items.select_related("product").all())
            skus = [it.sku for it in order_items]
            inventories = {
                inv.product_id: inv
                for inv in Inventory.objects.select_for_update().filter(product__sku__in=skus)
            }
            for it in order_items:
                product = it.product
                if product and product.id in inventories:
                    inv = inventories[product.id]
                    inv.quantity += it.quantity
                    inv.save(update_fields=["quantity", "updated_at"])

        order.status = "CANCELLED"
        order.save(update_fields=["status", "updated_at"])

    return order


def dispatch_order(order: Order) -> Order:
    if order.status != "CONFIRMED":
        raise InvalidTransitionError(f"Cannot dispatch order {order.order_number} from status {order.status}")
    order.status = "DISPATCHED"
    order.save(update_fields=["status", "updated_at"])
    return order


def deliver_order(order: Order) -> Order:
    if order.status != "DISPATCHED":
        raise InvalidTransitionError(f"Cannot deliver order {order.order_number} from status {order.status}")
    order.status = "DELIVERED"
    order.save(update_fields=["status", "updated_at"])
    return order
