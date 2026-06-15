from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from zug2ferd.core.compliance import (
    ComplianceResult,
    InvoicePayload,
    choose_primary_tax_id,
    extract_invoice_payload,
    run_ustg_check_with_payload,
)
from zug2ferd.core.deps import DependencyManager
from zug2ferd.core.paths import AppPaths
from zug2ferd.core.pipeline import (
    BuildResult,
    attach_facturx_xml,
    convert_image_to_pdf_bytes,
    is_image_path,
    is_pdf_path,
    merge_pdfs_to_file,
    run_mustang_validate,
    stamp_pdf_underlay,
)
from zug2ferd.core.settings import SettingsStore
from zug2ferd.core.telemetry import Telemetry
from zug2ferd.qt import QtCore, QtGui, QtWidgets
from zug2ferd.ui.admin_window import AdminWindow
from zug2ferd.ui.widgets import ClickableLabel, DocumentTableWidget, PdfDropLineEdit, StatusLamp


Signal = QtCore.pyqtSignal if hasattr(QtCore, "pyqtSignal") else QtCore.Signal
ROLE_PATH = int(QtCore.Qt.ItemDataRole.UserRole)
ROLE_KIND = int(QtCore.Qt.ItemDataRole.UserRole) + 1


@dataclass
class DocumentEntry:
    path: Path
    kind: str
    payload: Optional[InvoicePayload] = None


