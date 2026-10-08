from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree as ET

_CUSTOMIZATION_EN16931_UBLX = "urn:cen.eu:en16931:2017"
_CUSTOMIZATION_EN16931_CII = (
    "urn:cen.eu:en16931:2017#compliant#urn:factur-x.eu:1p0:basic"
)
_ZF_PROFILE_BASIC = "urn:factur-x.eu:1p0:basic"
_ZF_PROFILE_COMFORT = "urn:factur-x.eu:1p0:comfort"
_ZF_PROFILE_EXTENDED = "urn:factur-x.eu:1p0:extended"
_XR_PROFILE = "urn:fdc:peppol.eu:2017:poacc:billing:01:1.0"

_UBL_NS = "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
_UBL_CAC = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
_UBL_CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"

_CII_NS = "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
_CII_RSM = (
    "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
)
_CII_QDT = "urn:un:unece:uncefact:data:standard:QualifiedDataType:100"
_CII_UDT = "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100"


@dataclass(frozen=True)
class NormCheckResult:
    ok: bool
    root_tag: str
    dialect: str
    customization_id: Optional[str]
    profile_id: Optional[str]
    invoice_id: Optional[str]
    issue_date: Optional[str]
    seller_name: Optional[str]
    buyer_name: Optional[str]
    total_amount: Optional[str]
    currency: Optional[str]
    missing_required: tuple[str, ...]
    warnings: tuple[str, ...]
    error: Optional[str]

    @property
    def is_en16931(self) -> bool:
        if self.customization_id is None:
            return False
        return self.customization_id.startswith(
            _CUSTOMIZATION_EN16931_UBLX
        ) or self.customization_id.startswith(_CUSTOMIZATION_EN16931_CII)


@dataclass
class NormalizedInvoiceData:
    xml_bytes: bytes
    dialect: str
    seller_name: Optional[str]
    seller_street: Optional[str]
    seller_postcode: Optional[str]
    seller_city: Optional[str]
    seller_country: Optional[str]
    seller_tax_ids: list[str] = field(default_factory=list)
    buyer_name: Optional[str] = None
    buyer_street: Optional[str] = None
    buyer_postcode: Optional[str] = None
    buyer_city: Optional[str] = None
    buyer_country: Optional[str] = None
    buyer_tax_ids: list[str] = field(default_factory=list)
    invoice_id: Optional[str] = None
    issue_date: Optional[str] = None
    due_date: Optional[str] = None
    currency: Optional[str] = None
    payable_amount: Optional[str] = None
    vat_amount: Optional[str] = None
    taxable_amount: Optional[str] = None


