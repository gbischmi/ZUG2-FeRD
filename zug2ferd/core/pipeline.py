from __future__ import annotations

import copy
import datetime as dt
import os
import re
import subprocess
import uuid
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Optional

from zug2ferd import load_sysctl
from zug2ferd.core.xml_normcheck import (
    NormCheckResult,
    parse_invoice_xml,
)


@dataclass(frozen=True)
class BuildResult:
    ok: bool
    output_pdf_path: Optional[Path]
    mustang_ok: bool
    mustang_stdout: str
    mustang_stderr: str
    error: Optional[str]


@dataclass(frozen=True)
class XmlAssembleResult:
    ok: bool
    output_pdf_path: Optional[Path]
    normcheck: Optional[NormCheckResult]
    mustang_ok: bool
    mustang_stdout: str
    mustang_stderr: str
    error: Optional[str]


def is_image_path(p: Path) -> bool:
    return p.suffix.lower() in {".png", ".jpg", ".jpeg"}


def is_pdf_path(p: Path) -> bool:
    return p.suffix.lower() == ".pdf"


def convert_image_to_pdf_bytes(image_path: Path) -> bytes:
    try:
        from PIL import Image
    except Exception as e:
        raise RuntimeError("Pillow nicht verfügbar") from e

    with Image.open(image_path) as img:
        img = img.convert("RGB")
        a4_px = (2480, 3508)
        img.thumbnail(a4_px)

        canvas = Image.new("RGB", a4_px, (255, 255, 255))
        x = (a4_px[0] - img.size[0]) // 2
        y = (a4_px[1] - img.size[1]) // 2
        canvas.paste(img, (x, y))

        buf = BytesIO()
        canvas.save(buf, format="PDF")
        return buf.getvalue()


def stamp_pdf_underlay(
    input_pdf: Path,
    stationery_pdf: Optional[Path],
    apply_all_pages: bool,
) -> bytes:
    try:
        from pypdf import PdfReader, PdfWriter
    except Exception as e:
        raise RuntimeError("pypdf nicht verfügbar") from e

    if stationery_pdf is None or not stationery_pdf.exists():
        return input_pdf.read_bytes()

    reader = PdfReader(str(input_pdf))
    st_reader = PdfReader(str(stationery_pdf))
    if not st_reader.pages:
        return input_pdf.read_bytes()

    st_page0 = st_reader.pages[0]
    writer = PdfWriter()

    for idx, page in enumerate(reader.pages):
        if apply_all_pages or idx == 0:
            bg = copy.deepcopy(st_page0)
            bg.merge_page(page)
            writer.add_page(bg)
        else:
            writer.add_page(page)

    if reader.metadata:
        try:
            meta = {k: v for k, v in reader.metadata.items() if v is not None}
            if meta:
                writer.add_metadata(meta)
        except Exception:
            pass

    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def merge_pdfs_to_file(output_pdf_path: Path, pdf_bytes_list: list[bytes]) -> None:
    try:
        from pypdf import PdfReader, PdfWriter
    except Exception as e:
        raise RuntimeError("pypdf nicht verfügbar") from e

    writer = PdfWriter()
    for data in pdf_bytes_list:
        r = PdfReader(BytesIO(data))
        for p in r.pages:
            writer.add_page(p)

    with open(output_pdf_path, "wb") as f:
        writer.write(f)


