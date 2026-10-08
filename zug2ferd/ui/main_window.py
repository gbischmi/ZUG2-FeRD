from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from zug2ferd.core.bundle_detector import (
    BUNDLE_ROLE_ATTACHMENT,
    BUNDLE_ROLE_INVOICE,
    BUNDLE_ROLE_STATEMENT,
    BUNDLE_ROLE_SUMMARY,
    BUNDLE_ROLE_UNKNOWN,
    BundleFileEntry,
    DetectedBundle,
    build_bundle_entry,
    bundle_ready_for_export,
    describe_role,
    detect_bundles,
    parse_bundle_filename,
)
from zug2ferd.core.compliance import (
    ComplianceResult,
    InvoicePayload,
    SellerData,
    choose_primary_tax_id,
    extract_invoice_payload,
    run_ustg_check_with_payload,
    parse_seller_data,
)
from zug2ferd.core.deps import DependencyManager
from zug2ferd.core.paths import AppPaths
from zug2ferd.core.pipeline import (
    BuildResult,
    XmlAssembleResult,
    assemble_zugferd_pdf,
    assemble_zugferd_pipeline,
    attach_facturx_xml,
    convert_image_to_pdf_bytes,
    is_image_path,
    is_pdf_path,
    merge_pdfs_to_file,
    run_mustang_validate,
    stamp_pdf_underlay,
)
from zug2ferd.core.xml_normcheck import (
    NormCheckResult,
    NormalizedInvoiceData,
    check_xml_file,
    normalize_invoice_data,
    parse_invoice_xml,
)
from zug2ferd.core.settings import SettingsStore
from zug2ferd.core.telemetry import Telemetry
from zug2ferd.qt import QtCore, QtGui, QtWidgets
from zug2ferd.ui.admin_window import AdminWindow
from zug2ferd.ui.widgets import (
    ClickableLabel,
    DocumentTableWidget,
    PdfDropLineEdit,
    StatusLamp,
)


Signal = QtCore.pyqtSignal if hasattr(QtCore, "pyqtSignal") else QtCore.Signal
ROLE_PATH = int(QtCore.Qt.ItemDataRole.UserRole)
ROLE_KIND = int(QtCore.Qt.ItemDataRole.UserRole) + 1


@dataclass
class StandaloneXmlEntry:
    xml_bytes: bytes
    normcheck: NormCheckResult
    seller: SellerData


@dataclass
class DocumentEntry:
    path: Path
    kind: str
    payload: Optional[InvoicePayload] = None
    xml_entry: Optional[StandaloneXmlEntry] = None
    norm_ok: Optional[bool] = None
    norm_details: Optional[NormCheckResult] = None
    pdfa3_ok: Optional[bool] = None
    pdfa3_issues: tuple[str, ...] = ()
    consistency_ok: Optional[bool] = None
    consistency_details: tuple[str, ...] = ()
    bundle_role: str = BUNDLE_ROLE_UNKNOWN
    bundle_group_key: Optional[str] = None
    bundle_validation: tuple[str, ...] = ()


KIND_INVOICE = "invoice"
KIND_BASE_PDF = "base_pdf"
KIND_XML_DATASET = "xml_dataset"
KIND_RBU = "rbu"
KIND_BUNDLE_BASE = "bundle_base"
KIND_BUNDLE_RBU = "bundle_rbu"