def _localname(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _find_child_by_local(parent: ET.Element, names: set[str]) -> Optional[ET.Element]:
    for child in list(parent):
        if _localname(child.tag) in names:
            return child
    return None


def _find_text_local(parent: ET.Element, names: set[str]) -> Optional[str]:
    node = _find_child_by_local(parent, names)
    if node is not None and node.text and node.text.strip():
        return node.text.strip()
    return None


def _find_descendant_text(root: ET.Element, names: set[str]) -> Optional[str]:
    for node in root.iter():
        if _localname(node.tag) in names and node.text and node.text.strip():
            return node.text.strip()
    return None


def _iter_descendants_local(root: ET.Element, names: set[str]):
    for node in root.iter():
        if _localname(node.tag) in names:
            yield node


def _looks_like_amount(txt: str) -> bool:
    return bool(re.fullmatch(r"\-?\d+(?:\.\d+)?", txt.strip()))


def _normalize_date_to_iso(txt: str) -> Optional[str]:
    """
    Akzeptiert die üblichen E-Rechnungs-Datumsformate und gibt das
    kanonische ISO-Format YYYY-MM-DD zurück. Unterstützt:
        YYYYMMDD       (UN/EDIFACT Format 102, CII-Default)
        YYYY-MM-DD     (ISO)
        DD.MM.YYYY     (DE-DE-Default im PDF-Text)
        YYYY/MM/DD     (internationale Variante)
    Gibt None zurück, wenn das Format nicht erkannt oder das Datum invalide ist.
    """
    s = txt.strip()
    # Kürzen auf die ersten 10 Zeichen bei Zeitstempel (z. B. "2026-09-30T12:00:00Z)
    core = s[:10] if len(s) >= 10 else s
    m1 = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", core)
    if m1:
        y, mo, d = m1.group(1), m1.group(2), m1.group(3)
        try:
            return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
        except ValueError:
            return None
    m2 = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", s)
    if m2:
        y, mo, d = m2.group(1), m2.group(2), m2.group(3)
        try:
            return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
        except ValueError:
            return None
    m3 = re.fullmatch(r"(\d{2})\.(\d{2})\.(\d{4})", s)
    if m3:
        d, mo, y = m3.group(1), m3.group(2), m3.group(3)
        try:
            return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
        except ValueError:
            return None
    m4 = re.fullmatch(r"(\d{4})/(\d{2})/(\d{2})", s)
    if m4:
        y, mo, d = m4.group(1), m4.group(2), m4.group(3)
        try:
            return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
        except ValueError:
            return None
    return None


def _looks_like_iso_date(txt: str) -> bool:
    return _normalize_date_to_iso(txt) is not None


def _looks_like_currency(txt: str) -> bool:
    return bool(re.fullmatch(r"[A-Z]{3}", txt.strip()))


def _looks_like_tax_id(txt: str) -> bool:
    t = txt.strip()
    if len(t) < 5:
        return False
    return True


def detect_dialect(root: ET.Element) -> str:
    tag = _localname(root.tag)
    if tag == "Invoice":
        return "UBL-Invoice"
    if tag == "CreditNote":
        return "UBL-CreditNote"
    if tag == "CrossIndustryInvoice":
        return "CII-CrossIndustryInvoice"
    return tag


def parse_invoice_xml(xml_bytes: bytes) -> tuple[Optional[ET.Element], NormCheckResult]:
    try:
        root = ET.fromstring(xml_bytes)
    except Exception as e:
        empty = NormCheckResult(
            ok=False,
            root_tag="",
            dialect="",
            customization_id=None,
            profile_id=None,
            invoice_id=None,
            issue_date=None,
            seller_name=None,
            buyer_name=None,
            total_amount=None,
            currency=None,
            missing_required=("XML-Parsing",),
            warnings=(),
            error=str(e),
        )
        return None, empty

    dialect = detect_dialect(root)
    customization_id = _find_text_local(root, {"CustomizationID"})
    profile_id = _find_text_local(root, {"ProfileID"})

    invoice_id = _find_text_local(root, {"ID"})
    issue_date = _find_text_local(
        root, {"IssueDate", "DateTimeString", "CreationDateAndTime"}
    )
    currency = _find_text_local(root, {"DocumentCurrencyCode", "InvoiceCurrencyCode"})

    if dialect == "CII-CrossIndustryInvoice":
        ex_doc = _find_child_by_local(root, {"ExchangedDocument"})
        if ex_doc is not None:
            if not invoice_id:
                invoice_id = _find_text_local(ex_doc, {"ID"})
            if not issue_date:
                issue_dt_elem = _find_child_by_local(
                    ex_doc, {"IssueDateTime", "CreationDateTime"}
                )
                if issue_dt_elem is not None:
                    issue_date = _find_text_local(issue_dt_elem, {"DateTimeString"})
        if not currency:
            for settle in _iter_descendants_local(
                root, {"ApplicableHeaderTradeSettlement"}
            ):
                c = _find_text_local(settle, {"InvoiceCurrencyCode", "TaxCurrencyCode"})
                if c:
                    currency = c
                    break

    if dialect in {"UBL-Invoice", "UBL-CreditNote"}:
        seller_party = None
        for tag_name in {
            "AccountingSupplierParty",
            "SellerSupplierParty",
            "SellerTradeParty",
        }:
            container = _find_child_by_local(root, {tag_name})
            if container is not None:
                seller_party = _find_child_by_local(container, {"Party"})
                if seller_party is not None:
                    break
        seller_name = None
        if seller_party is not None:
            pn = _find_child_by_local(seller_party, {"PartyName"})
            if pn is not None:
                seller_name = _find_text_local(pn, {"Name"})
            if not seller_name:
                ple = _find_child_by_local(seller_party, {"PartyLegalEntity"})
                if ple is not None:
                    seller_name = _find_text_local(ple, {"RegistrationName"})

        buyer_party = None
        for tag_name in {
            "AccountingCustomerParty",
            "BuyerCustomerParty",
            "BuyerTradeParty",
        }:
            container = _find_child_by_local(root, {tag_name})
            if container is not None:
                buyer_party = _find_child_by_local(container, {"Party"})
                if buyer_party is not None:
                    break
        buyer_name = None
        if buyer_party is not None:
            pn = _find_child_by_local(buyer_party, {"PartyName"})
            if pn is not None:
                buyer_name = _find_text_local(pn, {"Name"})
            if not buyer_name:
                ple = _find_child_by_local(buyer_party, {"PartyLegalEntity"})
                if ple is not None:
                    buyer_name = _find_text_local(ple, {"RegistrationName"})

        total_amount = None
        lmt = _find_child_by_local(root, {"LegalMonetaryTotal"})
        if lmt is not None:
            total_amount = _find_text_local(
                lmt, {"PayableAmount", "TaxInclusiveAmount"}
            )
    elif dialect == "CII-CrossIndustryInvoice":
        ex_doc = _find_child_by_local(root, {"ExchangedDocument"})
        seller_name = None
        buyer_name = None
        total_amount = None
        if ex_doc is not None:
            parties_root = None
            for tag_name in {
                "SupplyChainTradeTransaction",
                "ApplicableHeaderTradeSettlement",
            }:
                parties_root = _find_child_by_local(root, {tag_name})
                if parties_root is not None:
                    break
            if parties_root is not None:
                settlement = _find_child_by_local(
                    parties_root, {"ApplicableHeaderTradeSettlement"}
                )
                if settlement is None:
                    settlement = parties_root
                for tp_node in _iter_descendants_local(
                    parties_root, {"TradeParty", "SellerTradeParty", "BuyerTradeParty"}
                ):
                    local_tag = _localname(tp_node.tag)
                    role = _find_descendant_text(tp_node, {"RoleCode", "TypeCode"})
                    nm = _find_descendant_text(tp_node, {"Name"})
                    is_seller = (role in {"SU", "SELLER"}) or (
                        local_tag == "SellerTradeParty"
                    )
                    is_buyer = (role in {"BY", "BUYER"}) or (
                        local_tag == "BuyerTradeParty"
                    )
                    if is_seller:
                        if not seller_name:
                            seller_name = nm
                    elif is_buyer:
                        if not buyer_name:
                            buyer_name = nm
                    else:
                        if not seller_name:
                            seller_name = nm
                        elif not buyer_name:
                            buyer_name = nm
                if settlement is not None:
                    total_amount = _find_descendant_text(
                        settlement, {"GrandTotalAmount", "DuePayableAmount"}
                    )
    else:
        seller_name = _find_descendant_text(root, {"Name", "RegistrationName"})
        buyer_name = None
        total_amount = None

    missing: list[str] = []
    warnings: list[str] = []

    normalized_issue_date: Optional[str] = None
    if issue_date:
        normalized_issue_date = _normalize_date_to_iso(issue_date)

    if not invoice_id:
        missing.append("InvoiceID")
    if not normalized_issue_date:
        missing.append("IssueDate (ISO YYYY-MM-DD)")
    if not seller_name:
        missing.append("SellerName")
    if not buyer_name:
        warnings.append("BuyerName nicht zweifelsfrei extrahierbar")
    if not currency or not _looks_like_currency(currency):
        missing.append("DocumentCurrencyCode (ISO 4217)")
    if not total_amount or not _looks_like_amount(total_amount):
        missing.append("PayableAmount / GrandTotalAmount")

    is_16931 = False
    if customization_id is not None:
        if customization_id.startswith(
            _CUSTOMIZATION_EN16931_UBLX
        ) or customization_id.startswith(_CUSTOMIZATION_EN16931_CII):
            is_16931 = True
    if profile_id and (
        profile_id == _XR_PROFILE
        or profile_id in {_ZF_PROFILE_BASIC, _ZF_PROFILE_COMFORT, _ZF_PROFILE_EXTENDED}
    ):
        if not is_16931:
            warnings.append(
                f"ProfileID={profile_id} ohne explizite EN16931-CustomizationID"
            )

    ok = len(missing) == 0
    result = NormCheckResult(
        ok=ok,
        root_tag=_localname(root.tag),
        dialect=dialect,
        customization_id=customization_id,
        profile_id=profile_id,
        invoice_id=invoice_id,
        issue_date=normalized_issue_date,
        seller_name=seller_name,
        buyer_name=buyer_name,
        total_amount=total_amount,
        currency=currency,
        missing_required=tuple(missing),
        warnings=tuple(warnings),
        error=None,
    )
    return root, result


def check_xml_file(xml_path: Path) -> NormCheckResult:
    try:
        xml_bytes = xml_path.read_bytes()
    except Exception as e:
        return NormCheckResult(
            ok=False,
            root_tag="",
            dialect="",
            customization_id=None,
            profile_id=None,
            invoice_id=None,
            issue_date=None,
            seller_name=None,
            buyer_name=None,
            total_amount=None,
            currency=None,
            missing_required=("Datei-Lesefehler",),
            warnings=(),
            error=str(e),
        )
    _, result = parse_invoice_xml(xml_bytes)
    return result


def normalize_invoice_data(xml_bytes: bytes) -> NormalizedInvoiceData:
    root, check = parse_invoice_xml(xml_bytes)
    data = NormalizedInvoiceData(
        xml_bytes=xml_bytes,
        dialect=check.dialect,
        seller_name=check.seller_name,
        seller_street=None,
        seller_postcode=None,
        seller_city=None,
        seller_country=None,
    )
    data.invoice_id = check.invoice_id
    data.issue_date = check.issue_date
    data.currency = check.currency
    data.buyer_name = check.buyer_name

    if root is None:
        return data

    if check.dialect in {"UBL-Invoice", "UBL-CreditNote"}:
        seller_container = None
        for tag_name in {
            "AccountingSupplierParty",
            "SellerSupplierParty",
            "SellerTradeParty",
        }:
            seller_container = _find_child_by_local(root, {tag_name})
            if seller_container is not None:
                break
        seller_party = None
        if seller_container is not None:
            seller_party = _find_child_by_local(seller_container, {"Party"})
        if seller_party is not None:
            postal = _find_child_by_local(
                seller_party, {"PostalAddress", "SellerPostalAddress"}
            )
            if postal is not None:
                data.seller_street = _find_text_local(
                    postal, {"StreetName", "LineOne", "Street"}
                )
                data.seller_postcode = _find_text_local(
                    postal, {"PostcodeCode", "PostalZone", "Postcode"}
                )
                data.seller_city = _find_text_local(postal, {"CityName", "City"})
                country = _find_child_by_local(postal, {"Country"})
                if country is not None:
                    data.seller_country = _find_text_local(
                        country, {"IdentificationCode"}
                    )
            for tax_node in _iter_descendants_local(seller_party, {"PartyTaxScheme"}):
                cid = _find_text_local(tax_node, {"CompanyID", "ID"})
                if cid and _looks_like_tax_id(cid):
                    data.seller_tax_ids.append(cid)
            for ple in _iter_descendants_local(seller_party, {"PartyLegalEntity"}):
                cid = _find_text_local(ple, {"CompanyID"})
                if cid and cid not in data.seller_tax_ids and _looks_like_tax_id(cid):
                    data.seller_tax_ids.append(cid)

        buyer_container = None
        for tag_name in {
            "AccountingCustomerParty",
            "BuyerCustomerParty",
            "CustomerParty",
        }:
            buyer_container = _find_child_by_local(root, {tag_name})
            if buyer_container is not None:
                break
        buyer_party = None
        if buyer_container is not None:
            buyer_party = _find_child_by_local(buyer_container, {"Party"})
        if buyer_party is not None:
            if data.buyer_name is None:
                data.buyer_name = _find_descendant_text(buyer_party, {"Name", "RegistrationName"})
            postal_b = _find_child_by_local(
                buyer_party, {"PostalAddress", "BuyerPostalAddress"}
            )
            if postal_b is not None:
                data.buyer_street = _find_text_local(
                    postal_b, {"StreetName", "LineOne", "Street"}
                )
                data.buyer_postcode = _find_text_local(
                    postal_b, {"PostcodeCode", "PostalZone", "Postcode"}
                )
                data.buyer_city = _find_text_local(postal_b, {"CityName", "City"})
                country_b = _find_child_by_local(postal_b, {"Country"})
                if country_b is not None:
                    data.buyer_country = _find_text_local(
                        country_b, {"IdentificationCode"}
                    )
            for tax_node_b in _iter_descendants_local(buyer_party, {"PartyTaxScheme"}):
                cid_b = _find_text_local(tax_node_b, {"CompanyID", "ID"})
                if cid_b and _looks_like_tax_id(cid_b):
                    data.buyer_tax_ids.append(cid_b)
            for ple_b in _iter_descendants_local(buyer_party, {"PartyLegalEntity"}):
                cid_b = _find_text_local(ple_b, {"CompanyID"})
                if cid_b and cid_b not in data.buyer_tax_ids and _looks_like_tax_id(cid_b):
                    data.buyer_tax_ids.append(cid_b)

        data.due_date = _find_text_local(root, {"DueDate"})

        lmt = _find_child_by_local(root, {"LegalMonetaryTotal"})
        if lmt is not None:
            data.payable_amount = _find_text_local(lmt, {"PayableAmount"})
            data.taxable_amount = _find_text_local(
                lmt, {"TaxExclusiveAmount", "LineExtensionAmount"}
            )

        tax_total = _find_child_by_local(root, {"TaxTotal"})
        if tax_total is not None:
            data.vat_amount = _find_text_local(tax_total, {"TaxAmount"})

    elif check.dialect == "CII-CrossIndustryInvoice":
        trade_settlement = None
        for cand in _iter_descendants_local(root, {"ApplicableHeaderTradeSettlement"}):
            trade_settlement = cand
            break
        if trade_settlement is not None:
            data.payable_amount = _find_descendant_text(
                trade_settlement, {"DuePayableAmount"}
            )
            data.vat_amount = _find_descendant_text(
                trade_settlement, {"TotalVATAmount"}
            )
            data.taxable_amount = _find_descendant_text(
                trade_settlement, {"BasisAmount"}
            )
        parties_container = None
        for tag_name in {
            "SupplyChainTradeTransaction",
            "ApplicableHeaderTradeDelivery",
        }:
            parties_container = _find_child_by_local(root, {tag_name})
            if parties_container is not None:
                break
        if parties_container is not None:
            for tp in _iter_descendants_local(parties_container, {"TradeParty"}):
                nm = _find_descendant_text(tp, {"Name", "RegistrationName"})
                postal = _find_child_by_local(
                    tp, {"PostalTradeAddress", "PostalAddress"}
                )
                if (
                    postal is not None
                    and data.seller_street is None
                    and nm == check.seller_name
                ):
                    data.seller_street = _find_text_local(
                        postal, {"StreetName", "LineOne"}
                    )
                    data.seller_postcode = _find_text_local(
                        postal, {"PostcodeCode", "PostalZone"}
                    )
                    data.seller_city = _find_text_local(postal, {"CityName", "City"})
                    country = _find_child_by_local(postal, {"Country"})
                    if country is not None:
                        data.seller_country = _find_text_local(country, {"ID"})
                if (
                    postal is not None
                    and data.buyer_street is None
                    and nm == check.buyer_name
                ):
                    data.buyer_street = _find_text_local(
                        postal, {"StreetName", "LineOne"}
                    )
                    data.buyer_postcode = _find_text_local(
                        postal, {"PostcodeCode", "PostalZone"}
                    )
                    data.buyer_city = _find_text_local(postal, {"CityName", "City"})
                    country_b = _find_child_by_local(postal, {"Country"})
                    if country_b is not None:
                        data.buyer_country = _find_text_local(country_b, {"ID"})
                for tax_container in _iter_descendants_local(
                    tp, {"SpecifiedTaxRegistration", "TaxRegistration"}
                ):
                    tid = _find_text_local(tax_container, {"ID"})
                    if tid and _looks_like_tax_id(tid):
                        if nm == check.seller_name and tid not in data.seller_tax_ids:
                            data.seller_tax_ids.append(tid)
                        if nm == check.buyer_name and tid not in data.buyer_tax_ids:
                            data.buyer_tax_ids.append(tid)
    return data
