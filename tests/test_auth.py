from __future__ import annotations

import unittest
from urllib.parse import parse_qs, urlsplit

from nso.auth import (
    CLIENT_ID,
    DEFAULT_F_API_URL,
    DEFAULT_F_TOKEN_URL,
    SESSION_TOKEN_URL,
    NsoAuthError,
    NsoAuthRequired,
    NintendoAuth,
    code_challenge,
    create_login_challenge,
    parse_redirect_link,
)
from nso.client import NsoClient

from tests.fake import ENCRYPT_URL, LOGIN_URL, FakeTransport, friend_payload


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
            app_version="3.5.0",
            f_api_headers={},
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
        self.assertEqual(account["coral_user_id"], 123)
        self.assertEqual(self.saved_tokens, ["session-token-1"])
        self.assertEqual(self.saved_accounts[0]["nickname"], "Me")
        self.assertTrue(self.auth.credential_ready)

        session_call = self.transport.last(SESSION_TOKEN_URL)
        self.assertEqual(session_call["data"]["client_id"], CLIENT_ID)
        self.assertEqual(session_call["data"]["session_token_code"], "CODE")
        self.assertEqual(session_call["data"]["session_token_code_verifier"], challenge.verifier)

        f_call = self.transport.last(DEFAULT_F_API_URL)
        body = f_call["json"]
        self.assertEqual(body["hash_method"], "1")
        self.assertEqual(body["token"], "id-token")
        self.assertEqual(body["na_id"], "na-id")
        self.assertEqual(body["encrypt_token_request"]["url"], LOGIN_URL)
        self.assertEqual(body["encrypt_token_request"]["parameter"]["naIdToken"], "id-token")
        self.assertEqual(f_call["headers"]["X-znca-Version"], "3.5.0")
        self.assertEqual(f_call["headers"]["X-znca-Client-Version"], "d8fAZDPzwimzQ7c6")

        # 登录请求体是加密后的 octet-stream，并且走了解密接口
        login_call = self.transport.last(LOGIN_URL)
        self.assertTrue(login_call["content"].startswith(b"ENC:"))
        self.assertEqual(login_call["headers"]["Content-Type"], "application/octet-stream")
        self.assertGreaterEqual(self.transport.decrypt_count, 1)

        client = NsoClient(self.auth)
        friends = await client.friends()
        self.assertEqual([friend.name for friend in friends], ["Alice"])
        self.assertEqual(friends[0].game.name, "Splatoon 3")
        self.assertTrue(friends[0].playing)

        friend_call = self.transport.last("https://api-lp1.znc.srv.nintendo.net/v4/Friend/List")
        self.assertEqual(friend_call["headers"]["Authorization"], "Bearer cred-1")
        self.assertTrue(friend_call["content"].startswith(b"ENC:"))
        self.assertGreaterEqual(self.transport.encrypt_count, 1)

    async def test_credential_is_cached(self) -> None:
        challenge = create_login_challenge()
        await self.auth.login_with_redirect(self._redirect(challenge), challenge)
        await self.auth.ensure_credential()
        await self.auth.ensure_credential()
        self.assertEqual(self.transport.login_count, 1)

    async def test_oauth_is_used_for_nxapi(self) -> None:
        transport = FakeTransport(friends=[friend_payload("Alice", game="Splatoon 3")])
        auth = NintendoAuth(
            transport,
            app_version="3.5.0",
            f_api_client_id="client-id",
            f_api_client_secret="client-secret",
            f_api_scope="ca:gf ca:er ca:dr",
        )
        challenge = create_login_challenge()
        await auth.login_with_redirect(self._redirect(challenge), challenge)

        self.assertEqual(transport.oauth_count, 1)
        oauth = transport.oauth_requests[0]
        self.assertEqual(oauth["grant_type"], "client_credentials")
        self.assertEqual(oauth["client_id"], "client-id")
        self.assertEqual(oauth["client_secret"], "client-secret")
        self.assertEqual(oauth["scope"], "ca:gf ca:er ca:dr")
        self.assertEqual(transport.last(DEFAULT_F_API_URL)["headers"]["Authorization"], "Bearer f-access")
        await NsoClient(auth).friends()
        self.assertEqual(transport.last(ENCRYPT_URL)["headers"]["Authorization"], "Bearer f-access")
        self.assertEqual(transport.last(DEFAULT_F_TOKEN_URL)["headers"]["Accept"], "application/json")

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
            self.transport.last("https://api-lp1.znc.srv.nintendo.net/v4/Friend/List")["headers"]["Authorization"],
            "Bearer cred-2",
        )


if __name__ == "__main__":
    unittest.main()
