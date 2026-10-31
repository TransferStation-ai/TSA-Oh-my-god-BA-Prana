import threading
from unittest.mock import MagicMock

from main import ChatRequest, PranaApp


class FakeSessionStore:
    def __init__(self, session):
        self.session = dict(session)
        self.update_calls = 0

    def load(self, session_id):
        return dict(self.session)

    def update(self, session_id, mutator):
        self.update_calls += 1
        mutator(self.session)
        return dict(self.session)


def _build_app(tmp_path, permanent_workspace=None):
    app = object.__new__(PranaApp)
    session_id = "a" * 32
    app.active_session_id = session_id
    app.active_session = {"permanent_workspace": permanent_workspace}
    app.state_lock = threading.RLock()
    app.pending_requests = {}
    app.session_store = FakeSessionStore(
        {
            "permanent_workspace": permanent_workspace,
            "messages": [],
        }
    )
    app.ai_client = MagicMock()
    app.ai_client.has_brainstorm_participants = False
    app.ai_client.create_user_content_with_webpage_images.return_value = (
        "hi",
        [],
    )
    app.ai_client.complete_with_media_skills.return_value = ("done", [])

    app._build_request_messages = (
        lambda session, user_message_id: [
            {"role": "user", "content": "hi"}
        ]
    )
    app._set_message_content = (
        lambda current, user_message_id, content: None
    )
    app._append_session_message = lambda *args, **kwargs: None
    app._persist_tool_exchange = lambda *args, **kwargs: None
    app._show_streamed_message = lambda *args, **kwargs: None
    app._request_local_media_selection = lambda: []
    app._handle_reply = lambda *args, **kwargs: None

    refreshed = []
    app._refresh_workspace_indicator = lambda: refreshed.append(True)

    def reload_active_session():
        app.active_session = app.session_store.load(
            app.active_session_id
        )
        app._refresh_workspace_indicator()

    app._reload_active_session = reload_active_session
    app._post_to_ui = lambda callback: callback()

    request = ChatRequest(
        request_id=1,
        session_id=session_id,
        user_message_id="b" * 32,
        display_message="hi",
        attachment_paths=[],
    )
    return app, request, refreshed


def test_process_request_passes_workspace_root_and_provider(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    app, request, _refreshed = _build_app(tmp_path, str(project))

    app._process_request(request)

    kwargs = app.ai_client.complete_with_media_skills.call_args.kwargs
    assert kwargs["permanent_root"] == project.resolve()
    assert kwargs["permanent_root_provider"]() == project.resolve()
    assert callable(kwargs["on_workspace_switched"])
    assert callable(kwargs["should_stop"])


def test_switch_callback_persists_workspace_and_updates_provider(tmp_path):
    app, request, refreshed = _build_app(tmp_path)
    app._process_request(request)

    kwargs = app.ai_client.complete_with_media_skills.call_args.kwargs
    provider = kwargs["permanent_root_provider"]
    callback = kwargs["on_workspace_switched"]
    assert provider() is None

    target = tmp_path / "switched"
    target.mkdir()
    callback(str(target))

    assert app.session_store.session["permanent_workspace"] == str(target)
    assert provider() == target.resolve()
    assert refreshed
