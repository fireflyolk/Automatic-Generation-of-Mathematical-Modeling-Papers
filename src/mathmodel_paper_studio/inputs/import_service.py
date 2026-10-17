"""Copy inputs into a new staging layout; originals are never modified."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from mathmodel_paper_studio.bootstrap.paths import validate_managed_path
from mathmodel_paper_studio.domain.models import SourceFile
from mathmodel_paper_studio.inputs.csv_reader import read_csv
from mathmodel_paper_studio.inputs.profiling import ImportLimits, profile_table, read_bounded
from mathmodel_paper_studio.inputs.xlsx_reader import read_xlsx


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
            selected += ((Path(template), 'docx'),)
        seen_paths, seen_names = set(), set()
        result = []
        for path, required in selected:
            path = validate_managed_path(path)
            path_key, name_key = str(path).casefold(), path.name.casefold()
            if path_key in seen_paths or name_key in seen_names:
                raise ValueError('输入文件路径或文件名重复（不区分大小写）。')
            kind = path.suffix.casefold().lstrip('.')
            if kind not in ('csv', 'xlsx', 'docx') or required is not None and kind != required:
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

    def populate(self, root: Path, project_id: str, problem: str, selected, *, sheets=None):
        if not isinstance(problem, str) or not problem.strip():
            raise ValueError('题目文本不能为空。')
        content = problem.encode('utf-8')
        if len(content) > self.limits.max_file_bytes:
            raise ValueError('题目文本超过字节上限。')
        entries = [self._entry(root, project_id, '00_inputs/problem-v0001.txt',
                               'problem-v0001.txt', content, 'problem', ('encoding=utf-8',))]
        for index, (source, kind) in enumerate(selected, start=1):
            content = read_bounded(source, self.limits)
            relative = f'00_inputs/source-{index:04d}-v0001.{kind}'
            entry = self._entry(root, project_id, relative, source.name, content, kind, ('opaque-copy',))
            copied = root / relative
            if kind in ('csv', 'xlsx'):
                table = (read_csv(copied, limits=self.limits) if kind == 'csv' else
                         read_xlsx(copied, sheet=(sheets or {}).get(source.name), limits=self.limits))
                entry = {**entry, 'parser_notes': list(table.notes)}
                profile = profile_table(table)
                write_json(root / f'10_analysis/source-{index:04d}-profile.json', profile)
                write_json(root / f'10_analysis/source-{index:04d}-dictionary.json',
                           {'sheet_name': table.sheet_name, 'columns': profile['columns']})
            # Detect source changes while copying/parsing; never edit the source.
            if hashlib.sha256(read_bounded(source, self.limits)).hexdigest() != entry['sha256']:
                raise ValueError('导入期间源文件发生变化，请重试。')
            entries.append(entry)
        write_json(root / '00_inputs/manifest-v0001.json', {'version': 1, 'sources': entries})
        return tuple(SourceFile(**entry['record']) for entry in entries)
