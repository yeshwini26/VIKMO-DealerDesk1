import re
import time
from decimal import Decimal, InvalidOperation
from datetime import datetime
import requests
from django.conf import settings
from django.db import transaction
from core.models import Product, Inventory
from core.exceptions import SupplierUnavailableError

MONEY_REGEX = re.compile(r"^\d+(\.\d{1,2})?$")


def is_valid_price(price_str: str) -> bool:
    if not price_str or not isinstance(price_str, str):
        return False
    if not MONEY_REGEX.match(price_str):
        return False
    try:
        val = Decimal(price_str)
        return val > Decimal("0.00")
    except (InvalidOperation, ValueError):
        return False


def run_supplier_sync() -> dict:
    api_url = getattr(settings, "SUPPLIER_API_URL", "http://127.0.0.1:8099").rstrip("/")
    api_key = getattr(settings, "SUPPLIER_API_KEY", "")

    cursor = None
    all_raw_items = []
    pages_fetched = 0
    requests_made = 0
    max_attempts = 5

    session = requests.Session()

    while True:
        url = f"{api_url}/v1/products?limit=20"
        if cursor:
            url += f"&cursor={cursor}"

        headers = {"X-Api-Key": api_key}
        success = False

        for attempt in range(1, max_attempts + 1):
            requests_made += 1
            try:
                resp = session.get(url, headers=headers, timeout=25)
                if resp.status_code == 200:
                    pages_fetched += 1
                    data = resp.json()
                    items = data.get("items", [])
                    all_raw_items.extend(items)
                    cursor = data.get("next_cursor")
                    success = True
                    break
                elif resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", "1"))
                    time.sleep(retry_after)
                elif resp.status_code in (500, 502, 503, 504):
                    time.sleep(0.5 * attempt)
                elif resp.status_code == 401:
                    # Unauthorized
                    raise SupplierUnavailableError("Supplier API key rejected (HTTP 401)")
                else:
                    # Unhandled client error
                    time.sleep(0.5 * attempt)
            except (requests.RequestException, SupplierUnavailableError) as e:
                if isinstance(e, SupplierUnavailableError):
                    raise
                if attempt == max_attempts:
                    raise SupplierUnavailableError(f"Failed to fetch supplier page after {max_attempts} attempts: {e}")
                time.sleep(0.5 * attempt)

        if not success:
            raise SupplierUnavailableError(f"Failed to fetch supplier page after {max_attempts} attempts")

        if not cursor:
            break

    # Process fetched feed
    rejected = []
    valid_items = []

    for item in all_raw_items:
        sku = (item.get("sku") or "").strip()
        price_val = str(item.get("price") or "").strip()
        sup_sku = item.get("supplier_sku", "")

        if not sku:
            rejected.append({"supplier_sku": sup_sku, "reason": "missing or empty sku"})
            continue

        if not is_valid_price(price_val):
            rejected.append({"supplier_sku": sup_sku, "reason": "invalid price"})
            continue

        valid_items.append(item)

    # Deduplicate by SKU using latest updated_at
    latest_by_sku = {}
    for item in valid_items:
        sku = item["sku"].strip()
        updated_str = item.get("updated_at", "")
        try:
            # Parse ISO 8601
            dt = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            dt = datetime.min

        if sku not in latest_by_sku:
            latest_by_sku[sku] = (dt, item)
        else:
            if dt > latest_by_sku[sku][0]:
                latest_by_sku[sku] = (dt, item)

    deduped_items = [v[1] for v in latest_by_sku.values()]

    # Apply all-or-nothing in atomic transaction
    created_count = 0
    updated_count = 0
    unchanged_count = 0

    with transaction.atomic():
        existing_products = {p.sku: p for p in Product.objects.select_for_update().filter(sku__in=list(latest_by_sku.keys()))}

        for item in deduped_items:
            sku = item["sku"].strip()
            name = (item.get("name") or "").strip()
            category = (item.get("category") or "").strip()
            brand = (item.get("brand") or "").strip()
            vehicle_fitment = (item.get("vehicle_fitment") or "").strip()
            description = (item.get("description") or "").strip()
            price = Decimal(str(item["price"]).strip())

            if sku not in existing_products:
                new_prod = Product.objects.create(
                    sku=sku,
                    name=name,
                    category=category,
                    brand=brand,
                    vehicle_fitment=vehicle_fitment,
                    description=description,
                    price=price,
                )
                Inventory.objects.create(product=new_prod, quantity=0, reason="Supplier sync initial stock")
                created_count += 1
            else:
                p = existing_products[sku]
                differs = (
                    p.name != name or
                    p.category != category or
                    p.brand != brand or
                    p.vehicle_fitment != vehicle_fitment or
                    p.description != description or
                    p.price != price
                )
                if differs:
                    p.name = name
                    p.category = category
                    p.brand = brand
                    p.vehicle_fitment = vehicle_fitment
                    p.description = description
                    p.price = price
                    p.save()
                    updated_count += 1
                else:
                    unchanged_count += 1

    return {
        "created": created_count,
        "updated": updated_count,
        "unchanged": unchanged_count,
        "rejected": rejected,
        "pages_fetched": pages_fetched,
        "requests_made": requests_made,
    }
