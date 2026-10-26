"""测试用的假 nxapi + Coral v4 服务。

模拟：
- Nintendo Account token / users/me
- nxapi-auth client_credentials
- nxapi /f（含 encrypt_token_request）
- /encrypt-request、/decrypt-response
- /v4/Account/Login、/v4/Friend/List（octet-stream）
"""

from __future__ import annotations

import base64
import json
from collections import defaultdict
from typing import Any

from nso.auth import (
    DEFAULT_F_API_URL,
    DEFAULT_F_TOKEN_URL,
    NSO_API_BASE,
    SESSION_TOKEN_URL,
    TOKEN_URL,
    USERS_ME_URL,
)
from nso.http import Response

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
LOGIN_URL = f"{NSO_API_BASE}/v4/Account/Login"
FRIEND_LIST_URL = f"{NSO_API_BASE}/v4/Friend/List"
ENCRYPT_URL = DEFAULT_F_API_URL[:-1] + "encrypt-request"
DECRYPT_URL = DEFAULT_F_API_URL[:-1] + "decrypt-response"


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def encrypted_blob(payload: dict[str, Any]) -> bytes:
    return b"ENC:" + json.dumps(payload, separators=(",", ":")).encode("utf-8")


def friend_payload(
    name: str = "Alice",
    *,
    friend_id: int = 1001,
    state: str = "ONLINE",
    game: str | None = None,
    total_play_time: int = 0,
    image_url: str = "",
    updated_at: int = 1_700_000_000,
    logout_at: int = 1_700_000_000,
) -> dict[str, Any]:
    presence_game: dict[str, Any] = {}
    if game:
        presence_game = {
            "name": game,
            "imageUri": image_url or "https://atum-img-lp1.cdn.nintendo.net/i/c/cover_512",
            "shopUri": "https://ec.nintendo.com/apps/0100000000000000/JP?lang=zh",
            "totalPlayTime": total_play_time,
            "firstPlayedAt": 1_600_000_000,
            "sysDescription": "",
        }
    return {
        "id": friend_id,
        "name": name,
        "nsaId": f"nsa-{friend_id}",
        "imageUri": "https://cdn-image-e0d67c509fb203858ebcb2fe3f88c2aa.baas.nintendo.com/1/avatar",
        "isFavoriteFriend": False,
        "isFriend": True,
        "presence": {
            "game": presence_game,
            "state": state,
            "logoutAt": logout_at,
            "updatedAt": updated_at,
        },
    }


class FakeTransport:
    def __init__(self, *, friends: list[dict[str, Any]] | None = None) -> None:
        self.friends = friends or []
        self.requests: list[tuple[str, str, dict[str, Any]]] = []
        self.calls: dict[str, int] = defaultdict(int)
        self.login_count = 0
        self.oauth_count = 0
        self.encrypt_count = 0
        self.decrypt_count = 0
        self.fail_session_token = False
        self.expire_first_friend_call = False
        self._credential_seq = 0
        self.login_parameters: list[dict[str, Any]] = []
        self.oauth_requests: list[dict[str, Any]] = []

    async def request(self, method: str, url: str, **kwargs: Any) -> Response:
        self.requests.append((method, url, kwargs))
        self.calls[url] += 1

        if url == SESSION_TOKEN_URL:
            if self.fail_session_token:
                return Response(400, b'{"error": "invalid_grant"}')
            return _json({"session_token": "session-token-1", "code": "code"})

        if url == TOKEN_URL:
            return _json({"access_token": "access-token", "id_token": "id-token", "expires_in": 900})

        if url == USERS_ME_URL:
            return _json(
                {
                    "id": "na-id",
                    "country": "JP",
                    "birthday": "2000-01-01",
                    "language": "zh-CN",
                    "nickname": "Me",
                }
            )

        if url == DEFAULT_F_TOKEN_URL:
            self.oauth_count += 1
            self.oauth_requests.append(dict(kwargs.get("data") or {}))
            return _json({"access_token": "f-access", "expires_in": 3600, "token_type": "Bearer"})

        if url == DEFAULT_F_API_URL:
            body = kwargs.get("json") or {}
            if "encrypt_token_request" in body:
                self.login_parameters.append(body["encrypt_token_request"]["parameter"])
                blob = encrypted_blob({"url": LOGIN_URL, "login": True})
                return _json(
                    {
                        "f": "f-login",
                        "request_id": "req-login",
                        "timestamp": 1_700_000_000_000,
                        "encrypted_token_request": base64.b64encode(blob).decode("ascii"),
                    }
                )
            return _json({"f": "f-value", "request_id": "req-1", "timestamp": 1_700_000_000_000})

        if url == ENCRYPT_URL:
            self.encrypt_count += 1
            body = kwargs.get("json") or {}
            blob = encrypted_blob({"url": body.get("url"), "data": body.get("data")})
            return _json({"data": b64url(blob)})

        if url == DECRYPT_URL:
            self.decrypt_count += 1
            body = kwargs.get("json") or {}
            raw = base64.b64decode(body.get("data") or "")
            payload = json.loads(raw[len(b"ENC:"):])
            target = payload.get("url")
            if target == LOGIN_URL:
                self._credential_seq += 1
                result = {
                    "status": 0,
                    "result": {
                        "user": {
                            "id": 123,
                            "name": "Me",
                            "nsaId": "me-nsa",
                            "imageUri": "",
                            "links": {"friendCode": {"id": "1234-5678-9012"}},
                        },
                        "naUser": {
                            "id": "na-id",
                            "country": "JP",
                            "birthday": "2000-01-01",
                            "language": "zh-CN",
                            "nickname": "Me",
                        },
                        "webApiServerCredential": {
                            "accessToken": f"cred-{self._credential_seq}",
                            "expiresIn": 7200,
                        },
                    },
                }
                self.login_count += 1
            elif target == FRIEND_LIST_URL:
                result = {"status": 0, "result": {"friends": self.friends, "extractFriendsIds": []}}
            else:
                result = {"status": 0, "result": {}}
            return Response(200, json.dumps(result).encode("utf-8"), {"content-type": "text/plain"})

        if url == LOGIN_URL:
            # 加密后的 Account/Login 请求体原样返回，由 decrypt-response 解出登录结果
            return Response(200, kwargs.get("content") or b"", {"content-type": "application/octet-stream"})

        if url == FRIEND_LIST_URL:
            headers = kwargs.get("headers") or {}
            authorization = str(headers.get("Authorization") or "")
            if self.expire_first_friend_call and authorization == "Bearer cred-1":
                return Response(401, b"{}")
            return Response(200, kwargs.get("content") or b"", {"content-type": "application/octet-stream"})

        if url.startswith("https://") and url.endswith(("avatar", "cover_512")):
            return Response(200, PNG_BYTES, {"content-type": "image/png"})

        return Response(404, b"not found")

    async def aclose(self) -> None:
        return None

    # ------------------------------------------------------------ 断言助手
    def calls_for(self, url: str) -> list[tuple[str, str, dict[str, Any]]]:
        return [item for item in self.requests if item[1] == url]

    def last(self, url: str) -> dict[str, Any]:
        calls = self.calls_for(url)
        assert calls, f"没有请求 {url}"
        return calls[-1][2]


def _json(payload: dict[str, Any], status: int = 200) -> Response:
    return Response(status, json.dumps(payload).encode("utf-8"), {"content-type": "application/json"})


def api_url(path: str) -> str:
    return f"{NSO_API_BASE}{path}"
