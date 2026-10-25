"""Nintendo Switch Online 鉴权。

严格按照 wiki 的流程实现，只对两处明显的笔误做了修正（README 中有说明）：

1. session_token 换取接口实际要求 ``POST`` + ``application/x-www-form-urlencoded``，
   wiki 里写成了 GET + JSON body；
2. ``/v3/Account/Login`` 的 ``naIdToken`` 与 imink 的 ``token`` 都使用 ``id_token``
   （wiki 正文写成 access_token，但示例 JWT 的 ``typ`` 是 ``id_token``）。

流程总览：
    authorize(PKCE) -> session_token_code -> session_token
    session_token -> access_token + id_token -> /users/me
    id_token -> imink f-API -> f / requestId / timestamp
    /v3/Account/Login -> webApiServerCredential (2 小时有效)
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import re
import secrets
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs, quote, urlencode

from .http import Response, Transport

CLIENT_ID = "71b963c1b7b6d119"
AUTHORIZE_URL = "https://accounts.nintendo.com/connect/1.0.0/authorize"
SESSION_TOKEN_URL = "https://accounts.nintendo.com/connect/1.0.0/api/session_token"
TOKEN_URL = "https://accounts.nintendo.com/connect/1.0.0/api/token"
USERS_ME_URL = "https://api.accounts.nintendo.com/2.0.0/users/me"
DEFAULT_F_API_URL = "https://api.imink.app/f"
NSO_API_BASE = "https://api-lp1.znc.srv.nintendo.net"
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
    """Nintendo 返回了非 0 的 status 或非预期 HTTP 状态。"""

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
    """43 字符的 base64url 随机串，满足 PKCE 对 verifier 的要求。"""

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
    """从用户粘贴的回调链接里取出 (session_token_code, state)。

    兼容以下几种粘贴形式：
        npf71b963c1b7b6d119://auth#session_state=...&session_token_code=...&state=...
        npf71b963c1b7b6d119://auth?session_token_code=...&state=...
        session_token_code=...&state=...
        裸的 session_token_code
    """

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
        # 允许直接粘贴裸 code
        code = raw
    if not code:
        raise NsoAuthError(
            "链接里没有找到 session_token_code。请确认复制的是 Nintendo 登录页"
            "「选择此人」按钮的链接地址（以 npf...://auth# 开头）。"
        )
    return code, state


class NintendoAuth:
    """负责 token 获取与 ``webApiServerCredential`` 缓存。"""

    def __init__(
        self,
        transport: Transport,
        *,
        app_version: str = "2.11.0",
        f_api_url: str = DEFAULT_F_API_URL,
        f_api_headers: dict[str, str] | None = None,
        session_token: str = "",
        on_session_token: Callable[[str], Awaitable[None]] | None = None,
        on_account: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self._transport = transport
        self.app_version = (app_version or "2.11.0").strip()
        self.f_api_url = f_api_url or DEFAULT_F_API_URL
        self._f_api_urls = [part for part in re.split(r"[\s,;]+", self.f_api_url.strip()) if part]
        self._f_api_headers = {
            str(key).strip(): str(value).strip()
            for key, value in (f_api_headers or {}).items()
            if str(key).strip() and str(value).strip()
        }
        self._session_token = (session_token or "").strip()
        self._on_session_token = on_session_token
        self._on_account = on_account
        self._credential: str = ""
        self._credential_expires_at: float = 0.0
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

    # -------------------------------------------------------------- 各步骤
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

    async def _login(self) -> None:
        access_token, id_token = await self._fresh_tokens()
        profile = await self._users_me(access_token)
        f_result = await self._imink_f(id_token)
        credential, account = await self._account_login(id_token, profile, f_result)
        self._credential = credential
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

    async def _imink_f(self, id_token: str) -> dict[str, Any]:
        """调用 f 参数服务。

        支持在 ``f_api_url`` 里用逗号/空格填写多个服务地址，按顺序尝试。
        公共 imink 实例自 2024-12 起受 Nintendo Play Integrity 影响长期不可用，
        因此这里也允许通过 ``f_api_headers`` 携带自建服务/授权服务的请求头。
        """

        failures: list[tuple[str, int, str]] = []
        payload: dict[str, Any] | None = None

        for url in self._f_api_urls:
            headers = {
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": f"astrbot-nso-friends/1.0 (+{url})",
                "Accept": "application/json",
            }
            headers.update(self._f_api_headers)
            response = await self._request(
                "POST",
                url,
                headers=headers,
                json={"token": id_token, "hash_method": 1},
            )
            if response.status_code == 200:
                try:
                    candidate = self._json(response, "f 参数接口")
                except NsoApiError:
                    failures.append((url, 200, "响应不是合法 JSON"))
                    continue
                missing = [key for key in ("f", "request_id", "timestamp") if not candidate.get(key)]
                if missing:
                    failures.append((url, 200, f"缺少字段 {','.join(missing)}"))
                    continue
                payload = candidate
                break
            failures.append((url, response.status_code, response.text[:160]))

        if payload is not None:
            return payload

        statuses = {status for _, status, _ in failures}
        hint = ""
        if statuses & {500, 502, 503, 504}:
            hint = (
                "（公共 imink f 服务受 Nintendo Play Integrity 影响已长期故障；"
                "请改用自建 f 服务，或在 f_api_url 中填写可用的服务地址）"
            )
        elif statuses & {401, 403}:
            hint = "（该 f 服务需要授权，请通过 f_api_headers 配置 Authorization 等请求头）"
        elif 429 in statuses:
            hint = "（f 服务触发限流，请稍后重试或更换服务地址）"

        url, status, detail = failures[-1] if failures else ("未配置 f_api_url", 0, "")
        raise NsoApiError(
            f"获取 f 参数失败（{url} HTTP {status}）：{detail}{hint}",
            http_status=status or None,
        )

    async def _account_login(
        self, id_token: str, profile: dict[str, Any], f_result: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        body = {
            "parameter": {
                "f": f_result["f"],
                "language": profile.get("language") or "zh-CN",
                "naBirthday": profile.get("birthday") or "",
                "naCountry": profile.get("country") or "",
                "naIdToken": id_token,
                "requestId": f_result["request_id"],
                "timestamp": f_result["timestamp"],
            }
        }
        response = await self._request(
            "POST",
            f"{NSO_API_BASE}/v3/Account/Login",
            headers=self.web_headers(""),
            json=body,
        )
        if response.status_code == 401:
            raise NsoAuthError("NSO 登录接口返回 401，token 可能已失效，请重新 /nso 登录。")
        if response.status_code >= 400:
            raise NsoApiError(
                f"NSO 登录失败（HTTP {response.status_code}）：{response.text[:200]}",
                http_status=response.status_code,
            )
        payload = self._json(response, "Account/Login 接口")
        status = payload.get("status")
        if status != 0:
            raise self._nso_status_error(status, payload)
        result = payload.get("result") or {}
        credential = (result.get("webApiServerCredential") or {}).get("accessToken") or ""
        expires_in = int((result.get("webApiServerCredential") or {}).get("expiresIn") or 7200)
        if not credential:
            raise NsoAuthError("Account/Login 未返回 webApiServerCredential.accessToken。")
        self._credential_expires_at = time.time() + max(300, expires_in - 120)

        user = result.get("user") or {}
        friend_code = ((user.get("links") or {}).get("friendCode") or {}).get("id") or ""
        account = {
            "nickname": user.get("name") or profile.get("nickname") or "",
            "friend_code": friend_code,
            "nsa_id": user.get("nsaId") or "",
            "image_url": user.get("imageUri") or "",
            "country": profile.get("country") or "",
            "birthday": profile.get("birthday") or "",
            "language": profile.get("language") or "",
            "updated_at": int(time.time()),
        }
        return credential, account

    # -------------------------------------------------------------- 工具方法
    def web_headers(self, credential: str | None = None) -> dict[str, str]:
        token = credential if credential is not None else self._credential
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/json",
            "Accept-Language": "en-US",
            "X-Platform": "Android",
            "X-ProductVersion": self.app_version,
            "Content-Type": "application/json;charset=utf-8",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def request_json(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        """带鉴权重试的 NSO 业务请求（供 NsoClient 使用）。"""

        credential = await self.ensure_credential()
        response = await self._request(method, url, headers=self.web_headers(credential), **kwargs)
        if response.status_code == 401:
            self.invalidate_credential()
            credential = await self.ensure_credential()
            response = await self._request(method, url, headers=self.web_headers(credential), **kwargs)
        if response.status_code >= 400:
            raise NsoApiError(
                f"NSO 接口请求失败（HTTP {response.status_code}）：{response.text[:200]}",
                http_status=response.status_code,
            )
        payload = self._json(response, url)
        status = payload.get("status")
        if status != 0:
            raise self._nso_status_error(status, payload)
        return payload

    def _nso_status_error(self, status: Any, payload: dict[str, Any]) -> NsoApiError:
        message = str(payload.get("errorMessage") or payload.get("message") or "").strip()
        hint = ""
        if status in (9403, 9404, 401):
            hint = "（可能是 app_version 过旧或 f 参数失效，请更新插件配置 app_version）"
        text = f"NSO 接口返回 status={status}"
        if message:
            text += f"：{message}"
        return NsoApiError(text + hint, status=status if isinstance(status, int) else None)

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
    def _json(response: Response, label: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise NsoApiError(f"{label}返回了非 JSON 内容（HTTP {response.status_code}）。") from exc
        if not isinstance(payload, dict):
            raise NsoApiError(f"{label}返回了非预期的 JSON 结构。")
        return payload