class MainWindow(QtWidgets.QMainWindow):
    """Koordiniert GUI, Dokumenttabelle, Compliance-Checks und Export-Pipeline."""

    def __init__(
        self,
        paths: AppPaths,
        telemetry: Telemetry,
        deps: DependencyManager,
        settings: SettingsStore,
    ) -> None:
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
        self._detected_bundles: list[DetectedBundle] = []
        self._active_bundle_key: Optional[str] = None

        self._footer_click_count = 0
        self._footer_reset_timer = QtCore.QTimer(self)
        self._footer_reset_timer.setSingleShot(True)
        self._footer_reset_timer.setInterval(900)
        self._footer_reset_timer.timeout.connect(self._reset_footer_clicks)

        self._compliance_bus = _ComplianceBus()
        self._compliance_bus.compliance_checked.connect(self._on_compliance_result)
        self._build_bus = _BuildBus()
        self._build_bus.done.connect(self._on_build_done_main)
        self._assemble_bus = _XmlAssembleBus()
        self._assemble_bus.done.connect(self._on_assemble_done_main)

        self.setWindowTitle("ZUG2-FeRD // Community Edition")
        self.resize(1180, 760)
        self.setMinimumSize(980, 620)
        self._apply_window_icon()
        self._build_ui()
        self._install_options_overlay()

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
        self._gear_btn.setIcon(
            self.style().standardIcon(
                QtWidgets.QStyle.StandardPixmap.SP_FileDialogDetailedView
            )
        )
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
        self._btn_build = QtWidgets.QPushButton("Zusammenführen")
        self._btn_build.setEnabled(False)
        self._btn_build.clicked.connect(self._start_build_pipeline)
        self._btn_reset = QtWidgets.QPushButton("Reset")
        self._btn_reset.clicked.connect(self._reset_pipeline_state)

        actions_layout.addWidget(self._btn_import, 0)
        actions_layout.addWidget(self._btn_build, 1)
        actions_layout.addWidget(self._btn_reset, 0)
        left_layout.addWidget(actions, 0)

        layout.addWidget(left, 1)
        return widget

    def _build_options_panel(self) -> QtWidgets.QFrame:
        panel = QtWidgets.QFrame()
        panel.setObjectName("OptionsPanel")

        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        title = QtWidgets.QLabel("Einstellungen")
        title.setObjectName("OptionsTitle")
        layout.addWidget(title, 0)

        self._chk_validate = QtWidgets.QCheckBox(
            "Generierte Dateien vor Export per Mustang validieren"
        )
        self._chk_validate.toggled.connect(self._on_validate_toggled)
        self._chk_ustg = QtWidgets.QCheckBox(
            "Optischen XML-zu-PDF Pflichtangaben-Abgleich (§ 14 UStG) durchführen"
        )
        self._chk_ustg.toggled.connect(self._on_ustg_toggled)
        self._chk_stationery_all = QtWidgets.QCheckBox(
            "Briefbogen auf alle Seiten anwenden"
        )
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

    def _install_options_overlay(self) -> None:
        central = self.centralWidget()
        if central is None:
            return

        self._options_dim = QtWidgets.QWidget(central)
        self._options_dim.setObjectName("OptionsDimOverlay")
        self._options_dim.setStyleSheet(
            "QWidget#OptionsDimOverlay { background-color: rgba(0, 0, 0, 110); }"
        )
        self._options_dim.setAttribute(
            QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, False
        )
        self._options_dim.hide()

        self._options_scroll = QtWidgets.QScrollArea(central)
        self._options_scroll.setObjectName("OptionsScrollArea")
        self._options_scroll.setWidgetResizable(True)
        self._options_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self._options_scroll.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._options_scroll.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._options_scroll.setStyleSheet(
            "QScrollArea#OptionsScrollArea {"
            " background-color: transparent;"
            " border: 0px;"
            "}"
            "QScrollBar:vertical {"
            " background: #0f1115;"
            " width: 10px;"
            " margin: 10px 0px 10px 0px;"
            " border-radius: 5px;"
            "}"
            "QScrollBar::handle:vertical {"
            " background: #3a3f4a;"
            " min-height: 40px;"
            " border-radius: 5px;"
            "}"
            "QScrollBar::handle:vertical:hover { background: #4a505c; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
        )

        inner = self._build_options_panel()
        inner.setFixedWidth(368)
        self._options_scroll.setWidget(inner)
        self._options_panel = inner
        self._options_scroll.hide()

        try:
            dim_type = QtCore.QEvent.Type.MouseButtonPress
        except Exception:
            dim_type = QtCore.QEvent.MouseButtonPress

        class _DimFilter(QtCore.QObject):
            def __init__(self, cb, outer):
                super().__init__(outer)
                self._cb = cb

            def eventFilter(self, obj, event):
                if event.type() == dim_type:
                    btn = (
                        event.button()
                        if hasattr(event, "button")
                        else QtCore.Qt.MouseButton.LeftButton
                    )
                    if btn == QtCore.Qt.MouseButton.LeftButton:
                        self._cb()
                        return True
                return False

        self._dim_filter = _DimFilter(self._close_options_overlay, self)
        self._options_dim.installEventFilter(self._dim_filter)

        try:
            shortcut_cls = QtGui.QShortcut
        except AttributeError:
            shortcut_cls = getattr(QtWidgets, "QShortcut", None)
            if shortcut_cls is None:
                shortcut_cls = getattr(QtCore, "QShortcut", None)
        if shortcut_cls is not None:
            try:
                seq = QtGui.QKeySequence(QtGui.QKeySequence.StandardKey.Cancel)
                if seq.isEmpty():
                    raise ValueError("empty sequence")
            except Exception:
                seq = QtGui.QKeySequence(QtCore.Qt.Key.Key_Escape)
            self._esc_shortcut = shortcut_cls(seq, self)
            try:
                ctx_enum = QtCore.Qt.ShortcutContext
            except AttributeError:
                ctx_enum = (
                    getattr(
                        getattr(QtWidgets, "ShortcutContext", None),
                        "WindowShortcut",
                        None,
                    )
                    or QtCore.Qt.WindowShortcut
                )
            try:
                self._esc_shortcut.setContext(ctx_enum.WindowShortcut)
            except Exception:
                try:
                    self._esc_shortcut.setContext(QtCore.Qt.WindowShortcut)
                except Exception:
                    pass
            self._esc_shortcut.activated.connect(self._close_options_overlay)

        self._options_anim: Optional[QtCore.QPropertyAnimation] = None
        self._options_anim_target_visible = False

    def _should_use_animation(self) -> bool:
        try:
            hints = QtGui.QGuiApplication.styleHints()
            if not hints.animated():
                return False
        except Exception:
            return False
        try:
            screen = QtGui.QGuiApplication.primaryScreen()
            if screen is not None:
                rr = screen.refreshRate()
                if 0 < rr < 30:
                    return False
        except Exception:
            pass
        return True

    def _stop_options_animation(self, jump_to_end: bool = False) -> None:
        if self._options_anim is None:
            return
        state = self._options_anim.state()
        if state == QtCore.QAbstractAnimation.State.Stopped:
            self._options_anim = None
            return
        self._options_anim.stop()
        if jump_to_end:
            try:
                end_val = self._options_anim.endValue()
                if end_val is not None:
                    self._options_scroll.setGeometry(end_val)
            except Exception:
                pass
        self._options_anim = None

    def _options_geometry_for(self, hidden: bool) -> Optional[QtCore.QRect]:
        central = self.centralWidget()
        if central is None:
            return None
        cw = central.width()
        ch = central.height()
        margins = self.centralWidget().layout().getContentsMargins()
        pad_top = margins[1] if len(margins) >= 4 else 0
        pad_right = margins[2] if len(margins) >= 4 else 0
        pad_bottom = margins[3] if len(margins) >= 4 else 0
        panel_w = 380
        panel_x = cw - panel_w - pad_right if not hidden else cw + 8
        panel_y = pad_top
        panel_h = ch - pad_top - pad_bottom
        return QtCore.QRect(panel_x, panel_y, panel_w, panel_h)

    def _position_options_overlay(self) -> None:
        central = self.centralWidget()
        if central is None:
            return
        cw = central.width()
        ch = central.height()
        margins = self.centralWidget().layout().getContentsMargins()
        pad_top = margins[1] if len(margins) >= 4 else 0
        pad_right = margins[2] if len(margins) >= 4 else 0
        pad_bottom = margins[3] if len(margins) >= 4 else 0
        panel_w = 380
        panel_x = cw - panel_w - pad_right
        panel_y = pad_top
        panel_h = ch - pad_top - pad_bottom

        self._options_dim.setGeometry(0, 0, cw, ch)
        self._options_scroll.setGeometry(panel_x, panel_y, panel_w, panel_h)

    def _close_options_overlay(self, *, force_instant: bool = False) -> None:
        if (
            not self._options_dim.isVisible()
            and not self._options_scroll.isVisible()
            and self._options_anim is None
        ):
            return
        self._options_anim_target_visible = False
        use_anim = (not force_instant) and self._should_use_animation()
        target = self._options_geometry_for(hidden=True)
        if target is None:
            self._stop_options_animation(jump_to_end=False)
            self._options_dim.hide()
            self._options_scroll.hide()
            return
        if use_anim and self._options_scroll.isVisible():
            self._stop_options_animation(jump_to_end=False)
            anim = QtCore.QPropertyAnimation(self._options_scroll, b"geometry")
            anim.setDuration(170)
            anim.setEasingCurve(QtCore.QEasingCurve.Type.InCubic)
            anim.setStartValue(self._options_scroll.geometry())
            anim.setEndValue(target)
            anim.finished.connect(self._on_options_anim_finished)
            self._options_anim = anim
            anim.start(QtCore.QAbstractAnimation.DeletionPolicy.KeepWhenStopped)
            return
        self._stop_options_animation(jump_to_end=False)
        self._options_dim.hide()
        self._options_scroll.hide()

    def _on_options_anim_finished(self) -> None:
        target_visible = bool(self._options_anim_target_visible)
        self._stop_options_animation(jump_to_end=False)
        if target_visible:
            self._position_options_overlay()
            self._options_dim.show()
            self._options_scroll.show()
            self._options_scroll.raise_()
            self._options_dim.raise_()
            self._options_scroll.raise_()
        else:
            self._options_dim.hide()
            self._options_scroll.hide()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        if self._options_anim is not None:
            self._stop_options_animation(jump_to_end=False)
            if self._options_anim_target_visible:
                self._position_options_overlay()
                self._options_dim.show()
                self._options_scroll.show()
                self._options_scroll.raise_()
                self._options_dim.raise_()
                self._options_scroll.raise_()
            else:
                self._options_dim.hide()
                self._options_scroll.hide()
            return
        if self._options_scroll.isVisible() or self._options_dim.isVisible():
            self._position_options_overlay()

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        if self._options_scroll.isVisible() or self._options_dim.isVisible():
            self._position_options_overlay()

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
        self._footer_label.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
        )
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
        visible_or_opening = (
            self._options_scroll.isVisible()
            or self._options_dim.isVisible()
            or (self._options_anim is not None and self._options_anim_target_visible)
        )
        if visible_or_opening:
            self._close_options_overlay()
            return
        self._options_anim_target_visible = True
        self._position_options_overlay()
        dim_geo = self._options_dim.geometry()
        if dim_geo.width() <= 0 or dim_geo.height() <= 0:
            self._stop_options_animation(jump_to_end=False)
            self._options_dim.show()
            self._options_scroll.show()
            self._options_scroll.raise_()
            self._options_dim.raise_()
            self._options_scroll.raise_()
            return
        target_vis = self._options_geometry_for(hidden=False)
        target_hid = self._options_geometry_for(hidden=True)
        if target_vis is None or target_hid is None:
            self._stop_options_animation(jump_to_end=False)
            self._options_dim.show()
            self._options_scroll.show()
            self._options_scroll.raise_()
            self._options_dim.raise_()
            self._options_scroll.raise_()
            return
        use_anim = self._should_use_animation()
        self._stop_options_animation(jump_to_end=False)
        self._options_dim.setGeometry(0, 0, dim_geo.width(), dim_geo.height())
        self._options_scroll.setGeometry(target_hid)
        self._options_dim.show()
        self._options_scroll.show()
        self._options_scroll.raise_()
        self._options_dim.raise_()
        self._options_scroll.raise_()
        if not use_anim:
            self._options_scroll.setGeometry(target_vis)
            return
        anim = QtCore.QPropertyAnimation(self._options_scroll, b"geometry")
        anim.setDuration(180)
        anim.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
        anim.setStartValue(target_hid)
        anim.setEndValue(target_vis)
        anim.finished.connect(self._on_options_anim_finished)
        self._options_anim = anim
        anim.start(QtCore.QAbstractAnimation.DeletionPolicy.KeepWhenStopped)

    def _refresh_document_table(self) -> None:
        self._document_table.setRowCount(0)
        self._evaluate_consistency_flags()
        overall_has_unsafe_norm = False
        overall_has_pdfa3_issue = False
        overall_has_consistency_issue = False
        for row, doc in enumerate(self._documents):
            self._document_table.insertRow(row)

            if doc.kind == KIND_INVOICE:
                kind_text = "E-Rechnung"
                if doc.norm_ok is False:
                    row_color = QtGui.QColor("#e06060")
                    overall_has_unsafe_norm = True
                elif doc.pdfa3_ok is False:
                    row_color = QtGui.QColor("#e68a00")
                    overall_has_unsafe_norm = True
                    overall_has_pdfa3_issue = True
                else:
                    row_color = QtGui.QColor("#80e0b0")
                if doc.pdfa3_ok is False:
                    overall_has_pdfa3_issue = True
            elif doc.kind == KIND_BASE_PDF:
                kind_text = "Basis-PDF"
                row_color = QtGui.QColor("#80c0e0")
            elif doc.kind == KIND_XML_DATASET:
                kind_text = "E-Rechnungs-XML"
                if doc.norm_ok is False:
                    row_color = QtGui.QColor("#e06060")
                    overall_has_unsafe_norm = True
                else:
                    row_color = QtGui.QColor("#e0c060")
            elif doc.kind == KIND_BUNDLE_BASE:
                kind_text = "GESAMT-Paket Basis (E-SUMMARY)"
                row_color = QtGui.QColor("#a0c8a0")
            elif doc.kind == KIND_BUNDLE_RBU:
                prefix = ""
                if doc.bundle_role == BUNDLE_ROLE_INVOICE:
                    prefix = "RBU 1: "
                elif doc.bundle_role == BUNDLE_ROLE_ATTACHMENT:
                    prefix = "RBU 2: "
                elif doc.bundle_role == BUNDLE_ROLE_STATEMENT:
                    prefix = "RBU 3: "
                kind_text = prefix + describe_role(doc.bundle_role)
                row_color = QtGui.QColor("#d0c080")
            else:
                kind_text = "RBU"
                row_color = None

            format_text = doc.path.suffix.lower().lstrip(".").upper()
            if doc.kind == KIND_INVOICE and doc.pdfa3_ok is False:
                format_text += " (KEIN PDF/A-3)"
            if doc.bundle_validation and doc.kind in (
                KIND_BUNDLE_BASE,
                KIND_BUNDLE_RBU,
                KIND_INVOICE,
                KIND_BASE_PDF,
            ):
                overall_has_consistency_issue = True

            if doc.consistency_ok is False and doc.kind != KIND_RBU:
                overall_has_consistency_issue = True

            kind_item = QtWidgets.QTableWidgetItem(kind_text)
            kind_item.setData(ROLE_PATH, str(doc.path))
            kind_item.setData(ROLE_KIND, doc.kind)
            file_display_name = doc.path.name
            file_color_override: Optional[QtGui.QColor] = None
            if (
                doc.bundle_validation
                and doc.bundle_group_key == self._active_bundle_key
            ):
                file_display_name += "   ⚠️"
                file_color_override = QtGui.QColor("#c83a3a")
            elif doc.consistency_ok is False and doc.kind != KIND_RBU:
                file_display_name += "   ⚠️"
                file_color_override = QtGui.QColor("#c83a3a")
            file_item = QtWidgets.QTableWidgetItem(file_display_name)
            file_item.setData(ROLE_PATH, str(doc.path))
            file_item.setData(ROLE_KIND, doc.kind)
            if file_color_override is not None:
                file_item.setForeground(file_color_override)
            format_item = QtWidgets.QTableWidgetItem(format_text)
            format_item.setData(ROLE_PATH, str(doc.path))
            format_item.setData(ROLE_KIND, doc.kind)
            if doc.kind == KIND_INVOICE and doc.pdfa3_ok is False:
                format_item.setForeground(QtGui.QBrush(QtGui.QColor("#e03030")))

            if row_color is not None:
                if doc.kind != KIND_INVOICE or doc.pdfa3_ok is not False:
                    for item in (kind_item, format_item):
                        item.setForeground(QtGui.QBrush(row_color))
                else:
                    kind_item.setForeground(QtGui.QBrush(row_color))
                if file_color_override is None:
                    file_item.setForeground(QtGui.QBrush(row_color))

            self._document_table.setItem(row, 0, kind_item)
            self._document_table.setItem(row, 1, file_item)
            self._document_table.setItem(row, 2, format_item)

        active_bundle = self._get_active_bundle()
        embedded_invoice = self._get_invoice_document()
        standalone_xml = self._get_standalone_xml()
        base_pdf = self._get_base_pdf()
        attachments = self._get_rbu_documents()

        consistency_note = ""
        if overall_has_consistency_issue:
            consistency_note = (
                " ⚠️ Hinweis: Die Übereinstimmung zwischen Druckbeleg (PDF) und E-Rechnungs-Datensatz (XML) "
                "konnte anhand Dateiname oder PDF-Inhalt nicht bestätigt werden. Bitte manuell prüfen."
            )

        if active_bundle is not None:
            bundle_complete = (
                active_bundle.summary_file is not None
                and active_bundle.invoice_file is not None
                and active_bundle.invoice_file.has_embedded_xml
            )

            if not bundle_complete:
                missing: list[str] = []
                if active_bundle.summary_file is None:
                    missing.append("E-SUMMARY (-000)")
                if active_bundle.invoice_file is None:
                    missing.append("E-INVOICE (-001) mit eingebettetem XML")
                elif not active_bundle.invoice_file.has_embedded_xml:
                    missing.append("E-INVOICE (-001) mit eingebettetem XML (XML fehlt)")
                if missing:
                    missing_text = " und ".join(missing)
                    self._summary_label.setText(
                        f"{len(self._documents)} Datei(en) importiert. "
                        f"Bitte fügen Sie bei Bedarf {missing_text} hinzu, um ein vollständiges GESAMT-Paket zu erzeugen."
                        + consistency_note
                    )
                else:
                    self._summary_label.setText(
                        f"{len(self._documents)} Datei(en) importiert."
                        + consistency_note
                    )
                self._btn_build.setEnabled(False)
            else:
                bundle_notes: list[str] = []
                build_enabled = True
                bundle_has_problem = False
                if active_bundle.validation_issues:
                    for iss in active_bundle.validation_issues[:3]:
                        bundle_notes.append(iss)
                    bundle_has_problem = True
                bundle_rbu_count = sum(
                    1
                    for f in active_bundle.files
                    if f.info.role
                    in (
                        BUNDLE_ROLE_INVOICE,
                        BUNDLE_ROLE_ATTACHMENT,
                        BUNDLE_ROLE_STATEMENT,
                    )
                )
                if bundle_has_problem:
                    self._summary_label.setText(
                        "GESAMT-Paket-Modus erkannt: Dach-Rechnung "
                        + (active_bundle.roof_token or "(unbekannt)")
                        + f" | Basis-PDF: {active_bundle.summary_file.path.name if active_bundle.summary_file else '(fehlt)'}"
                        + f" | RBU-Dokumente: {bundle_rbu_count}"
                        + ("" if not bundle_notes else " ⚠️ " + "; ".join(bundle_notes))
                        + consistency_note
                    )
                else:
                    rbu_docs = [
                        d
                        for d in self._documents
                        if d.kind in (KIND_BUNDLE_RBU, KIND_INVOICE, KIND_RBU)
                        and d.path
                        != (
                            active_bundle.summary_file.path
                            if active_bundle.summary_file
                            else None
                        )
                    ]
                    pdfa3_note = ""
                    if (
                        active_bundle.summary_file is not None
                        and getattr(active_bundle.summary_file, "pdfa3_ok", None)
                        is False
                    ):
                        pdfa3_note = " Hinweis: Die Basis-PDF-Datei entspricht derzeit noch NICHT dem PDF/A-3-Standard – dies wird beim Zusammenführen automatisch korrigiert (Self-Healing)."
                    base_name = (
                        active_bundle.summary_file.path.name
                        if active_bundle.summary_file
                        else "(Basis-PDF)"
                    )
                    self._summary_label.setText(
                        f"Belegpaket (Gesamt-Bundle) mit Basis-PDF '{base_name}' und {len(rbu_docs)} RBU-Dokument(en). "
                        "Reihenfolge wird automatisch nach Bundle-Rollen (Summary → Invoice → Attachment → Statement) sortiert."
                        + pdfa3_note
                        + consistency_note
                    )
                self._btn_build.setEnabled(build_enabled)
        elif embedded_invoice is not None:
            pdfa3_note = ""
            if embedded_invoice.pdfa3_ok is False:
                pdfa3_note = " Hinweis: Die PDF-Datei entspricht derzeit noch NICHT dem PDF/A-3-Standard – dies wird beim Zusammenführen automatisch korrigiert."
            self._summary_label.setText(
                f"Belegpaket mit 1 vorhandener ZUGFeRD-E-Rechnung und {len(attachments)} RBU-Dokument(en). "
                "Reihenfolge ist frei sortierbar." + pdfa3_note + consistency_note
            )
            self._btn_build.setEnabled(True)
        elif standalone_xml is not None and base_pdf is not None:
            self._summary_label.setText(
                "PDF+XML-Modus: Basis-PDF '"
                + base_pdf.path.name
                + "' wird mit E-Rechnungs-XML ("
                + standalone_xml.normcheck.dialect
                + ") zu einer ZUGFeRD-Datei kombiniert. "
                + f"{len(attachments)} RBU-Dokument(e) zusätzlich geladen."
                + consistency_note
            )
            self._btn_build.setEnabled(True)
        elif standalone_xml is not None and base_pdf is None:
            self._summary_label.setText(
                "E-Rechnungs-XML geladen, aber noch kein Basis-PDF vorhanden. Bitte zusätzlich das PDF-Rechnungsdokument ablegen."
            )
            self._btn_build.setEnabled(False)
        elif base_pdf is not None and standalone_xml is None:
            self._summary_label.setText(
                "Basis-PDF geladen, aber noch kein E-Rechnungs-XML vorhanden. Bitte zusätzlich das EN 16931 XML ablegen."
            )
            self._btn_build.setEnabled(False)
        else:
            self._summary_label.setText(
                "Noch kein Belegpaket geladen. Hinweis: Sie können entweder eine vorhandene ZUGFeRD-PDF plus Anhänge laden "
                "oder die Kombination aus einem normalen PDF (Rechnungsbild) plus einem separaten EN 16931 XML-Datensatz."
            )
            self._btn_build.setEnabled(False)

        if overall_has_unsafe_norm:
            self._status.set_state("unsafe")
        elif overall_has_pdfa3_issue:
            self._status.set_state("warn")
        elif active_bundle is not None and active_bundle.validation_issues:
            self._status.set_state("warn")

    def _get_invoice_document(self) -> Optional[DocumentEntry]:
        for doc in self._documents:
            if doc.kind == KIND_INVOICE:
                return doc
        return None

    def _get_standalone_xml(self) -> Optional[StandaloneXmlEntry]:
        for doc in self._documents:
            if doc.kind == KIND_XML_DATASET and doc.xml_entry is not None:
                return doc.xml_entry
        return None

    def _get_base_pdf(self) -> Optional[DocumentEntry]:
        for doc in self._documents:
            if doc.kind == KIND_BASE_PDF:
                return doc
        return None

    def _get_rbu_documents(self) -> list[DocumentEntry]:
        return [d for d in self._documents if d.kind == KIND_RBU]

    def _build_mode(self) -> str:
        if self._get_active_bundle() is not None:
            return "bundle_mode"
        if self._get_invoice_document() is not None:
            return "merge_mode"
        if self._get_standalone_xml() is not None and self._get_base_pdf() is not None:
            return "assemble_mode"
        if self._get_standalone_xml() is not None:
            rb_docs = self._get_rbu_documents()
            if rb_docs:
                return "rbu_promote_pending"
        return "none"

    def _detect_invoice_pdf_reference_relationship(
        self, standalone_xml_bytes: bytes, candidate_pdf: DocumentEntry
    ) -> Optional[list[str]]:
        findings: list[str] = []
        try:
            norm = normalize_invoice_data(standalone_xml_bytes)
        except Exception:
            return None
        invoice_id = (norm.invoice_id or "").strip()
        issue_date = (norm.issue_date or "").strip()
        pdf_name = candidate_pdf.path.name
        import re

        tokens: list[tuple[str, str]] = []
        inv_clean = re.sub(r"[^0-9A-Za-z]", "", invoice_id)
        if len(inv_clean) >= 6:
            tokens.append(("Rechnungsnummer (bereinigt)", inv_clean))
        if len(invoice_id) >= 6:
            tokens.append(("Rechnungsnummer", invoice_id))
        if issue_date:
            m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", issue_date)
            if m:
                y, mo, da = m.groups()
                tokens.append(("Datum kompakt", f"{y}{mo}{da}"))
                tokens.append(("Datum DE", f"{da}.{mo}.{y}"))
        matches_name: list[str] = []
        for label, tk in tokens:
            if tk and tk.upper() in pdf_name.upper():
                matches_name.append(f"{label} im Dateinamen: {tk!r}")
        matches_content: list[str] = []
        strong_tokens = [t[1] for t in tokens if len(t[1]) >= 6]
        if strong_tokens and not matches_name:
            try:
                from pypdf import PdfReader

                reader = PdfReader(str(candidate_pdf.path))
                haystack_parts: list[str] = []
                for page in reader.pages[:3]:
                    try:
                        t = page.extract_text() or ""
                        haystack_parts.append(t)
                    except Exception:
                        continue
                haystack = "\n".join(haystack_parts)
                if haystack.strip():
                    for label, tk in tokens:
                        if len(tk) >= 6 and tk in haystack:
                            matches_content.append(f"{label} im PDF-Inhalt: {tk!r}")
            except Exception:
                pass
        if matches_name:
            findings.extend(matches_name)
        if matches_content:
            findings.extend(matches_content)
        return findings or None

    def _try_promote_single_rbu_to_base_pdf(self) -> bool:
        standalone = self._get_standalone_xml()
        if standalone is None:
            return False
        rbus = self._get_rbu_documents()
        if len(rbus) != 1:
            return False
        candidate = rbus[0]
        if candidate.path.suffix.lower() not in {".pdf"}:
            return False
        findings = self._detect_invoice_pdf_reference_relationship(
            standalone.xml_bytes, candidate
        )
        if findings is None:
            QtWidgets.QMessageBox.information(
                self,
                "Kein Bezug herstellbar",
                "Das vorhandene PDF-Dokument wurde derzeit als 'RBU' (Anlage) klassifiziert. "
                "Ein Bezug zum E-Rechnungs-XML konnte anhand Dateiname oder PDF-Inhalt nicht nachgewiesen werden. "
                "Bitte ordnen Sie die Dateien manuell neu (z.B. über einen Dateinamen mit gleicher Rechnungsnummer).",
            )
            return False
        dialog = QtWidgets.QMessageBox(self)
        dialog.setIcon(QtWidgets.QMessageBox.Icon.Question)
        dialog.setWindowTitle("PDF-Dokument als Dokumenten-Basis übernehmen")
        dialog.setText(
            "Beide Dateien scheinen mit einander in Beziehung zu stehen.\n\n"
            "Befund:\n- " + "\n- ".join(findings[:6]) + "\n\n"
            "Soll das PDF-Dokument '"
            + candidate.path.name
            + "' als Grundlage für die E-Rechnungs-XML "
            "die neue Dokumenten-Basis für das Belegpaket werden?"
        )
        dialog.setInformativeText(
            "Bei 'Ja' wird die Rolle des PDF-Dokuments von 'RBU' auf 'Basis-PDF' umgestellt und "
            "der Merge-Vorgang kann unmittelbar starten."
        )
        btn_yes = dialog.addButton("Ja", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        dialog.addButton("Abbrechen", QtWidgets.QMessageBox.ButtonRole.RejectRole)
        dialog.exec()
        if dialog.clickedButton() is not btn_yes:
            return False
        candidate.kind = KIND_BASE_PDF
        self._telemetry.logger.info(
            "RBU → BASIS-PDF (Nutzer bestätigt): "
            + candidate.path.name
            + " | Hinweise: "
            + "; ".join(findings[:6])
        )
        self._refresh_document_table()
        return True

    def _recompute_bundle_annotations(self) -> None:
        for doc in self._documents:
            doc.bundle_role = BUNDLE_ROLE_UNKNOWN
            doc.bundle_group_key = None
            doc.bundle_validation = ()

        raw_entries: list[BundleFileEntry] = []
        for doc in self._documents:
            info = parse_bundle_filename(doc.path)
            if info.role == BUNDLE_ROLE_UNKNOWN and info.billing_token is None:
                continue
            sample_text = ""
            payload: Optional[InvoicePayload] = doc.payload
            if payload is None:
                try:
                    payload = extract_invoice_payload(doc.path)
                except Exception:
                    payload = None
            has_xml = payload is not None
            iid = None
            idate = None
            seller = None
            if payload is not None:
                try:
                    _, nc = parse_invoice_xml(payload.xml_bytes)
                    iid = nc.invoice_id
                    idate = nc.issue_date
                    seller = payload.seller.name or nc.seller_name
                except Exception:
                    pass
                if not seller:
                    seller = payload.seller.name
            try:
                from pypdf import PdfReader

                r = PdfReader(str(doc.path))
                if r.pages:
                    texts: list[str] = []
                    for pg in r.pages[:2]:
                        try:
                            texts.append(pg.extract_text() or "")
                        except Exception:
                            pass
                    sample_text = "\n".join(texts)
            except Exception:
                sample_text = ""
            bfe = build_bundle_entry(
                doc.path,
                pdf_sample_text=sample_text,
                has_embedded_xml=has_xml,
                invoice_id_from_xml=iid,
                issue_date_from_xml=idate,
                seller_name=seller,
            )
            raw_entries.append(bfe)

        bundles = detect_bundles(raw_entries) if raw_entries else []

        good: list[DetectedBundle] = []
        rejected: list[DetectedBundle] = []
        for b in bundles:
            if not b.roof_token:
                rejected.append(b)
                continue
            if b.summary_file is None and b.invoice_file is None:
                rejected.append(b)
                continue
            good.append(b)

        self._detected_bundles = good

        if not good:
            self._active_bundle_key = None
            return

        best = max(
            good,
            key=lambda b: (
                int(b.validation_ok),
                1 if b.summary_file is not None else 0,
                1 if b.invoice_file is not None else 0,
                len(b.files),
            ),
        )
        self._active_bundle_key = best.roof_token

        path_to_doc: dict[Path, DocumentEntry] = {}
        for doc in self._documents:
            path_to_doc[doc.path.resolve()] = doc

        for b in good:
            key = b.roof_token or ""
            for fe in b.files:
                try:
                    rp = fe.path.resolve()
                except Exception:
                    rp = fe.path
                doc = path_to_doc.get(rp)
                if doc is None:
                    continue
                doc.bundle_role = fe.info.role
                doc.bundle_group_key = key
                doc.bundle_validation = tuple(b.validation_issues)

                if b is best:
                    if fe.info.role == BUNDLE_ROLE_SUMMARY:
                        if doc.kind in (KIND_RBU, KIND_BASE_PDF):
                            doc.kind = KIND_BUNDLE_BASE
                    elif fe.info.role in (
                        BUNDLE_ROLE_INVOICE,
                        BUNDLE_ROLE_ATTACHMENT,
                        BUNDLE_ROLE_STATEMENT,
                    ):
                        if doc.kind not in (KIND_INVOICE, KIND_XML_DATASET):
                            doc.kind = KIND_BUNDLE_RBU

        self._reorder_documents_for_active_bundle()

    def _get_active_bundle(self) -> Optional[DetectedBundle]:
        if not self._active_bundle_key:
            return None
        for b in self._detected_bundles:
            if b.roof_token == self._active_bundle_key:
                return b
        return None

    def _reorder_documents_for_active_bundle(self) -> None:
        bundle = self._get_active_bundle()
        if bundle is None:
            return
        order_paths: list[Path] = []
        if bundle.summary_file is not None:
            order_paths.append(bundle.summary_file.path)
        for rbu in bundle.ordered_rbu_entries():
            order_paths.append(rbu.path)
        path_rank: dict[Path, int] = {}
        for idx, p in enumerate(order_paths):
            try:
                path_rank[p.resolve()] = idx
            except Exception:
                path_rank[p] = idx
        bundle_keys: set[str] = set()
        for d in self._documents:
            if d.bundle_group_key:
                bundle_keys.add(d.bundle_group_key)
        if not bundle_keys:
            return

        def _rank(doc: DocumentEntry) -> tuple[int, int, int]:
            try:
                rp = doc.path.resolve()
            except Exception:
                rp = doc.path
            if rp in path_rank:
                return (0, path_rank[rp], 0)
            if doc.bundle_group_key == self._active_bundle_key:
                return (0, 999, 0)
            return (1, 0, 0)

        self._documents.sort(key=_rank)

    def _set_checklist(self, result: ComplianceResult) -> None:
        if result.error:
            self._chkline_name.setText("Firmenname & Rechtsform -> FEHLT")
            self._chkline_addr.setText("Anschrift / Adresse -> FEHLT")
            self._chkline_tax.setText("USt-IdNr. / Steuernummer -> FEHLT")
            return

        self._chkline_name.setText(
            "Firmenname & Rechtsform -> "
            + ("GEFUNDEN" if result.found_name else "FEHLT")
        )
        self._chkline_addr.setText(
            "Anschrift / Adresse -> "
            + ("GEFUNDEN" if result.found_address else "FEHLT")
        )
        self._chkline_tax.setText(
            "USt-IdNr. / Steuernummer -> "
            + ("GEFUNDEN" if result.found_tax_id else "FEHLT")
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

            current_jar = (
                self._deps.selection.mustang_jar.name
                if self._deps.selection.mustang_jar
                else None
            )
            current_jre = (
                self._deps.selection.jre_dir.name
                if self._deps.selection.jre_dir
                else None
            )

            self._cmb_mustang.clear()
            if jars:
                for item in jars:
                    self._cmb_mustang.addItem(item.name)
                if current_jar and current_jar in [p.name for p in jars]:
                    self._cmb_mustang.setCurrentText(current_jar)
                else:
                    self._cmb_mustang.setCurrentIndex(len(jars) - 1)
                    self._deps.set_selected_mustang_by_name(
                        self._cmb_mustang.currentText()
                    )
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
        jar = (
            self._deps.selection.mustang_jar.name
            if self._deps.selection.mustang_jar
            else "(keine)"
        )
        jre = (
            self._deps.selection.jre_dir.name
            if self._deps.selection.jre_dir
            else "(keine)"
        )
        self._telemetry.logger.info(f"Aktive Auswahl: Mustang={jar} | JRE={jre}")

    def _cleanup_packages(self) -> None:
        self._telemetry.logger.info("Pakete bereinigen: Start.")
        self._deps.cleanup_unused(
            self._deps.selection.mustang_jar, self._deps.selection.jre_dir
        )

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
        accept = dialog.addButton(
            "Ich akzeptiere das Risiko", QtWidgets.QMessageBox.ButtonRole.AcceptRole
        )
        dialog.addButton("Abbrechen", QtWidgets.QMessageBox.ButtonRole.RejectRole)
        dialog.exec()

        if dialog.clickedButton() is accept:
            self._settings.set_ustg_check_enabled(False)
            self._telemetry.logger.info(
                "Compliance-Check §14 UStG deaktiviert (Risiko akzeptiert)."
            )
            return

        self._populating_settings = True
        try:
            self._chk_ustg.setChecked(True)
        finally:
            self._populating_settings = False

    def _choose_import_dir(self) -> None:
        start = str(self._suggest_import_dir())
        directory = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Import-Pfad wählen", start
        )
        if not directory:
            return
        self._settings.set_import_dir_from_absolute(Path(directory))
        self._import_dir_line.setText(directory)

    def _choose_export_dir(self) -> None:
        start = str(self._suggest_export_dir())
        directory = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Export-Pfad wählen", start
        )
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
            "Unterstützte Dateien (*.pdf *.xml *.png *.jpg *.jpeg *.doc *.docx *.xls *.xlsx);;"
            "PDF & E-Rechnung XML (*.pdf *.xml);;"
            "Alle Dateien (*)",
        )
        if file_paths:
            self._on_files_dropped(file_paths)

    def _on_files_dropped(self, paths: Iterable[str]) -> None:
        files = [Path(p) for p in paths]
        for path in files:
            self._telemetry.logger.info(f"Datei erhalten: {path}")
            if path.parent.exists():
                self._last_input_dir = path.parent

        office = [
            p for p in files if p.suffix.lower() in {".doc", ".docx", ".xls", ".xlsx"}
        ]
        if office:
            QtWidgets.QMessageBox.warning(
                self,
                "Format-Hinweis",
                "Bitte vorab als PDF/Bild speichern, um Formatierungsfehler zu vermeiden.",
            )
            for path in office:
                self._telemetry.logger.info(
                    "Bitte vorab als PDF/Bild speichern, um Formatierungsfehler zu vermeiden: "
                    + path.name
                )

        valid = [
            p
            for p in files
            if p.suffix.lower() in {".pdf", ".xml", ".png", ".jpg", ".jpeg"}
        ]
        invalid = [p for p in files if p not in office and p not in valid]
        for path in invalid:
            self._telemetry.logger.info(
                "Datei ignoriert (Format nicht unterstützt): " + path.name
            )

        second_invoice_hits: list[str] = []
        xml_errors: list[str] = []
        for path in valid:
            if not path.exists():
                continue

            if path.suffix.lower() == ".xml":
                self._handle_xml_drop(path, xml_errors, second_invoice_hits)
                continue

            if path.suffix.lower() != ".pdf":
                self._documents.append(DocumentEntry(path=path, kind=KIND_RBU))
                self._telemetry.logger.info(f"Anlage hinzugefügt (Bild): {path.name}")
                continue

            payload = self._try_extract_invoice_payload(path)
            if payload is not None:
                try:
                    from zug2ferd.core.xml_normcheck import parse_invoice_xml

                    _, nc = parse_invoice_xml(payload.xml_bytes)
                    norm_ok = nc.ok
                    norm_details = nc
                except Exception:
                    norm_ok = None
                    norm_details = None
                pdfa3_ok, pdfa3_issues = self._check_pdfa3_compliance(path)
                if norm_ok is not True:
                    self._telemetry.logger.info(
                        f"E-Rechnung importiert, aber NICHT normkonform: {path.name} | "
                        + (
                            "missing=" + ",".join(norm_details.missing_required)
                            if norm_details
                            else "pruefung_nicht_moeglich"
                        )
                    )
                else:
                    self._telemetry.logger.info(
                        f"E-Rechnung importiert, normkonform: {path.name} | pdfa3={'ok' if pdfa3_ok else ('fehlt: ' + '; '.join(pdfa3_issues))}"
                    )
                existing_pdf_inv = self._get_invoice_document()
                if existing_pdf_inv is not None:
                    resolved = False
                    try:
                        entry = StandaloneXmlEntry(
                            xml_bytes=payload.xml_bytes,
                            normcheck=norm_details,
                            seller=parse_seller_data(payload.xml_bytes),
                        )
                        if self._resolve_duplicate_invoice_choice(
                            existing_pdf_inv, path, entry
                        ):
                            resolved = True
                    except Exception:
                        resolved = False
                    if not resolved:
                        second_invoice_hits.append(path.name)
                    continue
                existing_xml = self._get_standalone_xml()
                if existing_xml is not None:
                    if self._try_resolve_conflict_pdf_when_xml_exists(path, payload):
                        continue
                    second_invoice_hits.append(path.name)
                    continue
                if not self._accept_invoice_document(path, payload):
                    continue
                self._documents.insert(
                    0,
                    DocumentEntry(
                        path=path,
                        kind=KIND_INVOICE,
                        payload=payload,
                        norm_ok=norm_ok,
                        norm_details=norm_details,
                        pdfa3_ok=pdfa3_ok,
                        pdfa3_issues=pdfa3_issues,
                    ),
                )
                self._telemetry.logger.info(f"Hauptrechnung importiert: {path.name}")
                self._start_compliance_recheck()
                continue

            if (
                self._get_invoice_document() is not None
                or self._get_standalone_xml() is not None
            ):
                self._documents.append(DocumentEntry(path=path, kind=KIND_RBU))
                self._telemetry.logger.info(f"Anlage hinzugefügt (PDF): {path.name}")
            else:
                if self._get_base_pdf() is not None:
                    self._documents.append(DocumentEntry(path=path, kind=KIND_RBU))
                    self._telemetry.logger.info(
                        f"Anlage hinzugefügt (PDF): {path.name}"
                    )
                else:
                    self._documents.insert(
                        0, DocumentEntry(path=path, kind=KIND_BASE_PDF)
                    )
                    self._telemetry.logger.info(
                        f"Basis-PDF für PDF+XML-Kombination erkannt: {path.name}"
                    )

        if second_invoice_hits:
            QtWidgets.QMessageBox.warning(
                self,
                "Weitere E-Rechnung abgewiesen",
                "Es ist bereits eine E-Rechnung (entweder als ZUGFeRD-PDF oder als separates XML) Bestandteil des "
                "aktuellen Belegpakets. Eine Zusammenführung ist nur mit genau einer einzelnen E-Rechnung zulässig. "
                "Folgende Dateien wurden nicht in das Belegpaket übernommen:\n- "
                + "\n- ".join(second_invoice_hits),
            )

        if xml_errors:
            QtWidgets.QMessageBox.warning(
                self,
                "XML nicht EN 16931 konform",
                "Folgende XML-Dateien wurden nicht als gültige EN 16931 E-Rechnung erkannt:\n- "
                + "\n- ".join(xml_errors)
                + "\n\nBitte verwenden Sie ein gültiges XRechnung-, Factur-X- oder ZUGFeRD-XML.",
            )

        self._recompute_bundle_annotations()
        self._refresh_document_table()

    def _handle_xml_drop(
        self, path: Path, xml_errors: list[str], second_invoice_hits: list[str]
    ) -> None:
        try:
            check = check_xml_file(path)
        except Exception as e:
            self._telemetry.logger.info(f"XML unlesbar: {path.name} | {e}")
            xml_errors.append(f"{path.name} (Lesefehler)")
            return

        norm_ok = bool(check.ok)

        try:
            xml_bytes = path.read_bytes()
            seller = parse_seller_data(xml_bytes)
            normalized = normalize_invoice_data(xml_bytes)
            if normalized.seller_tax_ids:
                merged_ids = list(seller.tax_ids)
                for tid in normalized.seller_tax_ids:
                    if tid not in merged_ids:
                        merged_ids.append(tid)
                seller = SellerData(
                    name=seller.name or normalized.seller_name,
                    address=seller.address,
                    tax_ids=tuple(merged_ids),
                )
            entry = StandaloneXmlEntry(
                xml_bytes=xml_bytes, normcheck=check, seller=seller
            )
        except Exception as exc:
            self._telemetry.logger.info(
                f"XML-Verarbeitung fehlgeschlagen: {path.name} | {exc}"
            )
            xml_errors.append(f"{path.name} (Verarbeitungsfehler)")
            return

        existing_pdf_inv = self._get_invoice_document()
        if existing_pdf_inv is not None:
            if self._resolve_duplicate_invoice_choice(existing_pdf_inv, path, entry):
                return
            second_invoice_hits.append(path.name)
            self._telemetry.logger.info(
                "E-Rechnungs-XML abgewiesen (Duplikat): bereits eine ZUGFeRD-PDF im Belegpaket enthalten: "
                + path.name
            )
            return

        existing_xml_doc: Optional[DocumentEntry] = None
        for d in self._documents:
            if d.kind == KIND_XML_DATASET and d.xml_entry is not None:
                existing_xml_doc = d
                break
        if existing_xml_doc is not None and existing_xml_doc.xml_entry is not None:
            identical = False
            try:
                if existing_xml_doc.xml_entry.xml_bytes == entry.xml_bytes:
                    identical = True
                else:
                    na = normalize_invoice_data(existing_xml_doc.xml_entry.xml_bytes)
                    nb = normalize_invoice_data(entry.xml_bytes)
                    identical = (
                        na.invoice_id == nb.invoice_id
                        and na.issue_date == nb.issue_date
                        and na.payable_amount == nb.payable_amount
                        and na.seller_name == nb.seller_name
                        and na.buyer_name == nb.buyer_name
                    )
            except Exception:
                identical = False
            dialog = QtWidgets.QMessageBox(self)
            dialog.setWindowTitle("Zweites E-Rechnungs-XML gefunden")
            if identical:
                dialog.setIcon(QtWidgets.QMessageBox.Icon.Information)
                dialog.setText(
                    "Das E-Rechnungs-XML '"
                    + path.name
                    + "' ist inhaltlich 100 % identisch mit dem bereits geladenen E-Rechnungs-XML '"
                    + existing_xml_doc.path.name
                    + "'.\n\n"
                    "Es darf nur genau ein E-Rechnungs-Datensatz verarbeitet werden. Bitte entscheiden Sie, welche Datei Sie behalten möchten. "
                    "Die jeweils andere Datei wird aus dem Belegpaket entfernt."
                )
            else:
                dialog.setIcon(QtWidgets.QMessageBox.Icon.Warning)
                dialog.setText(
                    "Im Belegpaket ist bereits ein E-Rechnungs-XML '"
                    + existing_xml_doc.path.name
                    + "' geladen. Das neue XML '"
                    + path.name
                    + "' enthält einen weiteren Datensatz – die beiden Datensätze weichen in einzelnen Feldern voneinander ab.\n\n"
                    "Es darf nur genau ein E-Rechnungs-Datensatz verarbeitet werden. Bitte entscheiden Sie, welche Datei Sie behalten möchten. "
                    "Die jeweils andere Datei wird aus dem Belegpaket entfernt."
                )
            btn_keep_existing = dialog.addButton(
                "Bereits geladenes XML behalten",
                QtWidgets.QMessageBox.ButtonRole.ActionRole,
            )
            btn_keep_new = dialog.addButton(
                "Neues XML behalten", QtWidgets.QMessageBox.ButtonRole.AcceptRole
            )
            dialog.addButton("Abbrechen", QtWidgets.QMessageBox.ButtonRole.RejectRole)
            dialog.exec()
            clicked = dialog.clickedButton()
            if clicked is btn_keep_new:
                self._documents = [
                    d for d in self._documents if d.path != existing_xml_doc.path
                ]
                if not self._accept_seller_branding(path, seller):
                    return
                self._documents.append(
                    DocumentEntry(
                        path=path,
                        kind=KIND_XML_DATASET,
                        xml_entry=entry,
                        norm_ok=norm_ok,
                        norm_details=check,
                    )
                )
                self._telemetry.logger.info(
                    f"ZWEITES_XML | Neues behalten: {path.name} | entfernt: {existing_xml_doc.path.name}"
                )
                self._start_assemble_compliance_recheck()
                return
            if clicked is btn_keep_existing:
                self._telemetry.logger.info(
                    f"ZWEITES_XML | Vorhandenes behalten: {existing_xml_doc.path.name} | ignoriert: {path.name}"
                )
                return
            second_invoice_hits.append(path.name)
            return

        if not check.ok:
            missing = ", ".join(check.missing_required)
            self._telemetry.logger.info(
                f"XML nicht EN 16931 konform, aber mit Warnung übernommen: {path.name} | missing={missing}"
            )

        if not self._accept_seller_branding(path, seller):
            return
        self._documents.append(
            DocumentEntry(
                path=path,
                kind=KIND_XML_DATASET,
                xml_entry=entry,
                norm_ok=norm_ok,
                norm_details=check,
            )
        )
        if norm_ok:
            self._telemetry.logger.info(
                f"E-Rechnungs-XML akzeptiert: {path.name} | Dialekt={check.dialect} | Customization={check.customization_id}"
            )
        else:
            self._telemetry.logger.info(
                f"E-Rechnungs-XML mit Normwarnungen geladen: {path.name} | missing={','.join(check.missing_required)}"
            )
        self._start_assemble_compliance_recheck()

    def _accept_seller_branding(self, source_path: Path, seller: SellerData) -> bool:
        seller_name = seller.name
        primary_tax = choose_primary_tax_id(seller.tax_ids)

        if not self._settings.has_branding() and seller_name and primary_tax:
            self._settings.set_branding(seller_name, primary_tax)
            self._telemetry.logger.info(
                f"User-Branding erfolgreich für {seller_name} hinterlegt."
            )

        if self._settings.has_branding() and not self._settings.matches_branding(
            seller_name, seller.tax_ids
        ):
            self._status.set_state("error")
            self._telemetry.logger.info(
                "MANDANTEN_SPERRE | file="
                + source_path.name
                + " | reason=branding_mismatch"
            )
            QtWidgets.QMessageBox.critical(
                self,
                "Warnung",
                "Warnung: Abweichender Rechnungsaussteller erkannt! Dieses Tool ist fest auf Ihr Unternehmensprofil "
                "geprägt. Verarbeitung abgebrochen.",
            )
            return False
        return True

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
            self._telemetry.logger.info(
                f"User-Branding erfolgreich für {seller_name} hinterlegt."
            )

        if self._settings.has_branding() and not self._settings.matches_branding(
            seller_name, payload.seller.tax_ids
        ):
            self._status.set_state("error")
            self._telemetry.logger.info(
                "MANDANTEN_SPERRE | file="
                + pdf_path.name
                + " | reason=branding_mismatch"
            )
            QtWidgets.QMessageBox.critical(
                self,
                "Warnung",
                "Warnung: Abweichender Rechnungsaussteller erkannt! Dieses Tool ist fest auf Ihr Unternehmensprofil "
                "geprägt. Verarbeitung abgebrochen.",
            )
            return False

        return True

    def _check_pdfa3_compliance(self, pdf_path: Path) -> tuple[bool, tuple[str, ...]]:
        issues: list[str] = []
        try:
            from pypdf import PdfReader
        except Exception:
            return True, tuple()
        try:
            reader = PdfReader(str(pdf_path))
            has_pdfa3 = False
            has_facturx_fx = False
            has_af = False
            metadata_ref = (
                reader.trailer.get("/Root", {}).get("/Metadata")
                if hasattr(reader.trailer, "get")
                else None
            )
            if metadata_ref is not None and hasattr(metadata_ref, "get_data"):
                try:
                    xmp_text = metadata_ref.get_data().decode("utf-8", errors="replace")
                    if (
                        "pdfaid:part>3" in xmp_text
                        or "pdfaid:part>3<" in xmp_text
                        or "<pdfaid:part>3</pdfaid:part>" in xmp_text
                    ):
                        has_pdfa3 = True
                    else:
                        import re

                        m = re.search(
                            r"<pdfaid:part[^>]*>\s*(\d+)\s*</pdfaid:part>", xmp_text
                        )
                        if m and m.group(1) == "3":
                            has_pdfa3 = True
                    if (
                        "factur-x:pdfa:CrossIndustryDocument" in xmp_text
                        or "fx:ConformanceLevel" in xmp_text
                    ):
                        has_facturx_fx = True
                except Exception:
                    pass
            root = (
                reader.trailer.get("/Root") if hasattr(reader.trailer, "get") else None
            )
            if root is not None and hasattr(root, "get"):
                af = root.get("/AF")
                if isinstance(af, list) or hasattr(af, "__len__"):
                    try:
                        if len(af) > 0:
                            has_af = True
                    except Exception:
                        has_af = False
        except Exception as e:
            issues.append(f"PDF-Prüfung fehlgeschlagen: {e}")
            return False, tuple(issues)
        if not has_pdfa3:
            issues.append("PDF/A-3-Level (pdfaid:part=3) nicht im XMP nachgewiesen")
        if not has_facturx_fx:
            issues.append("Factur-X-Schema im XMP nicht vorhanden")
        if not has_af:
            issues.append("Kein Associated-Files (/AF) Array am PDF-Root vorhanden")
        ok = len(issues) == 0
        return ok, tuple(issues)

    def _extract_invoice_keys_from_entry(
        self, doc: DocumentEntry
    ) -> Optional[NormalizedInvoiceData]:
        xml_bytes: Optional[bytes] = None
        if doc.kind == KIND_INVOICE and doc.payload is not None:
            xml_bytes = doc.payload.xml_bytes
        elif doc.kind == KIND_XML_DATASET and doc.xml_entry is not None:
            xml_bytes = doc.xml_entry.xml_bytes
        if xml_bytes is None:
            return None
        try:
            return normalize_invoice_data(xml_bytes)
        except Exception:
            return None

    def _has_pdf_text_search(self, pdf_path: Path, needles: list[str]) -> bool:
        if not needles:
            return False
        try:
            from pypdf import PdfReader
        except Exception:
            return False
        try:
            reader = PdfReader(str(pdf_path))
            if not reader.pages:
                return False
            sample_text_parts: list[str] = []
            for page in reader.pages[:3]:
                try:
                    t = page.extract_text() or ""
                    sample_text_parts.append(t)
                except Exception:
                    continue
            haystack = "\n".join(sample_text_parts)
            if not haystack.strip():
                return False
            for nd in needles:
                if len(nd) < 6:
                    continue
                if nd in haystack:
                    return True
            return False
        except Exception:
            return False

    def _evaluate_consistency_flags(self) -> None:
        invoice_docs: list[DocumentEntry] = []
        base_pdf_docs: list[DocumentEntry] = []
        for d in self._documents:
            d.consistency_ok = None
            d.consistency_details = ()
            if d.kind in (KIND_INVOICE, KIND_XML_DATASET):
                invoice_docs.append(d)
            elif d.kind == KIND_BASE_PDF:
                base_pdf_docs.append(d)
        if not invoice_docs or not base_pdf_docs:
            return
        for inv in invoice_docs:
            keys = self._extract_invoice_keys_from_entry(inv)
            if keys is None:
                continue
            invoice_id = (keys.invoice_id or "").strip()
            issue_date = (keys.issue_date or "").strip()
            strong_tokens: list[str] = []
            weak_tokens: list[str] = []
            import re

            invoice_id_clean = re.sub(r"[^0-9A-Za-z]", "", invoice_id)
            if len(invoice_id_clean) >= 6:
                strong_tokens.append(invoice_id_clean)
            if invoice_id and len(invoice_id) >= 6:
                strong_tokens.append(invoice_id)
            date_variants: list[str] = []
            if issue_date:
                m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", issue_date)
                if m:
                    y, mo, da = m.groups()
                    date_variants.append(f"{y}{mo}{da}")
                    date_variants.append(f"{da}.{mo}.{y}")
                    date_variants.append(f"{y}-{mo}-{da}")
            for v in date_variants:
                if len(v) >= 8 and v not in weak_tokens:
                    weak_tokens.append(v)
            matched_any_base = False
            all_details: list[str] = []
            for base_pdf in base_pdf_docs:
                pdf_name_upper = base_pdf.path.name.upper()
                found_strong = False
                found_weak = False
                for tk in strong_tokens:
                    if tk and tk.upper() in pdf_name_upper:
                        found_strong = True
                        break
                if not found_strong and strong_tokens:
                    found_via_text = self._has_pdf_text_search(
                        base_pdf.path, strong_tokens
                    )
                    if found_via_text:
                        found_strong = True
                if not found_strong and weak_tokens:
                    for tk in weak_tokens:
                        if tk and tk.upper() in pdf_name_upper:
                            found_weak = True
                            break
                    if not found_weak and weak_tokens:
                        found_via_text_wk = self._has_pdf_text_search(
                            base_pdf.path, weak_tokens
                        )
                        if found_via_text_wk:
                            found_weak = True
                matched = found_strong  # NUR starker Match zählt!
                if matched:
                    matched_any_base = True
                    matched_tokens = [
                        tk
                        for tk in strong_tokens
                        if tk and (tk.upper() in pdf_name_upper)
                    ]
                    reason = (
                        "Rechnungsnummer im Dateinamen"
                        if matched_tokens
                        else "Rechnungsnummer im PDF-Inhalt"
                    )
                    if matched_tokens:
                        all_details.append(
                            f"Übereinstimmung mit '{base_pdf.path.name}' "
                            f"({reason}: {', '.join(matched_tokens[:4])})"
                        )
                    else:
                        all_details.append(
                            f"Übereinstimmung mit '{base_pdf.path.name}' "
                            f"({reason}): Rechnungsnummer {invoice_id!r}"
                        )
                else:
                    if found_weak:
                        all_details.append(
                            f"Kein sicherer Bezug zu '{base_pdf.path.name}': "
                            f"Rechnungsnummer {invoice_id!r} nicht gefunden (nur Datums-Hinweise im Text)."
                        )
                    else:
                        all_details.append(
                            f"Kein Bezug zu '{base_pdf.path.name}': "
                            f"Rechnungsnummer {invoice_id!r} weder in Dateiname noch PDF-Inhalt auffindbar."
                        )
            if matched_any_base:
                inv.consistency_ok = True
                inv.consistency_details = tuple(all_details)
                for bp in base_pdf_docs:
                    bp.consistency_ok = True
            else:
                inv.consistency_ok = False
                inv.consistency_details = tuple(all_details)
                for bp in base_pdf_docs:
                    bp.consistency_ok = False
                    bp.consistency_details = (
                        f"Kein Bezug zum Datensatz: Rechnungsnummer {invoice_id!r} konnte im Dateinamen oder Inhalt dieses PDFs nicht verifiziert werden.",
                    )
                    break

    def _invoice_datasets_identical(
        self, payload_a: InvoicePayload, standalone_b: StandaloneXmlEntry
    ) -> bool:
        a = payload_a.xml_bytes
        b = standalone_b.xml_bytes
        if a == b:
            return True
        try:
            import hashlib

            na = normalize_invoice_data(a)
            nb = normalize_invoice_data(b)
            return (
                na.invoice_id == nb.invoice_id
                and na.issue_date == nb.issue_date
                and na.due_date == nb.due_date
                and na.seller_name == nb.seller_name
                and na.buyer_name == nb.buyer_name
                and na.payable_amount == nb.payable_amount
                and na.total_amount == nb.total_amount
                and na.taxable_amount == nb.taxable_amount
                and na.vat_amount == nb.vat_amount
                and na.currency == nb.currency
            )
        except Exception:
            return hashlib.md5(a).digest() == hashlib.md5(b).digest()

    def _resolve_duplicate_invoice_choice(
        self, pdf_entry: DocumentEntry, xml_path: Path, xml_entry: StandaloneXmlEntry
    ) -> bool:
        identical = False
        if pdf_entry.payload is not None:
            identical = self._invoice_datasets_identical(pdf_entry.payload, xml_entry)
        dialog = QtWidgets.QMessageBox(self)
        dialog.setWindowTitle("Doppelte E-Rechnung erkannt")
        if identical:
            dialog.setIcon(QtWidgets.QMessageBox.Icon.Information)
            dialog.setText(
                "Die E-Rechnung in der Datei '"
                + xml_path.name
                + "' ist inhaltlich 100 % identisch mit der bereits in '"
                + pdf_entry.path.name
                + "' enthaltenen E-Rechnung.\n\n"
                "Bitte entscheiden Sie, welche der beiden Dateien Sie im Belegpaket behalten möchten. "
                "Die jeweils andere Datei wird aus dem Belegpaket entfernt."
            )
        else:
            dialog.setIcon(QtWidgets.QMessageBox.Icon.Warning)
            dialog.setText(
                "Im aktuellen Belegpaket ist bereits eine E-Rechnung (PDF: '"
                + pdf_entry.path.name
                + "') enthalten. Die Datei '"
                + xml_path.name
                + "' enthält einen weiteren E-Rechnungs-Datensatz – die beiden Datensätze weichen jedoch in einzelnen Feldern voneinander ab.\n\n"
                "Nur genau EIN E-Rechnungs-Datensatz darf im Ergebnis enthalten sein. Bitte entscheiden Sie, welche der beiden Dateien Sie behalten möchten. "
                "Die jeweils andere Datei wird aus dem Belegpaket entfernt."
            )
        btn_pdf = dialog.addButton(
            "PDF behalten", QtWidgets.QMessageBox.ButtonRole.ActionRole
        )
        btn_xml = dialog.addButton(
            "XML behalten", QtWidgets.QMessageBox.ButtonRole.AcceptRole
        )
        dialog.addButton("Abbrechen", QtWidgets.QMessageBox.ButtonRole.RejectRole)
        dialog.exec()
        clicked = dialog.clickedButton()
        if clicked is btn_xml:
            self._documents = [d for d in self._documents if d.path != pdf_entry.path]
            seller = xml_entry.seller
            if not self._accept_seller_branding(xml_path, seller):
                return False
            self._documents.append(
                DocumentEntry(
                    path=xml_path,
                    kind=KIND_XML_DATASET,
                    xml_entry=xml_entry,
                    norm_ok=xml_entry.normcheck.ok,
                    norm_details=xml_entry.normcheck,
                )
            )
            self._telemetry.logger.info(
                f"DOPPELTE_ERECHNUNG | XML behalten: {xml_path.name} | entfernt: {pdf_entry.path.name}"
            )
            return True
        if clicked is btn_pdf:
            self._telemetry.logger.info(
                f"DOPPELTE_ERECHNUNG | PDF behalten: {pdf_entry.path.name} | ignoriert: {xml_path.name}"
            )
            return True
        return False

    def _try_resolve_conflict_pdf_when_xml_exists(
        self, pdf_path: Path, payload: InvoicePayload
    ) -> bool:
        existing = self._get_standalone_xml()
        if existing is None:
            return False
        identical = self._invoice_datasets_identical(payload, existing)
        existing_doc: Optional[DocumentEntry] = None
        for d in self._documents:
            if d.xml_entry is not None and d.xml_entry is existing:
                existing_doc = d
                break
        if existing_doc is None:
            return False
        dialog = QtWidgets.QMessageBox(self)
        dialog.setWindowTitle("Doppelte E-Rechnung erkannt")
        if identical:
            dialog.setIcon(QtWidgets.QMessageBox.Icon.Information)
            dialog.setText(
                "Die E-Rechnung in der PDF-Datei '"
                + pdf_path.name
                + "' ist inhaltlich 100 % identisch mit dem bereits geladenen separaten E-Rechnungs-XML '"
                + existing_doc.path.name
                + "'.\n\n"
                "Bitte entscheiden Sie, welche der beiden Dateien Sie im Belegpaket behalten möchten. "
                "Die jeweils andere Datei wird aus dem Belegpaket entfernt."
            )
        else:
            dialog.setIcon(QtWidgets.QMessageBox.Icon.Warning)
            dialog.setText(
                "Im Belegpaket ist bereits ein separates E-Rechnungs-XML '"
                + existing_doc.path.name
                + "' geladen. Die Datei '"
                + pdf_path.name
                + "' enthält einen weiteren E-Rechnungs-Datensatz – beide Datensätze weichen in einzelnen Feldern voneinander ab.\n\n"
                "Nur genau EIN E-Rechnungs-Datensatz darf im Ergebnis enthalten sein. Bitte entscheiden Sie, welche der beiden Dateien Sie behalten möchten. "
                "Die jeweils andere Datei wird aus dem Belegpaket entfernt."
            )
        btn_pdf = dialog.addButton(
            "PDF behalten", QtWidgets.QMessageBox.ButtonRole.AcceptRole
        )
        btn_xml = dialog.addButton(
            "XML behalten", QtWidgets.QMessageBox.ButtonRole.ActionRole
        )
        dialog.addButton("Abbrechen", QtWidgets.QMessageBox.ButtonRole.RejectRole)
        dialog.exec()
        clicked = dialog.clickedButton()
        if clicked is btn_pdf:
            self._documents = [
                d for d in self._documents if d.path != existing_doc.path
            ]
            if not self._accept_invoice_document(pdf_path, payload):
                return False
            try:
                from zug2ferd.core.xml_normcheck import parse_invoice_xml

                _, nc = parse_invoice_xml(payload.xml_bytes)
                norm_ok = nc.ok
                norm_details = nc
            except Exception:
                norm_ok = None
                norm_details = None
            pdfa3_ok, pdfa3_issues = self._check_pdfa3_compliance(pdf_path)
            self._documents.insert(
                0,
                DocumentEntry(
                    path=pdf_path,
                    kind=KIND_INVOICE,
                    payload=payload,
                    norm_ok=norm_ok,
                    norm_details=norm_details,
                    pdfa3_ok=pdfa3_ok,
                    pdfa3_issues=pdfa3_issues,
                ),
            )
            self._telemetry.logger.info(
                f"DOPPELTE_ERECHNUNG | PDF behalten: {pdf_path.name} | entfernt: {existing_doc.path.name}"
            )
            self._start_compliance_recheck()
            return True
        if clicked is btn_xml:
            self._telemetry.logger.info(
                f"DOPPELTE_ERECHNUNG | XML behalten: {existing_doc.path.name} | ignoriert: {pdf_path.name}"
            )
            return True
        return False

    def _on_rows_reordered(self, source_row: int, target_row: int) -> None:
        if not self._documents:
            return
        if source_row < 0 or source_row >= len(self._documents):
            return

        target_index = max(0, min(target_row, len(self._documents)))
        if target_index > source_row:
            target_index -= 1
        if target_index == source_row:
            self._refresh_document_table()
            return

        moved = self._documents.pop(source_row)
        self._documents.insert(target_index, moved)
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
            _ComplianceRunnable(
                self._compliance_bus, invoice.path, invoice.payload, stationery, enabled
            )
        )

    def _start_assemble_compliance_recheck(self) -> None:
        standalone = self._get_standalone_xml()
        base_pdf = self._get_base_pdf()
        if standalone is None or base_pdf is None:
            self._status.set_state("neutral")
            return
        self._last_bypass_missing = None
        stationery = self._settings.resolve_stationery_path()
        enabled = bool(self._settings.settings.ustg_check_enabled)
        payload = InvoicePayload(
            xml_bytes=standalone.xml_bytes, seller=standalone.seller
        )
        QtCore.QThreadPool.globalInstance().start(
            _ComplianceRunnable(
                self._compliance_bus, base_pdf.path, payload, stationery, enabled
            )
        )

    @(
        QtCore.pyqtSlot(object, object)
        if hasattr(QtCore, "pyqtSlot")
        else QtCore.Slot(object, object)
    )
    def _on_compliance_result(self, result: object, invoice_pdf: object) -> None:
        if not isinstance(result, ComplianceResult) or not isinstance(
            invoice_pdf, Path
        ):
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
        critical = [
            item
            for item in result.missing_fields
            if item in {"Firmenname", "USt-IdNr./Steuernummer"}
        ]
        if not critical:
            self._status.set_state("warn")
            return
        self._open_softblock(invoice_pdf, result.missing_fields)

    def _open_softblock(
        self, invoice_pdf: Path, missing_fields: tuple[str, ...]
    ) -> None:
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
            "Briefbogen (PDF) jetzt hinterlegen",
            QtWidgets.QMessageBox.ButtonRole.AcceptRole,
        )
        dialog.addButton(
            "Ignorieren und fortfahren (Bypass)",
            QtWidgets.QMessageBox.ButtonRole.DestructiveRole,
        )
        dialog.exec()
        if dialog.clickedButton() is button_stationery:
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

    def _on_stationery_dropped(self, path: str) -> None:
        self._set_stationery_from_path(Path(path))

    def _choose_stationery(self) -> None:
        start = str(self._suggest_import_dir())
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Briefbogen auswählen", start, "PDF (*.pdf)"
        )
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
            self._telemetry.logger.info(
                "Briefbogen konnte nicht gespeichert werden: " + str(exc)
            )
            return
        rel = dest.relative_to(self._paths.runtime_dir).as_posix()
        self._settings.set_stationery_rel(rel)
        self._stationery_line.setText(dest.name)
        self._telemetry.logger.info(f"Briefbogen gesetzt: {dest.name}")
        if self._build_mode() == "assemble_mode":
            self._start_assemble_compliance_recheck()
        else:
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
        base_pdf = self._get_base_pdf()
        return (
            self._settings.resolve_export_dir()
            or self._last_output_dir
            or (invoice.path.parent if invoice is not None else None)
            or (base_pdf.path.parent if base_pdf is not None else None)
            or self._last_input_dir
            or self._settings.resolve_import_dir()
            or Path.cwd()
        )

    def _build_export_filename(self) -> str:
        bundle = self._get_active_bundle()
        if bundle is not None and bundle.roof_token:
            suffix = "GESAMT"
            if bundle.customer_no:
                return f"{bundle.customer_no}_{bundle.roof_token}_{suffix}.pdf"
            return f"{bundle.roof_token}_{suffix}.pdf"

        invoice = self._get_invoice_document()
        base_pdf = self._get_base_pdf()
        standalone_xml = self._get_standalone_xml()
        first_doc = self._documents[0] if self._documents else None

        rbu_count = 0
        for d in self._documents:
            dt = getattr(d, "doc_type", None)
            if isinstance(dt, str) and dt.startswith("RBU"):
                rbu_count += 1
            if dt == "RBU_DOCUMENT":
                rbu_count += 1

        if standalone_xml is not None and base_pdf is not None:
            return base_pdf.path.stem + ".pdf"

        if invoice is None:
            if rbu_count == 0 and first_doc is not None:
                return first_doc.path.stem + ".pdf"
            return "belegpaket_inkl_RBU.pdf"

        if rbu_count == 0:
            if first_doc is None or first_doc.path == invoice.path:
                return invoice.path.stem + ".pdf"
            return first_doc.path.stem + ".pdf"

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
        dialog.addButton(
            "Original-E-Rechnung verwenden", QtWidgets.QMessageBox.ButtonRole.AcceptRole
        )
        btn_first = dialog.addButton(
            "Erste Tabellenzeile verwenden", QtWidgets.QMessageBox.ButtonRole.ActionRole
        )
        dialog.exec()

        if dialog.clickedButton() is btn_first:
            return first_doc.path.stem + "_inkl_RBU.pdf"
        return default_invoice_name

    def _start_build_pipeline(self) -> None:
        try:
            mode = self._build_mode()
            if mode == "rbu_promote_pending":
                if not self._try_promote_single_rbu_to_base_pdf():
                    return
                mode = self._build_mode()

            if mode == "none":
                QtWidgets.QMessageBox.warning(
                    self,
                    "Unvollständiges Belegpaket",
                    "Laden Sie entweder eine vorhandene ZUGFeRD-PDF oder die Kombination aus Basis-PDF plus EN 16931 XML.",
                )
                return

            if not self._confirm_primary_document_choice():
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
                self._telemetry.logger.info(
                    "Mustang-Validierung nicht möglich: JRE oder Mustang-JAR fehlt."
                )
                return

            self._btn_build.setEnabled(False)
            stationery = self._settings.resolve_stationery_path()
            apply_all = bool(self._settings.settings.stationery_apply_all_pages)

            if mode == "bundle_mode":
                self._start_bundle_pipeline(
                    java_exe, mustang_jar, stationery, apply_all
                )
            elif mode == "merge_mode":
                self._start_merge_pipeline(java_exe, mustang_jar, stationery, apply_all)
            else:
                self._start_assemble_pipeline(
                    java_exe, mustang_jar, stationery, apply_all
                )
        except Exception as _build_pipeline_exc:
            self._btn_build.setEnabled(True)
            self._status.set_state("error")
            import traceback

            tb = traceback.format_exc()
            self._telemetry.logger.critical(
                "BUILD-PIPELINE ABGEBROCHEN (Exception-Guard): "
                + str(_build_pipeline_exc)
                + "\n"
                + tb
            )
            QtWidgets.QMessageBox.critical(
                self,
                "Vorgang fehlgeschlagen",
                "Beim Starten der Verarbeitung ist ein unerwarteter Fehler aufgetreten:\n\n"
                + str(_build_pipeline_exc)
                + "\n\nDetails wurden in system.log protokolliert.",
            )

    def _start_merge_pipeline(
        self,
        java_exe: Path,
        mustang_jar: Path,
        stationery: Optional[Path],
        apply_all: bool,
    ) -> None:
        try:
            invoice = self._get_invoice_document()
            if invoice is None or invoice.payload is None:
                self._btn_build.setEnabled(True)
                QtWidgets.QMessageBox.critical(
                    self,
                    "Fehlender Datensatz",
                    "Für die MERGE-Verarbeitung wird genau eine vorhandene E-Rechnung mit eingebettetem Datensatz benötigt.",
                )
                return
            embedded_xml = invoice.payload.xml_bytes
            from zug2ferd.core.cii_converter import convert_invoice_xml_to_cii

            cii_result = convert_invoice_xml_to_cii(embedded_xml)
            ordered_paths = [doc.path for doc in self._documents]
            effective_xml_bytes = embedded_xml
            if cii_result.ok:
                if cii_result.source_dialect in {"UBL-Invoice", "UBL-CreditNote"}:
                    self._telemetry.logger.info(
                        f"MERGE: Eingebettetes XML war UBL ({cii_result.source_dialect}). "
                        "Automatische Konvertierung in Factur-X/CII CrossIndustryInvoice durchgeführt."
                    )
                    converted_xml = (
                        cii_result.xml_bytes if cii_result.xml_bytes else embedded_xml
                    )
                    try:
                        invoice.payload = InvoicePayload(
                            xml_bytes=converted_xml,
                            seller=invoice.payload.seller,
                            filespec_name="factur-x.xml",
                        )
                    except Exception:
                        invoice.payload = InvoicePayload(
                            xml_bytes=converted_xml,
                            seller=invoice.payload.seller,
                        )
                    effective_xml_bytes = converted_xml
            else:
                self._telemetry.logger.info(
                    "MERGE: Automatische CII-Konvertierung gescheitert ("
                    + str(cii_result.error or "keine Details")
                    + ") – Original-XML wird eingebettet (Mustang-Validierung dann ggf. fehlerhaft)."
                )
            self._telemetry.logger.info(
                f"Build-Pipeline [MERGE] gestartet (Stamping/Merge/Attachment/Validation) für {len(ordered_paths)} Dokument(e)."
            )
            tmp_name = f"zug2ferd_build_{os.getpid()}_{uuid.uuid4().hex}.pdf"
            tmp_output = self._paths.tmp_dir / tmp_name

            QtCore.QThreadPool.globalInstance().start(
                _BuildRunnable(
                    self._build_bus,
                    ordered_paths,
                    effective_xml_bytes,
                    stationery,
                    apply_all,
                    java_exe,
                    mustang_jar,
                    tmp_output,
                )
            )
        except Exception as _merge_exc:
            self._btn_build.setEnabled(True)
            self._status.set_state("error")
            import traceback

            tb = traceback.format_exc()
            self._telemetry.logger.critical(
                "MERGE-PIPELINE ABGEBROCHEN (Exception-Guard): "
                + str(_merge_exc)
                + "\n"
                + tb
            )
            QtWidgets.QMessageBox.critical(
                self,
                "MERGE fehlgeschlagen",
                "Beim Aufbereiten der E-Rechnung ist ein unerwarteter Fehler aufgetreten:\n\n"
                + str(_merge_exc)
                + "\n\nDetails wurden in system.log protokolliert.",
            )

    def _start_assemble_pipeline(
        self,
        java_exe: Path,
        mustang_jar: Path,
        stationery: Optional[Path],
        apply_all: bool,
    ) -> None:
        try:
            base_pdf = self._get_base_pdf()
            standalone = self._get_standalone_xml()
            if base_pdf is None or standalone is None:
                self._btn_build.setEnabled(True)
                QtWidgets.QMessageBox.critical(
                    self,
                    "Unvollständige Eingabe",
                    "Für die ASSEMBLE-Verarbeitung werden sowohl ein Basis-PDF als auch ein E-Rechnungs-XML-Datensatz benötigt.",
                )
                return

            xml_bytes_for_build = standalone.xml_bytes
            from zug2ferd.core.cii_converter import convert_invoice_xml_to_cii

            cii_result_asm = convert_invoice_xml_to_cii(standalone.xml_bytes)
            if cii_result_asm.ok and cii_result_asm.source_dialect in {
                "UBL-Invoice",
                "UBL-CreditNote",
            }:
                self._telemetry.logger.info(
                    "ASSEMBLE: Standalone XML war UBL ("
                    + cii_result_asm.source_dialect
                    + "). Automatische Konvertierung in Factur-X/CII CrossIndustryInvoice durchgeführt."
                )
                xml_bytes_for_build = cii_result_asm.xml_bytes or standalone.xml_bytes
            elif not cii_result_asm.ok:
                self._telemetry.logger.info(
                    "ASSEMBLE: Automatische CII-Konvertierung gescheitert ("
                    + str(cii_result_asm.error or "keine Details")
                    + ") – Original-XML wird verwendet."
                )

            rbu_documents = self._get_rbu_documents()
            self._telemetry.logger.info(
                "Build-Pipeline [ASSEMBLE] gestartet: Basis-PDF="
                + base_pdf.path.name
                + f", XML={standalone.normcheck.dialect}, RBU-Dokumente={len(rbu_documents)}."
            )
            tmp_name = f"zug2ferd_assemble_{os.getpid()}_{uuid.uuid4().hex}.pdf"
            tmp_output = self._paths.tmp_dir / tmp_name
            rbu_paths = [d.path for d in self._documents if d.kind == KIND_RBU]
            QtCore.QThreadPool.globalInstance().start(
                _XmlAssembleRunnable(
                    self._assemble_bus,
                    base_pdf.path,
                    xml_bytes_for_build,
                    rbu_paths,
                    stationery,
                    apply_all,
                    java_exe,
                    mustang_jar,
                    tmp_output,
                )
            )
        except Exception as _asm_exc:
            self._btn_build.setEnabled(True)
            self._status.set_state("error")
            import traceback

            tb = traceback.format_exc()
            self._telemetry.logger.critical(
                "ASSEMBLE-PIPELINE ABGEBROCHEN (Exception-Guard): "
                + str(_asm_exc)
                + "\n"
                + tb
            )
            QtWidgets.QMessageBox.critical(
                self,
                "ASSEMBLE fehlgeschlagen",
                "Beim Aufbereiten des Basis-PDF mit E-Rechnungs-XML ist ein unerwarteter Fehler aufgetreten:\n\n"
                + str(_asm_exc)
                + "\n\nDetails wurden in system.log protokolliert.",
            )

    def _start_bundle_pipeline(
        self,
        java_exe: Path,
        mustang_jar: Path,
        stationery: Optional[Path],
        apply_all: bool,
    ) -> None:
        bundle = self._get_active_bundle()
        assert bundle is not None
        assert bundle.summary_file is not None
        assert bundle.invoice_file is not None

        summary_path = bundle.summary_file.path
        invoice_xml_bytes: bytes = b""
        try:
            payload = extract_invoice_payload(bundle.invoice_file.path)
            if payload is not None:
                invoice_xml_bytes = payload.xml_bytes
        except Exception:
            invoice_xml_bytes = b""

        rbu_paths: list[Path] = []
        for rbu in bundle.ordered_rbu_entries():
            rbu_paths.append(rbu.path)

        self._telemetry.logger.info(
            "Build-Pipeline [GESAMT-BUNDLE] gestartet: "
            f"Dach-Rechnung={bundle.roof_token} | Basis-SUMMARY={summary_path.name}"
            f" | XML-Quelle={bundle.invoice_file.path.name}"
            f" | RBU-Reihenfolge: " + ", ".join(p.name for p in rbu_paths)
        )

        tmp_name = f"zug2ferd_bundle_{os.getpid()}_{uuid.uuid4().hex}.pdf"
        tmp_output = self._paths.tmp_dir / tmp_name
        QtCore.QThreadPool.globalInstance().start(
            _BundleRunnable(
                self._build_bus,
                summary_path,
                invoice_xml_bytes,
                rbu_paths,
                stationery,
                apply_all,
                java_exe,
                mustang_jar,
                tmp_output,
            )
        )

    def _confirm_primary_document_choice(self) -> bool:
        if not self._documents:
            return False

        mode = self._build_mode()

        if mode == "bundle_mode":
            bundle = self._get_active_bundle()
            if bundle is None:
                return False
            ready, blockers = bundle_ready_for_export(bundle)
            if not ready:
                QtWidgets.QMessageBox.critical(
                    self,
                    "GESAMT-Paket unvollständig",
                    "Das Bundle kann derzeit nicht exportiert werden:\n- "
                    + "\n- ".join(blockers),
                )
                return False
            if bundle.validation_issues:
                dialog = QtWidgets.QMessageBox(self)
                dialog.setIcon(QtWidgets.QMessageBox.Icon.Warning)
                dialog.setWindowTitle("GESAMT-Paket: Validierungshinweise")
                dialog.setText(
                    "Das automatisch erkannte Bundle hat folgende Validierungshinweise. "
                    "Der Export kann dennoch fortgesetzt werden – Prüfung und Akzeptanz obliegt Ihnen.\n\n"
                    + "- "
                    + "\n- ".join(bundle.validation_issues)
                )
                btn_continue = dialog.addButton(
                    "Trotzdem zusammenführen",
                    QtWidgets.QMessageBox.ButtonRole.AcceptRole,
                )
                dialog.addButton(
                    "Abbrechen", QtWidgets.QMessageBox.ButtonRole.RejectRole
                )
                dialog.exec()
                if dialog.clickedButton() is not btn_continue:
                    return False
            return True

        primary_invoice: Optional[DocumentEntry] = None
        if mode == "merge_mode":
            primary_invoice = self._get_invoice_document()
        elif mode == "assemble_mode":
            standalone = self._get_standalone_xml()
            if standalone is not None:
                for d in self._documents:
                    if d.kind == KIND_XML_DATASET and d.xml_entry is not None:
                        primary_invoice = d
                        break

        unsafe_reasons: list[str] = []
        if primary_invoice is not None:
            if primary_invoice.norm_ok is False:
                nc = primary_invoice.norm_details
                missing = (
                    list(nc.missing_required)
                    if nc is not None
                    else ["(Prüfergebnis nicht verfügbar)"]
                )
                unsafe_reasons.append(
                    "Norm-EN 16931: Es fehlen Pflichtfelder: " + ", ".join(missing)
                )
            if primary_invoice.pdfa3_ok is False:
                issues = list(primary_invoice.pdfa3_issues) or [
                    "(Struktur-Prüfung nicht bestanden)"
                ]
                unsafe_reasons.append(
                    "PDF/A-3-Struktur fehlerhaft: " + "; ".join(issues)
                )
        if unsafe_reasons:
            dialog = QtWidgets.QMessageBox(self)
            dialog.setIcon(QtWidgets.QMessageBox.Icon.Warning)
            dialog.setWindowTitle("Hinweis: E-Rechnung formal unvollständig")
            dialog.setText(
                "Die Basis-Datei der E-Rechnung ist nach unserer Beurteilung NICHT vollständig formalkorrekt. "
                "Die Akzeptanz auf der Seite des Empfängers können wir daher KEINESFALLS garantieren!\n\n"
                "Festgestellte Probleme:\n- " + "\n- ".join(unsafe_reasons)
            )
            dialog.setInformativeText(
                "Sie können den Vorgang dennoch fortsetzen – die erzeugte Ausgabedatei wird gemäß unseren "
                "Reparaturregeln soweit möglich auf den ZUGFeRD-Standard normiert. Eine nachträgliche Änderung "
                "durch den Rechnungsempfänger kann hiermit jedoch nicht ausgeschlossen werden."
            )
            btn_continue = dialog.addButton(
                "Trotzdem zusammenführen", QtWidgets.QMessageBox.ButtonRole.AcceptRole
            )
            dialog.addButton("Abbrechen", QtWidgets.QMessageBox.ButtonRole.RejectRole)
            dialog.exec()
            if dialog.clickedButton() is not btn_continue:
                return False

        if mode == "assemble_mode":
            standalone = self._get_standalone_xml()
            if standalone is not None and standalone.normcheck.ok is False:
                dialog2 = QtWidgets.QMessageBox(self)
                dialog2.setIcon(QtWidgets.QMessageBox.Icon.Warning)
                dialog2.setWindowTitle("Hinweis: E-Rechnung formal unvollständig")
                dialog2.setText(
                    "Das separate E-Rechnungs-XML ist nach unserer Beurteilung NICHT vollständig formalkorrekt. "
                    "Die Akzeptanz auf der Seite des Empfängers können wir daher KEINESFALLS garantieren!\n\n"
                    "Fehlend: " + ", ".join(standalone.normcheck.missing_required)
                )
                btn_continue2 = dialog2.addButton(
                    "Trotzdem zusammenführen",
                    QtWidgets.QMessageBox.ButtonRole.AcceptRole,
                )
                dialog2.addButton(
                    "Abbrechen", QtWidgets.QMessageBox.ButtonRole.RejectRole
                )
                dialog2.exec()
                if dialog2.clickedButton() is not btn_continue2:
                    return False
            return True

        first_doc = self._documents[0]
        if first_doc.kind == KIND_INVOICE:
            return True

        dialog = QtWidgets.QMessageBox(self)
        dialog.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        dialog.setWindowTitle("Primäres Dokument bestätigen")
        dialog.setText(
            "Die erste Position des Belegpakets ist aktuell kein E-Rechnungsdokument, sondern ein RBU-/PDF-Dokument. "
            "Soll dieses Dokument wirklich als primäre Merge-Basis verwendet werden?"
        )
        dialog.setInformativeText(
            "Die eingebettete XML der vorhandenen E-Rechnung bleibt dabei erhalten. Der spätere Exportname kann "
            "anschließend weiterhin separat gewählt werden."
        )
        continue_button = dialog.addButton(
            "Ja, so zusammenführen", QtWidgets.QMessageBox.ButtonRole.AcceptRole
        )
        dialog.addButton("Abbrechen", QtWidgets.QMessageBox.ButtonRole.RejectRole)
        dialog.exec()
        return dialog.clickedButton() is continue_button

    @QtCore.pyqtSlot(object) if hasattr(QtCore, "pyqtSlot") else QtCore.Slot(object)
    def _on_build_done_main(self, result: object) -> None:
        self._btn_build.setEnabled(True)
        if not isinstance(result, BuildResult):
            return
        if not result.ok or result.output_pdf_path is None:
            self._status.set_state("error")
            self._telemetry.logger.info(
                "Build fehlgeschlagen: " + (result.error or "unbekannt")
            )
            return
        export_mode_label = "MERGE"
        if not result.mustang_ok:
            self._status.set_state("warn")
            self._telemetry.logger.warning(
                "HINWEIS: Mustang-Validierung hat Abweichungen festgestellt. "
                "Datei wird dennoch gemäß §521 BGB (Schenkung) als 'AS-IS' exportiert - "
                "Nutzer hat die finale Prüfung selbst durchzuführen."
            )
            if result.mustang_stdout.strip():
                self._telemetry.logger.info(result.mustang_stdout.strip())
            if result.mustang_stderr.strip():
                self._telemetry.logger.info(result.mustang_stderr.strip())
            export_mode_label = "MERGE (WARNUNG: Mustang-Validierung mit Abweichungen)"
        else:
            self._status.set_state("ok")

        self._finalize_export(result.output_pdf_path, export_mode_label, result.mustang_stdout)

    @QtCore.pyqtSlot(object) if hasattr(QtCore, "pyqtSlot") else QtCore.Slot(object)
    def _on_assemble_done_main(self, result: object) -> None:
        self._btn_build.setEnabled(True)
        if not isinstance(result, XmlAssembleResult):
            return
        if not result.ok or result.output_pdf_path is None:
            self._status.set_state("error")
            self._telemetry.logger.info(
                "PDF+XML-Assemblierung fehlgeschlagen: " + (result.error or "unbekannt")
            )
            if result.normcheck is not None and result.normcheck.missing_required:
                self._telemetry.logger.info(
                    "XML-EN16931-Fehler: "
                    + ", ".join(result.normcheck.missing_required)
                )
            return
        export_mode = "ASSEMBLE"
        if result.normcheck is None or not result.normcheck.ok:
            self._status.set_state("warn")
            self._telemetry.logger.warning(
                "HINWEIS: Vorab-EN16931-Normprüfung hat Abweichungen. Datei wird AS-IS exportiert."
            )
            export_mode = "ASSEMBLE (WARNUNG: Normprüfung mit Abweichungen)"
        elif not result.mustang_ok:
            self._status.set_state("warn")
            self._telemetry.logger.warning(
                "HINWEIS: Mustang-Validierung nach Assemblierung mit Abweichungen. Datei wird AS-IS exportiert."
            )
            if result.mustang_stdout.strip():
                self._telemetry.logger.info(result.mustang_stdout.strip())
            if result.mustang_stderr.strip():
                self._telemetry.logger.info(result.mustang_stderr.strip())
            export_mode = "ASSEMBLE (WARNUNG: Mustang-Validierung mit Abweichungen)"
        else:
            self._status.set_state("ok")

        self._finalize_export(result.output_pdf_path, export_mode, result.mustang_stdout)

    def _finalize_export(
        self, tmp_output: Path, mode_tag: str, mustang_stdout: str
    ) -> None:
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
            target.write_bytes(tmp_output.read_bytes())
            self._last_output_dir = target.parent
        except Exception as exc:
            self._status.set_state("error")
            self._telemetry.logger.info("Konnte Export nicht schreiben: " + str(exc))
            return

        self._status.set_state("ok")
        self._telemetry.logger.info(
            f"MUSTANG_ERFOLG [{mode_tag}] | Die erzeugte Datei {Path(save_path).name} wurde als ZUGFeRD-konform erfolgreich validiert."
        )
        if mustang_stdout.strip():
            self._telemetry.logger.info(mustang_stdout.strip())
        self._telemetry.logger.info("Export gespeichert: " + Path(save_path).name)

    def _reset_pipeline_state(self) -> None:
        self._documents = []
        self._last_bypass_missing = None
        self._detected_bundles = []
        self._active_bundle_key = None
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
        result = run_ustg_check_with_payload(
            self._payload, self._invoice, self._stationery, self._enabled
        )
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
                    pdf_chunks.append(
                        stamp_pdf_underlay(
                            source, self._stationery, self._apply_all_pages
                        )
                    )
                    continue
                if is_image_path(source):
                    image_pdf = convert_image_to_pdf_bytes(source)
                    if self._stationery is not None and self._stationery.exists():
                        temp_pdf = (
                            self._tmp_output.parent / f"img_{uuid.uuid4().hex}.pdf"
                        )
                        temp_pdf.write_bytes(image_pdf)
                        temp_files.append(temp_pdf)
                        pdf_chunks.append(
                            stamp_pdf_underlay(
                                temp_pdf, self._stationery, self._apply_all_pages
                            )
                        )
                    else:
                        pdf_chunks.append(image_pdf)

            merge_pdfs_to_file(self._tmp_output, pdf_chunks)
            attach_facturx_xml(self._tmp_output, self._xml_bytes)
            mustang_ok, stdout, stderr = run_mustang_validate(
                self._java_exe, self._mustang_jar, self._tmp_output
            )
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


