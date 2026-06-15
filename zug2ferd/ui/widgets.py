from __future__ import annotations

from typing import List

from zug2ferd.qt import QtCore, QtGui, QtWidgets


Signal = QtCore.pyqtSignal if hasattr(QtCore, "pyqtSignal") else QtCore.Signal


class ClickableLabel(QtWidgets.QLabel):
    clicked = Signal()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class StatusLamp(QtWidgets.QFrame):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("StatusLamp")
        self.setFixedHeight(34)

        self._label = QtWidgets.QLabel("NEUTRAL", self)
        self._label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.addWidget(self._label)

        self.set_state("neutral")

    def set_state(self, state: str) -> None:
        if state == "ok":
            color = "#1f8f5f"
            text = "OK"
        elif state == "warn":
            color = "#d8a600"
            text = "WARNUNG"
        elif state == "error":
            color = "#c83a3a"
            text = "FEHLER"
        else:
            color = "#5a5f6a"
            text = "NEUTRAL"

        self._label.setText(text)
        self.setStyleSheet(
            "QFrame#StatusLamp {"
            f"background-color: {color};"
            "border-radius: 17px;"
            "}"
            "QFrame#StatusLamp QLabel {"
            "color: #ffffff;"
            "font-weight: 600;"
            "letter-spacing: 0.5px;"
            "}"
        )


class DropArea(QtWidgets.QFrame):
    files_dropped = Signal(list)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DropArea")
        self.setAcceptDrops(True)
        self.setMinimumHeight(220)

        self._icon = QtWidgets.QLabel()
        self._icon.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        pix = self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_FileDialogContentsView).pixmap(
            72, 72
        )
        self._icon.setPixmap(pix)

        self._text = QtWidgets.QLabel("ZUGFeRD-Rechnung oder Begleitdokument hierher ziehen")
        self._text.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._text.setWordWrap(True)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)
        layout.addStretch(1)
        layout.addWidget(self._icon, 0)
        layout.addWidget(self._text, 0)
        layout.addStretch(1)

        self._set_hover(False)

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._set_hover(True)
            return
        event.ignore()

    def dragLeaveEvent(self, event: QtGui.QDragLeaveEvent) -> None:
        self._set_hover(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QtGui.QDropEvent) -> None:
        self._set_hover(False)
        urls = event.mimeData().urls()
        paths: List[str] = []
        for url in urls:
            if url.isLocalFile():
                paths.append(url.toLocalFile())
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()
            return
        event.ignore()

    def _set_hover(self, hover: bool) -> None:
        if hover:
            self.setStyleSheet(
                "QFrame#DropArea {"
                "background-color: #20252f;"
                "border: 2px dashed #4da3ff;"
                "border-radius: 14px;"
                "}"
                "QFrame#DropArea QLabel {"
                "color: #e8e8ea;"
                "font-size: 14px;"
                "}"
            )
        else:
            self.setStyleSheet(
                "QFrame#DropArea {"
                "background-color: #171b22;"
                "border: 2px dashed #3a3f4a;"
                "border-radius: 14px;"
                "}"
                "QFrame#DropArea QLabel {"
                "color: #cfd3dc;"
                "font-size: 14px;"
                "}"
            )


class PdfDropLineEdit(QtWidgets.QLineEdit):
    pdf_dropped = Signal(str)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if any(u.isLocalFile() and u.toLocalFile().lower().endswith(".pdf") for u in urls):
                event.acceptProposedAction()
                return
        event.ignore()

    def dropEvent(self, event: QtGui.QDropEvent) -> None:
        urls = event.mimeData().urls()
        for u in urls:
            if not u.isLocalFile():
                continue
            p = u.toLocalFile()
            if p.lower().endswith(".pdf"):
                self.pdf_dropped.emit(p)
                event.acceptProposedAction()
                return
        event.ignore()


class DocumentTableWidget(QtWidgets.QTableWidget):
    files_dropped = Signal(list)
    empty_clicked = Signal()
    rows_reordered = Signal()

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(0, 3, parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QtWidgets.QAbstractItemView.DragDropMode.InternalMove)
        self.setDragDropOverwriteMode(False)
        self.setDropIndicatorShown(True)
        self.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(False)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setObjectName("DocumentTable")
        self.setHorizontalHeaderLabels(["Typ", "Datei", "Format"])
        self.horizontalHeader().setStretchLastSection(False)
        self.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.verticalHeader().setVisible(False)
        self.setMinimumHeight(260)
        self.setMouseTracking(True)
        self.setStyleSheet(
            "QTableWidget#DocumentTable {"
            "background-color: #171b22;"
            "border: 2px dashed #3a3f4a;"
            "border-radius: 14px;"
            "gridline-color: transparent;"
            "}"
            "QTableWidget#DocumentTable::item {"
            "padding: 8px;"
            "border-bottom: 1px solid #20252f;"
            "}"
            "QHeaderView::section {"
            "background-color: #0c0e12;"
            "color: #cfd3dc;"
            "padding: 8px;"
            "border: 0;"
            "font-weight: 600;"
            "}"
        )

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._set_hover(True)
            return
        super().dragEnterEvent(event)

    def dragLeaveEvent(self, event: QtGui.QDragLeaveEvent) -> None:
        self._set_hover(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QtGui.QDropEvent) -> None:
        self._set_hover(False)
        if event.mimeData().hasUrls():
            paths: List[str] = []
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    paths.append(url.toLocalFile())
            if paths:
                self.files_dropped.emit(paths)
                event.acceptProposedAction()
                return

        super().dropEvent(event)
        self.rows_reordered.emit()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if self.itemAt(event.pos()) is None and event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.empty_clicked.emit()
        super().mousePressEvent(event)

    def _set_hover(self, hover: bool) -> None:
        color = "#4da3ff" if hover else "#3a3f4a"
        self.setStyleSheet(
            "QTableWidget#DocumentTable {"
            "background-color: #171b22;"
            f"border: 2px dashed {color};"
            "border-radius: 14px;"
            "gridline-color: transparent;"
            "}"
            "QTableWidget#DocumentTable::item {"
            "padding: 8px;"
            "border-bottom: 1px solid #20252f;"
            "}"
            "QHeaderView::section {"
            "background-color: #0c0e12;"
            "color: #cfd3dc;"
            "padding: 8px;"
            "border: 0;"
            "font-weight: 600;"
            "}"
        )
