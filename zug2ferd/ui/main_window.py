from __future__ import annotations

import shutil
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional
import uuid

from zug2ferd.core.compliance import (
    ComplianceResult,
    InvoicePayload,
    choose_primary_tax_id,
    extract_invoice_payload,
    run_ustg_check_with_payload,
)
from zug2ferd.core.deps import DependencyManager
from zug2ferd.core.paths import AppPaths
from zug2ferd.core.settings import SettingsStore
from zug2ferd.core.telemetry import Telemetry
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
from zug2ferd.qt import QtCore, QtGui, QtWidgets
from zug2ferd.ui.admin_window import AdminWindow
from zug2ferd.ui.widgets import ClickableLabel, DropArea, PdfDropLineEdit, StatusLamp


Signal = QtCore.pyqtSignal if hasattr(QtCore, "pyqtSignal") else QtCore.Signal


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, paths: AppPaths, telemetry: Telemetry, deps: DependencyManager, settings: SettingsStore) -> None:
        super().__init__()
        self._paths = paths
        self._telemetry = telemetry
        self._deps = deps
        self._settings = settings
        self._admin_window: Optional[AdminWindow] = None
        self._populating_versions = False
        self._populating_settings = False
        self._current_invoice_pdf: Optional[Path] = None
        self._invoice_payload: Optional[InvoicePayload] = None
        self._attachments: list[Path] = []
        self._last_bypass_missing: Optional[tuple[str, ...]] = None

        self._footer_click_count = 0
        self._footer_reset_timer = QtCore.QTimer(self)
        self._footer_reset_timer.setSingleShot(True)
        self._footer_reset_timer.setInterval(900)
        self._footer_reset_timer.timeout.connect(self._reset_footer_clicks)

        self.setWindowTitle("ZUG2-FeRD // Community Edition")
        self.resize(1180, 760)

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)

        root_layout = QtWidgets.QVBoxLayout(central)
        root_layout.setContentsMargins(16, 16, 16, 12)
        root_layout.setSpacing(12)

        header = self._build_header()
        root_layout.addWidget(header, 0)

        middle = self._build_middle()
        root_layout.addWidget(middle, 1)

        self._checklist = self._build_checklist()
        root_layout.addWidget(self._checklist, 0)

        self._log_view = QtWidgets.QTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setObjectName("LogViewer")
        self._log_view.setMinimumHeight(170)
        root_layout.addWidget(self._log_view, 0)

        footer = self._build_footer()
        root_layout.addWidget(footer, 0)

        self._telemetry.bus.line.connect(self._append_log)
        self._deps.versions_changed.connect(self._refresh_versions)
        self._deps.selection_changed.connect(self._log_selection)
        self._compliance_bus = _ComplianceBus()
        self._compliance_bus.result.connect(self._on_compliance_result_main)
        self._refresh_versions()
        self._load_settings_into_ui()

    def _build_header(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        title = QtWidgets.QLabel("ZUG2-FeRD // Community Edition")
        title.setObjectName("HeaderTitle")

        self._gear_btn = QtWidgets.QToolButton()
        self._gear_btn.setObjectName("GearButton")
        self._gear_btn.setToolTip("Optionen")
        self._gear_btn.setIcon(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_FileDialogDetailedView))
        self._gear_btn.setIconSize(QtCore.QSize(18, 18))
        self._gear_btn.setFixedSize(36, 36)

        self._status = StatusLamp()

        layout.addWidget(title, 1)
        layout.addWidget(self._status, 0)
        layout.addWidget(self._gear_btn, 0)

        return w

    def _build_middle(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        left = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)

        self._drop_area = DropArea()
        self._drop_area.files_dropped.connect(self._on_files_dropped)
        left_layout.addWidget(self._drop_area, 1)

        actions = QtWidgets.QWidget()
        actions_layout = QtWidgets.QHBoxLayout(actions)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(10)

        self._btn_build = QtWidgets.QPushButton("Erzeugen & prüfen (Mustang)")
        self._btn_build.clicked.connect(self._start_build_pipeline)
        self._btn_build.setEnabled(False)

        self._btn_reset = QtWidgets.QPushButton("Reset")
        self._btn_reset.clicked.connect(self._reset_pipeline_state)

        actions_layout.addWidget(self._btn_build, 1)
        actions_layout.addWidget(self._btn_reset, 0)
        left_layout.addWidget(actions, 0)

        self._options_panel = self._build_options_panel()
        self._options_panel.setVisible(False)

        layout.addWidget(left, 1)
        layout.addWidget(self._options_panel, 0)

        self._gear_btn.clicked.connect(self._toggle_options_panel)
        return w

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

        layout.addWidget(self._chk_validate, 0)
        layout.addWidget(self._chk_ustg, 0)

        layout.addSpacing(8)

        stationery_label = QtWidgets.QLabel("Briefbogen (PDF)")
        self._stationery_line = PdfDropLineEdit()
        self._stationery_line.setPlaceholderText("Hier PDF droppen oder auswählen")
        self._stationery_line.pdf_dropped.connect(self._on_stationery_dropped)
        self._btn_stationery = QtWidgets.QPushButton("Briefbogen wählen")
        self._btn_stationery.clicked.connect(self._choose_stationery)

        layout.addWidget(stationery_label, 0)
        layout.addWidget(self._stationery_line, 0)
        layout.addWidget(self._btn_stationery, 0)

        self._chk_stationery_all = QtWidgets.QCheckBox("Briefbogen auf alle Seiten anwenden")
        self._chk_stationery_all.toggled.connect(self._on_stationery_all_toggled)
        layout.addWidget(self._chk_stationery_all, 0)

        layout.addSpacing(10)

        mustang_label = QtWidgets.QLabel("Mustang-JAR Version")
        self._cmb_mustang = QtWidgets.QComboBox()
        self._cmb_mustang.currentIndexChanged.connect(self._on_mustang_changed)

        jre_label = QtWidgets.QLabel("Portable JRE Paket")
        self._cmb_jre = QtWidgets.QComboBox()
        self._cmb_jre.currentIndexChanged.connect(self._on_jre_changed)

        layout.addWidget(mustang_label, 0)
        layout.addWidget(self._cmb_mustang, 0)
        layout.addWidget(jre_label, 0)
        layout.addWidget(self._cmb_jre, 0)

        layout.addSpacing(6)

        self._btn_cleanup = QtWidgets.QPushButton("Pakete bereinigen")
        self._btn_cleanup.clicked.connect(self._cleanup_packages)
        layout.addWidget(self._btn_cleanup, 0)

        layout.addStretch(1)
        return panel

    def _build_footer(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        disclaimer = (
            "© 2026 ZUG2-FeRD Community Edition. Wichtiger Hinweis für Unternehmer: Dieses Tool wird von privat als "
            "kostenlose Community Edition bereitgestellt. Die Bereitstellung erfolgt unter explizitem Ausschluss "
            "jeglicher Sach- und Rechtsmängelhaftung (§ 521 BGB). Es besteht kein Anspruch auf Funktion, "
            "Fehlerfreiheit oder Support. Der Entwickler übernimmt keinerlei Haftung für steuerliche, finanzielle "
            "oder rechtliche Schäden (z. B. durch fehlerhafte XML-Strukturen oder GoBD-Verstöße). Die finale Prüfung "
            "der Dateien obliegt einzig und allein dem Nutzer."
        )

        self._footer_label = ClickableLabel(disclaimer)
        self._footer_label.setObjectName("FooterDisclaimer")
        self._footer_label.setWordWrap(True)
        self._footer_label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        self._footer_label.clicked.connect(self._on_footer_clicked)

        layout.addWidget(self._footer_label, 1)
        return w

    def _toggle_options_panel(self) -> None:
        self._options_panel.setVisible(not self._options_panel.isVisible())

    def _cleanup_packages(self) -> None:
        keep_jar = self._deps.selection.mustang_jar
        keep_jre = self._deps.selection.jre_dir
        self._telemetry.logger.info("Pakete bereinigen: Start.")
        self._deps.cleanup_unused(keep_jar=keep_jar, keep_jre=keep_jre)

    def _append_log(self, line: str) -> None:
        self._log_view.append(line)
        doc = self._log_view.document()
        if doc.blockCount() > 1500:
            cursor = QtGui.QTextCursor(doc)
            cursor.movePosition(QtGui.QTextCursor.MoveOperation.Start)
            cursor.select(QtGui.QTextCursor.SelectionType.BlockUnderCursor)
            cursor.removeSelectedText()
            cursor.deleteChar()

    def _on_files_dropped(self, paths: Iterable[str]) -> None:
        files = [Path(p) for p in paths]
        for p in files:
            self._telemetry.logger.info(f"Datei erhalten: {p}")

        unsupported: list[Path] = []
        office: list[Path] = []

        for p in files:
            ext = p.suffix.lower()
            if ext in {".doc", ".docx", ".xls", ".xlsx"}:
                office.append(p)
                continue
            if ext in {".pdf", ".png", ".jpg", ".jpeg", ".xml"}:
                continue
            unsupported.append(p)

        for p in office:
            self._telemetry.logger.info(
                "Bitte vorab als PDF/Bild speichern, um Formatierungsfehler zu vermeiden: " + p.name
            )

        for p in unsupported:
            self._telemetry.logger.info("Datei ignoriert (Format nicht unterstützt): " + p.name)

        pdfs = [p for p in files if p.suffix.lower() == ".pdf"]
        images = [p for p in files if p.suffix.lower() in {".png", ".jpg", ".jpeg"}]

        if self._invoice_payload is None and pdfs:
            invoice = pdfs[0]
            self._start_invoice_import(invoice)
            pdfs = pdfs[1:]

        for p in pdfs:
            if self._invoice_payload is None:
                self._telemetry.logger.info("Bitte zuerst eine ZUGFeRD-Hauptrechnung (PDF) importieren.")
                break
            self._attachments.append(p)
            self._telemetry.logger.info("Anlage hinzugefügt (PDF): " + p.name)

        for p in images:
            if self._invoice_payload is None:
                self._telemetry.logger.info("Bitte zuerst eine ZUGFeRD-Hauptrechnung (PDF) importieren.")
                break
            self._attachments.append(p)
            self._telemetry.logger.info("Anlage hinzugefügt (Bild): " + p.name)

        self._btn_build.setEnabled(self._invoice_payload is not None)

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
        echo = QtWidgets.QLineEdit.EchoMode.Password if hasattr(QtWidgets.QLineEdit, "EchoMode") else QtWidgets.QLineEdit.Password
        text, ok = QtWidgets.QInputDialog.getText(self, "", "", echo=echo)
        if not ok:
            return
        if text != "0000":
            return

        if self._admin_window is None:
            self._admin_window = AdminWindow(self._telemetry, self)
        self._admin_window.show()
        self._admin_window.raise_()
        self._admin_window.activateWindow()

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

        self._chkline_name = QtWidgets.QLabel("🏢 Firmenname & Rechtsform -> —")
        self._chkline_addr = QtWidgets.QLabel("📍 Anschrift / Adresse -> —")
        self._chkline_tax = QtWidgets.QLabel("🆔 USt-IdNr. / Steuernummer -> —")

        layout.addWidget(self._chkline_name, 0)
        layout.addWidget(self._chkline_addr, 0)
        layout.addWidget(self._chkline_tax, 0)
        return frame

    def _set_checklist(self, result: ComplianceResult) -> None:
        if result.error:
            self._chkline_name.setText("🏢 Firmenname & Rechtsform -> ✗")
            self._chkline_addr.setText("📍 Anschrift / Adresse -> ✗")
            self._chkline_tax.setText("🆔 USt-IdNr. / Steuernummer -> ✗")
            return

        self._chkline_name.setText("🏢 Firmenname & Rechtsform -> " + ("✓" if result.found_name else "✗"))
        self._chkline_addr.setText("📍 Anschrift / Adresse -> " + ("✓" if result.found_address else "✗"))
        self._chkline_tax.setText("🆔 USt-IdNr. / Steuernummer -> " + ("✓" if result.found_tax_id else "✗"))

    def _load_settings_into_ui(self) -> None:
        self._populating_settings = True
        try:
            s = self._settings.settings
            self._chk_validate.setChecked(bool(s.validate_with_mustang))
            self._chk_ustg.setChecked(bool(s.ustg_check_enabled))
            st = self._settings.resolve_stationery_path()
            self._stationery_line.setText(st.name if st else "")
            self._chk_stationery_all.setChecked(bool(s.stationery_apply_all_pages))
        finally:
            self._populating_settings = False

    def _on_validate_toggled(self, checked: bool) -> None:
        if self._populating_settings:
            return
        self._settings.set_validate_with_mustang(bool(checked))

    def _on_stationery_all_toggled(self, checked: bool) -> None:
        if self._populating_settings:
            return
        self._settings.set_stationery_apply_all_pages(bool(checked))

    def _on_ustg_toggled(self, checked: bool) -> None:
        if self._populating_settings:
            return
        if checked:
            self._settings.set_ustg_check_enabled(True)
            self._telemetry.logger.info("Compliance-Check §14 UStG aktiviert.")
            return

        msg = QtWidgets.QMessageBox(self)
        msg.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        msg.setWindowTitle("Risiko-Akzeptanz")
        msg.setText(
            "Achtung: Das Deaktivieren dieser Schutzfunktion entbindet das Programm von der inhaltlichen Pflichtprüfung "
            "nach § 14 UStG. Sie übernehmen die alleinige Verantwortung für die rechtliche Korrektheit der verarbeiteten "
            "Dokumente. Der Programmhersteller übernimmt keinerlei Gewährleistung."
        )
        accept = msg.addButton("Ich akzeptiere das Risiko", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        cancel = msg.addButton("Abbrechen", QtWidgets.QMessageBox.ButtonRole.RejectRole)
        msg.exec()
        if msg.clickedButton() is accept:
            self._settings.set_ustg_check_enabled(False)
            self._telemetry.logger.info("Compliance-Check §14 UStG deaktiviert (Risiko akzeptiert).")
            return

        self._populating_settings = True
        try:
            self._chk_ustg.setChecked(True)
        finally:
            self._populating_settings = False

    def _on_stationery_dropped(self, path: str) -> None:
        self._set_stationery_from_path(Path(path))

    def _choose_stationery(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Briefbogen auswählen", "", "PDF (*.pdf)")
        if not path:
            return
        self._set_stationery_from_path(Path(path))

    def _set_stationery_from_path(self, src: Path) -> None:
        if not src.exists() or src.suffix.lower() != ".pdf":
            return
        dest = self._paths.stationery_dir / src.name
        if dest.exists():
            stem = src.stem
            n = 2
            while True:
                cand = self._paths.stationery_dir / f"{stem}_{n}{src.suffix}"
                if not cand.exists():
                    dest = cand
                    break
                n += 1
        try:
            shutil.copy2(src, dest)
        except Exception:
            return

        rel = dest.relative_to(self._paths.root_dir).as_posix()
        self._settings.set_stationery_rel(rel)
        self._stationery_line.setText(dest.name)
        self._telemetry.logger.info(f"Briefbogen gesetzt: {dest.name}")

        if self._current_invoice_pdf is not None and self._current_invoice_pdf.exists():
            self._start_compliance_recheck()

    def _start_invoice_import(self, invoice_pdf: Path) -> None:
        self._reset_pipeline_state()
        self._current_invoice_pdf = invoice_pdf
        self._telemetry.logger.info(f"Hauptrechnung importiert: {invoice_pdf.name}")
        self._status.set_state("neutral")
        self._btn_build.setEnabled(False)

        pool = QtCore.QThreadPool.globalInstance()
        pool.start(_InvoiceImportRunnable(self._compliance_bus, invoice_pdf))

    def _start_compliance_recheck(self) -> None:
        if self._current_invoice_pdf is None or self._invoice_payload is None:
            return
        self._last_bypass_missing = None
        st = self._settings.resolve_stationery_path()
        enabled = bool(self._settings.settings.ustg_check_enabled)
        pool = QtCore.QThreadPool.globalInstance()
        pool.start(_ComplianceRunnable(self._compliance_bus, self._current_invoice_pdf, self._invoice_payload, st, enabled))

    @QtCore.pyqtSlot(object, object) if hasattr(QtCore, "pyqtSlot") else QtCore.Slot(object, object)
    def _on_compliance_result_main(self, result: object, invoice_pdf: object) -> None:
        if isinstance(result, InvoiceImportResult):
            self._on_invoice_import_result(result)
            return
        self._on_compliance_result(result, invoice_pdf)  # type: ignore[arg-type]

    def _on_invoice_import_result(self, result: "InvoiceImportResult") -> None:
        if result.error is not None:
            self._status.set_state("error")
            self._telemetry.logger.info("Import-Fehler: " + result.error)
            self._invoice_payload = None
            self._btn_build.setEnabled(False)
            return

        if result.payload is None:
            self._status.set_state("error")
            self._invoice_payload = None
            self._btn_build.setEnabled(False)
            return

        self._invoice_payload = result.payload
        assert self._current_invoice_pdf is not None

        seller_name = result.payload.seller.name
        primary_tax = choose_primary_tax_id(result.payload.seller.tax_ids)

        if not self._settings.has_branding() and seller_name and primary_tax:
            self._settings.set_branding(seller_name, primary_tax)
            self._telemetry.logger.info(f"User-Branding erfolgreich für {seller_name} hinterlegt.")

        if not self._settings.matches_branding(seller_name, result.payload.seller.tax_ids):
            self._status.set_state("error")
            self._invoice_payload = None
            self._btn_build.setEnabled(False)
            self._telemetry.logger.info(
                "MANDANTEN_SPERRE | file=" + self._current_invoice_pdf.name + " | reason=branding_mismatch"
            )
            msg = QtWidgets.QMessageBox(self)
            msg.setIcon(QtWidgets.QMessageBox.Icon.Critical)
            msg.setWindowTitle("Warnung")
            msg.setText(
                "Warnung: Abweichender Rechnungsaussteller erkannt! Dieses Tool ist fest auf Ihr Unternehmensprofil "
                "geprägt. Verarbeitung abgebrochen."
            )
            msg.exec()
            return

        self._btn_build.setEnabled(True)
        self._start_compliance_recheck()

    def _on_compliance_result(self, result: ComplianceResult, invoice_pdf: Path) -> None:
        self._set_checklist(result)
        if result.error:
            self._status.set_state("error")
            self._telemetry.logger.info(f"Compliance-Fehler: {result.error}")
            return

        if result.ok:
            if self._last_bypass_missing is not None:
                self._status.set_state("warn")
                return
            self._status.set_state("warn" if result.used_stationery else "ok")
            return

        ustg_enabled = bool(self._settings.settings.ustg_check_enabled)
        if not ustg_enabled:
            self._status.set_state("warn")
            return

        critical_missing = [m for m in result.missing_fields if m in {"Firmenname", "USt-IdNr./Steuernummer"}]
        if not critical_missing:
            self._status.set_state("warn")
            return

        self._open_softblock(invoice_pdf, result.missing_fields)

    def _open_softblock(self, invoice_pdf: Path, missing_fields: tuple[str, ...]) -> None:
        msg = QtWidgets.QMessageBox(self)
        msg.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        msg.setWindowTitle("Compliance-Hinweis nach § 14 UStG")
        msg.setText(
            "Compliance-Hinweis nach § 14 UStG: Wichtige gesetzliche Stammdaten des Ausstellers (z. B. USt-IdNr. oder "
            "korrekte Firmierung) wurden im sichtbaren Text des PDF-Layouts nicht zweifelsfrei erkannt. Um den "
            "Vorsteuerabzug des Empfängers nicht zu gefährden, hinterlegen Sie bitte ein digitales Briefpapier (PDF) "
            "als Hintergrund-Ebene."
        )
        btn_stationery = msg.addButton("Briefbogen (PDF) jetzt hinterlegen", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        btn_bypass = msg.addButton(
            "Ignorieren und fortfahren (Bypass)", QtWidgets.QMessageBox.ButtonRole.DestructiveRole
        )
        msg.exec()

        if msg.clickedButton() is btn_stationery:
            self._choose_stationery()
            return

        self._last_bypass_missing = missing_fields
        self._telemetry.logger.info(
            "COMPLIANCE_BYPASS | file="
            + invoice_pdf.name
            + " | missing="
            + ",".join(missing_fields)
        )
        self._status.set_state("warn")

    def _reset_pipeline_state(self) -> None:
        self._current_invoice_pdf = None
        self._invoice_payload = None
        self._attachments = []
        self._last_bypass_missing = None
        self._btn_build.setEnabled(False)
        self._chkline_name.setText("🏢 Firmenname & Rechtsform -> —")
        self._chkline_addr.setText("📍 Anschrift / Adresse -> —")
        self._chkline_tax.setText("🆔 USt-IdNr. / Steuernummer -> —")
        self._status.set_state("neutral")

    def _start_build_pipeline(self) -> None:
        if self._current_invoice_pdf is None or self._invoice_payload is None:
            return

        if not self._settings.settings.validate_with_mustang:
            msg = QtWidgets.QMessageBox(self)
            msg.setIcon(QtWidgets.QMessageBox.Icon.Warning)
            msg.setWindowTitle("Hinweis")
            msg.setText("Die Mustang-Endkontrolle ist deaktiviert. Aktivieren Sie diese Option für den Export.")
            msg.exec()
            return

        java = self._deps.selection.java_exe
        jar = self._deps.selection.mustang_jar
        if java is None or jar is None:
            self._status.set_state("error")
            self._telemetry.logger.info("Mustang-Validierung nicht möglich: JRE oder Mustang-JAR fehlt.")
            return

        self._telemetry.logger.info("Build-Pipeline gestartet (Stamping/Merge/Attachment/Validation).")
        self._btn_build.setEnabled(False)

        st = self._settings.resolve_stationery_path()
        apply_all = bool(self._settings.settings.stationery_apply_all_pages)
        tmp_out = self._paths.tmp_dir / ("zug2ferd_build_" + str(os.getpid()) + ".pdf")

        pool = QtCore.QThreadPool.globalInstance()
        pool.start(
            _BuildRunnable(
                self._build_bus(),
                self._current_invoice_pdf,
                self._invoice_payload.xml_bytes,
                list(self._attachments),
                st,
                apply_all,
                java,
                jar,
                tmp_out,
            )
        )

    def _build_bus(self) -> "_BuildBus":
        if not hasattr(self, "_pipeline_bus"):
            self._pipeline_bus = _BuildBus()
            self._pipeline_bus.done.connect(self._on_build_done_main)
        return self._pipeline_bus

    @QtCore.pyqtSlot(object) if hasattr(QtCore, "pyqtSlot") else QtCore.Slot(object)
    def _on_build_done_main(self, result: object) -> None:
        res = result  # type: ignore[assignment]
        if not isinstance(res, BuildResult):
            return

        if not res.ok or res.output_pdf_path is None:
            self._status.set_state("error")
            self._btn_build.setEnabled(True)
            self._telemetry.logger.info("Build fehlgeschlagen: " + (res.error or "unbekannt"))
            if res.mustang_stdout:
                self._telemetry.logger.info(res.mustang_stdout.strip())
            if res.mustang_stderr:
                self._telemetry.logger.info(res.mustang_stderr.strip())
            return

        if not res.mustang_ok:
            self._status.set_state("error")
            self._btn_build.setEnabled(True)
            self._telemetry.logger.info("Mustang-Validierung fehlgeschlagen. Export blockiert.")
            if res.mustang_stdout:
                self._telemetry.logger.info(res.mustang_stdout.strip())
            if res.mustang_stderr:
                self._telemetry.logger.info(res.mustang_stderr.strip())
            return

        self._status.set_state("ok")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Speichern unter...", "", "PDF (*.pdf)")
        if not path:
            self._btn_build.setEnabled(True)
            return

        try:
            Path(path).write_bytes(res.output_pdf_path.read_bytes())
        except Exception:
            self._btn_build.setEnabled(True)
            self._telemetry.logger.info("Konnte Export nicht schreiben.")
            return

        self._telemetry.logger.info("Export gespeichert: " + Path(path).name)
        self._btn_build.setEnabled(True)

    def _refresh_versions(self) -> None:
        self._populating_versions = True
        try:
            jars = self._deps.list_mustang_jars()
            jres = self._deps.list_jre_dirs()

            current_jar = self._deps.selection.mustang_jar.name if self._deps.selection.mustang_jar else None
            current_jre = self._deps.selection.jre_dir.name if self._deps.selection.jre_dir else None

            self._cmb_mustang.clear()
            if not jars:
                self._cmb_mustang.addItem("(keine JAR)")
            else:
                for p in jars:
                    self._cmb_mustang.addItem(p.name)
                if current_jar and current_jar in [p.name for p in jars]:
                    self._cmb_mustang.setCurrentText(current_jar)
                else:
                    self._cmb_mustang.setCurrentIndex(len(jars) - 1)
                    self._deps.set_selected_mustang_by_name(self._cmb_mustang.currentText())

            self._cmb_jre.clear()
            if not jres:
                self._cmb_jre.addItem("(keine JRE)")
            else:
                for p in jres:
                    self._cmb_jre.addItem(p.name)
                if current_jre and current_jre in [p.name for p in jres]:
                    self._cmb_jre.setCurrentText(current_jre)
                else:
                    self._cmb_jre.setCurrentIndex(len(jres) - 1)
                    self._deps.set_selected_jre_by_name(self._cmb_jre.currentText())
        finally:
            self._populating_versions = False

    def _on_mustang_changed(self) -> None:
        if self._populating_versions:
            return
        name = self._cmb_mustang.currentText().strip()
        if not name or name.startswith("("):
            return
        self._deps.set_selected_mustang_by_name(name)

    def _on_jre_changed(self) -> None:
        if self._populating_versions:
            return
        name = self._cmb_jre.currentText().strip()
        if not name or name.startswith("("):
            return
        self._deps.set_selected_jre_by_name(name)

    def _log_selection(self) -> None:
        jar = self._deps.selection.mustang_jar.name if self._deps.selection.mustang_jar else "(keine)"
        jre = self._deps.selection.jre_dir.name if self._deps.selection.jre_dir else "(keine)"
        self._telemetry.logger.info(f"Aktive Auswahl: Mustang={jar} | JRE={jre}")


class _ComplianceBus(QtCore.QObject):
    result = Signal(object, object)


@dataclass(frozen=True)
class InvoiceImportResult:
    payload: Optional[InvoicePayload]
    error: Optional[str]


class _InvoiceImportRunnable(QtCore.QRunnable):
    def __init__(self, bus: _ComplianceBus, invoice_pdf: Path) -> None:
        super().__init__()
        self._bus = bus
        self._invoice = invoice_pdf

    def run(self) -> None:
        try:
            payload = extract_invoice_payload(self._invoice)
            res = InvoiceImportResult(payload=payload, error=None)
        except Exception as e:
            res = InvoiceImportResult(payload=None, error=str(e))
        self._bus.result.emit(res, self._invoice)


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
        self._bus.result.emit(result, self._invoice)


class _BuildBus(QtCore.QObject):
    done = Signal(object)


class _BuildRunnable(QtCore.QRunnable):
    def __init__(
        self,
        bus: _BuildBus,
        invoice_pdf: Path,
        xml_bytes: bytes,
        attachments: list[Path],
        stationery_pdf: Optional[Path],
        apply_all_pages: bool,
        java_exe: Path,
        mustang_jar: Path,
        tmp_output: Path,
    ) -> None:
        super().__init__()
        self._bus = bus
        self._invoice = invoice_pdf
        self._xml_bytes = xml_bytes
        self._attachments = attachments
        self._stationery = stationery_pdf
        self._apply_all = apply_all_pages
        self._java = java_exe
        self._jar = mustang_jar
        self._out = tmp_output

    def run(self) -> None:
        try:
            pages: list[bytes] = []

            inv_stamped = stamp_pdf_underlay(self._invoice, self._stationery, self._apply_all)
            pages.append(inv_stamped)

            for att in self._attachments:
                if is_pdf_path(att):
                    stamped = stamp_pdf_underlay(att, self._stationery, self._apply_all)
                    pages.append(stamped)
                    continue
                if is_image_path(att):
                    pdf_bytes = convert_image_to_pdf_bytes(att)
                    if self._stationery is not None and self._stationery.exists():
                        tmp = Path(self._out.parent) / ("img_" + str(uuid.uuid4()) + ".pdf")
                        tmp.write_bytes(pdf_bytes)
                        stamped = stamp_pdf_underlay(tmp, self._stationery, self._apply_all)
                        tmp.unlink(missing_ok=True)
                        pages.append(stamped)
                    else:
                        pages.append(pdf_bytes)
                    continue

            merge_pdfs_to_file(self._out, pages)
            attach_facturx_xml(self._out, self._xml_bytes)

            mustang_ok, mout, merr = run_mustang_validate(self._java, self._jar, self._out)
            res = BuildResult(
                ok=True,
                output_pdf_path=self._out,
                mustang_ok=mustang_ok,
                mustang_stdout=mout,
                mustang_stderr=merr,
                error=None,
            )
        except Exception as e:
            res = BuildResult(
                ok=False,
                output_pdf_path=None,
                mustang_ok=False,
                mustang_stdout="",
                mustang_stderr="",
                error=str(e),
            )

        self._bus.done.emit(res)
