"""用 Pillow 在本地渲染游戏卡片。

AstrBot 的 T2I 可能配置成只支持 Markdown 的本地服务（如 t2i-local），
此时 HTML 模板渲染会失败。这里提供一个纯本地兜底：不依赖网络、不依赖
T2I，直接用 Pillow + 系统 Noto CJK 字体画一张 PNG。
"""

from __future__ import annotations

import io
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Iterable

from .models import (
    Friend,
    format_clock,
    format_date,
    format_duration,
    format_relative,
    total_minutes,
    week_ago,
)

WIDTH = 760
PAD = 28
RADIUS = 24

_BOLD_FONTS = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKSC-Bold.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)
_REGULAR_FONTS = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKSC-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


@lru_cache(maxsize=64)
def _font(size: int, bold: bool = False):
    from PIL import ImageFont

    for path in (_BOLD_FONTS if bold else _REGULAR_FONTS):
        for index in (2, 0):
            try:
                return ImageFont.truetype(path, size, index=index)
            except Exception:  # noqa: BLE001 - 字体/索引不适用就换下一个
                continue
    return ImageFont.load_default()


def _gradient(width: int, height: int):
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (width, height), (11, 13, 19))
    draw = ImageDraw.Draw(image)
    top = (29, 36, 54)
    bottom = (12, 15, 23)
    for y in range(height):
        t = y / max(1, height - 1)
        color = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        draw.line((0, y, width, y), fill=color)
    return image


def _rounded_image(data: bytes, size: tuple[int, int], radius: int):
    from PIL import Image, ImageDraw, ImageOps

    with Image.open(io.BytesIO(data)) as source:
        image = ImageOps.fit(source.convert("RGB"), size, Image.LANCZOS)
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0], size[1]), radius=radius, fill=255)
    out = Image.new("RGBA", size, (0, 0, 0, 0))
    out.paste(image, (0, 0), mask)
    return out


