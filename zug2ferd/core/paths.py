from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import os
import sys
import tempfile
import shutil


@dataclass(frozen=True)
class AppPaths:
    root_dir: Path
    runtime_dir: Path
    # Zentrale UI-Ressourcen (Logo/Icon) liegen bewusst direkt unter zug2ferd/ui.
    ui_dir: Path
    bin_dir: Path
    packages_dir: Path
    log_dir: Path
    sys_cache_path: Path
    settings_path: Path
    stationery_dir: Path
    tmp_dir: Path

    @property
    def system_log_path(self) -> Path:
        return self.log_dir / "system.log"

    @property
    def logo_path(self) -> Path:
        return self.ui_dir / "logo.png"


def detect_project_root() -> Path:
    return Path(__file__).resolve().parents[2]


# Zeigerdatei, die der "ZUG2-FeRD Installer" neben die ZUG2-FeRD.exe schreibt.
# Inhalt: {"data_dir": "<Datenverzeichnis>"} – Umgebungsvariablen (%ProgramData% …) sind erlaubt.
RUNTIME_POINTER_FILENAME = "zug2ferd_runtime.json"
# Optionale Übersteuerung (z. B. für Tests): Umgebungsvariable mit dem Datenverzeichnis.
RUNTIME_ENV_VAR = "ZUG2FERD_DATA_DIR"


def _expand(raw: str) -> Path:
    return Path(os.path.expandvars(raw.strip())).expanduser()


def _read_runtime_pointer(exe_dir: Path) -> Path | None:
    pointer = exe_dir / RUNTIME_POINTER_FILENAME
    if not pointer.is_file():
        return None
    try:
        data = json.loads(pointer.read_text(encoding="utf-8-sig"))
    except Exception:
        return None
    raw = data.get("data_dir") if isinstance(data, dict) else None
    if not isinstance(raw, str) or not raw.strip():
        return None
    return _expand(raw)


def detect_runtime_root(project_root: Path) -> Path:
    env_dir = os.environ.get(RUNTIME_ENV_VAR, "")
    if env_dir.strip():
        return _expand(env_dir)
    if getattr(sys, "frozen", False):
        installed = _read_runtime_pointer(Path(sys.executable).resolve().parent)
        if installed is not None:
            return installed
    if getattr(sys, "frozen", False) or "_MEI" in project_root.name:
        return Path(tempfile.gettempdir()) / "ZUG2-FeRD"
    return project_root


def migrate_transient_runtime(project_root: Path, runtime_root: Path) -> tuple[list[str], list[str]]:
    if project_root.resolve() == runtime_root.resolve():
        return ([], [])
    if project_root.name.startswith("_MEI") is False:
        return ([], [])
    if runtime_root.exists() is False:
        runtime_root.mkdir(parents=True, exist_ok=True)

    copied: list[str] = []
    errors: list[str] = []

    def copy_file(src: Path, dst: Path) -> None:
        if src.exists() is False or src.is_file() is False:
            return
        if dst.exists():
            return
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied.append(dst.name)

    def copy_dir_contents(src_dir: Path, dst_dir: Path, pattern: str) -> None:
        if src_dir.exists() is False or src_dir.is_dir() is False:
            return
        dst_dir.mkdir(parents=True, exist_ok=True)
        for src in src_dir.glob(pattern):
            if src.is_file() is False:
                continue
            dst = dst_dir / src.name
            if dst.exists():
                continue
            shutil.copy2(src, dst)
            copied.append(f"{dst_dir.name}/{dst.name}")

    def has_java_binary(dir_path: Path) -> bool:
        if (dir_path / "bin" / "java.exe").exists():
            return True
        if (dir_path / "bin" / "java").exists():
            return True
        if (dir_path / "Contents" / "Home" / "bin" / "java").exists():
            return True
        return False

    try:
        copy_file(project_root / ".sys_cache.dat", runtime_root / ".sys_cache.dat")
        copy_file(project_root / ".zug2ferd_settings.json", runtime_root / ".zug2ferd_settings.json")
        copy_file(project_root / "log" / "system.log", runtime_root / "log" / "system.log")

        copy_dir_contents(project_root / "bin", runtime_root / "bin", "*.jar")

        src_packages = project_root / "packages"
        dst_packages = runtime_root / "packages"
        if src_packages.exists() and src_packages.is_dir():
            dst_packages.mkdir(parents=True, exist_ok=True)
            for child in src_packages.iterdir():
                if child.is_dir() is False:
                    continue
                if child.name.startswith("."):
                    continue
                if has_java_binary(child) is False:
                    continue
                target = dst_packages / child.name
                if target.exists():
                    continue
                shutil.copytree(child, target)
                copied.append(f"packages/{child.name}/")

        src_stationery = project_root / "stationery"
        dst_stationery = runtime_root / "stationery"
        copy_dir_contents(src_stationery, dst_stationery, "*.pdf")
    except Exception as exc:
        errors.append(str(exc))

    return (copied, errors)


def build_paths(root_dir: Path, runtime_dir: Path) -> AppPaths:
    return AppPaths(
        root_dir=root_dir,
        runtime_dir=runtime_dir,
        ui_dir=root_dir / "zug2ferd" / "ui",
        bin_dir=runtime_dir / "bin",
        packages_dir=runtime_dir / "packages",
        log_dir=runtime_dir / "log",
        sys_cache_path=runtime_dir / ".sys_cache.dat",
        settings_path=runtime_dir / ".zug2ferd_settings.json",
        stationery_dir=runtime_dir / "stationery",
        tmp_dir=runtime_dir / "tmp",
    )


def ensure_runtime_dirs(paths: AppPaths) -> None:
    paths.runtime_dir.mkdir(parents=True, exist_ok=True)
    paths.bin_dir.mkdir(parents=True, exist_ok=True)
    paths.packages_dir.mkdir(parents=True, exist_ok=True)
    paths.log_dir.mkdir(parents=True, exist_ok=True)
    paths.stationery_dir.mkdir(parents=True, exist_ok=True)
    paths.tmp_dir.mkdir(parents=True, exist_ok=True)
