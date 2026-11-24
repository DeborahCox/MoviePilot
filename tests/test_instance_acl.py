"""
实例 allowed_users ACL 辅助函数测试，覆盖 dict 与 Pydantic 两种配置形态。
"""

from app.application.security.acl import filter_allowed_configs, is_instance_allowed
from app.schemas.system import DownloaderConf, MediaServerConf


def _dict_configs():
    return [
        {"name": "公共", "allowed_users": []},
        {"name": "alice 的", "allowed_users": ["alice"]},
        {"name": "bob 的", "allowed_users": ["bob"]},
        {"name": "旧配置无字段"},
    ]


def _model_configs():
    return [
        MediaServerConf(name="公共", allowed_users=[]),
        MediaServerConf(name="alice 的", allowed_users=["alice"]),
        MediaServerConf(name="bob 的", allowed_users=["bob"]),
        DownloaderConf(name="旧配置无字段"),
    ]


def test_filter_allowed_configs_dict_and_model_agree():
    """
    dict 与 Pydantic 配置必须得到完全一致的可见性结果，不能因取值方式不同而静默失效。
    """
    for configs, name_of in ((_dict_configs(), lambda c: c["name"]), (_model_configs(), lambda c: c.name)):
        assert [name_of(c) for c in filter_allowed_configs(configs, username="alice", is_superuser=False)] == [
            "公共", "alice 的", "旧配置无字段",
        ]
        assert [name_of(c) for c in filter_allowed_configs(configs, username="bob", is_superuser=False)] == [
            "公共", "bob 的", "旧配置无字段",
        ]
        assert [name_of(c) for c in filter_allowed_configs(configs, username="admin", is_superuser=True)] == [
            "公共", "alice 的", "bob 的", "旧配置无字段",
        ]


def test_is_instance_allowed_missing_username_is_denied_for_restricted_instance():
    """
    令牌载荷缺少用户名时，受限实例一律拒绝；公共实例仍可用。
    """
    assert is_instance_allowed({"allowed_users": ["alice"]}, username=None, is_superuser=False) is False
    assert is_instance_allowed({"allowed_users": []}, username=None, is_superuser=False) is True
    assert is_instance_allowed({"allowed_users": ["alice"]}, username=None, is_superuser=True) is True


def test_is_instance_allowed_malformed_value_is_denied():
    """
    名单字段类型异常时拒绝访问，避免 ACL 因脏配置静默失效。
    """
    assert is_instance_allowed({"allowed_users": "alice,bob"}, username="alice", is_superuser=False) is False
    assert is_instance_allowed({"allowed_users": 1}, username="alice", is_superuser=False) is False
    assert is_instance_allowed({"allowed_users": "alice,bob"}, username="alice", is_superuser=True) is True
