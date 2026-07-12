import asyncio
import errno
import os
import signal
import time

import pytest

from forge.engine.terminal import (
    RingBuffer,
    SessionTerminals,
    Terminal,
    TerminalError,
)


async def _wait_for(pred, timeout=5.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        await asyncio.sleep(interval)
    return False


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


# -- RingBuffer -------------------------------------------------------------
def test_ring_buffer_offsets_and_read():
    rb = RingBuffer(max_bytes=1024)
    rb.append("hello")
    text, nxt = rb.read(0)
    assert text == "hello"
    assert nxt == 5
    # Resume from a cursor.
    rb.append("world")
    text, nxt = rb.read(5)
    assert text == "world"
    assert nxt == 10


def test_ring_buffer_truncation_advances_start():
    rb = RingBuffer(max_bytes=8)
    rb.append("abcdefghij")  # 10 bytes into an 8-byte buffer
    assert rb.start == 2
    assert rb.end == 10
    text, nxt = rb.read(0)  # below start -> clamped
    assert text == "cdefghij"
    assert nxt == 10


def test_ring_buffer_truncates_on_utf8_boundary():
    rb = RingBuffer(max_bytes=4)
    # 'é' is 2 bytes; a run of them must never be cut mid-character.
    rb.append("é" * 10)
    text, _ = rb.read(rb.start)
    assert "\ufffd" not in text
    assert text == "é" * (len(rb._buf) // 2)


# -- interactive I/O --------------------------------------------------------
async def test_interactive_input_output():
    terms = SessionTerminals()
    t = await terms.open(["cat"], os.getcwd())
    try:
        t.write("ping\n")
        assert await _wait_for(lambda: "ping" in t.read()[0])
        text, cursor = t.read()
        assert "ping" in text
        # cat echoes; resume read returns nothing new yet.
        assert t.read(cursor)[0] == ""
    finally:
        terms.reap_all()


async def test_shell_prompt_and_command():
    terms = SessionTerminals()
    t = await terms.open(["bash", "--norc", "-i"], os.getcwd())
    try:
        t.write("echo hello-terminal\n")
        assert await _wait_for(lambda: "hello-terminal" in t.read()[0])
    finally:
        terms.reap_all()


# -- ANSI preservation ------------------------------------------------------
async def test_ansi_output_preserved():
    terms = SessionTerminals()
    t = await terms.open(
        ["python3", "-c", r"import sys; sys.stdout.write('\x1b[31mRED\x1b[0m')"],
        os.getcwd())
    try:
        assert await _wait_for(lambda: t.state == "exited")
        assert "\x1b[31mRED\x1b[0m" in t.read()[0]
    finally:
        terms.reap_all()


# -- UTF-8 split across reads -----------------------------------------------
async def test_utf8_split_across_read_boundary():
    terms = SessionTerminals()
    # 65535 ASCII bytes + a 2-byte char straddle the 65536-byte read chunk.
    t = await terms.open(
        ["python3", "-c",
         r"import sys; sys.stdout.write('x'*65535 + 'é'); sys.stdout.flush()"],
        os.getcwd())
    try:
        assert await _wait_for(lambda: t.read()[0].endswith("é"), timeout=8)
        text = t.read()[0]
        assert "\ufffd" not in text
        assert text.endswith("é")
    finally:
        terms.reap_all()


# -- resize -----------------------------------------------------------------
async def test_resize_reports_new_size():
    terms = SessionTerminals()
    t = await terms.open(["bash", "--norc", "-i"], os.getcwd(), cols=80, rows=24)
    try:
        t.resize(120, 40)
        assert t.cols == 120 and t.rows == 40
        # The shell sees the new size via SIGWINCH / TIOCGWINSZ.
        t.write("echo SIZE=${COLUMNS}x${LINES}\n")
        assert await _wait_for(lambda: "SIZE=120x40" in t.read()[0])
    finally:
        terms.reap_all()


# -- Ctrl-C / signals -------------------------------------------------------
async def test_ctrl_c_interrupts_foreground():
    terms = SessionTerminals()
    t = await terms.open(["bash", "--norc", "-i"], os.getcwd())
    try:
        t.write("sleep 60\n")
        await asyncio.sleep(0.3)
        t.write("\x03")  # Ctrl-C -> SIGINT to the foreground group
        t.write("echo AFTER=$?\n")
        assert await _wait_for(lambda: "AFTER=" in t.read()[0])
    finally:
        terms.reap_all()


async def test_signal_terminates_process():
    terms = SessionTerminals()
    t = await terms.open(["sleep", "60"], os.getcwd())
    try:
        t.signal(signal.SIGTERM)
        assert await _wait_for(lambda: t.state == "exited")
        assert t.exit_reason == "signaled"
        assert t.exit_code == -signal.SIGTERM
    finally:
        terms.reap_all()


# -- process-group / background child cleanup -------------------------------
async def test_close_reaps_backgrounded_child(tmp_path):
    pid_file = tmp_path / "pid"
    terms = SessionTerminals()
    t = await terms.open(
        ["bash", "--norc", "-c",
         f"sleep 60 & echo $! > {pid_file}; echo up; sleep 60"],
        os.getcwd())
    try:
        assert await _wait_for(lambda: pid_file.exists() and pid_file.read_text().strip())
        child_pid = int(pid_file.read_text().strip())
        assert _pid_alive(child_pid)
        t.close()
        assert await _wait_for(lambda: not _pid_alive(child_pid))
    finally:
        terms.reap_all()


# -- idempotent close -------------------------------------------------------
async def test_idempotent_close():
    terms = SessionTerminals()
    t = await terms.open(["cat"], os.getcwd())
    t.close()
    assert t.state == "closed"
    # Repeated closes are no-ops and never raise.
    t.close()
    t.close()
    assert t.state == "closed"
    assert t.exit_reason == "closed"


async def test_close_after_natural_exit_does_not_rekill(monkeypatch):
    terms = SessionTerminals()
    t = await terms.open(["python3", "-c", "print('done')"], os.getcwd())
    try:
        # Let the process exit naturally and be reaped via _finalize().
        assert await _wait_for(lambda: t.state == "exited")
        assert t._reaped

        killed: list[tuple[int, int]] = []
        real_killpg = os.killpg
        monkeypatch.setattr(
            os, "killpg",
            lambda pgid, sig: (killed.append((pgid, sig)), real_killpg(pgid, sig))[1])
        # close() after the child was reaped must not killpg the (possibly
        # recycled) pgid.
        t.close()
        assert t.state == "closed"
        assert killed == []
    finally:
        terms.reap_all()


async def test_signal_after_natural_exit_never_killpg(monkeypatch):
    terms = SessionTerminals()
    t = await terms.open(["python3", "-c", "print('done')"], os.getcwd())
    try:
        # Let it exit and be reaped via _finalize(); its pid/pgid may now be
        # recycled by the OS.
        assert await _wait_for(lambda: t.state == "exited")
        assert t._reaped

        killed: list[tuple[int, int]] = []
        real_killpg = os.killpg
        monkeypatch.setattr(
            os, "killpg",
            lambda pgid, sig: (killed.append((pgid, sig)), real_killpg(pgid, sig))[1])
        # signal() on a reaped terminal must refuse rather than killpg a
        # possibly-recycled pgid.
        with pytest.raises(TerminalError):
            t.signal(signal.SIGINT)
        assert killed == []
    finally:
        terms.reap_all()


async def test_signal_after_close_never_killpg(monkeypatch):
    terms = SessionTerminals()
    t = await terms.open(["cat"], os.getcwd())
    t.close()
    assert t.state == "closed"
    killed: list[tuple[int, int]] = []
    real_killpg = os.killpg
    monkeypatch.setattr(
        os, "killpg",
        lambda pgid, sig: (killed.append((pgid, sig)), real_killpg(pgid, sig))[1])
    with pytest.raises(TerminalError):
        t.signal(signal.SIGTERM)
    assert killed == []


async def test_spurious_eagain_does_not_finalize_live_terminal():
    terms = SessionTerminals()
    t = await terms.open(["cat"], os.getcwd())
    try:
        real_read = os.read

        def fake_read(fd, n):
            if fd == t._master_fd:
                raise BlockingIOError("EAGAIN")
            return real_read(fd, n)

        # A spurious readable notification with no data must not finalize.
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(os, "read", fake_read)
            t._on_readable()
        assert t.is_live()
        assert t.state == "running"

        # And a real EAGAIN OSError is treated identically.
        def fake_read_oserror(fd, n):
            if fd == t._master_fd:
                raise OSError(errno.EAGAIN, "try again")
            return real_read(fd, n)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(os, "read", fake_read_oserror)
            t._on_readable()
        assert t.is_live()

        # The terminal still works after the spurious wakeups.
        t.write("still-here\n")
        assert await _wait_for(lambda: "still-here" in t.read()[0])
    finally:
        terms.reap_all()


async def test_reap_all_idempotent():
    terms = SessionTerminals()
    await terms.open(["cat"], os.getcwd())
    await terms.open(["cat"], os.getcwd())
    terms.reap_all()
    terms.reap_all()
    assert not terms.has_live()


# -- max-live cap -----------------------------------------------------------
async def test_max_live_cap():
    terms = SessionTerminals(max_live=4)
    opened = [await terms.open(["cat"], os.getcwd()) for _ in range(4)]
    try:
        assert terms.live_count() == 4
        with pytest.raises(TerminalError):
            await terms.open(["cat"], os.getcwd())
        # Closing one frees a slot.
        opened[0].close()
        assert await _wait_for(lambda: terms.live_count() == 3)
        fresh = await terms.open(["cat"], os.getcwd())
        assert fresh.is_live()
    finally:
        terms.reap_all()


# -- bounded dead-terminal retention ----------------------------------------
async def test_dead_terminals_are_bounded():
    terms = SessionTerminals(max_live=4, max_dead=3)
    ids: list[str] = []
    for _ in range(10):
        t = await terms.open(["cat"], os.getcwd())
        ids.append(t.id)
        terms.close(t.id)
    # Only max_dead dead records survive; all terminals here are dead.
    assert terms.dead_count() == 3
    assert len(terms.list()) == 3
    # The newest dead terminals are the ones retained.
    retained = {t.id for t in terms.list()}
    assert retained == set(ids[-3:])
    # Older, pruned ids are simply unknown again.
    for old in ids[:-3]:
        with pytest.raises(TerminalError):
            terms.get(old)


async def test_pruning_never_evicts_live_terminals():
    terms = SessionTerminals(max_live=4, max_dead=2)
    live = [await terms.open(["cat"], os.getcwd()) for _ in range(3)]
    try:
        # Churn through many dead terminals while live ones stay open.
        for _ in range(8):
            slot = await terms.open(["cat"], os.getcwd())  # would exceed if live
            terms.close(slot.id)
        # Live terminals are all still present and never pruned.
        for t in live:
            assert terms.get(t.id) is t
            assert t.is_live()
        assert terms.dead_count() == 2
    finally:
        terms.reap_all()


async def test_live_cap_still_enforced_with_dead_retention():
    terms = SessionTerminals(max_live=2, max_dead=20)
    a = await terms.open(["cat"], os.getcwd())
    await terms.open(["cat"], os.getcwd())  # second live terminal holds a slot
    try:
        with pytest.raises(TerminalError):
            await terms.open(["cat"], os.getcwd())
        # Retaining many dead terminals must not consume live slots.
        a.close()
        assert await _wait_for(lambda: terms.live_count() == 1)
        c = await terms.open(["cat"], os.getcwd())
        assert c.is_live()
        assert terms.live_count() == 2
    finally:
        terms.reap_all()


async def test_natural_exit_records_pruned_on_list():
    terms = SessionTerminals(max_live=4, max_dead=2)
    for _ in range(5):
        t = await terms.open(["python3", "-c", "print('x')"], os.getcwd())
        assert await _wait_for(lambda t=t: t.state == "exited")
    # list() prunes dead records that exited naturally (never went through close).
    assert len(terms.list()) == 2
    assert terms.dead_count() == 2


# -- survival of unrelated cancellation -------------------------------------
async def test_survives_unrelated_task_cancellation():
    terms = SessionTerminals()
    t = None

    async def opener():
        nonlocal t
        t = await terms.open(["cat"], os.getcwd())
        # Simulate an agent run that opened the terminal then keeps working.
        await asyncio.sleep(60)

    task = asyncio.create_task(opener())
    assert await _wait_for(lambda: t is not None)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # The terminal outlives the cancelled run: still readable and writable.
    assert t.is_live()
    t.write("still-here\n")
    assert await _wait_for(lambda: "still-here" in t.read()[0])
    terms.reap_all()


# -- unknown-id handling ----------------------------------------------------
async def test_unknown_terminal_raises():
    terms = SessionTerminals()
    with pytest.raises(TerminalError):
        terms.get("nope")
    with pytest.raises(TerminalError):
        terms.write("nope", "x")


# -- hooks ------------------------------------------------------------------
async def test_output_and_exit_hooks_fire():
    outputs: list[str] = []
    exited: list[Terminal] = []
    terms = SessionTerminals()
    t = await terms.open(
        ["python3", "-c", "print('hook-line')"], os.getcwd(),
        on_output=lambda term, text, off: outputs.append(text),
        on_exit=exited.append)
    try:
        assert await _wait_for(lambda: exited and "hook-line" in "".join(outputs))
        assert t.exit_reason == "exited"
        assert t.exit_code == 0
    finally:
        terms.reap_all()
