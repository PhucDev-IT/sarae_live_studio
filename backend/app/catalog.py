"""Product and presenter CRUD for Sarae Live Studio."""
from datetime import datetime, timezone
import re
from uuid import uuid4
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile, status

from app.identity import current_owner
from app.ownership import Owner, owned
from app import media_access
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, delete, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.database import Base, SessionLocal, DATA_DIR

PRODUCT_IMAGE_DIR = DATA_DIR / "product_images"
PRESENTER_IMAGE_DIR = DATA_DIR / "presenter_images"
MAX_IMAGE_BYTES = 5 * 1024 * 1024
IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}
IMAGE_NAME = re.compile(r"^[0-9a-f]{32}\.(jpg|png|webp|gif)$")

router = APIRouter(prefix="/api", tags=["Catalog"], dependencies=[Depends(current_owner)])


# Photos are fetched by <img> tags, which cannot send a token; their 128-bit random names are the capability.
public_router = APIRouter(prefix="/api", tags=["Catalog"])
def db_session():
    with SessionLocal() as session:
        yield session


DB = Annotated[Session, Depends(db_session)]


class Product(Base):
    __tablename__ = "products"
    # SKUs are unique per shop, not across the platform.
    __table_args__ = (UniqueConstraint("owner_id", "sku", name="uq_products_owner_sku"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    shop_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    tiktok_product_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    stock_num: Mapped[int | None] = mapped_column(Integer, nullable=True)
    origin_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    tiktok_category: Mapped[str | None] = mapped_column(String(250), nullable=True)
    sku: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    image_url: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Presenter(Base):
    __tablename__ = "presenters"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
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

    @field_validator("sku", "image_url")
    @classmethod
    def strip_text(cls, value: str) -> str:
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

    @field_validator("avatar_url")
    @classmethod
    def strip_avatar(cls, value: str) -> str:
        return value.strip()

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        if value not in {"ACTIVE", "INACTIVE"}:
            raise ValueError("Trạng thái không hợp lệ")
        return value


class ProductOut(ProductInput):
    shop_id: int | None = None
    tiktok_product_id: str | None = None
    stock_num: int | None = None
    origin_price: float | None = None
    tiktok_category: str | None = None
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime


class PresenterOut(PresenterInput):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime


def save_image(folder, file: UploadFile) -> str:
    """Validates an uploaded photo and stores it under a random name; returns that name."""
    ext = IMAGE_TYPES.get(file.content_type or "")
    if ext is None:
        raise HTTPException(415, "Chỉ hỗ trợ ảnh JPG, PNG, WEBP hoặc GIF")
    data = file.file.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "Ảnh tối đa 5 MB")
    if not data:
        raise HTTPException(422, "File ảnh rỗng")
    folder.mkdir(parents=True, exist_ok=True)
    name = uuid4().hex + ext
    (folder / name).write_bytes(data)
    return name


def serve_image(folder, name: str):
    path = folder / name
    if not IMAGE_NAME.match(name) or not path.is_file():
        raise HTTPException(404, "Không tìm thấy ảnh")
    return FileResponse(path, headers={"Cache-Control": "public, max-age=31536000, immutable"})


def sku_taken(db: Session, owner: dict, sku: str, exclude_id: int | None = None) -> bool:
    stmt = select(Product.id).where(Product.owner_id == owner["id"], Product.sku == sku)
    if exclude_id is not None:
        stmt = stmt.where(Product.id != exclude_id)
    return db.scalar(stmt) is not None


def product_out(item: Product) -> dict:
    """API view of a product; its photo URL is signed for its owner (see media_access)."""
    return {**ProductOut.model_validate(item).model_dump(), "image_url": media_access.public_url(item.image_url, item.owner_id)}


def presenter_out(item: Presenter) -> dict:
    return {**PresenterOut.model_validate(item).model_dump(), "avatar_url": media_access.public_url(item.avatar_url, item.owner_id)}


def photo_in_use(db: Session, ref: str) -> bool:
    return db.scalar(select(Product.id).where(Product.image_url == ref)) is not None or \
        db.scalar(select(Presenter.id).where(Presenter.avatar_url == ref)) is not None


def release_photo(db: Session, value: str | None, directory):
    media_access.release(db, value, directory, lambda ref: photo_in_use(db, ref))


@router.get("/products", response_model=list[ProductOut])
def list_products(db: DB, owner: Owner, q: str = "", limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
    stmt = select(Product).where(Product.owner_id == owner["id"])
    if q.strip():
        term = f"%{q.strip()}%"
        stmt = stmt.where((Product.name.ilike(term)) | (Product.sku.ilike(term)))
    return [product_out(p) for p in db.scalars(stmt.order_by(Product.id.desc()).offset(offset).limit(limit)).all()]


def new_sku(db: Session, owner: dict) -> str:
    """Short code for products saved without one (SKU is unique per owner, so blank cannot be stored twice)."""
    while True:
        sku = "SP-" + uuid4().hex[:6].upper()
        if not sku_taken(db, owner, sku):
            return sku


@router.post("/products", response_model=ProductOut, status_code=201)
def create_product(body: ProductInput, db: DB, owner: Owner):
    if body.sku and sku_taken(db, owner, body.sku):
        raise HTTPException(status_code=409, detail="Mã sản phẩm đã tồn tại")
    item = Product(**{**body.model_dump(), "sku": body.sku or new_sku(db, owner), "owner_id": owner["id"],
                      "image_url": media_access.stored_ref(db, owner, body.image_url, "products")})
    db.add(item)
    db.commit()
    db.refresh(item)
    return product_out(item)


@router.post("/products/images", status_code=201)
def upload_product_image(db: DB, owner: Owner, file: UploadFile = File(...)):
    """Stores a product photo owned by the caller. `url` (signed, for preview) or `ref` can be put in `image_url`."""
    name = save_image(PRODUCT_IMAGE_DIR, file)
    media_access.register(db, owner, name, "products")
    ref = f"/api/products/images/{name}"
    return {"url": media_access.public_url(ref, owner["id"]), "ref": ref}


@public_router.get("/products/images/{name}", name="product_image")
def product_image(name: str, db: DB, exp: int | None = None, sig: str | None = None):
    """Only through a signed URL issued to the photo's owner (see media_access)."""
    owner_id = media_access.image_owner(db, name, "products")
    if owner_id is None:
        raise HTTPException(404, "Không tìm thấy ảnh")
    media_access.check("product", name, owner_id, exp, sig)
    return serve_image(PRODUCT_IMAGE_DIR, name)


@router.get("/products/{entity_id}", response_model=ProductOut)
def get_product(entity_id: int, db: DB, owner: Owner):
    return product_out(owned(db, Product, entity_id, owner, "Không tìm thấy sản phẩm"))


@router.put("/products/{entity_id}", response_model=ProductOut)
def update_product(entity_id: int, body: ProductInput, db: DB, owner: Owner):
    item = owned(db, Product, entity_id, owner, "Không tìm thấy sản phẩm")
    if body.sku and sku_taken(db, owner, body.sku, exclude_id=entity_id):
        raise HTTPException(status_code=409, detail="Mã sản phẩm đã tồn tại")
    previous = item.image_url
    for key, value in body.model_dump(exclude={"sku", "image_url"}).items():
        setattr(item, key, value)
    item.image_url = media_access.stored_ref(db, owner, body.image_url, "products")
    if body.sku:
        item.sku = body.sku
    db.commit()
    db.refresh(item)
    if previous != item.image_url:
        release_photo(db, previous, PRODUCT_IMAGE_DIR)
    return product_out(item)


@router.delete("/products/{entity_id}", status_code=204)
def delete_product(entity_id: int, db: DB, owner: Owner):
    from app.product_videos import ProductLiveVideo
    from app.live_sessions import LiveSession, SessionProduct
    item = owned(db, Product, entity_id, owner, "Không tìm thấy sản phẩm")
    on_air = db.scalar(select(LiveSession.id).where(LiveSession.owner_id == owner["id"], LiveSession.status == "RUNNING",
                                                     LiveSession.current_product_id == item.id))
    if on_air is not None:
        raise HTTPException(status_code=409, detail="Sản phẩm đang phát trên LIVE, hãy chuyển sản phẩm khác trước khi xóa")
    # SQLite does not enforce ON DELETE CASCADE here; drop links so a reused id never inherits them.
    db.execute(delete(ProductLiveVideo).where(ProductLiveVideo.product_id == item.id))
    # Upcoming and running lineups lose the product; ended sessions keep it as history.
    open_sessions = select(LiveSession.id).where(LiveSession.owner_id == owner["id"], LiveSession.status != "ENDED")
    db.execute(delete(SessionProduct).where(SessionProduct.product_id == item.id, SessionProduct.session_id.in_(open_sessions)))
    photo = item.image_url
    db.delete(item)
    db.commit()
    release_photo(db, photo, PRODUCT_IMAGE_DIR)
    return Response(status_code=204)


@router.get("/presenters", response_model=list[PresenterOut])
def list_presenters(db: DB, owner: Owner, q: str = "", limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
    stmt = select(Presenter).where(Presenter.owner_id == owner["id"])
    if q.strip():
        stmt = stmt.where(Presenter.name.ilike(f"%{q.strip()}%"))
    return [presenter_out(p) for p in db.scalars(stmt.order_by(Presenter.id.desc()).offset(offset).limit(limit)).all()]


@router.post("/presenters", response_model=PresenterOut, status_code=201)
def create_presenter(body: PresenterInput, db: DB, owner: Owner):
    item = Presenter(**{**body.model_dump(), "owner_id": owner["id"],
                        "avatar_url": media_access.stored_ref(db, owner, body.avatar_url, "presenters")})
    db.add(item)
    db.commit()
    db.refresh(item)
    return presenter_out(item)


@router.post("/presenters/images", status_code=201)
def upload_presenter_image(db: DB, owner: Owner, file: UploadFile = File(...)):
    """Stores a presenter photo owned by the caller. `url` (signed, for preview) or `ref` can be put in `avatar_url`."""
    name = save_image(PRESENTER_IMAGE_DIR, file)
    media_access.register(db, owner, name, "presenters")
    ref = f"/api/presenters/images/{name}"
    return {"url": media_access.public_url(ref, owner["id"]), "ref": ref}


@public_router.get("/presenters/images/{name}", name="presenter_image")
def presenter_image(name: str, db: DB, exp: int | None = None, sig: str | None = None):
    owner_id = media_access.image_owner(db, name, "presenters")
    if owner_id is None:
        raise HTTPException(404, "Không tìm thấy ảnh")
    media_access.check("presenter", name, owner_id, exp, sig)
    return serve_image(PRESENTER_IMAGE_DIR, name)


@router.get("/presenters/{entity_id}", response_model=PresenterOut)
def get_presenter(entity_id: int, db: DB, owner: Owner):
    return presenter_out(owned(db, Presenter, entity_id, owner, "Không tìm thấy người giới thiệu"))


@router.put("/presenters/{entity_id}", response_model=PresenterOut)
def update_presenter(entity_id: int, body: PresenterInput, db: DB, owner: Owner):
    item = owned(db, Presenter, entity_id, owner, "Không tìm thấy người giới thiệu")
    previous = item.avatar_url
    for key, value in body.model_dump(exclude={"avatar_url"}).items():
        setattr(item, key, value)
    item.avatar_url = media_access.stored_ref(db, owner, body.avatar_url, "presenters")
    db.commit()
    db.refresh(item)
    if previous != item.avatar_url:
        release_photo(db, previous, PRESENTER_IMAGE_DIR)
    return presenter_out(item)


@router.delete("/presenters/{entity_id}", status_code=204)
def delete_presenter(entity_id: int, db: DB, owner: Owner):
    from app.media_presenters import MediaPresenter
    item = owned(db, Presenter, entity_id, owner, "Không tìm thấy người giới thiệu")
    # SQLite does not enforce ON DELETE CASCADE here; drop links so a reused id never inherits them.
    db.execute(delete(MediaPresenter).where(MediaPresenter.presenter_id == item.id))
    photo = item.avatar_url
    db.delete(item)
    db.commit()
    release_photo(db, photo, PRESENTER_IMAGE_DIR)
    return Response(status_code=204)
