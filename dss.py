import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database import get_db
from models import Product, StockTransaction, Supplier
from schemas import SQLITE_INT_MAX, DssAlert, DssStatus, DssSummary

WINDOW_DAYS = 7  # demand is measured over the last WINDOW_DAYS calendar days (UTC), today included
REVIEW_DAYS = 7  # an order should cover lead time plus this many days until the next review

# Sort priority for /dss/alerts; lower comes first.
STATUS_ORDER: dict[DssStatus, int] = {"CRITICAL": 0, "REORDER": 1, "NO_SALES": 2, "OK": 3}

router = APIRouter(prefix="/dss", tags=["dss"])


def _window_start(now: datetime) -> datetime:
    # Naive UTC, matching how txn_date is stored.
    today = now.astimezone(timezone.utc).replace(tzinfo=None).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return today - timedelta(days=WINDOW_DAYS - 1)


def _daily_sales(db: Session, product_id: int | None = None) -> dict[int, list[int]]:
    """product_id -> units sold on each day in the window that had sales."""
    day = func.date(StockTransaction.txn_date)
    stmt = (
        select(StockTransaction.product_id, func.sum(-StockTransaction.qty))  # sale qty is negative
        .where(
            StockTransaction.txn_type == "sale",
            StockTransaction.txn_date >= _window_start(datetime.now(timezone.utc)),
        )
        .group_by(StockTransaction.product_id, day)
    )
    if product_id is not None:
        stmt = stmt.where(StockTransaction.product_id == product_id)
    sales: dict[int, list[int]] = defaultdict(list)
    for pid, units in db.execute(stmt):
        sales[pid].append(units)
    return sales


def _round(x: Fraction) -> float:
    return round(float(x), 2)


def _alert(product_id: int, name: str, stock: int, lead_time: int, daily: list[int]) -> DssAlert:
    # Fractions keep the maths exact: with floats, 8/7 * 14 is 16.000000000000004 and ceil gives 17.
    d = Fraction(sum(daily), WINDOW_DAYS)
    max_daily = max(daily, default=0)  # days with no sales are 0, so an empty list means 0
    safety_stock = (max_daily - d) * lead_time
    reorder_point = d * lead_time + safety_stock

    if d == 0:
        days_of_cover = None
        state: DssStatus = "NO_SALES"
    else:
        days_of_cover = stock / d  # exact; the status rules use this, the API and reason show cover_shown
        if days_of_cover < lead_time:
            state = "CRITICAL"
        elif stock <= reorder_point:
            state = "REORDER"
        else:
            state = "OK"

    if state in ("CRITICAL", "REORDER"):
        order_qty = max(0, math.ceil(d * (lead_time + REVIEW_DAYS) + safety_stock - stock))
    else:
        order_qty = 0

    # Rounded once: the days_of_cover field and the reason text must show the same number.
    cover_shown = None if days_of_cover is None else round(float(days_of_cover), 1)

    if state == "NO_SALES":
        reason = f"No sales in the last {WINDOW_DAYS} days -> nothing to reorder."
    else:
        cover = f"Stock {stock} lasts ~{cover_shown:.1f} days"
        if state == "CRITICAL":
            reason = f"{cover}; supplier needs {lead_time} days -> order now."
        elif state == "REORDER":
            reason = f"{cover}, at or below reorder point {_round(reorder_point):g} -> order {order_qty} units."
        else:
            reason = f"{cover}, above reorder point {_round(reorder_point):g} -> no order needed."

    return DssAlert(
        product_id=product_id,
        name=name,
        stock=stock,
        lead_time_days=lead_time,
        avg_daily_demand=_round(d),
        max_daily_demand=max_daily,
        days_of_cover=cover_shown,
        safety_stock=_round(safety_stock),
        reorder_point=_round(reorder_point),
        status=state,
        recommended_order_qty=order_qty,
        reason=reason,
    )


def _product_rows(db: Session, product_id: int | None = None):
    stmt = select(Product.id, Product.name, Product.current_stock, Supplier.lead_time_days).join(
        Supplier, Product.supplier_id == Supplier.id
    )
    if product_id is not None:
        stmt = stmt.where(Product.id == product_id)
    return db.execute(stmt.order_by(Product.id)).all()


def compute_alerts(db: Session) -> list[DssAlert]:
    sales = _daily_sales(db)
    alerts = [
        _alert(pid, name, stock, lead_time, sales.get(pid, []))
        for pid, name, stock, lead_time in _product_rows(db)
    ]
    # Within a status, lowest days_of_cover first; NO_SALES has none, so fall back to product_id.
    alerts.sort(
        key=lambda a: (
            STATUS_ORDER[a.status],
            a.days_of_cover if a.days_of_cover is not None else 0,
            a.product_id,
        )
    )
    return alerts


@router.get("/alerts", response_model=list[DssAlert])
def list_alerts(db: Session = Depends(get_db)):
    return compute_alerts(db)


@router.get("/alerts/{product_id}", response_model=DssAlert)
def get_alert(
    product_id: Annotated[int, Path(ge=1, le=SQLITE_INT_MAX)], db: Session = Depends(get_db)
):
    rows = _product_rows(db, product_id)
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Product {product_id} not found")
    pid, name, stock, lead_time = rows[0]
    return _alert(pid, name, stock, lead_time, _daily_sales(db, product_id).get(pid, []))


@router.get("/summary", response_model=DssSummary)
def summary(db: Session = Depends(get_db)):
    counts = {s: 0 for s in STATUS_ORDER}
    for alert in compute_alerts(db):
        counts[alert.status] += 1
    return DssSummary(**counts)