import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "zoom_gpt_voice.py"
spec = importlib.util.spec_from_file_location("zoom_gpt_voice", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def write_events(path: Path, events: list[dict]) -> None:
    path.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")


def test_h20_keys_connection_uses_exact_url_and_customer_bearer(monkeypatch):
    realtime_url = "wss://keys.human20.app/v1/realtime?model=gpt-realtime-2.1"
    monkeypatch.setenv("H20_KEYS_REALTIME_URL", realtime_url)
    monkeypatch.setenv("H20_KEYS_API_KEY", "h20-customer-key")

    url, kwargs = mod.build_websocket_connection()
    header_arg = "additional_headers" if "additional_headers" in kwargs else "extra_headers"

    assert url == realtime_url
    assert kwargs[header_arg]["Authorization"] == "Bearer h20-customer-key"
    assert kwargs[header_arg]["OpenAI-Safety-Identifier"] == "chip-zoom-sigurd"


@pytest.mark.parametrize("missing", ["H20_KEYS_REALTIME_URL", "H20_KEYS_API_KEY"])
def test_h20_keys_connection_fails_closed_when_configuration_is_missing(monkeypatch, missing):
    monkeypatch.setenv("H20_KEYS_REALTIME_URL", "wss://keys.human20.app/v1/realtime")
    monkeypatch.setenv("H20_KEYS_API_KEY", "h20-customer-key")
    monkeypatch.delenv(missing)

    with pytest.raises(RuntimeError, match=missing):
        mod.load_h20_keys_connection()


def test_h20_keys_connection_rejects_direct_upstream_url(monkeypatch):
    direct_upstream = "wss://" + "api.openai" + ".com/v1/realtime"
    monkeypatch.setenv("H20_KEYS_REALTIME_URL", direct_upstream)
    monkeypatch.setenv("H20_KEYS_API_KEY", "h20-customer-key")

    with pytest.raises(RuntimeError, match="H20 Keys"):
        mod.load_h20_keys_connection()


def test_runtime_has_no_codex_or_openai_auth_file_path():
    source = MODULE_PATH.read_text(encoding="utf-8")
    forbidden = ("CODEX_AUTH_PATH", "load_access_token", "auth" + ".json")
    assert all(value not in source for value in forbidden)


@pytest.mark.parametrize(
    "template_name", ["sigurd-gpt-voice@.service", "sigurd-gpt-meet@.service"]
)
def test_service_templates_require_rendered_h20_keys_environment(template_name):
    service = (ROOT / "templates" / template_name).read_text(encoding="utf-8")

    assert "EnvironmentFile=@ENV_FILE@" in service
    assert "Environment=MEETING_SSH_IDENTITY=@SSH_IDENTITY@" in service
    assert "ExecStart=@PYTHON@ @RUNTIME_SCRIPT@" in service
    assert "@MEETING_REMOTE@" in service
    assert "H20_KEYS_API_KEY=" not in service
    assert "CODEX_AUTH_PATH" not in service
    assert "OPENAI_API_KEY" not in service
    assert service.count("@RUNTIME_SCRIPT@ --ssh") == 5
    assert "ExecStartPre=-@PYTHON@ @RUNTIME_SCRIPT@ --ssh" in service


def test_zoom_state_accepts_host_end_or_active_microphone_controls():
    source = (ROOT / "scripts/meeting_state.py").read_text(encoding="utf-8")
    assert "/^(leave|end)$/i" in source
    assert "mute my microphone|unmute my microphone" in source


def test_remote_ssh_gate_accepts_runtime_exec_stream_shape():
    source = (ROOT / "scripts/h20_voice_ssh_gate.py").read_text(encoding="utf-8")
    assert 'argv[2:] if argv[1] == "exec" else argv[1:]' in source
    assert 'command == "parec"' in source
    assert 'command == "paplay"' in source


def test_ssh_identity_is_optional(monkeypatch):
    monkeypatch.delenv("MEETING_SSH_IDENTITY", raising=False)
    assert mod.ssh_command_prefix() == ["ssh"]


def test_ssh_identity_is_applied_with_identities_only(monkeypatch, tmp_path):
    identity = tmp_path / "id_ed25519_h20_meeting_voice"
    identity.write_text("test-only-private-key-placeholder", encoding="utf-8")
    monkeypatch.setenv("MEETING_SSH_IDENTITY", str(identity))

    assert mod.ssh_command_prefix() == [
        "ssh",
        "-i",
        str(identity),
        "-o",
        "IdentitiesOnly=yes",
    ]


@pytest.mark.parametrize("kind", ["missing", "directory"])
def test_ssh_identity_fails_closed_unless_it_is_a_regular_file(monkeypatch, tmp_path, kind):
    identity = tmp_path / "identity"
    if kind == "directory":
        identity.mkdir()
    monkeypatch.setenv("MEETING_SSH_IDENTITY", str(identity))

    with pytest.raises(RuntimeError, match="regular file"):
        mod.ssh_command_prefix()


def test_every_runtime_ssh_lane_uses_shared_identity_helper():
    source = MODULE_PATH.read_text(encoding="utf-8")

    # Owner status, playback, capture, and the systemd ExecStartPre/StopPost wrapper.
    assert source.count('*ssh_command_prefix(),') == 4
    assert source.count('args = ["ssh"]') == 1


def test_service_ssh_wrapper_uses_same_identity_helper(monkeypatch, tmp_path):
    identity = tmp_path / "id_ed25519_h20_meeting_voice"
    identity.write_text("test-only-private-key-placeholder", encoding="utf-8")
    monkeypatch.setenv("MEETING_SSH_IDENTITY", str(identity))
    call = {}

    def fake_execvp(executable, args):
        call.update(executable=executable, args=args)

    monkeypatch.setattr(mod.os, "execvp", fake_execvp)
    mod.exec_ssh(["-T", "bot@example", "true"])

    assert call == {
        "executable": "ssh",
        "args": [
            "ssh",
            "-i",
            str(identity),
            "-o",
            "IdentitiesOnly=yes",
            "-T",
            "bot@example",
            "true",
        ],
    }


def test_installer_renders_portable_runtime(tmp_path):
    home = tmp_path / "service-home"
    home.mkdir()
    env_file = tmp_path / "h20-keys-voice.env"
    env_file.write_text("H20_KEYS_REALTIME_URL=ws://127.0.0.1:18750/v1/realtime\nH20_KEYS_API_KEY=test-only\n", encoding="utf-8")
    env_file.chmod(0o600)
    identity = tmp_path / "id_ed25519_h20_meeting_voice"
    identity.write_text("test-only-private-key-placeholder", encoding="utf-8")
    identity.chmod(0o600)

    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/install_voice_runtime.py"),
            "--home", str(home),
            "--python", sys.executable,
            "--env-file", str(env_file),
            "--meeting-remote", "bot@example",
            "--ssh-identity", str(identity),
        ],
        text=True,
        capture_output=True,
        check=True,
    )

    assert "installed runtime=" in proc.stdout
    assert (home / ".local/bin/sigurd-voice").stat().st_mode & 0o777 == 0o700
    for name in ("sigurd-gpt-voice@.service", "sigurd-gpt-meet@.service"):
        rendered = (home / ".config/systemd/user" / name).read_text(encoding="utf-8")
        assert not any(token in rendered for token in ("@PYTHON@", "@RUNTIME_SCRIPT@", "@ENV_FILE@", "@MEETING_REMOTE@", "@SSH_IDENTITY@"))
        assert f"EnvironmentFile={env_file}" in rendered
        assert f"Environment=MEETING_SSH_IDENTITY={identity}" in rendered
        assert "ExecStart=" + str(Path(sys.executable).resolve()) in rendered


