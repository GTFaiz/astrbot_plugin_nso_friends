from __future__ import annotations

import importlib.util
import io
import time
import unittest
from datetime import timezone

from nso.models import Friend, Game
from nso.pil_render import render_card_png

HAS_PIL = importlib.util.find_spec("PIL") is not None


@unittest.skipUnless(HAS_PIL, "Pillow 未安装，跳过本地渲染测试")
class PilRenderTest(unittest.TestCase):
    def _images(self) -> tuple[bytes, bytes]:
        from PIL import Image

        avatar = Image.new("RGB", (128, 128), (60, 120, 200))
        cover = Image.new("RGB", (512, 288), (200, 60, 120))
        avatar_bytes = io.BytesIO()
        cover_bytes = io.BytesIO()
        avatar.save(avatar_bytes, "PNG")
        cover.save(cover_bytes, "PNG")
        return avatar_bytes.getvalue(), cover_bytes.getvalue()

    def test_render_playing_card(self) -> None:
        avatar, cover = self._images()
        now = int(time.time())
        friend = Friend(
            id=6433111839604736,
            name="测试好友",
            state="ONLINE",
            is_favorite=True,
            updated_at=now - 60,
            game=Game(
                name="Splatoon 3",
                total_minutes=12115,
                first_played_at=1_662_700_000,
            ),
        )
        sessions = [{"game": "Splatoon 3", "start": now - 7200, "end": now - 3600}]
        png = render_card_png(
            friend,
            title="好友开始游玩",
            tz=timezone.utc,
            avatar_bytes=avatar,
            cover_bytes=cover,
            now=now,
            started_at=now - 3600,
            sessions=sessions,
        )
        self.assertIsNotNone(png)
        assert png is not None
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertGreater(len(png), 5000)

    def test_render_without_images_or_sessions(self) -> None:
        friend = Friend(id=1, name="Offline", state="OFFLINE", logout_at=1_700_000_000)
        png = render_card_png(friend, title="好友游戏详情", tz=timezone.utc)
        self.assertIsNotNone(png)
        assert png is not None
        self.assertTrue(png.startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()
