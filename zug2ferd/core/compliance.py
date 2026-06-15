from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree as ET


@dataclass(frozen=True)
class SellerData:
    name: Optional[str]
    address: Optional[str]
    tax_ids: tuple[str, ...]


@dataclass(frozen=True)
class ComplianceResult:
    ok: bool
    found_name: bool
    found_address: bool
    found_tax_id: bool
    missing_fields: tuple[str, ...]
    used_stationery: bool
    error: Optional[str]


@dataclass(frozen=True)
class InvoicePayload:
    xml_bytes: bytes
    seller: SellerData


def extract_invoice_payload(invoice_pdf_path: Path) -> InvoicePayload:
    xml_bytes = extract_zugferd_xml_from_pdf(invoice_pdf_path)
    seller = parse_seller_data(xml_bytes)
    return InvoicePayload(xml_bytes=xml_bytes, seller=seller)


def extract_zugferd_xml_from_pdf(pdf_path: Path) -> bytes:
    try:
        from pypdf import PdfReader
    except Exception as e:
        raise RuntimeError("pypdf nicht verfügbar") from e

    reader = PdfReader(str(pdf_path))

    if hasattr(reader, "attachments"):
        attachments = getattr(reader, "attachments")
        if isinstance(attachments, dict):
            candidates = []
            for name, obj in attachments.items():
                lname = str(name).lower()
                if "factur-x" in lname or "zugferd" in lname or lname.endswith(".xml"):
                    candidates.append((lname, obj))
            if not candidates:
                candidates = [(str(k).lower(), v) for k, v in attachments.items()]
            for _, obj in candidates:
                data = _attachment_to_bytes(obj)
                if data and _looks_like_xml(data):
                    return data

    root = reader.trailer.get("/Root")
    if root is not None:
        names = root.get("/Names") if hasattr(root, "get") else None
        if names is not None and hasattr(names, "get"):
            embedded = names.get("/EmbeddedFiles")
            if embedded is not None and hasattr(embedded, "get"):
                names_arr = embedded.get("/Names", [])
                for i in range(0, len(names_arr) - 1, 2):
                    fname = str(names_arr[i])
                    spec = names_arr[i + 1]
                    ef = spec.get("/EF") if hasattr(spec, "get") else None
                    fobj = ef.get("/F") if ef is not None and hasattr(ef, "get") else None
                    data = _indirect_to_bytes(fobj)
                    if not data:
                        continue
                    if _looks_like_xml(data) and (fname.lower().endswith(".xml") or "factur-x" in fname.lower()):
                        return data

    raise RuntimeError("Keine eingebettete ZUGFeRD/Factur-X XML gefunden")


def parse_seller_data(xml_bytes: bytes) -> SellerData:
    try:
        root = ET.fromstring(xml_bytes)
    except Exception as e:
        raise RuntimeError("XML konnte nicht geparst werden") from e

    seller = _find_first_by_localname(root, {"SellerTradeParty", "SellerSupplierParty", "AccountingSupplierParty"})
    name = None
    address = None
    tax_ids: list[str] = []

    if seller is not None:
        name = _find_text_anywhere(seller, {"Name", "RegistrationName"})
        addr_node = _find_first_by_localname(
            seller,
            {"PostalTradeAddress", "PostalAddress", "SellerPostalAddress", "PostPostalAddress"},
        )
        if addr_node is not None:
            street = _find_text_anywhere(addr_node, {"StreetName", "LineOne", "Street"})
            postcode = _find_text_anywhere(addr_node, {"PostcodeCode", "PostalZone", "Postcode"})
            city = _find_text_anywhere(addr_node, {"CityName", "City"})
            parts = [p for p in [street, postcode, city] if p]
            address = " ".join(parts) if parts else None

    for node in root.iter():
        ln = _localname(node.tag)
        if ln in {"VATRegistrationID", "TaxRegistrationID", "CompanyID"}:
            if node.text and node.text.strip():
                tax_ids.append(node.text.strip())
        if ln == "ID" and node.text and node.text.strip():
            txt = node.text.strip()
            if _looks_like_tax_id(txt):
                tax_ids.append(txt)

    tax_ids = _unique_preserve_order([t for t in tax_ids if t])
    return SellerData(name=name, address=address, tax_ids=tuple(tax_ids))


def extract_pdf_text_first_page(pdf_path: Path) -> str:
    try:
        import pdfplumber
    except Exception:
        pdfplumber = None

    if pdfplumber is not None:
        with pdfplumber.open(str(pdf_path)) as pdf:
            if not pdf.pages:
                return ""
            text = pdf.pages[0].extract_text() or ""
            return text

    try:
        from pypdf import PdfReader
    except Exception as e:
        raise RuntimeError("pypdf nicht verfügbar") from e

    reader = PdfReader(str(pdf_path))
    if not reader.pages:
        return ""
    return reader.pages[0].extract_text() or ""


