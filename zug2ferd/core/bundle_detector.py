from __future__ import annotations

import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


BUNDLE_ROLE_SUMMARY = "summary"
BUNDLE_ROLE_INVOICE = "einvoice"
BUNDLE_ROLE_ATTACHMENT = "attachment"
BUNDLE_ROLE_STATEMENT = "statement"
BUNDLE_ROLE_UNKNOWN = "unknown"

_VALID_ROLES = (
    BUNDLE_ROLE_SUMMARY,
    BUNDLE_ROLE_INVOICE,
    BUNDLE_ROLE_ATTACHMENT,
    BUNDLE_ROLE_STATEMENT,
)

_ROLE_RANK = {
    BUNDLE_ROLE_SUMMARY: 0,
    BUNDLE_ROLE_INVOICE: 1,
    BUNDLE_ROLE_ATTACHMENT: 2,
    BUNDLE_ROLE_STATEMENT: 3,
}

_RBU_RANK = {
    BUNDLE_ROLE_INVOICE: 0,
    BUNDLE_ROLE_ATTACHMENT: 1,
    BUNDLE_ROLE_STATEMENT: 2,
}

_FILENAME_RE = re.compile(
    r"^(?P<customer_no>\d+)"
    r"_(?P<date>\d{4}-\d{2}-\d{2})"
    r"(?:_(?P<country>[A-Z]{2}))?"
    r"_E-(?P<doctype>[A-Za-z ]+?)"
    r"_(?P<yy>\d{2})-(?P<billing>\d+)-(?P<suffix>\d{3})"
    r"\.(?P<ext>[^.]+)$"
)

_BILLING_TOKEN_RE = re.compile(r"(?P<yy>\d{2})-(?P<billing>\d+)-(?P<suffix>\d{3})")


def normalize_date_to_iso(value: str) -> Optional[str]:
    """
    Robuste, reihenfolgenunabhängige Datumsnormalisierung.

    Unterstützt die gängigsten Formate — auch mehrdeutige (z. B. DDMMYY
    vs. YYMMDD), wobei hier nur eindeutige Muster geparst werden.
    Folgende Formate werden erkannt (Beispiele für den 30.09.2026):

    *   2026-09-30, 2026/09/30, 2026.09.30         (ISO/intl 8stellig + sep)
    *   30.09.2026, 30-09-2026, 30/09/2026           (DE 8stellig + sep)
    *   09-30-2026                                     (US 8stellig + sep)
    *   20260930                                       (8stellig kompakt JJJJMMTT)
    *   30092026                                       (8stellig kompakt TTMMJJJJ)
    *   300926, 260930                                 (6stellig kompakt TTMMJJ / JJMMTT)

    Gibt das Datum im kanonischen Format ``YYYY-MM-DD`` zurück, oder None,
    wenn es nicht sicher zugeordnet werden kann.
    """
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None

    # —— 1) Formate mit expliziten Trennzeichen ——
    m = re.fullmatch(r"(\d{4})[\-/.](\d{1,2})[\-/.](\d{1,2})", s)
    if m:
        try:
            y = int(m.group(1))
            mo = int(m.group(2))
            d = int(m.group(3))
            if 1900 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31:
                return f"{y:04d}-{mo:02d}-{d:02d}"
        except ValueError:
            pass

    m = re.fullmatch(r"(\d{1,2})[\-\.\/](\d{1,2})[\-\.\/](\d{2,4})", s)
    if m:
        try:
            a = int(m.group(1))
            b = int(m.group(2))
            y_raw = int(m.group(3))
            sep = s[2] if len(s) >= 3 and s[2] in "-./" else "."
            y = y_raw if y_raw >= 100 else (2000 + y_raw)
            if 1900 > y or y > 2100:
                y = 2000 + (y_raw % 100)
            # DE-Variante: TT.MM.JJJJ (erster Wert Tag)
            if sep == "." or (1 <= a <= 31 and 1 <= b <= 12):
                if 1 <= a <= 31 and 1 <= b <= 12:
                    return f"{y:04d}-{b:02d}-{a:02d}"
            # US-Variante: MM/DD/YYYY
            if sep == "/" and 1 <= a <= 12 and 1 <= b <= 31:
                return f"{y:04d}-{a:02d}-{b:02d}"
        except ValueError:
            pass

    # —— 2) Kompakte Formate (8-stellig) ——
    m = re.fullmatch(r"(\d{8})", s)
    if m:
        c = m.group(1)
        # Variante A: JJJJMMTT (z. B. 20260930)
        try:
            y, mo, d = int(c[0:4]), int(c[4:6]), int(c[6:8])
            if 1900 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31:
                return f"{y:04d}-{mo:02d}-{d:02d}"
        except ValueError:
            pass
        # Variante B: TTMMJJJJ (z. B. 30092026)
        try:
            d, mo, y = int(c[0:2]), int(c[2:4]), int(c[4:8])
            if 1900 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31:
                return f"{y:04d}-{mo:02d}-{d:02d}"
        except ValueError:
            pass

    # —— 3) Kompakte Formate (6-stellig) —— TTMMJJ vs. JJMMTT ——
    m = re.fullmatch(r"(\d{6})", s)
    if m:
        c = m.group(1)
        candidates: list[tuple[int, str]] = []
        # Variante A: TTMMJJ -> interpretieren als Jahr 2000 + JJ
        try:
            d, mo, yy = int(c[0:2]), int(c[2:4]), int(c[4:6])
            if 1 <= d <= 31 and 1 <= mo <= 12:
                iso = f"20{yy:02d}-{mo:02d}-{d:02d}"
                dist = abs(2026 - (2000 + yy))
                candidates.append((dist, iso))
        except ValueError:
            pass
        # Variante B: JJMMTT
        try:
            yy, mo, d = int(c[0:2]), int(c[2:4]), int(c[4:6])
            if 1 <= d <= 31 and 1 <= mo <= 12:
                iso = f"20{yy:02d}-{mo:02d}-{d:02d}"
                dist = abs(2026 - (2000 + yy))
                candidates.append((dist, iso))
        except ValueError:
            pass
        if candidates:
            candidates.sort(key=lambda t: t[0])
            uniq_iso = {iso for _, iso in candidates}
            if len(uniq_iso) == 1:
                return next(iter(uniq_iso))
            return candidates[0][1]

    # —— 4) ISO 8601-Zeitstempel (2026-09-30T12:00:00Z) ——
    if len(s) >= 10:
        core = s[:10]
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", core):
            return core
    return None


