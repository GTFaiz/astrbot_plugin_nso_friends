"""游戏动态卡片（HTML）与纯文本降级方案。"""

from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Any, Iterable

from .models import (
    Friend,
    format_clock,
    format_date,
    format_duration,
    format_relative,
    format_ts,
    total_minutes,
    week_ago,
)


def _escape(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _status_meta(friend: Friend, tz: timezone) -> tuple[str, str]:
    """返回 (徽章文案, 徽章样式)；同时生成"最后在线"提示。"""

    if friend.playing:
        return "正在游玩", "play"
    if friend.online:
        return "在线", "online"
    return "离线", "offline"


def _recent_lines(sessions: Iterable[dict[str, Any]], tz: timezone, limit: int = 3) -> list[str]:
    ordered = sorted(sessions, key=lambda item: float(item.get("end") or 0), reverse=True)
    lines: list[str] = []
    for session in ordered[:limit]:
        try:
            start = float(session.get("start") or 0)
            end = float(session.get("end") or 0)
            minutes = max(0, int((end - start) // 60))
        except (TypeError, ValueError):
            continue
        game = str(session.get("game") or "未知游戏")
        lines.append(
            f"{format_clock(start, tz)} – {format_clock(end, tz)} · 《{game}》 · {format_duration(minutes)}"
        )
    return lines


def build_card_html(
    friend: Friend,
    *,
    title: str,
    tz: timezone,
    avatar_uri: str = "",
    cover_uri: str = "",
    now: float | None = None,
    started_at: int = 0,
    sessions: Iterable[dict[str, Any]] | None = None,
    footer: str = "数据来源：Nintendo Switch Online API · 由 AstrBot 渲染",
) -> str:
    now = datetime.now(tz).timestamp() if now is None else now
    badge_text, badge_class = _status_meta(friend, tz)
    game = friend.game

    if avatar_uri:
        avatar_html = f'<img class="avatar" src="{_escape(avatar_uri)}" alt="avatar">'
    else:
        avatar_html = '<div class="avatar avatar-ph">🎮</div>'

    if friend.playing and game is not None:
        if cover_uri:
            cover_html = f'<img class="cover" src="{_escape(cover_uri)}" alt="cover">'
        else:
            cover_html = '<div class="cover-ph">🎮</div>'
        rows = [
            ("累计游玩", format_duration(game.total_minutes)),
            ("首次游玩", format_date(game.first_played_at, tz) if game.first_played_at else "未知"),
        ]
        if started_at:
            rows.append(("本次开始", format_clock(started_at, tz)))
        rows_html = "".join(
            f'<div class="row"><span>{_escape(label)}</span><b>{_escape(value)}</b></div>'
            for label, value in rows
        )
        game_html = f"""
      <div class="game">
        {cover_html}
        <div class="ginfo">
          <div class="gname">{_escape(game.name)}</div>
          {rows_html}
        </div>
      </div>"""
    else:
        if friend.online:
            detail = "好友在线，但当前没有公开正在游玩的游戏"
        else:
            detail = f"最后在线：{format_ts(friend.logout_at, tz) if friend.logout_at else '未知'}"
        game_html = f"""
      <div class="game">
        <div class="cover-ph">🌙</div>
        <div class="ginfo">
          <div class="gname">当前未在游玩</div>
          <div class="row"><span>状态</span><b>{_escape(badge_text)}</b></div>
          <div class="row"><span>说明</span><b>{_escape(detail)}</b></div>
        </div>
      </div>"""

    meta_bits = ["好友"]
    if friend.is_favorite:
        meta_bits.append("特别好友")
    if friend.playing:
        meta_bits.append(f"更新于 {format_relative(friend.updated_at, tz, now)}")
    elif friend.online:
        meta_bits.append("在线")
    else:
        meta_bits.append(f"最后在线 {format_relative(friend.logout_at, tz, now)}")

    local_lines: list[str] = []
    if sessions is not None:
        sessions = list(sessions)
        week_sessions = [item for item in sessions if float(item.get("start") or 0) >= week_ago()]
        local_lines.append(
            f"本插件记录：累计 {len(sessions)} 次 · 近 7 天 {len(week_sessions)} 次 / "
            f"{format_duration(total_minutes(week_sessions))}"
        )
        local_lines.extend(_recent_lines(sessions, tz, limit=3))
    local_html = ""
    if local_lines:
        local_html = '<div class="local">' + "<br>".join(_escape(line) for line in local_lines) + "</div>"

    time_text = format_ts(now, tz)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body {{ background: #0b0d13; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Noto Sans CJK SC",
                 "Source Han Sans SC", "Microsoft YaHei", sans-serif;
    color: #eef1f7; padding: 26px;
  }}
  .card {{
    width: 760px; position: relative; overflow: hidden; border-radius: 26px;
    background: linear-gradient(155deg, #1d2436 0%, #111521 55%, #0d1019 100%);
    border: 1px solid rgba(255,255,255,.07);
    box-shadow: 0 24px 64px rgba(0,0,0,.55);
  }}
  .glow {{
    position: absolute; top: -150px; right: -110px; width: 380px; height: 380px; border-radius: 50%;
    background: radial-gradient(circle, rgba(230,0,18,.30), rgba(230,0,18,0) 65%);
  }}
  .top {{ position: relative; display: flex; justify-content: space-between; align-items: center; padding: 22px 28px 0; }}
  .brand {{ display: flex; align-items: center; gap: 10px; font-size: 13px; letter-spacing: 2px; color: #9aa4bd; text-transform: uppercase; }}
  .dot {{ width: 8px; height: 8px; border-radius: 50%; background: #e60012; box-shadow: 0 0 12px #e60012; }}
  .time {{ font-size: 13px; color: #7d879f; }}
  .headline {{ position: relative; padding: 16px 28px 0; font-size: 27px; font-weight: 700; letter-spacing: .5px; }}
  .friend {{
    position: relative; display: flex; align-items: center; gap: 14px; margin: 20px 28px 0;
    padding: 16px 18px; border-radius: 18px;
    background: rgba(255,255,255,.045); border: 1px solid rgba(255,255,255,.06);
  }}
  .avatar {{ width: 58px; height: 58px; border-radius: 50%; object-fit: cover; background: #2a3145; border: 2px solid rgba(255,255,255,.12); flex-shrink: 0; }}
  .avatar-ph {{ display: flex; align-items: center; justify-content: center; font-size: 26px; }}
  .who {{ flex: 1; min-width: 0; }}
  .name {{ font-size: 21px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .sub {{ margin-top: 4px; font-size: 13px; color: #8d97ae; }}
  .badge {{ padding: 6px 13px; border-radius: 999px; font-size: 13px; font-weight: 700; white-space: nowrap; }}
  .badge.play {{ color: #08170e; background: linear-gradient(135deg, #7bf1a8, #2fd47a); }}
  .badge.online {{ color: #cfe3ff; background: rgba(80,140,255,.20); border: 1px solid rgba(120,170,255,.35); }}
  .badge.offline {{ color: #a8b0c2; background: rgba(255,255,255,.07); border: 1px solid rgba(255,255,255,.10); }}
  .game {{
    position: relative; display: flex; gap: 20px; margin: 18px 28px 0; padding: 18px; border-radius: 20px;
    background: rgba(0,0,0,.28); border: 1px solid rgba(255,255,255,.05);
  }}
  .cover {{ width: 250px; height: 141px; border-radius: 14px; object-fit: cover; background: #202637; flex-shrink: 0; display: block; }}
  .cover-ph {{ width: 250px; height: 141px; border-radius: 14px; flex-shrink: 0; display: flex; align-items: center; justify-content: center; font-size: 42px; background: linear-gradient(135deg, #2b3348, #1a2030); }}
  .ginfo {{ flex: 1; min-width: 0; display: flex; flex-direction: column; justify-content: center; }}
  .gname {{ font-size: 23px; font-weight: 700; line-height: 1.3; margin-bottom: 10px; overflow-wrap: anywhere; }}
  .row {{ display: flex; justify-content: space-between; gap: 12px; font-size: 14px; padding: 5px 0; color: #9aa4bd; border-bottom: 1px dashed rgba(255,255,255,.06); }}
  .row:last-child {{ border-bottom: none; }}
  .row b {{ color: #e8edf7; font-weight: 600; text-align: right; }}
  .local {{ margin: 16px 28px 0; padding: 13px 16px; border-radius: 14px; font-size: 13px; line-height: 1.7; color: #a9b9d8; background: rgba(90,130,220,.12); border: 1px solid rgba(90,130,220,.18); }}
  .foot {{ display: flex; justify-content: space-between; padding: 16px 28px 20px; font-size: 12px; color: #616b82; }}
</style>
</head>
<body>
  <div class="card">
    <div class="glow"></div>
    <div class="top">
      <div class="brand"><span class="dot"></span>Nintendo Switch Online</div>
      <div class="time">{_escape(time_text)}</div>
    </div>
    <div class="headline">{_escape(title)}</div>
    <div class="friend">
      {avatar_html}
      <div class="who">
        <div class="name">{_escape(friend.name)}</div>
        <div class="sub">{_escape(" · ".join(meta_bits))}</div>
      </div>
      <div class="badge {badge_class}">{_escape(badge_text)}</div>
    </div>
    {game_html}
    {local_html}
    <div class="foot"><span>{_escape(footer)}</span><span>FRD-{friend.id}</span></div>
  </div>
</body>
</html>"""


def build_card_text(
    friend: Friend,
    *,
    tz: timezone,
    now: float | None = None,
    title: str = "",
    started_at: int = 0,
) -> str:
    """渲染失败或未开启卡片时使用的纯文本版本。"""

    now = datetime.now(tz).timestamp() if now is None else now
    lines: list[str] = []
    if title:
        lines.append(title)
    if friend.playing and friend.game is not None:
        game = friend.game
        lines.append(f"🎮 {friend.name} 正在游玩《{game.name}》")
        lines.append(
            f"累计 {format_duration(game.total_minutes)} · 首次 "
            f"{format_date(game.first_played_at, tz) if game.first_played_at else '未知'}"
        )
        if started_at:
            lines.append(f"本次开始：{format_clock(started_at, tz)}")
        if game.shop_url:
            lines.append(game.shop_url)
    elif friend.online:
        lines.append(f"🟢 {friend.name} 在线，当前未公开正在游玩的游戏")
    else:
        lines.append(
            f"⚫ {friend.name} 离线（最后在线 {format_ts(friend.logout_at, tz) if friend.logout_at else '未知'}）"
        )
    lines.append(f"状态更新：{format_relative(friend.updated_at, tz, now)}")
    return "\n".join(lines)


def build_stopped_text(friend: Friend, *, game: str, minutes: int, tz: timezone) -> str:
    return (
        f"🎮 {friend.name} 结束了《{game}》\n"
        f"本次游玩约 {format_duration(minutes)}（本地观测，仅供参考）"
    )
