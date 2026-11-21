"""
覆盖 TMDB 模块的边界分支和同步异步入口。
"""

import asyncio
from typing import Optional
from unittest.mock import AsyncMock, Mock

import pytest

from app.domain.context import MediaInfo
from app.domain.meta.metabase import MetaBase
from app.modules.themoviedb import TheMovieDbModule, _release_year_params
from app.modules.themoviedb.tmdbv3api.exceptions import TMDbConnectionError
from app.schemas.category import ClassificationEnrichmentRequest
from app.schemas.types import MediaSource, MediaType


def _module() -> TheMovieDbModule:
    """
    构造注入测试客户端的 TMDB 模块。
    """
    module = object.__new__(TheMovieDbModule)
    module.tmdb = Mock()
    module.cache = Mock()
    module.category = Mock()
    return module


def _request(media_type: str = "电影", missing_fields: Optional[list[str]] = None) -> ClassificationEnrichmentRequest:
    """
    构造分类事实补充请求。
    """
    return ClassificationEnrichmentRequest(
        identity={"media_source": "themoviedb", "media_id": "1"},
        media_type=media_type,
        missing_fields=missing_fields or ["genres"],
        external_ids={"themoviedb": "1"},
        policy_revision=1,
        timeout_seconds=1.0,
    )


def test_module_metadata_and_classification_boundaries() -> None:
    """
    模块元数据和分类补充应覆盖无效请求、缺失事实和成功响应。
    """
    module = _module()
    assert module.get_name() == "TheMovieDb"
    assert module.get_type().value
    assert module.get_subtype().value
    assert module.get_priority() == 1
    assert module.init_setting() is None
    assert module.get_media_classification_facts(_request(media_type="音乐")) is None
    module.tmdb.get_info.return_value = None
    assert module.get_media_classification_facts(_request(missing_fields=["genres"])) is None
    module.tmdb.get_info.return_value = {
        "id": 1,
        "media_type": MediaType.MOVIE,
        "title": "测试",
        "genres": [{"id": 18, "name": "剧情"}],
    }
    result = module.get_media_classification_facts(_request(missing_fields=["media.title", "unknown"]))
    assert result is not None
    assert result.match.media_id == "1"
    assert "media.title" in result.facts
    assert "unknown" not in result.facts
    module.tmdb.get_info.return_value = None
    assert module.get_media_classification_facts(_request()) is None