def iso_to_compact(value: str) -> Optional[str]:
    iso = normalize_date_to_iso(value)
    return iso.replace("-", "") if iso else None


def dates_match(a: str, b: str) -> bool:
    """
    Vergleicht zwei Datumsangaben formatunabhängig. True, wenn beide
    erfolgreich auf denselben YYYY-MM-DD-Tag normalisiert werden können.
    Wenn nur eine Seite normalisierbar ist → False.
    """
    if not a or not b:
        return False
    na = normalize_date_to_iso(a)
    nb = normalize_date_to_iso(b)
    return bool(na and nb and na == nb)


def scan_for_dates_in_name(file_stem: str) -> list[str]:
    """
    Durchsucht einen beliebigen Dateinamen (ohne Extension) nach allen
    erkennbaren Datumstokens und gibt sie als Liste von YYYY-MM-DD zurück
    (eindeutig, in Fund-Reihenfolge). Wird genutzt, falls das strenge
    Bundle-Pattern (FILENAME_RE) nicht passt.
    """
    results: list[str] = []
    seen: set[str] = set()

    # 1) 8-stellig kompakt + 6-stellig kompakt, alle laufenden Fenster
    #    suchen wir gezielt über Regex-Matcher.
    patterns = [
        r"(?<![0-9a-zA-Z])(\d{4}[\-/.]\d{1,2}[\-/.]\d{1,2})(?![0-9a-zA-Z])",
        r"(?<![0-9a-zA-Z])(\d{1,2}[\-\.\/]\d{1,2}[\-\.\/]\d{2,4})(?![0-9a-zA-Z])",
        r"(?<![0-9a-zA-Z])(\d{8})(?![0-9a-zA-Z])",
        r"(?<![0-9a-zA-Z])(\d{6})(?![0-9a-zA-Z])",
    ]
    for p in patterns:
        for m in re.finditer(p, file_stem):
            token = m.group(1)
            iso = normalize_date_to_iso(token)
            if iso and iso not in seen:
                seen.add(iso)
                results.append(iso)
    return results


