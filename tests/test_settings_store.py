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
        self.assertEqual(settings["api_key"], "private-test-key")
        self.assertEqual(settings["base_url"], "https://example.test/v1")
        self.assertEqual(settings["model"], "test-model")
        self.assertEqual(settings["image_model"], "image-model")
        self.assertEqual(settings["video_model"], "video-model")
        self.assertEqual(settings["review_model"], "review-model")
        self.assertEqual(settings["brainstorm_rounds"], 10)
        self.assertEqual(settings["linked_providers"], [])
        self.assertEqual(
            settings["subagents"],
            [
                {
                    "name": "图片助手",
                    "description": "图片分析",
                    "api_key": "private-agent-key",
                    "base_url": "https://agent.test/v1",
                    "model": "agent-model",
                }
            ],
        )
        vault_entry = settings["provider_vault"]["custom:https://example.test/v1"]
        self.assertEqual(vault_entry["api_key"], "private-test-key")
        self.assertEqual(vault_entry["base_url"], "https://example.test/v1")
        self.assertEqual(vault_entry["image_model"], "image-model")
        self.assertEqual(vault_entry["video_model"], "video-model")
        self.assertEqual(vault_entry["review_model"], "review-model")

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
        self.assertEqual(settings["provider_vault"], {})

    def test_loads_old_settings_without_provider_vault_as_empty(self) -> None:
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

        self.assertEqual(settings["provider_vault"], {})
        self.assertEqual(settings["api_key"], "plain-key")
        self.assertEqual(settings["base_url"], "https://example.test/v1")
        self.assertEqual(settings["model"], "model")

    def test_vault_roundtrip_encrypts_key(self) -> None:
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
                    provider_vault={
                        "linked:agnes": {
                            "name": "Agnes",
                            "base_url": "https://apihub.agnes-ai.com/v1",
                            "api_key": "k-agnes",
                            "api_key_hint": "…agas",
                            "models": [
                                {"id": "agnes-3.0-flash", "name": "Agnes 3.0 Flash"}
                            ],
                            "image_model": "",
                            "video_model": "",
                            "review_model": "",
                        }
                    },
                )
                saved_text = path.read_text(encoding="utf-8")
                disk_vault = json.loads(saved_text)["provider_vault"]
                settings = settings_store.load_settings()

        self.assertIn("api_key_enc", disk_vault["linked:agnes"])
        self.assertNotIn("k-agnes", saved_text)
        entry = settings["provider_vault"]["linked:agnes"]
        self.assertEqual(entry["api_key"], "k-agnes")
        self.assertEqual(entry["api_key_hint"], "…agas")
        self.assertEqual(
            entry["models"],
            [{"id": "agnes-3.0-flash", "name": "Agnes 3.0 Flash"}],
        )
        migrated = settings["provider_vault"]["custom:https://main.test/v1"]
        self.assertEqual(migrated["api_key"], "main-key")

    def test_save_without_vault_migrates_current_provider(self) -> None:
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
                written = settings_store.save_settings(
                    "openai-key",
                    "https://api.openai.com/v1",
                    "gpt-4o-mini",
                )
                entry = written["preset:OpenAI（GPT 官方）"]
                self.assertEqual(entry["api_key"], "openai-key")
                self.assertEqual(entry["models"], [])
                settings = settings_store.load_settings()

        loaded = settings["provider_vault"]["preset:OpenAI（GPT 官方）"]
        self.assertEqual(loaded["api_key"], "openai-key")
        self.assertEqual(loaded["base_url"], "https://api.openai.com/v1")

    def test_save_upsert_keeps_models_when_omitted(self) -> None:
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
                    "key-a",
                    "https://a.test/v1",
                    "model-a",
                    vault_models=[{"id": "m1", "name": "M1"}],
                )
                written = settings_store.save_settings(
                    "key-a",
                    "https://a.test/v1",
                    "model-a",
                )

        self.assertEqual(
            written["custom:https://a.test/v1"]["models"],
            [{"id": "m1", "name": "M1"}],
        )

    def test_normalize_game_defaults_gm_port_and_password(self) -> None:
        normalized = settings_store.normalize_game({})
        self.assertEqual(normalized["gm_port"], "27015")
        self.assertEqual(normalized["gm_password"], "")

    def test_normalize_game_illegal_gm_port_falls_back_to_default(self) -> None:
        normalized = settings_store.normalize_game({"gm_port": "99999"})
        self.assertEqual(normalized["gm_port"], "27015")

    def test_normalize_game_keeps_valid_custom_gm_port(self) -> None:
        normalized = settings_store.normalize_game(
            {"gm_port": "27020", "gm_password": "secret"}
        )
        self.assertEqual(normalized["gm_port"], "27020")
        self.assertEqual(normalized["gm_password"], "secret")


if __name__ == "__main__":
    unittest.main()
