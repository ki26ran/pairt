"""Simple PID lock file to prevent duplicate process instances."""
import os, sys, signal

_active_locks = []


def acquire_lock(name, lock_dir=None):
    """Acquire a PID lock file. Returns True if acquired, False if another instance is running.
    name: unique name for this lock (e.g. 'intra-live-trader')
    lock_dir: directory for lock files (defaults to /tmp/ngen26-locks/)
    """
    global _active_locks
    import tempfile
    _default = tempfile.gettempdir() if sys.platform == "win32" else "/tmp"
    base = lock_dir or os.path.join(_default, "ngen26-locks")
    os.makedirs(base, exist_ok=True)
    lock_path = os.path.join(base, f"{name}.pid")

    if os.path.exists(lock_path):
        with open(lock_path) as f:
            try:
                old_pid = int(f.read().strip())
            except (ValueError, OSError):
                old_pid = None
        if old_pid and _is_pid_running(old_pid):
            print(f"[LOCK] Another instance of '{name}' is already running (PID {old_pid}). Exiting.")
            return False
        os.remove(lock_path)

    with open(lock_path, "w") as f:
        f.write(str(os.getpid()))
    _active_locks.append(lock_path)
    return True


def release_locks():
    """Remove all acquired lock files."""
    global _active_locks
    for path in _active_locks:
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass
    _active_locks = []


def _is_pid_running(pid):
    """Check if a process with the given PID is still running."""
    if sys.platform.startswith("linux"):
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    else:
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(1, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return os.path.exists(f"/proc/{pid}")


def cleanup_previous():
    """Register atexit and signal handlers to clean up locks on exit."""
    import atexit
    atexit.register(release_locks)
    for sig in (signal.SIGTERM, signal.SIGINT, getattr(signal, 'SIGHUP', signal.SIGTERM)):
        try:
            signal.signal(sig, lambda s, f: (release_locks(), sys.exit(1)))
        except (ValueError, AttributeError):
            pass
