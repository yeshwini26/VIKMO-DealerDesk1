from decimal import Decimal
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from core.models import Product, Inventory, Dealer, Order
from core.serializers import (
    ProductSerializer,
    ProductCreateSerializer,
    ProductUpdateSerializer,
    DealerSerializer,
    DealerCreateSerializer,
    OrderSerializer,
)
from core.pagination import StandardResultsSetPagination
from core.exceptions import CustomAPIException
from core.services import orders as order_service
from django.shortcuts import render


def portal_view(request):
    """Renders the complete DealerDesk Interactive Executive Portal with live database grounding."""
    from django.db.models import Sum
    from core.services.forecasting import get_reorder_suggestions, load_lead_times, get_sku_forecast

    # Real database statistics
    total_products = Product.objects.count()
    total_dealers = Dealer.objects.count()
    total_stock = Inventory.objects.aggregate(total=Sum("quantity"))["total"] or 0

    # Live reorder suggestions
    try:
        reorders = get_reorder_suggestions()
    except Exception:
        reorders = []

    critical_reorders = [r for r in reorders if r.get("reorder_qty", 0) > 0]
    critical_reorders_count = len(critical_reorders)

    # Monitored forecast SKUs
    try:
        forecast_skus = sorted(list(load_lead_times().keys()))
    except Exception:
        forecast_skus = ["FIL-1016"]

    # Initial forecast for default selected SKU
    try:
        default_forecast = get_sku_forecast("FIL-1016")
    except Exception:
        default_forecast = {"sku": "FIL-1016", "forecast": []}

    # All seeded dealers
    dealers = list(Dealer.objects.all().order_by("id"))

    # Initial 20 products for instantaneous SSR render
    initial_products = list(
        Product.objects.select_related("inventory").all().order_by("sku")[:20]
    )

    # Initial recent orders
    recent_orders = list(
        Order.objects.select_related("dealer").prefetch_related("items").all().order_by("-created_at")[:20]
    )
    total_orders = Order.objects.count()

    context = {
        "total_products": total_products,
        "total_dealers": total_dealers,
        "total_stock": total_stock,
        "total_orders": total_orders,
        "tracked_skus_count": len(forecast_skus),
        "critical_reorders_count": critical_reorders_count,
        "critical_reorders": critical_reorders[:8],
        "all_reorders": reorders,
        "forecast_skus": forecast_skus,
        "default_forecast": default_forecast,
        "dealers": dealers,
        "initial_products": initial_products,
        "recent_orders": recent_orders,
    }
    return render(request, "portal.html", context)


@api_view(["GET"])
def health(request):
    """GET /api/health/ — healthcheck endpoint."""
    return Response({"status": "ok"})


# ---- Products -------------------------------------------------------------------------

@api_view(["GET", "POST"])
def product_list(request):
    if request.method == "GET":
        qs = Product.objects.select_related("inventory").all()

        search = request.query_params.get("search")
        if search:
            qs = qs.filter(
                Q(sku__icontains=search) |
                Q(name__icontains=search) |
                Q(brand__icontains=search)
            )

        category = request.query_params.get("category")
        if category:
            qs = qs.filter(category=category)

        vehicle = request.query_params.get("vehicle")
        if vehicle:
            qs = qs.filter(vehicle_fitment__icontains=vehicle)

        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = ProductSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    elif request.method == "POST":
        serializer = ProductCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            product = serializer.save()
            Inventory.objects.create(product=product, quantity=0, reason="New product initial stock")
        return Response(ProductSerializer(product).data, status=status.HTTP_201_CREATED)


