"""
媒体详情增强数据的应用服务：跨数据源只读查询的聚合入口。

endpoint 等入口层不得静态依赖具体模块实现（app.modules.*），
此处以动态导入隔离模块边界，与 connection 服务保持同一先例。
"""

import asyncio
from importlib import import_module
from typing import Any, Optional

from app.runtime.settings import get_runtime_setting

# IMDb 评分查询默认超时秒数：详情页辅助数据，超时即放弃，不影响主响应。
_IMDB_RATING_TIMEOUT_SECONDS = 2.0

# IMDb API 模块与符号的动态导入契约。
_IMDB_API_CONTRACT = ("app.modules.imdb.api", "ImdbApi")


def _load_imdb_api() -> Any:
    """
    按契约动态加载 IMDb API 类，避免入口层静态依赖模块实现。
    """
    module_name, symbol_name = _IMDB_API_CONTRACT
    return getattr(import_module(module_name), symbol_name)


async def async_query_imdb_rating(
    imdb_id: str,
    timeout_seconds: float = _IMDB_RATING_TIMEOUT_SECONDS,
) -> Optional[float]:
    """
    异步查询 IMDb 聚合评分（10 分制）。

    查询失败、超时或评分超出有效范围时返回 None，不抛出异常，
    不影响媒体详情主响应。

    :param imdb_id: IMDb 标识（tt 开头）
    :param timeout_seconds: 查询超时秒数
    :return: 0 < 评分 <= 10 时返回浮点值，否则返回 None
    """
    api: Any = None
    try:
        imdb_api_cls = _load_imdb_api()
        api = imdb_api_cls(proxies=get_runtime_setting('PROXY'))
        title = await asyncio.wait_for(api.async_get_title(imdb_id), timeout=timeout_seconds)
        rating = getattr(getattr(title, "rating", None), "aggregate_rating", None)
        if isinstance(rating, (int, float)) and not isinstance(rating, bool):
            value = float(rating)
            if 0 < value <= 10:
                return value
    except Exception:
        return None
    finally:
        if api is not None:
            try:
                api.close()
            except Exception:
                pass
    return None
