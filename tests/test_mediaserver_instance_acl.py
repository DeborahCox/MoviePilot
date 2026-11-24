"""
媒体服务器「按名访问/遍历实例」端点的 allowed_users ACL 测试。

受限实例不能通过显式 server 参数访问，也不能在省略 server 时被遍历到；
superuser 与名单内用户不受限制。
"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.endpoints import mediaserver as endpoint
from app.chain.download.existence import DownloadExistenceOwner
from app.schemas.mediaserver import ExistMediaInfo, MediaServerPlayItem
from app.schemas.system import MediaServerConf
from app.schemas.token import TokenPayload
from app.schemas.types import MediaType


def _configs():
    return [
        MediaServerConf(name="公共 Emby", type="emby", enabled=True, allowed_users=[]),
        MediaServerConf(name="alice 的 Emby", type="emby", enabled=True, allowed_users=["alice"]),
        MediaServerConf(name="bob 的 Plex", type="plex", enabled=True, allowed_users=["bob"]),
    ]


def _user(name="alice", super_user=False):
    return TokenPayload(sub=1, username=name, super_user=super_user)


def _client(monkeypatch, userinfo, configs=None):
    app = FastAPI()
    app.include_router(endpoint.router, prefix="/mediaserver")
    app.dependency_overrides[endpoint.verify_token] = lambda: userinfo
    app.dependency_overrides[endpoint.verify_resource_token] = lambda: userinfo
    monkeypatch.setattr(
        endpoint, "get_mediaserver_configs", lambda: _configs() if configs is None else configs
    )
    return TestClient(app)


def test_play_item_only_iterates_visible_instances(monkeypatch):
    """
    在线播放遍历的配置必须按可见名单过滤，受限实例不会收到查询。
    """
    tried = []

    def fake_iteminfo(self, **kwargs):
        tried.append(kwargs["server"])
        return None

    monkeypatch.setattr(endpoint.MediaServerChain, "iteminfo", fake_iteminfo)

    client = _client(monkeypatch, _user("alice"))
    assert client.get("/mediaserver/play/m1").json()["success"] is False
    assert tried == ["公共 Emby", "alice 的 Emby"]

    tried.clear()
    client = _client(monkeypatch, _user("admin", super_user=True))
    client.get("/mediaserver/play/m1")
    assert set(tried) == {"公共 Emby", "alice 的 Emby", "bob 的 Plex"}


def test_item_detail_explicit_restricted_instance_returns_404(monkeypatch):
    """
    显式指定无权实例返回 404，且不会向下游发起查询，避免泄露实例存在性。
    """
    called = []

    def fake_detail(self, **kwargs):
        called.append(kwargs["server"])
        return None

    monkeypatch.setattr(endpoint.MediaServerChain, "item_detail", fake_detail)

    client = _client(monkeypatch, _user("alice"))
    resp = client.get("/mediaserver/detail", params={"itemid": "m1", "server": "bob 的 Plex"})
    assert resp.status_code == 404
    assert called == []

    client = _client(monkeypatch, _user("bob"))
    resp = client.get("/mediaserver/detail", params={"itemid": "m1", "server": "bob 的 Plex"})
    assert resp.status_code == 404
    assert called == ["bob 的 Plex"]


def test_item_detail_auto_iteration_excludes_restricted_instance(monkeypatch):
    """
    省略 server 时的遍历列表必须过滤；受限实例即使能返回详情也不能被无权用户命中。
    """
    tried = []

    def fake_detail(self, **kwargs):
        server = kwargs["server"]
        tried.append(server)
        if server == "bob 的 Plex":
            return SimpleNamespace(item_id="m1", title="受限详情", item_type="Movie")
        return None

    monkeypatch.setattr(endpoint.MediaServerChain, "item_detail", fake_detail)

    client = _client(monkeypatch, _user("alice"))
    assert client.get("/mediaserver/detail", params={"itemid": "m1"}).status_code == 404
    assert tried == ["公共 Emby", "alice 的 Emby"]

    tried.clear()
    client = _client(monkeypatch, _user("admin", super_user=True))
    resp = client.get("/mediaserver/detail", params={"itemid": "m1"})
    assert resp.status_code == 200 and "bob 的 Plex" in tried


@pytest.mark.parametrize("path", ["/mediaserver/seasons", "/mediaserver/season-episodes"])
def test_season_endpoints_reject_restricted_instance(monkeypatch, path):
    """
    季与分集端点显式指定无权实例时返回 404，名单内用户可正常查询。
    """
    called = []
    monkeypatch.setattr(
        endpoint.MediaServerChain, "seasons",
        lambda self, **kwargs: called.append(kwargs["server"]) or [],
    )
    monkeypatch.setattr(
        endpoint.MediaServerChain, "season_episodes",
        lambda self, **kwargs: called.append(kwargs["server"]) or [],
    )

    client = _client(monkeypatch, _user("alice"))
    resp = client.get(path, params={"itemid": "s1", "server": "bob 的 Plex"})
    assert resp.status_code == 404
    assert called == []

    client = _client(monkeypatch, _user("bob"))
    resp = client.get(path, params={"itemid": "s1", "server": "bob 的 Plex"})
    assert resp.status_code != 404
    assert called == ["bob 的 Plex"]


@pytest.mark.parametrize("path", ["/mediaserver/browse", "/mediaserver/filters"])
def test_browse_and_filters_reject_restricted_instance(monkeypatch, path):
    """
    浏览与筛选端点显式指定无权实例时返回 404，名单内用户可继续走下游逻辑。
    """
    called = []
    monkeypatch.setattr(
        endpoint.MediaServerChain, "browse_items",
        lambda self, **kwargs: called.append(kwargs["server"]) or None,
    )
    monkeypatch.setattr(
        endpoint.MediaServerChain, "filter_options",
        lambda self, **kwargs: called.append(kwargs["server"]) or None,
    )

    client = _client(monkeypatch, _user("alice"))
    resp = client.get(path, params={"server": "bob 的 Plex"})
    assert resp.status_code == 404
    assert called == []

    client = _client(monkeypatch, _user("bob"))
    resp = client.get(path, params={"server": "bob 的 Plex"})
    assert resp.status_code != 404
    assert called == ["bob 的 Plex"]


def test_configured_stream_hosts_filters_restricted_instances(monkeypatch):
    """
    流主机白名单只汇总当前用户可见实例的主机，superuser 可见全部。
    """
    configs = [
        MediaServerConf(name="公共", config={"api_url": "http://public.local"}, allowed_users=[]),
        MediaServerConf(name="bob 的", config={"api_url": "http://bob.local"}, allowed_users=["bob"]),
    ]
    monkeypatch.setattr(endpoint, "get_mediaserver_configs", lambda: configs)
    monkeypatch.setattr(endpoint.StorageHelper, "get_storagies", lambda: [])

    hosts = endpoint._configured_stream_hosts(
        None, include_media_servers=True, username="alice", is_superuser=False
    )
    assert hosts == {"public.local"}

    hosts = endpoint._configured_stream_hosts(
        None, include_media_servers=True, username="bob", is_superuser=False
    )
    assert hosts == {"public.local", "bob.local"}

    hosts = endpoint._configured_stream_hosts(
        None, include_media_servers=True, username="admin", is_superuser=True
    )
    assert hosts == {"public.local", "bob.local"}


def test_stream_info_rejects_explicit_and_resolved_restricted_instance(monkeypatch):
    """
    流信息端点对显式指定和隐式解析出的受限实例都必须返回 404。
    """
    monkeypatch.setattr(
        endpoint,
        "_resolve_stream_item",
        lambda itemid, user_id, server=None: (
            "bob 的 Plex", SimpleNamespace(item_id=itemid, item_type="Movie"), None
        ),
    )
    client = _client(monkeypatch, _user("alice"))
    assert client.get("/mediaserver/stream-info", params={"itemid": "m1", "server": "bob 的 Plex"}).status_code == 404
    assert client.get("/mediaserver/stream-info", params={"itemid": "m1"}).status_code == 404

    client = _client(monkeypatch, _user("bob"))
    monkeypatch.setattr(endpoint.MediaServerChain, "get_stream_url", lambda self, *a, **kw: None)
    monkeypatch.setattr(endpoint.MediaServerChain, "item_detail", lambda self, **kw: None)
    resp = client.get("/mediaserver/stream-info", params={"itemid": "m1"})
    assert resp.status_code != 404


def test_search_library_rejects_restricted_instance(monkeypatch):
    """
    搜索端点指定受限实例时返回 404，不会向该实例发起搜索。
    """
    called = []
    monkeypatch.setattr(
        endpoint.MediaServerChain, "search_library",
        lambda self, *args, **kwargs: called.append(kwargs.get("server")) or [],
    )
    client = _client(monkeypatch, _user("alice"))
    resp = client.get("/mediaserver/search", params={"keyword": "test", "server": "bob 的 Plex"})
    assert resp.status_code == 404
    assert called == []


def test_search_library_without_server_touches_only_visible_instances(monkeypatch):
    """
    省略 server 时只搜索当前用户可见实例；受限实例既不接收查询，也不出现在结果中。
    """
    queried = []

    def fake_search(self, keyword, types, limit, **kwargs):
        server = kwargs.get("server")
        queried.append(server)
        return [MediaServerPlayItem(server=server, item_id=f"{server}-1", title="命中", type="电影")]

    monkeypatch.setattr(endpoint.MediaServerChain, "search_library", fake_search)

    client = _client(monkeypatch, _user("alice"))
    resp = client.get("/mediaserver/search", params={"keyword": "test"})
    assert resp.status_code == 200
    assert [item["server"] for item in resp.json()["data"]] == ["公共 Emby", "alice 的 Emby"]
    assert queried == ["公共 Emby", "alice 的 Emby"]

    queried.clear()
    client = _client(monkeypatch, _user("admin", super_user=True))
    resp = client.get("/mediaserver/search", params={"keyword": "test"})
    assert [item["server"] for item in resp.json()["data"]] == [
        "公共 Emby", "alice 的 Emby", "bob 的 Plex",
    ]
    assert queried == ["公共 Emby", "alice 的 Emby", "bob 的 Plex"]


def test_exists_local_hides_restricted_instance(monkeypatch):
    """
    本地存在性结果必须在响应层按 ACL 过滤，受限实例名和条目标识都不外泄。
    """

    async def fake_find_item(**kwargs):
        return SimpleNamespace(item_id="m1", server="bob 的 Plex")

    service = SimpleNamespace(find_item=fake_find_item)
    client = _client(monkeypatch, _user("alice"))
    client.app.dependency_overrides[endpoint.get_mediaserver_query_service] = lambda: service
    resp = client.get("/mediaserver/exists", params={"title": "测试"})
    assert resp.status_code == 200
    assert resp.json()["data"]["item"] == {}

    client = _client(monkeypatch, _user("bob"))
    client.app.dependency_overrides[endpoint.get_mediaserver_query_service] = lambda: service
    resp = client.get("/mediaserver/exists", params={"title": "测试"})
    assert resp.json()["data"]["item"] == {"id": "m1", "server": "bob 的 Plex"}

    client = _client(monkeypatch, _user("admin", super_user=True))
    client.app.dependency_overrides[endpoint.get_mediaserver_query_service] = lambda: service
    resp = client.get("/mediaserver/exists", params={"title": "测试"})
    assert resp.json()["data"]["item"] == {"id": "m1", "server": "bob 的 Plex"}


def test_exists_remote_only_queries_visible_instances(monkeypatch):
    """
    远端存在性查询只能遍历当前用户可见实例，不能从受限实例拿到剧集数据。
    """
    queried = []

    def fake_media_exists(self, mediainfo, query=None, server=None, **kwargs):
        queried.append(server)
        seasons = {1: [1, 2]}
        if server == "bob 的 Plex":
            seasons[2] = [3]
        return SimpleNamespace(seasons=seasons)

    monkeypatch.setattr(endpoint.MediaServerChain, "media_exists", fake_media_exists)
    payload = {"title": "测试", "type": "电视剧"}

    client = _client(monkeypatch, _user("alice"))
    resp = client.post("/mediaserver/exists_remote", json=payload)
    assert resp.status_code == 200
    assert resp.json()["data"] == {"1": [1, 2]}
    assert queried == ["公共 Emby", "alice 的 Emby"]

    queried.clear()
    client = _client(monkeypatch, _user("admin", super_user=True))
    resp = client.post("/mediaserver/exists_remote", json=payload)
    assert resp.json()["data"] == {"1": [1, 2], "2": [3]}
    assert queried == ["公共 Emby", "alice 的 Emby", "bob 的 Plex"]


def test_not_exists_limits_instance_scope(monkeypatch):
    """
    缺集查询只传入当前用户可见实例，不让受限实例影响缺失判断。
    """
    captured = {}

    class FakeDownloadChain:
        def get_no_exists_info(self, **kwargs):
            captured["server_names"] = kwargs.get("server_names")
            return False, {}

    monkeypatch.setattr(endpoint, "DownloadChain", FakeDownloadChain)
    payload = {
        "title": "测试", "type": "电视剧",
        "media_source": "themoviedb", "media_id": "123",
    }

    client = _client(monkeypatch, _user("alice"))
    resp = client.post("/mediaserver/notexists", json=payload)
    assert resp.status_code == 200
    assert captured["server_names"] == ["公共 Emby", "alice 的 Emby"]

    client = _client(monkeypatch, _user("admin", super_user=True))
    resp = client.post("/mediaserver/notexists", json=payload)
    assert resp.status_code == 200
    assert captured["server_names"] == ["公共 Emby", "alice 的 Emby", "bob 的 Plex"]


def test_existence_owner_honors_explicit_server_scope(monkeypatch):
    """
    缺集计算层必须按传入白名单遍历实例；空名单不触碰任何实例。
    """
    queried = []
    owner = DownloadExistenceOwner.__new__(DownloadExistenceOwner)
    monkeypatch.setattr(
        "app.chain.download.existence.get_mediaserver_configs",
        lambda: [SimpleNamespace(name="公共 Emby"), SimpleNamespace(name="bob 的 Plex")],
    )
    monkeypatch.setattr(
        owner, "media_server_repository",
        SimpleNamespace(get_item_id=lambda **kwargs: None),
        raising=False,
    )

    def fake_media_exists(**kwargs):
        queried.append(kwargs.get("server"))
        return ExistMediaInfo(type=MediaType.TV, seasons={1: [1, 2]})

    monkeypatch.setattr(owner, "media_exists", fake_media_exists)
    mediainfo = SimpleNamespace(type=MediaType.TV, title_year="测试")

    result = owner._media_exists_with_server_cache(mediainfo, query={}, server_names=["公共 Emby"])
    assert result is not None
    assert queried == ["公共 Emby"]

    queried.clear()
    result = owner._media_exists_with_server_cache(mediainfo, query={}, server_names=[])
    assert result is None
    assert queried == []
