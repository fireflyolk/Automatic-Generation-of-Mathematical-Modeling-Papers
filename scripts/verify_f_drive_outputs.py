"""F:-only desktop bootstrap and reproducible release inventory.

Only explicitly named F: trees are inspected. Native provider login stores
are never discovered, read, copied, or bundled.
"""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import sys

sys.dont_write_bytecode = True
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mathmodel_paper_studio.bootstrap.paths import AppPaths, validate_managed_path

_SENSITIVE_NAMES = frozenset((
    ".env", "auth.json", "credentials", "credentials.json", "id_rsa", "id_ed25519",
    ".claude.json", ".netrc", "_netrc",
))
_SECRET = re.compile(
    rb"sk-[A-Za-z0-9_-]{24,}|AKIA[A-Z0-9]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\r\n]+[A-Za-z0-9+/]{32,}={0,2}[\r\n]"
    rb"|(?:api[_-]?key|access[_-]?token|password)\s*[=:]\s*[\"']?[A-Za-z0-9_./+-]{24,}",
    re.IGNORECASE,
)
_PIN = re.compile(r"^([A-Za-z0-9_.-]+)==([A-Za-z0-9.+!-]+)(?:\s+--hash=sha256:[a-f0-9]{64})*$")
_MAX_FILES = 50000
_MAX_FILE_BYTES = 1024 * 1024 * 1024


def write_json(path, payload):
    path = validate_managed_path(Path(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    validate_managed_path(path.parent)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, indent=2, ensure_ascii=False)
        stream.write("\n")


def inventory_tree(root):
    """Hash every regular file and reject reparse escapes and secret material."""
    root = validate_managed_path(Path(root))
    if not root.is_dir():
        raise ValueError("Audit root must be an existing F: directory.")
    records = []
    for directory, children, names in os.walk(root, followlinks=False):
        validate_managed_path(Path(directory))
        for name in children:
            validate_managed_path(Path(directory) / name)
            if name.casefold() in (".codex", ".claude", ".ssh"):
                raise ValueError("Native login/config directory is forbidden: " + name)
        for name in sorted(names):
            path = validate_managed_path(Path(directory) / name)
            relative = path.relative_to(root).as_posix()
            if name.casefold() in _SENSITIVE_NAMES or name.casefold().startswith(".env."):
                raise ValueError("Credential/config file is forbidden: " + relative)
            before = path.stat()
            if not path.is_file() or before.st_size > _MAX_FILE_BYTES:
                raise ValueError("Non-file or oversized package member: " + relative)
            value = hashlib.sha256()
            tail = b""
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(65536), b""):
                    if _SECRET.search(tail + chunk):
                        raise ValueError("Possible credential material in: " + relative)
                    value.update(chunk)
                    tail = chunk[-256:]
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError("File changed during audit: " + relative)
            records.append({"path": relative, "size": before.st_size, "sha256": value.hexdigest()})
            if len(records) > _MAX_FILES:
                raise ValueError("Package member count exceeds the audit limit.")
    return tuple(sorted(records, key=lambda item: item["path"]))


