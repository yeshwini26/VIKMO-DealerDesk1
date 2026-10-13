# VIKMO DealerDesk — AI/ML Take-Home Assignment

**Track:** `aiml` (AI / ML Intern)  
**Submission:** GitHub Repository & Video Walkthrough  
**Video Walkthrough Link:** `[PENDING SUBMISSION - INSERT 5-MINUTE LOOM / UNLISTED YOUTUBE LINK HERE]`  
**Repository URL:** `[PENDING SUBMISSION - INSERT PUBLIC GITHUB REPO URL HERE]`  

---

## 1. Overview & Business Problem

VIKMO DealerDesk is an AI-native commerce operating system designed for South Indian automotive and motorcycle parts distributors supplying 25 regional dealers from a catalogue of 600 products.

The distributor historically suffered from four major operational bottlenecks:
1. **Overselling and GST errors:** Concurrent orders resulted in overselling the last available units, and floating-point errors caused accounting mismatches.
2. **Stale inventory and pricing:** Manual updates caused discrepancies with supplier feeds.
3. **Repetitive dealer inquiries:** Staff spent hours manually answering fitment, stock, and price questions over phone calls and WhatsApp.
4. **Guesswork in reordering:** Seasonality, moving Diwali festive dates, and promotion spikes caused either costly stockouts or inventory sitting idle.

DealerDesk provides a robust solution:
- **Backend Core (`A1` + `A2`):** Normalized PostgreSQL schema, transactional stock locking, exact 18% GST calculation (`ROUND_HALF_UP`), and strict idempotency.
- **Dealer Assistant (`C`):** Multi-step tool-calling conversational agent powered by Google Gemini (`gemini-2.5-flash`), live database grounding (`search_parts`, `check_stock`, `create_draft_order`), prompt injection resistance, and isolated session memory.
- **Demand Forecasting & Reorder (`D`):** Chronologically backtested demand forecaster generating `ml/forecast.csv` (160 rows, 40 SKUs across 4 target weeks) with zero future leakage, beating simple baselines by 2.5–3.7% WAPE, alongside live safety stock reorder recommendations.
- **Supplier Sync (`B` — Bonus):** Paginated supplier synchronization with rate limiting (`Retry-After`), retry backoff, duplicate resolution by `updated_at`, and all-or-nothing transactions.

---

## 2. Architecture & Tech Stack

```
DealerDesk Architecture
├── core/
│   ├── models.py                  # Product, Inventory, Dealer, Order, OrderItem, IdempotencyRecord, ConversationMessage
│   ├── serializers.py             # DRF serializers with exact error formatting and price validation
│   ├── views.py                   # REST endpoints (Products, Inventory, Dealers, Orders, Assistant, Forecast, Sync)
│   ├── urls.py                    # API route dispatching
│   ├── exceptions.py              # Custom exceptions & DRF error handler matching {"error": {"code", "message", "details"}}
│   ├── pagination.py              # StandardResultsSetPagination (page_size=20, max=100)
│   ├── management/commands/       # seed_data.py (idempotent catalogue & dealers loader)
│   └── services/
│       ├── orders.py              # Order draft creation, stock locking, transitions, money calculation
│       ├── assistant_tools.py     # search_parts, check_stock, create_draft_order
│       ├── assistant.py           # Multi-step conversational tool loop, grounding, memory
│       ├── llm_provider.py        # Gemini API client abstraction (gemini-2.5-flash)
│       ├── forecasting.py         # Forecast lookup & reorder formulas
│       └── supplier_sync.py       # Resilient supplier API synchronization
├── ml/
│   ├── train_forecast.py          # Reproducible forecast training and backtesting pipeline
│   └── forecast.csv               # 160 rows (40 SKUs x 4 weeks), strictly non-negative
├── tests/                         # Comprehensive pytest test suite (24 tests)
├── data/                          # Untrusted input CSV datasets
├── dealerdesk/                    # Django project configuration (settings.py, urls.py, wsgi.py)
├── manage.py                      # Django management root
├── requirements.txt               # Python dependencies
├── smoke_test.py                  # Contract verification script
├── API_CONTRACT.md                # Authoritative API contract
├── DECISIONS.md                   # 8 mandatory AI/ML track architecture decisions
└── IMPLEMENTATION_PLAN.md         # Requirements checklist & execution plan
```