def file_creation_iso(path: Path) -> Optional[str]:
    """
    Liefert das Erstelldatum der Datei als YYYY-MM-DD oder None.
    Windows: st_ctime = Creation Time; auf anderen Systemen:
    st_birthtime falls verfügbar, sonst None (kein Rückgriff auf mtime/atime,
    da dies Änderungszeitpunkte sind — explizit vom Anwender nicht gewünscht).
    """
    try:
        st = path.stat()
    except Exception:
        return None
    ts: Optional[float] = None
    if hasattr(st, "st_birthtime"):
        ts = st.st_birthtime  # type: ignore[attr-defined]
    elif sys.platform.startswith("win"):
        ts = st.st_ctime
    if ts is None:
        return None
    try:
        from datetime import datetime

        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except Exception:
        return None


@dataclass(frozen=True)
class BundleFilenameInfo:
    raw_name: str
    customer_no: Optional[str]
    date_iso: Optional[str]
    country: Optional[str]
    doctype_raw: Optional[str]
    yy_prefix: Optional[str]
    billing_no: Optional[str]
    suffix: Optional[str]
    role: str
    extension: str

    @property
    def billing_token(self) -> Optional[str]:
        if self.yy_prefix and self.billing_no and self.suffix:
            return f"{self.yy_prefix}-{self.billing_no}-{self.suffix}"
        return None

    @property
    def roof_token(self) -> Optional[str]:
        if self.yy_prefix and self.billing_no:
            return f"{self.yy_prefix}-{self.billing_no}-000"
        return None


@dataclass
class BundleFileEntry:
    path: Path
    info: BundleFilenameInfo
    pdf_text_hints: tuple[str, ...] = ()
    has_embedded_xml: bool = False
    invoice_id_from_xml: Optional[str] = None
    issue_date_from_xml: Optional[str] = None
    seller_name: Optional[str] = None


@dataclass
class DetectedBundle:
    customer_no: Optional[str]
    date_iso: Optional[str]
    country: Optional[str]
    yy_prefix: Optional[str]
    billing_no: Optional[str]
    roof_token: str
    files: list[BundleFileEntry] = field(default_factory=list)
    validation_issues: list[str] = field(default_factory=list)
    validation_ok: bool = False

    @property
    def summary_file(self) -> Optional[BundleFileEntry]:
        for f in self.files:
            if f.info.role == BUNDLE_ROLE_SUMMARY:
                return f
        return None

    @property
    def invoice_file(self) -> Optional[BundleFileEntry]:
        for f in self.files:
            if f.info.role == BUNDLE_ROLE_INVOICE:
                return f
        return None

    def ordered_rbu_entries(self) -> list[BundleFileEntry]:
        rb = [f for f in self.files if f.info.role in _RBU_RANK]
        rb.sort(key=lambda e: _RBU_RANK.get(e.info.role, 99))
        return rb

    def ordered_export_pdfs(self) -> list[Path]:
        result: list[Path] = []
        summary = self.summary_file
        if summary is not None:
            result.append(summary.path)
        for rbu in self.ordered_rbu_entries():
            if rbu is not summary:
                result.append(rbu.path)
        return result


