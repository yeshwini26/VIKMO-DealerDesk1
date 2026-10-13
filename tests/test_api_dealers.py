import pytest
from decimal import Decimal
from rest_framework.test import APIClient
from core.models import Dealer


@pytest.mark.django_db
class TestDealerAPIs:
    def setup_method(self):
        self.client = APIClient()

    def test_dealers_list_and_create(self):
        d1 = Dealer.objects.create(
            dealer_code="DLR-001",
            name="First Wheels",
            city="Chennai",
            credit_limit=Decimal("100000.00")
        )

        res = self.client.get("/api/dealers/")
        assert res.status_code == 200
        data = res.json()
        assert data["count"] == 1
        assert data["results"][0]["dealer_code"] == "DLR-001"
        assert data["results"][0]["credit_limit"] == "100000.00"

        # Create new dealer
        payload = {
            "dealer_code": "DLR-002",
            "name": "Second Wheels",
            "city": "Bengaluru",
            "phone": "9876543210",
            "email": "info@secondwheels.com",
            "credit_limit": "50000.00"
        }
        res_post = self.client.post("/api/dealers/", payload, format="json")
        assert res_post.status_code == 201
        assert res_post.json()["dealer_code"] == "DLR-002"

        # Duplicate dealer code -> 409 DUPLICATE
        res_dup = self.client.post("/api/dealers/", payload, format="json")
        assert res_dup.status_code == 409
        assert res_dup.json()["error"]["code"] == "DUPLICATE"

    def test_dealer_detail_and_404(self):
        d = Dealer.objects.create(
            dealer_code="DLR-003",
            name="Apex Motors",
            credit_limit=Decimal("75000.00")
        )
        res = self.client.get(f"/api/dealers/{d.id}/")
        assert res.status_code == 200
        assert res.json()["dealer_code"] == "DLR-003"

        res_404 = self.client.get("/api/dealers/999999/")
        assert res_404.status_code == 404
        assert res_404.json()["error"]["code"] == "NOT_FOUND"
