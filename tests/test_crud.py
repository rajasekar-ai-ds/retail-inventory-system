import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import Product
from schemas import SQLITE_INT_MAX

TOO_BIG = SQLITE_INT_MAX + 1


def new_supplier(client, **overrides):
    body = {"name": "Metro Wholesale", "lead_time_days": 5} | overrides
    return client.post("/suppliers", json=body)


# ---------- Rule 1: delete guards + passive_deletes ----------


def test_delete_supplier_with_products_returns_409(client, product_id):
    supplier_id = client.get(f"/products/{product_id}").json()["supplier_id"]
    r = client.delete(f"/suppliers/{supplier_id}")
    assert r.status_code == 409
    assert "still has products" in r.json()["detail"]
    assert client.get(f"/suppliers/{supplier_id}").status_code == 200


def test_delete_product_with_transactions_returns_409(client, product_id):
    client.post("/transactions", json={"product_id": product_id, "txn_type": "receipt", "qty": 5})
    r = client.delete(f"/products/{product_id}")
    assert r.status_code == 409
    assert "has stock transactions" in r.json()["detail"]
    assert client.get(f"/products/{product_id}").status_code == 200


def test_delete_unused_supplier_and_product_returns_204(client, product_id):
    supplier_id = client.get(f"/products/{product_id}").json()["supplier_id"]
    assert client.delete(f"/products/{product_id}").status_code == 204
    assert client.get(f"/products/{product_id}").status_code == 404
    assert client.delete(f"/suppliers/{supplier_id}").status_code == 204
    assert client.get(f"/suppliers/{supplier_id}").status_code == 404


def test_orm_delete_does_not_null_out_children(client, engine, product_id):
    # Bypass the API's exists() guard. Without passive_deletes="all" the ORM would first
    # UPDATE the child rows' product_id to NULL and hit the append-only trigger; with it,
    # the ORM issues the DELETE and SQLite's ON DELETE RESTRICT refuses it.
    client.post("/transactions", json={"product_id": product_id, "txn_type": "receipt", "qty": 5})
    with Session(engine) as db:
        product = db.get(Product, product_id)
        assert len(product.transactions) == 1  # loaded, so the ORM *could* try to nullify them
        db.delete(product)
        with pytest.raises(IntegrityError, match="FOREIGN KEY constraint failed"):
            db.commit()


# ---------- Rule 2: every DB CHECK has a Pydantic rule -> 422, never 500 ----------


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": ""},
        {"name": "   "},
        {"lead_time_days": -1},  # ck_suppliers_lead_time_nonneg
        {"lead_time_days": TOO_BIG},
        {"unexpected": 1},
    ],
)
def test_bad_supplier_input_returns_422(client, overrides):
    assert new_supplier(client, **overrides).status_code == 422
    assert client.get("/suppliers").json() == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": ""},
        {"name": "   "},
        {"unit_price": -1},  # ck_products_price_nonneg
        {"unit_price": TOO_BIG},
        {"supplier_id": TOO_BIG},
        {"current_stock": 500},  # ProductCreate has no current_stock
    ],
)
def test_bad_product_input_returns_422(client, overrides):
    supplier_id = new_supplier(client).json()["supplier_id"]
    body = {"supplier_id": supplier_id, "name": "Toor Dal 1kg", "unit_price": 16000} | overrides
    assert client.post("/products", json=body).status_code == 422
    assert client.get("/products").json() == []


@pytest.mark.parametrize(
    "method, path",
    [
        ("get", f"/suppliers/{TOO_BIG}"),
        ("delete", f"/suppliers/{TOO_BIG}"),
        ("get", f"/products/{TOO_BIG}"),
        ("delete", f"/products/{TOO_BIG}"),
        ("get", f"/transactions?product_id={TOO_BIG}"),
    ],
)
def test_oversized_ids_in_url_return_422(client, method, path):
    assert client.request(method, path).status_code == 422


# ---------- Rule 3: response ids ----------


def test_responses_use_supplier_id_and_product_id(client, product_id):
    supplier = client.get("/suppliers").json()[0]
    product = client.get(f"/products/{product_id}").json()
    assert set(supplier) == {"supplier_id", "name", "phone", "lead_time_days"}
    assert set(product) == {"product_id", "supplier_id", "name", "unit_price", "current_stock"}
    assert product["product_id"] == product_id
    assert product["current_stock"] == 0


# ---------- Rule 6: PUT /products with unknown supplier ----------


def test_put_product_with_unknown_supplier_returns_404(client, product_id):
    r = client.put(
        f"/products/{product_id}",
        json={"supplier_id": 999, "name": "Atta 5kg", "unit_price": 28500},
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "Supplier 999 not found"
    assert client.get(f"/products/{product_id}").json()["supplier_id"] != 999


def test_put_product_updates_fields(client, product_id):
    other = new_supplier(client).json()["supplier_id"]
    r = client.put(
        f"/products/{product_id}",
        json={"supplier_id": other, "name": "Atta 10kg", "unit_price": 55000},
    )
    assert r.status_code == 200
    assert r.json() == {
        "product_id": product_id,
        "supplier_id": other,
        "name": "Atta 10kg",
        "unit_price": 55000,
        "current_stock": 0,
    }