def test_google_provider_builds_meet_specific_runtime():
    env = os.environ.copy()
    env.update({"MEETING_PROVIDER": "google", "MEETING_KEY": "abc-defg-hij"})
    code = (
        "import importlib.util,json;"
        f"s=importlib.util.spec_from_file_location('m',{str(MODULE_PATH)!r});"
        "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
        "print(json.dumps({'label':m.PLATFORM_LABEL,'log':str(m.LOG_PATH),'instructions':m.SYSTEM_INSTRUCTIONS},ensure_ascii=False))"
    )
    proc = subprocess.run([sys.executable, "-c", code], env=env, text=True, capture_output=True, check=True)
    value = json.loads(proc.stdout)
    assert value["label"] == "Google Meet"
    assert "google-abc-defg-hij" in value["log"]
    assert "Google Meet" in value["instructions"]


def test_restart_context_replays_prior_meeting_transcript(tmp_path):
    log = tmp_path / "meeting.jsonl"
    write_events(
        log,
        [
            {"at": "2026-08-06T10:00:00+00:00", "kind": "participant_transcript", "text": "Решили сначала протестировать воронку."},
            {"at": "2026-08-06T10:00:02+00:00", "kind": "silent_turn"},
            {"at": "2026-08-06T10:00:05+00:00", "kind": "participant_transcript", "text": "Закупку трафика пока не запускаем."},
        ],
    )

    context = mod.build_continuity_context(log, max_chars=5000)

    assert "Решили сначала протестировать воронку" in context
    assert "Закупку трафика пока не запускаем" in context
    assert "silent_turn" not in context


