"""
下载任务查询端点按下载器 allowed_users 过滤的测试。
"""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.endpoints import download as endpoint
from app.schemas.context import MediaInfo as _SchemaMediaInfo
from app.schemas.context import TorrentInfo as _SchemaTorrentInfo
from app.schemas.download import DownloadSourceClassificationRequest, DownloadTaskUpdateRequest
from app.schemas.types import MediaSource


def _configs():
    return [
        {"name": "公共 qB", "type": "qbittorrent", "enabled": True, "allowed_users": []},
        {"name": "alice 的 TR", "type": "transmission", "enabled": True, "allowed_users": ["alice"]},
        {"name": "bob 的 qB", "type": "qbittorrent", "enabled": True, "allowed_users": ["bob"]},
        {"name": "停用受限", "type": "qbittorrent", "enabled": False, "allowed_users": ["alice"]},
    ]


def _principal(name="alice", superuser=False, manage_downloading=False):
    permissions = (
        {"manage": True, "features": {"manage.downloading": True}}
        if manage_downloading
        else {"search": True, "manage": False}
    )
    return SimpleNamespace(
        id=1, name=name, is_active=True, is_superuser=superuser,
        authorization_id=None, permissions=permissions,
    )


def _setup(monkeypatch):
    monkeypatch.setattr(
        endpoint, "get_configured_system_config",
        lambda: SimpleNamespace(get=lambda key: _configs()),
    )
    queried = []
    tasks = [
        SimpleNamespace(username="alice", downloader="公共 qB", title="P"),
        SimpleNamespace(username="alice", downloader="alice 的 TR", title="A"),
        SimpleNamespace(username="bob", downloader="bob 的 qB", title="B"),
    ]

    def downloading(name):
        queried.append(name)
        if name is None:
            return tasks
        return [task for task in tasks if task.downloader == name]

    monkeypatch.setattr(endpoint, "DownloadChain", lambda: SimpleNamespace(downloading=downloading))
    return queried


def test_current_rejects_explicit_restricted_downloader(monkeypatch):
    """
    显式指定无权下载器返回空列表，且不会向该下载器发起查询。
    """
    queried = _setup(monkeypatch)
    assert endpoint.current(name="bob 的 qB", _=_principal("alice")) == []
    assert queried == []


def test_current_allows_allowed_downloader(monkeypatch):
    """
    名单内用户可以查询自己的任务。
    """
    queried = _setup(monkeypatch)
    result = endpoint.current(name="alice 的 TR", _=_principal("alice"))
    assert [task.title for task in result] == ["A"]
    assert queried == ["alice 的 TR"]


def test_current_without_name_filters_restricted_downloaders(monkeypatch):
    """
    省略下载器名时，遍历全部下载器的任务必须按可见实例过滤。
    """
    queried = _setup(monkeypatch)
    result = endpoint.current(_=_principal("alice"))
    assert [task.title for task in result] == ["P", "A"]
    assert queried == [None]


def test_current_superuser_sees_all_instances(monkeypatch):
    """
    superuser 不受 allowed_users 限制。
    """
    _setup(monkeypatch)
    assert [t.title for t in endpoint.current(_=_principal("admin", superuser=True))] == ["P", "A", "B"]
    assert [t.title for t in endpoint.current(name="bob 的 qB", _=_principal("admin", superuser=True))] == ["B"]


def test_current_manage_permission_does_not_bypass_instance_acl(monkeypatch):
    """
    下载管理权限只放宽「查看所有用户任务」，不能绕过下载器实例 ACL。
    """
    _setup(monkeypatch)
    # 管理权限允许查看所有用户的任务，但 alice 的可见实例仍只有公共 qB 与 alice 的 TR，
    # 来自 bob 的 qB 的任务即便属于其他用户也必须被实例 ACL 排除。
    result = endpoint.current(_=_principal("alice", manage_downloading=True))
    assert [task.title for task in result] == ["P", "A"]


def test_paths_endpoint_unchanged():
    """
    /download/paths 仅返回目录，与下载器实例无关，保持原有行为（无 ACL 依赖）。
    """
    assert endpoint.paths.__name__ == "paths"


_HASH = "a" * 40


