import pathlib
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Path, Query, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import dss
from database import DB_PATH, Base, SessionLocal, engine, get_db
from models import Product, StockTransaction, Supplier
from schemas import (
    SQLITE_INT_MAX,
    ProductCreate,
    ProductOut,
    SupplierCreate,
    SupplierOut,
    TransactionCreate,
    TransactionOut,
    TransactionResult,
)
from seed import seed_demo_data


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Check before anything connects: the first connection creates the file.
    if not DB_PATH.exists():
        try:
            Base.metadata.create_all(engine)
            with SessionLocal() as db:
                seed_demo_data(db)
        except Exception:
            # Don't leave a half-built DB behind, or the next startup would skip setup.
            engine.dispose()
            DB_PATH.unlink(missing_ok=True)
            raise
    yield


app = FastAPI(title="Retail Inventory DSS", lifespan=lifespan)

# Dev setting: any origin. Narrow to the frontend's URL before deploying.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(dss.router)


# Bounded like schemas.Id so an oversized id in the URL is a 422, not a driver crash.
PathId = Annotated[int, Path(ge=1, le=SQLITE_INT_MAX)]


def get_or_404(db: Session, model, obj_id: int, label: str):
    obj = db.get(model, obj_id)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"{label} {obj_id} not found")
    return obj


# ---------- Suppliers ----------


@app.get("/suppliers", response_model=list[SupplierOut])
def list_suppliers(db: Session = Depends(get_db)):
    return db.scalars(select(Supplier).order_by(Supplier.id)).all()


@app.post("/suppliers", response_model=SupplierOut, status_code=status.HTTP_201_CREATED)
def create_supplier(body: SupplierCreate, db: Session = Depends(get_db)):
    supplier = Supplier(**body.model_dump())
    db.add(supplier)
    db.commit()
    db.refresh(supplier)
    return supplier


@app.get("/suppliers/{supplier_id}", response_model=SupplierOut)
def get_supplier(supplier_id: PathId, db: Session = Depends(get_db)):
    return get_or_404(db, Supplier, supplier_id, "Supplier")


@app.put("/suppliers/{supplier_id}", response_model=SupplierOut)
def update_supplier(supplier_id: PathId, body: SupplierCreate, db: Session = Depends(get_db)):
    supplier = get_or_404(db, Supplier, supplier_id, "Supplier")
    for field, value in body.model_dump().items():
        setattr(supplier, field, value)
    db.commit()
    db.refresh(supplier)
    return supplier


@app.delete("/suppliers/{supplier_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_supplier(supplier_id: PathId, db: Session = Depends(get_db)):
    supplier = get_or_404(db, Supplier, supplier_id, "Supplier")
    if db.scalar(select(exists().where(Product.supplier_id == supplier_id))):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Supplier {supplier_id} still has products; delete or reassign them first",
        )
    db.delete(supplier)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------- Products ----------


@app.get("/products", response_model=list[ProductOut])
def list_products(db: Session = Depends(get_db)):
    return db.scalars(select(Product).order_by(Product.id)).all()


@app.post("/products", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
def create_product(body: ProductCreate, db: Session = Depends(get_db)):
    get_or_404(db, Supplier, body.supplier_id, "Supplier")
    product = Product(**body.model_dump())
    db.add(product)
    db.commit()
    db.refresh(product)  # loads current_stock's server default (0)
    return product


@app.get("/products/{product_id}", response_model=ProductOut)
def get_product(product_id: PathId, db: Session = Depends(get_db)):
    return get_or_404(db, Product, product_id, "Product")


@app.put("/products/{product_id}", response_model=ProductOut)
def update_product(product_id: PathId, body: ProductCreate, db: Session = Depends(get_db)):
    product = get_or_404(db, Product, product_id, "Product")
    get_or_404(db, Supplier, body.supplier_id, "Supplier")
    for field, value in body.model_dump().items():
        setattr(product, field, value)
    db.commit()
    db.refresh(product)
    return product


@app.delete("/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product(product_id: PathId, db: Session = Depends(get_db)):
    product = get_or_404(db, Product, product_id, "Product")
    if db.scalar(select(exists().where(StockTransaction.product_id == product_id))):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Product {product_id} has stock transactions and cannot be deleted (history is append-only)",
        )
    db.delete(product)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------- Transactions ----------


@app.post("/transactions", response_model=TransactionResult, status_code=status.HTTP_201_CREATED)
def create_transaction(body: TransactionCreate, db: Session = Depends(get_db)):
    product = get_or_404(db, Product, body.product_id, "Product")
    txn = StockTransaction(**body.model_dump())
    db.add(txn)
    try:
        db.commit()  # INSERT runs here; the trigger updates products.current_stock in the same statement
    except IntegrityError as exc:
        db.rollback()
        if "ck_products_stock_nonneg" in str(exc.orig):
            stock = product.current_stock  # reloaded after rollback: the unchanged value
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=(
                    f"Insufficient stock for product {product.id}: current stock is {stock}, "
                    f"change of {body.qty} would make it {stock + body.qty}."
                ),
            ) from exc
        raise
    db.refresh(product)  # the trigger changed current_stock behind the ORM's back
    return TransactionResult(
        **TransactionOut.model_validate(txn).model_dump(),
        current_stock=product.current_stock,
    )


@app.get("/transactions", response_model=list[TransactionOut])
def list_transactions(
    product_id: Annotated[int | None, Query(ge=1, le=SQLITE_INT_MAX)] = None,
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    stmt = select(StockTransaction)
    if product_id is not None:
        stmt = stmt.where(StockTransaction.product_id == product_id)
    stmt = stmt.order_by(StockTransaction.txn_date.desc(), StockTransaction.txn_id.desc()).limit(limit)
    return db.scalars(stmt).all()


# ---------- Frontend ----------

# Resolved from this file, not the working directory, so `uvicorn main:app` works from anywhere.
FRONTEND_DIR = pathlib.Path(__file__).resolve().parent / "frontend"


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/app/")


# Mounted last and under /app so it can never shadow an API route.
app.mount("/app", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
