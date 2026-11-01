import os
from pathlib import Path
import sys
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from ai_client import AIClient
from main import PranaApp
import settings_store


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


@unittest.skipUnless(sys.platform == "win32", "Windows desktop integration")
class ProviderSidebarTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.settings_path = Path(temporary.name) / "settings.json"
        path_patch = patch.object(
            settings_store, "settings_file_path", return_value=self.settings_path
        )
        path_patch.start()
        self.addCleanup(path_patch.stop)
        env_patch = patch.dict(os.environ, {
            "PRANA_API_KEY": "",
            "PRANA_API_BASE_URL": "https://api.openai.com/v1",
            "PRANA_MODEL": "gpt-4o-mini",
            "PRANA_REVIEW_MODEL": "",
        })
        env_patch.start()
        self.addCleanup(env_patch.stop)
        self.root = tk.Tk()
        self.app: PranaApp
        self.addCleanup(self.close_app)

    def close_app(self):
        for callback in self.root.tk.call("after", "info"):
            self.root.after_cancel(callback)
        if hasattr(self, "app"):
            self.app._on_close()
            self.app.worker.join(timeout=2)
        else:
            self.root.destroy()

    def start_app(self, *, configured=False):
        if configured:
            settings_store.save_settings(
                "existing-openai-key", "https://api.openai.com/v1", "gpt-4o-mini"
            )
        self.app = PranaApp(self.root)
        self.root.update()

    def button(self, parent, prefix):
        matches = [
            child for child in descendants(parent)
            if isinstance(child, tk.Button)
            and str(child.cget("text")).startswith(prefix)
        ]
        self.assertEqual(len(matches), 1, f"Expected one {prefix!r} button")
        return matches[0]

    def dialog(self):
        matches = [
            child for child in self.root.winfo_children()
            if isinstance(child, tk.Toplevel)
        ]
        self.assertEqual(len(matches), 1)
        return matches[0]

    def entries(self, dialog):
        return [child for child in descendants(dialog) if isinstance(child, tk.Entry)]

    @staticmethod
    def fill(entry, value):
        entry.delete(0, tk.END)
        entry.insert(0, value)

    def test_fresh_start_shows_both_providers_and_prefills_without_saving(self):
        # Catches hidden defaults, wrong preset callbacks, or switching on click.
        self.start_app()
        for name, url in (
            ("Agnes AI", "https://apihub.agnes-ai.com/v1"),
            ("OpenAI", "https://api.openai.com/v1"),
            ("Kimi", "https://api.moonshot.cn/v1"),
        ):
            with self.subTest(provider=name):
                button = self.button(self.root, name)
                self.assertTrue(button.winfo_ismapped())
                self.assertLess(button.winfo_rootx(), self.app.canvas.winfo_rootx())
                button.invoke()
                dialog = self.dialog()
                fields = self.entries(dialog)
                self.assertEqual(fields[0].get(), "")
                self.assertEqual(fields[1].get(), url)
                self.assertFalse(self.settings_path.exists())
                self.assertEqual(self.app.ai_client.base_url, "https://api.openai.com/v1")
                self.button(dialog, "取消").invoke()

    def test_switch_requires_new_key_then_persists_and_marks_current_provider(self):
        # Catches sending the original provider's key to a different endpoint.
        self.start_app(configured=True)
        self.button(self.root, "Agnes AI").invoke()
        dialog = self.dialog()
        fields = self.entries(dialog)
        self.assertEqual(fields[2].get(), "")
        self.fill(fields[2], "agnes-chat-test")
        with patch("main.messagebox.showerror"):
            self.button(dialog, "保存").invoke()
        self.assertTrue(dialog.winfo_exists())
        self.assertEqual(settings_store.load_settings()["api_key"], "existing-openai-key")
        self.assertEqual(self.app.ai_client.base_url, "https://api.openai.com/v1")

        self.fill(fields[0], "new-agnes-key")
        self.button(dialog, "保存").invoke()
        restored = AIClient()
        self.assertEqual(restored.base_url, "https://apihub.agnes-ai.com/v1")
        self.assertEqual(restored.api_key, "new-agnes-key")
        self.assertEqual(restored.model, "agnes-chat-test")
        self.assertNotIn("new-agnes-key", self.settings_path.read_text(encoding="utf-8"))
        self.assertIn("当前使用", self.button(self.root, "Agnes AI").cget("text"))
        self.assertNotIn("当前使用", self.button(self.root, "OpenAI").cget("text"))
        self.assertFalse(dialog.winfo_exists())

    def test_same_provider_keeps_key_and_model_when_key_left_blank(self):
        # Catches losing saved credentials on harmless edits or trailing slashes.
        self.start_app(configured=True)
        self.button(self.root, "OpenAI").invoke()
        dialog = self.dialog()
        fields = self.entries(dialog)
        self.assertEqual(fields[2].get(), "gpt-4o-mini")
        self.fill(fields[1], " https://api.openai.com/v1/ ")
        self.fill(fields[2], "another-chat-model")
        self.button(dialog, "保存").invoke()
        restored = AIClient()
        self.assertEqual(restored.api_key, "existing-openai-key")
        self.assertEqual(restored.model, "another-chat-model")
        self.assertIn("当前使用", self.button(self.root, "OpenAI").cget("text"))

    def test_manual_address_change_cannot_bypass_key_isolation(self):
        # Catches checking only the clicked preset rather than the final address.
        self.start_app(configured=True)
        self.app._open_settings()
        dialog = self.dialog()
        fields = self.entries(dialog)
        self.fill(fields[1], "https://custom.example/v1")
        with patch("main.messagebox.showerror"):
            self.button(dialog, "保存").invoke()
        self.assertEqual(self.app.ai_client.base_url, "https://api.openai.com/v1")
        self.assertEqual(settings_store.load_settings()["api_key"], "existing-openai-key")
        self.assertTrue(dialog.winfo_exists())

    def test_sidebar_status_shows_current_linked_provider_name(self):
        # A non-preset provider shows its saved name in the sidebar status label.
        self.start_app(configured=True)
        self.app.ai_client.linked_providers = [
            {
                "id": "agnes",
                "name": "Agnes 链接",
                "base_url": "https://custom.example/v1",
                "api_key": "custom-key",
                "models": [],
            }
        ]
        self.app.ai_client.base_url = "https://custom.example/v1"
        self.app._refresh_provider_sidebar()
        self.assertIn("Agnes 链接", self.app.provider_status.cget("text"))

    def test_cancel_keeps_existing_configuration_and_chat_draft(self):
        self.start_app(configured=True)
        self.app.input.insert("1.0", "保留草稿")
        before = self.settings_path.read_bytes()
        self.button(self.root, "Agnes AI").invoke()
        dialog = self.dialog()
        self.fill(self.entries(dialog)[0], "unused-key")
        self.button(dialog, "取消").invoke()
        self.assertEqual(self.settings_path.read_bytes(), before)
        self.assertEqual(self.app.input.get("1.0", "end-1c"), "保留草稿")
        self.assertIn("当前使用", self.button(self.root, "OpenAI").cget("text"))

    def test_restart_restores_provider_indicator_and_small_window_controls(self):
        settings_store.save_settings(
            "agnes-key", "https://apihub.agnes-ai.com/v1", "chat-model"
        )
        self.start_app()
        self.assertIn("当前使用", self.button(self.root, "Agnes AI").cget("text"))
        width, height = self.root.minsize()
        self.root.geometry(f"{width}x{height}")
        self.root.update()
        for widget in (
            self.button(self.root, "Agnes AI"),
            self.button(self.root, "OpenAI"),
            self.app.input,
            self.app.send_button,
        ):
            self.assertTrue(widget.winfo_ismapped())
            right = widget.winfo_rootx() + widget.winfo_width()
            bottom = widget.winfo_rooty() + widget.winfo_height()
            self.assertLessEqual(right, self.root.winfo_rootx() + width)
            self.assertLessEqual(bottom, self.root.winfo_rooty() + height)


if __name__ == "__main__":
    unittest.main()
