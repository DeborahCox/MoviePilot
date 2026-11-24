"""
媒体服务器客户端安全投影接口测试。
"""

import asyncio
from types import SimpleNamespace

from app.api.endpoints import mediaserver as mediaserver_endpoint
from app.schemas.types import SystemConfigKey


def _fake_config(configs, calls=None):
    class FakeSystemConfig:
        """
        返回测试用媒体服务器配置。
        """

        def get(self, key):
            """
            记录读取键并返回测试配置。
            """
            if calls is not None:
                calls.append(key)
            return configs

    return FakeSystemConfig()


def _user(name="alice", is_superuser=False):
    return SimpleNamespace(id=1, name=name, is_superuser=is_superuser)


def test_media_server_clients_only_projects_enabled_names_and_types(monkeypatch) -> None:
    """
    客户端列表不得返回地址、令牌或其他完整连接配置。
    """
    calls = []

    monkeypatch.setattr(
        mediaserver_endpoint,
        "get_configured_system_config",
        lambda: _fake_config(
            [
                {
                    "name": "家庭 Emby",
                    "type": "emby",
                    "enabled": True,
                    "config": {"host": "https://example.invalid", "token": "secret"},
                },
                {
                    "name": "停用 Plex",
                    "type": "plex",
                    "enabled": False,
                    "config": {"token": "disabled-secret"},
                },
            ],
            calls,
        ),
    )

    result = asyncio.run(mediaserver_endpoint.clients(current_user=_user()))

    assert result == [{"name": "家庭 Emby", "type": "emby"}]
    assert calls == [SystemConfigKey.MediaServers]


def test_media_server_clients_hides_instances_not_allowed_for_user(monkeypatch) -> None:
    """
    普通用户看不到 allowed_users 不含自己的实例。
    """
    monkeypatch.setattr(
        mediaserver_endpoint,
        "get_configured_system_config",
        lambda: _fake_config(
            [
                {"name": "alice 的 Emby", "type": "emby", "enabled": True, "allowed_users": ["alice"]},
                {"name": "bob 的 Plex", "type": "plex", "enabled": True, "allowed_users": ["bob"]},
                {"name": "停用受限", "type": "jellyfin", "enabled": False, "allowed_users": ["bob"]},
            ]
        ),
    )

    result = asyncio.run(mediaserver_endpoint.clients(current_user=_user(name="alice")))

    assert result == [{"name": "alice 的 Emby", "type": "emby"}]


def test_media_server_clients_includes_instances_with_empty_allowed_users(monkeypatch) -> None:
    """
    空 allowed_users（含旧配置无该字段）表示所有用户可用。
    """
    monkeypatch.setattr(
        mediaserver_endpoint,
        "get_configured_system_config",
        lambda: _fake_config(
            [
                {"name": "公共 Jellyfin", "type": "jellyfin", "enabled": True, "allowed_users": []},
                {"name": "无字段 Navidrome", "type": "navidrome", "enabled": True},
            ]
        ),
    )

    result = asyncio.run(mediaserver_endpoint.clients(current_user=_user(name="alice")))

    assert result == [
        {"name": "公共 Jellyfin", "type": "jellyfin"},
        {"name": "无字段 Navidrome", "type": "navidrome"},
    ]


def test_media_server_clients_superuser_sees_all(monkeypatch) -> None:
    """
    superuser 不受 allowed_users 限制，可见全部已启用实例。
    """
    monkeypatch.setattr(
        mediaserver_endpoint,
        "get_configured_system_config",
        lambda: _fake_config(
            [
                {"name": "公共 Jellyfin", "type": "jellyfin", "enabled": True, "allowed_users": []},
                {"name": "alice 的 Emby", "type": "emby", "enabled": True, "allowed_users": ["alice"]},
                {"name": "bob 的 Plex", "type": "plex", "enabled": True, "allowed_users": ["bob"]},
            ]
        ),
    )

    result = asyncio.run(
        mediaserver_endpoint.clients(current_user=_user(name="admin", is_superuser=True))
    )

    assert result == [
        {"name": "公共 Jellyfin", "type": "jellyfin"},
        {"name": "alice 的 Emby", "type": "emby"},
        {"name": "bob 的 Plex", "type": "plex"},
    ]
