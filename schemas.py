from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

# SQLite INTEGER is 64-bit signed; anything larger crashes the driver (a 500), so cap it here.
SQLITE_INT_MAX = 2**63 - 1
# Business caps. MAX_QTY also keeps current_stock far from overflow (SQLite would turn it into a REAL).
MAX_QTY = 1_000_000
MAX_UNIT_PRICE = 1_000_000_000  # paise = ₹1 crore
MAX_LEAD_TIME_DAYS = 365

Id = Annotated[int, Field(ge=1, le=SQLITE_INT_MAX)]
Name = Annotated[str, Field(min_length=1)]


class SupplierCreate(BaseModel):
    # str_strip_whitespace: "   " becomes "" and fails min_length.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: Name
    phone: str | None = None
    lead_time_days: int = Field(ge=0, le=MAX_LEAD_TIME_DAYS)  # ck_suppliers_lead_time_nonneg


class SupplierOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    # ORM column is `id`; the API exposes it as `supplier_id`.
    supplier_id: int = Field(validation_alias="id")
    name: str
    phone: str | None
    lead_time_days: int


class ProductCreate(BaseModel):
    # No current_stock: stock only changes through POST /transactions.
    # extra="forbid" turns a client sending current_stock into a 422 instead of silently ignoring it.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    supplier_id: Id
    name: Name
    unit_price: int = Field(ge=0, le=MAX_UNIT_PRICE)  # in paise; ck_products_price_nonneg


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    product_id: int = Field(validation_alias="id")
    supplier_id: int
    name: str
    unit_price: int
    current_stock: int


class TransactionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: Id
    txn_type: Literal["sale", "receipt", "adjustment"]  # ck_txn_type
    qty: int = Field(ge=-MAX_QTY, le=MAX_QTY)

    @model_validator(mode="after")
    def check_qty_sign(self) -> "TransactionCreate":
        # Mirrors ck_txn_qty_nonzero / ck_txn_qty_sign so bad input is a 422, not a DB error.
        if self.qty == 0:
            raise ValueError("qty must not be 0")
        if self.txn_type == "sale" and self.qty > 0:
            raise ValueError("sale qty must be negative")
        if self.txn_type == "receipt" and self.qty < 0:
            raise ValueError("receipt qty must be positive")
        return self


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    txn_id: int
    product_id: int
    txn_type: str
    txn_date: datetime
    qty: int

    @field_serializer("txn_date")
    def serialize_txn_date(self, value: datetime) -> str:
        # SQLite hands back naive datetimes that are already UTC; mark them so with "Z".
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value.isoformat() + "Z"


class TransactionResult(TransactionOut):
    current_stock: int