def attach_facturx_xml(output_pdf_path: Path, xml_bytes: bytes, attachment_name: str = "factur-x.xml") -> None:
    try:
        from pypdf import PdfReader, PdfWriter
        from pypdf.generic import (
            ArrayObject,
            DecodedStreamObject,
            DictionaryObject,
            NameObject,
            TextStringObject,
        )
    except Exception as e:
        raise RuntimeError("pypdf nicht verfügbar") from e

    src_meta = _extract_source_pdf_metadata(output_pdf_path)

    reader = PdfReader(str(output_pdf_path))
    writer = PdfWriter()
    for p in reader.pages:
        writer.add_page(p)

    effective_info = {}
    if src_meta["info"]:
        effective_info.update(src_meta["info"])
    if reader.metadata:
        try:
            for k, v in reader.metadata.items():
                if v is None:
                    continue
                ks = str(k)
                if ks not in effective_info or not effective_info[ks]:
                    effective_info[ks] = v
        except Exception:
            pass

    if src_meta.get("author"):
        effective_info["/Author"] = src_meta["author"]
    if src_meta.get("title"):
        effective_info["/Title"] = src_meta["title"]
    if src_meta.get("subject"):
        effective_info["/Subject"] = src_meta["subject"]
    if src_meta.get("creator_tool"):
        effective_info["/Creator"] = src_meta["creator_tool"]
    if src_meta.get("producer"):
        effective_info["/Producer"] = src_meta["producer"]
    if "/CreationDate" not in effective_info:
        pdf_date = _iso_date_to_pdf_date(src_meta.get("create_date"))
        if pdf_date:
            effective_info["/CreationDate"] = pdf_date
    if "/ModDate" not in effective_info:
        pdf_date = _iso_date_to_pdf_date(src_meta.get("modify_date"))
        if pdf_date:
            effective_info["/ModDate"] = pdf_date

    if effective_info:
        try:
            writer.add_metadata(effective_info)
        except Exception:
            pass

    ef_stream = DecodedStreamObject()
    ef_stream.set_data(xml_bytes)
    ef_stream.update(
        {
            NameObject("/Type"): NameObject("/EmbeddedFile"),
            NameObject("/Subtype"): NameObject("/text#2Fxml"),
        }
    )
    ef_ref = writer._add_object(ef_stream)

    filespec = DictionaryObject()
    filespec.update(
        {
            NameObject("/Type"): NameObject("/Filespec"),
            NameObject("/F"): TextStringObject(attachment_name),
            NameObject("/UF"): TextStringObject(attachment_name),
            NameObject("/Desc"): TextStringObject("Factur-X/ZUGFeRD XML"),
            NameObject("/AFRelationship"): NameObject("/Data"),
            NameObject("/EF"): DictionaryObject({NameObject("/F"): ef_ref, NameObject("/UF"): ef_ref}),
        }
    )
    fs_ref = writer._add_object(filespec)

    root = writer._root_object
    names = root.get("/Names")
    if names is None:
        names = DictionaryObject()
        root[NameObject("/Names")] = names

    embedded = names.get("/EmbeddedFiles")
    if embedded is None:
        embedded = DictionaryObject()
        names[NameObject("/EmbeddedFiles")] = embedded

    arr = embedded.get("/Names")
    if arr is None:
        arr = ArrayObject()
        embedded[NameObject("/Names")] = arr

    arr.append(TextStringObject(attachment_name))
    arr.append(fs_ref)

    af = root.get("/AF")
    if af is None:
        af = ArrayObject()
        root[NameObject("/AF")] = af
    af.append(fs_ref)

    _ensure_minimal_xmp(
        writer,
        attachment_name,
        creator_tool=src_meta["creator_tool"],
        producer=src_meta["producer"],
        author=src_meta["author"],
        title=src_meta["title"],
        subject=src_meta["subject"],
        create_date=src_meta["create_date"],
        modify_date=src_meta["modify_date"],
    )

    tmp = output_pdf_path.with_suffix(".tmp.pdf")
    with open(tmp, "wb") as f:
        writer.write(f)
    tmp.replace(output_pdf_path)


def _ensure_minimal_xmp(
    writer,
    attachment_name: str,
    *,
    creator_tool: Optional[str] = None,
    producer: Optional[str] = None,
    author: Optional[str] = None,
    title: Optional[str] = None,
    subject: Optional[str] = None,
    create_date: Optional[str] = None,
    modify_date: Optional[str] = None,
) -> None:
    try:
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    except Exception:
        return

    xmp = _build_facturx_xmp(
        attachment_name,
        creator_tool=creator_tool,
        producer=producer,
        author=author,
        title=title,
        subject=subject,
        create_date=create_date,
        modify_date=modify_date,
    ).encode("utf-8")
    stream = DecodedStreamObject()
    stream.set_data(xmp)
    stream.update(
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Metadata"),
                NameObject("/Subtype"): NameObject("/XML"),
            }
        )
    )
    ref = writer._add_object(stream)
    writer._root_object[NameObject("/Metadata")] = ref


