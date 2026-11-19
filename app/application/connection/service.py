from __future__ import annotations

import inspect
import time
import traceback
from dataclasses import dataclass
from typing import Any, Optional

from app.runtime.log import logger
from app.schemas.connection import ConnectionErrorDetail, ConnectionTestData

_MAX_STACK_CHARS = 2000

_SECRET_KEYS = frozenset({"apikey", "api_key", "token", "password", "cookie", "secret", "authorization"})

_DOWNLOADER_CLIENTS: dict[str, tuple[str, str]] = {
    "qbittorrent": ("app.modules.qbittorrent.qbittorrent", "Qbittorrent"),
    "transmission": ("app.modules.transmission.transmission", "Transmission"),
    "rtorrent": ("app.modules.rtorrent.rtorrent", "Rtorrent"),
}

_MEDIASERVER_CLIENTS: dict[str, tuple[str, str]] = {
    "emby": ("app.modules.emby.emby", "Emby"),
    "jellyfin": ("app.modules.jellyfin.jellyfin", "Jellyfin"),
    "plex": ("app.modules.plex.plex", "Plex"),
    "navidrome": ("app.modules.navidrome.navidrome", "Navidrome"),
}

_ROUTE_META_KEYS = frozenset({"type", "name"})


@dataclass
class ProbeOutcome:
    ok: bool
    message: str = ""
    error_type: str = ""
    stack: str = ""


def _import_symbol(module_path: str, symbol: str) -> Any:
    import importlib

    module = importlib.import_module(module_path)
    return getattr(module, symbol)


def _sanitize_config(config: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in config.items()
        if value is not None
        and value != ""
        and key not in _ROUTE_META_KEYS
    }


def _client_supports_probe_only(client_cls: Any) -> bool:
    """
    判断客户端构造函数是否显式声明了 probe_only 只读探测参数。

    仅识别显式声明的形参：依赖 **kwargs 吞掉 probe_only 会被视为不支持，
    避免注入一个静默失效的参数。
    """
    try:
        parameters = inspect.signature(client_cls.__init__).parameters
    except (TypeError, ValueError):
        return False
    probe = parameters.get("probe_only")
    return probe is not None and probe.kind in (
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.KEYWORD_ONLY,
    )


def _redact(text: str, config: dict[str, Any]) -> str:
    if not text:
        return text
    result = text
    for key, value in config.items():
        if key.lower() not in _SECRET_KEYS or not isinstance(value, str) or len(value) < 4:
            continue
        result = result.replace(value, "***")
    return result


def _detail_from_exception(exc: BaseException, config: dict[str, Any] | None = None) -> ConnectionErrorDetail:
    stack = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    if len(stack) > _MAX_STACK_CHARS:
        stack = stack[:_MAX_STACK_CHARS] + "\n…（已截断）"
    message = str(exc).strip()
    if config:
        message = _redact(message, config)
        stack = _redact(stack, config)
    return ConnectionErrorDetail(
        error_type=type(exc).__name__,
        message=message,
        stack=stack,
    )


def probe_downloader(dtype: str, config: dict[str, Any]) -> ProbeOutcome:
    target = _DOWNLOADER_CLIENTS.get(dtype)
    if not target:
        return ProbeOutcome(ok=False, message=f"暂不支持的下载器类型：{dtype}", error_type="UnsupportedType")
    client_cls = _import_symbol(*target)
    probe_config = _sanitize_config(config)
    if _client_supports_probe_only(client_cls):
        # 连接测试必须是只读操作：探测模式下客户端跳过下载器全局设置同步。
        probe_config["probe_only"] = True
    client = client_cls(**probe_config)
    info = client.transfer_info()
    if not info:
        return ProbeOutcome(ok=False, message="连接成功但无法读取传输状态，请检查地址与凭据", error_type="ProbeFailed")
    return ProbeOutcome(ok=True, message="连接成功")


def probe_mediaserver(mtype: str, config: dict[str, Any]) -> ProbeOutcome:
    target = _MEDIASERVER_CLIENTS.get(mtype)
    if not target:
        return ProbeOutcome(ok=False, message=f"暂不支持的媒体服务器类型：{mtype}", error_type="UnsupportedType")
    client_cls = _import_symbol(*target)
    client = client_cls(**_sanitize_config(config))
    probe = getattr(client, "get_user", None) or getattr(client, "get_librarys", None)
    if probe is None:
        return ProbeOutcome(ok=False, message="客户端不支持连接探测", error_type="UnsupportedProbe")
    result = probe()
    if not result:
        return ProbeOutcome(ok=False, message="连接成功但无法读取用户信息，请检查地址与凭据", error_type="ProbeFailed")
    return ProbeOutcome(ok=True, message="连接成功")


def probe_site(config: dict[str, Any]) -> ProbeOutcome:
    from dataclasses import fields as dataclass_fields

    from app.application.site.contract import SiteSnapshot
    from app.chain.site import SiteChain
    from app.foundation.url import UrlUtils

    url = str(config.get("url") or "").strip()
    if not url:
        return ProbeOutcome(ok=False, message="站点地址未填写", error_type="MissingUrl")

    allowed = {field.name for field in dataclass_fields(SiteSnapshot)}
    overrides = {key: value for key, value in config.items() if key in allowed and key not in {"id", "name", "url"}}
    overrides.setdefault("id", 0)
    overrides.setdefault("name", str(config.get("name") or ""))
    overrides["url"] = UrlUtils.standardize_base_url(url) or url
    snapshot = SiteSnapshot(**overrides)

    ok, message = SiteChain().test_snapshot(snapshot)
    return ProbeOutcome(ok=bool(ok), message=message or ("连接成功" if ok else "连接失败"))


_PROBERS = {
    "downloader": lambda category_config: probe_downloader(
        str(category_config.get("type", "")).strip().lower(), category_config
    ),
    "mediaserver": lambda category_config: probe_mediaserver(
        str(category_config.get("type", "")).strip().lower(), category_config
    ),
    "site": probe_site,
}


def test_connection(category: str, config: dict[str, Any]) -> ConnectionTestData:
    started = time.perf_counter()
    prober = _PROBERS.get(category)
    if prober is None:
        return ConnectionTestData(
            connected=False,
            error=ConnectionErrorDetail(
                error_type="UnsupportedCategory",
                message=f"暂不支持的连接配置分类：{category}",
            ),
        )

    try:
        outcome = prober(config)
    except Exception as exc:
        logger.warning(f"连接测试失败（{category}）：{_redact(str(exc), config)}")
        return ConnectionTestData(
            connected=False,
            error=_detail_from_exception(exc, config),
        )

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    if outcome.ok:
        return ConnectionTestData(connected=True, elapsed_ms=elapsed_ms)

    return ConnectionTestData(
        connected=False,
        elapsed_ms=elapsed_ms,
        error=ConnectionErrorDetail(
            error_type=outcome.error_type,
            message=_redact(outcome.message, config),
            stack=_redact(outcome.stack, config),
        ),
    )