def _circle_image(data: bytes, size: int):
    from PIL import Image, ImageDraw, ImageOps

    with Image.open(io.BytesIO(data)) as source:
        image = ImageOps.fit(source.convert("RGB"), (size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(image, (0, 0), mask)
    return out


def _ellipsis(draw, text: str, font, max_width: int) -> str:
    if draw.textlength(text, font=font) <= max_width:
        return text
    while text and draw.textlength(text + "…", font=font) > max_width:
        text = text[:-1]
    return text + "…"


def _wrap(draw, text: str, font, max_width: int, max_lines: int = 2) -> list[str]:
    lines: list[str] = []
    current = ""
    for char in text:
        if draw.textlength(current + char, font=font) <= max_width:
            current += char
            continue
        if current:
            lines.append(current)
        current = char
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines:
        consumed = "".join(lines)
        if len(consumed) < len(text):
            lines[-1] = _ellipsis(draw, lines[-1] + "…", font, max_width)
    return lines or [""]


def _status(friend: Friend) -> tuple[str, tuple[int, int, int], tuple[int, int, int]]:
    if friend.playing:
        return "正在游玩", (47, 212, 122), (8, 23, 14)
    if friend.online:
        return "在线", (42, 78, 130), (207, 227, 255)
    return "离线", (48, 54, 68), (168, 176, 194)


def render_card_png(
    friend: Friend,
    *,
    title: str,
    tz: timezone,
    avatar_bytes: bytes | None = None,
    cover_bytes: bytes | None = None,
    now: float | None = None,
    started_at: int = 0,
    sessions: Iterable[dict[str, Any]] | None = None,
) -> bytes | None:
    """渲染卡片；缺少 Pillow 或字体时返回 None，由调用方降级为文本。"""

    try:
        from PIL import Image, ImageDraw, ImageOps
    except Exception:  # noqa: BLE001 - 没有 Pillow 就降级
        return None

    now = datetime.now(tz).timestamp() if now is None else now
    sessions = list(sessions) if sessions is not None else []
    game = friend.game if friend.playing else None
    game_title = game.name if game else "当前未在游玩"

    measure = ImageDraw.Draw(Image.new("RGB", (10, 10), (0, 0, 0)))
    game_lines = _wrap(measure, game_title, _font(26, True), 640, 2)

    friend_top = 104
    friend_height = 90
    game_top = friend_top + friend_height + 18
    cover_box = 320
    game_height = 20 + cover_box + 18 + len(game_lines) * 34 + 18 + 64 + 16
    local_height = 78 if sessions else 0
    height = game_top + game_height + (16 + local_height if sessions else 0) + 56

    image = _gradient(WIDTH, height).convert("RGBA")
    glow = Image.new("RGBA", (WIDTH, height), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((WIDTH - 260, -220, WIDTH + 220, 260), fill=(230, 0, 18, 44))
    image = Image.alpha_composite(image, glow)
    draw = ImageDraw.Draw(image)

    white = (238, 241, 247)
    muted = (141, 151, 174)
    dim = (97, 107, 130)

    # 顶栏
    draw.ellipse((PAD, 27, PAD + 8, 35), fill=(230, 0, 18))
    draw.text((PAD + 16, 24), "NINTENDO SWITCH ONLINE", font=_font(13, True), fill=(154, 164, 189))
    stamp = datetime.fromtimestamp(int(now), tz).strftime("%Y-%m-%d %H:%M")
    draw.text((WIDTH - PAD, 24), stamp, font=_font(13), fill=dim, anchor="ra")

    # 标题
    draw.text((PAD, 52), title, font=_font(30, True), fill=white)

    # 好友区域
    draw.rounded_rectangle(
        (PAD, friend_top, WIDTH - PAD, friend_top + friend_height),
        radius=18,
        fill=(30, 36, 52, 235),
        outline=(48, 56, 76),
    )
    avatar_size = 58
    avatar_x, avatar_y = PAD + 18, friend_top + 16
    avatar_ok = False
    if avatar_bytes:
        try:
            image.alpha_composite(_circle_image(avatar_bytes, avatar_size), (avatar_x, avatar_y))
            avatar_ok = True
        except Exception:  # noqa: BLE001
            avatar_ok = False
    if not avatar_ok:
        draw.ellipse(
            (avatar_x, avatar_y, avatar_x + avatar_size, avatar_y + avatar_size),
            fill=(42, 49, 68),
        )
        draw.text(
            (avatar_x + avatar_size / 2, avatar_y + avatar_size / 2 - 1),
            (friend.name or "?")[:1],
            font=_font(26, True),
            fill=(210, 218, 235),
            anchor="mm",
        )

    name_x = avatar_x + avatar_size + 18
    draw.text(
        (name_x, friend_top + 18),
        _ellipsis(draw, friend.name, _font(23, True), 360),
        font=_font(23, True),
        fill=white,
    )
    meta = ["好友"]
    if friend.is_favorite:
        meta.append("特别好友")
    if friend.playing:
        meta.append(f"更新于 {format_relative(friend.updated_at, tz, now)}")
    elif friend.online:
        meta.append("在线")
    else:
        meta.append(f"最后在线 {format_relative(friend.logout_at, tz, now)}")
    draw.text((name_x, friend_top + 52), " · ".join(meta), font=_font(14), fill=muted)

    badge_text, badge_bg, badge_fg = _status(friend)
    badge_font = _font(14, True)
    badge_width = int(draw.textlength(badge_text, font=badge_font)) + 26
    badge_x2 = WIDTH - PAD - 18
    badge_x1 = badge_x2 - badge_width
    draw.rounded_rectangle(
        (badge_x1, friend_top + 27, badge_x2, friend_top + 27 + 34),
        radius=17,
        fill=badge_bg,
    )
    draw.text(
        ((badge_x1 + badge_x2) / 2, friend_top + 43),
        badge_text,
        font=badge_font,
        fill=badge_fg,
        anchor="mm",
    )

    # 游戏区域：封面居中完整显示，游戏名与统计在封面下方
    draw.rounded_rectangle(
        (PAD, game_top, WIDTH - PAD, game_top + game_height),
        radius=20,
        fill=(14, 18, 28, 240),
        outline=(36, 42, 58),
    )

    box_x = (WIDTH - cover_box) // 2
    box_y = game_top + 20
    draw.rounded_rectangle(
        (box_x, box_y, box_x + cover_box, box_y + cover_box),
        radius=20,
        fill=(20, 25, 38),
        outline=(40, 48, 66),
    )

    cover_ok = False
    if cover_bytes:
        try:
            with Image.open(io.BytesIO(cover_bytes)) as source:
                contained = ImageOps.contain(
                    source.convert("RGB"),
                    (cover_box - 2, cover_box - 2),
                    Image.LANCZOS,
                )
            mask = Image.new("L", contained.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle(
                (0, 0, contained.width, contained.height),
                radius=16,
                fill=255,
            )
            rounded = Image.new("RGBA", contained.size, (0, 0, 0, 0))
            rounded.paste(contained, (0, 0), mask)
            px = box_x + (cover_box - contained.width) // 2
            py = box_y + (cover_box - contained.height) // 2
            image.alpha_composite(rounded, (px, py))
            cover_ok = True
        except Exception:  # noqa: BLE001
            cover_ok = False
    if not cover_ok:
        draw.text(
            (WIDTH / 2, box_y + cover_box / 2),
            "Nintendo Switch",
            font=_font(18, True),
            fill=(120, 132, 160),
            anchor="mm",
        )

    text_y = box_y + cover_box + 18
    for line in game_lines:
        draw.text((WIDTH / 2, text_y), line, font=_font(26, True), fill=white, anchor="ma")
        text_y += 34

    if game:
        rows = [
            ("累计游玩", format_duration(game.total_minutes)),
            ("首次游玩", format_date(game.first_played_at, tz) if game.first_played_at else "未知"),
        ]
        if started_at:
            rows.append(("本次开始", format_clock(started_at, tz)))
        column_width = 640 / max(1, len(rows))
        start_x = (WIDTH - 640) / 2
        stat_y = text_y + 12
        for index, (label, value) in enumerate(rows):
            center_x = start_x + column_width * (index + 0.5)
            draw.text((center_x, stat_y), label, font=_font(13), fill=muted, anchor="ma")
            draw.text((center_x, stat_y + 26), value, font=_font(17, True), fill=white, anchor="ma")
    else:
        detail = (
            "好友在线，但没有公开正在游玩的游戏"
            if friend.online
            else "最后在线：" + (format_date(friend.logout_at, tz) if friend.logout_at else "未知")
        )
        draw.text((WIDTH / 2, text_y + 8), detail, font=_font(15), fill=muted, anchor="ma")

    # 本地记录
    if sessions:
        local_top = game_top + game_height + 16
        week = [item for item in sessions if float(item.get("start") or 0) >= week_ago()]
        summary = (
            f"本插件记录：累计 {len(sessions)} 次 · 近 7 天 {len(week)} 次 / "
            f"{format_duration(total_minutes(week))}"
        )
        recent = sorted(sessions, key=lambda item: float(item.get("end") or 0), reverse=True)[0]
        try:
            start = float(recent.get("start") or 0)
            end = float(recent.get("end") or 0)
            minutes = max(1, int((end - start) // 60))
            recent_text = (
                f"最近：{format_clock(start, tz)}–{format_clock(end, tz)}"
                f"《{recent.get('game') or '未知游戏'}》{format_duration(minutes)}"
            )
        except Exception:  # noqa: BLE001
            recent_text = ""
        draw.rounded_rectangle(
            (PAD, local_top, WIDTH - PAD, local_top + local_height),
            radius=14,
            fill=(22, 34, 58, 235),
            outline=(44, 66, 110),
        )
        draw.text((PAD + 16, local_top + 12), summary, font=_font(13), fill=(169, 185, 216))
        if recent_text:
            draw.text((PAD + 16, local_top + 40), recent_text, font=_font(13), fill=(169, 185, 216))

    # 页脚
    draw.text((PAD, height - 32), "Nintendo Switch Online API · AstrBot", font=_font(12), fill=dim)
    draw.text((WIDTH - PAD, height - 32), f"FRD-{friend.id}", font=_font(12), fill=dim, anchor="ra")

    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, "PNG", optimize=True)
    return buffer.getvalue()
