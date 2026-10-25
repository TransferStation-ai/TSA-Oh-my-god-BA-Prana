import ctypes
import json
import unittest
from unittest.mock import Mock, patch

from skills.qq_music import (
    _INPUT,
    _WindowsQQMusicController,
    invoke_qq_music_skill,
    validate_qq_music_arguments,
    validate_qq_music_result_index,
)


class QQMusicSkillTests(unittest.TestCase):
    def test_input_structure_matches_windows_sendinput_layout(self) -> None:
        expected_size = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
        self.assertEqual(ctypes.sizeof(_INPUT), expected_size)

    def test_validates_play_pause_and_search_arguments(self) -> None:
        self.assertEqual(
            validate_qq_music_arguments({"action": "play_pause"}),
            ("play_pause", None),
        )
        self.assertEqual(
            validate_qq_music_arguments(
                {"action": "search", "query": "  青花瓷  "}
            ),
            ("search", "青花瓷"),
        )

    def test_rejects_invalid_or_oversized_search_arguments(self) -> None:
        for arguments in (
            [],
            {"action": "launch"},
            {"action": "play_pause", "query": "青花瓷"},
            {"action": "search"},
            {"action": "search", "query": " "},
            {"action": "search", "query": "x" * 201},
            {"action": "search", "query": "青花瓷\n下一首"},
            {"action": "search", "query": "青花瓷", "extra": True},
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    validate_qq_music_arguments(arguments)

    def test_invokes_controller_and_returns_result(self) -> None:
        controller = Mock()
        controller.execute.return_value = "已搜索"

        result = invoke_qq_music_skill(
            {"action": "search", "query": "青花瓷"},
            controller=controller,
        )

        controller.execute.assert_called_once_with("search", "青花瓷")
        self.assertEqual(
            json.loads(result),
            {
                "application": "QQ 音乐",
                "action": "search",
                "result": "已搜索",
            },
        )

    def test_validates_play_actions_and_result_index(self) -> None:
        self.assertEqual(
            validate_qq_music_arguments(
                {"action": "play_song", "query": "琵琶曲", "result_index": 2}
            ),
            ("play_song", "琵琶曲"),
        )
        self.assertEqual(
            validate_qq_music_result_index(
                {"action": "play_song", "query": "琵琶曲", "result_index": 2}
            ),
            2,
        )
        self.assertEqual(
            validate_qq_music_result_index({"action": "play", "query": "琵琶曲"}),
            1,
        )

    def test_rejects_invalid_result_index(self) -> None:
        for arguments in (
            {"action": "play", "query": "青花瓷", "result_index": 0},
            {"action": "play", "query": "青花瓷", "result_index": 11},
            {"action": "play", "query": "青花瓷", "result_index": True},
            {"action": "play", "query": "青花瓷", "result_index": "1"},
            {"action": "search", "query": "青花瓷", "result_index": 1},
            {"action": "play_pause", "result_index": 1},
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    validate_qq_music_arguments(arguments)

    def test_play_action_passes_result_index_to_controller(self) -> None:
        controller = Mock()
        controller.execute.return_value = "已播放"

        result = invoke_qq_music_skill(
            {"action": "play_song", "query": "琵琶曲", "result_index": 3},
            controller=controller,
        )

        controller.execute.assert_called_once_with("play_song", "琵琶曲", 3)
        self.assertEqual(json.loads(result)["action"], "play_song")

    def test_play_action_uses_search_suggestions_and_verifies_title(self) -> None:
        controller = _WindowsQQMusicController()
        with (
            patch("skills.qq_music.sys.platform", "win32"),
            patch("skills.qq_music.time.sleep"),
            patch.object(controller, "_focus_search_box", return_value=123),
            patch.object(controller, "_clear_and_type_search") as type_search,
            patch.object(controller, "_send_key") as send_key,
            patch.object(
                controller,
                "_wait_for_playback",
                return_value="琵琶曲 - 浅影阿",
            ),
        ):
            result = controller.execute("play_song", "琵琶曲 浅影阿", 2)

        type_search.assert_called_once_with("琵琶曲 浅影阿")
        self.assertEqual(
            [call.args[0] for call in send_key.call_args_list],
            [controller.VK_DOWN, controller.VK_DOWN, controller.VK_RETURN],
        )
        self.assertIn("当前：琵琶曲 - 浅影阿", result)

    def test_play_action_reports_when_playback_is_not_confirmed(self) -> None:
        controller = _WindowsQQMusicController()
        with (
            patch("skills.qq_music.sys.platform", "win32"),
            patch("skills.qq_music.time.sleep"),
            patch.object(controller, "_focus_search_box", return_value=123),
            patch.object(controller, "_clear_and_type_search"),
            patch.object(controller, "_send_key"),
            patch.object(controller, "_wait_for_playback", return_value=None),
            patch.object(controller, "_window_title", return_value="别的歌 - 歌手"),
        ):
            with self.assertRaisesRegex(RuntimeError, "未确认 QQ 音乐开始播放"):
                controller.execute("play", "琵琶曲")

    def test_title_matching_accepts_song_and_artist(self) -> None:
        self.assertTrue(
            _WindowsQQMusicController._title_matches_query(
                "琵琶曲 - 浅影阿", "琵琶曲 浅影阿"
            )
        )
        self.assertTrue(
            _WindowsQQMusicController._title_matches_query(
                "Bury the Light - Victor Borba", "Bury the Light"
            )
        )
        self.assertFalse(
            _WindowsQQMusicController._title_matches_query(
                "晴天 - 周杰伦", "琵琶曲 浅影阿"
            )
        )
        self.assertFalse(
            _WindowsQQMusicController._title_matches_query(
                "晴天 - 周杰伦", "晴天 林俊杰"
            )
        )

    def test_play_pause_checks_running_process_without_requiring_a_window(self) -> None:
        controller = _WindowsQQMusicController()
        with (
            patch("skills.qq_music.sys.platform", "win32"),
            patch.object(
                _WindowsQQMusicController, "_is_qq_music_running", return_value=True
            ),
            patch.object(_WindowsQQMusicController, "_send_key") as send_key,
            patch.object(
                _WindowsQQMusicController,
                "_find_qq_music_window",
                side_effect=AssertionError("play/pause must not require a window"),
            ),
        ):
            result = controller.execute("play_pause")

        self.assertIn("标准媒体播放/暂停按键", result)
        send_key.assert_called_once_with(controller.VK_MEDIA_PLAY_PAUSE)

    def test_play_pause_reports_when_main_process_is_not_running(self) -> None:
        controller = _WindowsQQMusicController()
        with (
            patch("skills.qq_music.sys.platform", "win32"),
            patch.object(
                _WindowsQQMusicController, "_is_qq_music_running", return_value=False
            ),
            patch.object(_WindowsQQMusicController, "_send_key") as send_key,
        ):
            with self.assertRaisesRegex(RuntimeError, "未检测到正在运行的 QQ 音乐主程序"):
                controller.execute("play_pause")

        send_key.assert_not_called()
