"""PTY-backed session terminals.

A ``Terminal`` owns one real Unix pseudo-terminal and the process running on
it, independent of any agent run or tool call. Because it is backed by a real
PTY, ANSI escapes, shell prompts, REPLs, job-control signals (Ctrl-C) and
``SIGWINCH`` resizes all behave as they would in a normal terminal.

``SessionTerminals`` is the per-session registry that bounds how many live
terminals a session may hold and provides the open/read/write/resize/signal/
close/list surface plus idempotent teardown.

Design notes:
- The output reader is attached to the event loop with ``add_reader`` and is
  therefore *not* tied to the agent task that opened the terminal: cancelling
  that run leaves the terminal (and its reader) alive.
- Bytes read off the master fd are UTF-8 decoded with an *incremental* decoder
  so a multi-byte character split across two reads is reassembled instead of
  turning into U+FFFD.
- Output is retained in a bounded ring buffer keyed by monotonically increasing
  offsets, so a late reader can resume from where it left off and detect when
  older output has been dropped.
- Process-group cleanup reuses BashTool's discipline: the child is a session
  leader (``setsid``), so its pgid equals its pid and we ``killpg`` the whole
  group to reap backgrounded children that inherited the slave.
"""

from __future__ import annotations

import asyncio
import codecs
import errno
import fcntl
import logging
import os
import pty
import signal
import struct
import subprocess
import termios
from collections.abc import Callable, Sequence
from typing import Literal
from uuid import uuid4

logger = logging.getLogger(__name__)

TerminalState = Literal["starting", "running", "exited", "closed"]
ExitReason = Literal["exited", "signaled", "closed"]

DEFAULT_BUFFER_BYTES = 256 * 1024
MAX_LIVE_TERMINALS = 4
# How many exited/closed terminals to retain for UI/agent inspection. Live
# terminals are always kept (and never counted against this cap); only dead
# records are pruned, oldest first, so each dead Terminal's ring buffer can't
# accumulate unboundedly over a long session.
MAX_DEAD_TERMINALS = 20
_READ_CHUNK = 65536


class TerminalError(Exception):
    """Raised for terminal API misuse (unknown id, cap exceeded, bad state)."""


class RingBuffer:
    """Bounded FIFO of decoded terminal text, addressed by monotonic byte
    offsets.

    Text is stored as its UTF-8 encoding. Only whole, already-decoded strings
    are appended, and front truncation always lands on a UTF-8 character
    boundary, so any suffix read back decodes cleanly. Offsets never rewind:
    ``end`` only grows, and ``start`` only advances as old bytes are dropped.
    """

    def __init__(self, max_bytes: int = DEFAULT_BUFFER_BYTES):
        self.max_bytes = max(1, max_bytes)
        self._buf = bytearray()
        self._start = 0

    @property
    def start(self) -> int:
        """Earliest offset still retained (older output has been dropped)."""
        return self._start

    @property
    def end(self) -> int:
        """Offset one past the newest byte; equals total bytes ever produced
        minus nothing — it is the cursor a fresh reader would resume from."""
        return self._start + len(self._buf)

    def append(self, text: str) -> None:
        self._buf += text.encode("utf-8")
        if len(self._buf) <= self.max_bytes:
            return
        drop = len(self._buf) - self.max_bytes
        # Advance off any UTF-8 continuation bytes so we cut on a char boundary.
        while drop < len(self._buf) and (self._buf[drop] & 0xC0) == 0x80:
            drop += 1
        del self._buf[:drop]
        self._start += drop

    def read(self, offset: int) -> tuple[str, int]:
        """Return ``(text, next_offset)`` for everything at/after ``offset``.

        An ``offset`` below ``start`` (output already dropped) is clamped up to
        ``start``; the returned ``next_offset`` is always ``end``.
        """
        if offset < self._start:
            offset = self._start
        elif offset > self.end:
            offset = self.end
        return self._buf[offset - self._start:].decode("utf-8", "replace"), self.end


