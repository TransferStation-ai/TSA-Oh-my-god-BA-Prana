import base64
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
                            "media_types": ["image"],
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
                "subagents": [
                    {
                        "name": "图片助手",
                        "description": "图片分析",
                        "api_key": "private-agent-key",
                        "base_url": "https://agent.test/v1",
                        "model": "agent-model",
                        "media_types": ["image"],
                    }
                ],
            },
        )


if __name__ == "__main__":
    unittest.main()
