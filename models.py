from datetime import datetime

from sqlalchemy import DDL, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, event, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class Supplier(Base):
    __tablename__ = "suppliers"
    __table_args__ = (
        CheckConstraint("lead_time_days >= 0", name="ck_suppliers_lead_time_nonneg"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    phone: Mapped[str | None] = mapped_column(String)
    lead_time_days: Mapped[int] = mapped_column(Integer, nullable=False)

    # passive_deletes="all": never let the ORM null out children's FK on delete; the DB's RESTRICT decides.
    products: Mapped[list["Product"]] = relationship(back_populates="supplier", passive_deletes="all")


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("unit_price >= 0", name="ck_products_price_nonneg"),
        CheckConstraint("current_stock >= 0", name="ck_products_stock_nonneg"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    supplier_id: Mapped[int] = mapped_column(
        ForeignKey("suppliers.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    unit_price: Mapped[int] = mapped_column(Integer, nullable=False)  # in cents
    # Cache of SUM(stock_transactions.qty); maintained by trg_stock_after_insert, never set directly.
    current_stock: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    supplier: Mapped[Supplier] = relationship(back_populates="products")
    transactions: Mapped[list["StockTransaction"]] = relationship(
        back_populates="product", passive_deletes="all"
    )


class StockTransaction(Base):
    __tablename__ = "stock_transactions"
    __table_args__ = (
        CheckConstraint(
            "txn_type IN ('sale', 'receipt', 'adjustment')", name="ck_txn_type"
        ),
        CheckConstraint("qty <> 0", name="ck_txn_qty_nonzero"),
        CheckConstraint(
            "(txn_type = 'sale' AND qty < 0)"
            " OR (txn_type = 'receipt' AND qty > 0)"
            " OR txn_type = 'adjustment'",
            name="ck_txn_qty_sign",
        ),
        Index("ix_txn_product_date", "product_id", "txn_date"),
    )

    txn_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    txn_type: Mapped[str] = mapped_column(String, nullable=False)
    # Stored in UTC (SQLite CURRENT_TIMESTAMP).
    txn_date: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
    qty: Mapped[int] = mapped_column(Integer, nullable=False)  # signed: sale < 0, receipt > 0

    product: Mapped[Product] = relationship(back_populates="transactions")


# If the update would push current_stock below 0, ck_products_stock_nonneg fails
# and SQLite rolls back the whole INSERT, so the ledger and cache never diverge.
event.listen(
    StockTransaction.__table__,
    "after_create",
    DDL(
        """
        CREATE TRIGGER trg_stock_after_insert
        AFTER INSERT ON stock_transactions
        BEGIN
            UPDATE products
            SET current_stock = current_stock + NEW.qty
            WHERE id = NEW.product_id;
        END;
        """
    ),
)

# Enforce append-only at the DB level: corrections must be new 'adjustment' rows.
# One DDL per trigger -- the sqlite3 driver executes only one statement per call.
event.listen(
    StockTransaction.__table__,
    "after_create",
    DDL(
        """
        CREATE TRIGGER trg_txn_no_update
        BEFORE UPDATE ON stock_transactions
        BEGIN
            SELECT RAISE(ABORT, 'stock_transactions is append-only; insert an adjustment instead');
        END;
        """
    ),
)
event.listen(
    StockTransaction.__table__,
    "after_create",
    DDL(
        """
        CREATE TRIGGER trg_txn_no_delete
        BEFORE DELETE ON stock_transactions
        BEGIN
            SELECT RAISE(ABORT, 'stock_transactions is append-only; insert an adjustment instead');
        END;
        """
    ),
)