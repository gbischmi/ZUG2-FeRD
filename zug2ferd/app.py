from __future__ import annotations

import ctypes
import platform
import sys

from zug2ferd.core.deps import DependencyManager
from zug2ferd.core.paths import (
    build_paths,
    detect_project_root,
    detect_runtime_root,
    ensure_runtime_dirs,
    migrate_transient_runtime,
)
from zug2ferd.core.settings import SettingsStore
from zug2ferd.core.telemetry import setup_telemetry
from zug2ferd.qt import QtCore, QtGui, QtWidgets
from zug2ferd.ui.main_window import MainWindow


def _apply_dark_fluent_style(app: QtWidgets.QApplication) -> None:
    app.setStyle("Fusion")
    app.setStyleSheet(
        """
        QWidget { background-color: #0f1115; color: #e8e8ea; font-family: Segoe UI, Inter, Arial; }
        QLabel#HeaderTitle { font-size: 18px; font-weight: 700; letter-spacing: 0.6px; }

        QToolButton#GearButton {
            background-color: #171b22;
            border: 1px solid #2a2f3a;
            border-radius: 10px;
        }
        QToolButton#GearButton:hover { border-color: #3b4252; background-color: #1b202a; }

        QTextEdit#LogViewer {
            background-color: #0c0e12;
            border: 1px solid #2a2f3a;
            border-radius: 12px;
            padding: 10px;
            font-family: Consolas, Cascadia Mono, monospace;
            font-size: 12px;
        }

        QFrame#OptionsPanel {
            background-color: #12151b;
            border: 1px solid #2a2f3a;
            border-radius: 14px;
        }
        QLabel#OptionsTitle { font-size: 14px; font-weight: 700; }
        QCheckBox { spacing: 10px; }
        QCheckBox::indicator {
            width: 18px; height: 18px;
            border-radius: 5px;
            border: 1px solid #3a3f4a;
            background-color: #0c0e12;
        }
        QCheckBox::indicator:checked { background-color: #2b7cd3; border-color: #2b7cd3; }

        QComboBox {
            background-color: #0c0e12;
            border: 1px solid #2a2f3a;
            border-radius: 10px;
            padding: 6px 10px;
        }
        QComboBox::drop-down { border: 0px; width: 30px; }

        QPushButton {
            background-color: #2b7cd3;
            border: 1px solid #2b7cd3;
            border-radius: 10px;
            padding: 8px 12px;
            font-weight: 600;
        }
        QPushButton:hover { background-color: #2f86e6; border-color: #2f86e6; }
        QPushButton:pressed { background-color: #266dba; border-color: #266dba; }

        QLabel#FooterDisclaimer {
            color: #b3b8c2;
            font-size: 11px;
            padding: 8px 2px 0px 2px;
        }
        """
    )


def _set_optional_qt_attribute(attribute_name: str) -> None:
    attr_enum = getattr(QtCore.Qt, "ApplicationAttribute", None)
    if attr_enum is None:
        return
    attr_value = getattr(attr_enum, attribute_name, None)
    if attr_value is None:
        return
    QtCore.QCoreApplication.setAttribute(attr_value, True)


def _configure_windows_app_id() -> None:
    if platform.system().lower() != "windows":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ZUG2FeRD.CommunityEdition")
    except Exception:
        return


def _build_app_icon(logo_path) -> QtGui.QIcon:
    icon = QtGui.QIcon()
    if logo_path.exists():
        icon.addFile(str(logo_path), QtCore.QSize(16, 16))
        icon.addFile(str(logo_path), QtCore.QSize(24, 24))
        icon.addFile(str(logo_path), QtCore.QSize(32, 32))
        icon.addFile(str(logo_path), QtCore.QSize(48, 48))
        icon.addFile(str(logo_path), QtCore.QSize(64, 64))
        icon.addFile(str(logo_path), QtCore.QSize(128, 128))
        icon.addFile(str(logo_path), QtCore.QSize(256, 256))
    return icon


def run(argv: list[str]) -> int:
    _set_optional_qt_attribute("AA_EnableHighDpiScaling")
    _set_optional_qt_attribute("AA_UseHighDpiPixmaps")
    _configure_windows_app_id()

    root = detect_project_root()
    runtime_root = detect_runtime_root(root)
    migrated, migration_errors = migrate_transient_runtime(root, runtime_root)
    paths = build_paths(root, runtime_root)
    ensure_runtime_dirs(paths)

    telemetry = setup_telemetry(str(paths.system_log_path), str(paths.sys_cache_path))
    settings = SettingsStore(paths.settings_path, paths.runtime_dir)
    settings.load()

    app = QtWidgets.QApplication(argv)
    app.setApplicationName("ZUG2-FeRD")
    app.setApplicationDisplayName("ZUG2-FeRD Community Edition")
    if hasattr(app, "setDesktopFileName"):
        app.setDesktopFileName("zug2ferd-community-edition")
    app_icon = _build_app_icon(paths.logo_path)
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)
    _apply_dark_fluent_style(app)

    deps = DependencyManager(paths, telemetry)
    w = MainWindow(paths, telemetry, deps, settings)
    w.show()

    telemetry.logger.info("Systemstart abgeschlossen.")
    telemetry.logger.info(f"Root: {paths.root_dir}")
    telemetry.logger.info(f"Runtime: {paths.runtime_dir}")
    telemetry.logger.info(f"bin/: {paths.bin_dir}")
    telemetry.logger.info(f"packages/: {paths.packages_dir}")
    telemetry.logger.info(f"log/: {paths.log_dir}")
    telemetry.logger.info(f".sys_cache.dat: {paths.sys_cache_path}")
    telemetry.logger.info(f"settings: {paths.settings_path}")
    if migrated:
        telemetry.logger.info("Runtime-Migration: übernommen: " + ", ".join(migrated))
    if migration_errors:
        telemetry.logger.info("Runtime-Migration: Fehler: " + ", ".join(migration_errors))

    QtCore.QTimer.singleShot(50, deps.start_background_bootstrap)

    return int(app.exec())
