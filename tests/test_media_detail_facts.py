"""
验证媒体详情新事实字段：TMDB 成本/收入投影与 IMDb 评分聚合。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.endpoints import media as media_endpoint
from app.domain.context import MediaInfo as CoreMediaInfo
from app.domain.projection import tmdb as tmdb_projection
from app.schemas.types import MediaSource, MediaType


def test_tmdb_movie_projection_exposes_budget_and_revenue() -> None:
    """
    TMDB 电影详情投影应把成本和收入透传给详情接口。
    """
    result = tmdb_projection.project({}, {
        "id": 42,
        "media_type": "movie",
        "title": "The Odyssey",
        "budget": 250000000,
        "revenue": 1804589435,
    })

    assert result["budget"] == 250000000
    assert result["revenue"] == 1804589435


class _FakeImdbApi:
    """不发起网络请求的 IMDb API 替身。"""

    rating: float | None = 8.4
    raises: bool = False

    def __init__(self, proxies=None) -> None:
        pass

    async def async_get_title(self, imdb_id: str):
        if self.raises:
            raise RuntimeError("imdb unavailable")
        return SimpleNamespace(rating=SimpleNamespace(aggregate_rating=self.rating))

    def close(self) -> None:
        pass


def _media_info() -> CoreMediaInfo:
    info = CoreMediaInfo(
        media_source=MediaSource.TMDB,
        type=MediaType.MOVIE,
        title="The Odyssey",
        tmdb_id=42,
        imdb_id="tt1234567",
    )
    # 投影字段通过 __dict__ 进入 to_dict()，不要求 dataclass 声明
    info.budget = 250000000
    info.revenue = 1804589435
    return info


async def _request_detail(monkeypatch: pytest.MonkeyPatch) -> dict:
    chain = Mock()
    chain.async_recognize_media = AsyncMock(return_value=_media_info())
    chain.async_obtain_images = AsyncMock(return_value=None)
    monkeypatch.setattr(media_endpoint, "MediaChain", Mock(return_value=chain))
    monkeypatch.setattr(media_endpoint, "ImdbApi", _FakeImdbApi)
    monkeypatch.setattr(media_endpoint, "get_runtime_setting", lambda name: None)

    app = FastAPI()
    app.dependency_overrides[media_endpoint.verify_token] = lambda: None
    app.include_router(media_endpoint.router, prefix="/api/v1/media")
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/v1/media/42",
            params={"media_source": MediaSource.TMDB.value, "type_name": MediaType.MOVIE.value},
        )
    assert response.status_code == 200
    return response.json()["data"]


@pytest.mark.asyncio
async def test_media_detail_exposes_imdb_rating_and_financials(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    详情接口应返回真实 IMDb 评分、成本和收入，且通过响应模型序列化。
    """
    data = await _request_detail(monkeypatch)

    assert data["imdbRating"] == 8.4
    assert data["budget"] == 250000000
    assert data["revenue"] == 1804589435


@pytest.mark.asyncio
async def test_media_detail_ignores_out_of_range_imdb_rating(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    超出十分制范围的 IMDb 评分视为无效，不污染详情响应。
    """
    _FakeImdbApi.rating = 11.0
    try:
        data = await _request_detail(monkeypatch)
    finally:
        _FakeImdbApi.rating = 8.4

    assert data["imdbRating"] is None


@pytest.mark.asyncio
async def test_media_detail_survives_imdb_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    IMDb 查询异常时详情主响应仍应正常返回。
    """
    _FakeImdbApi.raises = True
    try:
        data = await _request_detail(monkeypatch)
    finally:
        _FakeImdbApi.raises = False

    assert data["imdbRating"] is None
    assert data["budget"] == 250000000