def _build_facturx_xmp(
    attachment_name: str,
    *,
    creator_tool: Optional[str] = None,
    producer: Optional[str] = None,
    author: Optional[str] = None,
    title: Optional[str] = None,
    subject: Optional[str] = None,
    create_date: Optional[str] = None,
    modify_date: Optional[str] = None,
) -> str:
    uid = str(uuid.uuid4())
    effective_creator_tool = creator_tool or "ZUG2-FeRD Community Edition"
    effective_producer = producer or "ZUG2-FeRD Community Edition"
    now_iso = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    effective_create_date = create_date or now_iso
    effective_modify_date = modify_date or now_iso
    dc_block = ""
    if author or title or subject:
        dc_block += '  <rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        if author:
            dc_block += f'   <dc:creator><rdf:Seq><rdf:li>{_xml_escape(author)}</rdf:li></rdf:Seq></dc:creator>\n'
        if title:
            dc_block += f'   <dc:title><rdf:Alt><rdf:li xml:lang="x-default">{_xml_escape(title)}</rdf:li></rdf:Alt></dc:title>\n'
        if subject:
            dc_block += f'   <dc:description><rdf:Alt><rdf:li xml:lang="x-default">{_xml_escape(subject)}</rdf:li></rdf:Alt></dc:description>\n'
        dc_block += "  </rdf:Description>\n"
    return (
        '<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="ZUG2-FeRD Community Edition">\n'
        ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        '  <rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/">\n'
        f'   <xmp:CreatorTool>{_xml_escape(effective_creator_tool)}</xmp:CreatorTool>\n'
        f'   <xmp:CreateDate>{_xml_escape(effective_create_date)}</xmp:CreateDate>\n'
        f'   <xmp:ModifyDate>{_xml_escape(effective_modify_date)}</xmp:ModifyDate>\n'
        "  </rdf:Description>\n"
        '  <rdf:Description rdf:about="" xmlns:pdf="http://ns.adobe.com/pdf/1.3/">\n'
        f'   <pdf:Producer>{_xml_escape(effective_producer)}</pdf:Producer>\n'
        "  </rdf:Description>\n"
        + dc_block
        + '  <rdf:Description rdf:about="" xmlns:pdfaid="http://www.aiim.org/pdfa/ns/id/">\n'
        "   <pdfaid:part>3</pdfaid:part>\n"
        "   <pdfaid:conformance>B</pdfaid:conformance>\n"
        "  </rdf:Description>\n"
        '  <rdf:Description rdf:about="" xmlns:fx="urn:factur-x:pdfa:CrossIndustryDocument:invoice:1p0#">\n'
        "   <fx:DocumentType>INVOICE</fx:DocumentType>\n"
        "   <fx:DocumentFileName>"
        + _xml_escape(attachment_name)
        + "</fx:DocumentFileName>\n"
        "   <fx:Version>1.0</fx:Version>\n"
        "   <fx:ConformanceLevel>EN16931</fx:ConformanceLevel>\n"
        "   <fx:DocumentID>"
        + uid
        + "</fx:DocumentID>\n"
        "  </rdf:Description>\n"
        " </rdf:RDF>\n"
        "</x:xmpmeta>\n"
        "<?xpacket end=\"w\"?>"
    )


