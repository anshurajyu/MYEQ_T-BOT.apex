"""One cooperating motor process per serial device, across repository checkouts.

Locks live outside the checkout and are held by the kernel, not by a PID file.
Unmodified external programs can bypass this cooperative lock. Where the SDK
exposes a serial descriptor we additionally request the OS exclusive-open flag.
"""
import atexit
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import termios
import time


def canonical_port(port: str) -> str:
    path = os.path.realpath(os.path.expanduser(str(port)))
    # macOS tty/callout names address the same adapter through different minors.
    if path.startswith('/dev/tty.'):
        path = '/dev/cu.' + path[len('/dev/tty.'):]
    return path


def lock_path(port: str) -> Path:
    directory = Path('/tmp') / f'tbot-serial-owner-{os.getuid()}'
    directory.mkdir(mode=0o700, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError(f'Unsafe serial ownership directory: {directory}')
    key = hashlib.sha256(canonical_port(port).encode()).hexdigest()
    return directory / f'{key}.lock'


class SerialOwner:
    def __init__(self, port: str, purpose: str = 'T-BOT motor controller'):
        self.port = canonical_port(port)
        self.purpose = purpose
        self.fd = None
        self.path = lock_path(self.port)

    def acquire(self):
        if self.fd is not None:
            return self
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise RuntimeError(f'Unsafe serial ownership file: {self.path}')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                try:
                    owner = json.loads(os.read(fd, 4096).decode())
                    detail = f"{owner.get('purpose', 'motor controller')} (PID {owner.get('pid', '?')})"
                except (ValueError, OSError):
                    detail = 'another motor controller'
                raise ValueError(f'Serial device {self.port} is already owned by {detail}. Stop that process before connecting.') from exc
            metadata = {'port': self.port, 'pid': os.getpid(), 'purpose': self.purpose,
                        'program': Path(sys.argv[0]).name, 'acquired_at': time.time()}
            os.ftruncate(fd, 0)
            os.write(fd, json.dumps(metadata).encode())
            os.fsync(fd)
            self.fd = fd
            atexit.register(self.release)
            return self
        except BaseException:
            os.close(fd)
            raise

    def release(self):
        if self.fd is not None:
            fd, self.fd = self.fd, None
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
            atexit.unregister(self.release)
        # Never unlink a lock file: waiters must continue using the same inode.

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *_):
        self.release()


def request_os_exclusive(connection) -> bool:
    """Best effort for SDKs exposing a pyserial-like descriptor; no extra open."""
    if not hasattr(termios, 'TIOCEXCL'):
        return False
    pending, visited = [(connection, 0)], set()
    while pending:
        candidate, depth = pending.pop()
        if id(candidate) in visited:
            continue
        visited.add(id(candidate))
        fileno = getattr(candidate, 'fileno', None)
        if callable(fileno):
            try:
                fd = fileno()
                if os.isatty(fd):
                    fcntl.ioctl(fd, termios.TIOCEXCL)
                    return True
            except (OSError, ValueError, TypeError):
                pass
        if depth < 2:
            for name in ('serial', '_serial', 'ser', 'port', '_port', 'port_handler'):
                child = getattr(candidate, name, None)
                if child is not None and not isinstance(child, (str, bytes, int)):
                    pending.append((child, depth + 1))
    return False


class OwnedST3215:
    """Preserve the existing SDK interface while owning the bus until close."""
    def __init__(self, port: str, *args, **kwargs):
        from python_st3215 import ST3215
        self._owner = SerialOwner(port, Path(sys.argv[0]).name).acquire()
        self._bus = None
        try:
            self._bus = ST3215(port, *args, **kwargs)
            self.os_exclusive = request_os_exclusive(self._bus)
            atexit.register(self.close)
        except BaseException:
            self._owner.release()
            raise

    def __getattr__(self, name):
        return getattr(self._bus, name)

    def close(self):
        bus, self._bus = self._bus, None
        try:
            if bus is not None:
                bus.close()
        finally:
            self._owner.release()
            atexit.unregister(self.close)