class MainWindow(QtWidgets.QMainWindow):
    """Koordiniert GUI, Dokumenttabelle, Compliance-Checks und Export-Pipeline."""

    def __init__(self, paths: AppPaths, telemetry: Telemetry, deps: DependencyManager, settings: SettingsStore) -> None:
        super().__init__()
        self._paths = paths
        self._telemetry = telemetry
        self._deps = deps
        self._settings = settings

        self._admin_window: Optional[AdminWindow] = None
        self._documents: list[DocumentEntry] = []
        self._last_bypass_missing: Optional[tuple[str, ...]] = None
        self._last_input_dir: Optional[Path] = None
        self._last_output_dir: Optional[Path] = None
        self._populating_versions = False
        self._populating_settings = False

        self._footer_click_count = 0
        self._footer_reset_timer = QtCore.QTimer(self)
        self._footer_reset_timer.setSingleShot(True)
        self._footer_reset_timer.setInterval(900)
        self._footer_reset_timer.timeout.connect(self._reset_footer_clicks)

        self._compliance_bus = _ComplianceBus()
        self._compliance_bus.compliance_checked.connect(self._on_compliance_result)
        self._build_bus = _BuildBus()
        self._build_bus.done.connect(self._on_build_done_main)

        self.setWindowTitle("ZUG2-FeRD // Community Edition")
        self.resize(1180, 760)
        self._apply_window_icon()
        self._build_ui()

        self._telemetry.bus.line.connect(self._append_log)
        self._deps.versions_changed.connect(self._refresh_versions)
        self._deps.selection_changed.connect(self._log_selection)
        self._refresh_versions()
        self._load_settings_into_ui()
        self._refresh_document_table()

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)

        root_layout = QtWidgets.QVBoxLayout(central)
        root_layout.setContentsMargins(16, 16, 16, 12)
        root_layout.setSpacing(12)
        root_layout.addWidget(self._build_header(), 0)
        root_layout.addWidget(self._build_middle(), 1)
        root_layout.addWidget(self._build_checklist(), 0)

        self._log_view = QtWidgets.QTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setObjectName("LogViewer")
        self._log_view.setMinimumHeight(170)
        root_layout.addWidget(self._log_view, 0)
        root_layout.addWidget(self._build_footer(), 0)

    def _build_header(self) -> QtWidgets.QWidget:
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        title = QtWidgets.QLabel("ZUG2-FeRD // Community Edition")
        title.setObjectName("HeaderTitle")

        self._status = StatusLamp()

        self._gear_btn = QtWidgets.QToolButton()
        self._gear_btn.setObjectName("GearButton")
        self._gear_btn.setToolTip("Optionen")
        self._gear_btn.setIcon(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_FileDialogDetailedView))
        self._gear_btn.setIconSize(QtCore.QSize(18, 18))
        self._gear_btn.setFixedSize(36, 36)
        self._gear_btn.clicked.connect(self._toggle_options_panel)

        layout.addWidget(title, 1)
        layout.addWidget(self._status, 0)
        layout.addWidget(self._gear_btn, 0)
        return widget

    def _build_middle(self) -> QtWidgets.QWidget:
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        left = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)

        self._table_hint = QtWidgets.QLabel(
            "Belegpaket: Dateien hier ablegen oder in die leere Tabellenfläche klicken, um mehrere Dokumente auszuwählen."
        )
        self._table_hint.setWordWrap(True)
        left_layout.addWidget(self._table_hint, 0)

        self._document_table = DocumentTableWidget()
        self._document_table.files_dropped.connect(self._on_files_dropped)
        self._document_table.empty_clicked.connect(self._open_import_dialog)
        self._document_table.rows_reordered.connect(self._on_rows_reordered)
        left_layout.addWidget(self._document_table, 1)

        self._summary_label = QtWidgets.QLabel("Noch kein Belegpaket geladen.")
        self._summary_label.setWordWrap(True)
        left_layout.addWidget(self._summary_label, 0)

        actions = QtWidgets.QWidget()
        actions_layout = QtWidgets.QHBoxLayout(actions)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(10)

        self._btn_import = QtWidgets.QPushButton("Dokumente hinzufügen")
        self._btn_import.clicked.connect(self._open_import_dialog)
        self._btn_build = QtWidgets.QPushButton("Erzeugen & prüfen (Mustang)")
        self._btn_build.setEnabled(False)
        self._btn_build.clicked.connect(self._start_build_pipeline)
        self._btn_reset = QtWidgets.QPushButton("Reset")
        self._btn_reset.clicked.connect(self._reset_pipeline_state)

        actions_layout.addWidget(self._btn_import, 0)
        actions_layout.addWidget(self._btn_build, 1)
        actions_layout.addWidget(self._btn_reset, 0)
        left_layout.addWidget(actions, 0)

        self._options_panel = self._build_options_panel()
        self._options_panel.setVisible(False)

        layout.addWidget(left, 1)
        layout.addWidget(self._options_panel, 0)
        return widget

    def _build_options_panel(self) -> QtWidgets.QFrame:
        panel = QtWidgets.QFrame()
        panel.setObjectName("OptionsPanel")
        panel.setFixedWidth(380)

        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        title = QtWidgets.QLabel("Einstellungen")
        title.setObjectName("OptionsTitle")
        layout.addWidget(title, 0)

        self._chk_validate = QtWidgets.QCheckBox("Generierte Dateien vor Export per Mustang validieren")
        self._chk_validate.toggled.connect(self._on_validate_toggled)
        self._chk_ustg = QtWidgets.QCheckBox("Optischen XML-zu-PDF Pflichtangaben-Abgleich (§ 14 UStG) durchführen")
        self._chk_ustg.toggled.connect(self._on_ustg_toggled)
        self._chk_stationery_all = QtWidgets.QCheckBox("Briefbogen auf alle Seiten anwenden")
        self._chk_stationery_all.toggled.connect(self._on_stationery_all_toggled)

        layout.addWidget(self._chk_validate, 0)
        layout.addWidget(self._chk_ustg, 0)
        layout.addWidget(self._chk_stationery_all, 0)
        layout.addSpacing(8)

        layout.addWidget(QtWidgets.QLabel("Briefbogen (PDF)"), 0)
        self._stationery_line = PdfDropLineEdit()
        self._stationery_line.setPlaceholderText("Hier PDF droppen oder auswählen")
        self._stationery_line.pdf_dropped.connect(self._on_stationery_dropped)
        self._btn_stationery = QtWidgets.QPushButton("Briefbogen wählen")
        self._btn_stationery.clicked.connect(self._choose_stationery)
        layout.addWidget(self._stationery_line, 0)
        layout.addWidget(self._btn_stationery, 0)

        layout.addWidget(QtWidgets.QLabel("Import-Vorschlagspfad"), 0)
        self._import_dir_line = QtWidgets.QLineEdit()
        self._import_dir_line.setReadOnly(True)
        self._btn_import_dir = QtWidgets.QPushButton("Import-Pfad wählen")
        self._btn_import_dir.clicked.connect(self._choose_import_dir)
        layout.addWidget(self._import_dir_line, 0)
        layout.addWidget(self._btn_import_dir, 0)

        layout.addWidget(QtWidgets.QLabel("Export-Vorschlagspfad"), 0)
        self._export_dir_line = QtWidgets.QLineEdit()
        self._export_dir_line.setReadOnly(True)
        self._btn_export_dir = QtWidgets.QPushButton("Export-Pfad wählen")
        self._btn_export_dir.clicked.connect(self._choose_export_dir)
        layout.addWidget(self._export_dir_line, 0)
        layout.addWidget(self._btn_export_dir, 0)

        self._cmb_mustang = QtWidgets.QComboBox()
        self._cmb_mustang.currentIndexChanged.connect(self._on_mustang_changed)
        self._cmb_jre = QtWidgets.QComboBox()
        self._cmb_jre.currentIndexChanged.connect(self._on_jre_changed)

        layout.addWidget(QtWidgets.QLabel("Mustang-JAR Version"), 0)
        layout.addWidget(self._cmb_mustang, 0)
        layout.addWidget(QtWidgets.QLabel("Portable JRE Paket"), 0)
        layout.addWidget(self._cmb_jre, 0)

        self._btn_cleanup = QtWidgets.QPushButton("Pakete bereinigen")
        self._btn_cleanup.clicked.connect(self._cleanup_packages)
        layout.addWidget(self._btn_cleanup, 0)
        layout.addStretch(1)
        return panel

    def _build_checklist(self) -> QtWidgets.QFrame:
        frame = QtWidgets.QFrame()
        frame.setObjectName("ComplianceChecklist")
        frame.setStyleSheet(
            "QFrame#ComplianceChecklist {"
            "background-color: #0c0e12;"
            "border: 1px solid #2a2f3a;"
            "border-radius: 12px;"
            "padding: 10px;"
            "}"
            "QFrame#ComplianceChecklist QLabel { color: #cfd3dc; font-size: 12px; }"
        )

        layout = QtWidgets.QVBoxLayout(frame)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        self._chkline_name = QtWidgets.QLabel("Firmenname & Rechtsform -> -")
        self._chkline_addr = QtWidgets.QLabel("Anschrift / Adresse -> -")
        self._chkline_tax = QtWidgets.QLabel("USt-IdNr. / Steuernummer -> -")
        layout.addWidget(self._chkline_name, 0)
        layout.addWidget(self._chkline_addr, 0)
        layout.addWidget(self._chkline_tax, 0)
        return frame

    def _build_footer(self) -> QtWidgets.QWidget:
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        disclaimer = (
            "© 2026 ZUG2-FeRD Community Edition. Wichtiger Hinweis für Unternehmer: Dieses Tool wird von privat als "
            "kostenlose Community Edition bereitgestellt. Die Bereitstellung erfolgt unter explizitem Ausschluss "
            "jeglicher Sach- und Rechtsmängelhaftung (§ 521 BGB). Es besteht kein Anspruch auf Funktion, "
            "Fehlerfreiheit oder Support. Der Entwickler übernimmt keinerlei Haftung für steuerliche, finanzielle "
            "oder rechtliche Schäden (z. B. durch fehlerhafte XML-Strukturen oder GoBD-Verstöße). Die finale Prüfung "
            "der Dateien obliegt einzig und allein dem Nutzer."
        )

        self._footer_logo = QtWidgets.QLabel()
        self._footer_logo.setObjectName("FooterLogo")
        self._footer_logo.setFixedSize(64, 64)
        self._footer_logo.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        if self._paths.logo_path.exists():
            pixmap = QtGui.QPixmap(str(self._paths.logo_path))
            if not pixmap.isNull():
                self._footer_logo.setPixmap(
                    pixmap.scaled(
                        56,
                        56,
                        QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                        QtCore.Qt.TransformationMode.SmoothTransformation,
                    )
                )

        self._footer_label = ClickableLabel(disclaimer)
        self._footer_label.setObjectName("FooterDisclaimer")
        self._footer_label.setWordWrap(True)
        self._footer_label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        self._footer_label.clicked.connect(self._on_footer_clicked)

        layout.addWidget(self._footer_logo, 0, QtCore.Qt.AlignmentFlag.AlignBottom)
        layout.addWidget(self._footer_label, 1)
        return widget

    def _apply_window_icon(self) -> None:
        icon = QtGui.QIcon()
        if self._paths.logo_path.exists():
            for size in (16, 24, 32, 48, 64, 128, 256):
                icon.addFile(str(self._paths.logo_path), QtCore.QSize(size, size))
        if not icon.isNull():
            self.setWindowIcon(icon)

    def _append_log(self, line: str) -> None:
        self._log_view.append(line)
        doc = self._log_view.document()
        if doc.blockCount() > 1500:
            cursor = QtGui.QTextCursor(doc)
            cursor.movePosition(QtGui.QTextCursor.MoveOperation.Start)
            cursor.select(QtGui.QTextCursor.SelectionType.BlockUnderCursor)
            cursor.removeSelectedText()
            cursor.deleteChar()

    def _toggle_options_panel(self) -> None:
        self._options_panel.setVisible(not self._options_panel.isVisible())

    def _refresh_document_table(self) -> None:
        self._document_table.setRowCount(0)
        for row, doc in enumerate(self._documents):
            self._document_table.insertRow(row)

            kind_text = "E-Rechnung" if doc.kind == "invoice" else "RBU"
            format_text = doc.path.suffix.lower().lstrip(".").upper()

            kind_item = QtWidgets.QTableWidgetItem(kind_text)
            kind_item.setData(ROLE_PATH, str(doc.path))
            kind_item.setData(ROLE_KIND, doc.kind)
            file_item = QtWidgets.QTableWidgetItem(doc.path.name)
            file_item.setData(ROLE_PATH, str(doc.path))
            file_item.setData(ROLE_KIND, doc.kind)
            format_item = QtWidgets.QTableWidgetItem(format_text)
            format_item.setData(ROLE_PATH, str(doc.path))
            format_item.setData(ROLE_KIND, doc.kind)

            if doc.kind == "invoice":
                for item in (kind_item, file_item, format_item):
                    item.setForeground(QtGui.QBrush(QtGui.QColor("#80e0b0")))

            self._document_table.setItem(row, 0, kind_item)
            self._document_table.setItem(row, 1, file_item)
            self._document_table.setItem(row, 2, format_item)

        invoice = self._get_invoice_document()
        if invoice is None:
            self._summary_label.setText("Noch kein Belegpaket geladen.")
            self._btn_build.setEnabled(False)
            return

        attachments = [doc for doc in self._documents if doc.kind != "invoice"]
        self._summary_label.setText(
            f"Belegpaket mit 1 E-Rechnung und {len(attachments)} RBU-Dokument(en). Reihenfolge ist frei sortierbar."
        )
        self._btn_build.setEnabled(True)

    def _get_invoice_document(self) -> Optional[DocumentEntry]:
        for doc in self._documents:
            if doc.kind == "invoice":
                return doc
        return None

    def _set_checklist(self, result: ComplianceResult) -> None:
        if result.error:
            self._chkline_name.setText("Firmenname & Rechtsform -> FEHLT")
            self._chkline_addr.setText("Anschrift / Adresse -> FEHLT")
            self._chkline_tax.setText("USt-IdNr. / Steuernummer -> FEHLT")
            return

        self._chkline_name.setText(
            "Firmenname & Rechtsform -> " + ("GEFUNDEN" if result.found_name else "FEHLT")
        )
        self._chkline_addr.setText("Anschrift / Adresse -> " + ("GEFUNDEN" if result.found_address else "FEHLT"))
        self._chkline_tax.setText(
            "USt-IdNr. / Steuernummer -> " + ("GEFUNDEN" if result.found_tax_id else "FEHLT")
        )

    def _load_settings_into_ui(self) -> None:
        self._populating_settings = True
        try:
            cfg = self._settings.settings
            self._chk_validate.setChecked(bool(cfg.validate_with_mustang))
            self._chk_ustg.setChecked(bool(cfg.ustg_check_enabled))
            self._chk_stationery_all.setChecked(bool(cfg.stationery_apply_all_pages))

            stationery = self._settings.resolve_stationery_path()
            self._stationery_line.setText(stationery.name if stationery else "")

            import_dir = self._settings.resolve_import_dir()
            export_dir = self._settings.resolve_export_dir()
            self._import_dir_line.setText(str(import_dir) if import_dir else "")
            self._export_dir_line.setText(str(export_dir) if export_dir else "")
        finally:
            self._populating_settings = False

    def _refresh_versions(self) -> None:
        self._populating_versions = True
        try:
            jars = self._deps.list_mustang_jars()
            jres = self._deps.list_jre_dirs()

            current_jar = self._deps.selection.mustang_jar.name if self._deps.selection.mustang_jar else None
            current_jre = self._deps.selection.jre_dir.name if self._deps.selection.jre_dir else None

            self._cmb_mustang.clear()
            if jars:
                for item in jars:
                    self._cmb_mustang.addItem(item.name)
                if current_jar and current_jar in [p.name for p in jars]:
                    self._cmb_mustang.setCurrentText(current_jar)
                else:
                    self._cmb_mustang.setCurrentIndex(len(jars) - 1)
                    self._deps.set_selected_mustang_by_name(self._cmb_mustang.currentText())
            else:
                self._cmb_mustang.addItem("(keine JAR)")

            self._cmb_jre.clear()
            if jres:
                for item in jres:
                    self._cmb_jre.addItem(item.name)
                if current_jre and current_jre in [p.name for p in jres]:
                    self._cmb_jre.setCurrentText(current_jre)
                else:
                    self._cmb_jre.setCurrentIndex(len(jres) - 1)
                    self._deps.set_selected_jre_by_name(self._cmb_jre.currentText())
            else:
                self._cmb_jre.addItem("(keine JRE)")
        finally:
            self._populating_versions = False

    def _log_selection(self) -> None:
        jar = self._deps.selection.mustang_jar.name if self._deps.selection.mustang_jar else "(keine)"
        jre = self._deps.selection.jre_dir.name if self._deps.selection.jre_dir else "(keine)"
        self._telemetry.logger.info(f"Aktive Auswahl: Mustang={jar} | JRE={jre}")

    def _cleanup_packages(self) -> None:
        self._telemetry.logger.info("Pakete bereinigen: Start.")
        self._deps.cleanup_unused(self._deps.selection.mustang_jar, self._deps.selection.jre_dir)

    def _on_validate_toggled(self, checked: bool) -> None:
        if not self._populating_settings:
            self._settings.set_validate_with_mustang(bool(checked))

    def _on_stationery_all_toggled(self, checked: bool) -> None:
        if not self._populating_settings:
            self._settings.set_stationery_apply_all_pages(bool(checked))

    def _on_ustg_toggled(self, checked: bool) -> None:
        if self._populating_settings:
            return
        if checked:
            self._settings.set_ustg_check_enabled(True)
            self._telemetry.logger.info("Compliance-Check §14 UStG aktiviert.")
            return

        dialog = QtWidgets.QMessageBox(self)
        dialog.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        dialog.setWindowTitle("Risiko-Akzeptanz")
        dialog.setText(
            "Achtung: Das Deaktivieren dieser Schutzfunktion entbindet das Programm von der inhaltlichen Pflichtprüfung "
            "nach § 14 UStG. Sie übernehmen die alleinige Verantwortung für die rechtliche Korrektheit der "
            "verarbeiteten Dokumente. Der Programmhersteller übernimmt keinerlei Gewährleistung."
        )
        accept = dialog.addButton("Ich akzeptiere das Risiko", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        dialog.addButton("Abbrechen", QtWidgets.QMessageBox.ButtonRole.RejectRole)
        dialog.exec()

        if dialog.clickedButton() is accept:
            self._settings.set_ustg_check_enabled(False)
            self._telemetry.logger.info("Compliance-Check §14 UStG deaktiviert (Risiko akzeptiert).")
            return

        self._populating_settings = True
        try:
            self._chk_ustg.setChecked(True)
        finally:
            self._populating_settings = False

    def _choose_import_dir(self) -> None:
        start = str(self._suggest_import_dir())
        directory = QtWidgets.QFileDialog.getExistingDirectory(self, "Import-Pfad wählen", start)
        if not directory:
            return
        self._settings.set_import_dir_from_absolute(Path(directory))
        self._import_dir_line.setText(directory)

    def _choose_export_dir(self) -> None:
        start = str(self._suggest_export_dir())
        directory = QtWidgets.QFileDialog.getExistingDirectory(self, "Export-Pfad wählen", start)
        if not directory:
            return
        self._settings.set_export_dir_from_absolute(Path(directory))
        self._export_dir_line.setText(directory)

    def _on_mustang_changed(self) -> None:
        if self._populating_versions:
            return
        name = self._cmb_mustang.currentText().strip()
        if name and not name.startswith("("):
            self._deps.set_selected_mustang_by_name(name)

    def _on_jre_changed(self) -> None:
        if self._populating_versions:
            return
        name = self._cmb_jre.currentText().strip()
        if name and not name.startswith("("):
            self._deps.set_selected_jre_by_name(name)

    def _open_import_dialog(self) -> None:
        start = str(self._suggest_import_dir())
        file_paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "Dokumente auswählen",
            start,
            "Unterstützte Dateien (*.pdf *.png *.jpg *.jpeg *.doc *.docx *.xls *.xlsx);;Alle Dateien (*)",
        )
        if file_paths:
            self._on_files_dropped(file_paths)

    def _on_files_dropped(self, paths: Iterable[str]) -> None:
        files = [Path(p) for p in paths]
        for path in files:
            self._telemetry.logger.info(f"Datei erhalten: {path}")
            if path.parent.exists():
                self._last_input_dir = path.parent

        office = [p for p in files if p.suffix.lower() in {".doc", ".docx", ".xls", ".xlsx"}]
        if office:
            QtWidgets.QMessageBox.warning(
                self,
                "Format-Hinweis",
                "Bitte vorab als PDF/Bild speichern, um Formatierungsfehler zu vermeiden.",
            )
            for path in office:
                self._telemetry.logger.info(
                    "Bitte vorab als PDF/Bild speichern, um Formatierungsfehler zu vermeiden: " + path.name
                )

        valid = [p for p in files if p.suffix.lower() in {".pdf", ".png", ".jpg", ".jpeg"}]
        invalid = [p for p in files if p not in office and p not in valid]
        for path in invalid:
            self._telemetry.logger.info("Datei ignoriert (Format nicht unterstützt): " + path.name)

        second_invoice_hits: list[str] = []
        for path in valid:
            if not path.exists():
                continue
            if path.suffix.lower() != ".pdf":
                self._documents.append(DocumentEntry(path=path, kind="rbu"))
                self._telemetry.logger.info(f"Anlage hinzugefügt (Bild): {path.name}")
                continue

            payload = self._try_extract_invoice_payload(path)
            if payload is not None:
                if self._get_invoice_document() is not None:
                    second_invoice_hits.append(path.name)
                    self._telemetry.logger.info(
                        "Zweite E-Rechnung abgewiesen, da bereits eine E-Rechnung im Belegpaket enthalten ist: "
                        + path.name
                    )
                    continue
                if not self._accept_invoice_document(path, payload):
                    continue
                self._documents.insert(0, DocumentEntry(path=path, kind="invoice", payload=payload))
                self._telemetry.logger.info(f"Hauptrechnung importiert: {path.name}")
                self._start_compliance_recheck()
                continue

            self._documents.append(DocumentEntry(path=path, kind="rbu"))
            self._telemetry.logger.info(f"Anlage hinzugefügt (PDF): {path.name}")

        if second_invoice_hits:
            QtWidgets.QMessageBox.warning(
                self,
                "Weitere E-Rechnung abgewiesen",
                "Es ist bereits eine gültige E-Rechnung Bestandteil des aktuellen Belegpakets. "
                "Eine Zusammenführung ist nur mit genau einer einzelnen E-Rechnung zulässig.",
            )

        self._refresh_document_table()

    def _try_extract_invoice_payload(self, pdf_path: Path) -> Optional[InvoicePayload]:
        try:
            return extract_invoice_payload(pdf_path)
        except Exception:
            return None

    def _accept_invoice_document(self, pdf_path: Path, payload: InvoicePayload) -> bool:
        seller_name = payload.seller.name
        primary_tax = choose_primary_tax_id(payload.seller.tax_ids)

        if not self._settings.has_branding() and seller_name and primary_tax:
            self._settings.set_branding(seller_name, primary_tax)
            self._telemetry.logger.info(f"User-Branding erfolgreich für {seller_name} hinterlegt.")

        if self._settings.has_branding() and not self._settings.matches_branding(seller_name, payload.seller.tax_ids):
            self._status.set_state("error")
            self._telemetry.logger.info("MANDANTEN_SPERRE | file=" + pdf_path.name + " | reason=branding_mismatch")
            QtWidgets.QMessageBox.critical(
                self,
                "Warnung",
                "Warnung: Abweichender Rechnungsaussteller erkannt! Dieses Tool ist fest auf Ihr Unternehmensprofil "
                "geprägt. Verarbeitung abgebrochen.",
            )
            return False

        return True

    def _on_rows_reordered(self) -> None:
        if self._document_table.rowCount() == 0:
            return

        by_path = {str(doc.path): doc for doc in self._documents}
        reordered: list[DocumentEntry] = []
        for row in range(self._document_table.rowCount()):
            item = self._document_table.item(row, 1)
            if item is None:
                continue
            path_str = item.data(ROLE_PATH)
            if path_str in by_path:
                reordered.append(by_path[path_str])
        if len(reordered) == len(self._documents):
            self._documents = reordered
            self._telemetry.logger.info("Belegreihenfolge manuell angepasst.")
            self._refresh_document_table()

    def _start_compliance_recheck(self) -> None:
        invoice = self._get_invoice_document()
        if invoice is None or invoice.payload is None:
            self._status.set_state("neutral")
            return
        self._last_bypass_missing = None
        stationery = self._settings.resolve_stationery_path()
        enabled = bool(self._settings.settings.ustg_check_enabled)
        QtCore.QThreadPool.globalInstance().start(
            _ComplianceRunnable(self._compliance_bus, invoice.path, invoice.payload, stationery, enabled)
        )

    @QtCore.pyqtSlot(object, object) if hasattr(QtCore, "pyqtSlot") else QtCore.Slot(object, object)
    def _on_compliance_result(self, result: object, invoice_pdf: object) -> None:
        if not isinstance(result, ComplianceResult) or not isinstance(invoice_pdf, Path):
            return
        self._set_checklist(result)
        if result.error:
            self._status.set_state("error")
            self._telemetry.logger.info(f"Compliance-Fehler: {result.error}")
            return
        if result.ok:
            if self._last_bypass_missing is not None:
                self._status.set_state("warn")
            else:
                self._status.set_state("warn" if result.used_stationery else "ok")
            return
        if not self._settings.settings.ustg_check_enabled:
            self._status.set_state("warn")
            return
        critical = [item for item in result.missing_fields if item in {"Firmenname", "USt-IdNr./Steuernummer"}]
        if not critical:
            self._status.set_state("warn")
            return
        self._open_softblock(invoice_pdf, result.missing_fields)

    def _open_softblock(self, invoice_pdf: Path, missing_fields: tuple[str, ...]) -> None:
        dialog = QtWidgets.QMessageBox(self)
        dialog.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        dialog.setWindowTitle("Compliance-Hinweis nach § 14 UStG")
        dialog.setText(
            "Compliance-Hinweis nach § 14 UStG: Wichtige gesetzliche Stammdaten des Ausstellers (z. B. USt-IdNr. "
            "oder korrekte Firmierung) wurden im sichtbaren Text des PDF-Layouts nicht zweifelsfrei erkannt. "
            "Um den Vorsteuerabzug des Empfängers nicht zu gefährden, hinterlegen Sie bitte ein digitales "
            "Briefpapier (PDF) als Hintergrund-Ebene."
        )
        button_stationery = dialog.addButton(
            "Briefbogen (PDF) jetzt hinterlegen", QtWidgets.QMessageBox.ButtonRole.AcceptRole
        )
        dialog.addButton("Ignorieren und fortfahren (Bypass)", QtWidgets.QMessageBox.ButtonRole.DestructiveRole)
        dialog.exec()
        if dialog.clickedButton() is button_stationery:
            self._choose_stationery()
            return
        self._last_bypass_missing = missing_fields
        self._telemetry.logger.info(
            "COMPLIANCE_BYPASS | file=" + invoice_pdf.name + " | missing=" + ",".join(missing_fields)
        )
        self._status.set_state("warn")

    def _on_stationery_dropped(self, path: str) -> None:
        self._set_stationery_from_path(Path(path))

    def _choose_stationery(self) -> None:
        start = str(self._suggest_import_dir())
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Briefbogen auswählen", start, "PDF (*.pdf)")
        if file_path:
            self._set_stationery_from_path(Path(file_path))

    def _set_stationery_from_path(self, src: Path) -> None:
        if not src.exists() or src.suffix.lower() != ".pdf":
            return
        dest = self._paths.stationery_dir / src.name
        if dest.exists() and dest.resolve() != src.resolve():
            stem = src.stem
            suffix = src.suffix
            index = 2
            while True:
                candidate = self._paths.stationery_dir / f"{stem}_{index}{suffix}"
                if not candidate.exists():
                    dest = candidate
                    break
                index += 1
        try:
            if dest.resolve() != src.resolve():
                shutil.copy2(src, dest)
        except Exception as exc:
            self._telemetry.logger.info("Briefbogen konnte nicht gespeichert werden: " + str(exc))
            return
        rel = dest.relative_to(self._paths.root_dir).as_posix()
        self._settings.set_stationery_rel(rel)
        self._stationery_line.setText(dest.name)
        self._telemetry.logger.info(f"Briefbogen gesetzt: {dest.name}")
        self._start_compliance_recheck()

    def _suggest_import_dir(self) -> Path:
        return (
            self._settings.resolve_import_dir()
            or self._last_input_dir
            or self._settings.resolve_export_dir()
            or Path.cwd()
        )

    def _suggest_export_dir(self) -> Path:
        invoice = self._get_invoice_document()
        return (
            self._settings.resolve_export_dir()
            or self._last_output_dir
            or (invoice.path.parent if invoice is not None else None)
            or self._last_input_dir
            or self._settings.resolve_import_dir()
            or Path.cwd()
        )

    def _build_export_filename(self) -> str:
        invoice = self._get_invoice_document()
        first_doc = self._documents[0] if self._documents else None
        if invoice is None:
            return "belegpaket_inkl_RBU.pdf"

        default_invoice_name = invoice.path.stem + "_inkl_RBU.pdf"
        if first_doc is None or first_doc.path == invoice.path:
            return default_invoice_name

        dialog = QtWidgets.QMessageBox(self)
        dialog.setIcon(QtWidgets.QMessageBox.Icon.Question)
        dialog.setWindowTitle("Dateiname wählen")
        dialog.setText(
            "Die E-Rechnung steht nicht mehr an erster Stelle der Tabelle. Soll der Export trotzdem den Dateinamen "
            "der Original-E-Rechnung erhalten oder den Dateinamen der ersten Tabellenzeile verwenden?"
        )
        btn_invoice = dialog.addButton("Original-E-Rechnung verwenden", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        btn_first = dialog.addButton("Erste Tabellenzeile verwenden", QtWidgets.QMessageBox.ButtonRole.ActionRole)
        dialog.exec()

        if dialog.clickedButton() is btn_first:
            return first_doc.path.stem + "_inkl_RBU.pdf"
        return default_invoice_name

    def _start_build_pipeline(self) -> None:
        invoice = self._get_invoice_document()
        if invoice is None or invoice.payload is None:
            QtWidgets.QMessageBox.warning(
                self,
                "Keine E-Rechnung",
                "Es ist noch keine gültige ZUGFeRD-E-Rechnung im aktuellen Belegpaket vorhanden.",
            )
            return

        if not self._settings.settings.validate_with_mustang:
            QtWidgets.QMessageBox.warning(
                self,
                "Hinweis",
                "Die Mustang-Endkontrolle ist deaktiviert. Aktivieren Sie diese Option für den Export.",
            )
            return

        java_exe = self._deps.selection.java_exe
        mustang_jar = self._deps.selection.mustang_jar
        if java_exe is None or mustang_jar is None:
            self._status.set_state("error")
            self._telemetry.logger.info("Mustang-Validierung nicht möglich: JRE oder Mustang-JAR fehlt.")
            return

        ordered_paths = [doc.path for doc in self._documents]
        self._telemetry.logger.info(
            f"Build-Pipeline gestartet (Stamping/Merge/Attachment/Validation) für {len(ordered_paths)} Dokument(e)."
        )
        self._btn_build.setEnabled(False)

        tmp_name = f"zug2ferd_build_{os.getpid()}_{uuid.uuid4().hex}.pdf"
        tmp_output = self._paths.tmp_dir / tmp_name
        stationery = self._settings.resolve_stationery_path()
        apply_all = bool(self._settings.settings.stationery_apply_all_pages)

        QtCore.QThreadPool.globalInstance().start(
            _BuildRunnable(
                self._build_bus,
                ordered_paths,
                invoice.payload.xml_bytes,
                stationery,
                apply_all,
                java_exe,
                mustang_jar,
                tmp_output,
            )
        )

    @QtCore.pyqtSlot(object) if hasattr(QtCore, "pyqtSlot") else QtCore.Slot(object)
    def _on_build_done_main(self, result: object) -> None:
        self._btn_build.setEnabled(True)
        if not isinstance(result, BuildResult):
            return
        if not result.ok or result.output_pdf_path is None:
            self._status.set_state("error")
            self._telemetry.logger.info("Build fehlgeschlagen: " + (result.error or "unbekannt"))
            return
        if not result.mustang_ok:
            self._status.set_state("error")
            self._telemetry.logger.info("Mustang-Validierung fehlgeschlagen. Export blockiert.")
            if result.mustang_stdout.strip():
                self._telemetry.logger.info(result.mustang_stdout.strip())
            if result.mustang_stderr.strip():
                self._telemetry.logger.info(result.mustang_stderr.strip())
            return

        export_dir = self._suggest_export_dir()
        export_name = self._build_export_filename()
        save_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Speichern unter...",
            str(export_dir / export_name),
            "PDF (*.pdf)",
        )
        if not save_path:
            self._telemetry.logger.info("Export abgebrochen.")
            return

        try:
            target = Path(save_path)
            target.write_bytes(result.output_pdf_path.read_bytes())
            self._last_output_dir = target.parent
        except Exception as exc:
            self._status.set_state("error")
            self._telemetry.logger.info("Konnte Export nicht schreiben: " + str(exc))
            return

        self._status.set_state("ok")
        self._telemetry.logger.info(
            f"MUSTANG_ERFOLG | Die erzeugte Datei {Path(save_path).name} wurde als ZUGFeRD-konform erfolgreich validiert."
        )
        if result.mustang_stdout.strip():
            self._telemetry.logger.info(result.mustang_stdout.strip())
        self._telemetry.logger.info("Export gespeichert: " + Path(save_path).name)

    def _reset_pipeline_state(self) -> None:
        self._documents = []
        self._last_bypass_missing = None
        self._btn_build.setEnabled(False)
        self._chkline_name.setText("Firmenname & Rechtsform -> -")
        self._chkline_addr.setText("Anschrift / Adresse -> -")
        self._chkline_tax.setText("USt-IdNr. / Steuernummer -> -")
        self._status.set_state("neutral")
        self._refresh_document_table()

    def _reset_footer_clicks(self) -> None:
        self._footer_click_count = 0

    def _on_footer_clicked(self) -> None:
        self._footer_click_count += 1
        self._footer_reset_timer.start()
        if self._footer_click_count < 5:
            return
        self._reset_footer_clicks()
        self._open_admin_prompt()

    def _open_admin_prompt(self) -> None:
        echo = (
            QtWidgets.QLineEdit.EchoMode.Password
            if hasattr(QtWidgets.QLineEdit, "EchoMode")
            else QtWidgets.QLineEdit.Password
        )
        text, ok = QtWidgets.QInputDialog.getText(self, "", "", echo=echo)
        if not ok or text != "0000":
            return
        if self._admin_window is None:
            self._admin_window = AdminWindow(self._telemetry, self)
        self._admin_window.show()
        self._admin_window.raise_()
        self._admin_window.activateWindow()


