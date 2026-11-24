"""
服务实例的按用户可见性判断。

下载器与媒体服务器配置既有 dict（系统配置）和 Pydantic 模型（应用层）
两种形态，本模块统一读取 ``allowed_users``，避免任一调用点因取值方式
不同而静默跳过 ACL 判断。
"""

from typing import Any, Iterable, TypeVar

_T = TypeVar("_T")


def _config_value(config: Any, key: str, default: Any = None) -> Any:
    """
    同时兼容 dict 配置与 Pydantic/普通对象配置的字段读取。
    """
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


def is_instance_allowed(config: Any, *, username: Any, is_superuser: bool) -> bool:
    """
    判断当前用户是否允许访问某个已命名实例。

    语义与 ``/download/clients``、``/mediaserver/clients`` 保持一致：
    ``allowed_users`` 为空（含旧配置缺字段）表示所有用户可用，superuser
    不受限制，其余用户必须显式出现在名单中。名单字段存在但类型异常时
    拒绝访问，避免 ACL 因脏配置而静默失效。

    :param config: dict 或 Pydantic 配置对象
    :param username: 当前用户名
    :param is_superuser: 当前用户是否为超级用户
    :return: 允许访问时返回 True
    """
    if is_superuser:
        return True
    allowed = _config_value(config, "allowed_users", None)
    if allowed is None:
        return True
    if not isinstance(allowed, (list, tuple, set)):
        return False
    if not allowed:
        return True
    return username is not None and username in allowed


def filter_allowed_configs(
    configs: Iterable[_T], *, username: Any, is_superuser: bool
) -> list[_T]:
    """
    按当前用户过滤实例配置，仅保留其可见的实例。
    """
    return [
        config
        for config in configs
        if is_instance_allowed(config, username=username, is_superuser=is_superuser)
    ]
