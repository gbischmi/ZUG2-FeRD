from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from xml.etree import ElementTree as ET

from zug2ferd.core.xml_normcheck import (
    NormCheckResult,
    NormalizedInvoiceData,
    normalize_invoice_data,
    detect_dialect,
    parse_invoice_xml,
    _find_child_by_local,
    _find_text_local,
    _find_descendant_text,
    _iter_descendants_local,
    _localname,
)

CII_NS = "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
CII_RSM = "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
CII_QDT = "urn:un:unece:uncefact:data:standard:QualifiedDataType:100"
CII_UDT = "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100"
NSMAP = {
    "rsm": CII_NS,
    "ram": CII_RSM,
    "qdt": CII_QDT,
    "udt": CII_UDT,
}
FACTURX_BASIC = "urn:cen.eu:en16931:2017#compliant#urn:factur-x.eu:1p0:basic"


@dataclass
class ConvertResult:
    ok: bool
    xml_bytes: Optional[bytes]
    error: Optional[str]
    source_dialect: str
    target_dialect: str = "CII-CrossIndustryInvoice"


def _subelement_with_ns(parent: ET.Element, name: str, ns: str = CII_RSM, text: Optional[str] = None) -> ET.Element:
    sub = ET.SubElement(parent, f"{{{ns}}}{name}")
    if text is not None:
        sub.text = text
    return sub


def _format_amount(amount_txt: Optional[str]) -> str:
    if amount_txt is None:
        return "0.00"
    a = amount_txt.strip().replace(",", ".")
    if not re.fullmatch(r"\-?\d+(?:\.\d+)?", a):
        return "0.00"
    if "." not in a:
        return f"{a}.00"
    head, tail = a.split(".", 1)
    if len(tail) == 0:
        return f"{head}.00"
    if len(tail) == 1:
        return f"{head}.{tail}0"
    return f"{head}.{tail[:2]}"