class Terminal:
    """One PTY plus the process group running on it."""

    def __init__(self, command: Sequence[str], cwd: str, *, id: str | None = None,
                 cols: int = 80, rows: int = 24, env: dict[str, str] | None = None,
                 buffer_bytes: int = DEFAULT_BUFFER_BYTES,
                 on_output: Callable[["Terminal", str, int], None] | None = None,
                 on_exit: Callable[["Terminal"], None] | None = None):
        if not command:
            raise TerminalError("command must be a non-empty argv")
        self.id = id or uuid4().hex[:8]
        self.command = list(command)
        self.cwd = cwd
        self.cols = cols
        self.rows = rows
        self.env = env
        self.buffer = RingBuffer(buffer_bytes)
        self.on_output = on_output
        self.on_exit = on_exit

        self.state: TerminalState = "starting"
        self.pid: int | None = None
        self.exit_code: int | None = None
        self.exit_reason: ExitReason | None = None

        self._master_fd: int | None = None
        self._proc: subprocess.Popen | None = None
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._loop: asyncio.AbstractEventLoop | None = None
        self._reader_added = False
        self._reaped = False
        self._exit_event = asyncio.Event()

    # -- lifecycle -----------------------------------------------------------
    async def start(self) -> "Terminal":
        self._loop = asyncio.get_running_loop()
        master_fd, slave_fd = pty.openpty()
        _set_winsize(master_fd, self.rows, self.cols)
        env = dict(os.environ if self.env is None else self.env)
        env.setdefault("TERM", "xterm-256color")

        def _preexec() -> None:  # pragma: no cover - runs in the child
            # New session -> the child is its own process-group leader, so its
            # pgid == pid and we can killpg the whole group later. Claim the pty
            # as the controlling terminal so job-control signals (Ctrl-C) and
            # SIGWINCH reach the foreground process.
            os.setsid()
            fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)

        try:
            self._proc = subprocess.Popen(
                self.command, cwd=self.cwd, env=env,
                stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
                preexec_fn=_preexec, close_fds=True)
        except BaseException:
            os.close(master_fd)
            raise
        finally:
            os.close(slave_fd)
        self.pid = self._proc.pid
        self._master_fd = master_fd
        os.set_blocking(master_fd, False)
        self.state = "running"
        self._loop.add_reader(master_fd, self._on_readable)
        self._reader_added = True
        return self

    @property
    def pgid(self) -> int | None:
        return self.pid  # setsid() in the child makes pgid == pid

    def is_live(self) -> bool:
        return self.state in ("starting", "running")

    # -- operations ----------------------------------------------------------
    def write(self, data: str | bytes) -> None:
        if self.state != "running" or self._master_fd is None:
            raise TerminalError(f"terminal {self.id} is not running")
        raw = data.encode("utf-8") if isinstance(data, str) else data
        os.write(self._master_fd, raw)

    def read(self, offset: int = 0) -> tuple[str, int]:
        return self.buffer.read(offset)

    def resize(self, cols: int, rows: int) -> None:
        self.cols, self.rows = cols, rows
        if self._master_fd is not None and self.state == "running":
            # The kernel delivers SIGWINCH to the foreground group on TIOCSWINSZ.
            _set_winsize(self._master_fd, rows, cols)

    def signal(self, sig: int) -> None:
        """Send ``sig`` to the whole process group (e.g. SIGINT for Ctrl-C).

        Only permitted while the terminal is genuinely live and unreaped. Once
        the child has exited/been reaped its pid/pgid may have been recycled by
        the OS, so killpg could hit an unrelated process group — refuse instead
        of signalling a stranger."""
        if self.state != "running" or self.pid is None or self._reaped:
            raise TerminalError(f"terminal {self.id} is not running")
        try:
            os.killpg(self.pid, sig)
        except ProcessLookupError:
            pass

    async def wait(self) -> int | None:
        await self._exit_event.wait()
        return self.exit_code

    def close(self) -> None:
        """Tear the terminal down: SIGKILL the group, drop the reader, close the
        master fd. Idempotent — safe to call from any state, repeatedly."""
        if self.state == "closed":
            return
        self._remove_reader()
        # Hard group kill (same discipline as BashTool) reaps backgrounded
        # children that inherited the slave and would otherwise linger. Skip it
        # once the child has been reaped: its pid/pgid may have been recycled by
        # the OS, so killpg could hit an unrelated process group.
        if self.pid is not None and not self._reaped:
            try:
                os.killpg(self.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self._reap()
        if self.exit_code is None and self._proc is not None:
            self.exit_code = self._proc.returncode
        if self.exit_reason is None:
            self.exit_reason = "closed"
        self._close_fd()
        self.state = "closed"
        self._exit_event.set()

    # -- internals -----------------------------------------------------------
    def _on_readable(self) -> None:
        try:
            data = os.read(self._master_fd, _READ_CHUNK)
        except BlockingIOError:
            # EAGAIN/EWOULDBLOCK on the nonblocking master: a spurious readable
            # notification with no data yet. Not EOF — leave the terminal live.
            return
        except OSError as exc:
            if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                return
            if exc.errno == errno.EIO:
                # Linux delivers EIO on the master once the child (and its
                # controlling-terminal session) exits; treat it as EOF.
                data = b""
            else:
                raise
        if not data:
            self._finalize()
            return
        text = self._decoder.decode(data)
        if text:
            self.buffer.append(text)
            self._notify(text)

    def _finalize(self) -> None:
        """Handle EOF on the master: the process ended (or its group did).
        Flush the decoder, reap, and mark exited. Distinct from close(), which
        is an external teardown."""
        if self.state in ("exited", "closed"):
            return
        self._remove_reader()
        tail = self._decoder.decode(b"", final=True)
        if tail:
            self.buffer.append(tail)
            self._notify(tail)
        self._reap()
        rc = self._proc.returncode if self._proc is not None else None
        self.exit_code = rc
        self.exit_reason = "signaled" if (rc is not None and rc < 0) else "exited"
        self._close_fd()
        self.state = "exited"
        self._exit_event.set()
        if self.on_exit is not None:
            try:
                self.on_exit(self)
            except Exception:
                logger.exception("terminal %s on_exit hook failed", self.id)

    def _reap(self) -> None:
        if self._reaped or self._proc is None:
            return
        try:
            self._proc.wait(timeout=5)
        except Exception:
            logger.warning("terminal %s did not reap cleanly", self.id, exc_info=True)
        self._reaped = True

    def _notify(self, text: str) -> None:
        if self.on_output is None:
            return
        try:
            self.on_output(self, text, self.buffer.end)
        except Exception:
            logger.exception("terminal %s on_output hook failed", self.id)

    def _remove_reader(self) -> None:
        if self._reader_added and self._loop is not None and self._master_fd is not None:
            self._loop.remove_reader(self._master_fd)
        self._reader_added = False

    def _close_fd(self) -> None:
        if self._master_fd is not None:
            try:
                os.close(self._master_fd)
            except OSError:
                pass
            self._master_fd = None


def _set_winsize(fd: int, rows: int, cols: int) -> None:
    winsize = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)


