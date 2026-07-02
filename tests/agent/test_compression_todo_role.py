from types import SimpleNamespace


class _Compressor:
    _last_compress_aborted = False
    _last_summary_error = None
    _last_aux_model_failure_model = None
    _last_aux_model_failure_error = None
    compression_count = 0
    last_compression_rough_tokens = 0
    last_prompt_tokens = 0
    last_completion_tokens = 0
    awaiting_real_usage_after_compression = False

    def compress(self, messages, **kwargs):
        return [{"role": "user", "content": "[CONTEXT COMPACTION] summary"}]


class _TodoStore:
    def format_for_injection(self):
        return "[Your active task list was preserved across context compression]\n- [>] p1. Fix bug (in_progress)"


class _Agent(SimpleNamespace):
    def _emit_status(self, *args, **kwargs):
        pass

    def _emit_warning(self, *args, **kwargs):
        pass

    def _invalidate_system_prompt(self):
        pass

    def _build_system_prompt(self, system_message):
        return system_message or "system"

    def commit_memory_session(self, *args, **kwargs):
        pass


def test_todo_snapshot_after_compression_is_system_role_not_user():
    from agent.conversation_compression import compress_context

    agent = _Agent(
        _compression_feasibility_checked=True,
        _session_db=None,
        _memory_manager=None,
        context_compressor=_Compressor(),
        _todo_store=_TodoStore(),
        _cached_system_prompt=None,
        _last_compaction_in_place=False,
        compression_in_place=False,
        session_id="s1",
        model="test/model",
        tools=[],
        platform="telegram",
        event_callback=None,
        log_prefix="",
    )

    compressed, _ = compress_context(
        agent,
        [{"role": "user", "content": "before"}],
        "system",
        approx_tokens=100,
    )

    todo_rows = [m for m in compressed if "active task list" in str(m.get("content", ""))]
    assert todo_rows
    assert todo_rows[-1]["role"] == "system"
