from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nso.images import ImageCache, detect_mime, to_data_uri

from tests.fake import PNG_BYTES, FakeTransport

AVATAR = "https://cdn-image-e0d67c509fb203858ebcb2fe3f88c2aa.baas.nintendo.com/1/avatar"


class MimeTest(unittest.TestCase):
    def test_detect_mime(self) -> None:
        self.assertEqual(detect_mime(PNG_BYTES), "image/png")
        self.assertEqual(detect_mime(b"\xff\xd8\xff\xe0" + b"0" * 8), "image/jpeg")
        self.assertEqual(detect_mime(b"RIFF\x00\x00\x00\x00WEBP"), "image/webp")
        self.assertIsNone(detect_mime(b"<html>"))
        self.assertEqual(to_data_uri(PNG_BYTES)[:22], "data:image/png;base64,")


class ImageCacheTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.transport = FakeTransport()
        self.cache = ImageCache(Path(self._tmp.name), self.transport)

    async def test_download_and_memory_cache(self) -> None:
        uri = await self.cache.data_uri(AVATAR)
        self.assertTrue(uri.startswith("data:image/png;base64,"))
        self.assertEqual(self.transport.calls[AVATAR], 1)
        await self.cache.data_uri(AVATAR)
        self.assertEqual(self.transport.calls[AVATAR], 1)

    async def test_disk_cache_survives_new_instance(self) -> None:
        await self.cache.data_uri(AVATAR)
        other = ImageCache(Path(self._tmp.name), self.transport)
        uri = await other.data_uri(AVATAR)
        self.assertTrue(uri.startswith("data:image/png;base64,"))
        self.assertEqual(self.transport.calls[AVATAR], 1)

    async def test_missing_image_returns_empty(self) -> None:
        self.assertEqual(await self.cache.data_uri("https://example.com/missing.png"), "")
        self.assertEqual(await self.cache.data_uri(""), "")

    async def test_non_image_is_rejected(self) -> None:
        self.assertEqual(await self.cache.data_uri("https://example.com/page.html"), "")


if __name__ == "__main__":
    unittest.main()
