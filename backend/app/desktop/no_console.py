"""Keep console windows from flashing up on Windows.

Packrat's processes have no console of their own (the bundle is a windowed
program and the children are started with CREATE_NO_WINDOW). When a
process like that starts a console program, Windows opens a fresh console
window for it, which flashes up and vanishes as soon as the program ends.

Packrat's own subprocess calls already ask for no window, but libraries do
not: Python's platform module runs `cmd /c ver` whenever its WMI lookup
fails, and uvicorn and Celery call platform.system() as they start, so each
process start flashed a cmd window. install() makes CREATE_NO_WINDOW the
default for every subprocess these processes start. It only affects
console programs; a GUI program launched this way still shows its window.
"""

from __future__ import annotations

import subprocess
import sys

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_CONSOLE = 0x00000010

# Popen(args, bufsize, executable, stdin, stdout, stderr, preexec_fn,
#       close_fds, shell, cwd, env, universal_newlines, startupinfo,
#       creationflags, ...): creationflags is the 14th positional argument.
_CREATIONFLAGS_POSITION = 13

_installed = False


def with_no_window(args: tuple, kwargs: dict) -> dict:
    """The keyword arguments for Popen with CREATE_NO_WINDOW added, unless
    the caller passed creationflags positionally or asked for a console or
    a detached process on purpose."""
    if len(args) > _CREATIONFLAGS_POSITION:
        return kwargs
    flags = kwargs.get("creationflags") or 0
    if flags & (CREATE_NEW_CONSOLE | DETACHED_PROCESS):
        return kwargs
    return {**kwargs, "creationflags": flags | CREATE_NO_WINDOW}


def install() -> bool:
    """Patch subprocess.Popen in this process. Windows only; returns
    whether the patch is in place. Safe to call more than once."""
    global _installed
    if sys.platform != "win32":
        return False
    if _installed:
        return True
    original_init = subprocess.Popen.__init__

    def __init__(self, *args, **kwargs):  # noqa: N807 - replaces Popen.__init__
        original_init(self, *args, **with_no_window(args, kwargs))

    # Patch the class itself rather than rebinding subprocess.Popen, so code
    # that already holds a reference to it (from subprocess import Popen,
    # asyncio's Windows Popen subclass) gets the same behaviour.
    subprocess.Popen.__init__ = __init__  # type: ignore[method-assign]
    _installed = True
    return True