### Technology Details:
- **Language:** Python 3.11+
- **Backend:** Django 4.2+ / Django 5.2, Django REST Framework
- **Database:** PostgreSQL (with SQLite testing fallback for isolated offline environments)
- **Database Driver:** `psycopg[binary]>=3.1`, `dj-database-url>=2.1`
- **GenAI Provider:** Google Gemini API (`google-genai>=0.1.1`), Model: **`gemini-2.5-flash`**
- **Machine Learning:** `pandas>=2.0`, `numpy>=1.24`, `scikit-learn>=1.3`
- **Testing:** `pytest>=8.0`, `pytest-django>=4.8`, `requests>=2.31`

---

## 3. Database Schema & Models

1. **`Product`:** `sku` (unique, indexed), `name`, `category`, `brand`, `vehicle_fitment`, `price` (Decimal), `description`.
2. **`Inventory`:** `product` (`OneToOneField`, `on_delete=models.CASCADE`), `quantity` (`CheckConstraint(condition=Q(quantity__gte=0))`).
3. **`Dealer`:** `dealer_code` (unique, indexed), `name`, `city`, `phone`, `email`, `gstin`, `credit_limit` (Decimal).
4. **`Order`:** `order_number` (unique, indexed `ORD-YYYYMMDD-NNNN`), `dealer` (`ForeignKey`, `on_delete=models.PROTECT`), `status` (`DRAFT`, `CONFIRMED`, `DISPATCHED`, `DELIVERED`, `CANCELLED`), financial totals (`subtotal`, `tax_total`, `grand_total`).
5. **`OrderItem`:** `order` (`ForeignKey`, `on_delete=models.CASCADE`), `product` (`ForeignKey`, `on_delete=models.PROTECT`), denormalized `sku` and `name`, `quantity` (`>= 1` constraint), unit price and line totals.
6. **`IdempotencyRecord`:** `idempotency_key` (unique), SHA-256 `request_hash`, cached response status and payload.
7. **`ConversationMessage`:** `session_id`, `dealer`, `role`, `content`, `tool_calls`, `actions`, `cited_skus`.

*All monetary values are calculated using `decimal.Decimal` and rounded to 2 decimal places using `ROUND_HALF_UP`.*

---

## 4. Setup & Running

### Prerequisites
- Python 3.11 or newer
- PostgreSQL instance (local or hosted, e.g. Neon / Supabase)

### 1. Environment Setup
Create a virtual environment and install dependencies:
```bash
python -m venv .venv
source .venv/bin/activate       # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure Environment Variables
Copy `.env.example` to `.env` (never commit `.env`):
```bash
cp .env.example .env
```
Populate `.env` with your values:
```ini
DATABASE_URL=postgres://user:password@localhost:5432/dealerdesk
GEMINI_API_KEY=your-gemini-api-key
GEMINI_MODEL=gemini-2.5-flash
SUPPLIER_API_URL=http://127.0.0.1:8099
SUPPLIER_API_KEY=dev-supplier-key
```

### 3. Startup Execution Sequence
The submission adheres strictly to the required startup sequence:
```bash
python manage.py migrate --noinput
python manage.py seed_data
python manage.py seed_data
python manage.py runserver 0.0.0.0:8000 --noreload
```
*`seed_data` is completely idempotent. Running it twice loads exactly 600 catalogue products and 25 dealers with zero duplicates.*

---

## 5. API Reference & Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/health/` | Health check (`{"status": "ok"}`) |
| `GET` | `/api/products/` | Paginated product list with `search`, `category`, and `vehicle` filters |
| `POST` | `/api/products/` | Create product with stock 0; duplicate returns `409 DUPLICATE` |
| `GET` | `/api/products/{sku}/` | Product details with live stock |
| `PATCH` | `/api/products/{sku}/` | Partial product update (SKU is immutable) |
| `PUT` | `/api/inventory/{sku}/` | Set live stock (`quantity >= 0`); returns updated stock |
| `GET` | `/api/dealers/` | Paginated list of dealers |
| `POST` | `/api/dealers/` | Create dealer; duplicate code returns `409 DUPLICATE` |
| `GET` | `/api/dealers/{id}/` | Dealer details |
| `POST` | `/api/orders/` | Create DRAFT order with current catalogue prices (optional `Idempotency-Key`) |
| `GET` | `/api/orders/` | Paginated orders list with `dealer_id` and `status` filters |
| `GET` | `/api/orders/{order_number}/` | Order details with item breakdown and exact tax |
| `PUT` | `/api/orders/{order_number}/` | Replace items in DRAFT order |
| `POST` | `/api/orders/{order_number}/confirm/` | DRAFT → CONFIRMED with atomic stock deduction |
| `POST` | `/api/orders/{order_number}/cancel/` | Cancel order (restores stock if confirmed) |
| `POST` | `/api/assistant/chat/` | Multi-turn conversational Dealer Assistant with Gemini tool-calling |
| `GET` | `/api/forecast/{sku}/` | 4-week forecast matching `ml/forecast.csv` |
| `GET` | `/api/reorder-suggestions/` | Reorder suggestions for 40 SKUs based on live database stock |
| `POST` | `/api/sync/supplier/` | Resilient supplier catalogue synchronization |

