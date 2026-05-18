"""Tests for ephemeral cross-session recall preflight."""

from types import SimpleNamespace

from gateway.session_recall import (
    build_cross_session_recall_prompt,
    cross_session_recall_enabled,
)


class FakeSessionDB:
    def __init__(self):
        self.sessions = {
            "s1": {
                "id": "s1",
                "source": "telegram",
                "user_id": "617744661",
                "title": "Project Flow fix",
                "started_at": 1_700_000_000,
                "last_active": 1_700_000_100,
                "preview": "обсуждали project-flow",
            },
            "s2": {
                "id": "s2",
                "source": "telegram",
                "user_id": "other-user",
                "title": "Other private session",
                "started_at": 1_700_000_200,
                "last_active": 1_700_000_300,
                "preview": "private unrelated",
            },
            "s3": {
                "id": "s3",
                "source": "telegram",
                "user_id": "617744661",
                "title": "Recent promise",
                "started_at": 1_700_000_400,
                "last_active": 1_700_000_500,
                "preview": "нужно вернуться к проверке",
            },
        }
        self.messages = {
            "s1": [
                {"role": "user", "content": "почини project-flow"},
                {"role": "assistant", "content": "сделаю минимальный diff и проверю тестами"},
            ],
            "s3": [
                {"role": "assistant", "content": "нужно будет вернуться и доделать проверку"},
            ],
        }

    def search_messages(self, **kwargs):
        assert kwargs["source_filter"] == ["telegram"]
        return [
            {
                "session_id": "s1",
                "snippet": ">>>project-flow<<< remembered detail",
                "session_started": self.sessions["s1"]["started_at"],
            },
            {
                "session_id": "s2",
                "snippet": ">>>project-flow<<< private detail from another user",
                "session_started": self.sessions["s2"]["started_at"],
            },
        ]

    def list_sessions_rich(self, **kwargs):
        assert kwargs["source"] == "telegram"
        return [self.sessions["s2"], self.sessions["s3"]]

    def get_session(self, session_id):
        return self.sessions.get(session_id)

    def get_messages_as_conversation(self, session_id):
        return self.messages.get(session_id, [])


def _source(**overrides):
    data = {
        "platform": SimpleNamespace(value="telegram"),
        "chat_type": "dm",
        "user_id": "617744661",
        "external_safe_mode": False,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_cross_session_recall_prompt_filters_to_same_private_user():
    prompt = build_cross_session_recall_prompt(
        db=FakeSessionDB(),
        source=_source(),
        current_session_id="current",
        user_message="что мы решили по project-flow проверке?",
    )

    assert prompt is not None
    assert "Cross-session recall preflight" in prompt
    assert "Project Flow fix" in prompt
    assert "project-flow remembered detail" in prompt
    assert "Recent promise" in prompt
    assert "доделать проверку" in prompt
    assert "Other private session" not in prompt
    assert "another user" not in prompt
    assert "SUBCONSCIOUS" not in prompt


def test_cross_session_recall_skips_shared_group_contexts():
    prompt = build_cross_session_recall_prompt(
        db=FakeSessionDB(),
        source=_source(chat_type="group"),
        current_session_id="current",
        user_message="project-flow",
    )

    assert prompt is None


def test_cross_session_recall_can_be_disabled_by_config():
    assert cross_session_recall_enabled({}) is True
    assert cross_session_recall_enabled({"cross_session_recall": False}) is False
    assert cross_session_recall_enabled({"gateway": {"cross_session_recall": {"enabled": "off"}}}) is False
