import random
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from models import Product, StockTransaction, Supplier

HISTORY_DAYS = 30

# (name, unit_price in paise, supplier index, opening receipt, daily sales range)
DEMO_PRODUCTS = [
    ("Atta 5kg", 28500, 0, 200, (2, 12)),
    ("Toor Dal 1kg", 16000, 0, 150, (1, 8)),
    ("Sunflower Oil 1L", 17500, 1, 120, (1, 6)),
    ("Basmati Rice 5kg", 59000, 1, 80, (0, 4)),
    ("Rose Biscuits", 3000, 1, 60, (0, 0)),  # never sells: exercises the NO_SALES path
    # Steady 4/day: 125 - 29*4 leaves 9 units, ~2.6 days of cover vs a 5-day lead time -> CRITICAL.
    # Keep it last: the shared rng would otherwise shift every product after it.
    ("Colgate 200g", 11000, 1, 125, (4, 4)),
    # Steady 4/day, 3-day lead: REORDER needs d*3 <= stock <= max_daily*3 (= reorder point), i.e. 10.3..12.
    # 127 - 29*4 leaves 11 -> ~3.2 days of cover, at/below reorder point 12 -> REORDER.
    ("Tata Salt 1kg", 2800, 0, 127, (4, 4)),
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

    for name, price, supplier_idx, opening, (lo, hi) in DEMO_PRODUCTS:
        product = Product(name=name, unit_price=price, supplier_id=suppliers[supplier_idx].id)
        db.add(product)
        db.flush()

        # Rows go in one at a time in date order so the AFTER INSERT trigger keeps current_stock right.
        stock = opening
        db.add(StockTransaction(product_id=product.id, txn_type="receipt", qty=opening, txn_date=start))
        for day in range(1, HISTORY_DAYS):  # stops at yesterday so no txn_date lands in the future
            sold = min(rng.randint(lo, hi), stock)  # never oversell, or the CHECK aborts the seed
            if sold > 0:
                db.add(
                    StockTransaction(
                        product_id=product.id,
                        txn_type="sale",
                        qty=-sold,
                        txn_date=start + timedelta(days=day, hours=rng.randint(1, 10)),
                    )
                )
                stock -= sold
            db.flush()

    db.commit()