def _xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def run_mustang_validate(java_exe: Path, mustang_jar: Path, pdf_path: Path) -> tuple[bool, str, str]:
    cmd = [
        str(java_exe),
        "-Xmx1G",
        "-Dfile.encoding=UTF-8",
        "-jar",
        str(mustang_jar),
        "--action",
        "validate",
        "--source",
        str(pdf_path),
        "--no-notices",
    ]

    env = os.environ.copy()
    p = subprocess.run(cmd, capture_output=True, text=True, env=env)
    stdout = p.stdout or ""
    stderr = p.stderr or ""

    ok = p.returncode == 0
    if not ok:
        if 'summary status="valid"' in stdout:
            ok = True
    return ok, stdout, stderr


def _detect_xml_attachment_name(xml_bytes: bytes, fallback: str = "factur-x.xml") -> str:
    try:
        root, check = parse_invoice_xml(xml_bytes)
    except Exception:
        return fallback
    if check.dialect == "CII-CrossIndustryInvoice":
        return "factur-x.xml"
    if check.dialect in {"UBL-Invoice", "UBL-CreditNote"}:
        return "xrechnung.xml"
    return fallback


def _detect_zugferd_conformance(xml_bytes: bytes, check: Optional[NormCheckResult] = None) -> str:
    if check is None:
        try:
            _, check = parse_invoice_xml(xml_bytes)
        except Exception:
            return "EN16931"
    if not check.customization_id:
        return "EN16931"
    cid = check.customization_id.lower()
    if "extended" in cid:
        return "EXTENDED"
    if "comfort" in cid:
        return "COMFORT"
    if "basicwl" in cid:
        return "BASICWL"
    if "minimum" in cid:
        return "MINIMUM"
    if "basic" in cid:
        return "BASIC"
    return "EN16931"


def _convert_pdf_date_to_iso(pdf_date: Optional[str]) -> Optional[str]:
    if not pdf_date or not isinstance(pdf_date, str):
        return None
    s = pdf_date.strip()
    if not s:
        return None
    try:
        body = s
        if body.startswith("D:") or body.startswith("D;"):
            body = body[2:]
        body = body.replace("'", ":")
        if len(body) >= 14 and body[13] in ("-", "+"):
            tz_part = body[13:]
            if ":" not in tz_part and len(tz_part) >= 5:
                tz_part = tz_part[:3] + ":" + tz_part[3:]
                body = body[:13] + tz_part
        formats_try = [
            "%Y%m%d%H%M%S%z",
            "%Y%m%d%H%M%S",
            "%Y%m%d%H%M%z",
            "%Y%m%d%H%M",
            "%Y%m%d",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S",
        ]
        for fmt in formats_try:
            try:
                parsed = dt.datetime.strptime(body, fmt)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=dt.timezone.utc)
                iso_s = parsed.astimezone().isoformat(timespec="seconds")
                return iso_s
            except Exception:
                continue
        return s
    except Exception:
        return None


def _extract_source_pdf_metadata(source_pdf_path: Path):
    result = {
        "info": {},
        "creator_tool": None,
        "producer": None,
        "author": None,
        "title": None,
        "subject": None,
        "create_date": None,
        "modify_date": None,
    }
    try:
        from pypdf import PdfReader
    except Exception:
        _apply_sysctl_pdf_fallbacks(result)
        _ensure_pdf_dates_always_set(result)
        return result
    try:
        reader = PdfReader(str(source_pdf_path))
        if reader.metadata:
            raw = {str(k): v for k, v in reader.metadata.items() if v is not None}
            result["info"] = dict(raw)
            for k, v in raw.items():
                kk = str(k).lstrip("/").lower()
                sv = str(v) if not isinstance(v, str) else v
                if not sv:
                    continue
                if kk == "author":
                    result["author"] = sv
                elif kk == "title":
                    result["title"] = sv
                elif kk == "subject":
                    result["subject"] = sv
                elif kk == "creator":
                    result["creator_tool"] = sv
                elif kk == "producer":
                    result["producer"] = sv
                elif kk == "creationdate":
                    result["create_date"] = _convert_pdf_date_to_iso(sv)
                elif kk == "moddate":
                    result["modify_date"] = _convert_pdf_date_to_iso(sv)
    except Exception:
        pass

    _apply_sysctl_pdf_fallbacks(result)
    _ensure_pdf_dates_always_set(result)
    return result


