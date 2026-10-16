"""Product and presenter CRUD for Sarae Live Studio."""
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, delete, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.database import Base, SessionLocal

router = APIRouter(prefix="/api", tags=["Catalog"])


def db_session():
    with SessionLocal() as session:
        yield session


DB = Annotated[Session, Depends(db_session)]


class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    image_url: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Presenter(Base):
    __tablename__ = "presenters"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    avatar_url: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ProductInput(BaseModel):
    sku: str = Field(default="", max_length=64)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10000)
    price: float | None = Field(default=None, ge=0)
    image_url: str = Field(default="", max_length=2000)
    status: str = "ACTIVE"

    @field_validator("name")
    @classmethod
    def strip_required(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Không được để trống")
        return value.strip()

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        if value not in {"ACTIVE", "INACTIVE"}:
            raise ValueError("Trạng thái không hợp lệ")
        return value


class PresenterInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10000)
    avatar_url: str = Field(default="", max_length=2000)
    status: str = "ACTIVE"

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Không được để trống")
        return value.strip()

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        if value not in {"ACTIVE", "INACTIVE"}:
            raise ValueError("Trạng thái không hợp lệ")
        return value


class ProductOut(ProductInput):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime


class PresenterOut(PresenterInput):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime


def get_entity(db: Session, model, entity_id: int):
    entity = db.get(model, entity_id)
    if entity is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy dữ liệu")
    return entity


@router.get("/products", response_model=list[ProductOut])
def list_products(db: DB, q: str = "", limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
    stmt = select(Product).order_by(Product.id.desc()).offset(offset).limit(limit)
    if q.strip():
        term = f"%{q.strip()}%"
        stmt = select(Product).where((Product.name.ilike(term)) | (Product.sku.ilike(term))).order_by(Product.id.desc()).offset(offset).limit(limit)
    return db.scalars(stmt).all()


@router.post("/products", response_model=ProductOut, status_code=201)
def create_product(body: ProductInput, db: DB):
    if db.scalar(select(Product.id).where(Product.sku == body.sku)):
        raise HTTPException(status_code=409, detail="Mã sản phẩm đã tồn tại")
    item = Product(**body.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.get("/products/{entity_id}", response_model=ProductOut)
def get_product(entity_id: int, db: DB):
    return get_entity(db, Product, entity_id)


@router.put("/products/{entity_id}", response_model=ProductOut)
def update_product(entity_id: int, body: ProductInput, db: DB):
    item = get_entity(db, Product, entity_id)
    if body.sku and db.scalar(select(Product.id).where(Product.sku == body.sku, Product.id != entity_id)):
        raise HTTPException(status_code=409, detail="Mã sản phẩm đã tồn tại")
    for key, value in body.model_dump(exclude={"sku"}).items():
        setattr(item, key, value)
    if body.sku:
        item.sku = body.sku
    db.commit()
    db.refresh(item)
    return item


@router.delete("/products/{entity_id}", status_code=204)
def delete_product(entity_id: int, db: DB):
    from app.product_videos import ProductLiveVideo
    item = get_entity(db, Product, entity_id)
    # SQLite does not enforce ON DELETE CASCADE here; drop links so a reused id never inherits them.
    db.execute(delete(ProductLiveVideo).where(ProductLiveVideo.product_id == item.id))
    db.delete(item)
    db.commit()
    return Response(status_code=204)


@router.get("/presenters", response_model=list[PresenterOut])
def list_presenters(db: DB, q: str = "", limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
    stmt = select(Presenter).order_by(Presenter.id.desc()).offset(offset).limit(limit)
    if q.strip():
        stmt = select(Presenter).where(Presenter.name.ilike(f"%{q.strip()}%")).order_by(Presenter.id.desc()).offset(offset).limit(limit)
    return db.scalars(stmt).all()


@router.post("/presenters", response_model=PresenterOut, status_code=201)
def create_presenter(body: PresenterInput, db: DB):
    item = Presenter(**body.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.get("/presenters/{entity_id}", response_model=PresenterOut)
def get_presenter(entity_id: int, db: DB):
    return get_entity(db, Presenter, entity_id)


@router.put("/presenters/{entity_id}", response_model=PresenterOut)
def update_presenter(entity_id: int, body: PresenterInput, db: DB):
    item = get_entity(db, Presenter, entity_id)
    for key, value in body.model_dump().items():
        setattr(item, key, value)
    db.commit()
    db.refresh(item)
    return item


@router.delete("/presenters/{entity_id}", status_code=204)
def delete_presenter(entity_id: int, db: DB):
    from app.media_presenters import MediaPresenter
    item = get_entity(db, Presenter, entity_id)
    # SQLite does not enforce ON DELETE CASCADE here; drop links so a reused id never inherits them.
    db.execute(delete(MediaPresenter).where(MediaPresenter.presenter_id == item.id))
    db.delete(item)
    db.commit()
    return Response(status_code=204)
