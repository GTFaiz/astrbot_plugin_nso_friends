from __future__ import annotations

import asyncio
import json
import random
import re
import time
from datetime import timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .nso.auth import (
    LoginChallenge,
    NintendoAuth,
    NsoAuthRequired,
    NsoError,
    create_login_challenge,
)
from .nso.client import NsoClient
from .nso.http import HttpxTransport
from .nso.images import ImageCache
from .nso.models import (
    Friend,
    format_clock,
    format_date,
    format_duration,
    format_relative,
    format_ts,
    select_friends,
    sessions_between,
    split_name_filter,
    subscription_matches,
    total_minutes,
)
from .nso.monitor import Change, FriendMonitor, PollResult
from .nso.render import build_card_html, build_card_text, build_stopped_text
from .nso.store import JsonStore

PLUGIN_NAME = "astrbot_plugin_nso_friends"
_COMMAND_PREFIX = re.compile(r"^\s*[/／!！]?\s*(?:nso|任天堂)\s*", re.IGNORECASE)

_SUBCOMMANDS: dict[str, str] = {
    "login": "login",
    "登录": "login",
    "登陆": "login",
    "logout": "logout",
    "登出": "logout",
    "注销": "logout",
    "friends": "friends",
    "好友": "friends",
    "列表": "friends",
    "query": "query",
    "查询": "query",
    "查": "query",
    "history": "history",
    "记录": "history",
    "战绩": "history",
    "sub": "sub",
    "订阅": "sub",
    "subscribe": "sub",
    "unsub": "unsub",
    "退订": "unsub",
    "unsubscribe": "unsub",
    "subs": "subs",
    "订阅列表": "subs",
    "status": "status",
    "状态": "status",
    "check": "check",
    "检查": "check",
    "刷新": "check",
    "me": "me",
    "我": "me",
    "help": "help",
    "帮助": "help",
    "": "help",
}

_HELP_TEXT = """🎮 任天堂好友监控 · NSO Friends

/nso 登录 — 获取 Nintendo 账号授权链接（管理员）
/nso 登录 <回调链接> — 粘贴回调链接完成绑定（管理员）
/nso 好友 — 好友在线 / 游玩概览
/nso 查询 <好友> — 好友当前游戏详情卡片
/nso 记录 <好友> — 官方累计时长 + 本插件观测到的游玩记录
/nso 订阅 [好友,…] — 本会话开启动态推送（可只监控指定好友）
/nso 退订 — 关闭本会话推送
/nso 订阅列表 — 查看本会话订阅状态
/nso 状态 — 登录 / 轮询 / 推送状态
/nso 检查 — 立即拉取一次好友状态（管理员）
/nso 我 — 当前绑定的账号

提示：检测到好友开始游玩或切换游戏时，会把游戏名、封面、累计时长渲染成卡片推送。"""


def _command_args(event: AstrMessageEvent) -> str:
    raw = ""
    getter = getattr(event, "get_extra", None)
    if callable(getter):
        try:
            raw = str(getter("astrbot_original_message_str") or "")
        except Exception:  # noqa: BLE001 - 兼容不同版本
            raw = ""
    raw = raw or str(getattr(event, "message_str", "") or "")
    return _COMMAND_PREFIX.sub("", raw, count=1).strip()


async def _empty() -> str:
    return ""


def _make_chain(components: list[Any]) -> Any:
    from astrbot.api.event import MessageChain  # 延迟导入，避免影响单测

    return MessageChain(components)


