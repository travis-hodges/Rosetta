"""Stdlib terminal entry point. Runtime execution remains in rosetta.core."""

from __future__ import annotations

import argparse
from dataclasses import asdict, fields
import importlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
DELEGATES = {
    "bench": "rosetta.bench.run",
    "report": "rosetta.bench.report",
    "demo": "rosetta.demo.repair_loop",
    "tools": "rosetta.tools.server",
}


def _opencode() -> str:
    binary = shutil.which("opencode")
    if not binary:
        raise ValueError("OpenCode is not installed. Install OpenCode, then run rosetta code again.")
    return binary


def _config() -> dict:
    path = ROOT / "opencode.json"
    if not path.is_file():
        raise ValueError("Coding requires a source checkout. Run bash scripts/install.sh --bin-dir PATH from that checkout.")
    config = json.loads(path.read_text())
    config["mcp"]["rosetta"]["command"] = [sys.executable, "-m", "rosetta.tools"]
    config["mcp"]["rosetta"].setdefault("environment", {})["PYTHONPATH"] = str(ROOT)
    # Absolute paths are generated in memory, never written into the checkout.
    config["instructions"] = [str(ROOT / ".opencode" / "instructions.md")]
    for name in ("rosetta", "explain", "divergence"):
        profile = ROOT / ".opencode" / "agent" / f"{name}.md"
        content = profile.read_text().split("---", 2)[-1].strip()
        config.setdefault("agent", {}).setdefault(name, {}).update({
            "prompt": content, "mode": "primary" if name == "rosetta" else "subagent",
            "description": f"Rosetta {name} agent",
        })
    return config


def _code(args: argparse.Namespace) -> int:
    if args.timeout is not None and args.prompt is None:
        raise ValueError("--timeout requires --prompt; interactive sessions have no time limit.")
    project = args.project.resolve()
    if not project.is_dir():
        raise ValueError(f"Project directory does not exist: {project}")
    env = os.environ.copy()
    env["PWD"] = str(project)
    config = _config()
    # Preserve user's provider/model definitions supplied through OpenCode.
    if env.get("OPENCODE_CONFIG_CONTENT"):
        supplied = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        config.update({key: value for key, value in supplied.items() if key not in ("mcp", "agent")})
    env["OPENCODE_CONFIG_CONTENT"] = json.dumps(config)
    if args.corpus:
        corpus = args.corpus.resolve()
        if not corpus.is_dir():
            raise ValueError(f"Corpus directory does not exist: {corpus}")
        env["ROSETTA_CORPUS_DIR"] = str(corpus)
    cmd = [_opencode()]
    if args.prompt is not None:
        cmd += ["run", "--dir", str(project)]
    else:
        cmd += [str(project)]
    cmd += ["--agent", "rosetta"]
    if args.model:
        cmd += ["--model", args.model]
    if args.prompt is not None:
        cmd += ["--", args.prompt]
    try:
        return subprocess.run(
            cmd, cwd=project, env=env,
            timeout=(args.timeout or 300.0) if args.prompt is not None else None,
        ).returncode
    except subprocess.TimeoutExpired:
        # Do not include the command: the prompt may contain private source.
        print("ERROR: OpenCode prompt exceeded its time limit.", file=sys.stderr)
        return 124


def _doctor(args: argparse.Namespace) -> int:
    from rosetta.tools.server import RosettaServer
    from rosetta.tools.sources import default_store

    report = {
        "python": sys.version.split()[0],
        "opencode": shutil.which("opencode"),
        "source_checkout": (ROOT / "opencode.json").is_file(),
        "mcp_tools": len(RosettaServer().registry.list()),
        "corpus": str(default_store().corpus_dir),
        "routines": len(default_store().corpus_names()),
        "runtime": "not checked (use --runtime)",
        "runtime_languages": ["MUMPS"],
        "model_specialization": "user-selected provider/model; no bundled trained weights",
    }
    report["ok"] = bool(report["opencode"] and report["source_checkout"])
    code = 0 if report["ok"] else 1
    if args.runtime:
        from rosetta.core import ExecSpec, shutdown, verify_equivalence

        source = 'ROSCHK\n Q\nVALUE(X)\n Q X+1\n'
        try:
            verdict = verify_equivalence("ROSCHK", source, source, [ExecSpec("ROSCHK", "$$VALUE", ["1"])])
            passed = verdict.equivalent and verdict.n_cases == 1 and verdict.n_void == 0
            report["runtime"] = {"ok": passed, "verdict": asdict(verdict)}
            report["ok"] = report["ok"] and passed
            code = 0 if report["ok"] else 1
        except Exception as exc:
            report["runtime"] = {"ok": False, "error": str(exc)}
            report["ok"] = False
            code = 2
        finally:
            shutdown()
    print(json.dumps(report, indent=2))
    return code


