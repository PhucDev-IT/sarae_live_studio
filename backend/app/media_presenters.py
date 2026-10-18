"""People who appear in each video: a many-to-many, unordered link between Agent videos and presenters."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint, delete, func, select
from sqlalchemy.orm import Mapped, mapped_column

from app.catalog import Presenter
from app.database import Base
from app.identity import current_owner
from app.media import DB, library
from app.owner_agents import AgentId, owned_agent
from app.ownership import Owner, owned

router = APIRouter(prefix="/api/presenter-videos", tags=["Presenter videos"], dependencies=[Depends(current_owner)])


class MediaPresenter(Base):
    __tablename__ = "media_presenters"
    __table_args__ = (UniqueConstraint("presenter_id", "agent_id", "media_id", name="uq_media_presenter"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    presenter_id: Mapped[int] = mapped_column(ForeignKey("presenters.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[str] = mapped_column(String(80))
    media_id: Mapped[str] = mapped_column(String(64))


class PresenterVideos(BaseModel):
    media_ids: list[str] = Field(max_length=500)


def presenter_or_404(db, presenter_id: int, owner: dict) -> Presenter:
    return owned(db, Presenter, presenter_id, owner, "Không tìm thấy người giới thiệu")


def scope(owner: dict, agent_id: str):
    return MediaPresenter.owner_id == owner["id"], MediaPresenter.agent_id == agent_id


@router.get("")
def presenter_video_counts(db: DB, owner: Owner, agent_id: AgentId):
    """Number of videos each person appears in on that Agent (people with none are omitted)."""
    agent = owned_agent(owner, agent_id)
    rows = db.execute(select(MediaPresenter.presenter_id, func.count(MediaPresenter.id))
                      .where(*scope(owner, agent["id"])).group_by(MediaPresenter.presenter_id)).all()
    return [{"presenter_id": pid, "video_count": count} for pid, count in rows]


@router.get("/{presenter_id}")
def read_presenter_videos(presenter_id: int, db: DB, owner: Owner, agent_id: AgentId):
    presenter_or_404(db, presenter_id, owner)
    agent = owned_agent(owner, agent_id)
    ids = db.scalars(select(MediaPresenter.media_id).where(*scope(owner, agent["id"]), MediaPresenter.presenter_id == presenter_id)
                     .order_by(MediaPresenter.id)).all()
    return {"presenter_id": presenter_id, "media_ids": list(ids)}


@router.put("/{presenter_id}")
def save_presenter_videos(presenter_id: int, body: PresenterVideos, db: DB, owner: Owner, agent_id: AgentId):
    """Replace the set of videos this person appears in; other people in those videos are untouched."""
    if len(body.media_ids) != len(set(body.media_ids)):
        raise HTTPException(422, "Không được thêm trùng video cho cùng một người")
    presenter_or_404(db, presenter_id, owner)
    agent = owned_agent(owner, agent_id)
    if not set(body.media_ids) <= set(library(agent["id"])):
        raise HTTPException(422, "Danh sách chứa video không có trên Agent đang dùng")
    where = (*scope(owner, agent["id"]), MediaPresenter.presenter_id == presenter_id)
    current = set(db.scalars(select(MediaPresenter.media_id).where(*where)).all())
    db.execute(delete(MediaPresenter).where(*where, MediaPresenter.media_id.not_in(body.media_ids)))
    db.add_all([MediaPresenter(owner_id=owner["id"], agent_id=agent["id"], media_id=mid, presenter_id=presenter_id)
                for mid in body.media_ids if mid not in current])
    db.commit()
    return {"presenter_id": presenter_id, "media_ids": body.media_ids}
