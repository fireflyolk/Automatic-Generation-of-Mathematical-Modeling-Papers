"""Structural, bounded validation of run outputs. This is not math verification."""
from dataclasses import dataclass
import csv
import hashlib
import json
import math
from pathlib import Path
import struct
from types import MappingProxyType
from typing import Mapping
import zlib

from mathmodel_paper_studio.bootstrap.paths import PathPolicyError, validate_managed_path
from mathmodel_paper_studio.domain.models import ArtifactEvidence, StageStatus, relative_path
from mathmodel_paper_studio.execution.limits import ComputationLimits
from mathmodel_paper_studio.execution.python_runner import (
    RunArtifactLayout, _atomic_create, _evidence,
)


_CATEGORIES = {
    'script': '30_src',
    'table': '40_results',
    'validation': '40_results',
    'figure': '50_figures',
}


@dataclass(frozen=True)
class ArtifactExpectation:
    relative_path: str
    kind: str
    expected_columns: tuple[str, ...] | None = None
    expected_rows: int | None = None
    required: bool = True

    def __post_init__(self):
        relative_path(self.relative_path)
        if self.kind not in _CATEGORIES:
            raise ValueError('工件类型必须为 script、table、figure 或 validation。')
        parts = self.relative_path.split('/')
        if len(parts) < 2 or parts[0] != _CATEGORIES[self.kind]:
            raise ValueError('工件必须位于与类型对应的运行输出目录。')
        if parts[0] == '40_results' and parts[1] == 'validation-v0001.json':
            raise ValueError('验证报告路径由应用保留。')
        if self.expected_columns is not None:
            if self.kind != 'table' or not isinstance(self.expected_columns, (tuple, list)):
                raise ValueError('只有表格工件可以声明列结构。')
            columns = tuple(self.expected_columns)
            if (not columns or len(columns) > 4096
                    or any(not isinstance(name, str) or not name.strip() or len(name) > 128 for name in columns)
                    or len(columns) != len(set(columns))):
                raise ValueError('预期列名不能为空、重复或超长。')
            object.__setattr__(self, 'expected_columns', columns)
        if self.expected_rows is not None:
            if self.kind != 'table' or type(self.expected_rows) is not int or self.expected_rows < 0:
                raise ValueError('预期数据行数必须是表格的非负整数。')
        if type(self.required) is not bool:
            raise ValueError('required 必须为布尔值。')


@dataclass(frozen=True)
class ResultValidationReport:
    stage_status: StageStatus
    evidence: tuple[ArtifactEvidence, ...]
    issues: tuple[str, ...]
    missing_paths: tuple[str, ...]
    hashes: Mapping[str, str]
    summary_path: Path
    summary_evidence: ArtifactEvidence
    human_review_required: bool = True
    mathematical_correctness_verified: bool = False

    def __post_init__(self):
        object.__setattr__(self, 'stage_status', StageStatus(self.stage_status))
        object.__setattr__(self, 'evidence', tuple(self.evidence))
        object.__setattr__(self, 'issues', tuple(self.issues))
        object.__setattr__(self, 'missing_paths', tuple(self.missing_paths))
        object.__setattr__(self, 'hashes', MappingProxyType(dict(self.hashes)))
        if type(self.human_review_required) is not bool or self.human_review_required is not True:
            raise ValueError('结果验证始终需要人工复核。')
        if self.mathematical_correctness_verified is not False:
            raise ValueError('结构验证不得宣称数学正确性已证明。')