def read_lock(path):
    path = validate_managed_path(Path(path))
    records = []
    for line in path.read_text("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = _PIN.fullmatch(line)
        if match is None:
            raise ValueError("Lock file contains an unpinned or unsupported requirement.")
        records.append((match[1], match[2]))
    normalized = tuple(name.lower().replace("_", "-") for name, _ in records)
    if not records or len(set(normalized)) != len(records):
        raise ValueError("Lock file is empty or contains duplicate distributions.")
    return tuple(records)


def verify_lock(path):
    versions = []
    for name, expected in read_lock(path):
        actual = importlib.metadata.version(name)
        if actual != expected:
            raise ValueError("Locked dependency mismatch: " + name)
        versions.append({"name": name, "version": actual})
    return tuple(versions)


def export_licenses(lock_path, output):
    output = validate_managed_path(Path(output))
    output.mkdir(parents=True, exist_ok=False)
    notices = []
    for name, version in read_lock(lock_path):
        dist = importlib.metadata.distribution(name)
        files = []
        for member in dist.files or ():
            text = str(member).replace("\\", "/")
            if ((member.name.lower().startswith(("license", "licence", "copying", "notice"))
                    or "/licenses/" in text.lower())
                    and member.suffix.lower() not in (".py", ".pyc", ".pyo", ".pyd", ".dll", ".so")):
                source = validate_managed_path(Path(dist.locate_file(member)))
                if not source.is_file():
                    continue
                # Store wheel-relative paths only; never follow metadata ../ escapes.
                parts = tuple(p for p in Path(text).parts if p not in (".", ".."))
                target = validate_managed_path(output / name / Path(*parts))
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                files.append(target.relative_to(output).as_posix())
        notices.append({
            "name": name, "version": version,
            "license_expression": dist.metadata.get("License-Expression"),
            "license": dist.metadata.get("License"),
            "classifiers": tuple(c for c in dist.metadata.get_all("Classifier", ())
                                 if c.startswith("License ::")),
            "license_files": tuple(files),
        })
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file():
        source = validate_managed_path(python_license)
        shutil.copyfile(source, output / "CPython-LICENSE.txt")
    write_json(output / "THIRD_PARTY_NOTICES.json", notices)
    return tuple(notices)


def is_forbidden_archive_member(name):
    """Reject native config/credential and test paths by complete components.

    Substring matching would incorrectly reject application modules such as
    ``providers.codex_cli`` because their name contains ``.codex``.
    """
    if not isinstance(name, str) or not name:
        return True
    parts = tuple(part.casefold() for part in name.replace('\\', '/').split('/') if part)
    if any(part in ('.codex', '.claude', 'auth.json', 'credentials.json') for part in parts):
        return True
    dotted_parts = tuple(token for part in parts for token in part.split('.'))
    return 'tests' in dotted_parts


def archive_members(executable):
    executable = validate_managed_path(Path(executable))
    from PyInstaller.archive.readers import CArchiveReader
    archive = CArchiveReader(str(executable))
    names = list(archive.toc)
    for name in tuple(names):
        if name.endswith(".pyz"):
            names.extend("PYZ/" + item for item in archive.open_embedded_archive(name).toc)
    if any(is_forbidden_archive_member(name) for name in names):
        raise ValueError("Forbidden test/config material in executable archive.")
    return tuple(sorted(names))


def prepare_startup(root=None):
    paths = AppPaths(root) if root is not None else AppPaths()
    paths.configure_environment()
    if getattr(sys, "frozen", False):
        validate_managed_path(Path(sys.executable))
        validate_managed_path(Path(sys._MEIPASS))
    from mathmodel_paper_studio.storage.database import Database
    Database(paths).migrate()
    managed = (paths.root, paths.state, paths.database, paths.projects, paths.logs,
               paths.cache, paths.temp, paths.build, paths.tests)
    return paths, {"status": "ok", "app_version": "0.1.0", "python": sys.version.split()[0],
                   "frozen": bool(getattr(sys, "frozen", False)),
                   "managed_paths": tuple(str(p) for p in managed)}


def launch_desktop(paths):
    from PySide6.QtCore import QSettings, QThreadPool
    from PySide6.QtWidgets import QApplication
    from mathmodel_paper_studio.ui.fluent_theme import apply_fluent_theme
    from mathmodel_paper_studio.ui.main_window import MainWindow
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(paths.state))
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.SystemScope, str(paths.state))
    app = QApplication([sys.argv[0]])
    app.setOrganizationName("MathModelPaperStudio")
    app.setApplicationName("MathModelPaperStudio")
    apply_fluent_theme(app)
    window = MainWindow(paths)
    window.show()
    code = app.exec()
    QThreadPool.globalInstance().waitForDone()
    return code


def main(argv=None):
    parser = argparse.ArgumentParser(description="F: desktop bootstrap and release auditor")
    parser.add_argument("--root")
    parser.add_argument("--startup-check", action="store_true")
    parser.add_argument("--report")
    parser.add_argument("--inventory-root")
    parser.add_argument("--verify-lock")
    parser.add_argument("--licenses")
    parser.add_argument("--archive")
    args = parser.parse_args(argv)
    try:
        if args.inventory_root:
            payload = {"files": inventory_tree(args.inventory_root)}
        elif args.verify_lock:
            versions = verify_lock(args.verify_lock)
            payload = {"dependencies": versions}
            if args.licenses:
                payload["licenses"] = export_licenses(args.verify_lock, args.licenses)
        elif args.archive:
            payload = {"members": archive_members(args.archive)}
        else:
            paths, payload = prepare_startup(args.root)
            if not args.startup_check:
                return launch_desktop(paths)
        if args.report:
            write_json(args.report, payload)
        if sys.stdout is not None:
            print(json.dumps({"status": "ok", "report": args.report}))
        return 0
    except (ValueError, OSError, RuntimeError, importlib.metadata.PackageNotFoundError) as exc:
        # File/content values and credentials are never included in diagnostics.
        message = "\u542f\u52a8/\u53d1\u5e03\u68c0\u67e5\u5931\u8d25\uff1a" + str(exc)
        if sys.stderr is not None:
            print(message, file=sys.stderr)
        if not args.startup_check and not any((args.inventory_root, args.verify_lock, args.archive)):
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "\u6570\u6a21\u8bba\u6587\u5de5\u4f5c\u53f0", 0x10)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