def test_restart_context_prioritizes_structured_notes(tmp_path):
    log = tmp_path / "meeting.jsonl"
    write_events(
        log,
        [
            {"at": "2026-08-06T10:00:00+00:00", "kind": "meeting_note", "category": "decision", "summary": "Сначала тестируем лендинг", "owner": "Chip", "deadline": "не указан"},
            {"at": "2026-08-06T10:00:05+00:00", "kind": "participant_transcript", "text": "Окей."},
        ],
    )

    context = mod.build_continuity_context(log, max_chars=5000)

    assert "ЗАФИКСИРОВАННЫЕ ПУНКТЫ" in context
    assert "decision" in context
    assert "Сначала тестируем лендинг" in context
    assert "Chip" in context


def test_context_is_bounded_but_keeps_latest_transcript(tmp_path):
    log = tmp_path / "meeting.jsonl"
    events = [
        {"at": f"2026-08-06T10:{i:02d}:00+00:00", "kind": "participant_transcript", "text": f"Старая реплика номер {i} " + "я" * 80}
        for i in range(20)
    ]
    events.append({"at": "2026-08-06T11:00:00+00:00", "kind": "participant_transcript", "text": "САМАЯ ПОСЛЕДНЯЯ РЕПЛИКА"})
    write_events(log, events)

    context = mod.build_continuity_context(log, max_chars=700)

    assert len(context) <= 700
    assert "САМАЯ ПОСЛЕДНЯЯ РЕПЛИКА" in context


def test_session_instructions_force_use_of_persistent_context(tmp_path):
    log = tmp_path / "meeting.jsonl"
    write_events(log, [{"kind": "participant_transcript", "text": "Обсуждали склад Amy."}])

    instructions = mod.build_session_instructions(log)

    assert "Обсуждали склад Amy" in instructions
    assert "не утверждай, что записи или контекста нет" in instructions
    assert "Прямое обращение всегда имеет приоритет" in instructions
    assert "коротким подтверждением" in instructions
    assert "AGENDA" in instructions
    assert "update_agenda_item" in {tool["name"] for tool in mod.build_session_payload(log)["tools"]}


def test_realtime_usage_receipt_is_preserved_exactly():
    usage = {"total_tokens": 123, "input_token_details": {"audio_tokens": 45}}
    assert mod.extract_realtime_usage({"usage": usage}) == usage
    assert mod.extract_realtime_usage({}) is None


def test_normalize_agenda_update_keeps_state():
    update = mod.normalize_agenda_update(
        json.dumps(
            {
                "item": "Выбрать канал запуска",
                "status": "decided",
                "evidence": "Запускаем через Telegram",
                "next_step": "Chip публикует завтра",
            },
            ensure_ascii=False,
        )
    )
    assert update == {
        "item": "Выбрать канал запуска",
        "status": "decided",
        "evidence": "Запускаем через Telegram",
        "next_step": "Chip публикует завтра",
    }


def test_agenda_updates_are_restored_in_continuity(tmp_path):
    log = tmp_path / "meeting.jsonl"
    write_events(log, [{"kind": "agenda_update", "item": "Цена", "status": "open", "evidence": "", "next_step": "Назвать диапазон"}])
    context = mod.build_continuity_context(log)
    assert "AGENDA" in context
    assert "[open] Цена" in context
    assert "next=Назвать диапазон" in context


