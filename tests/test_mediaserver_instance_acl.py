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
from app.schemas.system import MediaServerConf
from app.schemas.token import TokenPayload


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
