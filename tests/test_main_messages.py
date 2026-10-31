import unittest

import main


class BuildRequestMessagesTests(unittest.TestCase):
    def test_includes_tool_result_message(self) -> None:
        tool_call = {
            "id": "call_1",
            "type": "function",
            "function": {"name": "get_system_time", "arguments": "{}"},
        }
        user_message = {
            "id": "u1",
            "role": "user",
            "content": "u1",
            "in_api": True,
        }
        tool_calls_message = {
            "id": "a1",
            "role": "assistant",
            "content": "",
            "tool_calls": [tool_call],
            "in_api": True,
        }
        tool_result_message = {
            "id": "t1",
            "role": "tool",
            "tool_call_id": "call_1",
            "content": '{"time": "20:00"}',
            "in_api": True,
        }
        session: dict[str, object] = {
            "messages": [
                user_message,
                tool_calls_message,
                tool_result_message,
            ]
        }
        messages = main.PranaApp._build_request_messages(session, "u1")

        roles = [m["role"] for m in messages]
        self.assertEqual(roles, ["system", "user", "assistant", "tool"])
        assistant_message = messages[2]
        self.assertEqual(assistant_message["tool_calls"], [tool_call])
        tool_message = messages[3]
        self.assertEqual(tool_message["tool_call_id"], "call_1")
        self.assertEqual(tool_message["content"], '{"time": "20:00"}')

    def test_skips_excluded_and_missing_content_messages(self) -> None:
        excluded_message = {
            "id": "a-x",
            "role": "assistant",
            "content": "错误信息",
            "in_api": False,
        }
        user_message = {
            "id": "u1",
            "role": "user",
            "content": "你好",
            "in_api": True,
        }
        session: dict[str, object] = {
            "messages": [excluded_message, user_message]
        }
        messages = main.PranaApp._build_request_messages(session, "u1")

        self.assertEqual(
            [m["role"] for m in messages],
            ["system", "user"],
        )

    def test_returns_empty_when_user_message_not_found(self) -> None:
        session: dict[str, object] = {
            "messages": [{"id": "u1", "role": "user", "content": "x"}]
        }
        self.assertEqual(
            main.PranaApp._build_request_messages(session, "missing"),
            [],
        )

    def test_tool_result_without_call_id_is_still_included(self) -> None:
        user_message = {
            "id": "u1",
            "role": "user",
            "content": "x",
            "in_api": True,
        }
        orphan_tool = {"id": "t", "role": "tool", "content": "r", "in_api": True}
        session: dict[str, object] = {
            "messages": [user_message, orphan_tool]
        }
        messages = main.PranaApp._build_request_messages(session, "u1")
        roles = [m["role"] for m in messages]
        self.assertEqual(roles, ["system", "user"])


    def test_system_prompt_mentions_coding_agent(self) -> None:
        from main import SYSTEM_PROMPT

        assert "run_shell_command" in SYSTEM_PROMPT
        assert "scratch" in SYSTEM_PROMPT or "临时" in SYSTEM_PROMPT
        assert "switch_workspace" in SYSTEM_PROMPT


if __name__ == "__main__":
    unittest.main()
