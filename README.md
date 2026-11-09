# Retail Inventory System

A simple retail inventory management API built with FastAPI.

## Features

- Add, update, and remove products
- Track stock levels
- View the product list

## Setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload
```

Then open:

- http://localhost:8000 – the DSS dashboard (served from `frontend/`)
- http://localhost:8000/docs – interactive API docs

`inventory.db` is created and seeded with demo data on first startup. Delete it to reset.
The dashboard loads Chart.js from a CDN, so the chart needs internet access; everything else works offline.

## Tech Stack

- Python
- FastAPI
- Uvicorn
- Frontend: plain HTML/CSS/JS + Chart.js (no build step)

## Database schema

SQLite (`inventory.db`, created and seeded with demo data on first startup).

| Table | Column | Type | Notes |
|---|---|---|---|
| suppliers | id | INTEGER PK | |
| | name | TEXT NOT NULL | |
| | phone | TEXT NULL | |
| | lead_time_days | INTEGER NOT NULL | CHECK >= 0 |
| products | id | INTEGER PK | |
| | supplier_id | INTEGER NOT NULL | FK → suppliers.id, ON DELETE RESTRICT |
| | name | TEXT NOT NULL | |
| | unit_price | INTEGER NOT NULL | in paise, CHECK >= 0 |
| | current_stock | INTEGER NOT NULL DEFAULT 0 | cache of SUM(qty), maintained by trigger, CHECK >= 0 |
| stock_transactions | txn_id | INTEGER PK | append-only (UPDATE/DELETE blocked by triggers) |
| | product_id | INTEGER NOT NULL | FK → products.id, ON DELETE RESTRICT |
| | txn_type | TEXT NOT NULL | `sale` \| `receipt` \| `adjustment` |
| | txn_date | DATETIME NOT NULL | UTC, defaults to CURRENT_TIMESTAMP |
| | qty | INTEGER NOT NULL | signed, non-zero: sale < 0, receipt > 0, adjustment either |

## API contract

All errors use FastAPI's shape: `{"detail": "<message>"}`. Validation errors (422)
use FastAPI's default `{"detail": [ ... ]}` list. Unknown fields in request bodies are rejected with 422.
Timestamps are UTC, ISO 8601 with a `Z` suffix (e.g. `"2026-11-08T10:15:00Z"`).

Every DB CHECK has a matching request rule, so bad input is always 422, never 500:

| Field | Rule |
|---|---|
| `name` | non-empty after trimming whitespace |
| `lead_time_days` | 0 – 365 |
| `unit_price` | 0 – 1,000,000,000 paise (₹1 crore) |
| `qty` | non-zero, −1,000,000 – 1,000,000, sign rules below |
| `txn_type` | `sale` \| `receipt` \| `adjustment` |
| any id (body, path, query) | 1 – 2⁶³−1 (SQLite's INTEGER range) |

### Suppliers

| Method | Path | Body | Success | Errors |
|---|---|---|---|---|
| GET | `/suppliers` | – | 200 `[Supplier]` | |
| POST | `/suppliers` | `SupplierCreate` | 201 `Supplier` | 422 |
| GET | `/suppliers/{supplier_id}` | – | 200 `Supplier` | 404 |
| PUT | `/suppliers/{supplier_id}` | `SupplierCreate` (full replace) | 200 `Supplier` | 404, 422 |
| DELETE | `/suppliers/{supplier_id}` | – | 204 | 404, 409 if it still has products |

```json
// SupplierCreate
{ "name": "Sharma Traders", "phone": "+91-9876543210", "lead_time_days": 3 }

// Supplier
{ "supplier_id": 1, "name": "Sharma Traders", "phone": "+91-9876543210", "lead_time_days": 3 }
```

### Products

| Method | Path | Body | Success | Errors |
|---|---|---|---|---|
| GET | `/products` | – | 200 `[Product]` | |
| POST | `/products` | `ProductCreate` | 201 `Product` (`current_stock` = 0) | 404 unknown supplier, 422 |
| GET | `/products/{product_id}` | – | 200 `Product` | 404 |
| PUT | `/products/{product_id}` | `ProductCreate` (full replace) | 200 `Product` | 404 unknown product or supplier, 422 |
| DELETE | `/products/{product_id}` | – | 204 | 404, 409 if it has transactions |

`ProductCreate` has no `current_stock`; stock only changes through `POST /transactions`.

```json
// ProductCreate
{ "supplier_id": 1, "name": "Atta 5kg", "unit_price": 28500 }

// Product
{ "product_id": 1, "supplier_id": 1, "name": "Atta 5kg", "unit_price": 28500, "current_stock": 40 }
```

### Transactions

| Method | Path | Body | Success | Errors |
|---|---|---|---|---|
| POST | `/transactions` | `TransactionCreate` | 201 `TransactionResult` | 404 unknown product, 409 stock would go negative, 422 |
| GET | `/transactions?product_id={id}&limit={n}` | – | 200 `[Transaction]`, ordered `txn_date` DESC, `txn_id` DESC | 422 |

`product_id` is optional; `limit` defaults to 100 (1–1000). There is no update or delete:
correct mistakes by posting an `adjustment`.

`qty` rules (422 otherwise): never 0; `sale` must be negative; `receipt` must be positive;
`adjustment` may be either sign.

```json
// TransactionCreate
{ "product_id": 1, "txn_type": "sale", "qty": -8 }

// Transaction
{ "txn_id": 12, "product_id": 1, "txn_type": "sale", "txn_date": "2026-11-08T10:15:00Z", "qty": -8 }

// TransactionResult = Transaction + product's stock after the insert
{ "txn_id": 12, "product_id": 1, "txn_type": "sale", "txn_date": "2026-11-08T10:15:00Z", "qty": -8, "current_stock": 42 }

// 409
{ "detail": "Insufficient stock for product 1: current stock is 42, change of -100 would make it -58." }
```

## SCHEMAS for JSON
[
  {
    "product_id": 1,
    "name": "Atta 5kg",
    "current_stock": 40,
    "avg_daily_demand": 8.0,
    "lead_time_days": 3,
    "safety_stock": 18,
    "reorder_point": 42,
    "status": "REORDER"
  }
]

[
  {
    "product_id": 1,
    "name": "Atta 5kg",
    "current_stock": 40,
    "avg_daily_demand": 5.3,
    "days_of_cover_avg": 7.5,
    "days_of_cover_worst": 2.9,
    "status": "OK"
  },
  {
    "product_id": 7,
    "name": "Rose Biscuits",
    "current_stock": 60,
    "avg_daily_demand": 0,
    "days_of_cover_avg": null,
    "days_of_cover_worst": null,
    "status": "NO_SALES"
  }
]

