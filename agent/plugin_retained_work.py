"""Small public retained-work facade for enabled Hermes plugins.

The facade resolves the host-owned parent only for the active agent turn and
forwards one native background delegation. It deliberately adds no signer,
broker, system service, or package-local authority layer.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any


class PluginRetainedWorkError(RuntimeError):
    """The host could not admit an exact active-parent background task."""


@dataclass(frozen=True)
class RetainedWorkHandleV1:
    delegation_id: str
    parent_session_id: str
    capability_mode: str = "parent-derived"
    delivery: str = "hermes-native"
    contract_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PluginRetainedWorkServiceV1:
    """Launch one native durable delegation from the exact active parent."""

    def __init__(self, plugin_name: str, *, delegate_task_fn: Any = None) -> None:
        if not plugin_name or not plugin_name.replace("-", "_").isidentifier():
            raise ValueError("plugin_name must be a non-empty identifier")
        self.plugin_name = plugin_name
        self._delegate_task_fn = delegate_task_fn

    @staticmethod
    def _active_top_level_parent() -> Any:
        from agent.subagent_lifecycle import get_active_subagent_parent

        parent = get_active_subagent_parent()
        if parent is None:
            raise PluginRetainedWorkError(
                "PUBLIC_RETAINED_WORK_NO_PARENT: an active Hermes parent is required"
            )
        if int(getattr(parent, "_delegate_depth", 0) or 0) != 0:
            raise PluginRetainedWorkError(
                "PUBLIC_RETAINED_WORK_NESTED_PARENT: only a top-level parent is supported"
            )
        if not str(getattr(parent, "session_id", "") or ""):
            raise PluginRetainedWorkError(
                "PUBLIC_RETAINED_WORK_UNROUTABLE_PARENT: parent session id is unavailable"
            )
        return parent

    @staticmethod
    def _require_durable_delivery() -> None:
        """Reject finite runners before Hermes falls back to synchronous work."""
        from gateway.session_context import async_delivery_supported

        if async_delivery_supported():
            return

        from tools.async_delegation import _current_origin_session_id

        if _current_origin_session_id():
            return
        raise PluginRetainedWorkError(
            "PUBLIC_RETAINED_WORK_UNROUTABLE_SURFACE: retained work requires "
            "a live Telegram/gateway, interactive CLI/TUI, or wakeable API session; "
            "one-shot cron and Kanban workers cannot receive detached completion"
        )

    def launch(
        self,
        *,
        goal: str,
        context: str | None = None,
        model: str | None = None,
        allowed_toolsets: tuple[str, ...] | None = None,
    ) -> RetainedWorkHandleV1:
        if not isinstance(goal, str) or not goal.strip() or len(goal) > 16_000:
            raise PluginRetainedWorkError("goal must contain 1..16000 characters")
        if context is not None and (
            not isinstance(context, str) or len(context) > 32_000
        ):
            raise PluginRetainedWorkError("context must contain at most 32000 characters")
        if model is not None or allowed_toolsets is not None:
            raise PluginRetainedWorkError(
                "model and toolset overrides are not supported by retained-work v1"
            )

        parent = self._active_top_level_parent()
        self._require_durable_delivery()
        if self._delegate_task_fn is None:
            from tools.delegate_tool import delegate_task, host_restart_context
        else:
            delegate_task = self._delegate_task_fn
            from contextlib import nullcontext
            host_restart_context = lambda **_kwargs: nullcontext()

        continuum_restartable_launch = self.plugin_name == "continuum"
        with host_restart_context(restartable=continuum_restartable_launch):
            raw = delegate_task(
                goal=goal,
                context=context,
                role="leaf",
                background=True,
                parent_agent=parent,
            )
        try:
            result = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise PluginRetainedWorkError(
                "PUBLIC_RETAINED_WORK_INVALID_HOST_RESPONSE"
            ) from exc
        if not isinstance(result, dict):
            raise PluginRetainedWorkError("PUBLIC_RETAINED_WORK_INVALID_HOST_RESPONSE")
        if result.get("status") != "dispatched" or result.get("mode") != "background":
            raise PluginRetainedWorkError(
                "PUBLIC_RETAINED_WORK_NOT_DURABLE: Hermes did not admit background delivery"
            )
        delegation_id = str(result.get("delegation_id") or "")
        if not delegation_id:
            raise PluginRetainedWorkError("PUBLIC_RETAINED_WORK_INVALID_HOST_RESPONSE")
        return RetainedWorkHandleV1(
            delegation_id=delegation_id,
            parent_session_id=str(parent.session_id),
        )
