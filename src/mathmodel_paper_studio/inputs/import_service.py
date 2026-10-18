"""Copy inputs into a new staging layout; originals are never modified."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
from uuid import uuid4

from mathmodel_paper_studio.bootstrap.paths import validate_import_source_path, validate_managed_path
from mathmodel_paper_studio.domain.models import SourceFile, relative_path as validate_relative_path
from mathmodel_paper_studio.inputs.csv_reader import read_csv
from mathmodel_paper_studio.inputs.docx_reader import read_docx
from mathmodel_paper_studio.inputs.profiling import (
    ImportLimits, profile_table, read_bounded, read_source_bounded,
)
from mathmodel_paper_studio.inputs.xlsx_reader import read_xlsx

MAX_PROBLEM_TEXT_BYTES = 1024 * 1024


def write_json(path: Path, value) -> None:
    validate_managed_path(path)
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write('\n')


class ImportService:
    def __init__(self, limits: ImportLimits = ImportLimits()):
        self.limits = limits

    def validate_sources(self, sources, template=None) -> tuple[tuple[Path, str], ...]:
        selected = tuple((Path(path), None) for path in sources)
        if template is not None:
            selected += ((Path(template), 'template'),)
        seen_paths, seen_names = set(), set()
        result = []
        for path, required in selected:
            path = validate_import_source_path(path)
            path_key, name_key = str(path).casefold(), path.name.casefold()
            if path_key in seen_paths or name_key in seen_names:
                raise ValueError('输入文件路径或文件名重复（不区分大小写）。')
            extension = path.suffix.casefold().lstrip('.')
            if required == 'template':
                if extension != 'docx':
                    raise ValueError('Word 模板必须是本地磁盘上的 DOCX 文件。')
                kind = 'docx'
            else:
                kind = 'docx_data' if extension == 'docx' else extension
            if kind not in ('csv', 'xlsx', 'docx_data', 'docx'):
                raise ValueError('仅支持 CSV、XLSX 和 DOCX 输入，模板必须是 DOCX。')
            if not path.is_file() or path.stat().st_size > self.limits.max_file_bytes:
                raise ValueError('输入不是普通文件或超过字节上限。')
            seen_paths.add(path_key)
            seen_names.add(name_key)
            result.append((path, kind))
        return tuple(result)

    def _entry(self, root, project_id, relative, selected_name, content, kind, notes):
        record = SourceFile(uuid4().hex, project_id, relative,
                            hashlib.sha256(content).hexdigest(), len(content), kind)
        target = validate_managed_path(root / relative)
        with target.open('xb') as stream:
            stream.write(content)
        if hashlib.sha256(read_bounded(target, self.limits)).hexdigest() != record.sha256:
            raise ValueError('输入副本哈希校验失败。')
        return {**asdict(record), 'selected_name': selected_name,
                'parser_notes': list(notes), 'record': asdict(record)}

    def _archive_problem_document(self, root: Path, snapshot):
        if snapshot is None:
            return None
        if not isinstance(snapshot, tuple) or len(snapshot) != 2:
            raise ValueError('题目原件必须是文件名和只读字节组成的二元组。')
        selected_name, content = snapshot
        if (not isinstance(selected_name, str) or not selected_name
                or any(char in '<>:"/\\|?*' or ord(char) < 32 for char in selected_name)
                or selected_name != selected_name.rstrip(' .')):
            raise ValueError('题目原件必须使用不含路径的普通文件名。')
        extension = Path(selected_name).suffix.casefold()
        if extension not in ('.docx', '.pdf'):
            raise ValueError('题目原件只支持 DOCX 或 PDF。')
        if not isinstance(content, bytes) or not content or len(content) > self.limits.max_file_bytes:
            raise ValueError('题目原件必须包含只读字节，且不得超过字节上限。')
        relative = f'00_inputs/problem-original-v0001{extension}'
        digest = hashlib.sha256(content).hexdigest()
        target = validate_managed_path(root / relative)
        with target.open('xb') as stream:
            stream.write(content)
        if hashlib.sha256(read_bounded(target, self.limits)).hexdigest() != digest:
            raise ValueError('题目原件副本哈希校验失败。')
        return {'selected_name': selected_name, 'relative_path': relative,
                'sha256': digest, 'size_bytes': len(content)}

    def populate(self, root: Path, project_id: str, problem: str, selected, *, sheets=None,
                 problem_document=None, problem_document_metadata=None,
                 revision: int = 1, progress=None):
        if type(revision) is not int or not 1 <= revision <= 9999:
            raise ValueError('输入版本号必须在 1–9999 之间。')
        if not isinstance(problem, str) or not problem.strip():
            raise ValueError('题目文本不能为空。')
        content = problem.encode('utf-8')
        if len(content) > MAX_PROBLEM_TEXT_BYTES:
            raise ValueError('题目文本超过 1 MiB 上限。')
        if len(content) > self.limits.max_file_bytes:
            raise ValueError('题目文本超过字节上限。')
        if problem_document is not None and problem_document_metadata is not None:
            raise ValueError('新题目原件与保留的原件记录不能同时提供。')
        problem_original = self._archive_problem_document(root, problem_document)
        if problem_document_metadata is not None:
            problem_original = self._validate_problem_document_metadata(problem_document_metadata)
        version = f'v{revision:04d}'
        problem_name = 'problem-v0001.txt' if revision == 1 else f'problem-{version}.txt'
        entries = [self._entry(root, project_id, f'00_inputs/{problem_name}',
                               problem_name, content, 'problem', ('encoding=utf-8',))]
        for index, (source, kind) in enumerate(selected, start=1):
            if progress is not None:
                progress(f'正在处理数据文件 {index}/{len(selected)}：{source.name}')
            content = read_source_bounded(source, self.limits)
            if progress is not None:
                progress(f'已读取数据文件 {index}/{len(selected)}：{source.name}')
            extension = source.suffix.casefold()
            relative = f'00_inputs/source-{index:04d}-{version}{extension}'
            notes = ('opaque-copy',)
            entry = self._entry(root, project_id, relative, source.name, content, kind, notes)
            if progress is not None:
                progress(f'已复制数据文件 {index}/{len(selected)}：{source.name}')
            copied = root / relative
            analysis_version = '' if revision == 1 else f'-{version}'
            if kind in ('csv', 'xlsx'):
                table = (read_csv(copied, limits=self.limits) if kind == 'csv' else
                         read_xlsx(copied, sheet=(sheets or {}).get(source.name), limits=self.limits))
                entry = {**entry, 'parser_notes': list(table.notes)}
                profile = profile_table(table)
                write_json(root / f'10_analysis/source-{index:04d}{analysis_version}-profile.json', profile)
                write_json(root / f'10_analysis/source-{index:04d}{analysis_version}-dictionary.json',
                           {'sheet_name': table.sheet_name, 'columns': profile['columns']})
            elif kind == 'docx_data':
                document = read_docx(copied, limits=self.limits)
                entry = {**entry, 'parser_notes': ['docx=defusedxml', 'text=body-paragraphs-and-tables']}
                write_json(root / f'10_analysis/source-{index:04d}{analysis_version}-profile.json', {
                    'kind': 'docx_data', 'paragraph_count': document.paragraph_count,
                    'table_count': document.table_count, 'character_count': document.character_count,
                    'parser_notes': list(entry['parser_notes']),
                })
            # Detect source changes while copying/parsing; never edit the source.
            if progress is not None:
                progress(f'正在复核数据文件 {index}/{len(selected)}：{source.name}')
            if hashlib.sha256(read_source_bounded(source, self.limits)).hexdigest() != entry['sha256']:
                raise ValueError('导入期间源文件发生变化，请重试。')
            entries.append(entry)
            if progress is not None:
                progress(f'已完成数据文件 {index}/{len(selected)}：{source.name}')
        manifest = {'version': revision, 'sources': entries}
        if problem_original is not None:
            manifest = {**manifest, 'problem_document': problem_original}
        write_json(root / f'00_inputs/manifest-{version}.json', manifest)
        return tuple(SourceFile(**entry['record']) for entry in entries)

    def _validate_problem_document_metadata(self, value):
        if not isinstance(value, dict) or set(value) != {
                'selected_name', 'relative_path', 'sha256', 'size_bytes'}:
            raise ValueError('题目原件记录结构无效。')
        selected_name = value['selected_name']
        relative = validate_relative_path(value['relative_path'])
        extension = Path(selected_name).suffix.casefold() if isinstance(selected_name, str) else ''
        if (not isinstance(selected_name, str) or not selected_name
                or selected_name != Path(selected_name).name
                or any(char in '<>:"/\\|?*' or ord(char) < 32 for char in selected_name)
                or selected_name != selected_name.rstrip(' .')
                or extension not in ('.docx', '.pdf')
                or re.fullmatch(r'00_inputs/problem-original-v[0-9]{4}\.\w+', relative) is None
                or Path(relative).suffix.casefold() != extension
                or not isinstance(value['sha256'], str)
                or re.fullmatch(r'[0-9a-f]{64}', value['sha256']) is None
                or type(value['size_bytes']) is not int
                or not 1 <= value['size_bytes'] <= self.limits.max_file_bytes):
            raise ValueError('题目原件记录无效。')
        return {
            'selected_name': selected_name, 'relative_path': relative,
            'sha256': value['sha256'], 'size_bytes': value['size_bytes'],
        }
