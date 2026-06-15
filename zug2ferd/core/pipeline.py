from __future__ import annotations

import os
import subprocess
import uuid
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class BuildResult:
    ok: bool
    output_pdf_path: Optional[Path]
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
            bg = st_page0.copy()
            bg.merge_page(page)
            writer.add_page(bg)
        else:
            writer.add_page(page)

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

    reader = PdfReader(str(output_pdf_path))
    writer = PdfWriter()
    for p in reader.pages:
        writer.add_page(p)

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

    _ensure_minimal_xmp(writer, attachment_name)

    tmp = output_pdf_path.with_suffix(".tmp.pdf")
    with open(tmp, "wb") as f:
        writer.write(f)
    tmp.replace(output_pdf_path)


def _ensure_minimal_xmp(writer, attachment_name: str) -> None:
    try:
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    except Exception:
        return

    xmp = _build_facturx_xmp(attachment_name).encode("utf-8")
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


def _build_facturx_xmp(attachment_name: str) -> str:
    uid = str(uuid.uuid4())
    return (
        '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
        ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        '  <rdf:Description rdf:about="" xmlns:pdfaid="http://www.aiim.org/pdfa/ns/id/">\n'
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
