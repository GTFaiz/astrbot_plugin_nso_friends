"""游戏封面 / 好友头像下载与 base64 内联。

T2I 渲染服务通常无法直接访问 Nintendo CDN（地域、UA、防盗链都可能拦截），
所以先把图片落到本地再以 data URI 嵌入 HTML，渲染成功率更稳定。
"""

from __future__ import annotations

import base64
import hashlib
import time
from collections import OrderedDict
from pathlib import Path
from .http import Transport

_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"RIFF", "image/webp"),  # 仅粗判，后面再看 WEBP 标记
)

MAX_IMAGE_BYTES = 4 * 1024 * 1024
DEFAULT_TTL = 7 * 86400


def detect_mime(content: bytes) -> str | None:
    for magic, mime in _MAGIC:
        if content.startswith(magic):
            if mime == "image/webp" and content[8:12] != b"WEBP":
                return None
            return mime
    return None


def to_data_uri(content: bytes) -> str:
    mime = detect_mime(content)
    if mime is None:
        return ""
    encoded = base64.b64encode(content).decode("ascii")
    return f"data:{mime};base64,{encoded}"


class ImageCache:
    def __init__(
        self,
        cache_dir: Path,
        transport: Transport,
        *,
        ttl: int = DEFAULT_TTL,
        max_items: int = 128,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self._transport = transport
        self._ttl = ttl
        self._max_items = max_items
        self._memory: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    async def data_uri(self, url: str) -> str:
        url = (url or "").strip()
        if not url.startswith(("http://", "https://")):
            return ""
        now = time.time()
        cached = self._memory.get(url)
        if cached and cached[0] > now:
            self._memory.move_to_end(url)
            return cached[1]

        path = self.cache_dir / hashlib.sha256(url.encode("utf-8")).hexdigest()
        content: bytes | None = None
        if path.exists():
            try:
                if now - path.stat().st_mtime < self._ttl:
                    content = path.read_bytes()
            except OSError:
                content = None
        if content is None:
            content = await self._download(url)
            if content is None:
                return ""
            try:
                path.write_bytes(content)
            except OSError:
                pass

        uri = to_data_uri(content)
        if uri:
            self._memory[url] = (now + self._ttl, uri)
            self._memory.move_to_end(url)
            while len(self._memory) > self._max_items:
                self._memory.popitem(last=False)
        return uri

    async def _download(self, url: str) -> bytes | None:
        try:
            response = await self._transport.request(
                "GET",
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36",
                    "Accept": "image/*,*/*;q=0.8",
                },
            )
        except Exception:  # noqa: BLE001 - 图片失败不应影响推送
            return None
        if response.status_code != 200:
            return None
        content = response.content
        if not content or len(content) > MAX_IMAGE_BYTES:
            return None
        if detect_mime(content) is None:
            return None
        return content
