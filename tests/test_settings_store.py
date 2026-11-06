import base64
import json
import tempfile
import unittest

import pytest
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
        current = settings["provider_vault"]["custom:https://example.test/v1"]
        self.assertEqual(current["api_key"], "private-test-key")
        self.assertEqual(current["models"], [])
        self.assertEqual(settings["game"], settings_store.DEFAULT_GAME_SETTINGS)
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
                    "k-agnes",
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
        self.assertEqual(settings["game"], settings_store.DEFAULT_GAME_SETTINGS)

    def test_vault_roundtrip_encrypts_provider_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            with (
                patch("settings_store.settings_file_path", return_value=path),
                patch(
                    "settings_store._protect_api_key",
                    side_effect=lambda key: base64.b64encode(
                        f"cipher:{key}".encode("utf-8")
                    ).decode("ascii"),
                ),
                patch(
                    "settings_store._unprotect_api_key",
                    side_effect=lambda value: base64.b64decode(value)
                    .decode("utf-8")
                    .removeprefix("cipher:"),
                ),
            ):
                settings_store.save_settings(
                    "k-agnes",
                    "https://x.test/v1",
                    "main-model",
                    provider_vault={
                        "linked:agnes": {
                            "name": "Agnes",
                            "base_url": "https://agnes.test/v1",
                            "api_key": "k-agnes",
                            "api_key_hint": "…gnes",
                            "models": [{"id": "m1", "name": "Model 1"}],
                        }
                    },
                    vault_key="linked:agnes",
                )
                disk = path.read_text(encoding="utf-8")
                loaded = settings_store.load_settings()
        self.assertNotIn("k-agnes", disk)
        self.assertIn("api_key_enc", disk)
        entry = loaded["provider_vault"]["linked:agnes"]
        self.assertEqual(entry["api_key"], "k-agnes")
        self.assertEqual(entry["models"], [{"id": "m1", "name": "Model 1"}])


def test_default_modeling_settings():
    value = settings_store.normalize_modeling(None)
    assert value == {
        "blender_path": "",
        "blockbench_path": "",
        "gmod_dir": "",
        "sfm_dir": "",
    }


def test_modeling_normalize_strips_and_rejects_non_dict():
    assert settings_store.normalize_modeling({"blender_path": "  x  "})["blender_path"] == "x"
    with pytest.raises(ValueError, match="建模设置"):
        settings_store.normalize_modeling([])


def test_load_and_save_include_modeling_section(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings_store, "settings_file_path", lambda: tmp_path / "settings.json")
    settings_store.save_settings(
        api_key="k",
        base_url="https://x.test/v1",
        model="m",
        provider_vault={},
        game={"mc_name": "Prana"},
        modeling={"blender_path": " D:\\blender.exe "},
    )
    loaded = settings_store.load_settings()
    assert loaded["modeling"]["blender_path"] == "D:\\blender.exe"
    assert loaded["game"]["mc_name"] == "Prana"


if __name__ == "__main__":
    unittest.main()
