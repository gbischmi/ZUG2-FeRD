from __future__ import annotations

from typing import Any


def _import_pyqt6() -> Any:
    from PyQt6 import QtCore, QtGui, QtWidgets

    return QtCore, QtGui, QtWidgets


def _import_pyside6() -> Any:
    from PySide6 import QtCore, QtGui, QtWidgets

    return QtCore, QtGui, QtWidgets


try:
    QtCore, QtGui, QtWidgets = _import_pyqt6()
except Exception:
    QtCore, QtGui, QtWidgets = _import_pyside6()


__all__ = ["QtCore", "QtGui", "QtWidgets"]
