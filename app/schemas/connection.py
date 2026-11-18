from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

ConnectionCategory = Literal["downloader", "mediaserver", "site"]


class ConnectionTestRequest(BaseModel):
    category: ConnectionCategory = Field(description="配置分类：downloader/mediaserver/site")
    type: str = Field(default="", description="实例类型，如 qbittorrent/emby；站点测试可留空")
    name: str = Field(default="", description="实例名称，仅用于错误提示")
    config: dict[str, Any] = Field(default_factory=dict, description="表单当前配置值")


class ConnectionErrorDetail(BaseModel):
    error_type: str = Field(default="", description="异常类名或错误类别")
    message: str = Field(default="", description="错误摘要")
    stack: str = Field(default="", description="截断后的堆栈片段")


class ConnectionTestData(BaseModel):
    connected: bool = Field(description="目标是否连通")
    elapsed_ms: Optional[int] = Field(default=None, description="握手耗时（毫秒）")
    error: Optional[ConnectionErrorDetail] = Field(default=None, description="结构化错误详情")
