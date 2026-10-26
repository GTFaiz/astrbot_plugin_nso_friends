"""Nintendo Switch Online 鉴权（Coral 3.5.0 / nxapi 加密协议）。

Nintendo 在 NSO 3.x 中把登录接口换成了 ``/v4/Account/Login``，并且要求
所有 Coral 请求体/响应都经过 App 端加密。社区维护的实现是 nxapi：

    https://gitlab.fancy.org.uk/samuel/nxapi
    https://github.com/samuelthomas2774/nxapi-znca-api

本模块按 nxapi 当前实现对接：

1. Nintendo Account：session_token -> access_token/id_token -> /users/me
2. f 参数：nxapi-znca-api 的 ``/f``，同时请求 ``encrypt_token_request``，
   直接得到加密后的 ``/v4/Account/Login`` 请求体
3. Coral 登录：``POST /v4/Account/Login``（octet-stream）
4. 业务请求：``/encrypt-request`` 加密请求，``/decrypt-response`` 解密响应
5. 凭证过期：重新走一遍登录（id_token 仍在有效期内）
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs, quote, urlencode

from .http import Response, Transport

CLIENT_ID = "71b963c1b7b6d119"
AUTHORIZE_URL = "https://accounts.nintendo.com/connect/1.0.0/authorize"
SESSION_TOKEN_URL = "https://accounts.nintendo.com/connect/1.0.0/api/session_token"
TOKEN_URL = "https://accounts.nintendo.com/connect/1.0.0/api/token"
USERS_ME_URL = "https://api.accounts.nintendo.com/2.0.0/users/me"
NSO_API_BASE = "https://api-lp1.znc.srv.nintendo.net"

DEFAULT_APP_VERSION = "3.5.0"
DEFAULT_F_API_URL = "https://nxapi-znca-api.fancy.org.uk/api/znca/f"
DEFAULT_F_TOKEN_URL = "https://nxapi-auth.fancy.org.uk/api/oauth/token"
DEFAULT_F_SCOPE = "ca:gf ca:er ca:dr"
# 取自 nxapi 源码 src/api/coral.ts 的 ZNCA_API_COMPATIBILITY_VERSION。
# 它是客户端兼容性标记（不是密钥）；nxapi-auth 若为你的客户端签发单独的值，请在配置里覆盖。
DEFAULT_F_CLIENT_VERSION = "d8fAZDPzwimzQ7c6"

SCOPE = "openid user user.birthday user.mii user.screenName"
REDIRECT_URI = f"npf{CLIENT_ID}://auth"


class NsoError(Exception):
    """插件内所有 Nintendo 相关错误的基类。"""


class NsoNetworkError(NsoError):
    """网络层失败（超时、DNS、连接重置等）。"""


class NsoAuthError(NsoError):
    """登录流程失败（链接无效、state 不匹配、账号接口异常等）。"""


class NsoAuthRequired(NsoAuthError):
    """没有可用的 session_token，或 session_token 已失效，需要重新登录。"""


class NsoApiError(NsoError):
    """Nintendo / f 服务返回了非预期结果。"""

    def __init__(self, message: str, *, status: int | None = None, http_status: int | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.http_status = http_status


@dataclass(slots=True)
class LoginChallenge:
    url: str
    state: str
    verifier: str
    created_at: float


def make_verifier() -> str:
    return secrets.token_urlsafe(32)


def code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def create_login_challenge() -> LoginChallenge:
    verifier = make_verifier()
    state = secrets.token_urlsafe(32)
    params = {
        "state": state,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "scope": SCOPE,
        "response_type": "session_token_code",
        "session_token_code_challenge": code_challenge(verifier),
        "session_token_code_challenge_method": "S256",
        "theme": "login_form",
    }
    return LoginChallenge(
        url=f"{AUTHORIZE_URL}?{urlencode(params, quote_via=quote)}",
        state=state,
        verifier=verifier,
        created_at=time.time(),
    )


def parse_redirect_link(link: str) -> tuple[str, str]:
    """从用户粘贴的回调链接里取出 (session_token_code, state)。"""

    raw = (link or "").strip()
    if not raw:
        raise NsoAuthError("回调链接为空，请重新复制「选择此人」按钮的链接地址。")
    if "#" in raw:
        raw = raw.split("#", 1)[1]
    elif "?" in raw:
        raw = raw.split("?", 1)[1]
    params = parse_qs(raw, keep_blank_values=True)
    code = (params.get("session_token_code") or [""])[0].strip()
    state = (params.get("state") or [""])[0].strip()
    if not code and raw and "=" not in raw:
        code = raw
    if not code:
        raise NsoAuthError(
            "链接里没有找到 session_token_code。请确认复制的是 Nintendo 登录页"
            "「选择此人」按钮的链接地址（以 npf...://auth# 开头）。"
        )
    return code, state


def _b64url_decode(data: str) -> bytes:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


class NintendoAuth:
    """负责 Nintendo Account 登录、f 生成与 Coral 请求加密。"""

    def __init__(
        self,
        transport: Transport,
        *,
        app_version: str = DEFAULT_APP_VERSION,
        f_api_url: str = DEFAULT_F_API_URL,
        f_api_headers: dict[str, str] | None = None,
        f_api_client_id: str = "",
        f_api_client_secret: str = "",
        f_api_scope: str = DEFAULT_F_SCOPE,
        f_api_token_url: str = DEFAULT_F_TOKEN_URL,
        f_api_client_version: str = DEFAULT_F_CLIENT_VERSION,
        session_token: str = "",
        on_session_token: Callable[[str], Awaitable[None]] | None = None,
        on_account: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self._transport = transport
        self.app_version = (app_version or DEFAULT_APP_VERSION).strip()
        self.f_api_url = (f_api_url or DEFAULT_F_API_URL).strip()
        self._f_api_base = self._derive_f_api_base(self.f_api_url)
        self._f_api_headers = {
            str(key).strip(): str(value).strip()
            for key, value in (f_api_headers or {}).items()
            if str(key).strip() and str(value).strip()
        }
        self._f_client_id = (f_api_client_id or "").strip()
        self._f_client_secret = (f_api_client_secret or "").strip()
        self._f_scope = (f_api_scope or DEFAULT_F_SCOPE).strip()
        self._f_token_url = (f_api_token_url or DEFAULT_F_TOKEN_URL).strip()
        self._f_client_version = (f_api_client_version or DEFAULT_F_CLIENT_VERSION).strip()
        self._f_token = ""
        self._f_token_expires_at = 0.0
        self._f_lock = asyncio.Lock()

        self._session_token = (session_token or "").strip()
        self._on_session_token = on_session_token
        self._on_account = on_account
        self._credential = ""
        self._credential_expires_at = 0.0
        self._coral_user_id = 0
        self._account: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self.last_login_at: float = 0.0

    # ---------------------------------------------------------------- 状态
    @property
    def session_token(self) -> str:
        return self._session_token

    @property
    def account(self) -> dict[str, Any]:
        return dict(self._account)

    @property
    def credential_expires_at(self) -> float:
        return self._credential_expires_at

    @property
    def credential_ready(self) -> bool:
        return bool(self._credential) and time.time() < self._credential_expires_at - 60

    @property
    def user_agent(self) -> str:
        return f"com.nintendo.znca/{self.app_version}(Android/8.0.0)"

    def set_session_token(self, token: str) -> None:
        self._session_token = (token or "").strip()
        self._credential = ""
        self._credential_expires_at = 0.0

    def invalidate_credential(self) -> None:
        self._credential = ""
        self._credential_expires_at = 0.0

    # ------------------------------------------------------------ 登录入口
    async def login_with_redirect(self, link: str, challenge: LoginChallenge) -> dict[str, Any]:
        code, state = parse_redirect_link(link)
        if state and state != challenge.state:
            raise NsoAuthError("回调链接的 state 与本次登录不匹配，请重新执行 /nso 登录 生成新链接。")
        session_token = await self._exchange_session_token(code, challenge.verifier)
        self.set_session_token(session_token)
        if self._on_session_token is not None:
            await self._on_session_token(session_token)
        await self._login()
        return self.account

    async def ensure_credential(self) -> str:
        async with self._lock:
            if self._credential and time.time() < self._credential_expires_at - 60:
                return self._credential
            if not self._session_token:
                raise NsoAuthRequired("尚未登录 Nintendo 账号，请先执行 /nso 登录。")
            await self._login()
            if not self._credential:
                raise NsoAuthRequired("Nintendo 登录未返回可用的 webApiServerCredential，请重新登录。")
            return self._credential

    # -------------------------------------------------------------- 登录
    async def _login(self) -> None:
        access_token, id_token = await self._fresh_tokens()
        profile = await self._users_me(access_token)
        na_id = str(profile.get("id") or "").strip()
        if not na_id:
            raise NsoAuthError("Nintendo 账号信息缺少 id，无法生成 f 参数。")

        parameter = {
            "naIdToken": id_token,
            "language": profile.get("language") or "zh-CN",
            "timestamp": 0,
            "requestId": "",
            "f": "",
        }
        f_result = await self._gen_f(
            id_token,
            na_id,
            {"url": f"{NSO_API_BASE}/v4/Account/Login", "parameter": parameter},
        )

        encrypted_token_request = str(f_result.get("encrypted_token_request") or "")
        if encrypted_token_request:
            body = base64.b64decode(encrypted_token_request)
        else:
            parameter["f"] = str(f_result.get("f") or "")
            parameter["requestId"] = str(f_result.get("request_id") or "")
            parameter["timestamp"] = int(f_result.get("timestamp") or 0)
            raw = json.dumps({"parameter": parameter}, separators=(",", ":"))
            body = await self._encrypt_request(f"{NSO_API_BASE}/v4/Account/Login", None, raw)

        response = await self._request(
            "POST",
            f"{NSO_API_BASE}/v4/Account/Login",
            headers=self._coral_headers(None),
            content=body,
        )
        payload = await self._decode_coral_response(response)
        self._raise_for_coral_status(payload)

        result = payload.get("result") or {}
        credential = (result.get("webApiServerCredential") or {}).get("accessToken") or ""
        expires_in = int((result.get("webApiServerCredential") or {}).get("expiresIn") or 7200)
        if not credential:
            raise NsoAuthError("Account/Login 未返回 webApiServerCredential.accessToken。")
        self._credential = credential
        self._credential_expires_at = time.time() + max(300, expires_in - 120)

        user = result.get("user") or {}
        na_user = result.get("naUser") or {}
        try:
            self._coral_user_id = int(user.get("id") or 0)
        except (TypeError, ValueError):
            self._coral_user_id = 0
        friend_code = ((user.get("links") or {}).get("friendCode") or {}).get("id") or ""
        account = {
            "nickname": user.get("name") or na_user.get("nickname") or profile.get("nickname") or "",
            "friend_code": friend_code,
            "nsa_id": user.get("nsaId") or "",
            "image_url": user.get("imageUri") or "",
            "country": na_user.get("country") or profile.get("country") or "",
            "birthday": na_user.get("birthday") or profile.get("birthday") or "",
            "language": na_user.get("language") or profile.get("language") or "",
            "coral_user_id": self._coral_user_id,
            "updated_at": int(time.time()),
        }
        self._account = account
        self.last_login_at = time.time()
        if self._on_account is not None:
            await self._on_account(account)

    async def _fresh_tokens(self) -> tuple[str, str]:
        response = await self._request(
            "POST",
            TOKEN_URL,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": self.user_agent,
                "Accept": "application/json",
                "X-Platform": "Android",
            },
            json={
                "client_id": CLIENT_ID,
                "session_token": self._session_token,
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer-session-token",
            },
        )
        if response.status_code >= 400:
            raise NsoAuthRequired(
                "session_token 已失效或被 Nintendo 拒绝，请重新执行 /nso 登录。"
                f"（HTTP {response.status_code}）"
            )
        payload = self._json(response, "token 接口")
        access_token = str(payload.get("access_token") or "").strip()
        id_token = str(payload.get("id_token") or "").strip()
        if not access_token or not id_token:
            raise NsoAuthError("token 接口返回缺少 access_token / id_token。")
        return access_token, id_token

    async def _users_me(self, access_token: str) -> dict[str, Any]:
        response = await self._request(
            "GET",
            USERS_ME_URL,
            headers={
                "Accept": "application/json",
                "Accept-Language": "en-US",
                "User-Agent": self.user_agent,
                "X-Platform": "Android",
                "Authorization": f"Bearer {access_token}",
            },
        )
        if response.status_code == 401:
            raise NsoAuthRequired("Nintendo 账号信息接口返回 401，请重新执行 /nso 登录。")
        if response.status_code >= 400:
            raise NsoApiError(
                f"读取 Nintendo 账号信息失败（HTTP {response.status_code}）。",
                http_status=response.status_code,
            )
        return self._json(response, "users/me 接口")

    # --------------------------------------------------------------- 业务
    async def coral_request(self, path: str, parameter: dict[str, Any] | None = None) -> dict[str, Any]:
        """发送一个加密的 Coral 请求并返回解密后的完整 JSON。"""

        credential = await self.ensure_credential()
        for attempt in range(2):
            body = json.dumps(
                {"parameter": parameter or {}, "requestId": str(uuid.uuid4())},
                separators=(",", ":"),
            )
            encrypted = await self._encrypt_request(f"{NSO_API_BASE}{path}", credential, body)
            response = await self._request(
                "POST",
                f"{NSO_API_BASE}{path}",
                headers=self._coral_headers(credential),
                content=encrypted,
            )
            if response.status_code == 401 and attempt == 0:
                self.invalidate_credential()
                credential = await self.ensure_credential()
                continue
            payload = await self._decode_coral_response(response)
            if payload.get("status") == 9404 and attempt == 0:
                self.invalidate_credential()
                credential = await self.ensure_credential()
                continue
            self._raise_for_coral_status(payload)
            return payload
        raise NsoApiError("Coral 请求重试后仍然失败。")

    # --------------------------------------------------------------- f 参数
    async def _gen_f(
        self,
        id_token: str,
        na_id: str,
        encrypt_request: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "hash_method": "1",
            "token": id_token,
            "na_id": na_id,
        }
        if encrypt_request is not None:
            body["encrypt_token_request"] = encrypt_request

        for attempt in range(2):
            headers = await self._f_headers()
            headers.update({"Content-Type": "application/json", "Accept": "application/json"})
            response = await self._request("POST", self.f_api_url, headers=headers, json=body)
            if response.status_code == 401 and attempt == 0 and self._f_client_id:
                self._f_token = ""
                continue
            if response.status_code != 200:
                self._raise_f_error(response, "获取 f 参数")
            payload = self._json(response, "f 参数接口")
            if not payload.get("f") and not payload.get("encrypted_token_request"):
                raise NsoApiError("f 参数接口返回缺少 f / encrypted_token_request。")
            return payload
        raise NsoApiError("f 参数服务重试后仍然失败。")

    async def _ensure_f_token(self) -> str:
        if not self._f_client_id:
            return ""
        if self._f_token and time.time() < self._f_token_expires_at - 60:
            return self._f_token
        async with self._f_lock:
            if self._f_token and time.time() < self._f_token_expires_at - 60:
                return self._f_token
            data = {
                "grant_type": "client_credentials",
                "scope": self._f_scope,
                "client_id": self._f_client_id,
            }
            if self._f_client_secret:
                data["client_secret"] = self._f_client_secret
            response = await self._request(
                "POST",
                self._f_token_url,
                headers={"Accept": "application/json", "User-Agent": "astrbot-nso-friends/2.0"},
                data=data,
            )
            if response.status_code != 200:
                raise NsoAuthError(
                    "nxapi-auth 获取访问令牌失败"
                    f"（HTTP {response.status_code}）：{response.text[:200]}"
                )
            payload = self._json(response, "nxapi-auth token 接口")
            token = str(payload.get("access_token") or "")
            if not token:
                raise NsoAuthError("nxapi-auth 未返回 access_token。")
            expires_in = int(payload.get("expires_in") or 3600)
            self._f_token = token
            self._f_token_expires_at = time.time() + max(120, expires_in - 60)
            return token

    async def _f_headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": f"astrbot-nso-friends/2.0 (+{self.f_api_url})",
            "X-znca-Client-Version": self._f_client_version,
        }
        if self._f_client_id:
            token = await self._ensure_f_token()
            if token:
                headers["Authorization"] = f"Bearer {token}"
        headers["X-znca-Platform"] = "Android"
        headers["X-znca-Version"] = self.app_version
        headers.update(self._f_api_headers)
        return headers

    # ---------------------------------------------------------- 加解密
    async def _encrypt_request(self, url: str, token: str | None, data: str) -> bytes:
        for attempt in range(2):
            headers = await self._f_headers()
            headers.update({"Content-Type": "application/json", "Accept": "application/json"})
            response = await self._request(
                "POST",
                f"{self._f_api_base}encrypt-request",
                headers=headers,
                json={"url": url, "token": token, "data": data},
            )
            if response.status_code == 401 and attempt == 0 and self._f_client_id:
                self._f_token = ""
                continue
            if response.status_code != 200:
                self._raise_f_error(response, "加密请求")
            payload = self._json(response, "encrypt-request 接口")
            encrypted = str(payload.get("data") or "")
            if not encrypted:
                raise NsoApiError("encrypt-request 未返回 data。")
            return _b64url_decode(encrypted)
        raise NsoApiError("encrypt-request 重试后仍然失败。")

    async def _decrypt_response(self, data: bytes) -> str:
        for attempt in range(2):
            headers = await self._f_headers()
            headers.update({"Content-Type": "application/json", "Accept": "text/plain"})
            response = await self._request(
                "POST",
                f"{self._f_api_base}decrypt-response",
                headers=headers,
                json={"data": base64.b64encode(data).decode("ascii")},
            )
            if response.status_code == 401 and attempt == 0 and self._f_client_id:
                self._f_token = ""
                continue
            if response.status_code != 200:
                self._raise_f_error(response, "解密响应")
            content_type = str(response.headers.get("content-type") or "")
            if "application/json" in content_type:
                payload = self._json(response, "decrypt-response 接口")
                return str(payload.get("data") or "")
            return response.text
        raise NsoApiError("decrypt-response 重试后仍然失败。")

    async def _decode_coral_response(self, response: Response) -> dict[str, Any]:
        raw = response.content
        if response.status_code >= 400:
            detail = raw[:200].decode("utf-8", "replace")
            raise NsoApiError(
                f"Coral 接口返回 HTTP {response.status_code}：{detail}",
                http_status=response.status_code,
            )
        payload: dict[str, Any] | None = None
        if raw.lstrip().startswith(b"{"):
            try:
                candidate = json.loads(raw)
            except ValueError:
                candidate = None
            if isinstance(candidate, dict):
                payload = candidate
        if payload is None:
            text = await self._decrypt_response(raw)
            try:
                payload = json.loads(text)
            except ValueError as exc:
                raise NsoApiError("解密后的 Coral 响应不是合法 JSON。") from exc
        if not isinstance(payload, dict):
            raise NsoApiError("Coral 响应结构异常。")
        return payload

    # --------------------------------------------------------------- 工具
    def _coral_headers(self, credential: str | None) -> dict[str, str]:
        headers = {
            "User-Agent": self.user_agent,
            "X-Platform": "Android",
            "X-ProductVersion": self.app_version,
            "Accept-Language": "en-US",
            "Content-Type": "application/octet-stream",
            "Accept": "application/octet-stream,application/json",
            "Pragma": "no-cache",
            "Cache-Control": "no-cache",
        }
        if credential:
            headers["Authorization"] = f"Bearer {credential}"
        return headers

    def _raise_for_coral_status(self, payload: dict[str, Any]) -> None:
        status = payload.get("status")
        if status == 0:
            return
        message = str(payload.get("errorMessage") or payload.get("message") or "").strip()
        hint = ""
        if status in (9403, 9404, 9406):
            hint = "（登录凭证已失效；若持续出现请重新 /nso 登录）"
        raise NsoApiError(
            f"Coral 接口返回 status={status}" + (f"：{message}" if message else "") + hint,
            status=status if isinstance(status, int) else None,
        )

    def _raise_f_error(self, response: Response, action: str) -> None:
        detail = ""
        try:
            payload = response.json()
            detail = str(payload.get("error_description") or payload.get("error_message") or payload.get("error") or "")
        except ValueError:
            detail = response.text[:160]
        hint = ""
        if response.status_code in (401, 403):
            hint = "（客户端未获授权或 token 无效，请检查 f_api_client_id / f_api_client_secret / scope）"
        elif response.status_code in (500, 502, 503, 504):
            hint = "（f 服务不可用；请检查 f_api_url / 网络，或改用自建服务）"
        raise NsoApiError(
            f"{action}失败（HTTP {response.status_code}）：{detail}{hint}",
            http_status=response.status_code,
        )

    async def _exchange_session_token(self, code: str, verifier: str) -> str:
        response = await self._request(
            "POST",
            SESSION_TOKEN_URL,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": self.user_agent,
                "Accept": "application/json",
                "X-Platform": "Android",
            },
            data={
                "client_id": CLIENT_ID,
                "session_token_code": code,
                "session_token_code_verifier": verifier,
            },
        )
        if response.status_code >= 400:
            raise NsoAuthError(
                "session_token 换取失败：登录链接可能已过期或已被使用，请重新执行 /nso 登录。"
                f"（HTTP {response.status_code}）"
            )
        payload = self._json(response, "session_token 接口")
        token = str(payload.get("session_token") or "").strip()
        if not token:
            raise NsoAuthError("session_token 接口没有返回 session_token。")
        return token

    async def _request(self, method: str, url: str, **kwargs: Any) -> Response:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                return await self._transport.request(method, url, **kwargs)
            except Exception as exc:  # noqa: BLE001 - 网络异常类型由 httpx 决定
                last_error = exc
                if attempt >= 2:
                    break
                await asyncio.sleep(0.8 * (attempt + 1))
        raise NsoNetworkError(f"请求 {url} 失败：{last_error}") from last_error

    @staticmethod
    def _derive_f_api_base(url: str) -> str:
        base = url.rstrip("/")
        if base.endswith("/f"):
            base = base[:-2]
        elif base.endswith("f"):
            base = base[:-1]
        return base.rstrip("/") + "/"

    @staticmethod
    def _json(response: Response, label: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise NsoApiError(f"{label}返回了非 JSON 内容（HTTP {response.status_code}）。") from exc
        if not isinstance(payload, dict):
            raise NsoApiError(f"{label}返回了非预期的 JSON 结构。")
        return payload