def parse_bundle_filename(path: Path) -> BundleFilenameInfo:
    """
    Parst einen Bundle-Dateinamen. Bevorzugt das strenge Muster
    _FILENAME_RE. Falls dieses nicht matcht (variante Datumsposition,
    abweichende Formatierung, Land/Doctype optional), wird ein robuster
    Fallback mit diesen Quellen versucht:

      * Billing-Token  (YY-Billing-Suffix per _BILLING_TOKEN_RE)
      * Datum-Tokens   (scan_for_dates_in_name — position-unabhängig,
                       alle gängigen Formate inkl. DDMMYYYY, DDMMJJ usw.)
      * Erstelldatum   (file_creation_iso — letzter Rückfall, nur ctime)
      * Doctype-Rolle  (E-XXX Token im Namen + Suffix-Ranking)
      * Kundennummer   (erster 6-10 stelliger numerischer Block)
    """
    name = path.name
    stem = path.stem
    m = _FILENAME_RE.match(name)
    if m:
        customer_no = m.group("customer_no")
        date_iso = normalize_date_to_iso(m.group("date"))
        country = m.group("country")
        doctype_raw = m.group("doctype").strip()
        yy_prefix = m.group("yy")
        billing_no = m.group("billing")
        suffix = m.group("suffix")
        extension = m.group("ext").lower()
        role = _classify_role_by_doctype(doctype_raw)
        if role == BUNDLE_ROLE_UNKNOWN:
            role = _classify_role_by_suffix(suffix or "")
        return BundleFilenameInfo(
            raw_name=name,
            customer_no=customer_no,
            date_iso=date_iso,
            country=country,
            doctype_raw=doctype_raw,
            yy_prefix=yy_prefix,
            billing_no=billing_no,
            suffix=suffix,
            role=role,
            extension=extension,
        )

    # —— Robuster Fallback-Pfad ——
    # 1) Billing-Token suchen (egal wo im Namen)
    bt = _BILLING_TOKEN_RE.search(stem)
    yy_prefix: Optional[str] = bt.group("yy") if bt else None
    billing_no: Optional[str] = bt.group("billing") if bt else None
    suffix: Optional[str] = bt.group("suffix") if bt else None

    # 2) Datum suchen — position-unabhängig, alle Formate
    date_candidates = scan_for_dates_in_name(stem)
    date_iso: Optional[str] = date_candidates[0] if date_candidates else None
    if not date_iso:
        date_iso = file_creation_iso(path)

    # 3) Doctype-Rolle erraten (E-XXX Muster)
    doctype_raw: Optional[str] = None
    doctype_match = re.search(r"E-([A-Za-z ]+?)(?:_|$)", stem)
    if doctype_match:
        doctype_raw = doctype_match.group(1).strip()
    role = _classify_role_by_doctype(doctype_raw or "")
    if role == BUNDLE_ROLE_UNKNOWN and suffix is not None:
        role = _classify_role_by_suffix(suffix)
    if role == BUNDLE_ROLE_UNKNOWN:
        role = _classify_role_by_hints(name, "")

    # 4) Kundennummer (erster lang genauer numerischer Block, 6–10 Stellen)
    customer_no: Optional[str] = None
    cnm = re.search(r"(?<!\d)(\d{6,10})(?!\d)", stem)
    if cnm:
        customer_no = cnm.group(1)

    # 5) Land (zwei Großbuchstaben-Token isoliert, zwischen _)
    country: Optional[str] = None
    cm = re.search(r"(?<=_)([A-Z]{2})(?=_)", stem)
    if cm:
        country = cm.group(1)

    extension = path.suffix.lstrip(".").lower()
    return BundleFilenameInfo(
        raw_name=name,
        customer_no=customer_no,
        date_iso=date_iso,
        country=country,
        doctype_raw=doctype_raw,
        yy_prefix=yy_prefix,
        billing_no=billing_no,
        suffix=suffix,
        role=role,
        extension=extension,
    )


def _classify_role_by_doctype(doctype_raw: str) -> str:
    if not doctype_raw:
        return BUNDLE_ROLE_UNKNOWN
    dt = doctype_raw.upper()
    if dt == "SUMMARY":
        return BUNDLE_ROLE_SUMMARY
    if dt == "INVOICE":
        return BUNDLE_ROLE_INVOICE
    if dt == "ATTACHMENT":
        return BUNDLE_ROLE_ATTACHMENT
    if "STATEMENT" in dt and "ACCOUNT" in dt:
        return BUNDLE_ROLE_STATEMENT
    return BUNDLE_ROLE_UNKNOWN


def _classify_role_by_suffix(suffix: str) -> str:
    if suffix == "000":
        return BUNDLE_ROLE_SUMMARY
    if suffix == "001":
        return BUNDLE_ROLE_INVOICE
    if suffix == "002":
        return BUNDLE_ROLE_ATTACHMENT
    return BUNDLE_ROLE_UNKNOWN


def _classify_role_by_hints(filename: str, content_hint: str) -> str:
    upper = filename.upper()
    if "E-SUMMARY" in upper:
        return BUNDLE_ROLE_SUMMARY
    if "E-INVOICE" in upper:
        return BUNDLE_ROLE_INVOICE
    if "E-ATTACHMENT" in upper:
        return BUNDLE_ROLE_ATTACHMENT
    if "STATEMENT" in upper:
        return BUNDLE_ROLE_STATEMENT
    return BUNDLE_ROLE_UNKNOWN


def _extract_billing_token_from_text(text: str) -> Optional[tuple[str, str, str]]:
    if not text:
        return None
    m = _BILLING_TOKEN_RE.search(text)
    if not m:
        return None
    return m.group("yy"), m.group("billing"), m.group("suffix")


