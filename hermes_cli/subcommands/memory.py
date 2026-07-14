"""``hermes memory`` subcommand parser.

Extracted from ``hermes_cli/main.py:main()`` (god-file Phase 2 follow-up).
Handler injected to avoid importing ``main``.
"""

from __future__ import annotations

from typing import Callable


def build_memory_parser(subparsers, *, cmd_memory: Callable) -> None:
    """Attach the ``memory`` subcommand to ``subparsers``."""
    memory_parser = subparsers.add_parser(
        "memory",
        help="Configure external memory provider",
        description=(
            "Set up and manage external memory provider plugins.\n\n"
            "Available providers: honcho, openviking, mem0, hindsight,\n"
            "holographic, retaindb, byterover.\n\n"
            "Only one external provider can be active at a time.\n"
            "Built-in memory (MEMORY.md/USER.md) is always active."
        ),
    )
    memory_sub = memory_parser.add_subparsers(dest="memory_command")
    _setup_parser = memory_sub.add_parser(
        "setup", help="Interactive provider selection and configuration"
    )
    _setup_parser.add_argument(
        "provider",
        nargs="?",
        default=None,
        help="Provider to configure directly (e.g. honcho), skipping the picker",
    )
    memory_sub.add_parser("status", help="Show current memory provider config")
    local_parser = memory_sub.add_parser(
        "local",
        help="Manage profile-scoped Hermes local hot/warm/cold memory",
        description=(
            "Append, compact, rotate, delete, and doctor the private local "
            "memory store under HERMES_HOME."
        ),
    )
    local_sub = local_parser.add_subparsers(dest="local_memory_command")
    local_append = local_sub.add_parser(
        "append", help="Append a typed local memory event to hot memory"
    )
    local_append.add_argument("text", nargs="*", help="Text to store; stdin is used when omitted")
    local_append.add_argument("--source-class", default="operator_note")
    local_append.add_argument("--origin-ref", default="cli")
    local_append.add_argument("--confidence", type=float, default=0.5)
    local_append.add_argument("--label", action="append", default=[])
    local_append.add_argument("--ttl-seconds", type=int, default=None)
    local_compact = local_sub.add_parser("compact", help="Compact hot notes into warm memory")
    local_compact.add_argument("--limit", type=int, default=None)
    local_rotate = local_sub.add_parser("rotate", help="Expire old hot/warm notes into tombstones")
    local_rotate.add_argument("--hot-max-age-seconds", type=int, default=86400)
    local_rotate.add_argument("--warm-max-age-seconds", type=int, default=30 * 86400)
    local_delete = local_sub.add_parser("delete", help="Delete a local note by id, leaving a tombstone")
    local_delete.add_argument("note_id")
    local_delete.add_argument("--reason", default="operator_delete")
    local_sub.add_parser("doctor", help="Check local memory store health")
    memory_sub.add_parser("off", help="Disable external provider (built-in only)")
    _reset_parser = memory_sub.add_parser(
        "reset",
        help="Erase all built-in memory (MEMORY.md and USER.md)",
    )
    _reset_parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip confirmation prompt",
    )
    _reset_parser.add_argument(
        "--target",
        choices=["all", "memory", "user"],
        default="all",
        help="Which store to reset: 'all' (default), 'memory', or 'user'",
    )
    memory_parser.set_defaults(func=cmd_memory)
