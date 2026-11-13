import random
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from models import Product, StockTransaction, Supplier

HISTORY_DAYS = 30

# (name, unit_price in paise, supplier index, opening receipt, daily sales)
# Daily sales is either a (lo, hi) tuple -- random units per day -- or a list cycled day by day,
# for products that must land in a specific DSS status on a fresh seed.
DEMO_PRODUCTS = [
    ("Atta 5kg", 28500, 0, 200, (2, 12)),
    ("Toor Dal 1kg", 16000, 0, 150, (1, 8)),
    ("Sunflower Oil 1L", 17500, 1, 120, (1, 6)),
    ("Basmati Rice 5kg", 59000, 1, 80, (0, 4)),
    ("Rose Biscuits", 3000, 1, 60, (0, 0)),  # never sells: exercises the NO_SALES path
    # 4/day for 30 days: 129 - 120 leaves 9 units, ~2.2 days of cover vs a 5-day lead time -> CRITICAL.
    ("Colgate 200g", 11000, 1, 129, [4]),
    # 2, 6, 2, 6...: max_daily 6 -> reorder point 6*3 = 18; d ~3.7 -> d*3 ~11.1.
    # 135 - 120 leaves 15, inside 11.1..18 (cover >= lead time, stock <= reorder point) -> REORDER.
    ("Tata Salt 1kg", 2800, 0, 135, [2, 6]),
]


def seed_demo_data(db: Session) -> None:
    rng = random.Random(42)  # fixed seed: same demo data on every fresh install
    now = datetime.now(timezone.utc).replace(tzinfo=None)  # naive UTC, like CURRENT_TIMESTAMP
    start = (now - timedelta(days=HISTORY_DAYS)).replace(hour=9, minute=0, second=0, microsecond=0)

    suppliers = [
        Supplier(name="Sharma Traders", phone="+91-9876543210", lead_time_days=3),
        Supplier(name="Metro Wholesale", phone="+91-9123456780", lead_time_days=5),
    ]
    db.add_all(suppliers)
    db.flush()  # assigns supplier ids

    for name, price, supplier_idx, opening, daily in DEMO_PRODUCTS:
        product = Product(name=name, unit_price=price, supplier_id=suppliers[supplier_idx].id)
        db.add(product)
        db.flush()

        # Rows go in one at a time in date order so the AFTER INSERT trigger keeps current_stock right.
        stock = opening
        db.add(StockTransaction(product_id=product.id, txn_type="receipt", qty=opening, txn_date=start))
        for day in range(1, HISTORY_DAYS + 1):  # through today, so today's slot in the DSS window has sales
            planned = daily[day % len(daily)] if isinstance(daily, list) else rng.randint(*daily)
            sold = min(planned, stock)  # never oversell, or the CHECK aborts the seed
            if sold > 0:
                # Today's sale may be scheduled later than now; clamp so no txn_date is in the future.
                when = min(start + timedelta(days=day, hours=rng.randint(1, 10)), now)
                db.add(
                    StockTransaction(product_id=product.id, txn_type="sale", qty=-sold, txn_date=when)
                )
                stock -= sold
            db.flush()

    db.commit()
