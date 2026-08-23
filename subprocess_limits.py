"""Resource fences inherited by ordinary children of the gateway."""
from __future__ import annotations

import os
import resource
from typing import Any

CHILD_NOFILE_LIMIT = 4096


def child_resource_preexec() -> None:
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    cap = min(CHILD_NOFILE_LIMIT, hard)
    resource.setrlimit(resource.RLIMIT_NOFILE, (cap, cap))


def bounded_child_kwargs(*, allow_fds: tuple[int, ...] = ()) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "close_fds": True,
        "pass_fds": tuple(sorted(set(int(fd) for fd in allow_fds))),
    }
    if os.name == "posix":
        kwargs["preexec_fn"] = child_resource_preexec
    return kwargs
