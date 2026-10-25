from __future__ import annotations

import unittest
from datetime import timezone

from nso.models import (
    Friend,
    format_clock,
    format_duration,
    format_relative,
    select_friends,
    split_name_filter,
    subscription_matches,
    total_minutes,
)

from tests.fake import friend_payload


class ModelTest(unittest.TestCase):
    def test_friend_from_api(self) -> None:
        friend = Friend.from_api(friend_payload("Alice", game="Splatoon 3", total_play_time=12115))
        self.assertIsNotNone(friend)
        assert friend is not None
        self.assertEqual(friend.name, "Alice")
        self.assertTrue(friend.playing)
        self.assertEqual(friend.status_label(), "正在游玩")
        self.assertEqual(friend.game.total_minutes, 12115)
        self.assertEqual(friend.game.shop_url, "https://ec.nintendo.com/apps/0100000000000000/JP?lang=zh")

    def test_friend_offline_has_no_game(self) -> None:
        friend = Friend.from_api(friend_payload("Bob", state="OFFLINE"))
        assert friend is not None
        self.assertFalse(friend.playing)
        self.assertFalse(friend.online)
        self.assertEqual(friend.status_label(), "离线")

    def test_friend_state_playing_is_not_offline(self) -> None:
        friend = Friend.from_api(friend_payload("Carol", state="PLAYING", game="Zelda"))
        assert friend is not None
        self.assertTrue(friend.playing)
        self.assertTrue(friend.online)

    def test_invalid_payload_is_ignored(self) -> None:
        self.assertIsNone(Friend.from_api({}))
        self.assertIsNone(Friend.from_api("nope"))

    def test_format_duration(self) -> None:
        self.assertEqual(format_duration(0), "未知")
        self.assertEqual(format_duration(-5), "未知")
        self.assertEqual(format_duration(45), "45 分钟")
        self.assertEqual(format_duration(60), "1 小时")
        self.assertEqual(format_duration(125), "2 小时 5 分钟")

    def test_format_relative(self) -> None:
        tz = timezone.utc
        now = 1_700_000_000
        self.assertEqual(format_relative(now - 10, tz, now), "刚刚")
        self.assertEqual(format_relative(now - 180, tz, now), "3 分钟前")
        self.assertEqual(format_relative(now - 7200, tz, now), "2 小时前")
        self.assertEqual(format_relative(0, tz, now), "未知")

    def test_format_clock(self) -> None:
        tz = timezone.utc
        self.assertIn("今天", format_clock(1_700_000_000, tz, now=1_700_000_000))

    def test_select_friends(self) -> None:
        friends = [
            Friend(id=1, name="Alice"),
            Friend(id=2, name="Alicia"),
            Friend(id=3, name="Bob", nsa_id="nsa-bob"),
        ]
        self.assertEqual([friend.id for friend in select_friends(friends, "alice")], [1])
        self.assertEqual([friend.id for friend in select_friends(friends, "ali")], [1, 2])
        self.assertEqual([friend.id for friend in select_friends(friends, "nsa-bob")], [3])
        self.assertEqual(select_friends(friends, "Zzz"), [])

    def test_split_name_filter(self) -> None:
        self.assertEqual(split_name_filter("Alice， Bob;Carol"), ["Alice", "Bob", "Carol"])
        self.assertEqual(split_name_filter(""), [])

    def test_subscription_matches(self) -> None:
        self.assertTrue(subscription_matches({}, "Anyone"))
        self.assertTrue(subscription_matches({"friends": ["Ali"]}, "Alice"))
        self.assertFalse(subscription_matches({"friends": ["Bob"]}, "Alice"))

    def test_total_minutes(self) -> None:
        sessions = [
            {"start": 100, "end": 100 + 3600},
            {"start": 200, "end": 200 + 1800},
        ]
        self.assertEqual(total_minutes(sessions), 90)


if __name__ == "__main__":
    unittest.main()
