"""Truthful command-line bootstrap until the desktop workflow is implemented."""
import argparse
import sqlite3
import sys
from mathmodel_paper_studio.bootstrap.paths import AppPaths, PathPolicyError


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='数模论文工作台：检查 F: 存储基础环境')
    parser.add_argument('--root', help='应用数据根目录；必须在批准的 F: 工作目录内')
    parser.add_argument('--check', action='store_true', help='初始化并检查目录及数据库结构')
    args = parser.parse_args(argv)
    try:
        paths = AppPaths(args.root) if args.root is not None else AppPaths()
        paths.configure_environment()
        from mathmodel_paper_studio.storage.database import Database
        Database(paths).migrate()
    except (PathPolicyError, OSError, RuntimeError, sqlite3.Error) as exc:
        print(f'启动检查失败：{exc}', file=sys.stderr)
        return 1
    print('基础存储检查通过。当前仅完成项目骨架，论文生成和桌面流程尚未实现。')
    for label, path in (('数据根目录', paths.root), ('数据库', paths.database),
                        ('项目', paths.projects), ('日志', paths.logs), ('缓存', paths.cache),
                        ('临时文件', paths.temp), ('构建输出', paths.build), ('测试输出', paths.tests)):
        print(f'{label}：{path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
