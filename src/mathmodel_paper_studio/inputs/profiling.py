"""Bounded immutable tables and deterministic, finite-only data dictionaries."""
from dataclasses import dataclass, fields
from datetime import date, datetime
import math
from pathlib import Path

from mathmodel_paper_studio.bootstrap.paths import validate_managed_path


@dataclass(frozen=True)
class ImportLimits:
    max_file_bytes: int = 32 * 1024 * 1024
    max_rows: int = 100_000  # Data rows, excluding the header.
    max_columns: int = 256
    max_cell_chars: int = 32_768
    max_zip_member_bytes: int = 16 * 1024 * 1024
    max_zip_total_bytes: int = 64 * 1024 * 1024
    max_zip_members: int = 2048
    max_cells: int = 1_000_000

    def __post_init__(self):
        if any(type(getattr(self, f.name)) is not int or getattr(self, f.name) <= 0 for f in fields(self)):
            raise ValueError('导入限制必须为正整数。')


@dataclass(frozen=True)
class Table:
    headers: tuple[str, ...]
    rows: tuple[tuple, ...]
    sheet_name: str | None = None
    sheet_names: tuple[str, ...] = ()
    formulas: tuple[str, ...] = ()
    merged_ranges: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


def read_bounded(path: Path | bytes, limits: ImportLimits) -> bytes:
    if isinstance(path, bytes):
        if len(path) > limits.max_file_bytes:
            raise ValueError('输入文件超过字节上限。')
        return path
    path = validate_managed_path(path)
    if not path.is_file():
        raise ValueError('输入不是普通文件。')
    if path.stat().st_size > limits.max_file_bytes:
        raise ValueError('输入文件超过字节上限。')
    with path.open('rb') as stream:
        content = stream.read(limits.max_file_bytes + 1)
    if len(content) > limits.max_file_bytes:
        raise ValueError('输入文件超过字节上限。')
    return content


def checked_table(headers, rows, limits: ImportLimits, **metadata) -> Table:
    headers = tuple(str(value) if value is not None else '' for value in headers)
    if (not headers or any(not value.strip() for value in headers)
            or len(set(headers)) != len(headers)):
        raise ValueError('表头不能为空或重复。')
    if len(headers) > limits.max_columns or len(rows) > limits.max_rows:
        raise ValueError('表格行数或列数超过上限。')
    if (len(rows) + 1) * len(headers) > limits.max_cells:
        raise ValueError('表格单元格总数超过上限。')
    for row in (headers, *rows):
        if len(row) != len(headers):
            raise ValueError('数据行列数与表头不一致。')
        if any(len(str(value)) > limits.max_cell_chars for value in row if value is not None):
            raise ValueError('单元格内容超过长度上限。')
    return Table(headers, tuple(tuple(row) for row in rows), **metadata)


def _kind(value) -> str:
    if isinstance(value, bool) or isinstance(value, str) and value.casefold() in ('true', 'false'):
        return 'boolean'
    if isinstance(value, (datetime, date)):
        return 'date'
    try:
        return 'number' if math.isfinite(float(value)) else 'text'
    except (TypeError, ValueError, OverflowError):
        return 'text'


def profile_table(table: Table) -> dict:
    columns = []
    for index, name in enumerate(table.headers):
        values = tuple(row[index] for row in table.rows)
        present = tuple(v for v in values if v is not None and str(v).strip() != '')
        kinds = frozenset(_kind(value) for value in present)
        kind = next(iter(kinds)) if len(kinds) == 1 else ('empty' if not kinds else 'mixed')
        numbers = tuple(float(v) for v in present) if kind == 'number' else ()
        columns.append({'name': name, 'type': kind, 'missing': len(values) - len(present),
                        'numeric_range': {'min': min(numbers), 'max': max(numbers)} if numbers else None})
    return {'sheet_name': table.sheet_name, 'sheet_names': list(table.sheet_names),
            'row_count': len(table.rows), 'columns': columns, 'formulas': list(table.formulas),
            'merged_ranges': list(table.merged_ranges), 'parser_notes': list(table.notes)}
