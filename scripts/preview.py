"""生成一张离线卡片预览（不依赖 AstrBot T2I）。

用法：
    python3 scripts/preview.py
输出：
    docs/preview.html
"""

from __future__ import annotations

import base64
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from nso.models import Friend, Game  # noqa: E402
from nso.render import build_card_html  # noqa: E402

COVER_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="512" height="288">
<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="#ff5c8a"/><stop offset="0.55" stop-color="#a03bff"/>
<stop offset="1" stop-color="#2b6cff"/></linearGradient></defs>
<rect width="512" height="288" fill="url(#g)"/>
<circle cx="420" cy="40" r="130" fill="rgba(255,255,255,0.10)"/>
<circle cx="60" cy="270" r="90" fill="rgba(0,0,0,0.12)"/>
<text x="34" y="150" font-family="Arial, sans-serif" font-size="50" font-weight="700"
      fill="#ffffff" textLength="330" lengthAdjust="spacingAndGlyphs">SPLATOON 3</text>
<text x="36" y="192" font-family="Arial, sans-serif" font-size="20"
      fill="rgba(255,255,255,0.85)">Nintendo Switch</text>
</svg>"""

AVATAR_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128">
<defs><linearGradient id="a" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="#59e0b5"/><stop offset="1" stop-color="#2f7cf6"/>
</linearGradient></defs>
<rect width="128" height="128" fill="url(#a)"/>
<text x="64" y="86" text-anchor="middle" font-family="Arial, sans-serif"
      font-size="58" font-weight="700" fill="#ffffff">M</text>
</svg>"""


def data_uri(svg: str) -> str:
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")


def main() -> None:
    tz = ZoneInfo("Asia/Shanghai")
    now = int(time.time())
    friend = Friend(
        id=6433111839604736,
        name="Mika",
        state="ONLINE",
        is_favorite=True,
        updated_at=now - 60,
        game=Game(
            name="Splatoon 3",
            image_url="https://example.com/cover.png",
            shop_url="https://ec.nintendo.com/apps/0100c2500fc20000/JP?lang=zh",
            total_minutes=12115,
            first_played_at=1_662_700_000,
        ),
    )
    sessions = [
        {"game": "Splatoon 3", "start": now - 7200, "end": now - 3600},
        {"game": "Splatoon 3", "start": now - 86400, "end": now - 82800},
        {"game": "Animal Crossing", "start": now - 3 * 86400, "end": now - 3 * 86400 + 5400},
    ]
    document = build_card_html(
        friend,
        title="好友开始游玩",
        tz=tz,
        avatar_uri=data_uri(AVATAR_SVG),
        cover_uri=data_uri(COVER_SVG),
        started_at=int(friend.updated_at),
        sessions=sessions,
        footer="预览数据 · 实际推送由 AstrBot T2I 渲染",
    )
    out = ROOT / "docs" / "preview.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(document, encoding="utf-8")
    print(f"written: {out}")


if __name__ == "__main__":
    main()
