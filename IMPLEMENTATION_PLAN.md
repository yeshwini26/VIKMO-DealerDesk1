# VIKMO DealerDesk — Implementation Plan (AI/ML Track)

## 1. Project Overview & Track Declaration
- **Track**: `AI / ML Intern`
- **Primary Focus**:
  - **Backend Core**: Products, Inventory, Dealers, DRAFT Orders with strict validation and decimal money handling.
  - **Dealer Assistant (GenAI)**: Multi-step tool-calling loop using Google Gemini (`gemini-2.5-flash`), live database grounding (`search_parts`, `check_stock`, `create_draft_order`), session isolation, prompt injection defense.
  - **Forecasting & Reorder (ML)**: 4-week demand forecasting for 40 SKUs (`ml/forecast.csv` with exactly 160 rows), zero future leakage, rolling-origin backtesting vs. baselines, `GET /api/forecast/{sku}/`, and `GET /api/reorder-suggestions/` with exact safety stock formulas.
  - **Code Quality, Automated Testing, and Rigorous Documentation (`README.md`, `DECISIONS.md`)**.

---

## 2. Requirements Checklist

### Phase 1: Inspection & Foundation Setup
- [x] Inspect PDF assignment brief, `API_CONTRACT.md`, `data/DATA_README.md`, CSV files, and `smoke_test.py`.
- [x] Restructure workspace so `manage.py`, `requirements.txt`, `data/`, `smoke_test.py`, `supplier_mock/` are at the repository root.
- [x] Set up Python virtual environment (`.venv`) with required packages (`Django`, `DRF`, `psycopg[binary]`, `dj-database-url`, `google-genai`, `pandas`, `numpy`, `scikit-learn`, `pytest`, `pytest-django`).
- [x] Create `IMPLEMENTATION_PLAN.md`.

### Phase 2: Schema, Migrations, Seed Command & Backend Core APIs
- [ ] **Database Models (`core/models.py`)**:
  - `Product`: sku (unique primary key or indexed unique), name, category, brand, vehicle_fitment, price (`DecimalField`), description.
  - `Inventory`: product (OneToOne), quantity (integer, `>= 0` `CheckConstraint`), updated_at, reason.
  - `Dealer`: dealer_code (unique), name, city, phone, email, gstin, credit_limit (`DecimalField`).
  - `Order`: order_number (unique, format `ORD-YYYYMMDD-NNNN`), dealer (`ForeignKey`), status (`DRAFT`, `CONFIRMED`, `DISPATCHED`, `DELIVERED`, `CANCELLED`), subtotal, tax_total, grand_total, created_at, updated_at.
  - `OrderItem`: order (`ForeignKey`, `CASCADE`), sku, name, quantity (`>= 1`), unit_price, line_subtotal, line_tax (18% rounded `ROUND_HALF_UP`), line_total.
  - `IdempotencyRecord`: idempotency_key (unique), request_hash, response_code, response_body, order (`ForeignKey`, optional), created_at.
  - `ConversationSession` & `ConversationMessage`: session_id, dealer, role, content, tool_calls, actions, timestamp.
- [ ] **Database Migrations (`core/migrations/`)**:
  - Initial migration creating all tables, indexes, and constraints.
- [ ] **Seed Command (`core/management/commands/seed_data.py`)**:
  - Idempotent loading of `data/catalogue.csv` (600 products and starting inventory).
  - Idempotent loading of `data/dealers.csv` (25 dealers).
  - Re-running command produces zero duplicate records and zero errors.
- [ ] **Core Backend Endpoints**:
  - `GET /api/health/` -> `200 {"status": "ok"}`
  - `GET /api/products/` -> Paginated list (`count`, `next`, `previous`, `results`), filters: `search`, `category`, `vehicle`.
  - `POST /api/products/` -> Create product with stock 0, duplicate sku -> `409 DUPLICATE`, validation -> `400 VALIDATION_ERROR`.
  - `GET /api/products/{sku}/` -> Product object or `404 NOT_FOUND`.
  - `PATCH /api/products/{sku}/` -> Partial update, SKU immutable, returns product.
  - `PUT /api/inventory/{sku}/` -> Update stock quantity (`>= 0`), returns `{"sku": ..., "quantity": ...}`.
  - `GET /api/dealers/` -> Paginated list.
  - `POST /api/dealers/` -> Create dealer, duplicate dealer_code -> `409 DUPLICATE`.
  - `GET /api/dealers/{id}/` -> Dealer object or `404 NOT_FOUND`.
  - `POST /api/orders/` -> Create DRAFT order, exact money calculation (18% GST `ROUND_HALF_UP` per line), format `ORD-YYYYMMDD-NNNN`.
  - `GET /api/orders/` -> Paginated list with filters (`dealer_id`, `status`).
  - `GET /api/orders/{order_number}/` -> Order detail or `404 NOT_FOUND`.
  - `PUT /api/orders/{order_number}/` -> Replace items in DRAFT order only.
  - Consistent error format `{"error": {"code": "...", "message": "...", "details": []}}`.

