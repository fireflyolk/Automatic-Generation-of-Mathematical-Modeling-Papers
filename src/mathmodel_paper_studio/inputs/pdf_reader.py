"""Bounded text-only PDF extraction; embedded actions and attachments are not opened."""
from dataclasses import dataclass
from io import BytesIO
import logging
from pathlib import Path
from threading import get_ident

from pypdf import PdfReader, apply_configuration
from pypdf.errors import DependencyError, LimitReachedError, PyPdfError
from pypdf.generic import ArrayObject, DictionaryObject, StreamObject

from mathmodel_paper_studio.inputs.profiling import ImportLimits, read_bounded


@dataclass(frozen=True)
class PdfText:
    text: str
    page_count: int
    character_count: int


class _ExtractionWarnings(logging.Handler):
    """pypdf can log and skip unsupported content even when strict=True."""

    def __init__(self):
        super().__init__(logging.WARNING)
        self.thread_id = get_ident()
        self.detected = False

    def emit(self, record):
        if record.thread == self.thread_id:
            self.detected = True

    def __enter__(self):
        logging.getLogger('pypdf').addHandler(self)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        logging.getLogger('pypdf').removeHandler(self)
        self.close()
        if exc_type is None and self.detected:
            raise ValueError('PDF 解析跳过了部分内容，无法完整导入；请重新导出文字版 PDF。')


def _check_resource_streams(pages, limits):
    # Count unique content, nested Form and font-mapping streams before extraction.
    pending = [(page.get(key), 0) for page in pages for key in ('/Contents', '/Resources')]
    seen = set()
    total = 0
    skip = {'/Parent', '/P', '/Metadata', '/AA', '/A', '/JS', '/JavaScript', '/EF'}
    while pending:
        reference, depth = pending.pop()
        if reference is None:
            continue
        node = reference.get_object()
        if id(node) in seen:
            continue
        seen.add(id(node))
        if len(seen) > 20_000 or depth > 32:
            raise ValueError('PDF 资源数量或嵌套深度超过上限。')
        if isinstance(node, StreamObject):
            if node.get('/Subtype') == '/Image' or node.get('/Type') == '/EmbeddedFile':
                continue
            size = len(node.get_data())
            total += size
            if size > limits.max_pdf_stream_bytes or total > limits.max_pdf_total_stream_bytes:
                raise ValueError('PDF 解压内容大小超过上限。')
        if isinstance(node, DictionaryObject):
            pending.extend((value, depth + 1) for key, value in node.items() if key not in skip)
        elif isinstance(node, ArrayObject):
            pending.extend((value, depth + 1) for value in node)


def _extract_pages(reader, limits):
    page_count = len(reader.pages)
    if not 0 < page_count <= limits.max_pdf_pages:
        raise ValueError('PDF 页数为空或超过页数上限。')
    _check_resource_streams(reader.pages, limits)
    fragments = []
    character_count = 0
    for page in reader.pages:
        content = page.get_contents()
        if content is not None:
            size = len(content.get_data())
            if size > limits.max_pdf_stream_bytes:
                raise ValueError('PDF 解压内容大小超过上限。')
        text = (page.extract_text() or '').strip()
        if not text:
            continue
        character_count += len(text) + (2 if fragments else 0)
        if character_count > limits.max_pdf_chars:
            raise ValueError('PDF 提取文本超过字符上限。')
        fragments.append(text)
    if not fragments:
        raise ValueError('PDF 中没有可提取的文字，可能是扫描图片版；请使用文字版 PDF 或手动粘贴题目。')
    return PdfText('\n\n'.join(fragments), page_count, character_count)


def read_pdf(path: Path | bytes, *, limits: ImportLimits = ImportLimits()) -> PdfText:
    content = read_bounded(path, limits)
    if not content.startswith(b'%PDF-'):
        raise ValueError('PDF 文件无效或已损坏。')
    try:
        # Apply budgets before opening the PDF, including compressed fonts/object streams.
        with _ExtractionWarnings(), apply_configuration(
            maximum_declared_stream_length=limits.max_pdf_stream_bytes,
            array_based_stream_maximum_output_length=limits.max_pdf_stream_bytes,
            zlib_maximum_output_length=limits.max_pdf_stream_bytes,
            lzw_maximum_output_length=limits.max_pdf_stream_bytes,
            run_length_maximum_output_length=limits.max_pdf_stream_bytes,
            brotli_maximum_output_length=limits.max_pdf_stream_bytes,
            stream_decoding_work_maximum_length=limits.max_pdf_total_stream_bytes,
            stream_parsing_work_maximum_count=200_000,
            array_maximum_element_count=20_000,
            dictionary_maximum_element_count=20_000,
            page_tree_maximum_entries=limits.max_pdf_pages * 4,
            page_tree_maximum_depth=32,
            xform_maximum_invocations_per_extraction=100,
            jbig2dec_binary=None,
        ):
            reader = PdfReader(BytesIO(content), strict=True)
            if reader.is_encrypted:
                raise ValueError('暂不支持加密 PDF，请先另存为未加密的文字版 PDF。')
            return _extract_pages(reader, limits)
    except LimitReachedError as exc:
        raise ValueError('PDF 解析或解压内容超过安全大小/工作量上限。') from exc
    except (PyPdfError, DependencyError, OSError, EOFError, KeyError, IndexError,
            TypeError, AttributeError, AssertionError, RecursionError, NotImplementedError) as exc:
        raise ValueError('PDF 文件损坏、无效或包含暂不支持的内容，请重新导出为文字版 PDF。') from exc
