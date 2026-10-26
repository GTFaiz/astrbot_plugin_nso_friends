"""数据模型与展示格式化工具。

本模块刻意不依赖 AstrBot，便于脱离框架做单元测试。
字段命名与 NSO-API（https://github.com/sodiumts/NSO-API/wiki）保持一致。
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_str(value: Any, default: str = "") -> str:
    if isinstance(value, str):
        return value.strip()
    return default


@dataclass(slots=True)
class Game:
    """presence.game 字段，见 /v4/Friend/List。"""

    name: str
    image_url: str = ""
    shop_url: str = ""
    total_minutes: int = 0
    first_played_at: int = 0
    sys_description: str = ""

    @classmethod
    def from_presence(cls, raw: Any) -> "Game | None":
        if not isinstance(raw, dict):
            return None
        name = _as_str(raw.get("name"))
        if not name:
            return None
        return cls(
            name=name,
            image_url=_as_str(raw.get("imageUri")),
            shop_url=_as_str(raw.get("shopUri")),
            total_minutes=_as_int(raw.get("totalPlayTime")),
            first_played_at=_as_int(raw.get("firstPlayedAt")),
            sys_description=_as_str(raw.get("sysDescription")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "image_url": self.image_url,
            "shop_url": self.shop_url,
            "total_minutes": self.total_minutes,
            "first_played_at": self.first_played_at,
        }


@dataclass(slots=True)
class Friend:
    """好友条目，见 /v4/Friend/List 的 result.friends[]。"""

    id: int
    name: str
    nsa_id: str = ""
    image_url: str = ""
    is_favorite: bool = False
    state: str = "OFFLINE"
    logout_at: int = 0
    updated_at: int = 0
    game: Game | None = None

    @classmethod
    def from_api(cls, raw: Any) -> "Friend | None":
        if not isinstance(raw, dict):
            return None
        friend_id = _as_int(raw.get("id"))
        if not friend_id:
            return None
        presence = raw.get("presence") if isinstance(raw.get("presence"), dict) else {}
        game = Game.from_presence(presence.get("game"))
        state = _as_str(presence.get("state")).upper()
        if not state:
            state = "ONLINE" if game is not None else "OFFLINE"
        return cls(
            id=friend_id,
            name=_as_str(raw.get("name"), f"好友 {friend_id}"),
            nsa_id=_as_str(raw.get("nsaId")),
            image_url=_as_str(raw.get("imageUri")),
            is_favorite=bool(raw.get("isFavoriteFriend")),
            state=state,
            logout_at=_as_int(presence.get("logoutAt")),
            updated_at=_as_int(presence.get("updatedAt")),
            game=game,
        )

    @property
    def playing(self) -> bool:
        """只要 presence.game 里有游戏名，就视为"正在游玩"。

        NSO 在 play 状态下 state 通常仍是 ONLINE，因此不能只看 state。
        """

        return bool(self.game and self.game.name) and self.state != "OFFLINE"

    @property
    def online(self) -> bool:
        return self.state != "OFFLINE"

    def status_label(self) -> str:
        if self.playing:
            return "正在游玩"
        if self.online:
            return "在线"
        return "离线"


# --------------------------------------------------------------------------
# 格式化
# --------------------------------------------------------------------------


def format_duration(minutes: int) -> str:
    """把分钟数格式化成中文时长。0/负数表示官方未提供。"""

    if minutes is None or minutes <= 0:
        return "未知"
    hours, mins = divmod(int(minutes), 60)
    if hours and mins:
        return f"{hours} 小时 {mins} 分钟"
    if hours:
        return f"{hours} 小时"
    return f"{mins} 分钟"


def format_ts(ts: int | float, tz: timezone, fmt: str = "%Y-%m-%d %H:%M") -> str:
    if not ts:
        return "未知"
    try:
        return datetime.fromtimestamp(int(ts), tz).strftime(fmt)
    except (OverflowError, OSError, ValueError):
        return "未知"


def format_date(ts: int | float, tz: timezone) -> str:
    return format_ts(ts, tz, "%Y-%m-%d")


def format_relative(ts: int | float, tz: timezone, now: float | None = None) -> str:
    """相对时间，例如"3 分钟前"。"""

    if not ts:
        return "未知"
    now = datetime.now(tz).timestamp() if now is None else now
    try:
        delta = int(now) - int(ts)
    except (TypeError, ValueError):
        return "未知"
    if delta < 0:
        delta = 0
    if delta < 60:
        return "刚刚"
    if delta < 3600:
        return f"{delta // 60} 分钟前"
    if delta < 86400:
        return f"{delta // 3600} 小时前"
    if delta < 86400 * 7:
        return f"{delta // 86400} 天前"
    return format_date(ts, tz)


def format_clock(ts: int | float, tz: timezone, now: float | None = None) -> str:
    """今天显示"今天 HH:MM"，否则显示日期。"""

    if not ts:
        return "未知"
    now_dt = datetime.now(tz) if now is None else datetime.fromtimestamp(int(now), tz)
    target = datetime.fromtimestamp(int(ts), tz)
    if now_dt.date() == target.date():
        return target.strftime("今天 %H:%M")
    return target.strftime("%m-%d %H:%M")


def split_name_filter(raw: str) -> list[str]:
    """把 "A,B；C" 这类输入拆成名字列表。"""

    normalized = raw.replace("，", ",").replace("；", ",").replace(";", ",")
    return [part.strip() for part in normalized.split(",") if part.strip()]


def select_friends(friends: Iterable[Friend], query: str) -> list[Friend]:
    """按 好友名 / nsaId / 好友 ID 查找，支持大小写不敏感的子串匹配。"""

    query = (query or "").strip()
    if not query:
        return []
    friends = list(friends)
    lowered = query.casefold()
    exact = [
        friend
        for friend in friends
        if friend.name.casefold() == lowered
        or friend.nsa_id.casefold() == lowered
        or str(friend.id) == query
    ]
    if exact:
        return exact
    contains = [friend for friend in friends if lowered in friend.name.casefold()]
    if contains:
        return contains
    names = [friend.name for friend in friends]
    close = difflib.get_close_matches(query, names, n=3, cutoff=0.5)
    return [friend for friend in friends if friend.name in close]


def subscription_matches(subscription: dict[str, Any], friend_name: str) -> bool:
    """订阅里的好友过滤为空 = 监控全部好友。"""

    friends = subscription.get("friends") or []
    if not friends:
        return True
    lowered = friend_name.casefold()
    return any(str(name).casefold() in lowered or lowered in str(name).casefold() for name in friends)


def sessions_between(sessions: Iterable[dict[str, Any]], since: float) -> list[dict[str, Any]]:
    return [session for session in sessions if float(session.get("start") or 0) >= since]


def total_minutes(sessions: Iterable[dict[str, Any]]) -> int:
    minutes = 0.0
    for session in sessions:
        try:
            minutes += max(0.0, float(session.get("end") or 0) - float(session.get("start") or 0))
        except (TypeError, ValueError):
            continue
    return int(minutes // 60)


def week_ago() -> float:
    return (datetime.now(timezone.utc) - timedelta(days=7)).timestamp()
