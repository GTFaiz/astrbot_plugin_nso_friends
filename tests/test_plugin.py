"""用最小 astrbot 桩模块跑一遍插件指令与推送链路。

这些测试不连接 Nintendo，也不依赖 AstrBot 运行时；
它们主要用于发现 "插件在真实框架里 import / 调用" 层面的错误。
"""

from __future__ import annotations

import asyncio
import logging
import sys
import tempfile
import types
import unittest
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_PARENT = PLUGIN_ROOT.parent
for path in (str(PLUGIN_ROOT), str(PLUGIN_PARENT)):
    if path not in sys.path:
        sys.path.insert(0, path)


# --------------------------------------------------------------------------
# astrbot 桩
# --------------------------------------------------------------------------
def _install_astrbot_stubs() -> None:
    if "astrbot" in sys.modules:
        return

    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    event_mod = types.ModuleType("astrbot.api.event")
    star_mod = types.ModuleType("astrbot.api.star")
    comp_mod = types.ModuleType("astrbot.api.message_components")
    core = types.ModuleType("astrbot.core")
    core_utils = types.ModuleType("astrbot.core.utils")
    core_path = types.ModuleType("astrbot.core.utils.astrbot_path")

    class AstrBotConfig(dict):
        pass

    class AstrMessageEvent:  # noqa: D401 - 占位
        pass

    class MessageChain:
        def __init__(self, chain: list[Any] | None = None) -> None:
            self.chain = list(chain or [])

    class Plain:
        def __init__(self, text: str) -> None:
            self.text = text

    class Image:
        def __init__(self, file: Any = None) -> None:
            self.file = file

        @classmethod
        def fromBytes(cls, data: bytes) -> "Image":
            return cls(data)

        @classmethod
        def fromURL(cls, url: str, **kwargs: Any) -> "Image":
            return cls(url)

    class Context:
        def __init__(self) -> None:
            self.sent: list[tuple[str, Any]] = []

        async def send_message(self, umo: str, chain: Any) -> bool:
            self.sent.append((umo, chain))
            return True

    class Star:
        html_render_calls: list[str] = []

        def __init__(self, context: Context) -> None:
            self.context = context

        async def html_render(self, tmpl: str, data: dict, return_url: bool = False, options: dict | None = None) -> bytes:
            Star.html_render_calls.append(tmpl)
            return b"\x89PNG\r\n\x1a\n" + b"0" * 32

    class _Filter:
        def command(self, *args: Any, **kwargs: Any):
            def decorator(func):
                return func

            return decorator

    def register(*args: Any, **kwargs: Any):
        def decorator(cls):
            return cls

        return decorator

    api.AstrBotConfig = AstrBotConfig
    api.logger = logging.getLogger("astrbot-test")
    event_mod.AstrMessageEvent = AstrMessageEvent
    event_mod.MessageChain = MessageChain
    event_mod.filter = _Filter()
    star_mod.Context = Context
    star_mod.Star = Star
    star_mod.register = register
    comp_mod.Plain = Plain
    comp_mod.Image = Image
    api.event = event_mod
    api.star = star_mod
    api.message_components = comp_mod
    core_path.get_astrbot_data_path = lambda: str(_TMP_DATA_ROOT[0])
    core.utils = core_utils
    core_utils.astrbot_path = core_path
    astrbot.api = api
    astrbot.core = core
    sys.modules.update(
        {
            "astrbot": astrbot,
            "astrbot.api": api,
            "astrbot.api.event": event_mod,
            "astrbot.api.star": star_mod,
            "astrbot.api.message_components": comp_mod,
            "astrbot.core": core,
            "astrbot.core.utils": core_utils,
            "astrbot.core.utils.astrbot_path": core_path,
        }
    )


_TMP_DATA_ROOT = [""]
_install_astrbot_stubs()

from tests.fake import FakeTransport, friend_payload  # noqa: E402


class FakeEvent:
    def __init__(self, message: str = "/nso", admin: bool = True) -> None:
        self.message_str = message
        self.role = "admin" if admin else "member"
        self.unified_msg_origin = "aiocqhttp:GroupMessage:123456"

    def is_admin(self) -> bool:
        return self.role == "admin"

    def get_extra(self, key: str) -> str:
        return ""

    def chain_result(self, components: list[Any]) -> tuple[str, list[Any]]:
        return ("chain", components)

    def plain_result(self, text: str) -> tuple[str, str]:
        return ("plain", text)


class PluginCommandTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        import importlib

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        _TMP_DATA_ROOT[0] = self._tmp.name

        # 每次重建 Star 的类级记录
        star_mod = sys.modules["astrbot.api.star"]
        star_mod.Star.html_render_calls.clear()

        module = importlib.import_module("astrbot_plugin_nso_friends.main")
        self.transport = FakeTransport(friends=[friend_payload("Alice", game="Splatoon 3")])
        self.plugin = module.NsoFriendsPlugin(
            star_mod.Context(),
            {"render_card": True, "enable_push": True, "timezone": "Asia/Shanghai"},
        )
        self.plugin._transport = self.transport
        self.plugin._auth._transport = self.transport
        self.plugin._images._transport = self.transport
        await self.plugin.store.load()

    async def asyncTearDown(self) -> None:
        await self.transport.aclose()

    async def test_login_flow_and_auto_subscribe(self) -> None:
        event = FakeEvent()
        components = await self.plugin._cmd_login(event, "")
        text = components[0].text
        self.assertIn("https://accounts.nintendo.com/connect/1.0.0/authorize", text)

        challenge, _ = self.plugin._login_challenges[event.unified_msg_origin]
        link = f"npf71b963c1b7b6d119://auth#session_token_code=CODE&state={challenge.state}"
        components = await self.plugin._cmd_login(event, link)
        self.assertIn("绑定成功", components[0].text)
        self.assertEqual(self.plugin.store.session_token, "session-token-1")
        self.assertIsNotNone(self.plugin.store.subscription(event.unified_msg_origin))
        self.assertEqual(self.plugin.store.account["nickname"], "Me")

    async def test_friends_query_history_status(self) -> None:
        await self.plugin._cmd_login(FakeEvent(), self._login_link())
        event = FakeEvent()

        friends = await self.plugin._cmd_friends(event, "")
        self.assertIn("好友 1 位", friends[0].text)
        self.assertIn("Splatoon 3", friends[0].text)

        query = await self.plugin._cmd_query(event, "Alice")
        self.assertEqual(len(query), 2)
        self.assertIn("商店页", query[1].text)

        history = await self.plugin._cmd_history(event, "Alice")
        self.assertIn("的游玩记录", history[0].text)

        status = await self.plugin._cmd_status(event, "")
        self.assertIn("Nintendo 好友监控状态", status[0].text)

        me = await self.plugin._cmd_me(event, "")
        self.assertIn("1234-5678-9012", me[0].text)

    async def test_query_falls_back_to_text_when_render_disabled(self) -> None:
        await self.plugin._cmd_login(FakeEvent(), self._login_link())
        self.plugin.config["render_card"] = False
        components = await self.plugin._cmd_query(FakeEvent(), "Alice")
        self.assertTrue(hasattr(components[0], "text"))
        self.assertIn("《Splatoon 3》", components[0].text)

    async def test_subscription_commands(self) -> None:
        event = FakeEvent()
        subscribed = await self.plugin._cmd_sub(event, "Alice, Bob")
        self.assertIn("Alice", subscribed[0].text)
        self.assertEqual(self.plugin.store.subscription(event.unified_msg_origin)["friends"], ["Alice", "Bob"])

        subs = await self.plugin._cmd_subs(event, "")
        self.assertIn("已订阅", subs[0].text)

        removed = await self.plugin._cmd_unsub(event, "")
        self.assertIn("已关闭", removed[0].text)
        self.assertIsNone(self.plugin.store.subscription(event.unified_msg_origin))

    async def test_poll_detects_game_and_pushes_card(self) -> None:
        event = FakeEvent()
        await self.plugin._cmd_login(event, self._login_link())

        seeded = await self.plugin._poll_once()
        self.assertTrue(seeded.seeded)
        self.assertEqual(self.plugin.context.sent, [])

        self.transport.friends = [friend_payload("Alice", game="Zelda", updated_at=1_700_000_500)]
        result = await self.plugin._poll_once()
        self.assertEqual([change.kind for change in result.changes], ["changed"])
        self.assertEqual(len(self.plugin.context.sent), 1)
        umo, chain = self.plugin.context.sent[0]
        self.assertEqual(umo, event.unified_msg_origin)
        self.assertEqual(len(chain.chain), 1)
        self.assertIsInstance(chain.chain[0].file, bytes)
        self.assertTrue(chain.chain[0].file.startswith(b"\x89PNG"))

    async def test_run_subcommand_help_and_unknown(self) -> None:
        components = await self.plugin._run_subcommand(FakeEvent(), "help", "")
        self.assertIn("/nso 登录", components[0].text)
        # 直接把未知子命令当好友名查询
        components = await self.plugin._run_subcommand(FakeEvent(), "query", "")
        self.assertIn("请指定好友名", components[0].text)

    async def test_generator_command_wrapper(self) -> None:
        results = [item async for item in self.plugin.nso(FakeEvent("/nso 帮助"))]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0], "chain")
        self.assertIn("/nso 登录", results[0][1][0].text)

    async def test_initialize_starts_and_terminate_cancels_poll_task(self) -> None:
        await self.plugin.store.set_session_token("session-token-1")
        self.plugin._auth.set_session_token("session-token-1")
        await self.plugin.initialize()
        self.assertIsNotNone(self.plugin._poll_task)
        self.assertFalse(self.plugin._poll_task.done())
        await self.plugin.terminate()
        self.assertTrue(self.plugin._poll_task is None or self.plugin._poll_task.done())

    async def test_command_args_strip_prefix(self) -> None:
        module = sys.modules["astrbot_plugin_nso_friends.main"]
        event = FakeEvent("/nso 查询 Alice")
        self.assertEqual(module._command_args(event), "查询 Alice")
        event = FakeEvent("／任天堂 好友")
        self.assertEqual(module._command_args(event), "好友")

    def _login_link(self) -> str:
        challenge = self.plugin._login_challenges.get("pre")
        if challenge:
            return ""
        event = FakeEvent()
        # 直接生成 challenge，避免依赖异步
        from nso.auth import create_login_challenge

        challenge = create_login_challenge()
        self.plugin._login_challenges[event.unified_msg_origin] = (challenge, __import__("time").time())
        return f"npf71b963c1b7b6d119://auth#session_token_code=CODE&state={challenge.state}"


if __name__ == "__main__":
    unittest.main()
