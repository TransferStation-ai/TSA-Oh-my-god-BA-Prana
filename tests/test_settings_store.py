import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import settings_store


class SettingsStoreTests(unittest.TestCase):
    def test_save_encrypts_api_key_and_loads_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Prana" / "settings.json"

            def protect(api_key: str) -> str:
                return base64.b64encode(
                    f"encrypted:{api_key}".encode("utf-8")
                ).decode("ascii")

            def unprotect(encrypted: str) -> str:
                return base64.b64decode(encrypted).decode("utf-8").removeprefix(
                    "encrypted:"
                )

            with (
                patch("settings_store.settings_file_path", return_value=path),
                patch("settings_store._protect_api_key", side_effect=protect),
                patch("settings_store._unprotect_api_key", side_effect=unprotect),
            ):
                settings_store.save_settings(
                    "private-test-key",
                    "https://example.test/v1",
                    "test-model",
                    [
                        {
                            "name": "图片助手",
                            "description": "图片分析",
                            "api_key": "private-agent-key",
                            "base_url": "https://agent.test/v1",
                            "model": "agent-model",
                        }
                    ],
                    "image-model",
                    "video-model",
                    "review-model",
                )
                saved_text = path.read_text(encoding="utf-8")
                settings = settings_store.load_settings()

        self.assertNotIn("private-test-key", saved_text)
        self.assertNotIn("private-agent-key", saved_text)
        self.assertEqual(
            settings,
            {
                "api_key": "private-test-key",
                "base_url": "https://example.test/v1",
                "model": "test-model",
                "image_model": "image-model",
                "video_model": "video-model",
                "review_model": "review-model",
                "brainstorm_rounds": 10,
                "linked_providers": [],
                "subagents": [
                    {
                        "name": "图片助手",
                        "description": "图片分析",
                        "api_key": "private-agent-key",
                        "base_url": "https://agent.test/v1",
                        "model": "agent-model",
                    }
                ],
            },
        )

    def test_saves_and_loads_brainstorm_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Prana" / "settings.json"

            def protect(api_key: str) -> str:
                return base64.b64encode(
                    f"encrypted:{api_key}".encode("utf-8")
                ).decode("ascii")

            def unprotect(encrypted: str) -> str:
                return base64.b64decode(encrypted).decode("utf-8").removeprefix(
                    "encrypted:"
                )

            with (
                patch("settings_store.settings_file_path", return_value=path),
                patch("settings_store._protect_api_key", side_effect=protect),
                patch("settings_store._unprotect_api_key", side_effect=unprotect),
            ):
                settings_store.save_settings(
                    "main-key",
                    "https://main.test/v1",
                    "main-model",
                    [
                        {
                            "name": "评审",
                            "description": "架构评审",
                            "api_key": "agent-key",
                            "base_url": "https://agent.test/v1",
                            "model": "agent-model",
                            "brainstorm": True,
                        }
                    ],
                    "image-model",
                    "video-model",
                    "review-model",
                    brainstorm_rounds=12,
                    linked_providers=[
                        {
                            "id": "main",
                            "name": "主模型提供商",
                            "base_url": "https://main.test/v1",
                            "api_key": "",
                            "source": "main",
                            "models": [
                                {
                                    "id": "main-model",
                                    "name": "主模型",
                                    "brainstorm": True,
                                }
                            ],
                        },
                        {
                            "id": "open-code",
                            "name": "OpenCode",
                            "base_url": "https://open.test/v1",
                            "api_key": "linked-key",
                            "source": "opencode",
                            "models": [
                                {
                                    "id": "m1",
                                    "name": "M1",
                                    "brainstorm": False,
                                }
                            ],
                        },
                    ],
                )
                saved_text = path.read_text(encoding="utf-8")
                settings = settings_store.load_settings()

        self.assertNotIn("linked-key", saved_text)
        self.assertEqual(settings["brainstorm_rounds"], 12)
        self.assertEqual(settings["linked_providers"][0]["api_key"], "")
        self.assertEqual(
            settings["linked_providers"][1]["api_key"], "linked-key"
        )
        self.assertTrue(settings["subagents"][0]["brainstorm"])

    def test_old_settings_get_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Prana" / "settings.json"
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "api_key": "ciphertext",
                        "base_url": "https://example.test/v1",
                        "model": "model",
                        "subagents": [],
                    }
                ),
                encoding="utf-8",
            )

            with (
                patch("settings_store.settings_file_path", return_value=path),
                patch(
                    "settings_store._unprotect_api_key",
                    return_value="plain-key",
                ),
            ):
                settings = settings_store.load_settings()

        self.assertEqual(settings["brainstorm_rounds"], 10)
        self.assertEqual(settings["linked_providers"], [])


if __name__ == "__main__":
    unittest.main()
