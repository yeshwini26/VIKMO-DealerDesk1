import pytest
from decimal import Decimal
from unittest.mock import MagicMock, patch
from rest_framework.test import APIClient
from core.models import Product, Inventory, Dealer, ConversationMessage
from core.services import assistant_tools, assistant as assistant_service


@pytest.mark.django_db
class TestAssistant:
    def setup_method(self):
        self.client = APIClient()
        self.dealer = Dealer.objects.create(
            dealer_code="DLR-AST",
            name="Assistant Spares",
            credit_limit=Decimal("200000.00")
        )
        self.prod = Product.objects.create(
            sku="BRK-1044",
            name="Brake Pad Set - Bajaj Pulsar 150",
            category="Brakes",
            brand="Endurance",
            vehicle_fitment="Bajaj Pulsar 150",
            price=Decimal("1459.50"),
            description="Endurance ceramic brake pads for Pulsar 150."
        )
        self.inv = Inventory.objects.create(product=self.prod, quantity=136)

    def test_search_parts_tool(self):
        results = assistant_tools.search_parts(query="brake pads", vehicle="Bajaj Pulsar 150")
        assert len(results) > 0
        top = results[0]
        assert top["sku"] == "BRK-1044"
        assert top["price"] == "1459.50"
        assert top["stock"] == 136

    def test_check_stock_tool(self):
        stock_info = assistant_tools.check_stock(sku="BRK-1044")
        assert stock_info["sku"] == "BRK-1044"
        assert stock_info["stock"] == 136
        assert stock_info["price"] == "1459.50"

        # Non-existent SKU
        err_info = assistant_tools.check_stock(sku="NOPE-999")
        assert "error" in err_info

    def test_create_draft_order_tool(self):
        order_res = assistant_tools.create_draft_order(dealer_id=self.dealer.id, sku="BRK-1044", quantity=2)
        assert order_res["success"] is True
        assert order_res["status"] == "DRAFT"
        assert order_res["order_number"].startswith("ORD-")
        assert order_res["grand_total"] == "3444.42"

    def test_session_isolation(self):
        ConversationMessage.objects.create(
            session_id="session-A",
            role="user",
            content="Message in session A"
        )
        ConversationMessage.objects.create(
            session_id="session-B",
            role="user",
            content="Message in session B"
        )

        msgs_a = ConversationMessage.objects.filter(session_id="session-A")
        msgs_b = ConversationMessage.objects.filter(session_id="session-B")
        assert msgs_a.count() == 1
        assert msgs_b.count() == 1
        assert msgs_a.first().content == "Message in session A"
        assert msgs_b.first().content == "Message in session B"

    @patch("core.services.llm_provider.GeminiProvider.generate_turn")
    @patch("core.services.llm_provider.GeminiProvider.is_available", return_value=True)
    def test_assistant_chat_turn_mocked_llm(self, mock_avail, mock_gen):
        # Mock turn 1: Gemini calls search_parts tool
        fc = MagicMock()
        fc.name = "search_parts"
        fc.args = {"query": "brake pads", "vehicle": "Bajaj Pulsar 150"}

        part_fc = MagicMock()
        part_fc.function_call = fc
        part_fc.text = None

        cand1 = MagicMock()
        cand1.content.parts = [part_fc]
        resp1 = MagicMock()
        resp1.candidates = [cand1]

        # Mock turn 2: Gemini gives final reply citing BRK-1044
        part_text = MagicMock()
        part_text.function_call = None
        part_text.text = "Yes, we have Endurance Brake Pad Set (BRK-1044) in stock for Bajaj Pulsar 150 priced at ₹1459.50 with 136 units available."

        cand2 = MagicMock()
        cand2.content.parts = [part_text]
        resp2 = MagicMock()
        resp2.candidates = [cand2]

        mock_gen.side_effect = [resp1, resp2]

        payload = {
            "session_id": "test-session-123",
            "dealer_id": self.dealer.id,
            "message": "Do you have brake pads for a Pulsar 150?"
        }
        res = self.client.post("/api/assistant/chat/", payload, format="json")
        assert res.status_code == 200
        data = res.json()
        assert "BRK-1044" in data["cited_skus"]
        assert len(data["tool_calls"]) == 1
        assert data["tool_calls"][0]["name"] == "search_parts"
        assert "BRK-1044" in data["reply"]

    def test_missing_api_key_graceful_fallback(self):
        with patch("core.services.llm_provider.GeminiProvider.is_available", return_value=False):
            payload = {
                "session_id": "fallback-session",
                "dealer_id": self.dealer.id,
                "message": "Hello"
            }
            res = self.client.post("/api/assistant/chat/", payload, format="json")
            assert res.status_code == 200
            data = res.json()
            assert "reply" in data
            assert data["cited_skus"] == []
            assert data["tool_calls"] == []
            assert data["actions"] == []
