import io

import docx
import pytest

from contract_review_ai.parsing import (
    MAX_SECTION_CHARS,
    UnsupportedFileType,
    from_text,
    parse_document,
)
from tests.conftest import CONTRACT


def _minimal_pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = io.BytesIO(), []
    out.write(b"%PDF-1.4\n")
    for i, o in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + o + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer << /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode())
    return out.getvalue()


def test_sections_are_exact_slices_in_order():
    doc = from_text("c.txt", CONTRACT)
    assert doc.sections, "expected sections"
    prev_end = 0
    for s in doc.sections:
        assert s.start >= prev_end and s.end > s.start
        assert doc.text[s.start : s.end] == doc.text[s.start : s.end].strip()
        prev_end = s.end
    assert "governed by the laws" in doc.section_text(doc.sections[-1])


def test_long_paragraph_is_split_under_the_cap():
    text = " ".join(f"Sentence number {i} says something. " for i in range(400))
    doc = from_text("long.txt", text)
    assert len(doc.sections) > 1
    assert all(s.end - s.start <= MAX_SECTION_CHARS * 1.5 for s in doc.sections)


def test_docx_parsing():
    d = docx.Document()
    d.add_paragraph("Governing Law. This Agreement is governed by the laws of Delaware.")
    d.add_paragraph("Assignment requires prior written consent.")
    buf = io.BytesIO()
    d.save(buf)
    doc = parse_document("a.docx", buf.getvalue())
    assert "laws of Delaware" in doc.text


def test_pdf_parsing():
    doc = parse_document("a.pdf", _minimal_pdf("Governed by the laws of Texas"))
    assert "laws of Texas" in doc.text


def test_unsupported_type():
    with pytest.raises(UnsupportedFileType):
        parse_document("a.xlsx", b"nope")
