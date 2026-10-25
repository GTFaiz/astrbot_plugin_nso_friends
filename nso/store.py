"""插件持久化。

所有数据写进 AstrBot 规范的 ``data/plugin_data/<plugin>/nso_state.json``，
包含：session_token、账号信息、分群订阅、好友状态快照、本地游玩记录。
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import time
from pathlib import Path
from typing import Any

SESSION_LIMIT_PER_FRIEND = 300
SESSION_KEEP_DAYS = 90

_DEFAULT_DATA: dict[str, Any] = {
    "version": 1,
    "session_token": "",
    "account": {},
    "subscriptions": {},
    "friend_states": {},
    "sessions": {},
    "last_poll_at": 0,
    "last_success_at": 0,
}


class JsonStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.data: dict[str, Any] = copy.deepcopy(_DEFAULT_DATA)
        self._save_lock = asyncio.Lock()

    # ------------------------------------------------------------ 读写文件
    async def load(self) -> None:
        if not self.path.exists():
            await self.save()
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            backup = self.path.with_suffix(f".corrupt-{int(time.time())}")
            try:
                self.path.replace(backup)
            except OSError:
                pass
            self.data = copy.deepcopy(_DEFAULT_DATA)
            await self.save()
            return
        if not isinstance(raw, dict):
            raw = {}
        data = copy.deepcopy(_DEFAULT_DATA)
        data.update(raw)
        for key, value in raw.items():
            if key in data and value is None:
                data[key] = copy.deepcopy(_DEFAULT_DATA.get(key))
        self.data = data

    async def save(self) -> None:
        async with self._save_lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            payload = json.dumps(self.data, ensure_ascii=False, indent=2)
            await asyncio.to_thread(self._write_atomic, tmp, payload)

    @staticmethod
    def _write_atomic(tmp: Path, payload: str) -> None:
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        target = tmp.with_name(tmp.name[: -len(".tmp")])
        os.replace(tmp, target)
        try:
            # session_token 等同账号凭证，尽量只让当前用户可读
            os.chmod(target, 0o600)
        except OSError:
            pass

    # ------------------------------------------------------------ 账号状态
    @property
    def session_token(self) -> str:
        return str(self.data.get("session_token") or "")

    async def set_session_token(self, token: str) -> None:
        self.data["session_token"] = token or ""
        await self.save()

    @property
    def account(self) -> dict[str, Any]:
        account = self.data.get("account")
        return dict(account) if isinstance(account, dict) else {}

    async def set_account(self, account: dict[str, Any]) -> None:
        self.data["account"] = dict(account or {})
        await self.save()

    async def clear_session(self) -> None:
        self.data["session_token"] = ""
        self.data["account"] = {}
        await self.save()

    # -------------------------------------------------------------- 订阅
    def subscriptions(self) -> dict[str, dict[str, Any]]:
        subscriptions = self.data.get("subscriptions")
        if not isinstance(subscriptions, dict):
            return {}
        return {str(key): dict(value) for key, value in subscriptions.items() if isinstance(value, dict)}

    def subscription(self, umo: str) -> dict[str, Any] | None:
        return self.subscriptions().get(str(umo))

    async def set_subscription(self, umo: str, friends: list[str] | None = None) -> dict[str, Any]:
        umo = str(umo)
        current = self.subscription(umo) or {}
        record = {
            "friends": list(friends if friends is not None else current.get("friends") or []),
            "enabled": True,
            "created_at": current.get("created_at") or int(time.time()),
            "updated_at": int(time.time()),
        }
        self.data.setdefault("subscriptions", {})[umo] = record
        await self.save()
        return record

    async def remove_subscription(self, umo: str) -> bool:
        subscriptions = self.data.setdefault("subscriptions", {})
        removed = subscriptions.pop(str(umo), None) is not None
        if removed:
            await self.save()
        return removed

    # ---------------------------------------------------------- 好友快照
    def friend_state(self, friend_id: int | str) -> dict[str, Any] | None:
        states = self.data.get("friend_states")
        if not isinstance(states, dict):
            return None
        state = states.get(str(friend_id))
        return dict(state) if isinstance(state, dict) else None

    def friend_states(self) -> dict[str, dict[str, Any]]:
        states = self.data.get("friend_states")
        if not isinstance(states, dict):
            return {}
        return {str(key): dict(value) for key, value in states.items() if isinstance(value, dict)}

    def set_friend_state(self, friend_id: int | str, state: dict[str, Any]) -> None:
        self.data.setdefault("friend_states", {})[str(friend_id)] = dict(state)

    # ---------------------------------------------------------- 游玩记录
    def sessions_for(self, friend_id: int | str) -> list[dict[str, Any]]:
        sessions = self.data.get("sessions")
        if not isinstance(sessions, dict):
            return []
        items = sessions.get(str(friend_id))
        if not isinstance(items, list):
            return []
        return [dict(item) for item in items if isinstance(item, dict)]

    async def add_session(
        self,
        friend_id: int | str,
        game: str,
        start: int | float,
        end: int | float,
        *,
        friend_name: str = "",
        save: bool = True,
    ) -> None:
        if not game or end <= start:
            return
        sessions = self.data.setdefault("sessions", {})
        bucket = sessions.setdefault(str(friend_id), [])
        if not isinstance(bucket, list):
            bucket = []
            sessions[str(friend_id)] = bucket
        bucket.append(
            {
                "game": game,
                "start": int(start),
                "end": int(end),
                "name": friend_name,
            }
        )
        cutoff = time.time() - SESSION_KEEP_DAYS * 86400
        bucket[:] = [item for item in bucket if float(item.get("start") or 0) >= cutoff]
        if len(bucket) > SESSION_LIMIT_PER_FRIEND:
            del bucket[: len(bucket) - SESSION_LIMIT_PER_FRIEND]
        if save:
            await self.save()

    # ---------------------------------------------------------- 轮询时间戳
    def mark_poll(self, *, success: bool) -> None:
        now = int(time.time())
        self.data["last_poll_at"] = now
        if success:
            self.data["last_success_at"] = now

    @property
    def last_poll_at(self) -> int:
        return int(self.data.get("last_poll_at") or 0)

    @property
    def last_success_at(self) -> int:
        return int(self.data.get("last_success_at") or 0)

    def reset_friend_states(self) -> None:
        self.data["friend_states"] = {}
