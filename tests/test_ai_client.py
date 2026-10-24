import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ai_client import AIClient


class FakeResponse:
    def __init__(self, body: dict) -> None:
        self.body = json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        pass

    def read(self) -> bytes:
        return self.body


class AIClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings_patch = patch(
            "ai_client.settings_store.load_settings", return_value={}
        )
        self.settings_patch.start()

    def tearDown(self) -> None:
        self.settings_patch.stop()

    def test_complete_sends_chat_request_and_returns_reply(self) -> None:
        env = {
            "PRANA_API_KEY": "test-key",
            "PRANA_API_BASE_URL": "https://example.test/v1/",
            "PRANA_MODEL": "test-model",
        }
        messages: list[dict[str, object]] = [
            {"role": "user", "content": "你好"}
        ]
        with patch.dict(os.environ, env, clear=True), patch(
            "ai_client.urllib.request.urlopen",
            return_value=FakeResponse(
                {"choices": [{"message": {"content": "你好！"}}]}
            ),
        ) as urlopen:
            reply = AIClient().complete(messages)

        self.assertEqual(reply, "你好！")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://example.test/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
        self.assertEqual(
            json.loads(request.data),
            {"model": "test-model", "messages": messages},
        )

    def test_save_configuration_persists_and_updates_client(self) -> None:
        with patch("ai_client.settings_store.load_settings", return_value={}), patch(
            "ai_client.settings_store.save_settings"
        ) as save_settings:
            client = AIClient()
            client.save_configuration(
                "  new-key  ", " https://example.test/v1/ ", " model-v2 "
            )

        save_settings.assert_called_once_with(
            "new-key", "https://example.test/v1", "model-v2", []
        )
        self.assertEqual(client.api_key, "new-key")
        self.assertEqual(client.base_url, "https://example.test/v1")
        self.assertEqual(client.model, "model-v2")

    def test_media_refusal_uses_first_matching_subagent(self) -> None:
        image_agent = {
            "name": "图像助手",
            "description": "分析图片",
            "api_key": "image-key",
            "base_url": "https://image.test/v1",
            "model": "image-model",
            "media_types": ["image"],
        }
        video_agent = {
            "name": "视频助手",
            "description": "分析视频",
            "api_key": "video-key",
            "base_url": "https://video.test/v1",
            "model": "video-model",
            "media_types": ["video"],
        }
        client = AIClient(
            {
                "api_key": "main-key",
                "base_url": "https://main.test/v1",
                "model": "main-model",
                "subagents": [image_agent, video_agent],
            }
        )
        with patch(
            "ai_client.AIClient.complete",
            autospec=True,
            side_effect=lambda instance, _messages: (
                "无法识别图片"
                if instance.model == "main-model"
                else "图片内容已分析"
            ),
        ) as complete:
            reply = client.complete_with_media_fallback(
                [
                    {"role": "system", "content": "system"},
                    {"role": "user", "content": [{"type": "image_url"}]},
                ],
                {"image"},
            )

        self.assertEqual(reply, "图片内容已分析")
        self.assertEqual(
            [call.args[0].model for call in complete.call_args_list],
            ["main-model", "image-model"],
        )

    def test_media_refusal_requires_a_matching_subagent(self) -> None:
        client = AIClient(
            {
                "api_key": "main-key",
                "base_url": "https://main.test/v1",
                "model": "main-model",
                "subagents": [],
            }
        )
        with patch.object(
            AIClient, "complete", return_value="我无法识别视频内容。"
        ):
            with self.assertRaisesRegex(RuntimeError, "没有配置"):
                client.complete_with_media_fallback(
                    [{"role": "user", "content": "总结视频"}], {"video"}
                )

    def test_media_fallback_is_not_used_for_regular_reply(self) -> None:
        client = AIClient(
            {
                "api_key": "main-key",
                "base_url": "https://main.test/v1",
                "model": "main-model",
                "subagents": [],
            }
        )
        with patch.object(AIClient, "complete", return_value="画面中有一只猫。") as complete:
            reply = client.complete_with_media_fallback(
                [{"role": "user", "content": "描述图片"}], {"image"}
            )

        self.assertEqual(reply, "画面中有一只猫。")
        complete.assert_called_once()

    def test_complete_requires_api_key(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            client = AIClient()

        with self.assertRaisesRegex(RuntimeError, "设置"):
            client.complete([{"role": "user", "content": "你好"}])

    def test_complete_rejects_empty_reply(self) -> None:
        with patch.dict(os.environ, {"PRANA_API_KEY": "test-key"}, clear=True), patch(
            "ai_client.urllib.request.urlopen",
            return_value=FakeResponse(
                {"choices": [{"message": {"content": "  "}}]}
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "空回复"):
                AIClient().complete([{"role": "user", "content": "你好"}])

    def test_create_user_content_encodes_an_image(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "sample.png"
            image_path.write_bytes(b"image-data")
            with patch.dict(
                os.environ, {"PRANA_API_KEY": "test-key"}, clear=True
            ):
                content = AIClient().create_user_content(
                    "请描述", [str(image_path)]
                )

        self.assertEqual(content[0], {"type": "text", "text": "请描述"})
        self.assertEqual(content[1]["type"], "image_url")
        self.assertEqual(
            content[1]["image_url"]["url"],
            "data:image/png;base64,aW1hZ2UtZGF0YQ==",
        )

    def test_create_user_content_rejects_unsupported_files(self) -> None:
        with patch.dict(os.environ, {"PRANA_API_KEY": "test-key"}, clear=True):
            client = AIClient()
        with self.assertRaisesRegex(ValueError, "不支持"):
            client.create_user_content("查看", ["file.txt"])

    def test_create_user_content_includes_linked_webpage_text(self) -> None:
        with patch.dict(os.environ, {"PRANA_API_KEY": "test-key"}, clear=True), patch(
            "ai_client.AIClient._fetch_webpage", return_value="网页正文"
        ) as fetch_webpage:
            content = AIClient().create_user_content(
                "请总结 https://example.test/article。", []
            )

        fetch_webpage.assert_called_once_with("https://example.test/article")
        self.assertIn("网页正文", content)
        self.assertIn("不可信内容", content)

    def test_create_user_content_limits_linked_webpages(self) -> None:
        urls = " ".join(f"https://example{i}.test/" for i in range(4))
        with patch.dict(os.environ, {"PRANA_API_KEY": "test-key"}, clear=True):
            client = AIClient()

        with self.assertRaisesRegex(ValueError, "最多读取 3 个"):
            client.create_user_content(urls, [])

    def test_webpage_fetch_rejects_private_addresses(self) -> None:
        with patch(
            "ai_client.socket.getaddrinfo",
            return_value=[(None, None, None, None, ("127.0.0.1", 80))],
        ):
            with self.assertRaisesRegex(ValueError, "公开访问"):
                AIClient._fetch_webpage("http://localhost/private")

    def test_webpage_fetch_extracts_html_and_omits_scripts(self) -> None:
        from email.message import Message

        class FakeWebResponse(FakeResponse):
            def __init__(self) -> None:
                super().__init__({})
                self.body = (
                    b"<html><script>secret()</script><p>Hello <b>world</b></p></html>"
                )
                self.headers = Message()
                self.headers["Content-Type"] = "text/html; charset=utf-8"

            def read(self, _size: int = -1) -> bytes:
                return self.body if _size < 0 else self.body[:_size]

        class FakeOpener:
            def open(self, *_args, **_kwargs) -> FakeWebResponse:
                return FakeWebResponse()

        with patch("ai_client._validate_public_web_url"), patch(
            "ai_client.urllib.request.build_opener", return_value=FakeOpener()
        ):
            content = AIClient._fetch_webpage("https://example.test/")

        self.assertEqual(content, "Hello world")

    def test_fetch_webpage_prefers_meta_charset_over_generic_header(self) -> None:
        body = '<meta charset="gbk"><p>网页正文</p>'.encode("gbk")

        class FakeResponse:
            headers = SimpleNamespace(
                get_content_type=lambda: "text/html",
                get_content_charset=lambda: "iso-8859-1",
            )

            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, *_args: object) -> None:
                pass

            def read(self, _size: int) -> bytes:
                return body

        opener = SimpleNamespace(open=lambda *_args, **_kwargs: FakeResponse())
        with (
            patch("ai_client._validate_public_web_url"),
            patch("ai_client.urllib.request.build_opener", return_value=opener),
        ):
            content = AIClient._fetch_webpage("https://example.test/")

        self.assertEqual(content, "网页正文")

    def test_create_user_content_samples_video_frames(self) -> None:
        class FakeCapture:
            def __init__(self) -> None:
                self.positions: list[int] = []

            def isOpened(self) -> bool:
                return True

            def get(self, property_id: int) -> float:
                return 30 if property_id == 1 else 1

            def set(self, _property_id: int, position: int) -> None:
                self.positions.append(position)

            def read(self) -> tuple[bool, object]:
                return True, SimpleNamespace(shape=(2, 3, 3))

            def release(self) -> None:
                pass

        capture = FakeCapture()
        fake_cv2 = SimpleNamespace(
            VideoCapture=lambda _path: capture,
            CAP_PROP_FRAME_COUNT=1,
            CAP_PROP_FPS=2,
            CAP_PROP_POS_FRAMES=3,
            IMWRITE_JPEG_QUALITY=4,
            imencode=lambda *_args: (True, b"frame-data"),
        )
        with tempfile.TemporaryDirectory() as directory:
            video_path = Path(directory) / "sample.mp4"
            video_path.write_bytes(b"video")
            with (
                patch.dict(os.environ, {"PRANA_API_KEY": "test-key"}, clear=True),
                patch.dict(sys.modules, {"cv2": fake_cv2}),
            ):
                content = AIClient().create_user_content(
                    "总结视频", [str(video_path)]
                )

        self.assertEqual(
            capture.positions,
            [0, 2, 4, 6, 8, 10, 12, 14, 15, 17, 19, 21, 23, 25, 27, 29],
        )
        self.assertEqual(
            [part["type"] for part in content],
            ["text", "text"]
            + ["text", "image_url"] * 16,
        )
        self.assertIn("按时间顺序", content[1]["text"])

    def test_create_user_content_handles_non_ascii_video_path(self) -> None:
        class FakeCapture:
            def isOpened(self) -> bool:
                return True

            def get(self, property_id: int) -> float:
                return 1 if property_id == 1 else 0

            def set(self, _property_id: int, _position: int) -> None:
                pass

            def read(self) -> tuple[bool, object]:
                return True, SimpleNamespace(shape=(2, 3, 3))

            def release(self) -> None:
                pass

        capture = FakeCapture()
        opened_paths: list[str] = []

        def open_video(video_path: str) -> FakeCapture:
            opened_paths.append(video_path)
            if not video_path.isascii():
                raise UnicodeEncodeError(
                    "ascii", video_path, 5, 6, "ordinal not in range(128)"
                )
            return capture

        fake_cv2 = SimpleNamespace(
            VideoCapture=open_video,
            CAP_PROP_FRAME_COUNT=1,
            CAP_PROP_FPS=2,
            CAP_PROP_POS_FRAMES=3,
            IMWRITE_JPEG_QUALITY=4,
            imencode=lambda *_args: (True, b"frame-data"),
        )
        with tempfile.TemporaryDirectory() as directory:
            video_path = Path(directory) / "视频.mp4"
            video_path.write_bytes(b"video")
            with (
                patch.dict(os.environ, {"PRANA_API_KEY": "test-key"}, clear=True),
                patch.dict(sys.modules, {"cv2": fake_cv2}),
            ):
                content = AIClient().create_user_content(
                    "总结视频", [str(video_path)]
                )

        self.assertEqual(len(content), 4)
        self.assertEqual(len(opened_paths), 2)
        self.assertFalse(Path(opened_paths[1]).exists())
        self.assertTrue(opened_paths[1].isascii())


if __name__ == "__main__":
    unittest.main()
