import os
import csv
from pathlib import Path
import pytest
from rest_framework.test import APIClient
from core.models import Product, Inventory

BASE_DIR = Path(__file__).resolve().parent.parent


class TestForecasting:
    def setup_method(self):
        self.client = APIClient()

    def test_forecast_file_structure(self):
        csv_path = BASE_DIR / "ml" / "forecast.csv"
        assert csv_path.exists(), "ml/forecast.csv must exist"

        with open(csv_path, mode="r", encoding="utf-8") as f:
            reader = list(csv.DictReader(f))

        assert len(reader) == 160, f"Expected 160 rows, got {len(reader)}"
        assert list(reader[0].keys())[:3] == ["sku", "week_start", "forecast_units"]

        expected_weeks = {"2026-09-28", "2026-10-05", "2026-10-12", "2026-10-19"}
        found_weeks = {r["week_start"] for r in reader}
        assert found_weeks == expected_weeks

        skus = {r["sku"] for r in reader}
        assert len(skus) == 40

        # Non-negative check
        for r in reader:
            assert float(r["forecast_units"]) >= 0, f"Negative forecast found: {r}"

    @pytest.mark.django_db
    def test_forecast_api_and_reorder_suggestions(self):
        # Create products and inventory for forecast SKUs to test live stock
        p = Product.objects.create(
            sku="FIL-1016",
            name="Oil Filter",
            category="Filters",
            brand="Bosch",
            vehicle_fitment="Universal",
            price="300.00"
        )
        Inventory.objects.create(product=p, quantity=25)

        # GET /api/forecast/FIL-1016/
        res = self.client.get("/api/forecast/FIL-1016/")
        assert res.status_code == 200
        data = res.json()
        assert data["sku"] == "FIL-1016"
        assert len(data["forecast"]) == 4
        assert [f["week_start"] for f in data["forecast"]] == [
            "2026-09-28", "2026-10-05", "2026-10-12", "2026-10-19"
        ]

        # Unknown SKU -> 404
        res_404 = self.client.get("/api/forecast/UNKNOWN-999/")
        assert res_404.status_code == 404

        # GET /api/reorder-suggestions/
        res_reorder = self.client.get("/api/reorder-suggestions/")
        assert res_reorder.status_code == 200
        res_list = res_reorder.json().get("results")
        assert isinstance(res_list, list)
        assert len(res_list) == 40

        item = next(x for x in res_list if x["sku"] == "FIL-1016")
        assert item["current_stock"] == 25
        assert "lead_time_weeks" in item
        assert "lead_time_demand" in item
        assert "safety_stock" in item
        assert "reorder_point" in item
        assert "reorder_qty" in item
