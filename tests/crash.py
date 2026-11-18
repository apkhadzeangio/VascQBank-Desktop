"""Abrupt process termination that works on every platform, for the crash/interruption tests.

POSIX: ``SIGKILL`` — the process dies at once, Python runs no ``finally``, ``atexit`` or buffered
flush, and ``subprocess`` reports the return code ``-SIGKILL``.

Windows has no ``SIGKILL``. There ``os.kill(pid, sig)`` with any signal other than the two console
control events calls ``TerminateProcess(handle, sig)``: just as abrupt (no Python clean-up, no
flush, open handles are closed by the kernel), and the exit code is the number passed — so killing
with ``SIGTERM`` gives the return code ``15``. (``Popen.terminate``/``send_signal`` are *not* used:
on Windows they terminate with exit code 1, which could not be told apart from a crash.)

These are process-kill tests: they say nothing about power loss, where the OS cache may be lost.
"""

from __future__ import annotations

import os
import signal
import textwrap

KILL_SIGNAL: int = int(signal.SIGTERM) if os.name == "nt" else int(signal.SIGKILL)
KILLED_RETURNCODE: int = int(signal.SIGTERM) if os.name == "nt" else -int(signal.SIGKILL)

# Paste into a child script (it imports ``os``, ``signal`` and ``time`` itself) and call ``die()``.
# TerminateProcess on the calling process is not guaranteed to have stopped it by the time the call
# returns, so the child then blocks: it can never run another line of the interrupted operation.
CHILD_DIE = textwrap.dedent(
    """
    import os as _os, signal as _signal, time as _time

    def die():
        _os.kill(_os.getpid(), _signal.SIGTERM if _os.name == "nt" else _signal.SIGKILL)
        _time.sleep(120)
    """
)


def kill(pid: int) -> None:
    """Terminate another process abruptly (see the module docstring)."""
    os.kill(pid, KILL_SIGNAL)


def was_killed(returncode: int | None) -> bool:
    return returncode == KILLED_RETURNCODE