def _apply_sysctl_pdf_fallbacks(meta: dict) -> None:
    try:
        sysctl = load_sysctl()
    except Exception:
        sysctl = {}
    if not isinstance(sysctl, dict):
        return

    _WEAK_DEFAULTS = {
        "",
        "pypdf",
        "ZUG2-FeRD Community Edition",
        "ZUG2-FeRD",
    }

    def _field_is_empty_or_weak(v) -> bool:
        if v is None:
            return True
        if not isinstance(v, str):
            return False
        sv = v.strip()
        if not sv:
            return True
        return sv in _WEAK_DEFAULTS or sv.startswith("ZUG2-FeRD")

    if _field_is_empty_or_weak(meta.get("creator_tool")):
        val = sysctl.get("pdf_creator")
        if isinstance(val, str) and val.strip():
            meta["creator_tool"] = val.strip()
    if _field_is_empty_or_weak(meta.get("producer")):
        val = sysctl.get("pdf_producer")
        if isinstance(val, str) and val.strip():
            meta["producer"] = val.strip()
    if _field_is_empty_or_weak(meta.get("author")):
        val = sysctl.get("pdf_author")
        if isinstance(val, str) and val.strip():
            meta["author"] = val.strip()
    if _field_is_empty_or_weak(meta.get("title")):
        val = sysctl.get("pdf_title")
        if isinstance(val, str) and val.strip():
            meta["title"] = val.strip()
    if _field_is_empty_or_weak(meta.get("subject")):
        val = sysctl.get("pdf_subject")
        if isinstance(val, str) and val.strip():
            meta["subject"] = val.strip()


def _ensure_pdf_dates_always_set(meta: dict) -> None:
    now_iso = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    if not meta.get("create_date") or not isinstance(meta.get("create_date"), str) or not meta.get("create_date").strip():
        meta["create_date"] = now_iso
    if not meta.get("modify_date") or not isinstance(meta.get("modify_date"), str) or not meta.get("modify_date").strip():
        meta["modify_date"] = meta["create_date"] or now_iso


def _iso_date_to_pdf_date(iso_str: Optional[str]) -> Optional[str]:
    if not iso_str or not isinstance(iso_str, str) or not iso_str.strip():
        return None
    try:
        s = iso_str.strip()
        parsed = None
        fmts = [
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S%z",
            "%Y-%m-%d %H:%M:%S",
        ]
        for fmt in fmts:
            try:
                parsed = dt.datetime.strptime(s, fmt)
                break
            except Exception:
                continue
        if parsed is None:
            try:
                parsed = dt.datetime.fromisoformat(s)
            except Exception:
                return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        tz = parsed.strftime("%z") or "+0000"
        tz_formatted = f"{tz[:3]}'{tz[3:5]}'"
        return f"D:{parsed.strftime('%Y%m%d%H%M%S')}{tz_formatted}"
    except Exception:
        return None


