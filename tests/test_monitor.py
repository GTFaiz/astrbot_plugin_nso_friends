from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from nso.models import Friend, Game
from nso.monitor import FriendMonitor
from nso.store import JsonStore

START = int(time.time()) - 86400


class Clock:
    def __init__(self, now: int = START) -> None:
        self.now = now

    def __call__(self) -> float:
        return float(self.now)


class FakeClient:
    def __init__(self, friends: list[Friend] | None = None) -> None:
        self.data = friends or []

    async def friends(self) -> list[Friend]:
        return list(self.data)


def make_friend(
    *,
    name: str = "Alice",
    game: str | None = "Splatoon 3",
    state: str = "ONLINE",
    friend_id: int = 1001,
    updated_at: int = START,
    total_minutes: int = 120,
) -> Friend:
    return Friend(
        id=friend_id,
        name=name,
        nsa_id=f"nsa-{friend_id}",
        state=state,
        updated_at=updated_at,
        logout_at=updated_at,
        game=Game(name=game, total_minutes=total_minutes) if game else None,
    )


class MonitorTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = JsonStore(Path(self._tmp.name) / "state.json")
        await self.store.load()
        self.clock = Clock()
        self.monitor = FriendMonitor(self.store, clock=self.clock)
        self.client = FakeClient()

    async def test_first_poll_seeds_without_notifications(self) -> None:
        self.client.data = [make_friend()]
        result = await self.monitor.poll(self.client)
        self.assertTrue(result.seeded)
        self.assertEqual(result.changes, [])
        state = self.store.friend_state(1001)
        self.assertIsNotNone(state)
        self.assertEqual(state["game"], "Splatoon 3")
        self.assertEqual(state["game_since"], START)

    async def test_same_game_does_not_repeat(self) -> None:
        self.client.data = [make_friend()]
        await self.monitor.poll(self.client)
        self.clock.now += 300
        self.client.data = [make_friend(updated_at=self.clock.now)]
        result = await self.monitor.poll(self.client)
        self.assertEqual(result.changes, [])
        self.assertEqual(self.store.friend_state(1001)["game_since"], START)

    async def test_game_switch_is_reported_and_recorded(self) -> None:
        self.client.data = [make_friend()]
        await self.monitor.poll(self.client)
        self.clock.now += 3600
        self.client.data = [make_friend(game="Animal Crossing", updated_at=self.clock.now)]
        result = await self.monitor.poll(self.client)

        self.assertEqual(len(result.changes), 1)
        change = result.changes[0]
        self.assertEqual(change.kind, "changed")
        self.assertEqual(change.previous_game, "Splatoon 3")
        self.assertEqual(change.duration_minutes, 60)

        sessions = self.store.sessions_for(1001)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["game"], "Splatoon 3")
        self.assertEqual(sessions[0]["start"], START)
        self.assertEqual(self.store.friend_state(1001)["game"], "Animal Crossing")

    async def test_stop_is_recorded_and_optional_notification(self) -> None:
        self.client.data = [make_friend()]
        await self.monitor.poll(self.client)
        self.clock.now += 1800
        self.client.data = [make_friend(game=None, state="OFFLINE", updated_at=self.clock.now)]
        result = await self.monitor.poll(self.client)

        self.assertEqual(len(result.changes), 1)
        self.assertEqual(result.changes[0].kind, "stopped")
        self.assertEqual(result.changes[0].duration_minutes, 30)
        self.assertFalse(self.store.friend_state(1001)["playing"])
        self.assertEqual(self.store.sessions_for(1001)[0]["end"], self.clock.now)

    async def test_restart_continues_state_without_spam(self) -> None:
        self.client.data = [make_friend()]
        await self.monitor.poll(self.client)

        restarted = JsonStore(self.store.path)
        await restarted.load()
        monitor = FriendMonitor(restarted, clock=self.clock)
        self.clock.now += 120
        self.client.data = [make_friend(updated_at=self.clock.now)]
        result = await monitor.poll(self.client)

        self.assertFalse(result.seeded)
        self.assertEqual(result.changes, [])
        self.assertEqual(restarted.friend_state(1001)["game_since"], START)

    async def test_new_friend_after_seed_is_reported(self) -> None:
        self.client.data = [make_friend()]
        await self.monitor.poll(self.client)
        self.clock.now += 60
        self.client.data = [
            make_friend(),
            make_friend(name="Bob", friend_id=2002, updated_at=self.clock.now),
        ]
        result = await self.monitor.poll(self.client)

        self.assertEqual([change.kind for change in result.changes], ["started"])
        self.assertEqual(result.changes[0].friend.name, "Bob")

    async def test_offline_friend_idle_state_is_not_a_change(self) -> None:
        self.client.data = [make_friend(game=None, state="OFFLINE")]
        await self.monitor.poll(self.client)
        self.clock.now += 60
        self.client.data = [make_friend(game=None, state="OFFLINE", updated_at=self.clock.now)]
        result = await self.monitor.poll(self.client)
        self.assertEqual(result.changes, [])


if __name__ == "__main__":
    unittest.main()
