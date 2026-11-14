from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import StockTransaction
from seed import seed_demo_data


def test_seed_sales_run_through_today_but_not_into_the_future(engine):
    with Session(engine) as db:
        seed_demo_data(db)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        dates = db.scalars(select(StockTransaction.txn_date)).all()

    assert max(dates) <= now
    assert max(dates).date() == now.date()


def test_fresh_seed_has_critical_and_reorder_products(client, engine):
    with Session(engine) as db:
        seed_demo_data(db)

    alerts = {a["name"]: a for a in client.get("/dss/alerts").json()}
    assert alerts["Colgate 200g"]["status"] == "CRITICAL"
    assert alerts["Colgate 200g"]["stock"] == 9
    assert alerts["Tata Salt 1kg"]["status"] == "REORDER"
    assert alerts["Tata Salt 1kg"]["stock"] == 15
    assert alerts["Tata Salt 1kg"]["reorder_point"] == 18
    # The demo dashboard shows every status.
    assert all(n > 0 for n in client.get("/dss/summary").json().values())
