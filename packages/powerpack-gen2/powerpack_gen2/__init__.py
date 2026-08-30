"""Collision-safe, PluginContext-only registration for Powerpack Gen2."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Callable

from . import cli, doctor, installer, tools

ROOT = Path(__file__).resolve().parents[1]
MODES = frozenset({"disabled", "compatibility", "gen2_only"})
VARIANTS = frozenset({"rentals", "employee"})


def _load_vendor(package_name: str, directory: Path):
    spec = importlib.util.spec_from_file_location(
        package_name,
        directory / "__init__.py",
        submodule_search_locations=[str(directory)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load provider package: {directory}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[package_name] = module
    spec.loader.exec_module(module)
    return module


def _policy_context(**_: object) -> dict[str, str]:
    return {
        "context": (
            "Powerpack evidence rule: when a user or operator premise contradicts a prior "
            "installation, tool result, or health claim, inspect canonical receipts and the "
            "component's declared managed interpreter/executable before deciding. Do not agree "
            "with the contradiction merely because the main Hermes venv or ambient PATH lacks "
            "the component. State which receipt and runtime were checked."
        )
    }


def _setting(ctx: Any, key: str, default: str, allowed: frozenset[str]) -> str:
    missing = object()
    getter = getattr(ctx, "get_config", None)
    if callable(getter):
        value = getter(key, default=missing)
    else:
        value = missing
    if value is missing:
        try:
            try:
                from hermes_cli.config import load_config_readonly as load_config
            except ImportError:
                from hermes_cli.config import load_config

            config = load_config() or {}
            plugin_id = ctx.manifest.key or ctx.manifest.name
            entry = ((config.get("plugins") or {}).get("entries") or {}).get(plugin_id) or {}
            value = missing
            if isinstance(entry, dict):
                for container_name in ("settings", "config"):
                    container = entry.get(container_name)
                    if isinstance(container, dict) and key in container:
                        value = container[key]
                        break
                if value is missing and key in entry:
                    value = entry[key]
            if value is missing:
                value = default
        except Exception:
            value = default
    normalized = str(value or default).strip().lower()
    if normalized not in allowed:
        raise ValueError(f"invalid Powerpack Gen2 {key}: {normalized}")
    return normalized


def _register_required(label: str, callback: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Register a non-effectful surface; Hermes raises on invalid input."""
    callback(*args, **kwargs)
    return True


def _register_tool_required(ctx: Any, name: str, schema: dict[str, Any], handler: Callable[..., Any]) -> None:
    """Register one effectful tool and prove that this exact handler won."""
    handle = ctx.register_tool(
        name=name,
        toolset="powerpack-gen2",
        schema=schema,
        handler=handler,
        override=name == "mem0g",
    )
    if handle is not None:  # compatibility with hosts that return a receipt
        return
    from tools.registry import registry

    entry = registry.get_entry(name)
    if entry is None or entry.toolset != "powerpack-gen2" or entry.handler is not handler:
        raise RuntimeError(f"Powerpack Gen2 registration rejected or collided: tool:{name}")


def _register_status_surfaces(ctx: Any, variant: str, mode: str) -> None:
    _register_required(
        "slash:powerpack-gen2",
        ctx.register_command,
        "powerpack-gen2",
        handler=lambda raw: cli.slash_status(ROOT, variant, mode, raw),
        description="Show Powerpack Gen2 status",
        args_hint="[doctor]",
    )
    _register_required(
        "cli:powerpack-gen2",
        ctx.register_cli_command,
        name="powerpack-gen2",
        help="Manage Human20 Powerpack Gen2",
        setup_fn=lambda parser: cli.setup_parser(parser, ROOT, variant, mode),
        handler_fn=lambda args: cli.handle_cli(args, ROOT, variant, mode, ctx=ctx),
    )


def _register_effectful_surfaces(ctx: Any, variant: str) -> int:
    count = 0
    for name, schema, handler in (
        ("mem0g", tools.MEM0G_SCHEMA, tools.handle_mem0g),
        ("continuum_host", tools.CONTINUUM_SCHEMA, tools.handle_continuum),
    ):
        _register_tool_required(ctx, name, schema, handler)
        count += 1
    if variant == "employee":
        _register_tool_required(
            ctx,
            "chipmanager_telegram",
            tools.CHIPMANAGER_SCHEMA,
            tools.handle_chipmanager,
        )
        count += 1

    perplexity = _load_vendor(
        f"{__package__}.vendors.perplexity",
        ROOT / "powerpack_gen2" / "vendors" / "perplexity",
    )
    human20_keys = _load_vendor(
        f"{__package__}.vendors.human20_keys",
        ROOT / "powerpack_gen2" / "vendors" / "human20_keys",
    )
    perplexity.register(ctx, required=True)
    human20_keys.register(ctx, required=True)
    return count


def register(ctx):
    """Register only surfaces owned by the selected coexistence mode.

    ``disabled`` performs no runtime registration. ``compatibility`` exposes
    namespaced diagnostics and non-effectful policy/skills. ``gen2_only`` adds
    the effectful tools and providers, failing closed on every collision.
    """
    mode = _setting(ctx, "mode", "disabled", MODES)
    variant = _setting(ctx, "variant", "rentals", VARIANTS)
    if mode == "disabled":
        return {"mode": mode, "variant": variant, "skills": 0, "tools": 0}

    skills = doctor.skill_entries(ROOT, variant)
    for item in skills:
        _register_required(
            f"skill:{item['name']}", ctx.register_skill, item["name"], item["path"]
        )
    _register_required("hook:pre_llm_call", ctx.register_hook, "pre_llm_call", _policy_context)
    _register_status_surfaces(ctx, variant, mode)

    tool_count = _register_effectful_surfaces(ctx, variant) if mode == "gen2_only" else 0
    return {"mode": mode, "variant": variant, "skills": len(skills), "tools": tool_count}


__all__ = ["MODES", "VARIANTS", "installer", "register"]
