"""Read the newest immutable input manifest in a project."""
import json
import re
from pathlib import Path

from mathmodel_paper_studio.bootstrap.paths import validate_managed_path

MAX_MANIFEST_BYTES = 1024 * 1024
_MANIFEST_NAME = re.compile(r'^manifest-v([0-9]{4})\.json$')


def latest_manifest(project_root: Path) -> tuple[int, Path, dict]:
    root = validate_managed_path(Path(project_root))
    if not root.is_dir():
        raise ValueError('项目目录不存在。')
    candidates = []
    for path in root.joinpath('00_inputs').glob('manifest-v*.json'):
        match = _MANIFEST_NAME.fullmatch(path.name)
        if match is not None:
            candidates.append((int(match.group(1)), path))
    if not candidates:
        raise ValueError('项目输入清单缺失。')
    version, path = max(candidates, key=lambda item: item[0])
    path = validate_managed_path(path)
    if (not path.is_relative_to(root) or not path.is_file()
            or path.stat().st_size > MAX_MANIFEST_BYTES):
        raise ValueError('项目输入清单缺失或超过 1 MiB 上限。')
    value = json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(value, dict) or value.get('version') != version
                or not isinstance(value.get('sources'), list) or len(value['sources']) > 34):
        raise ValueError('项目输入清单结构无效。')
    return version, path, value