def run_ustg_check(
    invoice_pdf_path: Path,
    stationery_pdf_path: Optional[Path],
    ustg_check_enabled: bool,
) -> ComplianceResult:
    try:
        payload = extract_invoice_payload(invoice_pdf_path)
    except Exception as e:
        used_stationery = stationery_pdf_path is not None and stationery_pdf_path.exists()
        return ComplianceResult(
            ok=False,
            found_name=False,
            found_address=False,
            found_tax_id=False,
            missing_fields=("XML/Einlesen",),
            used_stationery=used_stationery,
            error=str(e),
        )

    return run_ustg_check_with_payload(payload, invoice_pdf_path, stationery_pdf_path, ustg_check_enabled)


def run_ustg_check_with_payload(
    payload: InvoicePayload,
    invoice_pdf_path: Path,
    stationery_pdf_path: Optional[Path],
    ustg_check_enabled: bool,
) -> ComplianceResult:
    used_stationery = stationery_pdf_path is not None and stationery_pdf_path.exists()
    try:
        inv_text = extract_pdf_text_first_page(invoice_pdf_path)
        st_text = extract_pdf_text_first_page(stationery_pdf_path) if used_stationery else ""
        combined = inv_text + "\n" + st_text
    except Exception as e:
        return ComplianceResult(
            ok=False,
            found_name=False,
            found_address=False,
            found_tax_id=False,
            missing_fields=("PDF/Text",),
            used_stationery=used_stationery,
            error=str(e),
        )

    seller = payload.seller
    found_name = _match_name(seller.name, combined)
    found_address = _match_address(seller.address, combined)
    found_tax_id = _match_any_id(seller.tax_ids, combined)

    missing = []
    if not found_name:
        missing.append("Firmenname")
    if not found_address:
        missing.append("Adresse")
    if not found_tax_id:
        missing.append("USt-IdNr./Steuernummer")

    ok = len(missing) == 0
    return ComplianceResult(
        ok=ok,
        found_name=found_name,
        found_address=found_address,
        found_tax_id=found_tax_id,
        missing_fields=tuple(missing),
        used_stationery=used_stationery,
        error=None if ok or ustg_check_enabled else None,
    )


def choose_primary_tax_id(tax_ids: tuple[str, ...]) -> Optional[str]:
    for tid in tax_ids:
        if tid and tid.strip():
            return tid.strip()
    return None


def _attachment_to_bytes(obj) -> Optional[bytes]:
    if obj is None:
        return None
    if isinstance(obj, (bytes, bytearray)):
        return bytes(obj)
    data = _indirect_to_bytes(obj)
    if data:
        return data
    get_data = getattr(obj, "get_data", None)
    if callable(get_data):
        try:
            return get_data()
        except Exception:
            return None
    return None


def _indirect_to_bytes(obj) -> Optional[bytes]:
    if obj is None:
        return None
    get_data = getattr(obj, "get_data", None)
    if callable(get_data):
        try:
            return get_data()
        except Exception:
            return None
    get_object = getattr(obj, "get_object", None)
    if callable(get_object):
        try:
            real = get_object()
        except Exception:
            return None
        stream_data = getattr(real, "get_data", None)
        if callable(stream_data):
            try:
                return stream_data()
            except Exception:
                return None
    return None


def _looks_like_xml(data: bytes) -> bool:
    head = data.lstrip()[:20].lower()
    return head.startswith(b"<") and (b"xml" in head or b"invoice" in head or b"crossindustry" in head)


def _localname(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _find_first_by_localname(root: ET.Element, names: set[str]) -> Optional[ET.Element]:
    for node in root.iter():
        if _localname(node.tag) in names:
            return node
    return None


def _find_text_anywhere(root: ET.Element, names: set[str]) -> Optional[str]:
    for node in root.iter():
        if _localname(node.tag) in names and node.text and node.text.strip():
            return node.text.strip()
    return None


def _unique_preserve_order(items: list[str]) -> list[str]:
    out: list[str] = []
    seen = set()
    for it in items:
        key = it.strip()
        if not key:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _normalize_text(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def _match_name(name: Optional[str], pdf_text: str) -> bool:
    if not name:
        return False
    hay = pdf_text.lower()
    tokens = [t for t in re.split(r"\s+", re.sub(r"[^\wäöüÄÖÜß\- ]+", " ", name).strip()) if len(t) >= 3]
    if not tokens:
        return False
    hit = 0
    for t in tokens:
        if t.lower() in hay:
            hit += 1
    return hit >= max(1, len(tokens) // 2 + 1)


def _match_address(address: Optional[str], pdf_text: str) -> bool:
    if not address:
        return False
    hay = pdf_text.lower()
    tokens = [t for t in re.split(r"\s+", re.sub(r"[^\wäöüÄÖÜß\- ]+", " ", address).strip()) if len(t) >= 3]
    if len(tokens) < 2:
        return False
    hit = 0
    for t in tokens:
        if t.lower() in hay:
            hit += 1
    return hit >= max(2, len(tokens) // 2)


def _match_any_id(ids: tuple[str, ...], pdf_text: str) -> bool:
    if not ids:
        return False
    hay = _normalize_text(pdf_text)
    for tid in ids:
        if not tid:
            continue
        if _normalize_text(tid) and _normalize_text(tid) in hay:
            return True
    return False


def _looks_like_tax_id(s: str) -> bool:
    n = _normalize_text(s)
    if len(n) < 8:
        return False
    return True
