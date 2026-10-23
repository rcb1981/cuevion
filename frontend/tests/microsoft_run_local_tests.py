"""Run reviewed local unit suites in separate, credential-free processes."""
from pathlib import Path
import argparse
import shutil
import tempfile
import concurrent.futures
import json
import os
import re
import subprocess
import sys
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--group', choices=('microsoft', 'frontend', 'python'), default='microsoft')
parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
parser.add_argument('--output', type=Path, help='External folder for logs; defaults to a fresh temporary directory')
parser.add_argument('--suite', action='append', help='Run only this discovered/reviewed suite; may be repeated')
args = parser.parse_args()
ROOT = args.root.resolve()
FRONTEND = ROOT / 'frontend'
if not (FRONTEND / 'package.json').is_file():
    parser.error('Expected a complete checkout with frontend/package.json')
EVIDENCE = args.output.resolve() if args.output else Path(tempfile.mkdtemp(prefix='cuevion-microsoft-verification-'))
if EVIDENCE == ROOT or ROOT in EVIDENCE.parents:
    parser.error('Keep test logs and bootstrap files outside the checkout')
EVIDENCE.mkdir(parents=True, exist_ok=True)
NODE = shutil.which('node')
PYTHON = sys.executable
MODE = args.group
LABEL = MODE
if MODE != 'python':
    if not NODE:
        parser.error('Node.js is required; no packages are installed by this runner')
    for name, expected in [('sucrase', '3.35.1'), ('react', '18.3.1'), ('react-dom', '18.3.1'), ('react-test-renderer', '18.3.1')]:
        manifest = FRONTEND / 'node_modules' / name / 'package.json'
        if not manifest.is_file():
            parser.error('Missing local test prerequisite: ' + name + '@' + expected)
        actual = json.loads(manifest.read_text())['version']
        if actual != expected:
            parser.error('Expected ' + name + '@' + expected + ', found ' + actual)
ENV = {
    'PATH': os.pathsep.join(dict.fromkeys([str(Path(PYTHON).parent), str(Path(NODE).parent) if NODE else '/usr/local/bin', '/usr/local/bin', '/usr/bin', '/bin', '/usr/sbin', '/sbin'])),
    'TZ': 'Europe/Amsterdam',
    'LANG': 'en_US.UTF-8',
    'PYTHONDONTWRITEBYTECODE': '1',
    'PYTHONPATH': str(EVIDENCE) + ':' + str(FRONTEND),
    'NODE_OPTIONS': '--require=' + str(EVIDENCE / 'microsoft-node-no-network.cjs'),
    'SUCRASE_OPTIONS': '{"jsxRuntime":"automatic"}',
}

(EVIDENCE / 'microsoft-node-no-network.cjs').write_text("'use strict';\n// Local verification guard. Tests may replace fetch with an in-memory fake.\nlet attempts = 0;\nconst blocked = () => {\n  attempts += 1;\n  throw new Error('LOCAL_VERIFICATION_NETWORK_BLOCKED');\n};\nglobalThis.fetch = blocked;\nfor (const protocol of ['node:http', 'node:https']) {\n  const transport = require(protocol);\n  transport.request = blocked;\n  transport.get = blocked;\n}\nconst net = require('node:net');\nnet.connect = blocked;\nnet.createConnection = blocked;\nnet.Socket.prototype.connect = blocked;\nrequire('node:tls').connect = blocked;\nrequire('node:dgram').createSocket = blocked;\nprocess.on('exit', () => {\n  if (attempts) {\n    process.stderr.write(`LOCAL_VERIFICATION_NETWORK_ATTEMPTS=${attempts}\\n`);\n    process.exitCode = 1;\n  }\n});\n")
(EVIDENCE / 'sitecustomize.py').write_text('"""Temporary unit-test transport guard; never installed into application source."""\nimport atexit\nimport os\nimport socket\nimport sys\n\n_attempts = 0\n\ndef _blocked(*args, **kwargs):\n    global _attempts\n    _attempts += 1\n    raise RuntimeError(\'LOCAL_VERIFICATION_NETWORK_BLOCKED\')\n\nsocket.create_connection = _blocked\nsocket.socket.connect = _blocked\nsocket.socket.connect_ex = _blocked\nsocket.getaddrinfo = _blocked\n\ndef _report():\n    if _attempts:\n        sys.stderr.write(f\'LOCAL_VERIFICATION_NETWORK_ATTEMPTS={_attempts}\\n\')\n        sys.stderr.flush()\n        os._exit(1)\n\natexit.register(_report)\n')