class SessionTerminals:
    """Per-session registry of terminals with a live-count cap and teardown.

    Live terminals are capped at ``max_live``. Exited/closed terminals are kept
    for later inspection but bounded to ``max_dead`` records: when that many dead
    terminals accumulate, the oldest (by insertion order) are pruned first.
    Live terminals are never pruned and never counted against ``max_dead``, so
    the newest dead terminals stay available while total retained storage stays
    bounded to ``max_live + max_dead`` records.
    """

    def __init__(self, max_live: int = MAX_LIVE_TERMINALS,
                 max_dead: int = MAX_DEAD_TERMINALS):
        self.max_live = max_live
        self.max_dead = max(0, max_dead)
        self._terminals: dict[str, Terminal] = {}

    def has_live(self) -> bool:
        return any(t.is_live() for t in self._terminals.values())

    def live_count(self) -> int:
        return sum(1 for t in self._terminals.values() if t.is_live())

    def dead_count(self) -> int:
        return sum(1 for t in self._terminals.values() if not t.is_live())

    def _prune_dead(self) -> None:
        """Evict oldest dead terminals so at most ``max_dead`` are retained.

        Insertion order (dict order) approximates age; live terminals are
        skipped entirely. Newest dead records are preserved; pruned ids simply
        become unknown to ``get`` and yield the normal error."""
        excess = self.dead_count() - self.max_dead
        if excess <= 0:
            return
        for tid, term in list(self._terminals.items()):
            if excess <= 0:
                break
            if term.is_live():
                continue
            del self._terminals[tid]
            excess -= 1

    async def open(self, command: Sequence[str], cwd: str, *, cols: int = 80,
                   rows: int = 24, env: dict[str, str] | None = None,
                   buffer_bytes: int = DEFAULT_BUFFER_BYTES,
                   on_output: Callable[[Terminal, str, int], None] | None = None,
                   on_exit: Callable[[Terminal], None] | None = None) -> Terminal:
        if self.live_count() >= self.max_live:
            raise TerminalError(
                f"session already has {self.max_live} live terminals")
        self._prune_dead()
        term = Terminal(command, cwd, cols=cols, rows=rows, env=env,
                        buffer_bytes=buffer_bytes, on_output=on_output,
                        on_exit=on_exit)
        await term.start()
        self._terminals[term.id] = term
        return term

    def get(self, terminal_id: str) -> Terminal:
        try:
            return self._terminals[terminal_id]
        except KeyError:
            raise TerminalError(f"unknown terminal: {terminal_id}") from None

    def write(self, terminal_id: str, data: str | bytes) -> None:
        self.get(terminal_id).write(data)

    def read(self, terminal_id: str, offset: int = 0) -> tuple[str, int]:
        return self.get(terminal_id).read(offset)

    def resize(self, terminal_id: str, cols: int, rows: int) -> None:
        self.get(terminal_id).resize(cols, rows)

    def signal(self, terminal_id: str, sig: int) -> None:
        self.get(terminal_id).signal(sig)

    def close(self, terminal_id: str) -> None:
        # Closing turns a live terminal into a dead one; prune afterwards so the
        # dead cap is enforced as terminals wind down, not only on the next open.
        self.get(terminal_id).close()
        self._prune_dead()

    def list(self) -> list[Terminal]:
        # Terminals that exited naturally (EOF/_finalize) become dead without
        # going through close(); enforce the dead cap here so a caller that only
        # ever lists still sees bounded records.
        self._prune_dead()
        return list(self._terminals.values())

    def reap_all(self) -> None:
        """Idempotent teardown of every terminal's process group. Used by
        session delete/archive/shutdown so no PTY child is orphaned."""
        for term in self._terminals.values():
            term.close()
