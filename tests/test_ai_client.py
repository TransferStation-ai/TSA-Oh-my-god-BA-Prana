import base64
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ai_client import AIClient


class FakeResponse:
    def __init__(self, body: dict) -> None:
        self.body = json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        pass

    def read(self, _size: int = -1) -> bytes:
        return self.body if _size < 0 else self.body[:_size]


class FakeBrainstormRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def run(self, messages, problem, focus=""):
        from brainstorm import BrainstormResult

        self.calls.append((problem, focus))
        return BrainstormResult(
            status="completed",
            brainstorm_session_id="a" * 32,
            summary="总结",
            key_disagreements=["分歧"],
            rounds=1,
            participants=["Prana（主模型）", "A"],
        )


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

    def test_chat_request_retries_once_after_failure(self) -> None:
        with patch.dict(os.environ, {"PRANA_API_KEY": "test-key"}, clear=True):
            client = AIClient()
        with patch.object(
            client,
            "_complete_message_once",
            side_effect=[
                RuntimeError("temporarily unavailable"),
                {"role": "assistant", "content": "已收到"},
            ],
        ) as complete_once:
            response = client._complete_message(
                [{"role": "user", "content": "你好"}]
            )

        self.assertEqual(response["content"], "已收到")
        self.assertEqual(complete_once.call_count, 2)

    def test_chat_request_reports_error_after_retry_fails(self) -> None:
        with patch.dict(os.environ, {"PRANA_API_KEY": "test-key"}, clear=True):
            client = AIClient()
        with patch.object(
            client,
            "_complete_message_once",
            side_effect=RuntimeError("service unavailable"),
        ) as complete_once:
            with self.assertRaisesRegex(RuntimeError, "service unavailable"):
                client._complete_message(
                    [{"role": "user", "content": "你好"}]
                )

        self.assertEqual(complete_once.call_count, 2)

    def test_save_configuration_persists_and_updates_client(self) -> None:
        with patch("ai_client.settings_store.load_settings", return_value={}), patch(
            "ai_client.settings_store.save_settings"
        ) as save_settings:
            client = AIClient()
            client.save_configuration(
                "  new-key  ",
                " https://example.test/v1/ ",
                " model-v2 ",
                review_model=" review-v1 ",
            )

        save_settings.assert_called_once_with(
            "new-key",
            "https://example.test/v1",
            "model-v2",
            [],
            "gpt-image-1",
            "sora-2",
            "review-v1",
            brainstorm_rounds=10,
            linked_providers=[],
        )
        self.assertEqual(client.api_key, "new-key")
        self.assertEqual(client.base_url, "https://example.test/v1")
        self.assertEqual(client.model, "model-v2")
        self.assertEqual(client.review_model, "review-v1")
        self.assertEqual(client.brainstorm_rounds, 10)
        self.assertEqual(client.linked_providers, [])

    def test_brainstorm_participants_order_and_dedupe(self) -> None:
        client = AIClient(
            configuration={
                "api_key": "main-key",
                "base_url": "https://main.test/v1",
                "model": "main-model",
                "subagents": [
                    {
                        "name": "A",
                        "description": "架构",
                        "api_key": "a-key",
                        "base_url": "https://a.test/v1",
                        "model": "a-model",
                    },
                    {
                        "name": "Duplicate",
                        "description": "重复",
                        "api_key": "x-key",
                        "base_url": "https://main.test/v1",
                        "model": "main-model",
                    },
                ],
                "linked_providers": [
                    {
                        "id": "p1",
                        "name": "P1",
                        "base_url": "https://p1.test/v1",
                        "api_key": "p1-key",
                        "source": "opencode",
                        "models": [
                            {
                                "id": "p1-model",
                                "name": "P1M",
                                "brainstorm": True,
                            },
                            {
                                "id": "off",
                                "name": "Off",
                                "brainstorm": False,
                            },
                        ],
                    },
                    {
                        "id": "main",
                        "name": "Main",
                        "base_url": "https://main.test/v1",
                        "api_key": "",
                        "source": "main",
                        "models": [
                            {
                                "id": "main-model",
                                "name": "Main",
                                "brainstorm": True,
                            },
                            {
                                "id": "other",
                                "name": "Other",
                                "brainstorm": True,
                            },
                        ],
                    },
                ],
            }
        )

        names = [participant.name for participant in client.brainstorm_participants]

        self.assertEqual(
            names,
            ["Prana（主模型）", "A", "P1 / P1M", "Main / Other"],
        )
        self.assertTrue(client.has_brainstorm_participants)

    def test_brainstorm_requires_another_participant(self) -> None:
        client = AIClient(
            configuration={
                "api_key": "main-key",
                "base_url": "https://main.test/v1",
                "model": "main-model",
            }
        )
        self.assertFalse(client.has_brainstorm_participants)
        self.assertEqual(
            [participant.name for participant in client.brainstorm_participants],
            ["Prana（主模型）"],
        )

    def test_fetch_main_provider_models(self) -> None:
        client = AIClient(
            configuration={
                "api_key": "key",
                "base_url": "https://example.test/v1",
                "model": "m",
            }
        )
        with patch.object(
            client,
            "_open_authenticated_request",
            return_value=FakeResponse(
                {"data": [{"id": "m1", "name": "M1"}, {"id": "m2"}]}
            ),
        ) as open_request:
            models = client.fetch_main_provider_models()

        self.assertEqual(
            models,
            [
                {"id": "m1", "name": "M1"},
                {"id": "m2", "name": "m2"},
            ],
        )
        request = open_request.call_args.args[0]
        self.assertTrue(request.full_url.endswith("/models"))
        self.assertEqual(
            request.get_header("Authorization"), "Bearer key"
        )

    def test_fetch_main_provider_models_rejects_empty_list(self) -> None:
        client = AIClient(
            configuration={
                "api_key": "key",
                "base_url": "https://example.test/v1",
                "model": "m",
            }
        )
        with patch.object(
            client,
            "_open_authenticated_request",
            return_value=FakeResponse({"data": []}),
        ):
            with self.assertRaisesRegex(RuntimeError, "没有返回可用模型"):
                client.fetch_main_provider_models()

    def test_start_brainstorm_tool_is_available_and_returns_result(self) -> None:
        client = AIClient(
            configuration={
                "api_key": "main-key",
                "base_url": "https://main.test/v1",
                "model": "main-model",
                "subagents": [
                    {
                        "name": "A",
                        "description": "评审",
                        "api_key": "a-key",
                        "base_url": "https://a.test/v1",
                        "model": "a-model",
                    }
                ],
            }
        )
        runner = FakeBrainstormRunner()
        tool_call = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "function": {
                        "name": "start_brainstorm",
                        "arguments": json.dumps({"problem": "问题"}),
                    },
                }
            ],
        }
        with patch.object(
            client,
            "_complete_message",
            side_effect=[
                tool_call,
                {"role": "assistant", "content": "最终回答"},
            ],
        ) as complete:
            reply, media = client.complete_with_media_skills(
                [{"role": "user", "content": "请回答"}],
                brainstorm_runner=runner,
            )

        self.assertEqual(reply, "最终回答")
        self.assertEqual(media, [])
        self.assertEqual(runner.calls, [("问题", "")])
        first_tools = complete.call_args_list[0].kwargs["tools"]
        self.assertTrue(
            any(
                tool["function"]["name"] == "start_brainstorm"
                for tool in first_tools
            )
        )
        second_messages = complete.call_args_list[1].args[0]
        tool_messages = [
            message
            for message in second_messages
            if message.get("role") == "tool"
        ]
        self.assertEqual(len(tool_messages), 1)
        result = json.loads(tool_messages[0]["content"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["summary"], "总结")

    def test_start_brainstorm_without_runner_returns_unavailable(self) -> None:
        client = AIClient(
            configuration={
                "api_key": "main-key",
                "base_url": "https://main.test/v1",
                "model": "main-model",
                "subagents": [
                    {
                        "name": "A",
                        "description": "评审",
                        "api_key": "a-key",
                        "base_url": "https://a.test/v1",
                        "model": "a-model",
                    }
                ],
            }
        )
        tool_call = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "function": {
                        "name": "start_brainstorm",
                        "arguments": json.dumps({"problem": "问题"}),
                    },
                }
            ],
        }
        with patch.object(
            client,
            "_complete_message",
            side_effect=[
                tool_call,
                {"role": "assistant", "content": "最终回答"},
            ],
        ) as complete:
            reply, _ = client.complete_with_media_skills(
                [{"role": "user", "content": "请回答"}],
                brainstorm_runner=None,
            )

        self.assertEqual(reply, "最终回答")
        first_tools = complete.call_args_list[0].kwargs["tools"]
        self.assertFalse(
            any(
                tool["function"]["name"] == "start_brainstorm"
                for tool in first_tools
            )
        )
        second_messages = complete.call_args_list[1].args[0]
        tool_message = next(
            message
            for message in second_messages
            if message.get("role") == "tool"
        )
        result = json.loads(tool_message["content"])
        self.assertEqual(result["status"], "unavailable")

    def test_generate_image_saves_returned_png(self) -> None:
        png_data = b"\x89PNG\r\n\x1a\nimage-data"
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"PRANA_API_KEY": "test-key"}, clear=True
        ), patch(
            "ai_client.settings_store.settings_file_path",
            return_value=Path(directory) / "settings.json",
        ), patch(
            "ai_client.AIClient._open_authenticated_request",
            return_value=FakeResponse(
                {
                    "data": [
                        {"b64_json": base64.b64encode(png_data).decode("ascii")}
                    ]
                }
            ),
        ) as urlopen:
            client = AIClient()
            image_path = client.generate_image("一只猫")
            self.assertEqual(image_path.suffix, ".png")
            self.assertEqual(image_path.read_bytes(), png_data)
            request = urlopen.call_args.args[0]
            self.assertTrue(request.full_url.endswith("/images/generations"))
            self.assertEqual(
                request.get_header("Authorization"), "Bearer test-key"
            )
            self.assertEqual(
                json.loads(request.data),
                {
                    "model": "gpt-image-1",
                    "prompt": "一只猫",
                    "size": "1024x1024",
                },
            )

    def test_generate_image_downloads_url_when_b64_json_is_empty(self) -> None:
        png_data = b"\x89PNG\r\n\x1a\nimage-data"
        image_url = "https://example.test/output/image.png"
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"PRANA_API_KEY": "test-key"}, clear=True
        ), patch(
            "ai_client.settings_store.settings_file_path",
            return_value=Path(directory) / "settings.json",
        ), patch(
            "ai_client.AIClient._open_authenticated_request",
            return_value=FakeResponse(
                {"data": [{"url": image_url, "b64_json": ""}]}
            ),
        ), patch(
            "ai_client.AIClient._download_public_media",
            return_value=png_data,
        ) as download:
            client = AIClient()
            image_path = client.generate_image("一只猫")
            self.assertEqual(image_path.suffix, ".png")
            self.assertEqual(image_path.read_bytes(), png_data)
            download.assert_called_once_with(
                image_url, 50 * 1024 * 1024
            )

    def test_generate_video_creates_polls_and_saves_video(self) -> None:
        class BinaryResponse:
            def __init__(self, data: bytes) -> None:
                self.data = data

            def __enter__(self):
                return self

            def __exit__(self, *_args) -> None:
                pass

            def read(self, size: int = -1) -> bytes:
                return self.data if size < 0 else self.data[:size]

        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"PRANA_API_KEY": "test-key"}, clear=True
        ), patch(
            "ai_client.settings_store.settings_file_path",
            return_value=Path(directory) / "settings.json",
        ), patch(
            "ai_client.AIClient._open_authenticated_request",
            side_effect=[
                FakeResponse({"id": "video-job"}),
                FakeResponse({"status": "completed"}),
                BinaryResponse(b"video-data"),
            ],
        ) as urlopen, patch("ai_client.time.sleep"):
            client = AIClient()
            video_path = client.generate_video("海边日落")
            self.assertEqual(video_path.suffix, ".mp4")
            self.assertEqual(video_path.read_bytes(), b"video-data")
            create_request = urlopen.call_args_list[0].args[0]
            self.assertTrue(create_request.full_url.endswith("/videos"))
            self.assertEqual(
                json.loads(create_request.data),
                {
                    "model": "sora-2",
                    "prompt": "海边日落",
                    "seconds": "4",
                    "size": "1280x720",
                },
            )
            content_request = urlopen.call_args_list[2].args[0]
            self.assertTrue(
                content_request.full_url.endswith("/videos/video-job/content")
            )

    def test_generate_agnes_video_sends_mode_and_downloads_result_url(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch(
            "ai_client.settings_store.settings_file_path",
            return_value=Path(directory) / "settings.json",
        ), patch("ai_client.time.sleep") as sleep:
            client = AIClient(
                {
                    "api_key": "test-key",
                    "base_url": "https://apihub.agnes-ai.com/v1",
                    "model": "test-model",
                    "video_model": "agnes-video-2.5",
                }
            )
            with patch.object(
                client,
                "_api_request",
                side_effect=[
                    {"video_id": "agnes-video-id", "status": "queued"},
                    {
                        "status": "completed",
                        "url": "https://media.example.test/generated.mp4",
                    },
                ],
            ) as api_request, patch.object(
                client,
                "_download_public_media",
                return_value=b"agnes-video-data",
            ) as download:
                video_path = client.generate_video("海边日落")

            self.assertEqual(video_path.suffix, ".mp4")
            self.assertEqual(video_path.read_bytes(), b"agnes-video-data")
            self.assertEqual(
                api_request.call_args_list[0].args,
                (
                    "/videos",
                    {
                        "model": "agnes-video-2.5",
                        "prompt": "海边日落",
                        "seconds": "4",
                        "size": "720P",
                        "mode": "text",
                        "aspect_ratio": "16:9",
                    },
                ),
            )
            self.assertEqual(
                api_request.call_args_list[1].args[0],
                "https://apihub.agnes-ai.com/agnesapi?"
                "video_id=agnes-video-id&model_name=agnes-video-2.5",
            )
            download.assert_called_once_with(
                "https://media.example.test/generated.mp4",
                200 * 1024 * 1024,
                media_type="视频",
                timeout=120,
            )
            sleep.assert_called_once_with(2)

    def test_media_skill_generates_and_returns_media_for_chat(self) -> None:
        client = AIClient(
            {
                "api_key": "main-key",
                "base_url": "https://main.test/v1",
                "model": "main-model",
            }
        )
        media_path = Path("generated.png")
        assistant_tool_call = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-image",
                    "type": "function",
                    "function": {
                        "name": "send_image",
                        "arguments": json.dumps({"prompt": "一只猫"}),
                    },
                }
            ],
        }
        with patch.object(
            client,
            "_complete_message",
            side_effect=[
                assistant_tool_call,
                {"role": "assistant", "content": "图片已发送给老师。"},
            ],
        ) as complete_message, patch.object(
            client, "generate_image", return_value=media_path
        ) as generate_image, patch.object(
            client, "_review_local_file_operation"
        ) as review_file:
            reply, media = client.complete_with_media_skills(
                [{"role": "user", "content": "请生成一只猫的图片"}]
            )

        self.assertEqual(reply, "图片已发送给老师。")
        self.assertEqual(media, [media_path])
        generate_image.assert_called_once_with("一只猫")
        self.assertEqual(
            review_file.call_args.args[:2],
            ("send_image", {"prompt": "一只猫"}),
        )
        self.assertEqual(complete_message.call_count, 2)
        self.assertEqual(
            complete_message.call_args_list[0].kwargs["tool_choice"],
            {
                "type": "function",
                "function": {"name": "send_image"},
            },
        )
        self.assertIsNone(complete_message.call_args_list[1].kwargs["tool_choice"])
        self.assertEqual(
            [
                tool["function"]["name"]
                for tool in complete_message.call_args_list[0].kwargs["tools"]
            ],
            [
                "send_local_media",
                "send_web_image",
                "send_image",
                "send_video",
                "control_qq_music",
                "run_terminal_command",
                "get_project_structure",
                "search_workspace_files",
                "read_git_repository",
            ],
        )
        self.assertEqual(
            complete_message.call_args_list[0].kwargs["tools"][2]["function"][
                "name"
            ],
            "send_image",
        )
        tool_message = complete_message.call_args_list[1].args[0][-1]
        self.assertEqual(tool_message["role"], "tool")
        self.assertEqual(tool_message["tool_call_id"], "call-image")
        self.assertIn("generated.png", tool_message["content"])

    def test_qq_music_skill_is_invoked_for_explicit_tool_call(self) -> None:
        client = AIClient(
            {
                "api_key": "main-key",
                "base_url": "https://main.test/v1",
                "model": "main-model",
            }
        )
        with patch.object(
            client,
            "_complete_message",
            side_effect=[
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-qq-music",
                            "function": {
                                "name": "control_qq_music",
                                "arguments": json.dumps(
                                    {"action": "search", "query": "青花瓷"}
                                ),
                            },
                        }
                    ],
                },
                {"role": "assistant", "content": "已在 QQ 音乐中搜索。"},
            ],
        ) as complete_message, patch(
            "ai_client.invoke_qq_music_skill",
            return_value='{"application":"QQ 音乐","action":"search"}',
        ) as invoke:
            reply, media = client.complete_with_media_skills(
                [{"role": "user", "content": "在 QQ 音乐搜索青花瓷"}]
            )

        self.assertEqual(reply, "已在 QQ 音乐中搜索。")
        self.assertEqual(media, [])
        invoke.assert_called_once_with({"action": "search", "query": "青花瓷"})
        tool_message = complete_message.call_args_list[1].args[0][-1]
        self.assertEqual(tool_message["role"], "tool")
        self.assertIn("QQ 音乐", tool_message["content"])

    def test_chat_message_skill_sends_streamed_messages(self) -> None:
        client = AIClient(
            {
                "api_key": "main-key",
                "base_url": "https://main.test/v1",
                "model": "main-model",
            }
        )
        sent_messages: list[str] = []
        tool_call = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-chat",
                    "function": {
                        "name": "send_chat_message",
                        "arguments": json.dumps({"message": "先说明一下。"}),
                    },
                }
            ],
        }
        with patch.object(
            client,
            "_complete_message",
            side_effect=[
                tool_call,
                {"role": "assistant", "content": "完整回复"},
            ],
        ) as complete_message:
            reply, media = client.complete_with_media_skills(
                [{"role": "user", "content": "解释一下"}],
                message_sender=sent_messages.append,
            )

        self.assertEqual(reply, "完整回复")
        self.assertEqual(media, [])
        self.assertEqual(sent_messages, ["先说明一下。"])
        tool_names = [
            tool["function"]["name"]
            for tool in complete_message.call_args_list[0].kwargs["tools"]
        ]
        self.assertIn("send_chat_message", tool_names)

    def test_chat_message_skill_allows_empty_final_reply(self) -> None:
        client = AIClient(
            {
                "api_key": "main-key",
                "base_url": "https://main.test/v1",
                "model": "main-model",
            }
        )
        tool_call = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-chat",
                    "function": {
                        "name": "send_chat_message",
                        "arguments": json.dumps({"message": "只有这一条。"}),
                    },
                }
            ],
        }
        with patch.object(
            client,
            "_complete_message",
            side_effect=[
                tool_call,
                {"role": "assistant", "content": ""},
            ],
        ):
            reply, media = client.complete_with_media_skills(
                [{"role": "user", "content": "说一句话"}],
                message_sender=lambda _message: None,
            )

        self.assertEqual(reply, "")
        self.assertEqual(media, [])

    def test_chat_message_skill_is_not_offered_without_sender(self) -> None:
        client = AIClient(
            {
                "api_key": "main-key",
                "base_url": "https://main.test/v1",
                "model": "main-model",
            }
        )
        with patch.object(
            client,
            "_complete_message",
            return_value={"role": "assistant", "content": "你好"},
        ) as complete_message:
            client.complete_with_media_skills(
                [{"role": "user", "content": "你好"}]
            )

        tool_names = [
            tool["function"]["name"]
            for tool in complete_message.call_args.kwargs["tools"]
        ]
        self.assertNotIn("send_chat_message", tool_names)

    def test_webpage_image_skill_returns_selected_image_for_chat(self) -> None:
        client = AIClient(
            {
                "api_key": "main-key",
                "base_url": "https://main.test/v1",
                "model": "main-model",
            }
        )
        image_url = "https://example.test/cat.png"
        media_path = Path("cat.png")
        assistant_tool_call = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-web-image",
                    "type": "function",
                    "function": {
                        "name": "send_web_image",
                        "arguments": json.dumps({"url": image_url}),
                    },
                }
            ],
        }
        webpage_images = [{"url": image_url, "alt": "一只猫"}]
        with patch.object(
            client,
            "_complete_message",
            side_effect=[
                assistant_tool_call,
                {"role": "assistant", "content": "已把网页图片发给老师。"},
            ],
        ) as complete_message, patch.object(
            client, "download_webpage_image", return_value=media_path
        ) as download_image, patch.object(
            client, "_review_local_file_operation"
        ) as review_file:
            reply, media = client.complete_with_media_skills(
                [{"role": "user", "content": "请发送网页图片"}],
                webpage_images=webpage_images,
            )

        self.assertEqual(reply, "已把网页图片发给老师。")
        self.assertEqual(media, [media_path])
        download_image.assert_called_once_with(image_url, {image_url})
        review_file.assert_called_once()
        self.assertEqual(
            complete_message.call_args_list[0].kwargs["tools"][1]["function"][
                "name"
            ],
            "send_web_image",
        )

    def test_explicit_image_request_does_not_accept_unforced_text_reply(self) -> None:
        client = AIClient(
            {
                "api_key": "main-key",
                "base_url": "https://main.test/v1",
                "model": "main-model",
            }
        )
        with patch.object(
            client,
            "_complete_message",
            return_value={
                "role": "assistant",
                "content": "图片生成服务无法生成图片。",
            },
        ):
            with self.assertRaisesRegex(RuntimeError, "tool_choice"):
                client.complete_with_media_skills(
                    [{"role": "user", "content": "请生成一张猫的图片"}]
                )

    def test_explicit_image_requests_are_distinguished_from_questions(self) -> None:
        self.assertTrue(
            AIClient._explicit_image_generation_request(
                [{"role": "user", "content": "请生成一只猫的图片"}]
            )
        )
        self.assertTrue(
            AIClient._explicit_image_generation_request(
                [{"role": "user", "content": "帮我画一只猫"}]
            )
        )
        self.assertFalse(
            AIClient._explicit_image_generation_request(
                [{"role": "user", "content": "为什么无法生成图片？"}]
            )
        )
        self.assertFalse(
            AIClient._explicit_image_generation_request(
                [{"role": "user", "content": "如何生成图片？"}]
            )
        )

    def test_complete_message_sends_forced_tool_choice(self) -> None:
        tool_choice = {
            "type": "function",
            "function": {"name": "send_image"},
        }
        with patch.dict(os.environ, {"PRANA_API_KEY": "test-key"}, clear=True), patch(
            "ai_client.urllib.request.urlopen",
            return_value=FakeResponse(
                {"choices": [{"message": {"role": "assistant", "content": None}}]}
            ),
        ) as open_request:
            AIClient()._complete_message(
                [{"role": "user", "content": "请生成一张猫的图片"}],
                tools=[],
                tool_choice=tool_choice,
            )

        request_payload = json.loads(open_request.call_args.args[0].data)
        self.assertEqual(request_payload["tool_choice"], tool_choice)

    def test_media_skill_does_not_invoke_unknown_tools(self) -> None:
        client = AIClient(
            {
                "api_key": "main-key",
                "base_url": "https://main.test/v1",
                "model": "main-model",
            }
        )
        with patch.object(
            client,
            "_complete_message",
            side_effect=[
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-unknown",
                            "function": {
                                "name": "run_shell",
                                "arguments": "{}",
                            },
                        }
                    ],
                },
                {"role": "assistant", "content": "我无法执行该操作。"},
            ],
        ) as complete_message, patch.object(
            client, "generate_image"
        ) as generate_image:
            reply, media = client.complete_with_media_skills(
                [{"role": "user", "content": "你好"}]
            )

        self.assertEqual(reply, "我无法执行该操作。")
        self.assertEqual(media, [])
        generate_image.assert_not_called()
        self.assertIsNone(
            complete_message.call_args_list[0].kwargs["tool_choice"]
        )

    def test_terminal_command_is_reviewed_before_execution(self) -> None:
        client = AIClient(
            {
                "api_key": "main-key",
                "base_url": "https://main.test/v1",
                "model": "main-model",
                "review_model": "review-model",
            }
        )
        terminal_call = {
            "id": "call-terminal",
            "type": "function",
            "function": {
                "name": "run_terminal_command",
                "arguments": json.dumps(
                    {"terminal": "powershell", "command": "Get-ChildItem"}
                ),
            },
        }
        messages = [{"role": "user", "content": "请列出项目目录"}]
        with patch.object(
            client,
            "_complete_message",
            side_effect=[
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [terminal_call],
                },
                {"role": "assistant", "content": "项目目录已列出。"},
            ],
        ), patch.object(
            AIClient,
            "complete",
            autospec=True,
            return_value='{"decision":"allow","reason":"用户明确要求"}',
        ) as review, patch(
            "ai_client.invoke_terminal_skill",
            return_value='{"output":"README.md"}',
        ) as invoke:
            reply, media = client.complete_with_media_skills(messages)

        self.assertEqual(reply, "项目目录已列出。")
        self.assertEqual(media, [])
        review.assert_called_once()
        self.assertEqual(review.call_args.args[0].model, "review-model")
        review_payload = json.loads(review.call_args.args[1][1]["content"])
        self.assertEqual(
            review_payload["proposed_operation"]["arguments"],
            {"terminal": "powershell", "command": "Get-ChildItem"},
        )
        invoke.assert_called_once_with(
            {"terminal": "powershell", "command": "Get-ChildItem"}
        )

    def test_denied_terminal_command_is_not_executed(self) -> None:
        client = AIClient(
            {
                "api_key": "main-key",
                "base_url": "https://main.test/v1",
                "model": "main-model",
                "review_model": "review-model",
            }
        )
        terminal_call = {
            "id": "call-terminal",
            "function": {
                "name": "run_terminal_command",
                "arguments": '{"terminal":"cmd","command":"dir"}',
            },
        }
        with patch.object(
            client,
            "_complete_message",
            side_effect=[
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [terminal_call],
                },
                {"role": "assistant", "content": "未获批准，未执行。"},
            ],
        ) as complete_message, patch.object(
            AIClient,
            "complete",
            autospec=True,
            return_value='{"decision":"deny","reason":"未明确授权"}',
        ), patch("ai_client.invoke_terminal_skill") as invoke:
            reply, media = client.complete_with_media_skills(
                [{"role": "user", "content": "你好"}]
            )

        self.assertEqual(reply, "未获批准，未执行。")
        self.assertEqual(media, [])
        invoke.assert_not_called()
        self.assertIn(
            "未明确授权",
            complete_message.call_args_list[1].args[0][-1]["content"],
        )

    def test_workspace_skill_is_reviewed_before_execution(self) -> None:
        client = AIClient(
            {
                "api_key": "main-key",
                "base_url": "https://main.test/v1",
                "model": "main-model",
                "review_model": "review-model",
            }
        )
        arguments = {"mode": "content", "query": "Prana"}
        workspace_call = {
            "id": "call-workspace",
            "type": "function",
            "function": {
                "name": "search_workspace_files",
                "arguments": json.dumps(arguments),
            },
        }
        with patch.object(
            client,
            "_complete_message",
            side_effect=[
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [workspace_call],
                },
                {"role": "assistant", "content": "搜索完成。"},
            ],
        ) as complete_message, patch.object(
            AIClient,
            "complete",
            autospec=True,
            return_value='{"decision":"allow","reason":"用户明确要求"}',
        ) as review, patch(
            "ai_client.invoke_workspace_skill",
            return_value='{"results":["main.py"]}',
        ) as invoke:
            reply, media = client.complete_with_media_skills(
                [{"role": "user", "content": "在项目里搜索 Prana"}]
            )

        self.assertEqual(reply, "搜索完成。")
        self.assertEqual(media, [])
        review.assert_called_once()
        self.assertEqual(review.call_args.args[0].model, "review-model")
        review_payload = json.loads(review.call_args.args[1][1]["content"])
        self.assertEqual(
            review_payload["proposed_operation"]["skill"],
            "search_workspace_files",
        )
        self.assertEqual(
            review_payload["proposed_operation"]["arguments"], arguments
        )
        invoke.assert_called_once_with("search_workspace_files", arguments)
        tool_message = complete_message.call_args_list[1].args[0][-1]
        self.assertIn("main.py", tool_message["content"])

    def test_git_skill_is_reviewed_before_execution(self) -> None:
        client = AIClient(
            {
                "api_key": "main-key",
                "base_url": "https://main.test/v1",
                "model": "main-model",
                "review_model": "review-model",
            }
        )
        arguments = {"action": "status"}
        git_call = {
            "id": "call-git",
            "type": "function",
            "function": {
                "name": "read_git_repository",
                "arguments": json.dumps(arguments),
            },
        }
        with patch.object(
            client,
            "_complete_message",
            side_effect=[
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [git_call],
                },
                {"role": "assistant", "content": "Git 状态已读取。"},
            ],
        ) as complete_message, patch.object(
            AIClient,
            "complete",
            autospec=True,
            return_value='{"decision":"allow","reason":"用户明确要求"}',
        ), patch(
            "ai_client.invoke_git_skill",
            return_value='{"output":"M app.py"}',
        ) as invoke:
            reply, media = client.complete_with_media_skills(
                [{"role": "user", "content": "看看 Git 状态"}]
            )

        self.assertEqual(reply, "Git 状态已读取。")
        self.assertEqual(media, [])
        invoke.assert_called_once_with(arguments)
        tool_message = complete_message.call_args_list[1].args[0][-1]
        self.assertIn("M app.py", tool_message["content"])

    def test_denied_workspace_skill_is_not_executed(self) -> None:
        client = AIClient(
            {
                "api_key": "main-key",
                "base_url": "https://main.test/v1",
                "model": "main-model",
                "review_model": "review-model",
            }
        )
        workspace_call = {
            "id": "call-workspace",
            "function": {
                "name": "get_project_structure",
                "arguments": "{}",
            },
        }
        with patch.object(
            client,
            "_complete_message",
            side_effect=[
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [workspace_call],
                },
                {"role": "assistant", "content": "未获批准，未读取项目。"},
            ],
        ) as complete_message, patch.object(
            AIClient,
            "complete",
            autospec=True,
            return_value='{"decision":"deny","reason":"未明确授权"}',
        ), patch("ai_client.invoke_workspace_skill") as invoke:
            reply, media = client.complete_with_media_skills(
                [{"role": "user", "content": "你好"}]
            )

        self.assertEqual(reply, "未获批准，未读取项目。")
        self.assertEqual(media, [])
        invoke.assert_not_called()
        self.assertIn(
            "未明确授权",
            complete_message.call_args_list[1].args[0][-1]["content"],
        )

    def test_workspace_review_fails_closed_without_reviewer_model(self) -> None:
        client = AIClient(
            {
                "api_key": "main-key",
                "base_url": "https://main.test/v1",
                "model": "main-model",
            }
        )

        with self.assertRaisesRegex(RuntimeError, "尚未配置工作区审查模型"):
            client._review_workspace_operation(
                "get_project_structure",
                {},
                [{"role": "user", "content": "看看项目结构"}],
            )

    def test_local_media_skill_uses_user_file_picker(self) -> None:
        client = AIClient(
            {
                "api_key": "main-key",
                "base_url": "https://main.test/v1",
                "model": "main-model",
            }
        )
        local_paths = [Path("picked.png"), Path("picked.mp4")]
        select_local_media = Mock(return_value=local_paths)
        with patch.object(
            client,
            "_complete_message",
            side_effect=[
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-local",
                            "type": "function",
                            "function": {
                                "name": "send_local_media",
                                "arguments": "{}",
                            },
                        }
                    ],
                },
                {"role": "assistant", "content": "已把选中的媒体发送到聊天。"},
            ],
        ) as complete_message, patch.object(
            client, "_review_local_file_operation"
        ) as review_file:
            reply, media = client.complete_with_media_skills(
                [{"role": "user", "content": "请发送本地图片"}],
                local_media_selector=select_local_media,
            )

        self.assertEqual(reply, "已把选中的媒体发送到聊天。")
        self.assertEqual(media, local_paths)
        select_local_media.assert_called_once_with()
        review_file.assert_called_once()
        tool_message = complete_message.call_args_list[1].args[0][-1]
        self.assertIn("picked.png", tool_message["content"])
        self.assertIn("picked.mp4", tool_message["content"])

    def test_file_access_review_fails_closed_without_reviewer_model(self) -> None:
        client = AIClient(
            {
                "api_key": "main-key",
                "base_url": "https://main.test/v1",
                "model": "main-model",
            }
        )

        with self.assertRaisesRegex(RuntimeError, "尚未配置文件访问审查模型"):
            client._review_local_file_operation(
                "send_local_media",
                {},
                [{"role": "user", "content": "请发送本地图片"}],
            )
        with self.assertRaisesRegex(RuntimeError, "尚未配置终端命令审查模型"):
            client._review_terminal_command(
                {"terminal": "cmd", "command": "dir"},
                [{"role": "user", "content": "请列出项目目录"}],
            )

    def test_local_file_skill_is_not_run_without_approved_review(self) -> None:
        client = AIClient(
            {
                "api_key": "main-key",
                "base_url": "https://main.test/v1",
                "model": "main-model",
            }
        )
        select_local_media = Mock(return_value=[Path("picked.png")])
        with patch.object(
            client,
            "_complete_message",
            side_effect=[
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-local",
                            "function": {
                                "name": "send_local_media",
                                "arguments": "{}",
                            },
                        }
                    ],
                },
                {"role": "assistant", "content": "未获批准，未读取文件。"},
            ],
        ) as complete_message:
            reply, media = client.complete_with_media_skills(
                [{"role": "user", "content": "请发送本地图片"}],
                local_media_selector=select_local_media,
            )

        self.assertEqual(reply, "未获批准，未读取文件。")
        self.assertEqual(media, [])
        select_local_media.assert_not_called()
        self.assertIn(
            "尚未配置文件访问审查模型",
            complete_message.call_args_list[1].args[0][-1]["content"],
        )

    def test_file_access_review_uses_configured_model_and_requires_allow(self) -> None:
        client = AIClient(
            {
                "api_key": "main-key",
                "base_url": "https://main.test/v1",
                "model": "main-model",
                "review_model": "review-model",
            }
        )
        with patch.object(
            AIClient,
            "complete",
            autospec=True,
            return_value='{"decision":"allow","reason":"用户明确要求"}',
        ) as complete:
            client._review_local_file_operation(
                "send_local_media",
                {},
                [{"role": "user", "content": "请发送本地图片"}],
            )

        reviewer = complete.call_args.args[0]
        self.assertEqual(reviewer.model, "review-model")
        review_payload = json.loads(complete.call_args.args[1][1]["content"])
        self.assertEqual(review_payload["user_request"], "请发送本地图片")
        self.assertEqual(
            review_payload["proposed_operation"],
            {
                "skill": "send_local_media",
                "effect": (
                    "打开文件选择器让用户选择本地图片或视频，并读取用户选中的文件以在聊天中展示；"
                    "不会搜索其他路径或上传文件。"
                ),
                "arguments": {},
            },
        )

        with patch.object(
            AIClient,
            "complete",
            autospec=True,
            return_value='{"decision":"deny","reason":"未授权"}',
        ):
            with self.assertRaisesRegex(RuntimeError, "未授权"):
                client._review_local_file_operation(
                    "send_local_media",
                    {},
                    [{"role": "user", "content": "你好"}],
                )

        with patch.object(
            AIClient, "complete", autospec=True, return_value="not JSON"
        ):
            with self.assertRaisesRegex(RuntimeError, "无效结果"):
                client._review_local_file_operation(
                    "send_local_media",
                    {},
                    [{"role": "user", "content": "请发送本地图片"}],
                )

    def test_media_fallback_probes_subagents_in_order_with_text_only(self) -> None:
        first_agent: dict[str, object] = {
            "name": "第一助手",
            "description": "分析图片",
            "api_key": "first-key",
            "base_url": "https://first.test/v1",
            "model": "first-model",
        }
        second_agent: dict[str, object] = {
            "name": "第二助手",
            "description": "分析图片和视频",
            "api_key": "second-key",
            "base_url": "https://second.test/v1",
            "model": "second-model",
        }
        client = AIClient(
            {
                "api_key": "main-key",
                "base_url": "https://main.test/v1",
                "model": "main-model",
                "subagents": [first_agent, second_agent],
            }
        )
        media_messages = [
            {"role": "system", "content": "你是主模型"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "描述图片"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "https://example.test/cat.png"},
                    },
                ],
            },
        ]
        probes: list[tuple[str, list[dict[str, object]]]] = []
        delegations: list[tuple[str, list[dict[str, object]]]] = []

        def fake_complete(instance, messages):
            if instance.model == "main-model":
                return "我无法识别图片内容。"
            if len(messages) == 1 and isinstance(
                messages[0].get("content"), str
            ):
                probes.append((instance.model, messages))
                return "否" if instance.model == "first-model" else "是"
            delegations.append((instance.model, messages))
            return "第二助手已分析图片。"

        with patch(
            "ai_client.AIClient.complete",
            autospec=True,
            side_effect=fake_complete,
        ) as complete:
            reply = client.complete_with_media_fallback(
                media_messages, {"image"}
            )

        self.assertEqual(reply, "第二助手已分析图片。")
        self.assertEqual(
            [call.args[0].model for call in complete.call_args_list],
            ["main-model", "first-model", "second-model", "second-model"],
        )
        self.assertEqual(
            [model for model, _messages in probes],
            ["first-model", "second-model"],
        )
        for _model, probe_messages in probes:
            self.assertEqual(len(probe_messages), 1)
            self.assertIsInstance(probe_messages[0]["content"], str)
            self.assertIn("能力探测", probe_messages[0]["content"])
            self.assertNotIn(
                "image_url",
                json.dumps(probe_messages, ensure_ascii=False),
            )
            self.assertNotIn(
                "https://example.test/cat.png",
                json.dumps(probe_messages, ensure_ascii=False),
            )
        self.assertEqual(len(delegations), 1)
        delegated_model, delegated_messages = delegations[0]
        delegated_system = delegated_messages[0]["content"]
        self.assertEqual(delegated_model, "second-model")
        self.assertEqual(delegated_messages[0]["role"], "system")
        self.assertIsInstance(delegated_system, str)
        self.assertIn("第二助手", delegated_system)
        self.assertIn(
            "请直接分析用户提供的图片或视频，并明确说明无法确定的内容。",
            delegated_system,
        )
        self.assertEqual(delegated_messages[1], media_messages[1])
        self.assertIn(
            "https://example.test/cat.png",
            json.dumps(delegated_messages, ensure_ascii=False),
        )

    def test_media_fallback_requires_configured_subagent(self) -> None:
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
            with self.assertRaisesRegex(RuntimeError, "没有配置子代理"):
                client.complete_with_media_fallback(
                    [{"role": "user", "content": "总结视频"}], {"video"}
                )

    def test_media_fallback_reports_no_confirming_subagent(self) -> None:
        agent: dict[str, object] = {
            "name": "唯一助手",
            "description": "分析图片",
            "api_key": "agent-key",
            "base_url": "https://agent.test/v1",
            "model": "agent-model",
        }
        client = AIClient(
            {
                "api_key": "main-key",
                "base_url": "https://main.test/v1",
                "model": "main-model",
                "subagents": [agent],
            }
        )

        def fake_complete(instance, _messages):
            if instance.model == "main-model":
                return "我无法识别图片内容。"
            return "否"

        with patch(
            "ai_client.AIClient.complete",
            autospec=True,
            side_effect=fake_complete,
        ):
            with self.assertRaisesRegex(
                RuntimeError, "没有子代理确认支持"
            ):
                client.complete_with_media_fallback(
                    [
                        {
                            "role": "user",
                            "content": [{"type": "image_url"}],
                        }
                    ],
                    {"image"},
                )

    def test_media_fallback_reports_probe_failure(self) -> None:
        agent: dict[str, object] = {
            "name": "唯一助手",
            "description": "分析图片",
            "api_key": "agent-key",
            "base_url": "https://agent.test/v1",
            "model": "agent-model",
        }
        client = AIClient(
            {
                "api_key": "main-key",
                "base_url": "https://main.test/v1",
                "model": "main-model",
                "subagents": [agent],
            }
        )

        def fake_complete(instance, _messages):
            if instance.model == "main-model":
                return "我无法识别图片内容。"
            raise RuntimeError("探测连接失败")

        with patch(
            "ai_client.AIClient.complete",
            autospec=True,
            side_effect=fake_complete,
        ):
            with self.assertRaises(RuntimeError) as raised:
                client.complete_with_media_fallback(
                    [
                        {
                            "role": "user",
                            "content": [{"type": "image_url"}],
                        }
                    ],
                    {"image"},
                )

        error_text = str(raised.exception)
        self.assertIn("子代理能力探测失败", error_text)
        self.assertIn("探测失败", error_text)
        self.assertIn("唯一助手", error_text)
        self.assertIn("探测连接失败", error_text)

    def test_subagent_configuration_without_media_types_is_valid(self) -> None:
        agent: dict[str, object] = {
            "name": "无媒体类型助手",
            "description": "按需探测媒体能力",
            "api_key": "agent-key",
            "base_url": "https://agent.test/v1",
            "model": "agent-model",
        }
        client = AIClient(
            {
                "api_key": "main-key",
                "base_url": "https://main.test/v1",
                "model": "main-model",
                "subagents": [agent],
            }
        )

        self.assertEqual(client.subagents, [agent])
        AIClient._validate_subagents([agent])

    def test_probe_reply_is_affirmative_conservatively(self) -> None:
        self.assertTrue(AIClient._probe_reply_is_affirmative("是"))
        self.assertTrue(AIClient._probe_reply_is_affirmative("支持"))
        self.assertTrue(AIClient._probe_reply_is_affirmative("可以"))
        self.assertFalse(AIClient._probe_reply_is_affirmative("否"))
        self.assertFalse(AIClient._probe_reply_is_affirmative("不支持"))
        self.assertFalse(AIClient._probe_reply_is_affirmative("无法查看图片"))
        self.assertFalse(AIClient._probe_reply_is_affirmative("not sure"))
        self.assertFalse(AIClient._probe_reply_is_affirmative("不确定"))
        self.assertTrue(AIClient._probe_reply_is_affirmative("Yes, I know I can."))
        self.assertTrue(AIClient._probe_reply_is_affirmative("Note: yes"))

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

        fetch_webpage.assert_called_once_with(
            "https://example.test/article", include_images=True
        )
        self.assertIn("网页正文", content)
        self.assertIn("不可信内容", content)

    def test_create_user_content_returns_webpage_image_candidates(self) -> None:
        with patch.dict(os.environ, {"PRANA_API_KEY": "test-key"}, clear=True), patch(
            "ai_client.AIClient._fetch_webpage",
            return_value=(
                "网页正文",
                [("https://example.test/cat.png", "一只猫")],
            ),
        ):
            content, images = AIClient().create_user_content_with_webpage_images(
                "请发送 https://example.test/article 中的猫图", []
            )

        self.assertIn("网页图片候选", content)
        self.assertIn("一只猫", content)
        self.assertEqual(
            images,
            [{"url": "https://example.test/cat.png", "alt": "一只猫"}],
        )

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

    def test_webpage_fetch_extracts_image_urls_and_alt_text(self) -> None:
        from email.message import Message

        class FakeWebResponse(FakeResponse):
            def __init__(self) -> None:
                super().__init__({})
                self.body = (
                    b'<meta property="og:image" content="/cover.jpg">'
                    b'<p>Hello</p><img src="/images/cat.png" alt="A cat">'
                )
                self.headers = Message()
                self.headers["Content-Type"] = "text/html; charset=utf-8"

            def read(self, _size: int = -1) -> bytes:
                return self.body if _size < 0 else self.body[:_size]

        opener = SimpleNamespace(open=lambda *_args, **_kwargs: FakeWebResponse())
        with (
            patch("ai_client._validate_public_web_url"),
            patch("ai_client.urllib.request.build_opener", return_value=opener),
        ):
            page_text, images = AIClient._fetch_webpage(
                "https://example.test/article", include_images=True
            )

        self.assertEqual(page_text, "Hello")
        self.assertEqual(
            images,
            [
                ("https://example.test/cover.jpg", ""),
                ("https://example.test/images/cat.png", "A cat"),
            ],
        )

    def test_download_webpage_image_only_accepts_listed_urls(self) -> None:
        with patch.dict(os.environ, {"PRANA_API_KEY": "test-key"}, clear=True):
            client = AIClient()
        with patch.object(client, "_download_public_media") as download:
            with self.assertRaisesRegex(ValueError, "列出"):
                client.download_webpage_image(
                    "https://example.test/other.png",
                    {"https://example.test/cat.png"},
                )
        download.assert_not_called()

    def test_download_webpage_image_saves_verified_image(self) -> None:
        image_url = "https://example.test/cat.png"
        image_data = b"\x89PNG\r\n\x1a\nimage-data"
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"PRANA_API_KEY": "test-key"}, clear=True
        ), patch(
            "ai_client.settings_store.settings_file_path",
            return_value=Path(directory) / "settings.json",
        ), patch.object(
            AIClient,
            "_download_public_media",
            return_value=image_data,
        ) as download:
            image_path = AIClient().download_webpage_image(
                image_url, {image_url}
            )
            self.assertEqual(image_path.suffix, ".png")
            self.assertEqual(image_path.read_bytes(), image_data)
            download.assert_called_once_with(
                image_url, 10 * 1024 * 1024, media_type="网页图片"
            )

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
