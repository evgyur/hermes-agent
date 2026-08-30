"""Hermes native entrypoint for Human20 Powerpack Gen2."""

try:  # Hermes loads this as a package; pytest may collect it as a top-level module.
    from .powerpack_gen2 import register
except ImportError:  # pragma: no cover - exercised by repository test collection
    from powerpack_gen2 import register

__all__ = ["register"]