@api_view(["GET", "PATCH"])
def product_detail(request, sku):
    try:
        product = Product.objects.select_related("inventory").get(sku=sku)
    except Product.DoesNotExist:
        raise CustomAPIException(
            code="NOT_FOUND",
            message=f"Product with SKU '{sku}' not found",
            status_code=status.HTTP_404_NOT_FOUND
        )

    if request.method == "GET":
        return Response(ProductSerializer(product).data)

    elif request.method == "PATCH":
        serializer = ProductUpdateSerializer(product, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(ProductSerializer(product).data)


# ---- Inventory -----------------------------------------------------------------------

@api_view(["PUT"])
def inventory_detail(request, sku):
    try:
        product = Product.objects.get(sku=sku)
    except Product.DoesNotExist:
        raise CustomAPIException(
            code="NOT_FOUND",
            message=f"Product with SKU '{sku}' not found",
            status_code=status.HTTP_404_NOT_FOUND
        )

    qty = request.data.get("quantity")
    reason = request.data.get("reason", "")

    if qty is None or not isinstance(qty, int) or qty < 0:
        raise CustomAPIException(
            code="VALIDATION_ERROR",
            message="quantity must be a non-negative integer",
            details=["quantity must be >= 0"]
        )

    inventory, _ = Inventory.objects.update_or_create(
        product=product,
        defaults={"quantity": qty, "reason": reason}
    )

    return Response({"sku": sku, "quantity": inventory.quantity}, status=status.HTTP_200_OK)


# ---- Dealers -------------------------------------------------------------------------

@api_view(["GET", "POST"])
def dealer_list(request):
    if request.method == "GET":
        qs = Dealer.objects.all()
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = DealerSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    elif request.method == "POST":
        serializer = DealerCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        dealer = serializer.save()
        return Response(DealerSerializer(dealer).data, status=status.HTTP_201_CREATED)


@api_view(["GET"])
def dealer_detail(request, id):
    try:
        dealer = Dealer.objects.get(id=id)
    except Dealer.DoesNotExist:
        raise CustomAPIException(
            code="NOT_FOUND",
            message=f"Dealer with id {id} not found",
            status_code=status.HTTP_404_NOT_FOUND
        )
    return Response(DealerSerializer(dealer).data)


# ---- Orders --------------------------------------------------------------------------

@api_view(["GET", "POST"])
def order_list(request):
    if request.method == "GET":
        qs = Order.objects.prefetch_related("items").all()

        dealer_id = request.query_params.get("dealer_id")
        if dealer_id:
            try:
                qs = qs.filter(dealer_id=int(dealer_id))
            except ValueError:
                pass

        order_status = request.query_params.get("status")
        if order_status:
            qs = qs.filter(status=order_status)

        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = OrderSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    elif request.method == "POST":
        dealer_id = request.data.get("dealer_id")
        items_data = request.data.get("items")
        idempotency_key = request.headers.get("Idempotency-Key")

        if dealer_id is None:
            raise CustomAPIException(
                code="VALIDATION_ERROR",
                message="dealer_id is required",
                details=["Missing dealer_id"]
            )

        if items_data is None or not isinstance(items_data, list):
            raise CustomAPIException(
                code="VALIDATION_ERROR",
                message="items must be a list",
                details=["Missing or invalid items list"]
            )

        order, created = order_service.create_draft_order(
            dealer_id=dealer_id,
            items_data=items_data,
            idempotency_key=idempotency_key
        )

        resp_status = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return Response(OrderSerializer(order).data, status=resp_status)


@api_view(["GET", "PUT"])
def order_detail(request, order_number):
    try:
        order = Order.objects.prefetch_related("items").get(order_number=order_number)
    except Order.DoesNotExist:
        raise CustomAPIException(
            code="NOT_FOUND",
            message=f"Order {order_number} not found",
            status_code=status.HTTP_404_NOT_FOUND
        )

    if request.method == "GET":
        return Response(OrderSerializer(order).data)

    elif request.method == "PUT":
        items_data = request.data.get("items")
        if items_data is None or not isinstance(items_data, list):
            raise CustomAPIException(
                code="VALIDATION_ERROR",
                message="items must be a list",
                details=["Missing or invalid items list"]
            )

        updated_order = order_service.update_draft_order_items(order, items_data)
        return Response(OrderSerializer(updated_order).data)


@api_view(["POST"])
def order_confirm(request, order_number):
    try:
        order = Order.objects.prefetch_related("items").get(order_number=order_number)
    except Order.DoesNotExist:
        raise CustomAPIException(
            code="NOT_FOUND",
            message=f"Order {order_number} not found",
            status_code=status.HTTP_404_NOT_FOUND
        )
    confirmed_order = order_service.confirm_order(order)
    return Response(OrderSerializer(confirmed_order).data)


@api_view(["POST"])
def order_cancel(request, order_number):
    try:
        order = Order.objects.prefetch_related("items").get(order_number=order_number)
    except Order.DoesNotExist:
        raise CustomAPIException(
            code="NOT_FOUND",
            message=f"Order {order_number} not found",
            status_code=status.HTTP_404_NOT_FOUND
        )
    cancelled_order = order_service.cancel_order(order)
    return Response(OrderSerializer(cancelled_order).data)


@api_view(["POST"])
def order_dispatch(request, order_number):
    try:
        order = Order.objects.prefetch_related("items").get(order_number=order_number)
    except Order.DoesNotExist:
        raise CustomAPIException(
            code="NOT_FOUND",
            message=f"Order {order_number} not found",
            status_code=status.HTTP_404_NOT_FOUND
        )
    dispatched_order = order_service.dispatch_order(order)
    return Response(OrderSerializer(dispatched_order).data)


@api_view(["POST"])
def order_deliver(request, order_number):
    try:
        order = Order.objects.prefetch_related("items").get(order_number=order_number)
    except Order.DoesNotExist:
        raise CustomAPIException(
            code="NOT_FOUND",
            message=f"Order {order_number} not found",
            status_code=status.HTTP_404_NOT_FOUND
        )
    delivered_order = order_service.deliver_order(order)
    return Response(OrderSerializer(delivered_order).data)


# ---- Assistant -----------------------------------------------------------------------

@api_view(["POST"])
def assistant_chat(request):
    """
    POST /api/assistant/chat/
    Body: {"session_id": "abc-123", "dealer_id": 7, "message": "..."}
    Response: {"reply": "...", "cited_skus": [], "tool_calls": [], "actions": []}
    """
    session_id = request.data.get("session_id")
    dealer_id = request.data.get("dealer_id")
    message = request.data.get("message")

    if not session_id or not isinstance(session_id, str):
        raise CustomAPIException(
            code="VALIDATION_ERROR",
            message="session_id is required",
            details=["session_id must be a non-empty string"]
        )

    if message is None or not isinstance(message, str):
        raise CustomAPIException(
            code="VALIDATION_ERROR",
            message="message is required",
            details=["message must be a string"]
        )

    from core.services import assistant as assistant_service
    res = assistant_service.handle_chat_turn(
        session_id=session_id,
        dealer_id=dealer_id,
        message=message
    )
    return Response(res, status=status.HTTP_200_OK)


# ---- Forecasting & Reorder -----------------------------------------------------------

@api_view(["GET"])
def forecast_detail(request, sku):
    """
    GET /api/forecast/{sku}/
    Returns 4-week forecast for the given forecast SKU matching ml/forecast.csv.
    """
    from core.services import forecasting as forecast_service
    res = forecast_service.get_sku_forecast(sku)
    return Response(res, status=status.HTTP_200_OK)


@api_view(["GET"])
def reorder_suggestions(request):
    """
    GET /api/reorder-suggestions/
    Returns reorder suggestions for all 40 forecast SKUs based on live database stock.
    """
    from core.services import forecasting as forecast_service
    results = forecast_service.get_reorder_suggestions()
    return Response({"results": results}, status=status.HTTP_200_OK)


# ---- Supplier Sync -------------------------------------------------------------------

@api_view(["POST"])
def supplier_sync(request):
    """
    POST /api/sync/supplier/
    Pulls supplier catalogue into DealerDesk, handling rate limits and failures.
    """
    from core.services import supplier_sync as sync_service
    res = sync_service.run_supplier_sync()
    return Response(res, status=status.HTTP_200_OK)