class ResultValidator:
    def __init__(self, layout: RunArtifactLayout, limits: ComputationLimits = ComputationLimits()):
        if not isinstance(layout, RunArtifactLayout) or not isinstance(limits, ComputationLimits):
            raise TypeError('结果验证器需要运行目录和有限资源规格。')
        self.layout = layout
        self.limits = limits

    def validate(self, expectations) -> ResultValidationReport:
        if (not isinstance(expectations, (tuple, list)) or not expectations
                or len(expectations) > self.limits.max_artifacts):
            raise ValueError('至少需要一个预期工件，且数量不得超过上限。')
        specs = tuple(expectations)
        if any(not isinstance(spec, ArtifactExpectation) for spec in specs):
            raise TypeError('工件集合包含无效类型。')
        if len({spec.relative_path for spec in specs}) != len(specs):
            raise ValueError('工件相对路径不能重复。')

        evidence = []
        issues = []
        missing = []
        hashes = {}
        for spec in specs:
            if spec.relative_path.startswith('40_results/validation-v'):
                raise ValueError('验证报告由应用保留。')
            try:
                path = self.layout.path_for(spec.relative_path)
            except (ValueError, PathPolicyError, OSError):
                issues.append(f'工件路径不安全：{spec.relative_path}')
                continue
            if not path.exists():
                if spec.required:
                    missing.append(spec.relative_path)
                    issues.append(f'缺少必需工件：{spec.relative_path}')
                continue
            try:
                validate_managed_path(path)
                if path.is_symlink() or not path.is_file():
                    issues.append(f'工件不是普通文件：{spec.relative_path}')
                    continue
                before = path.stat()
                if before.st_size > self.limits.artifact_bytes:
                    issues.append(f'工件超过字节上限：{spec.relative_path}')
                    continue
                if spec.kind == 'table':
                    issue = self._validate_csv(path, spec)
                elif spec.kind == 'figure':
                    issue = self._validate_png(path)
                else:
                    issue = None
                if issue is not None:
                    issues.append(f'{issue}：{spec.relative_path}')
                    continue
                digest = self._hash_file(path, self.limits.artifact_bytes)
                after = path.stat()
                if (before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns
                        or len(digest) != 64):
                    issues.append(f'工件在验证期间发生变化：{spec.relative_path}')
                    continue
                hashes[spec.relative_path] = digest
                evidence.append(_evidence(self.layout, spec.relative_path, spec.kind, path))
            except (OSError, UnicodeError, csv.Error, ValueError, PathPolicyError, struct.error):
                issues.append(f'工件读取或结构验证失败：{spec.relative_path}')

        status = StageStatus.PAUSED if issues else StageStatus.COMPLETED
        summary_path = self._next_summary_path()
        summary = {
            'schema_version': 1,
            'run_id': self.layout.run.run_id,
            'stage_status': status.value,
            'mathematical_correctness_verified': False,
            'human_review_required': True,
            'missing_paths': missing,
            'issues': issues,
            'artifacts': [
                {'evidence_id': item.evidence_id, 'relative_path': item.relative_path,
                 'sha256': item.sha256, 'kind': item.kind, 'structurally_verified': item.verified}
                for item in evidence
            ],
            'resource_limits': {
                'artifact_bytes': self.limits.artifact_bytes,
                'max_artifacts': self.limits.max_artifacts,
                'max_rows': self.limits.max_rows,
                'max_columns': self.limits.max_columns,
            },
        }
        encoded = json.dumps(summary, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':')).encode('utf-8')
        if len(encoded) > self.limits.validation_summary_bytes:
            raise ValueError('验证摘要超过工件字节上限。')
        _atomic_create(summary_path, encoded)
        summary_relative = summary_path.relative_to(self.layout.root).as_posix()
        summary_evidence = _evidence(self.layout, summary_relative, 'validation', summary_path)
        return ResultValidationReport(
            status, tuple(evidence), tuple(issues), tuple(missing), hashes,
            summary_path, summary_evidence, True, False,
        )

    def _next_summary_path(self) -> Path:
        for version in range(1, 10000):
            relative = f'40_results/validation-v{version:04d}.json'
            path = self.layout.path_for(relative)
            if not path.exists():
                return path
        raise ValueError('验证摘要版本数量超过上限。')

    def _validate_csv(self, path: Path, spec: ArtifactExpectation) -> str | None:
        with path.open('r', encoding='utf-8-sig', newline='') as stream:
            reader = csv.reader(stream, strict=True)
            header = next(reader, None)
            if (header is None or not header or len(header) > self.limits.max_columns
                    or any(not value.strip() for value in header)
                    or len(header) != len(set(header))):
                return '表格列结构无效'
            if spec.expected_columns is not None and tuple(header) != spec.expected_columns:
                return '表格列名或顺序不匹配'
            row_count = 0
            for row in reader:
                row_count += 1
                if row_count > self.limits.max_rows:
                    return '表格超过行数上限'
                if len(row) != len(header):
                    return '表格行列数不匹配'
                for value in row:
                    if not value.strip():
                        continue
                    try:
                        numeric = float(value)
                    except ValueError:
                        continue
                    if not math.isfinite(numeric):
                        return '表格包含 NaN 或 Inf 非有限值'
            if spec.expected_rows is not None and row_count != spec.expected_rows:
                return '表格数据行数不匹配'
        return None

    def _validate_png(self, path: Path) -> str | None:
        with path.open('rb') as stream:
            if stream.read(8) != b'\x89PNG\r\n\x1a\n':
                return '图片不是 PNG'
            seen_header = False
            seen_data = False
            seen_end = False
            width = height = 0
            chunks = 0
            while not seen_end:
                header = stream.read(8)
                if len(header) != 8:
                    return 'PNG 分块不完整'
                length = struct.unpack('>I', header[:4])[0]
                kind = header[4:]
                if length > self.limits.artifact_bytes or chunks >= 4096:
                    return 'PNG 分块超过上限'
                if chunks == 0 and kind != b'IHDR':
                    return 'PNG 缺少首个 IHDR 分块'
                if kind == b'IHDR':
                    if seen_header or length != 13:
                        return 'PNG IHDR 无效'
                    payload = stream.read(13)
                    if len(payload) != 13:
                        return 'PNG IHDR 不完整'
                    width, height, depth, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', payload)
                    if (width == 0 or height == 0 or width > 32768 or height > 32768
                            or width * height > 100_000_000
                            or depth not in (1, 2, 4, 8, 16) or color not in (0, 2, 3, 4, 6)
                            or compression != 0 or filtering != 0 or interlace not in (0, 1)):
                        return 'PNG 图像尺寸或 IHDR 参数无效'
                    crc = zlib.crc32(kind)
                    crc = zlib.crc32(payload, crc)
                    seen_header = True
                else:
                    if not seen_header:
                        return 'PNG 分块顺序无效'
                    crc = zlib.crc32(kind)
                    remaining = length
                    while remaining:
                        block = stream.read(min(128 * 1024, remaining))
                        if not block:
                            return 'PNG 分块数据不完整'
                        crc = zlib.crc32(block, crc)
                        remaining -= len(block)
                    if kind == b'IDAT':
                        seen_data = seen_data or length > 0
                    if kind == b'IEND':
                        if length != 0 or not seen_data:
                            return 'PNG 结束分块或图像数据无效'
                        seen_end = True
                stored_crc = stream.read(4)
                if len(stored_crc) != 4 or struct.unpack('>I', stored_crc)[0] != crc & 0xffffffff:
                    return 'PNG CRC 校验失败'
                chunks += 1
            if not seen_header or not seen_data or stream.read(1):
                return 'PNG 图像内容不完整或含尾随数据'
        return None

    @staticmethod
    def _hash_file(path: Path, byte_limit: int) -> str:
        digest = hashlib.sha256()
        total = 0
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(128 * 1024), b''):
                total += len(chunk)
                if total > byte_limit:
                    raise ValueError('artifact byte limit')
                digest.update(chunk)
        return digest.hexdigest()