def test_module_connection_test_and_plan_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    连接测试和识别计划应分别返回成功、状态码失败、网络失败及拒绝原因。
    """
    module = _module()
    monkeypatch.setattr("app.modules.themoviedb.RequestUtils", lambda **_: Mock(get_res=Mock(return_value=Mock(status_code=200))))
    assert module.test() == (True, "")
    monkeypatch.setattr("app.modules.themoviedb.RequestUtils", lambda **_: Mock(get_res=Mock(return_value=Mock(status_code=503))))
    assert module.test()[0] is False
    monkeypatch.setattr("app.modules.themoviedb.RequestUtils", lambda **_: Mock(get_res=Mock(return_value=None)))
    assert module.test()[0] is False
    meta = MetaBase("测试 2024")
    assert module._build_recognize_plan(meta, MediaType.MUSIC, None, None, None, True) is None
    assert module._build_recognize_plan(meta, MediaType.MOVIE, MediaSource.Douban, None, None, True) is None
    assert module._build_recognize_plan(meta, MediaType.MOVIE, MediaSource.TMDB, "bad", None, True) is None
    assert module._build_recognize_plan(None, None, None, None, None, True) is None


def test_module_search_and_episode_group_helpers() -> None:
    """
    搜索计划、剧集组年份和媒体结果应覆盖空值与有效值分支。
    """
    meta = MetaBase("测试 2024")
    assert TheMovieDbModule._build_media_search_plan(meta, None) is not None
    meta.name = ""
    plan = TheMovieDbModule._build_media_search_plan(meta, None)
    assert plan is not None and not plan.search_multi
    meta.name = "测试"
    meta.type = MediaType.UNKNOWN
    meta.year = None
    plan = TheMovieDbModule._build_media_search_plan(meta, None)
    assert plan is not None and plan.search_multi
    assert TheMovieDbModule._merge_media_search_results(plan, multi_results=[{"id": 1}]) == [{"id": 1}]
    tv = MediaInfo(tmdb_info={"id": 1, "media_type": MediaType.TV, "name": "测试", "first_air_date": "2024-01-01"})
    group = [{"order": 1, "episodes": [{"episode_number": 1, "air_date": "2024-02-03"}]}]
    TheMovieDbModule._fill_group_season_info(tv, "group", group)
    assert tv.seasons == {1: [1]}
    assert tv.season_years == {1: "2024"}
    assert TheMovieDbModule._collect_group_season_years(group) == {1: "2024"}
    search_meta = MetaBase("测试")
    search_meta.begin_season = 2
    result = TheMovieDbModule._build_search_medias_result(search_meta, [{"id": 1, "media_type": MediaType.TV, "name": "测试"}])
    assert result[0].season == 2


def test_module_identity_disambiguation_and_cache_management() -> None:
    """
    身份消歧、缓存回填和缓存管理应保留成功及拒绝分支。
    """
    module = _module()
    meta = MetaBase("同名 2024")
    meta.type = MediaType.TV
    tv = {"id": 1, "name": "同名", "first_air_date": "2024-01-01"}
    movie = {"id": 2, "title": "其它", "release_date": "2020-01-01"}
    assert module._resolve_tmdbid_candidates(1, meta, tv, False, movie, False) == tv
    assert TheMovieDbModule._disambiguate_by_meta(tv, movie, meta) == tv
    assert TheMovieDbModule._disambiguate_by_meta(tv, movie, None) is None
    with pytest.raises(TMDbConnectionError):
        module._resolve_tmdbid_candidates(1, None, None, True, None, False)
    info = MediaInfo(tmdb_info={"id": 1, "media_type": MediaType.MOVIE, "title": "测试"})
    assert module.update_recognize_cache(meta, info) is True
    info.media_source = "douban"
    assert module.update_recognize_cache(meta, info) is None
    assert module.tmdb_cache_items() == module.cache.list_items.return_value
    module.tmdb_cache_delete("key")
    module.tmdb_cache_clear()
    module.clear_cache = Mock()


def test_module_match_and_image_sync_async_boundaries() -> None:
    """
    匹配详情、图片补全和异步入口应覆盖有效及空响应。
    """
    module = _module()
    module.tmdb.match.return_value = {"id": 1, "media_type": MediaType.MOVIE, "title": "测试"}
    assert module.match_tmdbinfo("测试") is not None
    module.tmdb.match.return_value = {"id": 1, "media_type": MediaType.MOVIE}
    module.tmdb.get_info.return_value = {"id": 1, "media_type": MediaType.MOVIE, "title": "测试"}
    assert module.match_tmdbinfo("测试") is not None
    module.tmdb.async_match = AsyncMock(return_value=None)
    assert asyncio.run(module.async_match_tmdbinfo("测试")) is None
    module.tmdb.get_info.return_value = {"id": 1, "media_type": MediaType.MOVIE}
    assert module.tmdb_info(1, MediaType.MOVIE, 0) is not None
    module.tmdb.get_tv_season_detail.return_value = {"id": 1}
    assert module.tmdb_info(1, MediaType.TV, 1) == {"id": 1}
    module.tmdb.async_get_info = AsyncMock(return_value=None)
    assert asyncio.run(module.async_tmdb_info(1, MediaType.MOVIE)) is None
    assert TheMovieDbModule._pick_best_tmdb_image(None) is None


@pytest.mark.parametrize(
    ("release_year", "mtype", "expected"),
    [
        ("", MediaType.MOVIE, {}),
        ("", MediaType.TV, {}),
        (None, MediaType.MOVIE, {}),
        ("2024", MediaType.MOVIE, {"primary_release_year": 2024}),
        ("2024", MediaType.TV, {"first_air_date_year": 2024}),
        (
            "2020s",
            MediaType.MOVIE,
            {
                "primary_release_date.gte": "2020-01-01",
                "primary_release_date.lte": "2029-12-31",
            },
        ),
        (
            "2020s",
            MediaType.TV,
            {
                "first_air_date.gte": "2020-01-01",
                "first_air_date.lte": "2029-12-31",
            },
        ),
        (
            "1990s",
            MediaType.MOVIE,
            {
                "primary_release_date.gte": "1990-01-01",
                "primary_release_date.lte": "1999-12-31",
            },
        ),
        ("abc", MediaType.MOVIE, {}),
        ("20", MediaType.TV, {}),
        ("20201", MediaType.MOVIE, {}),
        ("2025s", MediaType.TV, {}),
    ],
)
def test_release_year_params_maps_year_codes_and_ignores_dirty_values(
    release_year: Optional[str], mtype: MediaType, expected: dict
) -> None:
    """
    年份 code 应精确映射为电影与剧集的 TMDB discover 原生参数，脏值直接忽略。
    """
    assert _release_year_params(release_year, mtype) == expected


def test_discover_sync_async_merge_release_year_params() -> None:
    """
    同步与异步发现入口应把映射后的年份参数合并进各自的 TMDB discover 请求。
    """
    module = _module()
    module.tmdb.discover_movies.return_value = []
    module.tmdb.async_discover_movies = AsyncMock(return_value=[])
    request = {
        "mtype": MediaType.MOVIE,
        "sort_by": "popularity.desc",
        "with_genres": "",
        "with_original_language": "",
        "with_keywords": "",
        "with_watch_providers": "",
        "vote_average": 0.0,
        "vote_count": 0,
        "release_date": "",
        "release_year": "2020s",
    }

    assert module.tmdb_discover(**request) == []
    assert asyncio.run(module.async_tmdb_discover(**request)) == []

    sync_params = module.tmdb.discover_movies.call_args.args[0]
    async_params = module.tmdb.async_discover_movies.call_args.args[0]
    expected = {
        "primary_release_date.gte": "2020-01-01",
        "primary_release_date.lte": "2029-12-31",
    }
    assert sync_params == async_params
    assert {key: sync_params[key] for key in expected} == expected