class _ComplianceBus(QtCore.QObject):
    compliance_checked = Signal(object, object)


class _ComplianceRunnable(QtCore.QRunnable):
    def __init__(
        self,
        bus: _ComplianceBus,
        invoice_pdf: Path,
        payload: InvoicePayload,
        stationery_pdf: Optional[Path],
        ustg_check_enabled: bool,
    ) -> None:
        super().__init__()
        self._bus = bus
        self._invoice = invoice_pdf
        self._payload = payload
        self._stationery = stationery_pdf
        self._enabled = ustg_check_enabled

    def run(self) -> None:
        result = run_ustg_check_with_payload(self._payload, self._invoice, self._stationery, self._enabled)
        self._bus.compliance_checked.emit(result, self._invoice)


class _BuildBus(QtCore.QObject):
    done = Signal(object)


class _BuildRunnable(QtCore.QRunnable):
    def __init__(
        self,
        bus: _BuildBus,
        ordered_documents: list[Path],
        xml_bytes: bytes,
        stationery_pdf: Optional[Path],
        apply_all_pages: bool,
        java_exe: Path,
        mustang_jar: Path,
        tmp_output: Path,
    ) -> None:
        super().__init__()
        self._bus = bus
        self._ordered_documents = ordered_documents
        self._xml_bytes = xml_bytes
        self._stationery = stationery_pdf
        self._apply_all_pages = apply_all_pages
        self._java_exe = java_exe
        self._mustang_jar = mustang_jar
        self._tmp_output = tmp_output

    def run(self) -> None:
        temp_files: list[Path] = []
        try:
            pdf_chunks: list[bytes] = []
            for source in self._ordered_documents:
                if is_pdf_path(source):
                    pdf_chunks.append(stamp_pdf_underlay(source, self._stationery, self._apply_all_pages))
                    continue
                if is_image_path(source):
                    image_pdf = convert_image_to_pdf_bytes(source)
                    if self._stationery is not None and self._stationery.exists():
                        temp_pdf = self._tmp_output.parent / f"img_{uuid.uuid4().hex}.pdf"
                        temp_pdf.write_bytes(image_pdf)
                        temp_files.append(temp_pdf)
                        pdf_chunks.append(stamp_pdf_underlay(temp_pdf, self._stationery, self._apply_all_pages))
                    else:
                        pdf_chunks.append(image_pdf)

            merge_pdfs_to_file(self._tmp_output, pdf_chunks)
            attach_facturx_xml(self._tmp_output, self._xml_bytes)
            mustang_ok, stdout, stderr = run_mustang_validate(self._java_exe, self._mustang_jar, self._tmp_output)
            result = BuildResult(
                ok=True,
                output_pdf_path=self._tmp_output,
                mustang_ok=mustang_ok,
                mustang_stdout=stdout,
                mustang_stderr=stderr,
                error=None,
            )
        except Exception as exc:
            result = BuildResult(
                ok=False,
                output_pdf_path=None,
                mustang_ok=False,
                mustang_stdout="",
                mustang_stderr="",
                error=str(exc),
            )
        finally:
            for file_path in temp_files:
                file_path.unlink(missing_ok=True)
        self._bus.done.emit(result)
