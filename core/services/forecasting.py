import csv
import math
from pathlib import Path
from django.conf import settings
from core.models import Product, Inventory
from core.exceptions import CustomAPIException

BASE_DIR = settings.BASE_DIR
DATA_DIR = settings.DATA_DIR
ML_DIR = BASE_DIR / "ml"
FORECAST_FILE = ML_DIR / "forecast.csv"
LEAD_TIMES_FILE = DATA_DIR / "lead_times.csv"
SALES_HISTORY_FILE = DATA_DIR / "sales_history.csv"


_cached_lead_times = None
_cached_sales_sigma = None
_cached_forecast_data = None


def load_lead_times() -> dict[str, int]:
    global _cached_lead_times
    if _cached_lead_times is not None:
        return _cached_lead_times

    lead_times = {}
    with open(LEAD_TIMES_FILE, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            lead_times[row["sku"].strip()] = int(row["lead_time_weeks"].strip())
    _cached_lead_times = lead_times
    return lead_times


def load_forecast_data() -> dict[str, list[dict]]:
    global _cached_forecast_data
    if _cached_forecast_data is not None:
        return _cached_forecast_data

    forecasts = {}
    with open(FORECAST_FILE, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sku = row["sku"].strip()
            wk = row["week_start"].strip()
            units = float(row["forecast_units"].strip())
            if sku not in forecasts:
                forecasts[sku] = []
            forecasts[sku].append({
                "week_start": wk,
                "forecast_units": units,
            })
    # Sort weeks chronologically
    for sku in forecasts:
        forecasts[sku].sort(key=lambda x: x["week_start"])
    _cached_forecast_data = forecasts
    return forecasts


def load_sales_sigma() -> dict[str, float]:
    """
    Computes sample standard deviation (ddof=1) of units_sold over the most recent 12 weeks
    for each SKU (or all available weeks if fewer than 12).
    """
    global _cached_sales_sigma
    if _cached_sales_sigma is not None:
        return _cached_sales_sigma

    sales_by_sku = {}
    with open(SALES_HISTORY_FILE, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sku = row["sku"].strip()
            wk = row["week_start"].strip()
            units = float(row["units_sold"].strip())
            if sku not in sales_by_sku:
                sales_by_sku[sku] = []
            sales_by_sku[sku].append((wk, units))

    sigmas = {}
    for sku, records in sales_by_sku.items():
        records.sort(key=lambda x: x[0])  # sort by week_start
        recent_12 = [units for _, units in records[-12:]]
        n = len(recent_12)
        if n > 1:
            mean = sum(recent_12) / n
            variance = sum((x - mean) ** 2 for x in recent_12) / (n - 1)
            sigmas[sku] = math.sqrt(variance)
        else:
            sigmas[sku] = 0.0

    _cached_sales_sigma = sigmas
    return sigmas


def get_sku_forecast(sku: str) -> dict:
    lead_times = load_lead_times()
    if sku not in lead_times:
        raise CustomAPIException(
            code="NOT_FOUND",
            message=f"SKU '{sku}' is not a forecast SKU",
            status_code=404
        )

    forecast_data = load_forecast_data()
    entries = forecast_data.get(sku)
    if not entries:
        raise CustomAPIException(
            code="NOT_FOUND",
            message=f"No forecast found for SKU '{sku}'",
            status_code=404
        )

    return {
        "sku": sku,
        "forecast": entries,
    }


def get_reorder_suggestions() -> list[dict]:
    lead_times = load_lead_times()
    forecast_data = load_forecast_data()
    sigmas = load_sales_sigma()

    # Query live stock from PostgreSQL / database
    skus = list(lead_times.keys())
    live_stocks = {
        inv.product.sku: inv.quantity
        for inv in Inventory.objects.select_related("product").filter(product__sku__in=skus)
    }

    suggestions = []
    for sku in sorted(skus):
        L = lead_times[sku]
        sku_forecasts = forecast_data.get(sku, [])
        first_l_forecasts = sku_forecasts[:L]
        lead_time_demand = round(sum(f["forecast_units"] for f in first_l_forecasts), 1)

        sigma = sigmas.get(sku, 0.0)
        safety_stock = math.ceil(1.65 * sigma * math.sqrt(L))
        reorder_point = round(lead_time_demand + safety_stock, 1)

        current_stock = live_stocks.get(sku, 0)
        reorder_qty = max(0, math.ceil(reorder_point - current_stock))

        suggestions.append({
            "sku": sku,
            "current_stock": current_stock,
            "lead_time_weeks": L,
            "lead_time_demand": lead_time_demand,
            "safety_stock": safety_stock,
            "reorder_point": reorder_point,
            "reorder_qty": reorder_qty,
        })

    return suggestions