class _XmlAssembleBus(QtCore.QObject):
    done = Signal(object)


class _XmlAssembleRunnable(QtCore.QRunnable):
    def __init__(
        self,
        bus: _XmlAssembleBus,
        base_pdf: Path,
        xml_bytes: bytes,
        rbu_documents: list[Path],
        stationery_pdf: Optional[Path],
        apply_all_pages: bool,
        java_exe: Path,
        mustang_jar: Path,
        tmp_output: Path,
    ) -> None:
        super().__init__()
        self._bus = bus
        self._base_pdf = base_pdf
        self._xml_bytes = xml_bytes
        self._rbu_documents = rbu_documents
        self._stationery = stationery_pdf
        self._apply_all_pages = apply_all_pages
        self._java_exe = java_exe
        self._mustang_jar = mustang_jar
        self._tmp_output = tmp_output

    def run(self) -> None:
        temp_files: list[Path] = []
        try:
            intermediate: Path = self._tmp_output.with_suffix(".step1.pdf")
            if self._rbu_documents:
                chunks: list[bytes] = []
                chunks.append(
                    stamp_pdf_underlay(
                        self._base_pdf, self._stationery, self._apply_all_pages
                    )
                )
                for source in self._rbu_documents:
                    if is_pdf_path(source):
                        chunks.append(
                            stamp_pdf_underlay(
                                source, self._stationery, self._apply_all_pages
                            )
                        )
                        continue
                    if is_image_path(source):
                        image_pdf = convert_image_to_pdf_bytes(source)
                        if self._stationery is not None and self._stationery.exists():
                            temp_pdf = (
                                self._tmp_output.parent / f"img_{uuid.uuid4().hex}.pdf"
                            )
                            temp_pdf.write_bytes(image_pdf)
                            temp_files.append(temp_pdf)
                            chunks.append(
                                stamp_pdf_underlay(
                                    temp_pdf, self._stationery, self._apply_all_pages
                                )
                            )
                        else:
                            chunks.append(image_pdf)
                merge_pdfs_to_file(intermediate, chunks)
            else:
                data = stamp_pdf_underlay(
                    self._base_pdf, self._stationery, self._apply_all_pages
                )
                intermediate.write_bytes(data)

            result = assemble_zugferd_pipeline(
                base_pdf_path=intermediate,
                invoice_xml_path_or_bytes=self._xml_bytes,
                output_pdf_path=self._tmp_output,
                stationery_pdf=None,
                apply_stationery_all_pages=False,
                attachment_name=None,
                java_exe=self._java_exe,
                mustang_jar=self._mustang_jar,
            )
            if not result.ok:
                self._bus.done.emit(result)
                return
            self._bus.done.emit(result)
        except Exception as exc:
            result = XmlAssembleResult(
                ok=False,
                output_pdf_path=None,
                normcheck=None,
                mustang_ok=False,
                mustang_stdout="",
                mustang_stderr="",
                error=str(exc),
            )
            self._bus.done.emit(result)
        finally:
            for file_path in temp_files:
                file_path.unlink(missing_ok=True)
            try:
                step1 = self._tmp_output.with_suffix(".step1.pdf")
                if step1.exists() and step1.resolve() != self._tmp_output.resolve():
                    step1.unlink(missing_ok=True)
            except Exception:
                pass


