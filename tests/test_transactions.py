from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import StockTransaction
from schemas import MAX_QTY, SQLITE_INT_MAX


def post_txn(client, product_id, txn_type, qty):
    return client.post(
        "/transactions", json={"product_id": product_id, "txn_type": txn_type, "qty": qty}
    )


def stock_of(client, product_id):
    return client.get(f"/products/{product_id}").json()["current_stock"]


def test_receipt_then_sale_updates_stock(client, product_id):
    r = post_txn(client, product_id, "receipt", 50)
    assert r.status_code == 201
    assert r.json()["current_stock"] == 50

    r = post_txn(client, product_id, "sale", -8)
    assert r.status_code == 201
    assert r.json()["current_stock"] == 42
    assert stock_of(client, product_id) == 42


def test_oversell_returns_409_and_leaves_stock_unchanged(client, product_id):
    post_txn(client, product_id, "receipt", 50)
    post_txn(client, product_id, "sale", -8)

    r = post_txn(client, product_id, "sale", -100)
    assert r.status_code == 409
    assert r.json()["detail"] == (
        f"Insufficient stock for product {product_id}: current stock is 42, "
        "change of -100 would make it -58."
    )

    assert stock_of(client, product_id) == 42
    # The rejected sale must not be in the ledger either.
    assert len(client.get("/transactions", params={"product_id": product_id}).json()) == 2


def test_negative_adjustment_below_zero_returns_409(client, product_id):
    post_txn(client, product_id, "receipt", 5)
    assert post_txn(client, product_id, "adjustment", -6).status_code == 409
    assert post_txn(client, product_id, "adjustment", -5).json()["current_stock"] == 0


def test_unknown_product_returns_404(client):
    r = post_txn(client, 999, "receipt", 5)
    assert r.status_code == 404
    assert r.json()["detail"] == "Product 999 not found"


# Rule 2 for transactions: ck_txn_type, ck_txn_qty_nonzero, ck_txn_qty_sign, plus range caps.
@pytest.mark.parametrize(
    "txn_type, qty",
    [
        ("sale", 5),
        ("sale", 0),
        ("receipt", -5),
        ("receipt", 0),
        ("adjustment", 0),
        ("return", 5),
        ("receipt", MAX_QTY + 1),
        ("adjustment", -(MAX_QTY + 1)),
        ("receipt", SQLITE_INT_MAX + 1),
    ],
)
def test_invalid_transaction_returns_422(client, product_id, txn_type, qty):
    r = post_txn(client, product_id, txn_type, qty)
    assert r.status_code == 422
    assert stock_of(client, product_id) == 0


def test_oversized_product_id_returns_422(client):
    assert post_txn(client, SQLITE_INT_MAX + 1, "receipt", 5).status_code == 422


# ---------- Rule 4: txn_date is UTC with "Z" ----------


def test_txn_date_is_utc_with_z_suffix(client, product_id):
    posted = post_txn(client, product_id, "receipt", 5).json()["txn_date"]
    listed = client.get("/transactions").json()[0]["txn_date"]
    for value in (posted, listed):
        assert value.endswith("Z")
        assert datetime.fromisoformat(value).utcoffset().total_seconds() == 0


# ---------- Rule 5: GET /transactions ordering ----------


def test_list_orders_by_txn_date_desc_then_txn_id_desc(client, engine, product_id):
    # Insert out of date order, with a tie on the latest date, to test both sort keys.
    rows = [
        ("2026-11-01 09:00:00", 10),
        ("2026-11-03 09:00:00", 20),
        ("2026-11-02 09:00:00", 30),
        ("2026-11-03 09:00:00", 40),
    ]
    with Session(engine) as db:
        for date, qty in rows:
            db.add(
                StockTransaction(
                    product_id=product_id,
                    txn_type="receipt",
                    qty=qty,
                    txn_date=datetime.fromisoformat(date),
                )
            )
            db.flush()
        db.commit()

    listed = client.get("/transactions", params={"product_id": product_id}).json()
    assert [t["qty"] for t in listed] == [40, 20, 30, 10]
    assert listed[0]["txn_date"] == "2026-11-03T09:00:00Z"
    assert client.get("/transactions", params={"limit": 2}).json() == listed[:2]


# ---------- Append-only triggers ----------


@pytest.mark.parametrize(
    "sql",
    ["UPDATE stock_transactions SET qty = 999", "DELETE FROM stock_transactions"],
)
def test_ledger_rows_cannot_be_updated_or_deleted(client, engine, product_id, sql):
    post_txn(client, product_id, "receipt", 50)

    with pytest.raises(IntegrityError, match="append-only"):
        with engine.begin() as conn:
            conn.execute(text(sql))

    assert stock_of(client, product_id) == 50
