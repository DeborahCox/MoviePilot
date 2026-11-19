"""
连接测试只读性回归：探测下载器不得修改下载器全局偏好设置。

背景：Qbittorrent / Transmission 客户端在登录后会同步全局设置
（incomplete_files_ext / rename-partial-files）。连接测试
（probe_downloader）通过实例化客户端探测，历史实现会连带触发该同步，
使"测试连接"变成有副作用的写操作。

验收标准：
- probe_downloader 以 probe_only=True 构造客户端，登录后不调用
  app_set_preferences / set_session。
- 正常业务实例化（不带 probe_only）仍按配置同步全局设置。
"""

import functools
import sys
import types
from typing import Any

import pytest

import app.modules.qbittorrent.qbittorrent as qb_module
import app.modules.transmission.transmission as tr_module
from app.application.connection import service as connection_service
from app.application.connection.service import _client_supports_probe_only, probe_downloader
from app.modules.qbittorrent.qbittorrent import Qbittorrent
from app.modules.transmission.transmission import Transmission

_PROBE_CONFIG = {
    "host": "https://downloader.invalid",
    "port": 443,
    "username": "user",
    "password": "secret-password",
}


class FakeQbittorrentClient:
    """
    记录偏好写入调用的 qBittorrent SDK 替身；服务端 incomplete_files_ext 为 False。
    """

    set_preferences_calls: list[dict] = []

    def __init__(self, **kwargs: Any) -> None:
        FakeQbittorrentClient.set_preferences_calls.clear()

    def auth_log_in(self) -> None:
        return None

    def app_preferences(self) -> dict:
        return {"incomplete_files_ext": False}

    def app_set_preferences(self, preferences: dict) -> None:
        FakeQbittorrentClient.set_preferences_calls.append(preferences)

    def transfer_info(self) -> dict:
        return {"dht_nodes": 100}


class FakeTransmissionSession:
    """服务端 rename-partial-files 为 False 的会话替身。"""

    def get(self, key: str) -> Any:
        return {"rename-partial-files": False}.get(key)


class FakeTransmissionClient:
    """记录会话写入调用的 transmission-rpc 替身。"""

    set_session_calls: list[dict] = []

    def __init__(self, **kwargs: Any) -> None:
        FakeTransmissionClient.set_session_calls.clear()

    def get_session(self) -> FakeTransmissionSession:
        return FakeTransmissionSession()

    def set_session(self, **kwargs: Any) -> None:
        FakeTransmissionClient.set_session_calls.append(kwargs)

    def session_stats(self) -> dict:
        return {"downloaded-ever": 1}


@pytest.fixture()
def fake_qb_client(monkeypatch: pytest.MonkeyPatch) -> type[FakeQbittorrentClient]:
    monkeypatch.setattr(qb_module.qbittorrentapi, "Client", FakeQbittorrentClient)
    return FakeQbittorrentClient


@pytest.fixture()
def fake_tr_client(monkeypatch: pytest.MonkeyPatch) -> type[FakeTransmissionClient]:
    monkeypatch.setattr(tr_module.transmission_rpc, "Client", FakeTransmissionClient)
    return FakeTransmissionClient


def test_qbittorrent_probe_never_touches_preferences(fake_qb_client):
    """
    连接测试不得修改 qBittorrent 全局偏好：默认开启的 incomplete_files_ext
    也不得在探测时写入服务端（服务端当前为 False，写入即改变全局设置）。
    """
    outcome = probe_downloader("qbittorrent", dict(_PROBE_CONFIG))
    assert outcome.ok
    assert FakeQbittorrentClient.set_preferences_calls == []


def test_probe_downloader_passes_probe_only(monkeypatch: pytest.MonkeyPatch, fake_qb_client):
    """
    probe_downloader 以只读探测模式构造客户端，而非复用业务实例化路径。
    """
    recorded: dict[str, Any] = {}

    original_init = qb_module.Qbittorrent.__init__

    @functools.wraps(original_init)
    def spy_init(self: Any, *args: Any, **kwargs: Any) -> None:
        recorded.update(kwargs)
        return original_init(self, *args, **kwargs)

    monkeypatch.setattr(qb_module.Qbittorrent, "__init__", spy_init)
    outcome = probe_downloader("qbittorrent", dict(_PROBE_CONFIG))
    assert outcome.ok
    assert recorded.get("probe_only") is True


def test_qbittorrent_probe_skips_sync_even_with_explicit_false(fake_qb_client):
    """
    探测模式下即使配置显式传入 incomplete_files_ext=False 也不写服务端。
    """
    config = {**_PROBE_CONFIG, "incomplete_files_ext": False}
    outcome = probe_downloader("qbittorrent", config)
    assert outcome.ok
    assert FakeQbittorrentClient.set_preferences_calls == []


def test_qbittorrent_normal_init_still_syncs_preference(fake_qb_client):
    """
    正常业务实例化（无 probe_only）仍按默认配置同步全局设置——同步能力不回退。
    """
    client = Qbittorrent(**_PROBE_CONFIG)
    assert client.qbc is not None
    assert FakeQbittorrentClient.set_preferences_calls == [{"incomplete_files_ext": True}]


def test_transmission_probe_never_touches_session(fake_tr_client):
    """
    连接测试不得修改 Transmission 全局会话设置（rename-partial-files）。
    """
    outcome = probe_downloader("transmission", dict(_PROBE_CONFIG))
    assert outcome.ok
    assert FakeTransmissionClient.set_session_calls == []


def test_transmission_normal_init_still_syncs_session(fake_tr_client):
    """
    正常业务实例化仍按默认配置同步 rename-partial-files。
    """
    client = Transmission(**_PROBE_CONFIG)
    assert client.trc is not None
    assert FakeTransmissionClient.set_session_calls == [{"rename_partial_files": True}]


def test_probe_only_not_injected_into_unsupported_clients(monkeypatch: pytest.MonkeyPatch):
    """
    构造函数未显式声明 probe_only 的客户端（如 rtorrent）不得被注入该参数，
    避免依赖 **kwargs 吞参数造成"已只读"的假象。
    """

    class FakeRtorrentClient:
        def __init__(self, **kwargs: Any) -> None:
            assert "probe_only" not in kwargs, "probe_only 不应注入未声明的客户端"

        def transfer_info(self) -> dict:
            return {"ok": 1}

    fake_module = types.ModuleType("fake_rtorrent_module")
    fake_module.FakeRtorrentClient = FakeRtorrentClient
    monkeypatch.setitem(sys.modules, "fake_rtorrent_module", fake_module)
    monkeypatch.setitem(
        connection_service._DOWNLOADER_CLIENTS,
        "rtorrent",
        ("fake_rtorrent_module", "FakeRtorrentClient"),
    )
    outcome = probe_downloader("rtorrent", dict(_PROBE_CONFIG))
    assert outcome.ok


def test_supports_probe_only_requires_explicit_declaration():
    """
    仅靠 **kwargs 兜底的构造函数不视为支持 probe_only。
    """

    class KwargOnlyClient:
        def __init__(self, **kwargs: Any) -> None:
            pass

    assert _client_supports_probe_only(Qbittorrent) is True
    assert _client_supports_probe_only(Transmission) is True
    assert _client_supports_probe_only(KwargOnlyClient) is False