### Phase 3: Dealer Assistant (GenAI)
- [ ] **Provider Abstraction (`core/services/llm_provider.py`)**:
  - Google Gemini integration using `google-genai` SDK and model `gemini-2.5-flash`.
  - Clean interface for tool definitions, message generation, and function-calling response handling.
  - Graceful fallback/error handling when provider or key is unavailable.
- [ ] **Assistant Tools (`core/services/assistant_tools.py`)**:
  - `search_parts`: Intelligent text and fitment search over database products, rank by vehicle and query token match, return SKUs and metadata.
  - `check_stock`: Live database query of available quantity and current price.
  - `create_draft_order`: Server-side validated DRAFT order creation reusing order business logic.
- [ ] **Multi-Step Tool-Calling Loop (`core/services/assistant.py`)**:
  - Conversation history per `session_id`.
  - Iterative tool execution loop (up to max iterations).
  - Accurate capture of `tool_calls`, `cited_skus`, and `actions` (`draft_order_created`).
  - Strict grounding: prompt injection resistance (treating descriptions as untrusted data), polite off-topic declines.
- [ ] **Endpoint `POST /api/assistant/chat/`**:
  - Request shape: `{"session_id": "...", "dealer_id": 7, "message": "..."}`.
  - Response shape: `{"reply": "...", "cited_skus": [...], "tool_calls": [...], "actions": [...]}`.

### Phase 4: Forecasting & Reorder (ML)
- [ ] **Forecasting Pipeline (`ml/train_forecast.py`)**:
  - Load `data/sales_history.csv`, `data/promo_calendar.csv`, `data/lead_times.csv`, `data/festival_calendar.csv`.
  - Implement rolling-origin temporal validation (zero future leakage).
  - Benchmark baselines: Last value, 4-week recent average, same week last year.
  - Feature engineering: Lagged demand (lag 1..4), rolling statistics, promo indicator, days/weeks relative to Diwali.
  - Robust forecasting models: Ridge/ElasticNet regularized regression with clip `>= 0`, fallback for short histories.
  - Generate deterministic predictions for the 4 forecast weeks (2026-09-28, 2026-10-05, 2026-10-12, 2026-10-19) for all 40 SKUs.
- [ ] **Forecast File (`ml/forecast.csv`)**:
  - Exactly 160 rows, columns: `sku,week_start,forecast_units`.
- [ ] **Forecast & Reorder Endpoints**:
  - `GET /api/forecast/{sku}/` -> 4 forecast entries matching `ml/forecast.csv`.
  - `GET /api/reorder-suggestions/` -> Exactly 40 items using the formula:
    - `L = lead_time_weeks`
    - `lead_time_demand = sum(forecast_units[0:L])`
    - `sigma = sample std dev (ddof=1) of units_sold over last 12 historical weeks`
    - `safety_stock = ceil(1.65 * sigma * sqrt(L))`
    - `reorder_point = lead_time_demand + safety_stock`
    - `reorder_qty = max(0, ceil(reorder_point - current_stock))`
    - `current_stock` retrieved live from database.

### Phase 5: Automated Testing & Verification
- [ ] Unit tests for models, money calculations, and stock constraints.
- [ ] Tests for product search, filtering, and pagination.
- [ ] Tests for order validation and draft creation.
- [ ] Unit tests with mocked LLM for assistant tool execution, session isolation, and grounding.
- [ ] Verification tests for `ml/forecast.csv` shape, non-negativity, and dates.
- [ ] Verification of reorder formulas against known inputs.
- [ ] Run `python smoke_test.py --base-url http://localhost:8000 --track aiml`.

### Phase 6: Documentation & Finalization
- [ ] Create comprehensive `DECISIONS.md` answering all 8 AI/ML track questions with precise file paths and line numbers.
- [ ] Update `README.md` with complete documentation, architecture, setup instructions, Gemini details, ML results, and test results.