def test_normalize_meeting_note_keeps_structured_fields():
    note = mod.normalize_meeting_note(
        json.dumps(
            {
                "category": "action",
                "summary": "Проверить воронку",
                "owner": "Chip",
                "deadline": "завтра",
                "evidence": "Сначала проверим воронку",
            },
            ensure_ascii=False,
        )
    )

    assert note == {
        "category": "action",
        "summary": "Проверить воронку",
        "owner": "Chip",
        "deadline": "завтра",
        "evidence": "Сначала проверим воронку",
    }


def test_normalize_meeting_note_rejects_malformed_json():
    assert mod.normalize_meeting_note("not-json") is None


def test_session_payload_exposes_note_and_deep_memory_tools(tmp_path):
    log = tmp_path / "meeting.jsonl"
    write_events(log, [{"kind": "participant_transcript", "text": "Решили проверить воронку."}])

    payload = mod.build_session_payload(log)

    assert "Решили проверить воронку" in payload["instructions"]
    assert {tool["name"] for tool in payload["tools"]} == {"wait_for_user", "record_meeting_note", "update_agenda_item", "consult_hermes"}


def test_private_owner_room_requires_exactly_chip_and_sigurd():
    assert mod.is_private_owner_room({"inMeeting": True, "participantCount": 2, "hasChip": True, "hasSigurd": True})
    assert not mod.is_private_owner_room({"inMeeting": True, "participantCount": 3, "hasChip": True, "hasSigurd": True})
    assert not mod.is_private_owner_room({"inMeeting": True, "participantCount": 2, "hasChip": False, "hasSigurd": True})


def test_continuity_exclusion_removes_synthetic_test_events(tmp_path):
    log = tmp_path / "meeting.jsonl"
    write_events(log, [
        {"at": "2026-08-06T11:03:10+00:00", "kind": "participant_transcript", "text": "СИНТЕТИЧЕСКИЙ ТЕСТ ВОРОНКИ"},
        {"at": "2026-08-06T11:03:11+00:00", "kind": "meeting_note", "category": "decision", "summary": "СИНТЕТИЧЕСКОЕ РЕШЕНИЕ"},
        {"at": "2026-08-06T11:06:00+00:00", "kind": "participant_transcript", "text": "РЕАЛЬНАЯ РЕПЛИКА"},
        {"kind": "continuity_exclude", "from_at": "2026-08-06T11:03:00+00:00", "to_at": "2026-08-06T11:05:59+00:00"},
    ])

    context = mod.build_continuity_context(log)

    assert "СИНТЕТИЧЕСК" not in context
    assert "РЕАЛЬНАЯ РЕПЛИКА" in context


def test_owner_only_general_memory_is_injected_into_session(tmp_path):
    log = tmp_path / "meeting.jsonl"
    memory = tmp_path / "MEMORY.md"
    user = tmp_path / "USER.md"
    write_events(log, [])
    memory.write_text("Проект: Человек 2.0", encoding="utf-8")
    user.write_text("Владелец: Евгений", encoding="utf-8")

    general = mod.load_general_memory(memory, user)
    payload = mod.build_session_payload(log, general_memory=general)

    assert "Проект: Человек 2.0" in payload["instructions"]
    assert "Владелец: Евгений" in payload["instructions"]
    assert "ОБЩАЯ ПАМЯТЬ HERMES" in payload["instructions"]


def test_fast_general_memory_excludes_sensitive_lines(tmp_path):
    memory = tmp_path / "MEMORY.md"
    user = tmp_path / "USER.md"
    memory.write_text("Проект: Человек 2.0\nAPI token: sk-secret\nСервер: 10.20.30.40", encoding="utf-8")
    user.write_text("Имя: Евгений\nEmail: owner@example.com\nТелефон: +79991234567", encoding="utf-8")

    general = mod.load_general_memory(memory, user)

    assert "Проект: Человек 2.0" in general
    assert "Имя: Евгений" in general
    assert "sk-secret" not in general
    assert "10.20.30.40" not in general
    assert "owner@example.com" not in general
    assert "+79991234567" not in general
