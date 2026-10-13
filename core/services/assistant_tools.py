import re
from decimal import Decimal
from django.db.models import Q
from core.models import Product, Inventory, Dealer
from core.services import orders as order_service


def normalize_tokens(text: str) -> list[str]:
    if not text:
        return []
    # Remove punctuation, lowercase, split on whitespace
    cleaned = re.sub(r"[^\w\s]", " ", text.lower())
    return [t for t in cleaned.split() if len(t) > 1]


def search_parts(query: str, vehicle: str = None) -> list[dict]:
    """
    Search product database grounded in real products.
    Uses token matching, vehicle fitment filtering, and relevance ranking.
    """
    if not query or not query.strip():
        return []

    query_tokens = normalize_tokens(query)
    vehicle_tokens = normalize_tokens(vehicle) if vehicle else []

    qs = Product.objects.select_related("inventory").all()

    # Base candidate retrieval
    q_filter = Q()
    for token in query_tokens:
        q_filter |= Q(name__icontains=token) | Q(category__icontains=token) | Q(sku__icontains=token)

    if vehicle:
        v_filter = Q(vehicle_fitment__icontains=vehicle) | Q(vehicle_fitment__iexact="Universal")
        for vt in vehicle_tokens:
            v_filter |= Q(vehicle_fitment__icontains=vt)
        candidates = qs.filter(q_filter & v_filter)
        if not candidates.exists():
            candidates = qs.filter(q_filter)
    else:
        candidates = qs.filter(q_filter)

    results = []
    for p in candidates:
        score = 0
        p_name_lower = p.name.lower()
        p_cat_lower = p.category.lower()
        p_fit_lower = p.vehicle_fitment.lower()
        p_sku_lower = p.sku.lower()

        # Vehicle matching bonus
        if vehicle:
            v_lower = vehicle.lower()
            if v_lower in p_fit_lower:
                score += 50
            elif "universal" in p_fit_lower:
                score += 20
            for vt in vehicle_tokens:
                if vt in p_fit_lower:
                    score += 10

        # Query token matching
        for token in query_tokens:
            if token == p_sku_lower:
                score += 100
            if token in p_name_lower:
                score += 20
            if token in p_cat_lower:
                score += 15

        results.append((score, p))

    # Sort descending by relevance score
    results.sort(key=lambda x: x[0], reverse=True)
    top_matches = results[:5]

    return [
        {
            "sku": p.sku,
            "name": p.name,
            "category": p.category,
            "brand": p.brand,
            "vehicle_fitment": p.vehicle_fitment,
            "price": f"{p.price:.2f}",
            "stock": p.stock,
            "description": p.description[:200],  # truncated snippet
        }
        for _, p in top_matches
    ]


def check_stock(sku: str) -> dict:
    """
    Check live stock and catalogue price directly from database at execution time.
    """
    if not sku:
        return {"error": "SKU is required"}

    sku = sku.strip()
    try:
        product = Product.objects.select_related("inventory").get(sku=sku)
    except Product.DoesNotExist:
        return {"error": f"Product with SKU '{sku}' not found"}

    return {
        "sku": product.sku,
        "name": product.name,
        "brand": product.brand,
        "vehicle_fitment": product.vehicle_fitment,
        "price": f"{product.price:.2f}",
        "stock": product.stock,
    }


def create_draft_order(dealer_id: int, sku: str, quantity: int) -> dict:
    """
    Creates a draft order using the shared business logic.
    Only creates DRAFT orders.
    """
    if not dealer_id:
        return {"error": "dealer_id is required"}
    if not sku:
        return {"error": "sku is required"}
    if quantity is None or not isinstance(quantity, int) or quantity < 1:
        return {"error": "quantity must be an integer >= 1"}

    sku = sku.strip()
    try:
        order, _ = order_service.create_draft_order(
            dealer_id=dealer_id,
            items_data=[{"sku": sku, "quantity": quantity}]
        )
        return {
            "success": True,
            "order_number": order.order_number,
            "status": order.status,
            "dealer_id": dealer_id,
            "sku": sku,
            "quantity": quantity,
            "grand_total": f"{order.grand_total:.2f}",
        }
    except Exception as e:
        return {"error": str(e)}