def _setup_write(monkeypatch):
    """
    为下载写端点装配配置与下载器替身，记录所有下游调用。
    """
    monkeypatch.setattr(
        endpoint, "get_configured_system_config",
        lambda: SimpleNamespace(get=lambda key: _configs()),
    )
    monkeypatch.setattr(endpoint, "_is_tmdb_recognize_source", lambda: False)
    monkeypatch.setattr(
        endpoint, "MediaChain",
        lambda: SimpleNamespace(
            recognize_by_meta=lambda *a, **kw: _SchemaMediaInfo(title="测试", type="电影"),
            recognize_media=lambda *a, **kw: None,
        ),
    )
    monkeypatch.setattr(endpoint, "organize_existing_source", lambda *a, **kw: {})

    calls = []

    class FakeDownloadChain:
        def download_single(self, **kwargs):
            calls.append(("download_single", kwargs))
            return "task-1"

        def set_downloading(self, *args, **kwargs):
            calls.append(("set_downloading", args, kwargs))
            return True

        def remove_downloading(self, hash_string, **kwargs):
            calls.append(("remove_downloading", hash_string, kwargs))
            return True

        def list_torrents(self, *args, **kwargs):
            return []

        def set_torrents_tag(self, *args, **kwargs):
            return True

        def update_torrent(self, *args, **kwargs):
            return {}

    monkeypatch.setattr(endpoint, "DownloadChain", FakeDownloadChain)
    return calls


def _media_in():
    return _SchemaMediaInfo(title="测试", type="电影")


def _torrent_in():
    return _SchemaTorrentInfo(title="测试", category="电影")


def test_write_endpoints_reject_restricted_downloader(monkeypatch):
    """
    所有接受实例名的写端点都不得向受限下载器提交任务。
    """
    calls = _setup_write(monkeypatch)
    user = _principal("alice")

    def denied(fn, *args, **kwargs):
        with pytest.raises(HTTPException) as error:
            fn(*args, **kwargs)
        assert error.value.status_code == 403
        assert calls == []

    denied(
        endpoint.download, media_in=_media_in(), torrent_in=_torrent_in(),
        downloader="bob 的 qB", current_user=user,
    )
    denied(
        endpoint.download_artist_collection, artist_name="甲", artist_id="123",
        media_source=MediaSource.MusicBrainz, torrent_in=_torrent_in(),
        downloader="bob 的 qB", current_user=user,
    )
    denied(
        endpoint.add, torrent_in=_torrent_in(),
        downloader="bob 的 qB", current_user=user,
    )
    denied(endpoint.start, _HASH, "bob 的 qB", user)
    denied(endpoint.stop, _HASH, "bob 的 qB", user)
    denied(endpoint.delete, _HASH, "bob 的 qB", user)
    denied(
        asyncio.run,
        endpoint.update_task(
            _HASH, DownloadTaskUpdateRequest(downloader="bob 的 qB", action="start"),
            _principal("alice"),
        ),
    )
    denied(
        asyncio.run,
        endpoint.classify_source(
            _HASH, DownloadSourceClassificationRequest(downloader="bob 的 qB"),
            _principal("alice"),
        ),
    )


def test_write_endpoints_allow_visible_and_superuser(monkeypatch):
    """
    名单内用户与 superuser 可以正常提交，下载管理权限不能绕过实例 ACL。
    """
    calls = _setup_write(monkeypatch)

    assert endpoint.download(
        media_in=_media_in(), torrent_in=_torrent_in(),
        downloader="alice 的 TR", current_user=_principal("alice"),
    ).success is True
    assert len(calls) == 1

    calls.clear()
    assert endpoint.download(
        media_in=_media_in(), torrent_in=_torrent_in(),
        downloader="bob 的 qB", current_user=_principal("admin", superuser=True),
    ).success is True
    assert len(calls) == 1

    calls.clear()
    assert endpoint.add(
        torrent_in=_torrent_in(), downloader="alice 的 TR",
        current_user=_principal("alice"),
    ).success is True
    assert len(calls) == 1

    calls.clear()
    assert endpoint.download_artist_collection(
        artist_name="甲", artist_id="123", media_source=MediaSource.MusicBrainz,
        torrent_in=_torrent_in(), downloader="alice 的 TR",
        current_user=_principal("alice"),
    ).success is True
    assert len(calls) == 1

    for action in (endpoint.start, endpoint.stop, endpoint.delete):
        calls.clear()
        assert action(_HASH, "alice 的 TR", _principal("alice")).success is True
        assert len(calls) == 1

    calls.clear()
    result = asyncio.run(endpoint.update_task(
        _HASH, DownloadTaskUpdateRequest(downloader="alice 的 TR", action="start"),
        _principal("alice"),
    ))
    assert result.success is True

    calls.clear()
    result = asyncio.run(endpoint.classify_source(
        _HASH, DownloadSourceClassificationRequest(downloader="alice 的 TR"),
        _principal("alice"),
    ))
    assert result.success is True