def build_bundle_entry(
    path: Path,
    *,
    pdf_sample_text: str = "",
    has_embedded_xml: bool = False,
    invoice_id_from_xml: Optional[str] = None,
    issue_date_from_xml: Optional[str] = None,
    seller_name: Optional[str] = None,
) -> BundleFileEntry:
    info = parse_bundle_filename(path)
    hints: list[str] = []
    if pdf_sample_text:
        for t in pdf_sample_text.splitlines():
            t = t.strip()
            if t:
                hints.append(t)
            if len(hints) >= 20:
                break
    return BundleFileEntry(
        path=path,
        info=info,
        pdf_text_hints=tuple(hints),
        has_embedded_xml=has_embedded_xml,
        invoice_id_from_xml=invoice_id_from_xml,
        issue_date_from_xml=issue_date_from_xml,
        seller_name=seller_name,
    )


def _group_key(
    customer_no: Optional[str],
    date_iso: Optional[str],
    yy_prefix: Optional[str],
    billing_no: Optional[str],
) -> tuple[str, str, str, str]:
    return (
        customer_no or "",
        date_iso or "",
        yy_prefix or "",
        billing_no or "",
    )


def detect_bundles(entries: list[BundleFileEntry]) -> list[DetectedBundle]:
    groups: dict[tuple[str, str, str, str], list[BundleFileEntry]] = {}
    unknowns: list[BundleFileEntry] = []

    for e in entries:
        info = e.info
        if info.role == BUNDLE_ROLE_UNKNOWN and info.billing_token is None:
            unknowns.append(e)
            continue
        key = _group_key(
            info.customer_no, info.date_iso, info.yy_prefix, info.billing_no
        )
        groups.setdefault(key, []).append(e)

    bundles: list[DetectedBundle] = []
    for (customer_no, date_iso, yy_prefix, billing_no), files in groups.items():
        roof = f"{yy_prefix}-{billing_no}-000" if yy_prefix and billing_no else ""
        country: Optional[str] = None
        for f in files:
            if f.info.country:
                country = f.info.country
                break
        bundle = DetectedBundle(
            customer_no=customer_no or None,
            date_iso=date_iso or None,
            country=country,
            yy_prefix=yy_prefix or None,
            billing_no=billing_no or None,
            roof_token=roof,
            files=files,
        )
        _validate_bundle(bundle)
        bundles.append(bundle)

    if unknowns:
        for u in unknowns:
            bundles.append(
                DetectedBundle(
                    customer_no=None,
                    date_iso=None,
                    country=None,
                    yy_prefix=None,
                    billing_no=None,
                    roof_token="",
                    files=[u],
                    validation_issues=["Kein Bundle-Dateinamen-Muster erkannt."],
                    validation_ok=False,
                )
            )

    bundles.sort(key=lambda b: (b.roof_token or "", b.date_iso or ""))
    return bundles


