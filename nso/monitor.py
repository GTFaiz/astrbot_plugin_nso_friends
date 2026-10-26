"""轮询与状态差分。

每次轮询调用 /v4/Friend/List，与上一次快照比较，产出：
- started：好友从"未游玩"变成"正在玩某游戏"
- changed：好友切换了游戏
- stopped：好友结束游玩（可选推送）

同时用本地快照把"游玩区间"落库，弥补 NSO 只暴露当前游戏累计时长的限制。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from .models import Friend
from .store import JsonStore


@dataclass(slots=True)
class Change:
    kind: str  # started / changed / stopped
    friend: Friend
    previous_game: str = ""
    duration_minutes: int = 0


@dataclass(slots=True)
class PollResult:
    friends: list[Friend] = field(default_factory=list)
    changes: list[Change] = field(default_factory=list)
    seeded: bool = False
    polled_at: float = 0.0


class FriendMonitor:
    def __init__(self, store: JsonStore, *, clock: Callable[[], float] = time.time) -> None:
        self._store = store
        self._clock = clock

    async def poll(self, client) -> PollResult:
        friends = await client.friends()
        now = int(self._clock())
        seeded = not self._store.friend_states()
        changes: list[Change] = []

        for friend in friends:
            previous = self._store.friend_state(friend.id)
            previous_playing = bool(previous and previous.get("playing"))
            previous_game = str((previous or {}).get("game") or "")
            game = friend.game

            if friend.playing and game is not None:
                if previous_playing and previous_game and previous_game != game.name:
                    start = int(previous.get("game_since") or previous.get("updated_at") or now)
                    await self._store.add_session(
                        friend.id,
                        previous_game,
                        start,
                        now,
                        friend_name=friend.name,
                        save=False,
                    )
                    changes.append(
                        Change(
                            kind="changed",
                            friend=friend,
                            previous_game=previous_game,
                            duration_minutes=max(0, (now - start) // 60),
                        )
                    )
                elif previous is not None and not previous_playing:
                    changes.append(Change(kind="started", friend=friend))
                elif previous is None and not seeded:
                    # 首次运行不刷屏；之后新出现的好友若在游玩则正常播报
                    changes.append(Change(kind="started", friend=friend))

                if previous_playing and previous_game == game.name:
                    game_since = int(previous.get("game_since") or now)
                else:
                    game_since = now
                self._store.set_friend_state(
                    friend.id,
                    {
                        "name": friend.name,
                        "state": friend.state,
                        "playing": True,
                        "game": game.name,
                        "game_since": game_since,
                        "updated_at": friend.updated_at or now,
                        "seen_at": now,
                    },
                )
            else:
                if previous_playing:
                    start = int(previous.get("game_since") or now)
                    await self._store.add_session(
                        friend.id,
                        previous_game or "未知游戏",
                        start,
                        now,
                        friend_name=friend.name,
                        save=False,
                    )
                    changes.append(
                        Change(
                            kind="stopped",
                            friend=friend,
                            previous_game=previous_game,
                            duration_minutes=max(0, (now - start) // 60),
                        )
                    )
                self._store.set_friend_state(
                    friend.id,
                    {
                        "name": friend.name,
                        "state": friend.state,
                        "playing": False,
                        "game": "",
                        "game_since": 0,
                        "updated_at": friend.updated_at or now,
                        "seen_at": now,
                    },
                )

        self._store.mark_poll(success=True)
        await self._store.save()
        return PollResult(friends=friends, changes=changes, seeded=seeded, polled_at=time.time())