def _build_xmp_pdfa3(
    xml_bytes: bytes,
    attachment_name: str,
    check: Optional[NormCheckResult] = None,
    *,
    creator_tool: Optional[str] = None,
    producer: Optional[str] = None,
    author: Optional[str] = None,
    title: Optional[str] = None,
    subject: Optional[str] = None,
    create_date: Optional[str] = None,
    modify_date: Optional[str] = None,
) -> str:
    conformance = _detect_zugferd_conformance(xml_bytes, check)
    document_id = str(uuid.uuid4())
    instance_id = str(uuid.uuid4())
    doc_type = "INVOICE"
    if check is not None and check.dialect == "UBL-CreditNote":
        doc_type = "CREDIT_NOTE"

    pdfaid_part = "3"
    pdfaid_conf = "B"
    escaped_name = _xml_escape(attachment_name)

    effective_creator_tool = creator_tool or "ZUG2-FeRD Community Edition"
    effective_producer = producer or "ZUG2-FeRD Community Edition"
    now_iso = dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S%z")
    if not now_iso.endswith("+") and not "+" in now_iso:
        local_offset = dt.datetime.now().astimezone().strftime("%z")
        if not local_offset:
            local_offset = "+0000"
        now_iso = dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S") + local_offset[:3] + ":" + local_offset[3:]
    effective_create_date = create_date or now_iso
    effective_modify_date = modify_date or now_iso

    author_block = ""
    if author:
        author_block = (
            '  <rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
            f'   <dc:creator><rdf:Seq><rdf:li>{_xml_escape(author)}</rdf:li></rdf:Seq></dc:creator>\n'
        )
        if title:
            author_block += f'   <dc:title><rdf:Alt><rdf:li xml:lang="x-default">{_xml_escape(title)}</rdf:li></rdf:Alt></dc:title>\n'
        if subject:
            author_block += f'   <dc:description><rdf:Alt><rdf:li xml:lang="x-default">{_xml_escape(subject)}</rdf:li></rdf:Alt></dc:description>\n'
        author_block += "  </rdf:Description>\n"

    return (
        '<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="ZUG2-FeRD Community Edition">\n'
        ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        '  <rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/">\n'
        f'   <xmp:CreatorTool>{_xml_escape(effective_creator_tool)}</xmp:CreatorTool>\n'
        f'   <xmp:CreateDate>{_xml_escape(effective_create_date)}</xmp:CreateDate>\n'
        f'   <xmp:ModifyDate>{_xml_escape(effective_modify_date)}</xmp:ModifyDate>\n'
        f'   <xmp:DocumentID>uuid:{document_id}</xmp:DocumentID>\n'
        f'   <xmp:InstanceID>uuid:{instance_id}</xmp:InstanceID>\n'
        '  </rdf:Description>\n'
        '  <rdf:Description rdf:about="" xmlns:pdf="http://ns.adobe.com/pdf/1.3/">\n'
        f'   <pdf:Producer>{_xml_escape(effective_producer)}</pdf:Producer>\n'
        '  </rdf:Description>\n'
        + author_block
        + '  <rdf:Description rdf:about="" xmlns:pdfaid="http://www.aiim.org/pdfa/ns/id/">\n'
        f'   <pdfaid:part>{pdfaid_part}</pdfaid:part>\n'
        f'   <pdfaid:conformance>{pdfaid_conf}</pdfaid:conformance>\n'
        '  </rdf:Description>\n'
        '  <rdf:Description rdf:about="" xmlns:fx="urn:factur-x:pdfa:CrossIndustryDocument:invoice:1p0#">\n'
        f'   <fx:DocumentType>{doc_type}</fx:DocumentType>\n'
        f'   <fx:DocumentFileName>{escaped_name}</fx:DocumentFileName>\n'
        '   <fx:Version>1.0</fx:Version>\n'
        f'   <fx:ConformanceLevel>{conformance}</fx:ConformanceLevel>\n'
        f'   <fx:DocumentID>{document_id}</fx:DocumentID>\n'
        '  </rdf:Description>\n'
        ' </rdf:RDF>\n'
        "</x:xmpmeta>\n"
        '<?xpacket end="w"?>'
    )


def _ensure_pdfa3_xmp(
    writer,
    xml_bytes: bytes,
    attachment_name: str,
    check: Optional[NormCheckResult] = None,
    *,
    creator_tool: Optional[str] = None,
    producer: Optional[str] = None,
    author: Optional[str] = None,
    title: Optional[str] = None,
    subject: Optional[str] = None,
    create_date: Optional[str] = None,
    modify_date: Optional[str] = None,
) -> None:
    try:
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    except Exception:
        return

    xmp = _build_xmp_pdfa3(
        xml_bytes,
        attachment_name,
        check,
        creator_tool=creator_tool,
        producer=producer,
        author=author,
        title=title,
        subject=subject,
        create_date=create_date,
        modify_date=modify_date,
    ).encode("utf-8")
    stream = DecodedStreamObject()
    stream.set_data(xmp)
    stream.update(
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Metadata"),
                NameObject("/Subtype"): NameObject("/XML"),
            }
        )
    )
    ref = writer._add_object(stream)
    writer._root_object[NameObject("/Metadata")] = ref


