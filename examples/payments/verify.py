"""Execute payment cases through the real Rosetta core; no simulated verdicts."""
from __future__ import annotations
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
# Source checkout convenience; installed launchers also supply PYTHONPATH.
for parent in ROOT.parents:
    if (parent / 'rosetta/core').is_dir():
        sys.path.insert(0, str(parent))
        break
from rosetta.core import ExecSpec, Runtime


def verify(cases_path: Path) -> dict:
    cases = json.loads(cases_path.read_text())
    if not isinstance(cases, list) or not cases:
        raise ValueError('Expected a nonempty JSON array of cases')
    sources = {p.stem: p.read_text() for p in sorted((ROOT / 'routines').glob('*.m'))}
    outcomes = []
    with Runtime() as runtime:
        # Load callees before callers; loading all before execution also supports
        # newly added routines. The capture planner resolves local source via env.
        for name in sorted(sources, key=lambda x: (x != 'PAYSTORE', x)):
            runtime.load_routine(name, sources[name])
        for case in cases:
            spec = ExecSpec(routine=case['routine'], entry=case['entry'],
                            args=case.get('args', []), globals_in=case.get('globals_in', {}),
                            locals_in=case.get('locals_in', {}))
            with runtime.clean_state():
                result = runtime.execute(spec)
            errors = []
            if result.error or result.void or result.restarts:
                errors.append({'runtime': asdict(result)})
            if result.stdout != case['stdout']:
                errors.append({'stdout': {'expected': case['stdout'], 'actual': result.stdout}})
            if result.globals_out != case['globals_out']:
                errors.append({'globals': {'expected': case['globals_out'], 'actual': result.globals_out}})
            outcomes.append({'name': case['name'], 'passed': not errors, 'failures': errors})
    return {'runtime': 'YottaDB', 'isolation': 'clean_state with per-case transaction rollback',
            'sources': {name: hashlib.sha256(source.encode()).hexdigest() for name, source in sources.items()},
            'cases_sha256': hashlib.sha256(cases_path.read_bytes()).hexdigest(),
            'passed': sum(x['passed'] for x in outcomes), 'total': len(outcomes), 'cases': outcomes}


def main() -> int:
    import os
    os.environ['ROSETTA_CORPUS_DIR'] = str(ROOT / 'routines')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, default=ROOT / 'cases.json')
    args = parser.parse_args()
    try:
        result = verify(args.cases)
    except Exception as exc:
        print(json.dumps({'error': str(exc), 'type': type(exc).__name__, 'verified': False}), flush=True)
        return 2
    print(json.dumps(result, indent=2), flush=True)
    print(f"{result['passed']}/{result['total']} tests passed", flush=True)
    return 0 if result['passed'] == result['total'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
