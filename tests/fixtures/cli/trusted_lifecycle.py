"""Trusted, disposable process fixture. Never a provider or sandbox demonstration."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

mode, folder = sys.argv[1:3]
root = Path(folder)
if not root.is_absolute() or root.drive.casefold() != 'f:':
    raise SystemExit(99)
if mode == 'closedpipes':
    with (root / 'sink.log').open('wb') as sink:
        child = subprocess.Popen([sys.executable, '-I', '-B', __file__, 'child', str(root)], stdin=subprocess.DEVNULL, stdout=sink, stderr=sink, env=dict(os.environ), cwd=root)
        (root / 'child.pid').write_text(str(child.pid), encoding='ascii')
        deadline = time.monotonic() + 3
        while not (root / 'grandchild.pid').exists() and time.monotonic() < deadline:
            time.sleep(.01)
    print(json.dumps({'kind': 'completed', 'text': ''}), flush=True)
    raise SystemExit(0)
if mode == 'splitredaction':
    for fragment in ['sk-fixture-', 'sensitive-tail ', 'C:\\outside\\', 'private-filename.txt']:
        print(json.dumps({'kind': 'text', 'text': fragment}), flush=True)
    print(json.dumps({'kind': 'completed', 'text': ''}), flush=True)
    raise SystemExit(0)
if mode in ('tree', 'rootexit', 'child'):
    next_mode = 'grandchild' if mode == 'child' else 'child'
    child = subprocess.Popen([sys.executable, '-I', '-B', __file__, next_mode, str(root)],
                             env=dict(os.environ), cwd=root)
    (root / (next_mode + '.pid')).write_text(str(child.pid), encoding='ascii')
    if mode == 'rootexit':
        raise SystemExit(0)
if mode in ('tree', 'rootexit', 'child', 'grandchild', 'timeout'):
    time.sleep(60)
elif mode == 'output':
    sys.stdout.write('x' * 500000)
    sys.stdout.flush()
elif mode == 'malformed':
    print('{bad json}', flush=True)
    time.sleep(60)
elif mode == 'nonzero':
    print(json.dumps({'kind': 'completed', 'text': ''}), flush=True)
    raise SystemExit(7)
elif mode == 'private-diagnostic':
    sys.stderr.write('opaque-account-identifier copied-input-cell\n')
    print(json.dumps({'kind': 'completed', 'text': ''}), flush=True)
elif mode == 'redaction':
    for fragment in ['sk-fixture-', 'secret', '\nC:\\outside\\', 'private.txt\n']:
        sys.stderr.write(fragment)
        sys.stderr.flush()
        time.sleep(.02)
    print(json.dumps({'kind': 'text', 'text': 'sk-fixture-secret C:\\outside\\private.txt'}), flush=True)
    print(json.dumps({'kind': 'completed', 'text': ''}), flush=True)
else:
    print(json.dumps({'kind': 'text', 'text': '你好'}), flush=True)
    print(json.dumps({'kind': 'completed', 'text': ''}), flush=True)


