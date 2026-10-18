"""Small real PDF fixtures, including Chinese CID text and compressed streams."""
from io import BytesIO

from pypdf import PdfWriter
from pypdf.generic import (
    ArrayObject, DecodedStreamObject, DictionaryObject, NameObject,
    NumberObject, TextStringObject,
)


def pdf_bytes(pages=('数学建模题目：优化运输成本', '附件数据见表一'), *, password=None,
              compress=False, padding=0, as_form=False, form_uses=1, raw_content=None):
    writer = PdfWriter()
    descendant = DictionaryObject({
        NameObject('/Type'): NameObject('/Font'),
        NameObject('/Subtype'): NameObject('/CIDFontType0'),
        NameObject('/BaseFont'): NameObject('/STSong-Light'),
        NameObject('/CIDSystemInfo'): DictionaryObject({
            NameObject('/Registry'): TextStringObject('Adobe'),
            NameObject('/Ordering'): TextStringObject('GB1'),
            NameObject('/Supplement'): NumberObject(0),
        }),
    })
    font = writer._add_object(DictionaryObject({
        NameObject('/Type'): NameObject('/Font'),
        NameObject('/Subtype'): NameObject('/Type0'),
        NameObject('/BaseFont'): NameObject('/STSong-Light'),
        NameObject('/Encoding'): NameObject('/UniGB-UCS2-H'),
        NameObject('/DescendantFonts'): ArrayObject([writer._add_object(descendant)]),
    }))
    for text in pages:
        page = writer.add_blank_page(width=595, height=842)
        page[NameObject('/Resources')] = DictionaryObject({
            NameObject('/Font'): DictionaryObject({NameObject('/F1'): font}),
        })
        content = DecodedStreamObject()
        encoded = text.encode('utf-16-be').hex().encode('ascii')
        content.set_data(b'BT /F1 12 Tf 40 760 Td <' + encoded + b'> Tj ET\n' + b' ' * padding)
        if raw_content is not None:
            content.set_data(raw_content)
        if as_form:
            content.update({
                NameObject('/Type'): NameObject('/XObject'),
                NameObject('/Subtype'): NameObject('/Form'),
                NameObject('/BBox'): ArrayObject([NumberObject(v) for v in (0, 0, 595, 842)]),
                NameObject('/Resources'): page['/Resources'],
            })
            form = writer._add_object(content.flate_encode() if compress else content)
            page[NameObject('/Resources')] = DictionaryObject({
                NameObject('/XObject'): DictionaryObject({NameObject('/Form1'): form}),
            })
            content = DecodedStreamObject()
            content.set_data(b'/Form1 Do\n' * form_uses)
        page[NameObject('/Contents')] = writer._add_object(
            content.flate_encode() if compress else content)
    if password is not None:
        writer.encrypt(password)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def type1_font_pdf_bytes():
    writer = PdfWriter()
    mapping = DecodedStreamObject()
    mapping.set_data(b'/Encoding 256 array\ndup 65 /A put\n' + b' ' * 300)
    descriptor = writer._add_object(DictionaryObject({
        NameObject('/Type'): NameObject('/FontDescriptor'),
        NameObject('/FontName'): NameObject('/Helvetica'),
        NameObject('/FontFile'): writer._add_object(mapping),
    }))
    font = writer._add_object(DictionaryObject({
        NameObject('/Type'): NameObject('/Font'),
        NameObject('/Subtype'): NameObject('/Type1'),
        NameObject('/BaseFont'): NameObject('/Helvetica'),
        NameObject('/FontDescriptor'): descriptor,
    }))
    page = writer.add_blank_page(width=595, height=842)
    page[NameObject('/Resources')] = DictionaryObject({
        NameObject('/Font'): DictionaryObject({NameObject('/F1'): font}),
    })
    content = DecodedStreamObject()
    content.set_data(b'BT /F1 12 Tf 40 760 Td (A) Tj ET\n')
    page[NameObject('/Contents')] = writer._add_object(content)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()