def _attach_invoice_xml_to_writer(
    writer,
    xml_bytes: bytes,
    attachment_name: str,
    check: Optional[NormCheckResult] = None,
) -> None:
    from pypdf.generic import (
        ArrayObject,
        DecodedStreamObject,
        DictionaryObject,
        NameObject,
        NumberObject,
        TextStringObject,
    )

    subtype = "/text#2Fxml"
    dialect = ""
    if check is not None:
        dialect = check.dialect
    if dialect == "CII-CrossIndustryInvoice":
        af_rel = "/Data"
        desc = "Factur-X/ZUGFeRD CrossIndustryInvoice XML"
    elif dialect in {"UBL-Invoice", "UBL-CreditNote"}:
        af_rel = "/Data"
        desc = "XRechnung EN 16931 (UBL) XML"
    else:
        af_rel = "/Data"
        desc = "E-Invoice XML"

    ef_stream = DecodedStreamObject()
    ef_stream.set_data(xml_bytes)
    ef_stream.update(
        {
            NameObject("/Type"): NameObject("/EmbeddedFile"),
            NameObject("/Subtype"): NameObject(subtype),
            NameObject("/Params"): DictionaryObject(
                {NameObject("/Size"): NumberObject(len(xml_bytes))}
            ),
        }
    )
    ef_ref = writer._add_object(ef_stream)

    filespec = DictionaryObject()
    filespec.update(
        {
            NameObject("/Type"): NameObject("/Filespec"),
            NameObject("/F"): TextStringObject(attachment_name),
            NameObject("/UF"): TextStringObject(attachment_name),
            NameObject("/Desc"): TextStringObject(desc),
            NameObject("/AFRelationship"): NameObject(af_rel),
            NameObject("/EF"): DictionaryObject({NameObject("/F"): ef_ref, NameObject("/UF"): ef_ref}),
        }
    )
    fs_ref = writer._add_object(filespec)

    root = writer._root_object
    names = root.get("/Names")
    if names is None:
        names = DictionaryObject()
        root[NameObject("/Names")] = names

    embedded = names.get("/EmbeddedFiles")
    if embedded is None:
        embedded = DictionaryObject()
        names[NameObject("/EmbeddedFiles")] = embedded

    arr = embedded.get("/Names")
    if arr is None:
        arr = ArrayObject()
        embedded[NameObject("/Names")] = arr

    arr.append(TextStringObject(attachment_name))
    arr.append(fs_ref)

    af = root.get("/AF")
    if af is None:
        af = ArrayObject()
        root[NameObject("/AF")] = af
    af.append(fs_ref)


