"""Per-GPU job lock so two pipeline stages never share the same physical
GPU at once — but stages on *different* GPUs are free to run concurrently.

This started as a single global lock (see git history) after a concrete
incident: a manual `produce --force` run and a leftover generation both
hammered one 8GB card at the same time, turning a normal ~10s-per-frame
b-roll generation into 15+ minutes per frame from VRAM thrashing. That
worked, but was needlessly conservative once this machine's second GPU
was pressed into service (2026-09-02): draft (Ollama, pinned to GPU 1) and
produce (the SD webui, pinned to GPU 0) don't actually contend for
anything, so serializing them wastes the whole point of having two cards.
Each stage now locks only the physical GPU it actually uses, keyed by
resource name — see GPU_RESOURCE_FOR_STAGE below.
"""

import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from .config import SKILL_DIR
from .log import log

# Which physical GPU resource each pipeline stage actually contends for.
# draft/topics calls hit Ollama (both instances pinned to GPU 1 — see
# ollama-start-gpu1.bat and run_scan_two.bat); produce calls hit the local
# SD webui (pinned to GPU 0 via --device-id 0 in webui-user.bat). Two
# stages mapped to the *same* resource serialize against each other; stages
# on different resources run concurrently.
GPU_RESOURCE_FOR_STAGE = {
    "draft": "gpu1_ollama",
    "produce": "gpu0_sdwebui",
}


def _lock_path(resource: str) -> Path:
    return SKILL_DIR / f"job_{resource}.lock"

# How long to wait for another job to finish before giving up.
DEFAULT_TIMEOUT_SEC = 3600
POLL_INTERVAL_SEC = 5


def _pid_alive(pid: int) -> bool:
    """Cross-platform "is this process still running" check.

    os.kill(pid, 0) is the standard POSIX idiom, but on Windows signal 0
    isn't meaningfully supported and raises a raw SystemError instead of a
    clean OSError — it crashed the very first time two jobs actually
    contended for this lock. Windows needs its own path via the win32 API
    (through ctypes, no extra dependency required).
    """
    if sys.platform == "win32":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we don't own it — treat as alive.
        return True


def _read_lock_pid(lock_path: Path) -> int | None:
    try:
        return int(lock_path.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        return None


@contextmanager
def job_lock(label: str, timeout_sec: int = DEFAULT_TIMEOUT_SEC):
    """Block until no other pipeline job holds the same GPU resource this
    `label` stage needs, then hold it for the duration of the `with` block.
    Stale locks (holder process no longer running — e.g. a previous run
    crashed without cleanup) are detected and cleared automatically rather
    than deadlocking every future run.

    `label` is a pipeline stage name ("draft", "produce", ...); it's mapped
    to the physical GPU resource that stage actually uses via
    GPU_RESOURCE_FOR_STAGE, so two stages on different GPUs never block
    each other, only stages sharing one GPU do.
    """
    resource = GPU_RESOURCE_FOR_STAGE.get(label, label)
    lock_path = _lock_path(resource)
    SKILL_DIR.mkdir(parents=True, exist_ok=True)
    waited = 0
    announced = False

    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            break
        except FileExistsError:
            holder_pid = _read_lock_pid(lock_path)
            if holder_pid is not None and not _pid_alive(holder_pid):
                log(f"[joblock] Clearing stale '{resource}' lock from dead process {holder_pid}")
                lock_path.unlink(missing_ok=True)
                continue
            if not announced:
                log(f"[joblock] '{label}' waiting for '{resource}' (pid {holder_pid}) to be free...")
                announced = True
            if waited >= timeout_sec:
                raise TimeoutError(
                    f"Timed out after {timeout_sec}s waiting for the '{resource}' job lock "
                    f"(held by pid {holder_pid}). If that process is gone, delete {lock_path}."
                )
            time.sleep(POLL_INTERVAL_SEC)
            waited += POLL_INTERVAL_SEC

    try:
        yield
    finally:
        lock_path.unlink(missing_ok=True)
