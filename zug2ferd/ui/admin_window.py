from __future__ import annotations

from zug2ferd.core.telemetry import Telemetry
from zug2ferd.qt import QtCore, QtWidgets


class AdminWindow(QtWidgets.QDialog):
    def __init__(self, telemetry: Telemetry, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._telemetry = telemetry
        self.setWindowTitle("Admin")
        self.setModal(False)
        self.resize(780, 520)

        self._text = QtWidgets.QTextEdit()
        self._text.setReadOnly(True)

        self._export_btn = QtWidgets.QPushButton("Forensischen Audit-Trail als TXT exportieren")

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        layout.addWidget(self._text, 1)
        layout.addWidget(self._export_btn, 0, QtCore.Qt.AlignmentFlag.AlignRight)

        self._export_btn.clicked.connect(self._export_txt)
        self._reload()

    def showEvent(self, event) -> None:
        self._reload()
        super().showEvent(event)

    def _reload(self) -> None:
        try:
            self._text.setPlainText(self._telemetry.blackbox.read_text())
        except Exception:
            self._text.setPlainText("")

    def _export_txt(self) -> None:
        dlg = QtWidgets.QFileDialog(self)
        dlg.setAcceptMode(QtWidgets.QFileDialog.AcceptMode.AcceptSave)
        dlg.setNameFilter("Textdateien (*.txt)")
        dlg.setDefaultSuffix("txt")
        if dlg.exec():
            path = dlg.selectedFiles()[0]
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(self._text.toPlainText())
            except Exception:
                pass