def reviewed_frontend_suites():
    package = json.loads((FRONTEND / 'package.json').read_text())
    paths = set()
    for name, command in package['scripts'].items():
        if name.startswith('test:'):
            paths.update(re.findall(r"require\('\./(src/[^']+\.test\.ts)'\)", command))
    for path in (FRONTEND / 'src').rglob('*'):
        relative = path.relative_to(FRONTEND).as_posix()
        if not re.search(r'\.test\.(ts|tsx|mjs|cjs)$', relative):
            continue
        if '.web.test.' in relative:
            continue
        if 'microsoft' in relative.lower() or 'collaboration' in relative.lower():
            paths.add(relative)
    paths.add('src/components/auth/Auth0LoginView.test.ts')
    return sorted(path for path in paths if MODE != 'microsoft' or 'microsoft' in path.lower())

def reviewed_python_suites():
    names = [
        'api.auth.test_auth0_flow',
        'api.auth.test_auth_routes',
        'api.auth.test_session_store',
        'api.auth.test_account_authority',
        'api.collaboration.test_authorization',
        'api.collaboration.test_models',
        'api.collaboration.test_http_boundary',
        'api.collaboration.test_owner_http',
        'api.collaboration.test_guest_http',
        'api.collaboration.test_source_message',
        'api.inboxes.gmail_oauth_lifecycle_test',
    ]
    names.extend('api.inboxes.' + path.stem for path in (FRONTEND / 'api/inboxes').glob('test_*.py')
                 if re.search(r'gmail|imap', path.name))
    return sorted(set(names))

def command_for(name):
    if MODE == 'python':
        return [PYTHON, '-B', '-m', 'unittest', name, '-q']
    if name.endswith(('.mjs', '.cjs')):
        return [NODE, '--test', name]
    return [NODE, '-r', str(FRONTEND / 'node_modules/sucrase/register/ts.js'),
            '-r', str(FRONTEND / 'node_modules/sucrase/register/tsx.js'),
            '-e', 'require(' + json.dumps('./' + name) + ')']

def run(name):
    start = time.monotonic()
    command = command_for(name)
    try:
        result = subprocess.run(command, cwd=FRONTEND, env=ENV,
                                capture_output=True, text=True, timeout=90)
        code = result.returncode
        output = result.stdout + result.stderr
    except subprocess.TimeoutExpired as error:
        code = 124
        output = 'TIMEOUT\n' + str(error.stdout or '') + str(error.stderr or '')
    filename = LABEL + '-' + re.sub(r'[^a-zA-Z0-9_.-]', '_', name) + '.log'
    (EVIDENCE / filename).write_text(output)
    count = re.search(r'Ran (\d+) tests?', output) if MODE == 'python' else None
    entry = {'suite': name, 'exit_code': code, 'seconds': round(time.monotonic() - start, 3),
             'log': filename, 'network_attempts': bool(re.search('LOCAL_VERIFICATION_NETWORK_ATTEMPTS=', output)),
             'command': command}
    if count:
        entry['tests'] = int(count.group(1))
    print(('PASS' if code == 0 else 'FAIL') + ' ' + name, flush=True)
    if code:
        print(output[-2400:], flush=True)
    return entry

available = reviewed_python_suites() if MODE == 'python' else reviewed_frontend_suites()
if args.suite and any(name not in available for name in args.suite):
    parser.error('A requested suite is outside the discovered/reviewed selection')
names = args.suite or available
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    results = list(pool.map(run, names))
report = {'mode': MODE, 'root': str(ROOT), 'timezone': ENV['TZ'],
          'isolation': 'separate process per suite; explicit empty-derived environment; transport guard; no credentials',
          'suites': len(results), 'passed': sum(r['exit_code'] == 0 for r in results),
          'failed': sum(r['exit_code'] != 0 for r in results),
          'network_attempt_suites': sum(r['network_attempts'] for r in results),
          'tests_reported': sum(r.get('tests', 0) for r in results), 'results': results}
(EVIDENCE / ('microsoft-' + LABEL + ('-targeted' if args.suite else '') + '-regressions.json')).write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps({k:v for k,v in report.items() if k != 'results'}, indent=2), flush=True)
print('Evidence: ' + str(EVIDENCE), flush=True)
sys.exit(1 if report['failed'] else 0)
