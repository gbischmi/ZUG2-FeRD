from __future__ import annotations

import json
import platform
import shutil
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from zug2ferd.core.paths import AppPaths
from zug2ferd.core.telemetry import Telemetry
from zug2ferd.qt import QtCore


Signal = QtCore.pyqtSignal if hasattr(QtCore, "pyqtSignal") else QtCore.Signal


@dataclass
class RuntimeSelection:
    mustang_jar: Optional[Path] = None
    jre_dir: Optional[Path] = None
    java_exe: Optional[Path] = None


class DependencyManager(QtCore.QObject):
    versions_changed = Signal()
    selection_changed = Signal()

    def __init__(self, paths: AppPaths, telemetry: Telemetry) -> None:
        super().__init__()
        self._paths = paths
        self._telemetry = telemetry
        self._lock = threading.Lock()
        self.selection = RuntimeSelection()

    @property
    def paths(self) -> AppPaths:
        return self._paths

    def list_mustang_jars(self) -> list[Path]:
        if not self._paths.bin_dir.exists():
            return []
        jars = sorted([p for p in self._paths.bin_dir.glob("*.jar") if p.is_file()], key=lambda p: p.name.lower())
        return jars

    def list_jre_dirs(self) -> list[Path]:
        if not self._paths.packages_dir.exists():
            return []
        dirs = []
        for p in self._paths.packages_dir.iterdir():
            if not p.is_dir():
                continue
            java = self._find_java_exe(p)
            if java is not None:
                dirs.append(p)
        return sorted(dirs, key=lambda p: p.name.lower())

    def _find_java_exe(self, jre_dir: Path) -> Optional[Path]:
        sysname = platform.system().lower()
        if sysname.startswith("win"):
            candidates = [jre_dir / "bin" / "java.exe"]
        else:
            candidates = [jre_dir / "bin" / "java", jre_dir / "Contents" / "Home" / "bin" / "java"]
        for cand in candidates:
            if cand.exists():
                return cand
        return None

    def set_selected_mustang_by_name(self, name: str) -> None:
        jars = self.list_mustang_jars()
        target = next((p for p in jars if p.name == name), None)
        with self._lock:
            self.selection.mustang_jar = target
        self.selection_changed.emit()

    def set_selected_jre_by_name(self, name: str) -> None:
        dirs = self.list_jre_dirs()
        target = next((p for p in dirs if p.name == name), None)
        java = self._find_java_exe(target) if target else None
        with self._lock:
            self.selection.jre_dir = target
            self.selection.java_exe = java
        self.selection_changed.emit()

    def cleanup_unused(self, keep_jar: Optional[Path], keep_jre: Optional[Path]) -> None:
        removed: list[str] = []

        for jar in self.list_mustang_jars():
            if keep_jar is not None and jar.resolve() == keep_jar.resolve():
                continue
            try:
                jar.unlink(missing_ok=True)
                removed.append(f"bin/{jar.name}")
            except Exception:
                self._telemetry.logger.info(f"Konnte JAR nicht löschen: {jar.name}")

        for jre in self.list_jre_dirs():
            if keep_jre is not None and jre.resolve() == keep_jre.resolve():
                continue
            try:
                shutil.rmtree(jre)
                removed.append(f"packages/{jre.name}/")
            except Exception:
                self._telemetry.logger.info(f"Konnte JRE nicht löschen: {jre.name}")

        if removed:
            self._telemetry.logger.info("Bereinigung abgeschlossen. Entfernt: " + ", ".join(removed))
        else:
            self._telemetry.logger.info("Bereinigung abgeschlossen. Keine ungenutzten Pakete gefunden.")

        self.versions_changed.emit()

    def start_background_bootstrap(self) -> None:
        pool = QtCore.QThreadPool.globalInstance()
        pool.start(_Runnable(lambda: self._ensure_jre()))
        pool.start(_Runnable(lambda: self._ensure_mustang()))

    def _ensure_jre(self) -> None:
        if self.list_jre_dirs():
            jres = self.list_jre_dirs()
            chosen = jres[-1]
            self._telemetry.logger.info(f"JRE gefunden: {chosen.name}")
            self._select_jre_dir(chosen)
            self._emit_versions_changed()
            return

        self._telemetry.logger.info("Keine portable JRE gefunden. Download wird gestartet (Hintergrund).")
        try:
            jre_dir = self._download_and_install_jre()
        except Exception:
            self._telemetry.logger.info("JRE-Download fehlgeschlagen.")
            return

        self._telemetry.logger.info(f"JRE installiert: {jre_dir.name}")
        self._select_jre_dir(jre_dir)
        self._emit_versions_changed()

    def _download_and_install_jre(self) -> Path:
        os_name = platform.system().lower()
        if os_name.startswith("win"):
            os_api = "windows"
        elif os_name.startswith("linux"):
            os_api = "linux"
        elif os_name.startswith("darwin") or os_name.startswith("mac"):
            os_api = "mac"
        else:
            raise RuntimeError("Nicht unterstütztes Betriebssystem")

        arch = platform.machine().lower()
        if arch in {"amd64", "x86_64"}:
            arch_api = "x64"
        elif arch in {"arm64", "aarch64"}:
            arch_api = "aarch64"
        else:
            arch_api = "x64"

        api_url = f"https://api.adoptium.net/v3/binary/latest/17/ga/{os_api}/{arch_api}/jre/hotspot/normal/eclipse"
        archive = self._paths.packages_dir / ".jre_download.tmp"
        if archive.exists():
            archive.unlink(missing_ok=True)

        self._download_file(api_url, archive)

        extract_dir = self._paths.packages_dir / ".jre_extract.tmp"
        if extract_dir.exists():
            shutil.rmtree(extract_dir)
        extract_dir.mkdir(parents=True, exist_ok=True)

        try:
            with open(archive, "rb") as f:
                head = f.read(4)

            if head[:2] == b"PK":
                import zipfile

                with zipfile.ZipFile(archive, "r") as zf:
                    zf.extractall(extract_dir)
            else:
                import tarfile

                with tarfile.open(archive, "r:*") as tf:
                    tf.extractall(extract_dir)
        finally:
            archive.unlink(missing_ok=True)

        children = [p for p in extract_dir.iterdir() if p.is_dir()]
        if len(children) != 1:
            shutil.rmtree(extract_dir, ignore_errors=True)
            raise RuntimeError("JRE-Archiv hat unerwartete Struktur")

        installed = self._paths.packages_dir / children[0].name
        if installed.exists():
            shutil.rmtree(installed)
        shutil.move(str(children[0]), str(installed))
        shutil.rmtree(extract_dir, ignore_errors=True)

        java = self._find_java_exe(installed)
        if java is None:
            shutil.rmtree(installed, ignore_errors=True)
            raise RuntimeError("JRE ohne java-Binary")

        return installed

    def _ensure_mustang(self) -> None:
        jars = [p for p in self.list_mustang_jars() if "mustang" in p.name.lower()]
        local_latest = jars[-1] if jars else None

        try:
            latest = self._fetch_latest_mustang_asset()
        except Exception:
            self._telemetry.logger.info("Mustang-Update-Prüfung fehlgeschlagen (GitHub-API).")
            return

        if latest is None:
            self._telemetry.logger.info("GitHub-Release enthält keine Mustang-CLI-JAR.")
            return

        latest_name, latest_url = latest
        if local_latest is None:
            self._telemetry.logger.info(f"Mustang-CLI fehlt. Download {latest_name} wird gestartet (Hintergrund).")
        elif local_latest.name != latest_name:
            self._telemetry.logger.info(
                f"Neue Prüfmethode (Mustang-CLI Version {latest_name}) verfügbar! Update wird im Hintergrund geladen."
            )
        else:
            self._select_mustang_jar(local_latest)
            self._emit_versions_changed()
            return

        dest = self._paths.bin_dir / latest_name
        tmp = self._paths.bin_dir / (latest_name + ".tmp")
        try:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            self._download_file(latest_url, tmp, headers={"User-Agent": "ZUG2-FeRD"})

            with open(tmp, "rb") as f:
                head = f.read(2)
            if head != b"PK":
                raise RuntimeError("JAR-Kopf ungültig")

            if dest.exists():
                dest.unlink(missing_ok=True)
            tmp.rename(dest)
        except Exception:
            tmp.unlink(missing_ok=True)
            self._telemetry.logger.info("Mustang-Download fehlgeschlagen. Lokale Version bleibt aktiv.")
            if local_latest is not None:
                self._select_mustang_jar(local_latest)
            self._emit_versions_changed()
            return

        self._telemetry.logger.info(f"Mustang-CLI bereit: {dest.name}")
        self._select_mustang_jar(dest)
        self._emit_versions_changed()

    def _fetch_latest_mustang_asset(self) -> Optional[tuple[str, str]]:
        api = "https://api.github.com/repos/ZUGFeRD/mustangproject/releases/latest"
        req = urllib.request.Request(api, headers={"User-Agent": "ZUG2-FeRD"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        assets = data.get("assets", [])
        for asset in assets:
            name = str(asset.get("name", ""))
            url = str(asset.get("browser_download_url", ""))
            if not name or not url:
                continue
            low = name.lower()
            if low.endswith(".jar") and "mustang" in low and "cli" in low:
                return name, url
        for asset in assets:
            name = str(asset.get("name", ""))
            url = str(asset.get("browser_download_url", ""))
            if not name or not url:
                continue
            if name.lower().endswith(".jar"):
                return name, url
        return None

    def _download_file(self, url: str, dest: Path, headers: Optional[dict[str, str]] = None) -> None:
        req = urllib.request.Request(url, headers=headers or {"User-Agent": "ZUG2-FeRD"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as f:
                shutil.copyfileobj(resp, f)

    def _select_mustang_jar(self, jar: Path) -> None:
        with self._lock:
            self.selection.mustang_jar = jar
        self._emit_selection_changed()

    def _select_jre_dir(self, jre_dir: Path) -> None:
        java = self._find_java_exe(jre_dir)
        with self._lock:
            self.selection.jre_dir = jre_dir
            self.selection.java_exe = java
        self._emit_selection_changed()

    def _emit_versions_changed(self) -> None:
        QtCore.QMetaObject.invokeMethod(self, "_emit_versions_changed_main", QtCore.Qt.ConnectionType.QueuedConnection)

    def _emit_selection_changed(self) -> None:
        QtCore.QMetaObject.invokeMethod(self, "_emit_selection_changed_main", QtCore.Qt.ConnectionType.QueuedConnection)

    @QtCore.pyqtSlot() if hasattr(QtCore, "pyqtSlot") else QtCore.Slot()
    def _emit_versions_changed_main(self) -> None:
        self.versions_changed.emit()

    @QtCore.pyqtSlot() if hasattr(QtCore, "pyqtSlot") else QtCore.Slot()
    def _emit_selection_changed_main(self) -> None:
        self.selection_changed.emit()


class _Runnable(QtCore.QRunnable):
    def __init__(self, fn) -> None:
        super().__init__()
        self._fn = fn

    def run(self) -> None:
        self._fn()
