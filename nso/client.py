"""NSO 业务接口客户端（Coral v4 + 请求加密）。"""

from __future__ import annotations

from typing import Any

from .auth import NintendoAuth, NsoApiError
from .models import Friend


class NsoClient:
    def __init__(self, auth: NintendoAuth) -> None:
        self._auth = auth

    async def friends(self) -> list[Friend]:
        """拉取好友列表（/v4/Friend/List，请求与响应均加密）。"""

        payload = await self._auth.coral_request("/v4/Friend/List", {})
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

    async def lookup_friend_code(self, friend_code: str) -> dict[str, Any]:
        """按好友码查找用户（不做加好友操作）。"""

        payload = await self._auth.coral_request(
            "/v3/Friend/GetUserByFriendCode",
            {"friendCode": friend_code},
        )
        result = payload.get("result")
        if not isinstance(result, dict):
            raise NsoApiError("好友码查询响应缺少 result 字段。")
        return result

    async def send_friend_request(self, nsa_id: str) -> dict[str, Any]:
        """发送好友申请（对方仍需接受）。"""

        payload = await self._auth.coral_request(
            "/v4/FriendRequest/Create",
            {"nsaId": nsa_id, "channel": "FRIEND_CODE"},
        )
        return payload

    async def received_friend_requests(self) -> list[dict[str, Any]]:
        payload = await self._auth.coral_request("/v4/FriendRequest/Received/List", {})
        result = payload.get("result") or {}
        requests = result.get("friendRequests")
        return [item for item in requests if isinstance(item, dict)] if isinstance(requests, list) else []

    async def sent_friend_requests(self) -> list[dict[str, Any]]:
        payload = await self._auth.coral_request("/v3/FriendRequest/Sent/List", {})
        result = payload.get("result") or {}
        requests = result.get("friendRequests")
        return [item for item in requests if isinstance(item, dict)] if isinstance(requests, list) else []

    async def accept_friend_request(self, request_id: str) -> None:
        await self._auth.coral_request("/v3/FriendRequest/Accept", {"id": request_id})

    async def reject_friend_request(self, request_id: str) -> None:
        await self._auth.coral_request("/v3/FriendRequest/Reject", {"id": request_id})

    async def cancel_friend_request(self, request_id: str) -> None:
        await self._auth.coral_request("/v3/FriendRequest/Cancel", {"id": request_id})

    async def own_profile(self) -> dict[str, Any]:
        return self._auth.account