def _validate_bundle(bundle: DetectedBundle) -> None:
    issues: list[str] = []

    roles_present = {f.info.role for f in bundle.files}

    if bundle.summary_file is None:
        issues.append("Kein E-SUMMARY-Dokument (-000) im Bundle gefunden.")
    if bundle.invoice_file is None:
        issues.append("Kein E-INVOICE-Dokument (-001) im Bundle gefunden.")
    elif not bundle.invoice_file.has_embedded_xml:
        issues.append("E-INVOICE-Dokument hat KEIN eingebettetes E-Rechnungs-XML.")

    if not bundle.billing_no:
        issues.append("Abrechnungsnummer aus Dateinamen nicht extrahierbar.")
    if not bundle.yy_prefix:
        issues.append("Jahres-Präfix aus Dateinamen nicht extrahierbar.")
    if not bundle.customer_no:
        issues.append("Kundennummer aus Dateinamen nicht extrahierbar.")
    if not bundle.date_iso:
        issues.append("Belegdatum aus Dateinamen nicht extrahierbar.")

    if bundle.invoice_file is not None and bundle.invoice_file.invoice_id_from_xml:
        iid = bundle.invoice_file.invoice_id_from_xml
        if bundle.billing_no and bundle.yy_prefix:
            expected = f"{bundle.yy_prefix}/{bundle.billing_no}/001"
            expected_alt = f"{bundle.yy_prefix}-{bundle.billing_no}-001"
            if iid not in (expected, expected_alt):
                issues.append(
                    "Rechnungs-ID im XML stimmt nicht mit Dateinamen-Billing-Token überein "
                    f"(XML: {iid!r}, erwartet: {expected!r})."
                )

    if bundle.invoice_file is not None:
        date_sources: list[str] = []
        if bundle.date_iso:
            date_sources.append(bundle.date_iso)
        if bundle.invoice_file.issue_date_from_xml:
            date_sources.append(bundle.invoice_file.issue_date_from_xml)
        try:
            fcd = file_creation_iso(bundle.invoice_file.path)
            if fcd:
                date_sources.append(fcd)
        except Exception:
            pass
        for hint in bundle.invoice_file.pdf_text_hints or ():
            founds = scan_for_dates_in_name(hint)
            date_sources.extend(founds)
        normalized = [normalize_date_to_iso(d) for d in date_sources if d]
        normalized = [d for d in normalized if d]
        consensus: Optional[str] = None
        if normalized:
            counter = Counter(normalized)
            top_date, top_count = counter.most_common(1)[0]
            if top_count >= 2:
                consensus = top_date
        if consensus is None and len(normalized) >= 2:
            uniq = sorted(set(normalized))
            if len(uniq) == 1:
                consensus = uniq[0]
        if consensus is None and len(normalized) >= 2:
            iso_a = normalize_date_to_iso(bundle.date_iso) if bundle.date_iso else None
            iso_b = (
                normalize_date_to_iso(bundle.invoice_file.issue_date_from_xml)
                if bundle.invoice_file.issue_date_from_xml
                else None
            )
            if iso_a and iso_b and not dates_match(iso_a, iso_b):
                issues.append(
                    "Belegdatum aus Dateinamen und XML unterscheiden sich "
                    f"(Datei: {iso_a!r}, XML: {iso_b!r})."
                )
            elif (
                len(normalized) >= 3
                and len(set(normalized)) == len(normalized)
                and iso_a
                and iso_b
            ):
                issues.append(
                    "Datum inkonsistent über alle Quellen ("
                    + ", ".join(repr(d) for d in sorted(set(normalized)))
                    + ") — bitte manuell prüfen."
                )

    sellers = {f.seller_name for f in bundle.files if f.seller_name}
    if len(sellers) > 1:
        issues.append(f"Mehrere Aussteller im Bundle erkannt: {sorted(sellers)}")

    suffixes_used = {f.info.suffix for f in bundle.files if f.info.suffix}
    if BUNDLE_ROLE_SUMMARY in roles_present:
        if "000" not in suffixes_used:
            issues.append("Summary-Rolle ohne Suffix -000.")
    if BUNDLE_ROLE_INVOICE in roles_present:
        if "001" not in suffixes_used:
            issues.append("Invoice-Rolle ohne Suffix -001.")

    bundle.validation_issues = issues
    bundle.validation_ok = len(issues) == 0


def bundle_ready_for_export(bundle: DetectedBundle) -> tuple[bool, list[str]]:
    blockers: list[str] = []
    if bundle.summary_file is None:
        blockers.append("E-SUMMARY (-000) fehlt als Basis für das Ausgabedokument.")
    if bundle.invoice_file is None:
        blockers.append(
            "E-INVOICE (-001) fehlt als Quelle für den E-Rechnungs-Datensatz."
        )
    elif not bundle.invoice_file.has_embedded_xml:
        blockers.append(
            "E-INVOICE enthält kein eingebettetes XML (kein gültiger E-Rechnungsdatensatz)."
        )
    if not bundle.roof_token:
        blockers.append(
            "Gültige Dach-Rechnungsnummer (YY-Billing-000) konnte nicht gebildet werden."
        )
    return (len(blockers) == 0, blockers)


def describe_role(role: str) -> str:
    mapping = {
        BUNDLE_ROLE_SUMMARY: "Gesamt-Summary (-000, Dach-PDF mit Summen)",
        BUNDLE_ROLE_INVOICE: "E-Rechnung (-001, mit E-Rechnungs-XML, wird zu RBU 1)",
        BUNDLE_ROLE_ATTACHMENT: "E-Attachment (-002, RBU 2)",
        BUNDLE_ROLE_STATEMENT: "E-Statement of Account (-002, RBU 3, nach Attachment)",
        BUNDLE_ROLE_UNKNOWN: "Nicht klassifiziert",
    }
    return mapping.get(role, "Nicht klassifiziert")
