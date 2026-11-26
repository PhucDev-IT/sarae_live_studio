"""Local presenters and their links to managed videos."""
from __future__ import annotations

import shutil
import sqlite3
import subprocess
from uuid import uuid4

import pytest

from sarae_agent.studio.service import StudioError, StudioService


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
    monkeypatch.delenv("SARAE_STUDIO_DATA_DIR", raising=False)


@pytest.fixture
def studio(tmp_path):
    instance = StudioService(data_dir=tmp_path / "studio")
    try:
        yield instance
    finally:
        instance.close()


@pytest.fixture
def video(tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg and FFprobe are required to generate and validate real video")
    path = tmp_path / "giới thiệu.mp4"
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "color=c=red:s=32x32:r=10:d=0.3", "-an", "-c:v", "mpeg4", str(path)],
                   check=True, capture_output=True, timeout=30)
    return path


def call(studio, method, **params):
    return studio.dispatch(method, params)


def error_code(operation) -> str:
    with pytest.raises(StudioError) as caught:
        operation()
    return caught.value.code


def create(studio, **overrides):
    params = {"name": "Chị Lan", "description": "Giọng miền Bắc"}
    params.update(overrides)
    return call(studio, "studio.presenters.create", **params)["presenter"]


def test_presenter_crud_and_partial_update(studio):
    presenter = create(studio)
    assert presenter["active"] is True and presenter["name"] == "Chị Lan"
    updated = call(studio, "studio.presenters.update", id=presenter["id"], active=False)["presenter"]
    assert updated["active"] is False
    assert updated["name"] == "Chị Lan" and updated["description"] == "Giọng miền Bắc"
    assert updated["created_at"] == presenter["created_at"]
    assert call(studio, "studio.presenters.list")["presenters"] == [updated]
    assert call(studio, "studio.presenters.delete", id=presenter["id"]) == {"deleted": True}
    assert call(studio, "studio.presenters.list")["presenters"] == []


def test_list_shows_active_presenters_first(studio):
    create(studio, name="An", active=False)
    create(studio, name="Bình")
    create(studio, name="anh Cường")
    names = [item["name"] for item in call(studio, "studio.presenters.list")["presenters"]]
    assert names == ["anh Cường", "Bình", "An"]


@pytest.mark.parametrize("params", [
    {"name": "  "}, {"name": "x" * 201}, {"name": "a\x00b"}, {"name": "Lan", "active": "yes"},
    {"name": "Lan", "active": 1}, {"name": "Lan", "description": 5}, {"name": "Lan", "avatar_url": ""},
])
def test_invalid_presenter_is_rejected(studio, params):
    assert error_code(lambda: call(studio, "studio.presenters.create", **params)) == "VALIDATION_ERROR"
    assert call(studio, "studio.presenters.list")["presenters"] == []


def test_update_requires_fields_and_existing_presenter(studio):
    presenter = create(studio)
    assert error_code(lambda: call(studio, "studio.presenters.update", id=presenter["id"])) == "VALIDATION_ERROR"
    assert error_code(lambda: call(studio, "studio.presenters.update", id=str(uuid4()), name="X")) == "NOT_FOUND"
    assert error_code(lambda: call(studio, "studio.presenters.delete", id=str(uuid4()))) == "NOT_FOUND"


def test_presenters_survive_reopen(tmp_path):
    first = StudioService(data_dir=tmp_path / "studio")
    presenter = create(first)
    first.close()
    second = StudioService(data_dir=tmp_path / "studio")
    try:
        assert call(second, "studio.presenters.list")["presenters"] == [presenter]
    finally:
        second.close()


def test_import_links_presenters_and_filters_by_presenter(studio, video):
    lan, minh = create(studio), create(studio, name="Anh Minh")
    linked = call(studio, "studio.media.import", source_path=str(video),
                  presenter_ids=[minh["id"], lan["id"], lan["id"]])["media"]
    plain = call(studio, "studio.media.import", source_path=str(video))["media"]
    assert linked["presenter_ids"] == sorted([lan["id"], minh["id"]])
    assert plain["presenter_ids"] == []
    assert [item["id"] for item in call(studio, "studio.media.list", presenter_id=lan["id"])["media"]] == [linked["id"]]
    assert {item["id"] for item in call(studio, "studio.media.list")["media"]} == {linked["id"], plain["id"]}


def test_import_with_unknown_presenter_publishes_nothing(studio, video):
    code = error_code(lambda: call(studio, "studio.media.import", source_path=str(video),
                                   presenter_ids=[str(uuid4())]))
    assert code == "NOT_FOUND"
    assert call(studio, "studio.media.list")["media"] == []
    assert not [path for path in (studio.media_dir).iterdir()]


def test_update_media_relinks_product_and_presenters(studio, video):
    product = call(studio, "studio.products.create", name="Áo linen")["product"]
    lan, minh = create(studio), create(studio, name="Anh Minh")
    item = call(studio, "studio.media.import", source_path=str(video), presenter_ids=[lan["id"]])["media"]
    updated = call(studio, "studio.media.update", id=item["id"], product_id=product["id"],
                   presenter_ids=[minh["id"]])["media"]
    assert updated["product_id"] == product["id"] and updated["presenter_ids"] == [minh["id"]]
    # Each field is optional; omitted fields keep their links.
    unlinked = call(studio, "studio.media.update", id=item["id"], product_id=None)["media"]
    assert unlinked["product_id"] is None and unlinked["presenter_ids"] == [minh["id"]]
    cleared = call(studio, "studio.media.update", id=item["id"], presenter_ids=[])["media"]
    assert cleared["presenter_ids"] == []


def test_failed_media_update_rolls_back_every_link(studio, video):
    product = call(studio, "studio.products.create", name="Áo linen")["product"]
    lan = create(studio)
    item = call(studio, "studio.media.import", source_path=str(video), presenter_ids=[lan["id"]])["media"]
    code = error_code(lambda: call(studio, "studio.media.update", id=item["id"], product_id=product["id"],
                                   presenter_ids=[str(uuid4())]))
    assert code == "NOT_FOUND"
    current = call(studio, "studio.media.list")["media"][0]
    assert current["product_id"] is None and current["presenter_ids"] == [lan["id"]]


@pytest.mark.parametrize("params", [{}, {"presenter_ids": "not-a-list"}, {"presenter_ids": ["x"]},
                                    {"presenter_ids": [str(uuid4())] * 201}, {"sha256": "0" * 64}])
def test_invalid_media_update_is_rejected(studio, video, params):
    item = call(studio, "studio.media.import", source_path=str(video))["media"]
    assert error_code(lambda: call(studio, "studio.media.update", id=item["id"], **params)) == "VALIDATION_ERROR"


def test_deleting_presenter_or_media_removes_links_only(studio, video):
    lan, minh = create(studio), create(studio, name="Anh Minh")
    item = call(studio, "studio.media.import", source_path=str(video),
                presenter_ids=[lan["id"], minh["id"]])["media"]
    call(studio, "studio.presenters.delete", id=lan["id"])
    current = call(studio, "studio.media.list")["media"]
    assert [entry["presenter_ids"] for entry in current] == [[minh["id"]]]
    assert current[0]["status"] == "ready"
    call(studio, "studio.media.delete", id=item["id"])
    connection = sqlite3.connect(studio.database_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM media_presenters").fetchone()[0] == 0
    finally:
        connection.close()
    assert [entry["id"] for entry in call(studio, "studio.presenters.list")["presenters"]] == [minh["id"]]
