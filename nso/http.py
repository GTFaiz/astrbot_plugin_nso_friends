"""极薄的 HTTP 抽象层。

AstrBot 自带 httpx，但为了让鉴权/监控逻辑可以脱离框架单测，
这里把请求收敛到一个很小的 Transport 协议上，测试时注入假实现即可。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, runtime_checkable


@dataclass(slots=True)
class Response:
    status_code: int
    content: bytes = b""
    headers: Mapping[str, str] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> Any:
        try:
            return json.loads(self.text)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"响应不是合法 JSON（HTTP {self.status_code}）：{self.text[:200]}") from exc


@runtime_checkable
class Transport(Protocol):
    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
        json: Any = None,
        content: bytes | None = None,
    ) -> Response: ...

    async def aclose(self) -> None: ...


class HttpxTransport:
    """默认使用 httpx.AsyncClient，延迟导入避免测试环境强依赖。"""

    def __init__(self, timeout: float = 30.0) -> None:
        self._timeout = timeout
        self._client: Any = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            import httpx  # noqa: PLC0415 - 延迟导入

            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout, connect=min(10.0, self._timeout)),
                follow_redirects=True,
            )
        return self._client

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
        json: Any = None,
        content: bytes | None = None,
    ) -> Response:
        client = self._ensure_client()
        response = await client.request(
            method,
            url,
            headers=dict(headers or {}),
            params=params,
            data=data,
            json=json,
            content=content,
        )
        return Response(
            status_code=response.status_code,
            content=response.content,
            headers={key.lower(): value for key, value in response.headers.items()},
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