class _BundleRunnable(QtCore.QRunnable):
    def __init__(
        self,
        bus: _BuildBus,
        summary_pdf: Path,
        invoice_xml_bytes: bytes,
        rbu_documents: list[Path],
        stationery_pdf: Optional[Path],
        apply_all_pages: bool,
        java_exe: Path,
        mustang_jar: Path,
        tmp_output: Path,
    ) -> None:
        super().__init__()
        self._bus = bus
        self._summary_pdf = summary_pdf
        self._xml_bytes = invoice_xml_bytes
        self._rbu_documents = rbu_documents
        self._stationery = stationery_pdf
        self._apply_all_pages = apply_all_pages
        self._java_exe = java_exe
        self._mustang_jar = mustang_jar
        self._tmp_output = tmp_output

    def run(self) -> None:
        temp_files: list[Path] = []
        try:
            intermediate: Path = self._tmp_output.with_suffix(".bundle_step1.pdf")
            merged: Path = self._tmp_output.with_suffix(".bundle_step2.pdf")
            final: Path = self._tmp_output.with_suffix(".bundle_step3.pdf")

            try:
                _, nc = parse_invoice_xml(self._xml_bytes)
            except Exception:
                nc = None
            if nc is None or not nc.ok:
                raise RuntimeError(
                    "Das in der E-INVOICE eingebettete XML ist nicht EN 16931-konform "
                    "oder konnte nicht gelesen werden."
                )

            # Step 1: SUMMARY als PDF/A-3 ZUGFeRD Basis aufbereiten (XML + XMP + /AF)
            assemble_zugferd_pdf(
                base_pdf_path=self._summary_pdf,
                invoice_xml_path_or_bytes=self._xml_bytes,
                output_pdf_path=intermediate,
                stationery_pdf=self._stationery,
                apply_stationery_all_pages=self._apply_all_pages,
                run_normcheck=False,
            )

            # Step 2: SUMMARY + alle RBU-Dokumente zusammenführen
            # ACHTUNG: merge_pdfs_to_file() zerstört durch pypdf-seitiges Rekonstruieren
            # das /AF-Array, /EmbeddedFiles Name Tree und XMP-Metadaten der Step-1 PDF.
            # Nach dem Merge brauchen wir daher zwingend Step 3 (Self-Healing).
            chunks: list[bytes] = []
            chunks.append(intermediate.read_bytes())
            for source in self._rbu_documents:
                if is_pdf_path(source):
                    chunks.append(
                        stamp_pdf_underlay(
                            source, self._stationery, self._apply_all_pages
                        )
                    )
                    continue
                if is_image_path(source):
                    image_pdf = convert_image_to_pdf_bytes(source)
                    if self._stationery is not None and self._stationery.exists():
                        temp_pdf = (
                            self._tmp_output.parent
                            / f"bundle_img_{uuid.uuid4().hex}.pdf"
                        )
                        temp_pdf.write_bytes(image_pdf)
                        temp_files.append(temp_pdf)
                        chunks.append(
                            stamp_pdf_underlay(
                                temp_pdf, self._stationery, self._apply_all_pages
                            )
                        )
                    else:
                        chunks.append(image_pdf)

            merge_pdfs_to_file(merged, chunks)

            # Step 3: PDF/A-3 Self-Healing nach Merge — zweiter Pass!
            # Rekonstruiert /AF, /EmbeddedFiles, XMP (pdfaid:part=3, fx:ConformanceLevel)
            # und bettet das Original-XML erneut ein. Nur so wird Mustang-Validation
            # nach dem Merge grün.
            assemble_zugferd_pdf(
                base_pdf_path=merged,
                invoice_xml_path_or_bytes=self._xml_bytes,
                output_pdf_path=final,
                stationery_pdf=None,  # Stempel wurden bereits in Step 1/2 aufgetragen
                apply_stationery_all_pages=False,
                run_normcheck=False,
            )

            mustang_ok, stdout, stderr = run_mustang_validate(
                self._java_exe, self._mustang_jar, final
            )

            final.replace(self._tmp_output)

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
            for suffix in (
                ".bundle_step1.pdf",
                ".bundle_step2.pdf",
                ".bundle_step3.pdf",
            ):
                try:
                    p = self._tmp_output.with_suffix(suffix)
                    if p.exists() and p.resolve() != self._tmp_output.resolve():
                        p.unlink(missing_ok=True)
                except Exception:
                    pass
        self._bus.done.emit(result)
