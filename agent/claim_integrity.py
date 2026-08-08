"""Deterministic guard against unsupported completion and live-state claims.

The model is allowed to be concise, but it may not turn a file edit into a
claim about live behaviour.  This module inspects only the current turn's tool
trace and fails closed when the visible wording is stronger than the evidence.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable


_STRONG_CLAIM = re.compile(
    r"(?iu)\b(?:"
    r"исправил(?:а|и)?|починил(?:а|и)?|готово|сделано|"
    r"установил(?:а|и)?|настроил(?:а|и)?|обновил(?:а|и)?|"
    r"запустил(?:а|и)?|развернул(?:а|и)?|проверил(?:а|и)?|"
    r"починен(?:о|а|ы)?|исправлен(?:о|а|ы)?|"
    r"получилось|выполнено|внедрено|устранено|решено|"
    r"fixed|done|resolved|completed|deployed|installed|configured|verified"
    r")\b"
)

_ABSOLUTE_OR_LIVE = re.compile(
    r"(?iu)(?:"
    r"\bбольше\b.{0,24}\b(?:не|никогда)\b|"
    r"\bтеперь\b.{0,48}\b(?:работает|исправлен|готов|можно|не\s+будет|не\s+нужно)\b|"
    r"\bжив(?:ое|ой|ом|ую)\b|\bproduction\b|\bprod\b|\bв\s+прод(?:е|акшн)?\b|"
    r"\b(?:gateway|сервис|бот)\b.{0,32}\b(?:работает|запущен|исправлен|обновлен|готов)\b|"
    r"\b(?:gateway|service|bot)\b.{0,32}\b(?:works|active|ready|fixed|updated)\b|"
    r"\b(?:всё|все)\s+работает\b|"
    r"\bне\s+(?:повторится|буду)\b|\bnever\s+again\b|\blive\b"
    r")"
)

_PROMISE_CLAIM = re.compile(
    r"(?iu)(?:\bбольше\b.{0,24}\b(?:не|никогда)\b|"
    r"\bне\s+(?:повторится|буду|обману|совру|повторю|допущу)\b|"
    r"\b(?:обещаю|гарантирую)\b|\bnever\s+again\b|"
    r"\b(?:i\s+will\s+not|i\s+won't)\b)"
)

_LIVE_ACTION = re.compile(
    r"(?iu)\b(?:установил(?:а|и)?|настроил(?:а|и)?|запустил(?:а|и)?|"
    r"развернул(?:а|и)?|deployed|installed|configured)\b"
)

_FUNCTIONAL_CLAIM = re.compile(
    r"(?iu)(?:\bтест(?:ы|ирование)?\b.{0,24}\b(?:прош|pass)|"
    r"\bпроверил(?:а|и)?\b|\bverified\b|\bисправил(?:а|и)?\s+(?:код|баг|ошиб)|"
    r"\bпочинил(?:а|и)?\b|\bготово\b|\bсделано\b|\bполучилось\b|"
    r"\b(?:выполнено|внедрено|устранено|решено)\b|"
    r"\b(?:fixed|done|resolved|completed)\b)"
)

_ARTIFACT_ONLY_CLAIM = re.compile(
    r"(?iu)(?:"
    r"\b(?:изменил|обновил|добавил|исправил)(?:а|и)?\b.{0,50}"
    r"\b(?:файл|инструкц|правил|skill|prompt|конфиг)\b|"
    r"\b(?:файл|инструкц|правил|skill|prompt|конфиг)\b.{0,50}"
    r"\b(?:изменен|обновлен|исправлен|перечитан)"
    r")"
)

_TEST_COMMAND = re.compile(
    r"(?iu)(?:\bpytest\b|\bpython(?:3)?\s+-m\s+(?:pytest|unittest|compileall|py_compile)\b|"
    r"\b(?:ruff|mypy|eslint|tsc)\b|"
    r"\b(?:npm|pnpm|yarn|go|cargo)\s+(?:test|check|build|lint)\b|"
    r"\bmake\s+(?:test|check|build|lint)\b|"
    r"(?:^|\s)(?:\./|/)?\S*(?:verify|smoke|test)[-_]\S*)"
)

_LIVE_PROBE_COMMAND = re.compile(
    r"(?iu)(?:\b(?:curl|wget)\s+[^\n]*(?:https?://|/health\b)|"
    r"\bsystemctl\s+(?:is-active|status)\s+\S+|\bss\s+-[a-z]*l\b|"
    r"(?:^|\s)(?:\./|/)?\S*(?:health-check|smoke-test|readback|probe)[-_\w.]*)"
)

_TERMINAL_MUTATION_COMMAND = re.compile(
    r"(?iu)(?:\b(?:cp|mv|install|rm|mkdir|chmod|chown)\b|"
    r"systemctl\s+(?:restart|start|stop|enable|disable)\b|"
    r"git\s+(?:push|merge|rebase|cherry-pick|commit)\b|"
    r"(?:pip|uv\s+pip|apt|dnf|yum|npm|pnpm|yarn)\s+(?:install|remove|uninstall|upgrade)\b)"
)
_SHELL_EVIDENCE_MASKING = re.compile(
    r"(?iu)(?:\|\||;|\bset\s+\+e\b|"
    r"\b(?:python(?:3)?|bash|sh)\s+-c\b|\beval\b|"
    r"#[^\n]*(?:health|smoke|readback|probe|pytest|test)|"
    r"\b(?:echo|printf)\b[^\n]*(?:health|smoke|readback|probe|pytest|test))"
)
_RESULT_FAILURE = re.compile(
    r"(?iu)(?:traceback\s+\(most\s+recent\s+call\s+last\)|"
    r"(?:^|\s)[1-9][0-9]*\s+failed\b|\bFAILED\s+[^\n]+|"
    r"connection\s+refused|\bunhealthy\b)"
)

_CHANGE_CLAIM = re.compile(
    r"(?iu)\b(?:исправил(?:а|и)?|починил(?:а|и)?|установил(?:а|и)?|"
    r"настроил(?:а|и)?|обновил(?:а|и)?|запустил(?:а|и)?|"
    r"развернул(?:а|и)?|fixed|deployed|installed|configured)\b"
)

_MUTATION_TOOLS = {
    "patch",
    "write_file",
    "skill_manage",
    "memory",
    "cronjob",
}
_ARTIFACT_READBACK_TOOLS = {"read_file", "search_files"}
_SYNTHETIC_USER_FLAGS = {
    "_verification_stop_synthetic",
    "_pre_verify_synthetic",
}


_TARGET_TOKEN_STOP = {
    "active", "after", "before", "command", "configured", "deployed",
    "done", "fixed", "health", "https", "installed", "live", "localhost",
    "output", "passed", "production", "result", "status", "success",
    "check", "system", "verified", "works", "готово", "живое", "живой", "после", "проверил",
    "проходит", "работает", "результат", "система", "успешно", "исправил",
}
_GENERIC_TARGET_TOKENS = {"bot", "gateway", "service", "сервис"}


@dataclass(frozen=True)
class Evidence:
    mutations: int = 0
    artifact_readbacks: int = 0
    functional_verifications: int = 0
    live_verifications: int = 0
    latest_mutation_index: int = -1
    latest_artifact_index: int = -1
    latest_functional_index: int = -1
    latest_live_index: int = -1
    mutation_targets: frozenset[str] = frozenset()
    artifact_targets: frozenset[str] = frozenset()
    functional_targets: frozenset[str] = frozenset()
    live_targets: frozenset[str] = frozenset()

    @property
    def artifact_after_mutation(self) -> bool:
        return (
            self.mutations > 0
            and self.artifact_readbacks > 0
            and self.latest_artifact_index >= self.latest_mutation_index
        )

    @property
    def functional_after_mutation(self) -> bool:
        return (
            self.mutations > 0
            and self.functional_verifications > 0
            and self.latest_functional_index >= self.latest_mutation_index
        )

    @property
    def live_after_mutation(self) -> bool:
        return (
            self.mutations > 0
            and self.live_verifications > 0
            and self.latest_live_index >= self.latest_mutation_index
        )


def _tool_name(value: Any) -> str:
    text = str(value or "").strip()
    return text.rsplit(".", 1)[-1]


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(value)


def _target_tokens(value: Any) -> frozenset[str]:
    tokens = set(re.findall(r"(?iu)[a-zа-яё0-9]+", _text(value).lower()))
    return frozenset(
        token
        for token in tokens
        if (len(token) >= 4 or token == "bot")
        and token not in _TARGET_TOKEN_STOP
        and not token.isdigit()
    )


def _result_succeeded(content: Any) -> bool:
    payload = _as_dict(content)
    if payload:
        if payload.get("success") is False or payload.get("ok") is False:
            return False
        exit_code = payload.get("exit_code")
        if type(exit_code) is int and exit_code != 0:
            return False
        status = str(payload.get("status") or "").lower()
        if status in {"error", "failed", "failure", "timeout", "cancelled"}:
            return False
        error = payload.get("error")
        if error not in {None, "", False}:
            return False
        if _RESULT_FAILURE.search(_text(payload.get("output") or "")):
            return False
        return True

    lowered = _text(content).lower()
    if _RESULT_FAILURE.search(lowered):
        return False
    return not any(
        marker in lowered
        for marker in ("tool_error", "traceback", "exit_code\": 1", "failed:")
    )


def _current_turn(messages: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = list(messages)
    start = -1
    for index, message in enumerate(rows):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        if any(message.get(flag) for flag in _SYNTHETIC_USER_FLAGS):
            continue
        start = index
    return rows[start + 1 :]


def collect_evidence(messages: Iterable[dict[str, Any]]) -> Evidence:
    turn = _current_turn(messages)
    calls: dict[str, tuple[int, str, dict[str, Any]]] = {}
    mutations = artifact = functional = live = 0
    latest_mutation = latest_artifact = latest_functional = latest_live = -1
    mutation_targets: set[str] = set()
    artifact_targets: set[str] = set()
    functional_targets: set[str] = set()
    live_targets: set[str] = set()

    for index, message in enumerate(turn):
        if not isinstance(message, dict):
            continue
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                function = call.get("function") or {}
                if not isinstance(function, dict):
                    function = {}
                call_id = str(call.get("id") or "")
                calls[call_id] = (
                    index,
                    _tool_name(function.get("name")),
                    _as_dict(function.get("arguments")),
                )
            continue
        if message.get("role") != "tool":
            continue

        call_id = str(message.get("tool_call_id") or message.get("id") or "")
        call_index, call_name, arguments = calls.get(
            call_id,
            (index, _tool_name(message.get("name")), {}),
        )
        if not _result_succeeded(message.get("content")):
            continue

        effect_disposition = str(message.get("effect_disposition") or "").lower()
        if call_name in _MUTATION_TOOLS and effect_disposition != "none":
            mutations += 1
            latest_mutation = max(latest_mutation, call_index)
            mutation_targets.update(_target_tokens(arguments))
            mutation_targets.update(_target_tokens(message.get("content")))
            payload = _as_dict(message.get("content"))
            if payload.get("verified") is True:
                artifact += 1
                latest_artifact = max(latest_artifact, index)
                artifact_targets.update(_target_tokens(arguments))
                artifact_targets.update(_target_tokens(message.get("content")))

        if call_name in _ARTIFACT_READBACK_TOOLS:
            artifact += 1
            latest_artifact = max(latest_artifact, index)
            artifact_targets.update(_target_tokens(arguments))
            artifact_targets.update(_target_tokens(message.get("content")))

        if call_name == "terminal":
            command = str(arguments.get("command") or "")
            if _TERMINAL_MUTATION_COMMAND.search(command):
                mutations += 1
                latest_mutation = max(latest_mutation, call_index)
                mutation_targets.update(_target_tokens(command))
                mutation_targets.update(_target_tokens(message.get("content")))
            if _SHELL_EVIDENCE_MASKING.search(command):
                continue
            if _TEST_COMMAND.search(command):
                functional += 1
                latest_functional = max(latest_functional, index)
                functional_targets.update(_target_tokens(command))
                functional_targets.update(_target_tokens(message.get("content")))
            if _LIVE_PROBE_COMMAND.search(command):
                live += 1
                latest_live = max(latest_live, index)
                live_targets.update(_target_tokens(command))
                live_targets.update(_target_tokens(message.get("content")))

    return Evidence(
        mutations=mutations,
        artifact_readbacks=artifact,
        functional_verifications=functional,
        live_verifications=live,
        latest_mutation_index=latest_mutation,
        latest_artifact_index=latest_artifact,
        latest_functional_index=latest_functional,
        latest_live_index=latest_live,
        mutation_targets=frozenset(mutation_targets),
        artifact_targets=frozenset(artifact_targets),
        functional_targets=frozenset(functional_targets),
        live_targets=frozenset(live_targets),
    )


def _blocked_response(reason: str, evidence: Evidence) -> str:
    confirmed: list[str] = []
    if evidence.mutations:
        confirmed.append("инструменты этого хода изменяли файлы или настройки")
    if evidence.artifact_after_mutation:
        confirmed.append("изменения файлов перечитаны")
    if evidence.functional_after_mutation:
        confirmed.append("после изменений прошла функциональная проверка")
    if evidence.live_after_mutation:
        confirmed.append("после изменений прошла живая проверка")

    if reason == "unprovable_future_promise":
        missing = "обещание о всех будущих ответах невозможно доказать текущей проверкой"
    elif reason == "missing_target_binding":
        missing = "проверка не привязана к названному объекту изменения"
    elif reason == "missing_live_verification":
        missing = "живое поведение не подтверждено"
    elif reason == "missing_functional_verification":
        missing = "проверка результата не подтверждена"
    else:
        missing = "само заявленное действие не подтверждено инструментами этого хода"

    confirmed_text = "; ".join(confirmed) if confirmed else "доказательств результата в этом ходе нет"
    return (
        "⚠️ **Ответ заблокирован защитой от неподтверждённых утверждений.**\n\n"
        "Я попытался заявить о результате сильнее, чем подтверждают инструменты. "
        f"Подтверждено: {confirmed_text}. Не подтверждено: {missing}.\n\n"
        "Результат нельзя считать готовым, пока не появится проверка нужного уровня."
    )


def claim_integrity_enabled() -> bool:
    """Return whether the fail-closed final-response guard is enabled."""

    import os

    raw = os.getenv("HERMES_CLAIM_INTEGRITY_GUARD")
    if raw is not None:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    try:
        from hermes_cli.config import cfg_get, load_config

        return bool(cfg_get(load_config(), "agent", "claim_integrity_guard", default=False))
    except Exception:
        return False


def claim_guarded_callbacks(stream_callback, stream_delta_callback, interim_callback):
    """Buffer assistant text/interim output until final claim verification."""

    try:
        if claim_integrity_enabled():
            return None, None, None
        return stream_callback, stream_delta_callback, interim_callback
    except Exception:
        return None, None, None


def _live_target_bound(text: str, evidence: Evidence, *, require_mutation: bool) -> bool:
    return _result_target_bound(
        text,
        evidence,
        evidence.live_targets,
        require_mutation=require_mutation,
    )


def _result_target_bound(
    text: str,
    evidence: Evidence,
    verification_targets: frozenset[str],
    *,
    require_mutation: bool,
) -> bool:
    claim_targets = _target_tokens(text)
    shared = claim_targets & verification_targets
    if not claim_targets or not shared:
        return False
    claim_specific = claim_targets - _GENERIC_TARGET_TOKENS
    if claim_specific and not (shared - _GENERIC_TARGET_TOKENS):
        return False
    if require_mutation:
        mutation_shared = claim_targets & evidence.mutation_targets
        if not mutation_shared:
            return False
        if claim_specific and not (mutation_shared - _GENERIC_TARGET_TOKENS):
            return False
    return True


def enforce_claim_integrity(
    response_text: str,
    messages: Iterable[dict[str, Any]],
) -> tuple[str, bool, str | None]:
    """Return ``(visible_text, blocked, reason)`` for a final response."""

    text = str(response_text or "")
    has_action_claim = bool(_STRONG_CLAIM.search(text))
    has_promise_claim = bool(_PROMISE_CLAIM.search(text))
    has_live_claim = bool(_ABSOLUTE_OR_LIVE.search(text))
    if not text or (not has_action_claim and not has_promise_claim and not has_live_claim):
        return text, False, None

    evidence = collect_evidence(messages)

    # A promise about all future turns ("больше не буду", "never again")
    # cannot be proven by any finite current-turn probe. Force bounded wording
    # instead of pretending a permanent guarantee exists.
    if has_promise_claim:
        return (
            _blocked_response("unprovable_future_promise", evidence),
            True,
            "unprovable_future_promise",
        )

    has_change_claim = bool(_CHANGE_CLAIM.search(text))
    has_live_scope = bool(has_live_claim or _LIVE_ACTION.search(text))
    if has_live_scope:
        if has_change_claim:
            live_ok = evidence.live_after_mutation
        else:
            live_ok = evidence.live_verifications > 0
        if not live_ok:
            return _blocked_response("missing_live_verification", evidence), True, "missing_live_verification"
        if not _live_target_bound(text, evidence, require_mutation=has_change_claim):
            return _blocked_response("missing_target_binding", evidence), True, "missing_target_binding"
        return text, False, None

    if _FUNCTIONAL_CLAIM.search(text):
        if has_change_claim:
            functional_ok = evidence.functional_after_mutation
        else:
            functional_ok = evidence.functional_verifications > 0
        if not functional_ok:
            return (
                _blocked_response("missing_functional_verification", evidence),
                True,
                "missing_functional_verification",
            )
        if not _result_target_bound(
            text,
            evidence,
            evidence.functional_targets,
            require_mutation=has_change_claim,
        ):
            return (
                _blocked_response("missing_target_binding", evidence),
                True,
                "missing_target_binding",
            )
        return text, False, None

    if _ARTIFACT_ONLY_CLAIM.search(text):
        if (
            evidence.mutations
            and evidence.artifact_after_mutation
            and _result_target_bound(
                text,
                evidence,
                evidence.artifact_targets,
                require_mutation=True,
            )
        ):
            return text, False, None

    return _blocked_response("missing_action_evidence", evidence), True, "missing_action_evidence"
