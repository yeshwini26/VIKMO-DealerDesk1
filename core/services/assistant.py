import re
import logging
from typing import Any
from google.genai import types

from core.models import ConversationMessage, Dealer, Product
from core.services import assistant_tools
from core.services.llm_provider import GeminiProvider

logger = logging.getLogger(__name__)

SKU_PATTERN = re.compile(r"\b[A-Z]{3}-\d{4}\b")


def extract_cited_skus(text: str) -> list[str]:
    """
    Find all SKU patterns in the reply text and verify they exist in the database.
    Only real, existing database SKUs are included.
    """
    if not text:
        return []
    candidates = set(SKU_PATTERN.findall(text))
    if not candidates:
        return []
    existing = set(
        Product.objects.filter(sku__in=list(candidates)).values_list("sku", flat=True)
    )
    # Maintain first-seen order
    ordered = []
    for c in candidates:
        if c in existing and c not in ordered:
            ordered.append(c)
    return ordered


def handle_chat_turn(session_id: str, dealer_id: int, message: str) -> dict:
    """
    Orchestrates the multi-turn conversational loop with Gemini tool-calling.
    """
    dealer = None
    if dealer_id:
        dealer = Dealer.objects.filter(id=dealer_id).first()

    provider = GeminiProvider()

    # If provider is not available (e.g. no GEMINI_API_KEY), return graceful fallback
    if not provider.is_available():
        reply = (
            "DealerDesk Assistant is running, but the AI provider key (GEMINI_API_KEY) "
            "is not configured in the environment. Please configure GEMINI_API_KEY to enable AI chat."
        )
        return {
            "reply": reply,
            "cited_skus": [],
            "tool_calls": [],
            "actions": [],
        }

    # Load session history
    past_messages = ConversationMessage.objects.filter(session_id=session_id).order_by("created_at")[:10]

    contents = []
    for m in past_messages:
        role = "user" if m.role == "user" else "model"
        contents.append(types.Content(role=role, parts=[types.Part.from_text(text=m.content)]))

    # Add current user message
    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=message)]))

    tool_calls_executed = []
    actions_performed = []
    final_reply = ""
    max_iterations = 5

    try:
        for _ in range(max_iterations):
            response = provider.generate_turn(contents=contents)
            candidate = response.candidates[0] if response.candidates else None
            if not candidate or not candidate.content or not candidate.content.parts:
                final_reply = "I could not generate a response. Please try again."
                break

            function_calls = []
            text_parts = []
            for part in candidate.content.parts:
                if part.function_call:
                    function_calls.append(part.function_call)
                elif part.text:
                    text_parts.append(part.text)

            if not function_calls:
                final_reply = "".join(text_parts).strip()
                break

            # Model returned function calls; execute them
            contents.append(candidate.content)
            response_parts = []

            for fc in function_calls:
                fn_name = fc.name
                fn_args = dict(fc.args) if fc.args else {}

                tool_calls_executed.append({
                    "name": fn_name,
                    "arguments": fn_args,
                })

                if fn_name == "search_parts":
                    q = fn_args.get("query", "")
                    v = fn_args.get("vehicle")
                    tool_result = assistant_tools.search_parts(query=q, vehicle=v)
                elif fn_name == "check_stock":
                    s = fn_args.get("sku", "")
                    tool_result = assistant_tools.check_stock(sku=s)
                elif fn_name == "create_draft_order":
                    d_id = fn_args.get("dealer_id") or dealer_id
                    s = fn_args.get("sku", "")
                    q = fn_args.get("quantity", 1)
                    if isinstance(q, str):
                        try:
                            q = int(q)
                        except ValueError:
                            q = 1
                    tool_result = assistant_tools.create_draft_order(dealer_id=d_id, sku=s, quantity=q)
                    if tool_result.get("success"):
                        actions_performed.append({
                            "type": "draft_order_created",
                            "order_number": tool_result["order_number"],
                        })
                else:
                    tool_result = {"error": f"Unknown tool: {fn_name}"}

                response_parts.append(
                    types.Part.from_function_response(
                        name=fn_name,
                        response={"result": tool_result},
                    )
                )

            contents.append(types.Content(role="user", parts=response_parts))

    except Exception as e:
        logger.exception("Error in assistant chat execution: %s", e)
        final_reply = f"An error occurred while processing your request: {str(e)}"

    cited_skus = extract_cited_skus(final_reply)

    # Persist turn in conversation memory
    ConversationMessage.objects.create(
        session_id=session_id,
        dealer=dealer,
        role="user",
        content=message,
    )
    ConversationMessage.objects.create(
        session_id=session_id,
        dealer=dealer,
        role="assistant",
        content=final_reply,
        tool_calls=tool_calls_executed,
        actions=actions_performed,
        cited_skus=cited_skus,
    )

    return {
        "reply": final_reply,
        "cited_skus": cited_skus,
        "tool_calls": tool_calls_executed,
        "actions": actions_performed,
    }
