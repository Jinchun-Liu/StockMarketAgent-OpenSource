"""Deterministic, parseable synthetic PDF; no network, fonts or model downloads."""

import json
from io import BytesIO
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
    TextStringObject,
)


def formal_pdf() -> bytes:
    material = json.loads(Path("tests/fixtures/research/formal-disclosure.v1.json").read_text())
    writer = PdfWriter()
    cmap = DecodedStreamObject()
    cmap.set_data(
        b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CIDSystemInfo << "
        b"/Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def /CMapName /Synthetic def "
        b"/CMapType 2 def 1 begincodespacerange <0000> <FFFF> endcodespacerange 1 "
        b"beginbfrange <0000> <FFFF> <0000> endbfrange endcmap CMapName currentdict /CMap "
        b"defineresource pop end end"
    )
    cid = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/CIDFontType2"),
            NameObject("/BaseFont"): NameObject("/Synthetic"),
            NameObject("/CIDSystemInfo"): DictionaryObject(
                {
                    NameObject("/Registry"): TextStringObject("Adobe"),
                    NameObject("/Ordering"): TextStringObject("Identity"),
                    NameObject("/Supplement"): NumberObject(0),
                }
            ),
        }
    )
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type0"),
            NameObject("/BaseFont"): NameObject("/Synthetic"),
            NameObject("/Encoding"): NameObject("/Identity-H"),
            NameObject("/DescendantFonts"): ArrayObject([cid]),
            NameObject("/ToUnicode"): cmap,
        }
    )
    for part in material["formal_text"].split("[PDF page ")[1:]:
        page = writer.add_blank_page(width=600, height=800)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
        commands = []
        for number, line in enumerate(part.split("]", 1)[1].strip().splitlines()):
            commands.append(
                f"BT /F1 8 Tf 30 {760 - number * 20} Td <{line.encode('utf-16-be').hex()}> Tj ET"
            )
        stream = DecodedStreamObject()
        stream.set_data("\n".join(commands).encode())
        page[NameObject("/Contents")] = stream
    output = BytesIO()
    writer.write(output)
    return output.getvalue()
