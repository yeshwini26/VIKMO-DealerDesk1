from django.urls import path
from . import views

urlpatterns = [
    # Health
    path("health/", views.health, name="health"),

    # Products
    path("products/", views.product_list, name="product_list"),
    path("products/<str:sku>/", views.product_detail, name="product_detail"),

    # Inventory
    path("inventory/<str:sku>/", views.inventory_detail, name="inventory_detail"),

    # Dealers
    path("dealers/", views.dealer_list, name="dealer_list"),
    path("dealers/<int:id>/", views.dealer_detail, name="dealer_detail"),

    # Orders
    path("orders/", views.order_list, name="order_list"),
    path("orders/<str:order_number>/", views.order_detail, name="order_detail"),
    path("orders/<str:order_number>/confirm/", views.order_confirm, name="order_confirm"),
    path("orders/<str:order_number>/cancel/", views.order_cancel, name="order_cancel"),
    path("orders/<str:order_number>/dispatch/", views.order_dispatch, name="order_dispatch"),
    path("orders/<str:order_number>/deliver/", views.order_deliver, name="order_deliver"),

    # Assistant
    path("assistant/chat/", views.assistant_chat, name="assistant_chat"),

    # Forecasting & Reorder
    path("forecast/<str:sku>/", views.forecast_detail, name="forecast_detail"),
    path("reorder-suggestions/", views.reorder_suggestions, name="reorder_suggestions"),

    # Supplier Sync
    path("sync/supplier/", views.supplier_sync, name="supplier_sync"),
]
