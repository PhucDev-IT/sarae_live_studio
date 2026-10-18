"""People who appear in each video: a many-to-many, unordered link between media and presenters."""
from fastapi import APIRouter, Depends, HTTPException

from app.identity import current_owner
from app.ownership import Owner, owned, owned_ids
from pydantic import BaseModel, Field
from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint, delete, func, select
from sqlalchemy.orm import Mapped, mapped_column

from app.catalog import Presenter
from app.database import Base
from app.media import DB, Media

router = APIRouter(prefix="/api/presenter-videos", tags=["Presenter videos"], dependencies=[Depends(current_owner)])


class MediaPresenter(Base):
    __tablename__ = "media_presenters"
    __table_args__ = (UniqueConstraint("media_id", "presenter_id", name="uq_media_presenter"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    media_id: Mapped[int] = mapped_column(ForeignKey("media.id", ondelete="CASCADE"), index=True)
    presenter_id: Mapped[int] = mapped_column(ForeignKey("presenters.id", ondelete="CASCADE"), index=True)


class PresenterVideos(BaseModel):
    media_ids: list[int] = Field(max_length=500)


def presenter_or_404(db, presenter_id: int, owner: dict) -> Presenter:
    return owned(db, Presenter, presenter_id, owner, "Không tìm thấy người giới thiệu")


@router.get("")
def presenter_video_counts(db: DB, owner: Owner):
    """Number of videos each person appears in, for list views (people with none are omitted)."""
    rows = db.execute(select(MediaPresenter.presenter_id, func.count(MediaPresenter.id))
                      .join(Media, Media.id == MediaPresenter.media_id)
                      .where(MediaPresenter.owner_id == owner["id"])
                      .group_by(MediaPresenter.presenter_id)).all()
    return [{"presenter_id": pid, "video_count": count} for pid, count in rows]


@router.get("/{presenter_id}")
def read_presenter_videos(presenter_id: int, db: DB, owner: Owner):
    presenter_or_404(db, presenter_id, owner)
    ids = db.scalars(select(MediaPresenter.media_id)
                     .join(Media, Media.id == MediaPresenter.media_id)
                     .where(MediaPresenter.presenter_id == presenter_id)
                     .order_by(MediaPresenter.id)).all()
    return {"presenter_id": presenter_id, "media_ids": list(ids)}


@router.put("/{presenter_id}")
def save_presenter_videos(presenter_id: int, body: PresenterVideos, db: DB, owner: Owner):
    """Replace the set of videos this person appears in; other people in those videos are untouched."""
    if len(body.media_ids) != len(set(body.media_ids)):
        raise HTTPException(422, "Không được thêm trùng video cho cùng một người")
    presenter_or_404(db, presenter_id, owner)
    if body.media_ids:
        if owned_ids(db, Media, body.media_ids, owner) != set(body.media_ids):
            raise HTTPException(422, "Danh sách chứa video không tồn tại")
    current = set(db.scalars(select(MediaPresenter.media_id).where(MediaPresenter.presenter_id == presenter_id)).all())
    db.execute(delete(MediaPresenter).where(
        MediaPresenter.presenter_id == presenter_id, MediaPresenter.media_id.not_in(body.media_ids)))
    db.add_all([MediaPresenter(media_id=mid, presenter_id=presenter_id, owner_id=owner["id"]) for mid in body.media_ids if mid not in current])
    db.commit()
    return {"presenter_id": presenter_id, "media_ids": body.media_ids}
