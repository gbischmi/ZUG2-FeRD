from __future__ import annotations

import sys

from zug2ferd.core.deps import DependencyManager
from zug2ferd.core.paths import build_paths, detect_project_root, ensure_runtime_dirs
from zug2ferd.core.settings import SettingsStore
from zug2ferd.core.telemetry import setup_telemetry
from zug2ferd.qt import QtCore, QtWidgets
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


def run(argv: list[str]) -> int:
    QtCore.QCoreApplication.setAttribute(QtCore.Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)
    QtCore.QCoreApplication.setAttribute(QtCore.Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)

    root = detect_project_root()
    paths = build_paths(root)
    ensure_runtime_dirs(paths)

    telemetry = setup_telemetry(str(paths.system_log_path), str(paths.sys_cache_path))
    settings = SettingsStore(paths.settings_path, paths.root_dir)
    settings.load()

    app = QtWidgets.QApplication(argv)
    _apply_dark_fluent_style(app)

    deps = DependencyManager(paths, telemetry)
    w = MainWindow(paths, telemetry, deps, settings)
    w.show()

    telemetry.logger.info("Systemstart abgeschlossen.")
    telemetry.logger.info(f"Root: {paths.root_dir}")
    telemetry.logger.info(f"bin/: {paths.bin_dir}")
    telemetry.logger.info(f"packages/: {paths.packages_dir}")
    telemetry.logger.info(f"log/: {paths.log_dir}")
    telemetry.logger.info(f".sys_cache.dat: {paths.sys_cache_path}")
    telemetry.logger.info(f"settings: {paths.settings_path}")

    QtCore.QTimer.singleShot(50, deps.start_background_bootstrap)

    return int(app.exec())
