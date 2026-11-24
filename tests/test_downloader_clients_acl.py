"""
下载器客户端列表按用户可见性测试。
"""

import asyncio
from types import SimpleNamespace

from app.api.endpoints import download as download_endpoint


def _fake_config(configs):
    class FakeSystemConfig:
        """
        返回测试用下载器配置。
        """

        def get(self, key):
            return configs

    return FakeSystemConfig()


def _user(name="alice", is_superuser=False):
    return SimpleNamespace(id=1, name=name, is_superuser=is_superuser)


def test_downloader_clients_hides_instances_not_allowed_for_user(monkeypatch) -> None:
    """
    普通用户看不到 allowed_users 不含自己的下载器。
    """
    monkeypatch.setattr(
        download_endpoint,
        "get_configured_system_config",
        lambda: _fake_config(
            [
                {"name": "alice 的 TR", "type": "transmission", "enabled": True, "allowed_users": ["alice"]},
                {"name": "bob 的 qB", "type": "qbittorrent", "enabled": True, "allowed_users": ["bob"]},
                {"name": "停用受限", "type": "qbittorrent", "enabled": False, "allowed_users": ["alice"]},
            ]
        ),
    )

    result = asyncio.run(download_endpoint.clients(current_user=_user(name="alice")))

    assert result == [{"name": "alice 的 TR", "type": "transmission"}]


def test_downloader_clients_includes_instances_with_empty_allowed_users(monkeypatch) -> None:
    """
    空 allowed_users（含旧配置无该字段）表示所有用户可用。
    """
    monkeypatch.setattr(
        download_endpoint,
        "get_configured_system_config",
        lambda: _fake_config(
            [
                {"name": "公共 qB", "type": "qbittorrent", "enabled": True, "allowed_users": []},
                {"name": "无字段 rT", "type": "rtorrent", "enabled": True},
            ]
        ),
    )

    result = asyncio.run(download_endpoint.clients(current_user=_user(name="alice")))

    assert result == [
        {"name": "公共 qB", "type": "qbittorrent"},
        {"name": "无字段 rT", "type": "rtorrent"},
    ]


def test_downloader_clients_superuser_sees_all(monkeypatch) -> None:
    """
    superuser 不受 allowed_users 限制，可见全部已启用下载器。
    """
    monkeypatch.setattr(
        download_endpoint,
        "get_configured_system_config",
        lambda: _fake_config(
            [
                {"name": "公共 qB", "type": "qbittorrent", "enabled": True, "allowed_users": []},
                {"name": "alice 的 TR", "type": "transmission", "enabled": True, "allowed_users": ["alice"]},
                {"name": "bob 的 qB", "type": "qbittorrent", "enabled": True, "allowed_users": ["bob"]},
            ]
        ),
    )

    result = asyncio.run(
        download_endpoint.clients(current_user=_user(name="admin", is_superuser=True))
    )

    assert result == [
        {"name": "公共 qB", "type": "qbittorrent"},
        {"name": "alice 的 TR", "type": "transmission"},
        {"name": "bob 的 qB", "type": "qbittorrent"},
    ]
