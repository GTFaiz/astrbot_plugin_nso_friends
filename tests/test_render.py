from __future__ import annotations

import unittest
from datetime import timezone

from nso.models import Friend, Game
from nso.render import build_card_html, build_card_text, build_stopped_text

PNG_URI = "data:image/png;base64,iVBORw0KGgo="


class RenderTest(unittest.TestCase):
    def _friend(self, *, name: str = "Alice", game: str = "Splatoon 3") -> Friend:
        return Friend(
            id=1001,
            name=name,
            state="ONLINE",
            updated_at=1_700_000_000,
            logout_at=1_699_999_000,
            game=Game(
                name=game,
                image_url="https://example.com/cover.png",
                shop_url="https://ec.nintendo.com/apps/0100/JP",
                total_minutes=12115,
                first_played_at=1_600_000_000,
            ),
        )

    def test_card_contains_game_and_escapes_html(self) -> None:
        friend = self._friend(name='<img src=x onerror="alert(1)">', game="A & B <script>")
        html = build_card_html(
            friend,
            title="好友开始游玩",
            tz=timezone.utc,
            avatar_uri=PNG_URI,
            cover_uri=PNG_URI,
            started_at=1_700_000_000,
            sessions=[{"game": "Splatoon 3", "start": 1_699_990_000, "end": 1_699_993_600}],
        )
        self.assertIn("&lt;img src=x onerror=&quot;alert(1)&quot;&gt;", html)
        self.assertNotIn("<script>", html)
        self.assertIn("A &amp; B &lt;script&gt;", html)
        self.assertIn("好友开始游玩", html)
        self.assertIn(PNG_URI, html)
        self.assertIn("201 小时 55 分钟", html)
        self.assertIn("本插件记录", html)

    def test_card_without_images_uses_placeholders(self) -> None:
        html = build_card_html(self._friend(), title="好友游戏详情", tz=timezone.utc)
        self.assertIn("cover-ph", html)
        self.assertIn("avatar-ph", html)

    def test_idle_card(self) -> None:
        friend = Friend(id=1, name="Bob", state="OFFLINE", logout_at=1_699_000_000)
        html = build_card_html(friend, title="好友游戏详情", tz=timezone.utc)
        self.assertIn("当前未在游玩", html)
        self.assertIn("离线", html)

    def test_text_fallback(self) -> None:
        text = build_card_text(self._friend(), tz=timezone.utc, title="🎮 好友开始游玩")
        self.assertIn("《Splatoon 3》", text)
        self.assertIn("201 小时 55 分钟", text)
        self.assertIn("https://ec.nintendo.com/apps/0100/JP", text)

    def test_stopped_text(self) -> None:
        text = build_stopped_text(self._friend(), game="Splatoon 3", minutes=95, tz=timezone.utc)
        self.assertIn("结束了《Splatoon 3》", text)
        self.assertIn("1 小时 35 分钟", text)


if __name__ == "__main__":
    unittest.main()
