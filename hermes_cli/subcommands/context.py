"""Parser surface for the strict local ``hermes context audit`` command.

Execution is intercepted before regular CLI bootstrap by
``hermes_cli.main``. This parser exists so top-level help and parser tests keep
the command discoverable without changing that safety boundary.
"""

from __future__ import annotations


def build_context_parser(subparsers) -> None:
    context_parser = subparsers.add_parser(
        "context",
        help="Inspect local prompt and skill-context attribution",
    )
    actions = context_parser.add_subparsers(dest="context_command")
    audit = actions.add_parser(
        "audit",
        help="Strict read-only local context/skill attribution audit",
    )
    audit.add_argument("--local", action="store_true")
    audit.add_argument("--json", action="store_true")
    audit.add_argument("--platform", default="telegram")
    audit.add_argument("--session-limit", type=int, default=250)
    audit.add_argument("--task-limit", type=int, default=20)
    audit.add_argument("--no-history", action="store_true")