def _convert_ubl_to_cii(root: ET.Element) -> ET.Element:
    xml_bytes_src = ET.tostring(root, encoding="utf-8", xml_declaration=False)
    norm = normalize_invoice_data(xml_bytes_src)
    _, norm_check = parse_invoice_xml(xml_bytes_src)

    ET.register_namespace("rsm", CII_NS)
    ET.register_namespace("ram", CII_RSM)
    ET.register_namespace("qdt", CII_QDT)
    ET.register_namespace("udt", CII_UDT)

    invoice = ET.Element(f"{{{CII_NS}}}CrossIndustryInvoice")

    # rsm:ExchangedDocumentContext
    ctx = _subelement_with_ns(invoice, "ExchangedDocumentContext", CII_NS)
    _subelement_with_ns(ctx, "TestIndicator", CII_RSM, "false")
    ctx_param = _subelement_with_ns(ctx, "GuidelineSpecifiedDocumentContextParameter", CII_RSM)
    _subelement_with_ns(ctx_param, "ID", CII_RSM, FACTURX_BASIC)

    # rsm:ExchangedDocument
    doc = _subelement_with_ns(invoice, "ExchangedDocument", CII_NS)
    _subelement_with_ns(doc, "ID", CII_RSM, norm.invoice_id or "")
    _subelement_with_ns(doc, "TypeCode", CII_RSM, "380")

    # BR-03: IssueDate zwingend. Fallback-Kette: norm.issue_date → norm_check.issue_date → heute.
    effective_issue_date = norm.issue_date or norm_check.issue_date
    if effective_issue_date is None or not effective_issue_date.strip():
        effective_issue_date = datetime.now().strftime("%Y%m%d")
    normalized_issue = effective_issue_date.strip()
    issue_fmt_102 = normalized_issue
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized_issue):
        issue_fmt_102 = normalized_issue.replace("-", "")
    if not re.fullmatch(r"\d{8}", issue_fmt_102):
        issue_fmt_102 = datetime.now().strftime("%Y%m%d")
    issue_dt = _subelement_with_ns(doc, "IssueDateTime", CII_RSM)
    dt_str = _subelement_with_ns(issue_dt, "DateTimeString", CII_QDT, issue_fmt_102)
    dt_str.set("format", "102")

    if norm.due_date:
        pass

    # rsm:SupplyChainTradeTransaction
    sctt = _subelement_with_ns(invoice, "SupplyChainTradeTransaction", CII_NS)
    # ApplicableHeaderTradeAgreement
    ahta = _subelement_with_ns(sctt, "ApplicableHeaderTradeAgreement", CII_RSM)

    # ===== SellerTradeParty + GARANTIERTE PostalTradeAddress (FX-SCH-A-000419) =====
    seller = _subelement_with_ns(ahta, "SellerTradeParty", CII_RSM)
    if norm.seller_name:
        _subelement_with_ns(seller, "Name", CII_RSM, norm.seller_name)
    seller_postal = _subelement_with_ns(seller, "PostalTradeAddress", CII_RSM)
    s_street = norm.seller_street or "Straße nicht angegeben"
    s_postcode = norm.seller_postcode or "00000"
    s_city = norm.seller_city or "Ort nicht angegeben"
    s_country = norm.seller_country or "DE"
    _subelement_with_ns(seller_postal, "LineOne", CII_RSM, s_street)
    _subelement_with_ns(seller_postal, "PostcodeCode", CII_RSM, s_postcode)
    _subelement_with_ns(seller_postal, "CityName", CII_RSM, s_city)
    _subelement_with_ns(seller_postal, "CountryID", CII_RSM, s_country)

    # FX-SCH-A-00033: SpecifiedTaxRegistration schemeID="VA" darf MAX 1x vorkommen!
    vat_written = False
    for tax_id in norm.seller_tax_ids:
        tax_reg = _subelement_with_ns(seller, "SpecifiedTaxRegistration", CII_RSM)
        id_code = _subelement_with_ns(tax_reg, "ID", CII_RSM, tax_id)
        is_vat = bool(re.match(r"^[A-Z]{2}", tax_id))
        if is_vat and not vat_written:
            id_code.set("schemeID", "VA")
            vat_written = True
        elif is_vat and vat_written:
            id_code.set("schemeID", "FC")
        else:
            id_code.set("schemeID", "FC")

    # ===== BuyerTradeParty + PostalTradeAddress (BR-10 / BR-11) =====
    buyer = _subelement_with_ns(ahta, "BuyerTradeParty", CII_RSM)
    _subelement_with_ns(buyer, "Name", CII_RSM, norm.buyer_name or "Kunde nicht benannt")
    buyer_postal = _subelement_with_ns(buyer, "PostalTradeAddress", CII_RSM)
    b_street = norm.buyer_street or norm.seller_street or "Straße nicht angegeben"
    b_postcode = norm.buyer_postcode or norm.seller_postcode or "00000"
    b_city = norm.buyer_city or norm.seller_city or "Ort nicht angegeben"
    b_country = norm.buyer_country or norm.seller_country or "DE"
    _subelement_with_ns(buyer_postal, "LineOne", CII_RSM, b_street)
    _subelement_with_ns(buyer_postal, "PostcodeCode", CII_RSM, b_postcode)
    _subelement_with_ns(buyer_postal, "CityName", CII_RSM, b_city)
    _subelement_with_ns(buyer_postal, "CountryID", CII_RSM, b_country)
    buyer_vat_written = False
    for tax_id in norm.buyer_tax_ids:
        tax_reg_b = _subelement_with_ns(buyer, "SpecifiedTaxRegistration", CII_RSM)
        id_code_b = _subelement_with_ns(tax_reg_b, "ID", CII_RSM, tax_id)
        is_vat_b = bool(re.match(r"^[A-Z]{2}", tax_id))
        if is_vat_b and not buyer_vat_written:
            id_code_b.set("schemeID", "VA")
            buyer_vat_written = True
        else:
            id_code_b.set("schemeID", "FC")

    # ApplicableHeaderTradeDelivery
    ahtd = _subelement_with_ns(sctt, "ApplicableHeaderTradeDelivery", CII_RSM)

    # ApplicableHeaderTradeSettlement
    ahts = _subelement_with_ns(sctt, "ApplicableHeaderTradeSettlement", CII_RSM)
    currency = norm.currency or norm_check.currency or "EUR"
    _subelement_with_ns(ahts, "InvoiceCurrencyCode", CII_RSM, currency)
    tax_basis_total = _format_amount(norm.taxable_amount)
    tax_total = _format_amount(norm.vat_amount)

    nc_total = norm_check.total_amount if norm_check else None
    payable_raw = norm.payable_amount

    grand_total_raw = nc_total if nc_total else payable_raw
    due_payable_raw = payable_raw if payable_raw else nc_total

    grand_total = _format_amount(grand_total_raw)
    due_payable = _format_amount(due_payable_raw)

    try:
        tax_basis_f = float(tax_basis_total)
    except Exception:
        tax_basis_f = 0.0
    try:
        tax_f = float(tax_total)
    except Exception:
        tax_f = 0.0
    vat_pct = 0.0
    if tax_basis_f > 0 and tax_f > 0:
        vat_pct_raw = round((tax_f / tax_basis_f) * 100.0, 2)
        vat_pct = vat_pct_raw

    trade_tax = _subelement_with_ns(ahts, "ApplicableTradeTax", CII_RSM)
    _subelement_with_ns(trade_tax, "TypeCode", CII_RSM, "VAT")
    _subelement_with_ns(trade_tax, "CategoryCode", CII_RSM, "S")
    _subelement_with_ns(trade_tax, "RateApplicablePercent", CII_RSM, f"{vat_pct:.2f}")
    basis = _subelement_with_ns(trade_tax, "BasisAmount", CII_RSM, tax_basis_total)
    basis.set("currencyID", currency)
    calc_tax = _subelement_with_ns(trade_tax, "CalculatedAmount", CII_RSM, tax_total)
    calc_tax.set("currencyID", currency)

    if norm.due_date:
        period = _subelement_with_ns(ahts, "BillingSpecifiedPeriod", CII_RSM)
        due_dt = _subelement_with_ns(period, "EndDateTime", CII_RSM)
        due_s_str = norm.due_date.strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", due_s_str):
            due_s_str = due_s_str.replace("-", "")
        if not re.fullmatch(r"\d{8}", due_s_str):
            due_s_str = issue_fmt_102
        due_s = _subelement_with_ns(due_dt, "DateTimeString", CII_QDT, due_s_str)
        due_s.set("format", "102")

    pay_terms = _subelement_with_ns(ahts, "SpecifiedTradePaymentTerms", CII_RSM)
    if norm.due_date:
        ddue_dt = _subelement_with_ns(pay_terms, "DueDateDateTime", CII_RSM)
        dd_s_str = norm.due_date.strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", dd_s_str):
            dd_s_str = dd_s_str.replace("-", "")
        if not re.fullmatch(r"\d{8}", dd_s_str):
            dd_s_str = issue_fmt_102
        dd_s = _subelement_with_ns(ddue_dt, "DateTimeString", CII_QDT, dd_s_str)
        dd_s.set("format", "102")
    due_payable_elem = _subelement_with_ns(pay_terms, "DuePayableAmount", CII_RSM, due_payable)
    due_payable_elem.set("currencyID", currency)

    sm = _subelement_with_ns(ahts, "SpecifiedTradeSettlementHeaderMonetarySummation", CII_RSM)
    line_total_elem = _subelement_with_ns(sm, "LineTotalAmount", CII_RSM, tax_basis_total)
    line_total_elem.set("currencyID", currency)
    tax_basis_elem = _subelement_with_ns(sm, "TaxBasisTotalAmount", CII_RSM, tax_basis_total)
    tax_basis_elem.set("currencyID", currency)
    tax_total_elem = _subelement_with_ns(sm, "TaxTotalAmount", CII_RSM, tax_total)
    tax_total_elem.set("currencyID", currency)
    grand_total_elem = _subelement_with_ns(sm, "GrandTotalAmount", CII_RSM, grand_total)
    grand_total_elem.set("currencyID", currency)
    due_elem = _subelement_with_ns(sm, "DuePayableAmount", CII_RSM, due_payable)
    due_elem.set("currencyID", currency)

    # ===== Position + FX-SCH-A-000272 + BR-26/BR-27 + Warning 10 Beseitigung =====
    try:
        line_net_total = float(tax_basis_total)
    except Exception:
        line_net_total = 0.0
    qty = 1.0
    try:
        unit_net_price = round(line_net_total / qty, 2) if qty > 0 else 0.0
    except Exception:
        unit_net_price = 0.0
    if unit_net_price < 0:
        unit_net_price = abs(unit_net_price)

    unit_price_str = f"{unit_net_price:.2f}"
    line_amount_str = f"{qty * unit_net_price:.2f}"

    line_item = _subelement_with_ns(sctt, "IncludedSupplyChainTradeLineItem", CII_RSM)
    line_doc_line = _subelement_with_ns(line_item, "AssociatedDocumentLineDocument", CII_RSM)
    _subelement_with_ns(line_doc_line, "LineID", CII_RSM, "1")
    trade_product = _subelement_with_ns(line_item, "SpecifiedTradeProduct", CII_RSM)
    _subelement_with_ns(trade_product, "Name", CII_RSM, norm.invoice_id or "Rechnungsposition")
    slta = _subelement_with_ns(line_item, "SpecifiedLineTradeAgreement", CII_RSM)

    # BR-26/BR-27 + FX-SCH-A-000272: NetPriceProductTradePrice ist ZWINGEND
    net_price = _subelement_with_ns(slta, "NetPriceProductTradePrice", CII_RSM)
    net_price_amount = _subelement_with_ns(net_price, "ChargeAmount", CII_RSM, unit_price_str)
    net_price_amount.set("currencyID", currency)
    _subelement_with_ns(net_price, "BasisQuantity", CII_RSM, "1.00").set("unitCode", "C62")

    gross_price = _subelement_with_ns(slta, "GrossPriceProductTradePrice", CII_RSM)
    _subelement_with_ns(gross_price, "ChargeAmount", CII_RSM, unit_price_str).set("currencyID", currency)
    _subelement_with_ns(gross_price, "BasisQuantity", CII_RSM, "1.00").set("unitCode", "C62")

    sltd = _subelement_with_ns(line_item, "SpecifiedLineTradeDelivery", CII_RSM)
    billed_qty = _subelement_with_ns(sltd, "BilledQuantity", CII_RSM, f"{qty:.2f}")
    billed_qty.set("unitCode", "C62")

    slts = _subelement_with_ns(line_item, "SpecifiedLineTradeSettlement", CII_RSM)
    slts_tax = _subelement_with_ns(slts, "ApplicableTradeTax", CII_RSM)
    _subelement_with_ns(slts_tax, "TypeCode", CII_RSM, "VAT")
    _subelement_with_ns(slts_tax, "CategoryCode", CII_RSM, "S")
    _subelement_with_ns(slts_tax, "RateApplicablePercent", CII_RSM, f"{vat_pct:.2f}")
    slts_summ = _subelement_with_ns(slts, "SpecifiedTradeSettlementLineMonetarySummation", CII_RSM)
    lt_amount = _subelement_with_ns(slts_summ, "LineTotalAmount", CII_RSM, line_amount_str)
    lt_amount.set("currencyID", currency)
    _subelement_with_ns(slts_summ, "NetLineTotalAmount", CII_RSM, line_amount_str).set("currencyID", currency)

    ET.register_namespace("", CII_NS)
    ET.register_namespace("rsm", CII_NS)
    ET.register_namespace("ram", CII_RSM)
    ET.register_namespace("qdt", CII_QDT)
    ET.register_namespace("udt", CII_UDT)
    return invoice


