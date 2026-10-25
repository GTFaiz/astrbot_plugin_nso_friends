from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from nso.store import SESSION_LIMIT_PER_FRIEND, JsonStore


class StoreTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "nested" / "state.json"
        self.store = JsonStore(self.path)
        await self.store.load()

    async def test_roundtrip(self) -> None:
        await self.store.set_session_token("token-abc")
        await self.store.set_account({"nickname": "Me", "friend_code": "1234"})
        await self.store.set_subscription("aiocqhttp:GroupMessage:1", ["Alice"])
        self.store.set_friend_state(1001, {"name": "Alice", "playing": True, "game": "Splatoon 3"})
        await self.store.save()

        reloaded = JsonStore(self.path)
        await reloaded.load()
        self.assertEqual(reloaded.session_token, "token-abc")
        self.assertEqual(reloaded.account["nickname"], "Me")
        self.assertEqual(reloaded.subscription("aiocqhttp:GroupMessage:1")["friends"], ["Alice"])
        self.assertTrue(reloaded.friend_state(1001)["playing"])

    async def test_unsubscribe(self) -> None:
        await self.store.set_subscription("umo")
        self.assertTrue(await self.store.remove_subscription("umo"))
        self.assertFalse(await self.store.remove_subscription("umo"))
        self.assertIsNone(self.store.subscription("umo"))

    async def test_corrupt_file_is_backed_up(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{not json", encoding="utf-8")
        store = JsonStore(self.path)
        await store.load()
        self.assertEqual(store.session_token, "")
        backups = list(self.path.parent.glob("state.corrupt-*"))
        self.assertEqual(len(backups), 1)

    async def test_sessions_are_capped(self) -> None:
        base = int(time.time()) - (SESSION_LIMIT_PER_FRIEND + 10) * 120
        for index in range(SESSION_LIMIT_PER_FRIEND + 10):
            await self.store.add_session(1001, "Game", base + index * 120, base + index * 120 + 60, save=False)
        await self.store.save()
        sessions = self.store.sessions_for(1001)
        self.assertEqual(len(sessions), SESSION_LIMIT_PER_FRIEND)

    async def test_old_sessions_are_pruned(self) -> None:
        old_start = int(time.time()) - 120 * 86400
        self.store.data["sessions"]["1001"] = [{"game": "Old", "start": old_start, "end": old_start + 60}]
        await self.store.add_session(1001, "New", time.time() - 60, time.time())
        games = [session["game"] for session in self.store.sessions_for(1001)]
        self.assertEqual(games, ["New"])

    async def test_clear_session_keeps_subscriptions(self) -> None:
        await self.store.set_session_token("token")
        await self.store.set_subscription("umo")
        await self.store.clear_session()
        self.assertEqual(self.store.session_token, "")
        self.assertIsNotNone(self.store.subscription("umo"))

    async def test_defaults_survive_partial_file(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"session_token": "abc"}), encoding="utf-8")
        store = JsonStore(self.path)
        await store.load()
        self.assertEqual(store.session_token, "abc")
        self.assertEqual(store.subscriptions(), {})
        self.assertEqual(store.friend_states(), {})


if __name__ == "__main__":
    unittest.main()