def _evaluate(args: argparse.Namespace) -> int:
    from rosetta.core import ExecSpec, shutdown, verify_equivalence
    from rosetta.tools.cases import spec_from_dict
    from rosetta.tools.sources import normalise_name

    if args.out:
        for source in (args.baseline, args.candidate, args.cases):
            if args.out.resolve() == source.resolve() or (
                args.out.exists() and source.exists() and args.out.samefile(source)
            ):
                raise ValueError("--out must not overwrite baseline, candidate, or cases (including file aliases).")

    document = json.loads(args.cases.read_text())
    cases = document.get("cases") if isinstance(document, dict) else document
    if not isinstance(cases, list) or not cases:
        raise ValueError("Cases must be a nonempty JSON array (or an object with a cases array).")
    specs = []
    allowed = {field.name for field in fields(ExecSpec)}
    for index, case in enumerate(cases):
        if not isinstance(case, dict) or set(case) - allowed:
            raise ValueError(f"Case {index + 1} must be an object containing only ExecSpec fields.")
        if not isinstance(case.get("routine"), str):
            raise ValueError(f"Case {index + 1} must name its routine as a string.")
        normalise_name(case["routine"])
        timeout = case.get("timeout_s", 10.0)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not (0 < timeout <= sys.float_info.max):
            raise ValueError(f"Case {index + 1} timeout_s must be a finite positive number.")
        specs.append(spec_from_dict(case))
    stem = args.baseline.stem
    routine = normalise_name(args.routine or ("%" + stem[1:] if stem.startswith("_") else stem))
    if any(spec.routine != routine for spec in specs):
        raise ValueError("Every case must name the evaluated routine.")
    try:
        report = verify_equivalence(routine, args.baseline.read_text(), args.candidate.read_text(), specs)
        rendered = json.dumps(asdict(report), indent=2)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(rendered + "\n")
        print(rendered)
        return 0 if report.equivalent and report.n_void == 0 and report.n_cases == len(specs) else 1
    finally:
        shutdown()


def _positive_seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a finite positive number") from exc
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return seconds


def main(argv: list[str] | None = None) -> int:
    """Run a terminal command and preserve failure exit statuses."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in DELEGATES:
        try:
            return importlib.import_module(DELEGATES[argv[0]]).main(argv[1:])
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
    parser = argparse.ArgumentParser(prog="rosetta", description="Code with your model. Evaluate changes against real execution.")
    sub = parser.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor", help="check local components; optionally execute a runtime probe")
    doctor.add_argument("--runtime", action="store_true")
    code = sub.add_parser("code", help="start OpenCode with Rosetta tools and your selected model")
    code.add_argument("project", nargs="?", type=Path, default=Path.cwd())
    code.add_argument("--model", default=os.environ.get("ROSETTA_MODEL"), help="provider/model; local or hosted")
    code.add_argument("--prompt", help="run one prompt instead of the interactive terminal")
    code.add_argument("--timeout", type=_positive_seconds, help="prompt time limit in seconds (default: 300; requires --prompt)")
    code.add_argument("--corpus", type=Path, help="optional local MUMPS corpus directory")
    models = sub.add_parser("models", help="list OpenCode models, including configured specialist endpoints")
    models.add_argument("provider", nargs="?")
    evaluate = sub.add_parser("eval", help="compare baseline and candidate MUMPS source with a case suite")
    evaluate.add_argument("--baseline", type=Path, required=True)
    evaluate.add_argument("--candidate", type=Path, required=True)
    evaluate.add_argument("--cases", type=Path, required=True)
    evaluate.add_argument("--routine")
    evaluate.add_argument("--out", type=Path)
    for name in DELEGATES:
        sub.add_parser(name, help=f"run {name}; use {name} --help for options")
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            return _doctor(args)
        if args.command == "code":
            return _code(args)
        if args.command == "models":
            return subprocess.run([_opencode(), "models", *([args.provider] if args.provider else [])]).returncode
        return _evaluate(args)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
