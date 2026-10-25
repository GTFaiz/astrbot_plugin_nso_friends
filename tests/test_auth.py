from __future__ import annotations

import unittest
from urllib.parse import parse_qs, urlsplit

from nso.auth import (
    CLIENT_ID,
    SESSION_TOKEN_URL,
    NsoApiError,
    NsoAuthError,
    NsoAuthRequired,
    NintendoAuth,
    code_challenge,
    create_login_challenge,
    parse_redirect_link,
)
from nso.client import NsoClient
from nso.http import Response

from tests.fake import FakeTransport, friend_payload


class LoginChallengeTest(unittest.TestCase):
    def test_challenge_is_valid_pkce(self) -> None:
        challenge = create_login_challenge()
        params = parse_qs(urlsplit(challenge.url).query)
        self.assertEqual(params["client_id"][0], CLIENT_ID)
        self.assertEqual(params["response_type"][0], "session_token_code")
        self.assertEqual(params["session_token_code_challenge_method"][0], "S256")
        self.assertEqual(params["session_token_code_challenge"][0], code_challenge(challenge.verifier))
        self.assertNotIn("=", params["session_token_code_challenge"][0])
        self.assertEqual(len(challenge.state), 43)
        self.assertTrue(challenge.url.startswith("https://accounts.nintendo.com/connect/1.0.0/authorize?"))

    def test_parse_redirect_link_variants(self) -> None:
        link = "npf71b963c1b7b6d119://auth#session_state=abc&session_token_code=CODE123&state=STATE1"
        self.assertEqual(parse_redirect_link(link), ("CODE123", "STATE1"))
        query_link = "npf71b963c1b7b6d119://auth?session_token_code=CODE456&state=STATE2"
        self.assertEqual(parse_redirect_link(query_link), ("CODE456", "STATE2"))
        self.assertEqual(parse_redirect_link("session_token_code=CODE789&state=STATE3"), ("CODE789", "STATE3"))
        self.assertEqual(parse_redirect_link("RAWCODE"), ("RAWCODE", ""))

    def test_parse_redirect_link_rejects_garbage(self) -> None:
        with self.assertRaises(NsoAuthError):
            parse_redirect_link("")


class FallbackTransport(FakeTransport):
    """f 服务多地址回退测试用传输层。"""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.f_headers: list[dict] = []

    async def request(self, method: str, url: str, **kwargs):
        if url.startswith("https://broken.example/"):
            self.requests.append((method, url, kwargs))
            self.calls[url] += 1
            return Response(500, b'{"error":true,"reason":"Something went wrong."}')
        if url.startswith("https://good.example/"):
            self.requests.append((method, url, kwargs))
            self.calls[url] += 1
            self.f_headers.append(dict(kwargs.get("headers") or {}))
            import json

            return Response(200, json.dumps({"f": "f2", "request_id": "req2", "timestamp": 1}).encode())
        return await super().request(method, url, **kwargs)


class AuthFlowTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.transport = FakeTransport(friends=[friend_payload("Alice", game="Splatoon 3")])
        self.saved_tokens: list[str] = []
        self.saved_accounts: list[dict] = []

        async def on_token(token: str) -> None:
            self.saved_tokens.append(token)

        async def on_account(account: dict) -> None:
            self.saved_accounts.append(account)

        self.auth = NintendoAuth(
            self.transport,
            app_version="2.11.0",
            on_session_token=on_token,
            on_account=on_account,
        )

    def _redirect(self, challenge, code: str = "CODE") -> str:
        return f"npf71b963c1b7b6d119://auth#session_token_code={code}&state={challenge.state}"

    async def test_full_login_and_friend_list(self) -> None:
        challenge = create_login_challenge()
        account = await self.auth.login_with_redirect(self._redirect(challenge), challenge)

        self.assertEqual(account["nickname"], "Me")
        self.assertEqual(account["friend_code"], "1234-5678-9012")
        self.assertEqual(self.saved_tokens, ["session-token-1"])
        self.assertEqual(self.saved_accounts[0]["nickname"], "Me")
        self.assertTrue(self.auth.credential_ready)

        session_call = self.transport.last(SESSION_TOKEN_URL)
        self.assertEqual(session_call["data"]["client_id"], CLIENT_ID)
        self.assertEqual(session_call["data"]["session_token_code"], "CODE")
        self.assertEqual(session_call["data"]["session_token_code_verifier"], challenge.verifier)

        f_call = self.transport.last(self.auth.f_api_url)
        self.assertEqual(f_call["json"], {"token": "id-token", "hash_method": 1})

        login_call = self.transport.last("https://api-lp1.znc.srv.nintendo.net/v3/Account/Login")
        parameter = login_call["json"]["parameter"]
        self.assertEqual(parameter["naIdToken"], "id-token")
        self.assertEqual(parameter["naCountry"], "JP")
        self.assertEqual(parameter["naBirthday"], "2000-01-01")
        self.assertEqual(parameter["language"], "zh-CN")
        self.assertEqual(parameter["f"], "f-value")
        self.assertEqual(login_call["headers"]["X-ProductVersion"], "2.11.0")
        self.assertIn("com.nintendo.znca/2.11.0", login_call["headers"]["User-Agent"])

        client = NsoClient(self.auth)
        friends = await client.friends()
        self.assertEqual([friend.name for friend in friends], ["Alice"])
        self.assertEqual(friends[0].game.name, "Splatoon 3")
        self.assertTrue(friends[0].playing)

        friend_call = self.transport.last("https://api-lp1.znc.srv.nintendo.net/v3/Friend/List")
        self.assertEqual(friend_call["headers"]["Authorization"], "Bearer cred-1")

    async def test_credential_is_cached(self) -> None:
        challenge = create_login_challenge()
        await self.auth.login_with_redirect(self._redirect(challenge), challenge)
        await self.auth.ensure_credential()
        await self.auth.ensure_credential()
        self.assertEqual(self.transport.login_count, 1)

    async def test_state_mismatch_is_rejected(self) -> None:
        challenge = create_login_challenge()
        bad_link = "npf71b963c1b7b6d119://auth#session_token_code=CODE&state=WRONG"
        with self.assertRaises(NsoAuthError):
            await self.auth.login_with_redirect(bad_link, challenge)
        self.assertEqual(self.transport.calls_for(SESSION_TOKEN_URL), [])

    async def test_bad_code_is_rejected(self) -> None:
        self.transport.fail_session_token = True
        challenge = create_login_challenge()
        with self.assertRaises(NsoAuthError):
            await self.auth.login_with_redirect(self._redirect(challenge), challenge)
        self.assertEqual(self.saved_tokens, [])

    async def test_missing_session_token_raises(self) -> None:
        with self.assertRaises(NsoAuthRequired):
            await self.auth.ensure_credential()

    async def test_f_service_fallback_and_headers(self) -> None:
        transport = FallbackTransport()
        auth = NintendoAuth(
            transport,
            f_api_url="https://broken.example/f, https://good.example/f",
            f_api_headers={"Authorization": "Bearer test-token"},
        )
        challenge = create_login_challenge()
        link = f"npf71b963c1b7b6d119://auth#session_token_code=CODE&state={challenge.state}"
        account = await auth.login_with_redirect(link, challenge)
        self.assertEqual(account["nickname"], "Me")
        self.assertEqual(transport.calls["https://broken.example/f"], 1)
        self.assertEqual(transport.calls["https://good.example/f"], 1)
        self.assertEqual(transport.f_headers[0]["Authorization"], "Bearer test-token")

    async def test_f_service_failure_message_is_actionable(self) -> None:
        class Always500(FakeTransport):
            async def request(self, method, url, **kwargs):
                if url == "https://broken.example/f":
                    return Response(500, b'{"error":true,"reason":"Something went wrong."}')
                return await super().request(method, url, **kwargs)

        auth = NintendoAuth(Always500(), app_version="2.11.0", f_api_url="https://broken.example/f")
        challenge = create_login_challenge()
        link = f"npf71b963c1b7b6d119://auth#session_token_code=CODE&state={challenge.state}"
        with self.assertRaises(NsoApiError) as ctx:
            await auth.login_with_redirect(link, challenge)
        self.assertIn("Play Integrity", str(ctx.exception))

    async def test_expired_credential_triggers_relogin(self) -> None:
        client = NsoClient(self.auth)
        challenge = create_login_challenge()
        await self.auth.login_with_redirect(self._redirect(challenge), challenge)
        self.assertEqual(self.transport.login_count, 1)

        self.transport.expire_first_friend_call = True
        friends = await client.friends()
        self.assertEqual(len(friends), 1)
        self.assertEqual(self.transport.login_count, 2)
        self.assertEqual(
            self.transport.last("https://api-lp1.znc.srv.nintendo.net/v3/Friend/List")["headers"]["Authorization"],
            "Bearer cred-2",
        )


if __name__ == "__main__":
    unittest.main()
