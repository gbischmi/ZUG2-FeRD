from __future__ import annotations

import base64
import hashlib
import logging
import struct
import uuid
import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from zug2ferd.qt import QtCore


class LogBus(QtCore.QObject):
    line = QtCore.pyqtSignal(str) if hasattr(QtCore, "pyqtSignal") else QtCore.Signal(str)


class _QtLogHandler(logging.Handler):
    def __init__(self, bus: LogBus) -> None:
        super().__init__()
        self._bus = bus

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:
            msg = record.getMessage()
        self._bus.line.emit(msg)


class BlackBoxStore:
    def __init__(self, sys_cache_path: Path) -> None:
        self._path = sys_cache_path
        self._fernet = _build_fernet()

    @property
    def available(self) -> bool:
        return self._fernet is not None

    def append_line(self, plaintext_line: str) -> None:
        if self._fernet is None:
            return

        token = self._fernet.encrypt(zlib.compress(plaintext_line.encode("utf-8")))
        block = struct.pack(">I", len(token)) + token

        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "ab") as f:
            f.write(block)

        try:
            self._rotate_older_than(days=365)
        except Exception:
            return

    def read_lines(self) -> list[str]:
        if self._fernet is None or not self._path.exists():
            return []

        out: list[str] = []
        with open(self._path, "rb") as f:
            while True:
                header = f.read(4)
                if not header:
                    break
                if len(header) != 4:
                    break
                (n,) = struct.unpack(">I", header)
                if n <= 0 or n > 20_000_000:
                    break
                token = f.read(n)
                if len(token) != n:
                    break
                try:
                    data = self._fernet.decrypt(token)
                    line = zlib.decompress(data).decode("utf-8", errors="replace")
                except Exception:
                    continue
                out.append(line)
        return out

    def read_text(self) -> str:
        return "\n".join(self.read_lines())

    def restore_system_log_if_needed(self, system_log_path: Path) -> bool:
        lines = self.read_lines()
        if not lines:
            return False

        if not system_log_path.exists():
            system_log_path.parent.mkdir(parents=True, exist_ok=True)
            system_log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return True

        try:
            content = system_log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            content = []

        last_plain = next((l for l in reversed(content) if l.strip()), "")
        last_audit = next((l for l in reversed(lines) if l.strip()), "")

        if not content or (last_audit and last_plain != last_audit):
            system_log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return True

        return False

    def _rotate_older_than(self, days: int) -> None:
        if self._fernet is None or not self._path.exists():
            return

        cutoff = datetime.now() - timedelta(days=days)
        lines = self.read_lines()
        kept: list[str] = []

        for line in lines:
            ts = _parse_log_timestamp(line)
            if ts is None or ts >= cutoff:
                kept.append(line)

        if len(kept) == len(lines):
            return

        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.unlink(missing_ok=True)
        with open(tmp, "wb") as f:
            for line in kept:
                token = self._fernet.encrypt(zlib.compress(line.encode("utf-8")))
                f.write(struct.pack(">I", len(token)))
                f.write(token)
        tmp.replace(self._path)


def _parse_log_timestamp(line: str) -> Optional[datetime]:
    if len(line) < 19:
        return None
    head = line[:19]
    try:
        return datetime.strptime(head, "%d.%m.%Y %H:%M:%S")
    except Exception:
        return None


def _build_fernet():
    try:
        from cryptography.fernet import Fernet
    except Exception:
        return None

    node = uuid.getnode()
    digest = hashlib.sha256(str(node).encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest[:32])
    return Fernet(key)


class _BlackBoxHandler(logging.Handler):
    def __init__(self, store: BlackBoxStore) -> None:
        super().__init__()
        self._store = store

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:
            msg = record.getMessage()
        self._store.append_line(msg)


@dataclass(frozen=True)
class Telemetry:
    logger: logging.Logger
    bus: LogBus
    blackbox: BlackBoxStore


class _LocalTimeFormatter(logging.Formatter):
    def formatTime(self, record: logging.LogRecord, datefmt: Optional[str] = None) -> str:
        dt = datetime.fromtimestamp(record.created)
        return dt.strftime("%d.%m.%Y %H:%M:%S")


def setup_telemetry(system_log_path: str, sys_cache_path: str) -> Telemetry:
    bus = LogBus()
    blackbox = BlackBoxStore(Path(sys_cache_path))
    blackbox.restore_system_log_if_needed(Path(system_log_path))

    logger = logging.getLogger("zug2ferd")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    formatter = _LocalTimeFormatter("%(asctime)s | %(levelname)s | %(message)s")

    file_handler = logging.FileHandler(system_log_path, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)

    qt_handler = _QtLogHandler(bus)
    qt_handler.setLevel(logging.INFO)
    qt_handler.setFormatter(formatter)

    blackbox_handler = _BlackBoxHandler(blackbox)
    blackbox_handler.setLevel(logging.INFO)
    blackbox_handler.setFormatter(formatter)

    logger.handlers.clear()
    logger.addHandler(file_handler)
    logger.addHandler(qt_handler)
    logger.addHandler(blackbox_handler)

    if not blackbox.available:
        logger.info("Krypto-Blackbox deaktiviert (cryptography nicht verfügbar).")

    return Telemetry(logger=logger, bus=bus, blackbox=blackbox)