def assemble_zugferd_pdf(
    base_pdf_path: Path,
    invoice_xml_path_or_bytes: Path | bytes,
    output_pdf_path: Path,
    *,
    stationery_pdf: Optional[Path] = None,
    apply_stationery_all_pages: bool = False,
    attachment_name: Optional[str] = None,
    run_normcheck: bool = True,
) -> tuple[NormCheckResult, Path]:
    try:
        from pypdf import PdfReader, PdfWriter
    except Exception as e:
        raise RuntimeError("pypdf nicht verfügbar") from e

    if isinstance(invoice_xml_path_or_bytes, (bytes, bytearray)):
        xml_bytes = bytes(invoice_xml_path_or_bytes)
    else:
        xml_bytes = Path(invoice_xml_path_or_bytes).read_bytes()

    _, check = parse_invoice_xml(xml_bytes)
    if run_normcheck and not check.ok:
        missing = ", ".join(check.missing_required)
        raise RuntimeError(f"XML nicht EN 16931 konform. Fehlende Pflichtfelder: {missing}")

    effective_attachment_name = attachment_name or _detect_xml_attachment_name(xml_bytes, fallback="factur-x.xml")

    src_meta = _extract_source_pdf_metadata(base_pdf_path)

    prepared_pdf = stamp_pdf_underlay(base_pdf_path, stationery_pdf, apply_stationery_all_pages)

    reader = PdfReader(BytesIO(prepared_pdf))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)

    effective_info = {}
    if src_meta["info"]:
        effective_info.update(src_meta["info"])
    if reader.metadata:
        try:
            for k, v in reader.metadata.items():
                if v is None:
                    continue
                ks = str(k)
                if ks not in effective_info or not effective_info[ks]:
                    effective_info[ks] = v
        except Exception:
            pass

    if src_meta.get("author"):
        effective_info["/Author"] = src_meta["author"]
    if src_meta.get("title"):
        effective_info["/Title"] = src_meta["title"]
    if src_meta.get("subject"):
        effective_info["/Subject"] = src_meta["subject"]
    if src_meta.get("creator_tool"):
        effective_info["/Creator"] = src_meta["creator_tool"]
    if src_meta.get("producer"):
        effective_info["/Producer"] = src_meta["producer"]
    if "/CreationDate" not in effective_info:
        pdf_date = _iso_date_to_pdf_date(src_meta.get("create_date"))
        if pdf_date:
            effective_info["/CreationDate"] = pdf_date
    if "/ModDate" not in effective_info:
        pdf_date = _iso_date_to_pdf_date(src_meta.get("modify_date"))
        if pdf_date:
            effective_info["/ModDate"] = pdf_date

    if effective_info:
        try:
            writer.add_metadata(effective_info)
        except Exception:
            pass

    _attach_invoice_xml_to_writer(writer, xml_bytes, effective_attachment_name, check)
    _ensure_pdfa3_xmp(
        writer,
        xml_bytes,
        effective_attachment_name,
        check,
        creator_tool=src_meta["creator_tool"],
        producer=src_meta["producer"],
        author=src_meta["author"],
        title=src_meta["title"],
        subject=src_meta["subject"],
        create_date=src_meta["create_date"],
        modify_date=src_meta["modify_date"],
    )

    tmp = output_pdf_path.with_suffix(".tmp.pdf")
    with open(tmp, "wb") as f:
        writer.write(f)
    tmp.replace(output_pdf_path)

    return check, output_pdf_path


def assemble_zugferd_pipeline(
    base_pdf_path: Path,
    invoice_xml_path_or_bytes: Path | bytes,
    output_pdf_path: Path,
    *,
    stationery_pdf: Optional[Path] = None,
    apply_stationery_all_pages: bool = False,
    attachment_name: Optional[str] = None,
    java_exe: Optional[Path] = None,
    mustang_jar: Optional[Path] = None,
) -> XmlAssembleResult:
    try:
        check, out_path = assemble_zugferd_pdf(
            base_pdf_path=base_pdf_path,
            invoice_xml_path_or_bytes=invoice_xml_path_or_bytes,
            output_pdf_path=output_pdf_path,
            stationery_pdf=stationery_pdf,
            apply_stationery_all_pages=apply_stationery_all_pages,
            attachment_name=attachment_name,
            run_normcheck=True,
        )
        mustang_ok = False
        mustang_stdout = ""
        mustang_stderr = ""
        if java_exe is not None and mustang_jar is not None and java_exe.exists() and mustang_jar.exists():
            mustang_ok, mustang_stdout, mustang_stderr = run_mustang_validate(java_exe, mustang_jar, out_path)
        return XmlAssembleResult(
            ok=True,
            output_pdf_path=out_path,
            normcheck=check,
            mustang_ok=mustang_ok,
            mustang_stdout=mustang_stdout,
            mustang_stderr=mustang_stderr,
            error=None,
        )
    except Exception as exc:
        return XmlAssembleResult(
            ok=False,
            output_pdf_path=None,
            normcheck=None,
            mustang_ok=False,
            mustang_stdout="",
            mustang_stderr="",
            error=str(exc),
        )
