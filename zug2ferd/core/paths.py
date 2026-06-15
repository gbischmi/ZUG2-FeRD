from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppPaths:
    root_dir: Path
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


def build_paths(root_dir: Path) -> AppPaths:
    return AppPaths(
        root_dir=root_dir,
        ui_dir=root_dir / "zug2ferd" / "ui",
        bin_dir=root_dir / "bin",
        packages_dir=root_dir / "packages",
        log_dir=root_dir / "log",
        sys_cache_path=root_dir / ".sys_cache.dat",
        settings_path=root_dir / ".zug2ferd_settings.json",
        stationery_dir=root_dir / "stationery",
        tmp_dir=root_dir / "tmp",
    )


def ensure_runtime_dirs(paths: AppPaths) -> None:
    paths.bin_dir.mkdir(parents=True, exist_ok=True)
    paths.packages_dir.mkdir(parents=True, exist_ok=True)
    paths.log_dir.mkdir(parents=True, exist_ok=True)
    paths.stationery_dir.mkdir(parents=True, exist_ok=True)
    paths.tmp_dir.mkdir(parents=True, exist_ok=True)