### Error Format
All errors return the contract-compliant structure:
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Human-readable explanation",
    "details": []
  }
}
```

---

## 6. Dealer Assistant (GenAI)

- **Provider:** Google Gemini API (`google-genai` SDK)
- **Model:** `gemini-2.5-flash`
- **Tool Calling Interface:**
  1. `search_parts(query, vehicle)`: Grounded multi-attribute token and vehicle fitment matching against live products.
  2. `check_stock(sku)`: Live PostgreSQL query of current quantity and price at execution time.
  3. `create_draft_order(dealer_id, sku, quantity)`: Server-validated draft order creation reusing shared order business logic.
- **Safety & Injection Defense:**
  - System prompt establishes catalogue text as untrusted data.
  - Descriptions are truncated and structured in JSON parameters.
  - Off-topic queries are politely declined.
  - All quoted prices and stocks originate from database tools.
  - `cited_skus` verifies all mentioned SKUs against actual database products.
  - Session history is strictly isolated per `session_id`.

---

## 7. Demand Forecasting & Backtesting (ML)

### Dataset
- `data/sales_history.csv`: ~4,000 weekly observations for 40 SKUs (2024-09-30 to 2026-09-21)
- `data/promo_calendar.csv`: Planned promotions for the 4 forecast weeks
- `data/festival_calendar.csv`: Diwali festival dates (2024, 2025, 2026)
- `data/lead_times.csv`: Supplier lead times (1–4 weeks) for the 40 forecast SKUs

### Target Forecast Horizon
- **Weeks:** `2026-09-28`, `2026-10-05`, `2026-10-12`, `2026-10-19`
- **File:** `ml/forecast.csv` (exactly 160 rows, 40 SKUs, non-negative)
- **Pipeline:** `python ml/train_forecast.py`

### Chronological Rolling-Origin Backtesting Results
Zero future leakage: training sets strictly exclude observations after each cutoff origin.

| Validation Cutoff | Baseline 1 (Last Value) | Baseline 2 (Recent 4-Wk Avg) | Baseline 3 (Lag-52 Prev Year) | DealerDesk Proposed Model | Improvement Over Best Baseline |
|---|---|---|---|---|---|
| **2026-07-27** (4 wks) | MAE 11.88 / WAPE 33.50% | MAE 9.76 / WAPE 27.52% | MAE 11.48 / WAPE 32.36% | **MAE 8.46 / WAPE 23.84%** (RMSE 12.35) | **+3.68% points** |
| **2026-08-24** (4 wks) | MAE 11.72 / WAPE 34.41% | MAE 8.76 / WAPE 25.71% | MAE 12.27 / WAPE 36.01% | **MAE 7.91 / WAPE 23.21%** (RMSE 11.91) | **+2.50% points** |

---

## 8. Automated Tests & Verification

Run the full pytest suite:
```bash
pytest -v
```
**Test Results:** `24 passed in 14.74s`

Run the official contract smoke test:
```bash
python smoke_test.py --base-url http://localhost:8000 --track aiml
```
**Smoke Test Result:** `10/10 checks passed`

---

## 9. Technical Decisions Documentation
See [`DECISIONS.md`](file:///DECISIONS.md) for complete answers to all 8 technical review questions, including exact code citations and file paths.
