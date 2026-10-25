"""NSO 业务接口客户端。"""

from __future__ import annotations

from typing import Any

from .auth import NSO_API_BASE, NintendoAuth, NsoApiError
from .models import Friend


class NsoClient:
    def __init__(self, auth: NintendoAuth) -> None:
        self._auth = auth

    async def friends(self) -> list[Friend]:
        """拉取好友列表（含当前游戏与在线状态）。"""

        payload = await self._auth.request_json(
            "POST",
            f"{NSO_API_BASE}/v3/Friend/List",
            json={},
        )
        result = payload.get("result")
        if not isinstance(result, dict):
            raise NsoApiError("好友列表响应缺少 result 字段。")
        raw_friends = result.get("friends")
        if raw_friends is None:
            raw_friends = []
        if not isinstance(raw_friends, list):
            raise NsoApiError("好友列表响应中的 friends 不是数组。")
        friends: list[Friend] = []
        for item in raw_friends:
            friend = Friend.from_api(item)
            if friend is not None:
                friends.append(friend)
        return friends

    async def own_profile(self) -> dict[str, Any]:
        return self._auth.account