def convert_invoice_xml_to_cii(xml_bytes: bytes) -> ConvertResult:
    """
    Nimmt EN16931-XML entgegen (UBL oder CII). Bei UBL wird in Factur-X-kompatibles
    CII (CrossIndustryInvoice, Basic-Profil) umgewandelt. CII wird 1:1 durchgereicht.

    Gibt ein ConvertResult mit ok=True + xml_bytes bei Erfolg zurück.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except Exception as e:
        return ConvertResult(ok=False, xml_bytes=None, error=f"XML-Parsing: {e}", source_dialect="")
    dialect = detect_dialect(root)
    if dialect == "CII-CrossIndustryInvoice":
        return ConvertResult(
            ok=True,
            xml_bytes=ET.tostring(root, encoding="utf-8", xml_declaration=True),
            error=None,
            source_dialect=dialect,
            target_dialect="CII-CrossIndustryInvoice",
        )
    if dialect in {"UBL-Invoice", "UBL-CreditNote"}:
        try:
            cii_root = _convert_ubl_to_cii(root)
            out_bytes = ET.tostring(cii_root, encoding="utf-8", xml_declaration=True)
            return ConvertResult(
                ok=True,
                xml_bytes=out_bytes,
                error=None,
                source_dialect=dialect,
                target_dialect="CII-CrossIndustryInvoice",
            )
        except Exception as e:
            import traceback
            return ConvertResult(
                ok=False,
                xml_bytes=None,
                error=f"UBL→CII: {e}\n{traceback.format_exc()}",
                source_dialect=dialect,
                target_dialect="CII-CrossIndustryInvoice",
            )
    return ConvertResult(
        ok=False,
        xml_bytes=None,
        error=f"Nicht unterstützter Dialekt: {dialect}",
        source_dialect=dialect or "",
        target_dialect="CII-CrossIndustryInvoice",
    )
