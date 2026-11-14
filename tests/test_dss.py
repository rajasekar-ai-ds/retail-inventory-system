from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from dss import WINDOW_DAYS
from models import StockTransaction


def make_product(client, engine, *, lead_time, stock, sales=(), name="Item"):
    """Create a product ending at `stock` units, with `sales` = [(days_ago, units), ...].

    Sales are inserted straight into the DB because the API always stamps txn_date as "now".
    """
    supplier = client.post("/suppliers", json={"name": "S", "lead_time_days": lead_time}).json()
    pid = client.post(
        "/products", json={"supplier_id": supplier["supplier_id"], "name": name, "unit_price": 100}
    ).json()["product_id"]

    total_sold = sum(units for _, units in sales)
    if stock + total_sold:
        client.post(
            "/transactions",
            json={"product_id": pid, "txn_type": "receipt", "qty": stock + total_sold},
        )

    # 01:00 UTC on each day: inside the calendar day, and never later than now-ish.
    today = datetime.now(timezone.utc).replace(tzinfo=None, hour=1, minute=0, second=0, microsecond=0)
    with Session(engine) as db:
        for days_ago, units in sales:
            db.add(
                StockTransaction(
                    product_id=pid,
                    txn_type="sale",
                    qty=-units,
                    txn_date=today - timedelta(days=days_ago),
                )
            )
        db.commit()
    return pid


def alert(client, pid):
    r = client.get(f"/dss/alerts/{pid}")
    assert r.status_code == 200
    return r.json()


def every_day(units):
    return [(day, units) for day in range(WINDOW_DAYS)]


# ---------- One test per status ----------


def test_critical(client, engine):
    # d = 2, max = 2 -> safety 0, ROP 10. Stock 3 covers 1.5 days < 5-day lead time.
    pid = make_product(client, engine, lead_time=5, stock=3, sales=every_day(2))
    a = alert(client, pid)
    assert a["status"] == "CRITICAL"
    assert a["avg_daily_demand"] == 2.0
    assert a["max_daily_demand"] == 2
    assert a["days_of_cover"] == 1.5
    assert a["safety_stock"] == 0
    assert a["reorder_point"] == 10
    assert a["recommended_order_qty"] == 21  # ceil(2 * (5 + 7) + 0 - 3)
    assert a["reason"] == "Stock 3 lasts ~1.5 days; supplier needs 5 days -> order now."


def test_reorder(client, engine):
    # 14 units on one day: d = 2, max = 14 -> safety (14 - 2) * 3 = 36, ROP 6 + 36 = 42.
    # Stock 40 covers 20 days (>= 3) but is <= 42.
    pid = make_product(client, engine, lead_time=3, stock=40, sales=[(2, 14)])
    a = alert(client, pid)
    assert a["status"] == "REORDER"
    assert a["max_daily_demand"] == 14
    assert a["safety_stock"] == 36
    assert a["reorder_point"] == 42
    assert a["days_of_cover"] == 20.0
    assert a["recommended_order_qty"] == 16  # ceil(2 * (3 + 7) + 36 - 40)


def test_ok(client, engine):
    # d = 2, max = 2 -> ROP 6. Stock 50 covers 25 days.
    pid = make_product(client, engine, lead_time=3, stock=50, sales=every_day(2))
    a = alert(client, pid)
    assert a["status"] == "OK"
    assert a["days_of_cover"] == 25.0
    assert a["recommended_order_qty"] == 0


def test_no_sales(client, engine):
    # A sale just outside the window must not count as demand.
    pid = make_product(client, engine, lead_time=3, stock=10, sales=[(WINDOW_DAYS, 5)])
    a = alert(client, pid)
    assert a["status"] == "NO_SALES"
    assert a["avg_daily_demand"] == 0
    assert a["days_of_cover"] is None
    assert a["recommended_order_qty"] == 0


# ---------- d == 0 ----------


def test_zero_demand_does_not_divide_by_zero(client, engine):
    # Stock 0, lead time 0, and a negative adjustment (not a sale, so not demand).
    pid = make_product(client, engine, lead_time=0, stock=0)
    client.post("/transactions", json={"product_id": pid, "txn_type": "receipt", "qty": 4})
    client.post("/transactions", json={"product_id": pid, "txn_type": "adjustment", "qty": -4})

    a = alert(client, pid)
    assert a["stock"] == 0
    assert a["status"] == "NO_SALES"
    assert a["days_of_cover"] is None
    assert a["safety_stock"] == 0
    assert a["reorder_point"] == 0
    assert a["recommended_order_qty"] == 0
    assert client.get("/dss/alerts").status_code == 200


def test_order_qty_is_exact_not_float_rounded(client, engine):
    # d = 8/7. With floats, 8/7 * 14 = 16.000000000000004 and ceil would give 55, not 54.
    # max 8 -> safety (8 - 8/7) * 7 = 48, ROP 8 + 48 = 56; stock 10 -> REORDER.
    pid = make_product(client, engine, lead_time=7, stock=10, sales=[(0, 8)])
    a = alert(client, pid)
    assert a["status"] == "REORDER"
    assert a["recommended_order_qty"] == 54  # ceil(8/7 * 14 + 48 - 10)


def test_days_of_cover_matches_reason(client, engine):
    # d = 11/7, stock 15 -> exact cover 9.5454... Rounding twice (9.55, then 9.6) used to disagree
    # with the reason's 9.5; both must show the one rounded value.
    pid = make_product(client, engine, lead_time=3, stock=15, sales=[(0, 11)])
    a = alert(client, pid)
    assert a["days_of_cover"] == 9.5
    assert f"lasts ~{a['days_of_cover']:.1f} days" in a["reason"]


# ---------- Sorting and summary ----------


def test_alerts_sorted_by_status_then_days_of_cover(client, engine):
    ok = make_product(client, engine, lead_time=3, stock=50, sales=every_day(2), name="ok")
    none = make_product(client, engine, lead_time=3, stock=10, name="none")
    crit_2 = make_product(client, engine, lead_time=5, stock=4, sales=every_day(2), name="crit_2")
    reorder = make_product(client, engine, lead_time=3, stock=40, sales=[(2, 14)], name="reorder")
    crit_1 = make_product(client, engine, lead_time=5, stock=2, sales=every_day(2), name="crit_1")

    rows = client.get("/dss/alerts").json()
    assert [r["product_id"] for r in rows] == [crit_1, crit_2, reorder, none, ok]
    assert [r["status"] for r in rows] == ["CRITICAL", "CRITICAL", "REORDER", "NO_SALES", "OK"]

    assert client.get("/dss/summary").json() == {"CRITICAL": 2, "REORDER": 1, "NO_SALES": 1, "OK": 1}


def test_summary_empty(client):
    assert client.get("/dss/summary").json() == {"CRITICAL": 0, "REORDER": 0, "NO_SALES": 0, "OK": 0}


# ---------- 404 ----------


def test_unknown_product_returns_404(client):
    r = client.get("/dss/alerts/999")
    assert r.status_code == 404
    assert r.json()["detail"] == "Product 999 not found"