@register(
    PLUGIN_NAME,
    "GTFaiz",
    "轮询任天堂好友在线/游玩状态，渲染游戏卡片推送到群，并支持查询好友游玩记录",
    "1.0.0",
)
class NsoFriendsPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None) -> None:
        super().__init__(context)
        self.context = context
        self.config: Any = config or {}
        self.data_dir = Path(get_astrbot_data_path()) / "plugin_data" / PLUGIN_NAME
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._cache_dir = self.data_dir / "images"
        self._tz = self._load_timezone()
        self.store = JsonStore(self.data_dir / "nso_state.json")
        self._transport = HttpxTransport(timeout=self._cfg_float("request_timeout", 30.0))
        self._auth = NintendoAuth(
            self._transport,
            app_version=self._cfg_str("app_version", "2.11.0"),
            f_api_url=self._cfg_str("f_api_url", "https://api.imink.app/f"),
            f_api_headers=self._f_api_headers(),
            on_session_token=self._on_session_token,
            on_account=self._on_account,
        )
        self._client = NsoClient(self._auth)
        self._monitor = FriendMonitor(self.store)
        self._images = ImageCache(self._cache_dir, self._transport)
        self._poll_task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._poll_lock = asyncio.Lock()
        self._login_challenges: dict[str, tuple[LoginChallenge, float]] = {}
        self._friends_cache: tuple[float, list[Friend]] | None = None
        self._last_poll_at: float = 0.0
        self._last_poll_ok: bool = True
        self._last_error: str = ""
        self._failures: int = 0
        self._last_auth_warning: float = 0.0
        self._loaded = False

    # ------------------------------------------------------------ 生命周期
    async def initialize(self) -> None:
        await self.store.load()
        self._loaded = True
        self._auth.set_session_token(self.store.session_token)
        await self._ensure_default_subscriptions()
        self._poll_task = asyncio.create_task(self._poll_loop(), name=f"{PLUGIN_NAME}-poll")
        account = self.store.account
        if self.store.session_token:
            logger.info(
                "[NSO] 已加载 Nintendo 登录态（账号：%s），轮询间隔 %s 秒",
                account.get("nickname") or "未知",
                self._poll_interval(),
            )
        else:
            logger.info("[NSO] 尚未登录，发送 /nso 登录 绑定 Nintendo 账号")

    async def terminate(self) -> None:
        self._stop.set()
        task = self._poll_task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._poll_task = None
        if self._loaded:
            try:
                await self.store.save()
            except Exception:  # noqa: BLE001
                logger.warning("[NSO] 退出时保存数据失败", exc_info=True)
        await self._transport.aclose()

    # -------------------------------------------------------------- 配置
    def _cfg(self, key: str, default: Any = None) -> Any:
        try:
            value = self.config.get(key, default)
        except Exception:  # noqa: BLE001
            value = getattr(self.config, key, default)
        return default if value is None or value == "" else value

    def _cfg_bool(self, key: str, default: bool) -> bool:
        value = self._cfg(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        return str(value).strip().lower() in {"1", "true", "yes", "on", "是", "开启"}

    def _cfg_int(self, key: str, default: int) -> int:
        try:
            return int(self._cfg(key, default))
        except (TypeError, ValueError):
            return default

    def _cfg_float(self, key: str, default: float) -> float:
        try:
            return float(self._cfg(key, default))
        except (TypeError, ValueError):
            return default

    def _cfg_str(self, key: str, default: str) -> str:
        return str(self._cfg(key, default)).strip()

    def _f_api_headers(self) -> dict[str, str]:
        raw = self._cfg("f_api_headers", {})
        if isinstance(raw, dict):
            return {str(key): str(value) for key, value in raw.items()}
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
            except ValueError:
                logger.warning("[NSO] f_api_headers 不是合法 JSON，已忽略")
                return {}
            if isinstance(parsed, dict):
                return {str(key): str(value) for key, value in parsed.items()}
        return {}

    def _poll_interval(self) -> int:
        return max(60, min(3600, self._cfg_int("poll_interval", 300)))

    def _load_timezone(self):
        name = self._cfg_str("timezone", "Asia/Shanghai")
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            logger.warning("[NSO] 时区 %s 无效，回退 Asia/Shanghai", name)
        try:
            return ZoneInfo("Asia/Shanghai")
        except Exception:  # noqa: BLE001 - 系统缺少 tzdata 时退回 UTC
            logger.warning("[NSO] 系统缺少时区数据库，使用 UTC")
            return timezone.utc

    async def _ensure_default_subscriptions(self) -> None:
        raw = self._cfg("default_subscriptions", [])
        if isinstance(raw, str):
            raw = split_name_filter(raw)
        if not isinstance(raw, (list, tuple)):
            return
        for item in raw:
            umo = str(item).strip()
            if not umo:
                continue
            if self.store.subscription(umo) is None:
                await self.store.set_subscription(umo)

    # ------------------------------------------------------------ 鉴权回调
    async def _on_session_token(self, token: str) -> None:
        await self.store.set_session_token(token)

    async def _on_account(self, account: dict[str, Any]) -> None:
        await self.store.set_account(account)

    # -------------------------------------------------------------- 轮询
    async def _poll_loop(self) -> None:
        await asyncio.sleep(5)
        while not self._stop.is_set():
            interval = self._poll_interval()
            if not self.store.session_token:
                delay = 120
            elif not self._cfg_bool("enable_push", True):
                delay = 300
            else:
                try:
                    await self._poll_once(push=True)
                    self._last_poll_ok = True
                    self._last_error = ""
                    if self._failures:
                        logger.info("[NSO] 轮询恢复正常（此前连续失败 %s 次）", self._failures)
                    self._failures = 0
                    delay = interval
                except NsoAuthRequired as exc:
                    self._failures += 1
                    self._last_poll_ok = False
                    self._last_error = str(exc)
                    await self._warn_auth(str(exc))
                    delay = min(interval * (2 ** min(self._failures, 3)), 1800)
                except Exception as exc:  # noqa: BLE001 - 后台任务不能死
                    self._failures += 1
                    self._last_poll_ok = False
                    self._last_error = f"{type(exc).__name__}: {exc}"
                    logger.warning("[NSO] 轮询失败（连续 %s 次）：%s", self._failures, exc)
                    delay = min(interval * (2 ** min(self._failures, 3)), 1800)
            delay = max(30.0, delay) * (0.95 + random.random() * 0.1)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                continue

    async def _poll_once(self, *, push: bool = True) -> PollResult:
        async with self._poll_lock:
            result = await self._monitor.poll(self._client)
            self._friends_cache = (time.time(), result.friends)
            self._last_poll_at = time.time()
            self._last_poll_ok = True
        if push and not result.seeded and result.changes:
            await self._dispatch(result.changes)
        return result

    async def _dispatch(self, changes: list[Change]) -> None:
        if not self._cfg_bool("enable_push", True):
            return
        max_push = max(1, min(20, self._cfg_int("max_push_per_cycle", 6)))
        notify_start = self._cfg_bool("notify_game_start", True)
        notify_change = self._cfg_bool("notify_game_change", True)
        notify_offline = self._cfg_bool("notify_game_stop", False)

        subscriptions = {
            umo: sub
            for umo, sub in self.store.subscriptions().items()
            if sub.get("enabled", True)
        }
        if not subscriptions:
            return

        skipped = 0
        pushed = 0
        for change in changes:
            if change.kind == "started" and not notify_start:
                continue
            if change.kind == "changed" and not notify_change:
                continue
            if change.kind == "stopped" and not notify_offline:
                continue
            recipients = [
                umo
                for umo, sub in subscriptions.items()
                if subscription_matches(sub, change.friend.name)
            ]
            if not recipients:
                continue
            if pushed >= max_push:
                skipped += 1
                continue
            try:
                chain = await self._build_change_chain(change)
            except Exception:  # noqa: BLE001
                logger.warning("[NSO] 构建推送消息失败：%s", change.friend.name, exc_info=True)
                continue
            pushed += 1
            for umo in recipients:
                await self._send(umo, chain)
                await asyncio.sleep(0.3)
        if skipped:
            logger.warning("[NSO] 单轮动态过多，已跳过 %s 条推送（可调大 max_push_per_cycle）", skipped)

    async def _build_change_chain(self, change: Change) -> Any:
        from astrbot.api.event import MessageChain  # 延迟导入，避免影响单测

        friend = change.friend
        if change.kind == "stopped":
            return MessageChain(
                [Comp.Plain(build_stopped_text(friend, game=change.previous_game, minutes=change.duration_minutes, tz=self._tz))]
            )
        if change.kind == "changed":
            title = "好友切换了游戏"
        else:
            title = "好友开始游玩"
        game_since = 0
        state = self.store.friend_state(friend.id)
        if state:
            game_since = int(state.get("game_since") or 0)
        image = await self._render_card(friend, title=title, started_at=game_since)
        if image:
            return MessageChain([Comp.Image.fromBytes(image)])
        components: list[Any] = [Comp.Plain(build_card_text(friend, tz=self._tz, title=f"🎮 {title}"))]
        if friend.game and friend.game.image_url:
            try:
                components.append(Comp.Image.fromURL(friend.game.image_url))
            except Exception:  # noqa: BLE001
                pass
        return MessageChain(components)

    async def _render_card(
        self,
        friend: Friend,
        *,
        title: str,
        started_at: int = 0,
        sessions: list[dict[str, Any]] | None = None,
    ) -> bytes | None:
        if not self._cfg_bool("render_card", True):
            return None
        avatar_uri, cover_uri = await asyncio.gather(
            self._images.data_uri(friend.image_url),
            self._images.data_uri(friend.game.image_url) if friend.game else _empty(),
        )
        document = build_card_html(
            friend,
            title=title,
            tz=self._tz,
            avatar_uri=avatar_uri,
            cover_uri=cover_uri,
            started_at=started_at,
            sessions=sessions,
        )
        timeout = max(30.0, min(240.0, self._cfg_float("render_timeout", 90.0)))
        try:
            data = await asyncio.wait_for(
                self.html_render(
                    document,
                    {},
                    return_url=False,
                    options={"type": "png", "full_page": True, "device_scale_factor_level": "ultra"},
                ),
                timeout=timeout,
            )
        except Exception as exc:  # noqa: BLE001 - 渲染失败要降级
            logger.warning("[NSO] 卡片渲染失败，降级为文本：%s", exc)
            return None
        return await self._coerce_image(data)

    async def _coerce_image(self, data: Any) -> bytes | None:
        if isinstance(data, (bytes, bytearray)):
            return bytes(data)
        if isinstance(data, str):
            if data.startswith(("http://", "https://")):
                try:
                    response = await self._transport.request("GET", data)
                    if response.status_code == 200 and response.content:
                        return response.content
                except Exception:  # noqa: BLE001
                    return None
            else:
                path = Path(data)
                try:
                    if path.is_file():
                        return path.read_bytes()
                except OSError:
                    return None
        return None

    # ---------------------------------------------------------------- 发送
    async def _send(self, umo: str, chain: Any) -> bool:
        try:
            sent = await self.context.send_message(umo, chain)
        except Exception:  # noqa: BLE001
            logger.warning("[NSO] 推送到 %s 失败", umo, exc_info=True)
            return False
        if not sent:
            logger.warning("[NSO] 推送到 %s 未找到匹配的会话", umo)
            return False
        return True

    async def _warn_auth(self, detail: str) -> None:
        now = time.time()
        if now - self._last_auth_warning < 12 * 3600:
            return
        self._last_auth_warning = now
        subscriptions = [umo for umo, sub in self.store.subscriptions().items() if sub.get("enabled", True)]
        if not subscriptions:
            return
        text = f"⚠️ Nintendo 登录已失效，动态推送暂停。\n请管理员执行 /nso 登录 重新绑定。\n原因：{detail}"
        chain = _make_chain([Comp.Plain(text)])
        for umo in subscriptions[:20]:
            await self._send(umo, chain)

    # -------------------------------------------------------------- 好友数据
    async def _fetch_friends(self, *, force: bool = False) -> list[Friend]:
        now = time.time()
        if not force and self._friends_cache and now - self._friends_cache[0] < 30:
            return self._friends_cache[1]
        friends = await self._client.friends()
        self._friends_cache = (now, friends)
        return friends

    def _cached_friend_states(self) -> list[Friend]:
        friends: list[Friend] = []
        for fid, state in self.store.friend_states().items():
            try:
                friend_id = int(fid)
            except (TypeError, ValueError):
                continue
            friends.append(
                Friend(
                    id=friend_id,
                    name=str(state.get("name") or f"好友 {fid}"),
                    state=str(state.get("state") or "OFFLINE"),
                    updated_at=int(state.get("updated_at") or 0),
                )
            )
        return friends

    # ---------------------------------------------------------------- 指令
    @filter.command("nso", alias={"任天堂"})
    async def nso(self, event: AstrMessageEvent):
        args = _command_args(event)
        if " " in args:
            head, tail = args.split(" ", 1)
        else:
            head, tail = args, ""
        sub = _SUBCOMMANDS.get(head.casefold(), "")
        if not sub:
            # 允许 /nso 张三 直接查询
            tail = args
            sub = "query"
        arg = tail.strip()
        try:
            components = await self._run_subcommand(event, sub, arg)
        except NsoAuthRequired as exc:
            components = [Comp.Plain(f"🔐 {exc}")]
        except NsoError as exc:
            logger.warning("[NSO] 指令失败：%s", exc)
            components = [Comp.Plain(f"❌ {exc}")]
        except Exception as exc:  # noqa: BLE001
            logger.exception("[NSO] 指令执行异常")
            components = [Comp.Plain(f"❌ 执行失败：{type(exc).__name__}: {exc}")]
        if components:
            yield event.chain_result(components)

    async def _run_subcommand(self, event: AstrMessageEvent, sub: str, arg: str) -> list[Any]:
        handlers = {
            "help": self._cmd_help,
            "login": self._cmd_login,
            "logout": self._cmd_logout,
            "friends": self._cmd_friends,
            "query": self._cmd_query,
            "history": self._cmd_history,
            "sub": self._cmd_sub,
            "unsub": self._cmd_unsub,
            "subs": self._cmd_subs,
            "status": self._cmd_status,
            "check": self._cmd_check,
            "me": self._cmd_me,
        }
        handler = handlers.get(sub, self._cmd_help)
        return await handler(event, arg)

    # ------------------------------------------------------------ 各子命令
    async def _cmd_help(self, event: AstrMessageEvent, arg: str) -> list[Any]:
        return [Comp.Plain(_HELP_TEXT)]

    async def _cmd_login(self, event: AstrMessageEvent, arg: str) -> list[Any]:
        if not self._is_admin(event):
            return [Comp.Plain("🔐 只有管理员可以绑定 / 解绑 Nintendo 账号。")]
        umo = self._umo(event)
        looks_like_link = bool(arg) and (
            "session_token_code" in arg
            or arg.startswith("npf")
            or "://auth" in arg
            or (len(arg) > 100 and " " not in arg)
        )
        if looks_like_link:
            record = self._login_challenges.pop(umo, None)
            if record is None:
                return [Comp.Plain("没有待完成的登录会话。请先发送 /nso 登录 获取授权链接。")]
            challenge, created_at = record
            if time.time() - created_at > 900:
                return [Comp.Plain("登录会话已超过 15 分钟，请重新发送 /nso 登录 获取新链接。")]
            account = await self._auth.login_with_redirect(arg, challenge)
            await self.store.set_subscription(umo)
            self._friends_cache = None
            nickname = account.get("nickname") or "Nintendo 账号"
            friend_code = account.get("friend_code") or "未知"
            return [
                Comp.Plain(
                    f"✅ 绑定成功：{nickname}（好友码 {friend_code}）\n"
                    "已自动开启本会话的动态推送。发送 /nso 好友 查看好友列表。"
                )
            ]

        challenge = create_login_challenge()
        self._login_challenges[umo] = (challenge, time.time())
        return [
            Comp.Plain(
                "🔗 请在已登录 Nintendo 账号的浏览器中打开下面的链接：\n"
                f"{challenge.url}\n\n"
                "登录后点击「选择此人」（Select this person），\n"
                "在按钮上右键 →「复制链接地址」，把复制的 npf...://auth#... 链接\n"
                "用 /nso 登录 <链接> 发回来即可。\n"
                "（链接 15 分钟内有效；session_token 绑定后约 2 年有效）"
            )
        ]

    async def _cmd_logout(self, event: AstrMessageEvent, arg: str) -> list[Any]:
        if not self._is_admin(event):
            return [Comp.Plain("🔐 只有管理员可以解绑 Nintendo 账号。")]
        await self.store.clear_session()
        self.store.reset_friend_states()
        await self.store.save()
        self._auth.set_session_token("")
        self._friends_cache = None
        return [Comp.Plain("✅ 已清除本地 Nintendo 登录态与好友状态快照。")]

    async def _cmd_friends(self, event: AstrMessageEvent, arg: str) -> list[Any]:
        friends = await self._fetch_friends()
        if not friends:
            return [Comp.Plain("好友列表为空。请确认 Nintendo 账号已添加好友。")]
        playing = [friend for friend in friends if friend.playing]
        online = [friend for friend in friends if friend.online and not friend.playing]
        offline = [friend for friend in friends if not friend.online]
        offline.sort(key=lambda friend: friend.logout_at, reverse=True)
        ordered = playing + online + offline
        limit = max(5, min(50, self._cfg_int("friend_list_limit", 25)))
        lines = [f"👥 好友 {len(friends)} 位 · 在线 {len(playing) + len(online)} · 游玩 {len(playing)}"]
        for friend in ordered[:limit]:
            if friend.playing and friend.game:
                lines.append(f"🟢 {friend.name} — 正在游玩《{friend.game.name}》")
                lines.append(f"    累计 {format_duration(friend.game.total_minutes)}")
            elif friend.online:
                lines.append(f"🔵 {friend.name} — 在线")
            else:
                last_seen = format_relative(friend.logout_at, self._tz) if friend.logout_at else "未知"
                lines.append(f"⚫ {friend.name} — 最后在线 {last_seen}")
        if len(ordered) > limit:
            lines.append(f"… 仅显示前 {limit} 位（共 {len(ordered)} 位）")
        lines.append("\n用 /nso 查询 <好友名> 查看详情卡片。")
        return [Comp.Plain("\n".join(lines))]

    async def _cmd_query(self, event: AstrMessageEvent, arg: str) -> list[Any]:
        query = arg or ""
        if not query:
            return [Comp.Plain("请指定好友名，例如：/nso 查询 张三")]
        friends = await self._fetch_friends()
        matches = select_friends(friends, query)
        if not matches:
            return [Comp.Plain(f"未找到匹配「{query}」的好友。发送 /nso 好友 查看好友列表。")]
        if len(matches) > 1:
            names = "\n".join(f"· {friend.name}" for friend in matches[:10])
            return [Comp.Plain(f"找到多位匹配的好友，请指定更完整的名字：\n{names}")]
        friend = matches[0]
        state = self.store.friend_state(friend.id) or {}
        started_at = int(state.get("game_since") or 0) if friend.playing else 0
        sessions = self.store.sessions_for(friend.id)
        image = await self._render_card(
            friend,
            title="好友游戏详情",
            started_at=started_at,
            sessions=sessions,
        )
        if image:
            components: list[Any] = [Comp.Image.fromBytes(image)]
            if friend.game and friend.game.shop_url:
                components.append(Comp.Plain(f"《{friend.game.name}》商店页：{friend.game.shop_url}"))
            return components
        text = build_card_text(friend, tz=self._tz, title="🎮 好友游戏详情", started_at=started_at)
        components = [Comp.Plain(text)]
        if friend.game and friend.game.image_url:
            try:
                components.append(Comp.Image.fromURL(friend.game.image_url))
            except Exception:  # noqa: BLE001
                pass
        return components

    async def _cmd_history(self, event: AstrMessageEvent, arg: str) -> list[Any]:
        query = arg or ""
        if not query:
            return [Comp.Plain("请指定好友名，例如：/nso 记录 张三")]
        try:
            friends = await self._fetch_friends()
            source = "live"
        except NsoError as exc:
            friends = self._cached_friend_states()
            source = "cache"
            if not friends:
                raise exc
        matches = select_friends(friends, query)
        if not matches:
            return [Comp.Plain(f"未找到匹配「{query}」的好友。")]
        if len(matches) > 1:
            names = "\n".join(f"· {friend.name}" for friend in matches[:10])
            return [Comp.Plain(f"找到多位匹配的好友，请指定更完整的名字：\n{names}")]
        friend = matches[0]
        state = self.store.friend_state(friend.id) or {}
        sessions = sorted(
            self.store.sessions_for(friend.id),
            key=lambda item: float(item.get("end") or 0),
            reverse=True,
        )
        now = time.time()
        week = sessions_between(sessions, now - 7 * 86400)
        month = sessions_between(sessions, now - 30 * 86400)

        lines = [f"🎮 {friend.name} 的游玩记录"]
        if friend.playing and friend.game:
            game = friend.game
            lines.append(f"当前游戏：《{game.name}》")
            lines.append(
                f"官方累计：{format_duration(game.total_minutes)} · "
                f"首次游玩：{format_date(game.first_played_at, self._tz) if game.first_played_at else '未知'}"
            )
            game_since = int(state.get("game_since") or 0)
            if game_since:
                elapsed = max(1, int((now - game_since) // 60))
                lines.append(f"本次开始：{format_clock(game_since, self._tz)}（已观测 {format_duration(elapsed)}）")
        elif friend.online:
            lines.append("当前：在线，未公开正在游玩的游戏")
        else:
            last_seen = format_ts(friend.logout_at, self._tz) if friend.logout_at else "未知"
            lines.append(f"当前：离线（最后在线 {last_seen}）")
            if source == "cache":
                lines.append("（Nintendo 接口暂时不可用，以下仅为本地记录）")

        lines.append("")
        lines.append("—— 本插件观测 ——")
        lines.append(f"近 7 天：{len(week)} 次 · {format_duration(total_minutes(week))}")
        lines.append(f"近 30 天：{len(month)} 次 · {format_duration(total_minutes(month))}")
        if sessions:
            lines.append("最近记录：")
            for session in sessions[:8]:
                start = float(session.get("start") or 0)
                end = float(session.get("end") or 0)
                minutes = max(1, int((end - start) // 60))
                lines.append(
                    f"· {format_clock(start, self._tz)} – {format_clock(end, self._tz)} "
                    f"《{session.get('game') or '未知游戏'}》{format_duration(minutes)}"
                )
        else:
            lines.append("暂无本地记录（插件运行并轮询到游玩状态后才会积累）。")
        lines.append("")
        lines.append("说明：Nintendo API 只提供「当前游戏」的累计时长，无法直接拉取完整历史；")
        lines.append("本插件通过轮询补充记录，因此请保持轮询开启。")
        return [Comp.Plain("\n".join(lines))]

    async def _cmd_sub(self, event: AstrMessageEvent, arg: str) -> list[Any]:
        umo = self._umo(event)
        names = split_name_filter(arg) if arg else []
        await self.store.set_subscription(umo, names)
        if names:
            return [Comp.Plain(f"✅ 已订阅本会话动态推送，仅监控：{'、'.join(names)}")]
        return [Comp.Plain("✅ 已订阅本会话动态推送（全部好友）。可用 /nso 订阅 好友A,好友B 限定范围。")]

    async def _cmd_unsub(self, event: AstrMessageEvent, arg: str) -> list[Any]:
        umo = self._umo(event)
        removed = await self.store.remove_subscription(umo)
        if removed:
            return [Comp.Plain("✅ 已关闭本会话的动态推送。")]
        return [Comp.Plain("本会话原本就没有订阅推送。")]

    async def _cmd_subs(self, event: AstrMessageEvent, arg: str) -> list[Any]:
        subscriptions = self.store.subscriptions()
        current = subscriptions.get(self._umo(event))
        lines = [f"📣 订阅会话：{len(subscriptions)} 个"]
        if current:
            friends = current.get("friends") or []
            lines.append(f"本会话：已订阅（{'全部好友' if not friends else '、'.join(friends)}）")
        else:
            lines.append("本会话：未订阅（发送 /nso 订阅 开启）")
        return [Comp.Plain("\n".join(lines))]

    async def _cmd_status(self, event: AstrMessageEvent, arg: str) -> list[Any]:
        account = self.store.account
        now = time.time()
        lines = ["🛰 Nintendo 好友监控状态"]
        if self.store.session_token:
            nickname = account.get("nickname") or "未知"
            friend_code = account.get("friend_code") or "未知"
            lines.append(f"账号：{nickname}（好友码 {friend_code}）")
            lines.append("登录态：✅ session_token 已保存")
        else:
            lines.append("登录态：⚠️ 未登录（发送 /nso 登录）")

        if self._auth.credential_ready:
            remain = max(0, int((self._auth.credential_expires_at - now) // 60))
            lines.append(f"凭证：✅ 有效，剩余约 {remain} 分钟")
        else:
            lines.append("凭证：— 尚未获取（下次请求时自动登录）")

        lines.append(f"轮询：每 {self._poll_interval()} 秒 · 推送开关 {'开' if self._cfg_bool('enable_push', True) else '关'}")
        if self._last_poll_at:
            state = "成功" if self._last_poll_ok else "失败"
            lines.append(f"上次轮询：{format_relative(self._last_poll_at, self._tz)}（{state}）")
        elif self.store.last_poll_at:
            lines.append(f"上次轮询：{format_relative(self.store.last_poll_at, self._tz)}")
        else:
            lines.append("上次轮询：尚未开始")
        if self._failures:
            lines.append(f"连续失败：{self._failures} 次")
        if self._last_error:
            lines.append(f"最近错误：{self._last_error[:200]}")

        cached = self._friends_cache[1] if self._friends_cache else []
        if cached:
            playing = sum(1 for friend in cached if friend.playing)
            lines.append(f"好友缓存：{len(cached)} 位 · 正在游玩 {playing} 位")
        subscriptions = self.store.subscriptions()
        lines.append(f"订阅会话：{len(subscriptions)} 个")
        current = subscriptions.get(self._umo(event))
        lines.append(f"本会话：{'已订阅' if current else '未订阅'}")
        return [Comp.Plain("\n".join(lines))]

    async def _cmd_check(self, event: AstrMessageEvent, arg: str) -> list[Any]:
        if not self._is_admin(event):
            return [Comp.Plain("🔐 只有管理员可以手动触发检查。")]
        if not self.store.session_token:
            return [Comp.Plain("尚未登录 Nintendo 账号，请先执行 /nso 登录。")]
        result = await self._poll_once(push=True)
        if result.seeded:
            return [Comp.Plain(f"✅ 已初始化 {len(result.friends)} 位好友的状态（首轮不推送）。")]
        if not result.changes:
            return [Comp.Plain(f"✅ 检查完成，共 {len(result.friends)} 位好友，暂无状态变化。")]
        lines = [f"✅ 检查完成，发现 {len(result.changes)} 条变化："]
        for change in result.changes[:10]:
            if change.kind == "stopped":
                lines.append(f"⚫ {change.friend.name} 结束了《{change.previous_game}》")
            elif change.kind == "changed":
                game = change.friend.game.name if change.friend.game else "未知游戏"
                lines.append(f"🔄 {change.friend.name} 切换到了《{game}》")
            else:
                game = change.friend.game.name if change.friend.game else "未知游戏"
                lines.append(f"🟢 {change.friend.name} 开始游玩《{game}》")
        return [Comp.Plain("\n".join(lines))]

    async def _cmd_me(self, event: AstrMessageEvent, arg: str) -> list[Any]:
        account = self.store.account
        if not self.store.session_token:
            return [Comp.Plain("尚未登录 Nintendo 账号，请先执行 /nso 登录。")]
        lines = [
            "👤 当前 Nintendo 账号",
            f"昵称：{account.get('nickname') or '未知'}",
            f"好友码：{account.get('friend_code') or '未知'}",
            f"国家/地区：{account.get('country') or '未知'}",
            f"语言：{account.get('language') or '未知'}",
        ]
        return [Comp.Plain("\n".join(lines))]

    # ---------------------------------------------------------------- 工具
    @staticmethod
    def _umo(event: AstrMessageEvent) -> str:
        return str(getattr(event, "unified_msg_origin", "") or "unknown")

    @staticmethod
    def _is_admin(event: AstrMessageEvent) -> bool:
        checker = getattr(event, "is_admin", None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception:  # noqa: BLE001
                pass
        role = str(getattr(event, "role", "") or "").strip().lower()
        if role:
            return role in {"admin", "owner"}
        return True
