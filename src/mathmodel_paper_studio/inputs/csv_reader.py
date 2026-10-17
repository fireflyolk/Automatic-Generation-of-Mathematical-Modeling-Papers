"""Strict CSV parsing; cell expressions are retained as inert strings."""
import csv
import io
from pathlib import Path

from mathmodel_paper_studio.inputs.profiling import ImportLimits, checked_table, read_bounded


def read_csv(path: Path | bytes, *, limits: ImportLimits = ImportLimits()):
    content = read_bounded(path, limits)
    encodings = ('utf-8-sig',) if content.startswith(b'\xef\xbb\xbf') else ('utf-8', 'gb18030')
    for encoding in encodings:
        try:
            text = content.decode(encoding, errors='strict')
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError('CSV 编码无效；请使用 UTF-8、GBK 或 GB18030。')
    if any(ord(c) < 32 and c not in '\n\r\t' for c in text):
        raise ValueError('CSV 包含不支持的控制字符。')
    reader = csv.reader(io.StringIO(text, newline=''), strict=True)
    # csv's process-wide parser limit remains unchanged. Enforce our smaller
    # limit on every returned row and report the builtin safety limit clearly.
    try:
        headers = next(reader, ())
        checked_table(headers, (), limits)
        rows = []
        for row in reader:
            if len(rows) >= limits.max_rows or (len(rows) + 2) * len(headers) > limits.max_cells:
                raise ValueError('CSV 行数或单元格总数超过上限。')
            if len(row) != len(headers) or len(row) > limits.max_columns:
                raise ValueError('CSV 数据行列数与表头不一致或超过上限。')
            if any(len(value) > limits.max_cell_chars for value in row):
                raise ValueError('CSV 单元格内容超过长度上限。')
            rows.append(tuple(row))
    except csv.Error as exc:
        raise ValueError('CSV 格式错误或单元格过长。') from exc
    return checked_table(headers, rows, limits, notes=(f'encoding={encoding}', 'expressions=literal'))
