"""Extract bounded body text from a DOCX input without opening embedded content."""
from dataclasses import dataclass
import io
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from defusedxml import ElementTree as SafeXML
from defusedxml.common import DefusedXmlException

from mathmodel_paper_studio.inputs.profiling import ImportLimits, read_bounded

_W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
_DOCUMENT = _W + 'document'
_BODY = _W + 'body'
_PARAGRAPH = _W + 'p'
_TABLE = _W + 'tbl'
_ROW = _W + 'tr'
_CELL = _W + 'tc'
_TEXT = _W + 't'
_TAB = _W + 'tab'
_BREAKS = frozenset((_W + 'br', _W + 'cr'))


@dataclass(frozen=True)
class DocxText:
    text: str
    paragraph_count: int
    table_count: int
    character_count: int


def _inline_text(element, remaining: int) -> str:
    pieces = []
    length = 0
    for node in element.iter():
        if node.tag == _TEXT:
            piece = node.text or ''
        elif node.tag == _TAB:
            piece = '\t'
        elif node.tag in _BREAKS:
            piece = '\n'
        else:
            continue
        length += len(piece)
        if length > remaining:
            raise ValueError('DOCX 提取文本超过字符上限。')
        pieces.append(piece)
    return ''.join(pieces)


def read_docx(path: Path | bytes, *, limits: ImportLimits = ImportLimits()) -> DocxText:
    content = read_bounded(path, limits)
    try:
        with ZipFile(io.BytesIO(content)) as archive:
            infos = archive.infolist()
            if not infos or len(infos) > limits.max_zip_members:
                raise ValueError('DOCX ZIP 成员数量无效或超过上限。')
            seen = set()
            total = 0
            for info in infos:
                name = info.filename
                parts = name.replace('\\', '/').split('/')
                if (not name or name.startswith(('/', '\\')) or ':' in name or '..' in parts
                        or name.casefold() in seen or info.flag_bits & 1):
                    raise ValueError('DOCX ZIP 路径重复、无效或加密。')
                seen.add(name.casefold())
                total += info.file_size
                if (info.file_size > limits.max_zip_member_bytes
                        or info.compress_size > limits.max_file_bytes
                        or total > limits.max_zip_total_bytes):
                    raise ValueError('DOCX ZIP 解压大小超过上限。')
            try:
                document = archive.read('word/document.xml')
            except KeyError as exc:
                raise ValueError('DOCX 缺少正文文档。') from exc
        if len(document) > limits.max_zip_member_bytes:
            raise ValueError('DOCX 正文 XML 超过成员大小上限。')
        root = SafeXML.fromstring(document, forbid_dtd=True, forbid_entities=True, forbid_external=True)
        if root.tag != _DOCUMENT:
            raise ValueError('DOCX 正文结构无效。')
        body = root.find(_BODY)
        if body is None:
            raise ValueError('DOCX 缺少正文内容。')
        if any(node.tag == _W + 'txbxContent' for node in body.iter()):
            raise ValueError('DOCX 包含暂不支持的文本框内容，请先转为普通段落。')
        fragments = []
        paragraph_count = 0
        table_count = 0
        character_count = 0

        def append(fragment: str):
            nonlocal character_count
            if not fragment:
                return
            character_count += len(fragment) + bool(fragments)
            if character_count > limits.max_docx_chars:
                raise ValueError('DOCX 提取文本超过字符上限。')
            fragments.append(fragment)

        for block in body:
            if block.tag == _PARAGRAPH:
                paragraph_count += 1
                append(_inline_text(block, limits.max_docx_chars - character_count))
            elif block.tag == _TABLE:
                table_count += 1
                for row in block.findall(_ROW):
                    cells = row.findall(_CELL)
                    row_text = '\t'.join(_inline_text(cell, limits.max_docx_chars - character_count)
                                          for cell in cells)
                    append(row_text)
        text = '\n'.join(fragments).strip()
        if not text:
            raise ValueError('DOCX 正文中没有可提取的段落或表格文本。')
        return DocxText(text, paragraph_count, table_count, len(text))
    except ValueError:
        raise
    except (BadZipFile, DefusedXmlException, OSError, RuntimeError, EOFError) as exc:
        raise ValueError('DOCX 文件无效或 XML 不安全。') from exc
