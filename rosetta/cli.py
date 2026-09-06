"""``rosetta`` -- the OpenCode-derived TUI and one front door for Rosetta.

Before this module there were eleven entry points (``python3 -m
rosetta.core.selftest``, ``python3 -m rosetta.bench.build``, ``python3 -m
rosetta.train.sft``, ...) and no way to tell from the outside which one you
wanted. The modules were fine; the surface was the problem. This file adds no
capability. It names the five workflows in the words a user would use, and
every command ends by printing the next one.

    rosetta                     open the Rosetta TUI in the current project
    rosetta status              what is wired, what is not, what to run next
    rosetta doctor              can this machine actually do the work
    rosetta edit ROUTINE        change a routine with the verifier in the loop
    rosetta verify ROUTINE      check a change you already made
    rosetta model add ...       bring your own model
    rosetta bench run           measure a model on the held-out eval set
    rosetta train sft           turn verified work into training data
    rosetta gui                 optional browser view over the same workflows

Two rules this module keeps, because it is the first thing anyone touches:

  * It never fakes a capability. A step that is not wired says so and names
    what is missing -- the same discipline the website keeps by showing a
    pending state instead of a placeholder number.
  * ``rosetta --help`` and ``rosetta status`` work offline, with no container,
    network or credentials. Everything that needs those is imported inside the
    handler that needs it.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, fields
import json
import math
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Sequence

from rosetta import __version__

REPO_ROOT = Path(__file__).resolve().parents[1]
ROOT = REPO_ROOT  # compatibility for source-install callers

# The five workflows, in the order they are usually met. The status command
# prints this, and it is the same order used in docs/WORKFLOWS.md.
WORKFLOWS: tuple[tuple[str, str, str], ...] = (
    ("edit", "Change a routine, verified", "rosetta edit DPTLK --request '...'"),
    ("verify", "Check a change you already made", "rosetta verify DPTLK -c new.m"),
    ("model", "Bring your own model", "rosetta model add my-model provider/id"),
    ("bench", "Measure a model on held-out tasks", "rosetta bench run --model my-model"),
    ("train", "Turn verified work into training data", "rosetta train sft"),
)


# --------------------------------------------------------------------------
# Primary TUI
# --------------------------------------------------------------------------

def _opencode() -> str:
    binary = shutil.which("opencode")
    if not binary:
        raise ValueError("OpenCode is not installed. Install OpenCode, then run rosetta again.")
    return binary


def _command_profiles() -> dict[str, dict[str, object]]:
    """Inline Rosetta slash commands when the TUI opens another project."""
    commands: dict[str, dict[str, object]] = {}
    launcher = (
        f"PYTHONPATH={shlex.quote(str(REPO_ROOT))} "
        f"{shlex.quote(sys.executable)} -m rosetta"
    )
    for path in sorted((REPO_ROOT / ".opencode" / "command").glob("*.md")):
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---\n") or "\n---\n" not in text[4:]:
            raise ValueError(f"Invalid OpenCode command profile: {path}")
        header, template = text[4:].split("\n---\n", 1)
        entry: dict[str, object] = {
            "template": template.strip().replace("python3 -m rosetta", launcher)
        }
        for line in header.splitlines():
            if not line.strip():
                continue
            if ":" not in line:
                raise ValueError(f"Invalid OpenCode command metadata in {path}: {line}")
            key, value = (part.strip() for part in line.split(":", 1))
            if key not in {"description", "agent", "subtask"}:
                raise ValueError(f"Unsupported OpenCode command field {key!r} in {path}")
            entry[key] = value.lower() == "true" if key == "subtask" else value
        commands[path.stem] = entry
    return commands


def _config() -> dict[str, Any]:
    """Build a portable OpenCode config without writing into the target project."""
    path = REPO_ROOT / "opencode.json"
    if not path.is_file():
        raise ValueError(
            "Coding requires a source checkout. Reinstall the launcher from that checkout."
        )
    config = json.loads(path.read_text(encoding="utf-8"))
    config["mcp"]["rosetta"]["command"] = [sys.executable, "-m", "rosetta.tools"]
    config["mcp"]["rosetta"].setdefault("environment", {})["PYTHONPATH"] = str(REPO_ROOT)
    config["instructions"] = [str(REPO_ROOT / ".opencode" / "instructions.md")]
    plugin = REPO_ROOT / ".opencode" / "plugins" / "rosetta-experience.js"
    config["plugin"] = [plugin.resolve().as_uri()]
    for name in (
        "rosetta-agent",
        "rosetta-plan",
        "rosetta-verify",
        "rosetta-explain",
        "rosetta-divergence",
    ):
        profile = REPO_ROOT / ".opencode" / "agent" / f"{name}.md"
        prompt = profile.read_text(encoding="utf-8").split("---", 2)[-1].strip()
        config.setdefault("agent", {}).setdefault(name, {})["prompt"] = prompt
    config["command"] = _command_profiles()
    return config


def _merge_user_config(config: dict[str, Any], raw: str) -> dict[str, Any]:
    """Preserve user config while reserving Rosetta's named integration points."""
    supplied = json.loads(raw)
    if not isinstance(supplied, dict):
        raise ValueError("OPENCODE_CONFIG_CONTENT must contain a JSON object.")
    reserved = {key: config.get(key, {}) for key in ("mcp", "agent", "command")}
    config.update(
        {
            key: value
            for key, value in supplied.items()
            if key not in reserved
            and key not in {"instructions", "default_agent", "provider", "model", "plugin"}
        }
    )
    for key, rosetta_entries in reserved.items():
        user_entries = supplied.get(key, {})
        if user_entries is not None and not isinstance(user_entries, dict):
            raise ValueError(f"OPENCODE_CONFIG_CONTENT {key!r} must be an object.")
        config[key] = {**(user_entries or {}), **rosetta_entries}
    user_providers = supplied.get("provider", {})
    if user_providers is not None and not isinstance(user_providers, dict):
        raise ValueError("OPENCODE_CONFIG_CONTENT 'provider' must be an object.")
    rosetta_providers = config.get("provider", {})
    merged_providers = dict(user_providers or {})
    for provider_name, rosetta_provider in rosetta_providers.items():
        user_provider = merged_providers.get(provider_name, {})
        if not isinstance(user_provider, dict) or not isinstance(rosetta_provider, dict):
            merged_providers[provider_name] = rosetta_provider
            continue
        merged_provider = {**user_provider, **rosetta_provider}
        user_models = user_provider.get("models", {})
        rosetta_models = rosetta_provider.get("models", {})
        if isinstance(user_models, dict) and isinstance(rosetta_models, dict):
            merged_models = dict(user_models)
            for model_name, model_config in rosetta_models.items():
                prior = merged_models.get(model_name, {})
                merged_models[model_name] = (
                    {**prior, **model_config}
                    if isinstance(prior, dict) and isinstance(model_config, dict)
                    else model_config
                )
            merged_provider["models"] = merged_models
        merged_providers[provider_name] = merged_provider
    config["provider"] = merged_providers
    user_plugins = supplied.get("plugin", [])
    if not isinstance(user_plugins, list) or not all(isinstance(item, str) for item in user_plugins):
        raise ValueError("OPENCODE_CONFIG_CONTENT 'plugin' must be an array of strings.")
    config["plugin"] = list(dict.fromkeys([*user_plugins, *config.get("plugin", [])]))
    if isinstance(supplied.get("model"), str):
        config["model"] = supplied["model"]
    user_instructions = supplied.get("instructions", [])
    if not isinstance(user_instructions, list) or not all(
        isinstance(item, str) for item in user_instructions
    ):
        raise ValueError("OPENCODE_CONFIG_CONTENT 'instructions' must be an array of strings.")
    config["instructions"] = [*user_instructions, *config.get("instructions", [])]
    config["default_agent"] = "rosetta-agent"
    return config


def cmd_tui(args: argparse.Namespace) -> int:
    """Open the branded OpenCode TUI with every Rosetta workflow attached."""
    try:
        project = args.project.expanduser().resolve()
        if not project.is_dir():
            raise ValueError(f"Project directory does not exist: {project}")
        if args.timeout is not None and args.prompt is None:
            raise ValueError("--timeout requires --prompt; interactive sessions have no time limit.")
        env = os.environ.copy()
        env["PWD"] = str(project)
        env["ROSETTA_PROJECT_DIR"] = str(project)
        env["ROSETTA_HOME"] = str(REPO_ROOT)
        env["ROSETTA_PYTHON"] = sys.executable
        corpus = args.corpus.expanduser().resolve() if args.corpus else Path(
            env.get("ROSETTA_CORPUS_DIR", project)
        ).expanduser().resolve()
        if not corpus.is_dir():
            raise ValueError(f"Corpus directory does not exist: {corpus}")
        env["ROSETTA_CORPUS_DIR"] = str(corpus)

        config = _config()
        if env.get("OPENCODE_CONFIG_CONTENT"):
            config = _merge_user_config(config, env["OPENCODE_CONFIG_CONTENT"])
        mcp_env = config["mcp"]["rosetta"].setdefault("environment", {})
        mcp_env["ROSETTA_CORPUS_DIR"] = str(corpus)
        mcp_env["ROSETTA_PROJECT_DIR"] = str(project)
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(config)

        command = [_opencode()]
        if args.prompt is None:
            command.append(str(project))
        else:
            command.extend(["run", "--dir", str(project)])
        command.extend(["--agent", "rosetta-agent"])
        if args.model:
            from rosetta import models as model_registry

            command.extend(["--model", model_registry.resolve(args.model) or args.model])
        if args.prompt is not None:
            command.extend(["--", args.prompt])
        try:
            return subprocess.run(
                command,
                cwd=project,
                env=env,
                timeout=(args.timeout or 300.0) if args.prompt is not None else None,
            ).returncode
        except subprocess.TimeoutExpired:
            print("ERROR: OpenCode prompt exceeded its time limit.", file=sys.stderr)
            return 124
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


# --------------------------------------------------------------------------
# Presentation. Nothing here may raise: a crash while formatting a verdict
# would lose the verdict.
# --------------------------------------------------------------------------

def _color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM", "") in ("", "dumb"):
        return False
    return sys.stdout.isatty()


def _paint(text: str, code: str) -> str:
    return f"\x1b[{code}m{text}\x1b[0m" if _color() else text


def _yes(text: str) -> str:
    return f"{_paint('ok', '32')}    {text}"


def _no(text: str) -> str:
    return f"{_paint('--', '31')}    {text}"


def _meh(text: str) -> str:
    return f"{_paint('..', '33')}    {text}"


def _head(text: str) -> None:
    print()
    print(_paint(text, "1"))


def _next(*commands: str) -> None:
    """Every command ends here. Not knowing what to run next was the bug."""
    if not commands:
        return
    print()
    print(_paint("next", "1"), end="  ")
    print("\n      ".join(commands))


def _fail(message: str, *hints: str) -> int:
    print(f"{_paint('error', '31;1')}  {message}", file=sys.stderr)
    for h in hints:
        print(f"       {h}", file=sys.stderr)
    return 1


# --------------------------------------------------------------------------
# Facts about this checkout. Filesystem only -- fast, and safe to call before
# anything is installed.
# --------------------------------------------------------------------------

def _rel(path: Path) -> str:
    """Repo-relative when it can be, so a `next` line stays readable."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _taskset_path() -> Path:
    return REPO_ROOT / "data" / "tasks" / "eval_tasks.json"


def _count_tasks() -> int | None:
    p = _taskset_path()
    if not p.exists():
        return None
    try:
        return len(json.loads(p.read_text(encoding="utf-8")).get("tasks", []))
    except (OSError, json.JSONDecodeError):
        return None


def _split_summary() -> str | None:
    p = REPO_ROOT / "data" / "tasks" / "split.lock.json"
    if not p.exists():
        return None
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return f"{len(doc.get('train', []))} train / {len(doc.get('eval', []))} eval"


def _traces() -> list[Path]:
    d = REPO_ROOT / "results" / "bench"
    return sorted(d.glob("*.jsonl")) if d.is_dir() else []


def _published() -> dict[str, Any] | None:
    p = REPO_ROOT / "results" / "summary.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# --------------------------------------------------------------------------
# status / doctor
# --------------------------------------------------------------------------

def cmd_status(args: argparse.Namespace) -> int:
    """The map. Where you are, and the shortest way to the next thing."""
    from rosetta import models as model_registry

    if not args.plain:
        try:
            from rosetta.ui.banner import print_banner

            print_banner()
        except Exception:  # a banner must never block the tool
            pass

    _head("workflows")
    for name, blurb, example in WORKFLOWS:
        print(f"  {name:<8} {blurb}")
        print(f"           {_paint(example, '2')}")

    _head("this checkout")
    registered = model_registry.load()
    if registered:
        print(_yes(f"{len(registered)} model(s) registered: "
                   + ", ".join(m.name for m in registered)))
    else:
        print(_meh("no models registered — `rosetta model add NAME provider/id`"))

    split = _split_summary()
    print(_yes(f"split lock: {split}") if split
          else _no("no split lock — `rosetta bench split --write`"))

    n = _count_tasks()
    if n:
        print(_yes(f"eval task set: {n} tasks"))
    elif n == 0:
        print(_no("eval task set is empty — `rosetta bench build`"))
    else:
        print(_meh("no eval task set yet — `rosetta bench build`"))

    traces = _traces()
    print(_yes(f"{len(traces)} benchmark trace file(s) in results/bench")
          if traces else _meh("no benchmark runs yet — `rosetta bench run`"))

    pub = _published()
    if pub:
        print(_yes("results/summary.json published — the website reads this"))
    else:
        print(_meh("nothing published — `rosetta bench report` writes summary.json"))

    print(_yes("opencode on PATH") if shutil.which("opencode")
          else _no("opencode not on PATH — needed to drive any model"))

    _next("rosetta               # open the primary TUI in this project",
          "rosetta gui           # optional browser view",
          "rosetta doctor        # check the verifier can actually run",
          "rosetta demo          # the side-by-side, offline, 60 seconds")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """Can this machine do the work. Slow checks live here, not in status."""
    ok = True
    _head("python")
    print(_yes(f"python {sys.version.split()[0]}") if sys.version_info >= (3, 11)
          else _no(f"python {sys.version.split()[0]} — 3.11+ required"))
    ok = ok and sys.version_info >= (3, 11)

    _head("verifier")
    try:
        from rosetta.demo.verify import verifier_status

        usable, reason = verifier_status()
        print(_yes(reason) if usable else _no(reason))
        ok = ok and usable
    except Exception as exc:  # import-time failure is itself the finding
        print(_no(f"could not query the verifier: {exc}"))
        ok = False

    _head("model access")
    try:
        from rosetta.demo.agents import opencode_available

        usable, reason = opencode_available()
        print(_yes(reason) if usable else _no(reason))
    except Exception as exc:
        print(_no(f"could not query opencode: {exc}"))

    _head("data")
    split = _split_summary()
    print(_yes(f"split lock: {split}") if split else _no("split lock missing"))
    n = _count_tasks()
    print(_yes(f"eval task set: {n} tasks") if n else _meh("no eval task set"))

    if ok:
        _next("rosetta edit ROUTINE --request '...'   # the real workflow",
              "rosetta bench run --model NAME        # the measurement")
    else:
        _next("scripts/bootstrap.sh                  # pull the container, find the regions",
              "rosetta demo                          # works offline regardless")
    return 0 if ok else 1


# --------------------------------------------------------------------------
# Workflow 1 — edit
# --------------------------------------------------------------------------

def cmd_edit(args: argparse.Namespace) -> int:
    """Propose, verify, repair — until the verifier says equivalent.

    The loop itself lives in :mod:`rosetta.workflow` so the GUI cannot drift
    away from it. This function is presentation and nothing else.
    """
    from rosetta import models as model_registry
    from rosetta.workflow import WorkflowError, edit

    routine = args.routine.upper()
    model = model_registry.resolve(args.model)
    print(f"routine   {routine}")
    print(f"request   {args.request}")
    print(f"model     {model or 'opencode default'}")
    print(f"attempts  up to {args.attempts}, {args.timeout:.0f}s each")

    accepted: dict[str, Any] | None = None
    rejected = 0
    try:
        for ev in edit(routine, args.request, model=model, attempts=args.attempts,
                       timeout_s=args.timeout, explicit_cases=args.cases):
            if ev.kind == "source":
                routine = ev.data["routine"]
                print(f"source    {ev.data['lines']} lines ({ev.data['origin']})")
            elif ev.kind == "inputs":
                print(f"inputs    {ev.data['origin']}")
            elif ev.kind == "attempt":
                _head(f"attempt {ev.data['n']}")
                print(ev.data["diff"] or "(no textual change)")
            elif ev.kind == "verdict":
                print()
                print(ev.data["verdict"])
                if not ev.data["equivalent"]:
                    rejected += 1
                    for d in ev.data.get("divergences", [])[:5]:
                        print(f"  [case {d['case_index']}] {d['headline']}")
                        for line in d.get("details", [])[:3]:
                            print(f"      {line}")
            elif ev.kind == "accepted":
                accepted = ev.data
            elif ev.kind == "exhausted":
                print(_meh(ev.data["reason"]))
    except WorkflowError as exc:
        hints = _hints_for(exc)
        if exc.data.get("did_you_mean"):
            hints.insert(0, "did you mean: " + ", ".join(exc.data["did_you_mean"]))
        return _fail(str(exc), *hints)

    _head("result")
    if accepted is None:
        print(_no(f"no verified candidate after {rejected} rejected attempt(s)"))
        print("     Nothing was written. The routine on disk is untouched.")
        _next(f"rosetta edit {routine} --request '...' --attempts {args.attempts + 2}",
              f"rosetta verify {routine} -c yourfile.m   # if you want to edit by hand")
        return 1

    out_path = (Path(args.out) if args.out
                else REPO_ROOT / "results" / f"{routine}.verified.m")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(accepted["candidate_src"], encoding="utf-8")
    print(_yes(f"verified candidate written to {_rel(out_path)}"))
    print(f"     accepted on attempt {accepted['n']} of up to {args.attempts}")
    print("     The routine on disk is untouched: applying it is your call.")
    _next(f"cp {_rel(out_path)} data/routines/{routine}.m   # apply it",
          f"rosetta verify {routine} -c {_rel(out_path)}   # re-check independently")
    return 0


def _hints_for(exc: Any) -> list[str]:
    """Turn a workflow error's payload into the one line that unblocks the user."""
    hints: list[str] = []
    data = getattr(exc, "data", {}) or {}
    if data.get("available_suites"):
        hints.append("routines with a stored input suite: "
                     + ", ".join(data["available_suites"][:10]))
        hints.append("a routine with no suite and no benchmark task cannot be "
                     "verified — there is nothing to run it on")
    if data.get("backend"):
        hints.append("rosetta doctor    # the verifier backend is not reachable")
    if data.get("timeout_s"):
        hints.append(f"the budget was {data['timeout_s']:.0f}s per attempt; a "
                     "tools-on agent calls the verifier itself")
        hints.append("rosetta edit ... --timeout 3600    # give it longer")
    return hints


# --------------------------------------------------------------------------
# Workflow 5 — verify
# --------------------------------------------------------------------------

def cmd_verify(args: argparse.Namespace) -> int:
    """Verify a candidate you already wrote. No model involved."""
    from rosetta.workflow import WorkflowError, cases_for, verify

    routine = args.routine.upper()
    cand_path = Path(args.candidate)
    if not cand_path.exists():
        return _fail(f"{cand_path} not found",
                     "--candidate takes the COMPLETE replacement source, not a patch")
    baseline_src = (
        Path(args.baseline).read_text(encoding="utf-8", errors="replace")
        if args.baseline else None
    )

    from rosetta.tools.tools import ToolRegistry

    reg = ToolRegistry()
    try:
        cases, case_origin = cases_for(reg, routine, args.cases)
    except WorkflowError as exc:
        return _fail(str(exc))
    if not args.json:
        print(f"inputs    {case_origin}")

    try:
        out = verify(
            routine,
            cand_path.read_text(encoding="utf-8", errors="replace"),
            baseline_src=baseline_src,
            cases=cases,
            registry=reg,
        )
    except WorkflowError as exc:
        if args.json:
            print(json.dumps({"error": str(exc), **exc.data}, indent=2))
            return 1
        return _fail(str(exc), *_hints_for(exc))

    if args.json:
        print(json.dumps(out, indent=2))
        return 0 if out["equivalent"] else 1

    print(out["verdict"])
    print(f"\n{out['n_cases']} case(s), {out['n_diverged']} diverged, "
          f"{out['n_void']} void")
    divs = out.get("divergences", [])
    # The counts above are the full picture; this list is capped so a wall of
    # repeats cannot bury the first, most actionable one. `--json` is uncapped.
    for d in divs[: args.show]:
        print(f"\n  [case {d['case_index']}] {d['headline']}")
        for line in d.get("details", []):
            print(f"      {line}")
    if len(divs) > args.show:
        print(f"\n  ... and {len(divs) - args.show} more "
              f"(--show {len(divs)}, or --json for all)")
    if out["equivalent"]:
        _next(f"cp {_rel(cand_path)} data/routines/{routine}.m   # apply it")
        return 0
    _next("fix the divergence above, then re-run this command",
          f"rosetta edit {routine} --request '...'   # let a model repair it")
    return 1


# --------------------------------------------------------------------------
# Workflow 2 — bring your own model
# --------------------------------------------------------------------------

def cmd_model(args: argparse.Namespace) -> int:
    from rosetta import models as model_registry

    if args.model_cmd == "list":
        entries = model_registry.load()
        if not entries:
            print("No models registered.")
            print("\nRosetta does not host models. Register the id OpenCode uses:")
            print("  rosetta model add opus     anthropic/claude-opus-5")
            print("  rosetta model add local    ollama/qwen2.5-coder:32b")
            _next("opencode models              # what your install can reach")
            return 0
        width = max(len(m.name) for m in entries)
        for m in entries:
            print(f"  {m.name:<{width}}  {m.model}"
                  + (f"   {_paint(m.notes, '2')}" if m.notes else ""))
        _next("rosetta model test NAME        # one live call, no benchmark",
              "rosetta bench run --model NAME")
        return 0

    if args.model_cmd == "add":
        try:
            m = model_registry.add(args.name, args.id, args.notes or "",
                                   replace=args.replace)
        except model_registry.RegistryError as exc:
            return _fail(str(exc))
        print(_yes(f"{m.name} -> {m.model}"))
        _next(f"rosetta model test {m.name}",
              f"rosetta bench run --model {m.name}")
        return 0

    if args.model_cmd == "remove":
        try:
            m = model_registry.remove(args.name)
        except model_registry.RegistryError as exc:
            return _fail(str(exc))
        print(_yes(f"removed {m.name} ({m.model})"))
        return 0

    if args.model_cmd == "test":
        from rosetta.demo.agents import AgentUnavailable, OpenCodeAgent

        model = model_registry.resolve(args.name)
        print(f"asking {model or 'the opencode default'} for one MUMPS rewrite...")
        agent = OpenCodeAgent(model=model, tools_on=False, cwd=str(REPO_ROOT),
                              max_attempts=1, timeout_s=args.timeout)
        task = _EditTask("XLFSTR", "Return the string unchanged. Change nothing else.")
        try:
            attempt = agent.propose(task, "XLFSTR ;\n QUIT\n", [])
        except AgentUnavailable as exc:
            return _fail(f"{model or 'default'} is not usable: {exc}",
                         "opencode auth login    # if this is a credentials problem")
        if attempt is None:
            return _fail(f"{model or 'default'} returned nothing")
        print(_yes(f"{model or 'default'} answered with a parseable candidate "
                   f"({len(attempt.candidate_src.splitlines())} lines)"))
        print("     This proves reachability and output format — not competence.")
        _next(f"rosetta bench run --model {args.name}   # competence is measured, not tested")
        return 0

    return _fail(f"unknown model subcommand {args.model_cmd!r}")


# --------------------------------------------------------------------------
# Workflows 3 and 4 — delegation to the modules that already do the work
# --------------------------------------------------------------------------

def _delegate(module: str, argv: Sequence[str], after: Sequence[str] = ()) -> int:
    """Call another module's ``main``. Its flags stay authoritative."""
    import importlib

    try:
        mod = importlib.import_module(module)
    except Exception as exc:
        return _fail(f"could not load {module}: {exc}")
    main: Callable[[Sequence[str]], int] | None = getattr(mod, "main", None)
    if main is None:
        return _fail(f"{module} has no main()")
    rc = main(list(argv))
    if rc == 0:
        _next(*after)
    return rc


def cmd_bench(args: argparse.Namespace) -> int:
    extra = list(getattr(args, "extra", None) or [])
    if args.bench_cmd == "split":
        return _delegate("rosetta.bench.split", extra, ["rosetta bench build"])
    if args.bench_cmd == "build":
        return _delegate("rosetta.bench.build", extra,
                         ["rosetta bench run --model NAME"])
    if args.bench_cmd == "run":
        from rosetta import models as model_registry

        if args.model:
            extra = ["--model", model_registry.resolve(args.model) or "", *extra]
        return _delegate("rosetta.bench.run", extra, ["rosetta bench report"])
    if args.bench_cmd == "report":
        return _delegate("rosetta.bench.report", extra,
                         ["npm run dev    # the site reads results/summary.json"])
    if args.bench_cmd == "status":
        try:
            import rosetta.bench.progress  # noqa: F401
        except ModuleNotFoundError:
            n, traces = _count_tasks(), _traces()
            done = sum(sum(1 for _ in p.open(encoding="utf-8")) for p in traces)
            print(f"{len(traces)} trace file(s), {done} trace record(s)")
            print(f"task set: {n if n is not None else 'not built'}")
            _next("rosetta bench report")
            return 0
        return _delegate("rosetta.bench.progress", extra, ["rosetta bench report"])
    return _fail(f"unknown bench subcommand {args.bench_cmd!r}")


def cmd_train(args: argparse.Namespace) -> int:
    extra = list(getattr(args, "extra", None) or [])
    if args.train_cmd == "labels":
        return _delegate("rosetta.train.labels", extra, ["rosetta train sft"])
    if args.train_cmd == "sft":
        return _delegate("rosetta.train.sft", extra, ["rosetta train submit --help"])
    if args.train_cmd == "grader":
        return _delegate("rosetta.train.grader", extra)
    if args.train_cmd == "submit":
        # Deliberately not implemented. Submitting a fine-tune means a provider
        # account, a spend decision and data leaving the machine -- none of which
        # a Rosetta subcommand should make on someone's behalf, least of all in
        # the air-gapped deployment this project targets.
        jsonl = REPO_ROOT / "data" / "train" / "sft.jsonl"
        print("Rosetta builds the training set. It does not submit it for you.")
        print("\nData leaving this machine is your decision, not a subcommand's —")
        print("and in the deployment this project targets, it cannot leave at all.")
        print(f"\ntraining set   {jsonl}"
              f"  {'(built)' if jsonl.exists() else '(not built yet)'}")
        print("\nSubmit it with your provider's own tooling, for example:")
        print("  openai api fine_tuning.jobs.create -t data/train/sft.jsonl -m MODEL")
        print("\nThen register what comes back and measure it:")
        print("  rosetta model add tuned provider/ft:your-model-id")
        print("  rosetta bench run --model tuned")
        _next("rosetta train grader     # the reward function, for an RFT run")
        return 0
    return _fail(f"unknown train subcommand {args.train_cmd!r}")


def cmd_demo(args: argparse.Namespace) -> int:
    return _delegate("rosetta.demo.sidebyside", list(args.extra),
                     ["rosetta edit ROUTINE --request '...'   # the same loop, live"])


def cmd_mcp(args: argparse.Namespace) -> int:
    if args.mcp_cmd == "serve":
        return _delegate("rosetta.tools.server", list(args.extra))
    if args.mcp_cmd == "list":
        from rosetta.tools.tools import ToolRegistry

        for tool in ToolRegistry().list():
            flag = " (needs the container)" if tool.requires_execution else ""
            print(f"  {tool.name:<22} {tool.title}{flag}")
        print("\nThese are what an editor or agent host calls. Wire them with:")
        print('  "rosetta": {"type": "local", "command": ["python3", "-m", "rosetta.tools"]}')
        _next("rosetta mcp serve      # run it in the foreground to watch traffic")
        return 0
    return _fail(f"unknown mcp subcommand {args.mcp_cmd!r}")


def cmd_gui(args: argparse.Namespace) -> int:
    """The browser surface. Same workflows, same verdicts, loopback only."""
    from rosetta.gui.server import serve

    return serve(args.host, args.port, open_browser=not args.no_open)


def cmd_selftest(args: argparse.Namespace) -> int:
    return _delegate("rosetta.core.selftest", list(args.extra),
                     ["rosetta demo", "rosetta edit ROUTINE --request '...'"])


# --------------------------------------------------------------------------
# Compatibility surfaces used by the TUI command palette
# --------------------------------------------------------------------------

def cmd_models(args: argparse.Namespace) -> int:
    """Expose OpenCode's model catalogue without hiding its exit status."""
    try:
        command = [_opencode(), "models"]
        if args.provider:
            command.append(args.provider)
        return subprocess.run(command).returncode
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


def cmd_report(args: argparse.Namespace) -> int:
    """Short spelling retained for the TUI's ``/report`` command."""
    return _delegate("rosetta.bench.report", list(args.extra))


def cmd_eval(args: argparse.Namespace) -> int:
    """Compare two complete sources against explicitly supplied cases."""
    from rosetta.core import ExecSpec, shutdown, verify_equivalence
    from rosetta.tools.cases import spec_from_dict
    from rosetta.tools.sources import normalise_name

    try:
        for source in (args.baseline, args.candidate, args.cases):
            if not source.is_file():
                raise ValueError(f"Input file does not exist: {source}")
        if args.out:
            for source in (args.baseline, args.candidate, args.cases):
                if args.out.resolve() == source.resolve() or (
                    args.out.exists() and args.out.samefile(source)
                ):
                    raise ValueError(
                        "--out must not overwrite baseline, candidate, or cases "
                        "(including file aliases)."
                    )

        document = json.loads(args.cases.read_text(encoding="utf-8"))
        cases = document.get("cases") if isinstance(document, dict) else document
        if not isinstance(cases, list) or not cases:
            raise ValueError(
                "Cases must be a nonempty JSON array (or an object with a cases array)."
            )
        specs = []
        allowed = {field.name for field in fields(ExecSpec)}
        for index, case in enumerate(cases):
            if not isinstance(case, dict) or set(case) - allowed:
                raise ValueError(
                    f"Case {index + 1} must be an object containing only ExecSpec fields."
                )
            if not isinstance(case.get("routine"), str):
                raise ValueError(
                    f"Case {index + 1} must name its routine as a string."
                )
            normalise_name(case["routine"])
            timeout = case.get("timeout_s", 10.0)
            if (
                isinstance(timeout, bool)
                or not isinstance(timeout, (int, float))
                or not (0 < timeout <= sys.float_info.max)
            ):
                raise ValueError(
                    f"Case {index + 1} timeout_s must be a finite positive number."
                )
            specs.append(spec_from_dict(case))

        stem = args.baseline.stem
        inferred = "%" + stem[1:] if stem.startswith("_") else stem
        routine = normalise_name(args.routine or inferred)
        if any(spec.routine != routine for spec in specs):
            raise ValueError("Every case must name the evaluated routine.")

        report = verify_equivalence(
            routine,
            args.baseline.read_text(encoding="utf-8"),
            args.candidate.read_text(encoding="utf-8"),
            specs,
        )
        rendered = json.dumps(asdict(report), indent=2)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
        return (
            0
            if report.equivalent
            and report.n_void == 0
            and report.n_cases == len(specs)
            else 1
        )
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    finally:
        shutdown()


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------

def _remainder(p: argparse.ArgumentParser, label: str) -> None:
    p.add_argument("extra", nargs=argparse.REMAINDER,
                   help=f"passed straight through to {label}; try `-- --help`")


def _positive_seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a finite positive number") from exc
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return seconds


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="rosetta",
        description="Verified modification of code nobody can read.",
        epilog="Run `rosetta` with no arguments to open the TUI in this project.",
    )
    ap.add_argument("--plain", action="store_true", help="no banner")
    # A download channel with no way to report its own version is unfixable in the
    # field: a bug report has to be able to say which build it came from.
    ap.add_argument("--version", action="version", version=f"rosetta {__version__}")
    sub = ap.add_subparsers(dest="cmd")

    # `--plain` is global, but `rosetta status --plain` is what people type.
    # SUPPRESS keeps the subparser copy from clobbering a global one with its
    # own default.
    for name, help_text in (
        ("status", "what is wired, and what to run next"),
        ("doctor", "can this machine run the verifier"),
    ):
        q = sub.add_parser(name, help=help_text)
        q.add_argument("--plain", action="store_true", default=argparse.SUPPRESS,
                       help="no banner")

    for name, help_text in (
        ("tui", "open the Rosetta TUI in a project"),
        ("code", "alias for tui"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("project", nargs="?", type=Path, default=Path.cwd())
        p.add_argument(
            "--model",
            default=os.environ.get("ROSETTA_MODEL"),
            help="registered name or provider/model",
        )
        p.add_argument("--corpus", type=Path, help="MUMPS routine corpus (default: project)")
        p.add_argument("--prompt", help="run one prompt instead of opening interactively")
        p.add_argument(
            "--timeout",
            type=_positive_seconds,
            help="prompt time limit in seconds (default 300; requires --prompt)",
        )

    p = sub.add_parser("edit", help="change a routine with the verifier in the loop")
    p.add_argument("routine")
    p.add_argument("--request", "-m", required=True, help="the change, in English")
    p.add_argument("--model", help="a registered name or a provider/model id")
    p.add_argument("--attempts", type=int, default=3)
    p.add_argument("--timeout", type=float, default=1800.0, metavar="SECONDS",
                   help="per-attempt budget for the model (default 1800; a "
                        "tools-on agent calls the verifier itself and is slow)")
    p.add_argument("--cases", help="JSON list of ExecSpecs (default: stored suite, "
                                   "then the built task set)")
    p.add_argument("--out", help="where to write a verified candidate")

    p = sub.add_parser("verify", help="check a change you already made")
    p.add_argument("routine")
    p.add_argument("--candidate", "-c", required=True,
                   help="file holding the COMPLETE replacement source")
    p.add_argument("--baseline", "-b", help="original source (default: on disk)")
    p.add_argument("--cases", help="JSON list of ExecSpecs (default: stored suite, "
                                   "then the built task set)")
    p.add_argument("--show", type=int, default=6, metavar="N",
                   help="how many divergences to print (default 6)")
    p.add_argument("--json", action="store_true", help="machine-readable report")

    p = sub.add_parser("model", help="bring your own model")
    msub = p.add_subparsers(dest="model_cmd", required=True)
    msub.add_parser("list", help="registered models")
    a = msub.add_parser("add", help="register a model id under a name you choose")
    a.add_argument("name")
    a.add_argument("id", help="provider/model, e.g. ollama/qwen2.5-coder:32b")
    a.add_argument("--notes", default="")
    a.add_argument("--replace", action="store_true")
    r = msub.add_parser("remove", help="unregister")
    r.add_argument("name")
    t = msub.add_parser("test", help="one live call: is it reachable and parseable")
    t.add_argument("name")
    t.add_argument("--timeout", type=float, default=300.0)

    p = sub.add_parser("bench", help="measure a model on the held-out eval set")
    bsub = p.add_subparsers(dest="bench_cmd", required=True)
    for name, label in (
        ("split", "rosetta.bench.split"),
        ("build", "rosetta.bench.build"),
        ("status", "the progress view"),
        ("report", "rosetta.bench.report"),
    ):
        _remainder(bsub.add_parser(name, help=label), label)
    b = bsub.add_parser("run", help="run a model over every eval task, both conditions")
    b.add_argument("--model", help="a registered name or a provider/model id")
    _remainder(b, "rosetta.bench.run")

    p = sub.add_parser("train", help="turn verified work into training data")
    tsub = p.add_subparsers(dest="train_cmd", required=True)
    for name, label in (
        ("labels", "rosetta.train.labels"),
        ("sft", "rosetta.train.sft"),
        ("grader", "rosetta.train.grader"),
    ):
        _remainder(tsub.add_parser(name, help=label), label)
    tsub.add_parser("submit", help="how to submit the fine-tune, and why we do not")

    p = sub.add_parser("demo", help="the side-by-side, offline")
    _remainder(p, "rosetta.demo.sidebyside")

    p = sub.add_parser("mcp", help="the tool server an editor or agent host calls")
    xsub = p.add_subparsers(dest="mcp_cmd", required=True)
    xsub.add_parser("list", help="the tools and what they need")
    s = xsub.add_parser("serve", help="run the server on stdio")
    _remainder(s, "rosetta.tools.server")

    p = sub.add_parser("gui", help="the same five workflows in a browser")
    p.add_argument("--port", type=int, default=7391)
    p.add_argument("--host", default="127.0.0.1", help="loopback addresses only")
    p.add_argument("--no-open", action="store_true", help="do not open a browser")

    p = sub.add_parser("selftest", help="prove the verifier works on this machine")
    _remainder(p, "rosetta.core.selftest")

    p = sub.add_parser("models", help="list models available through OpenCode")
    p.add_argument("provider", nargs="?")

    p = sub.add_parser("eval", help="compare baseline and candidate source with a case suite")
    p.add_argument("--baseline", type=Path, required=True)
    p.add_argument("--candidate", type=Path, required=True)
    p.add_argument("--cases", type=Path, required=True)
    p.add_argument("--routine")
    p.add_argument("--out", type=Path)

    p = sub.add_parser("report", help="alias for bench report")
    _remainder(p, "rosetta.bench.report")

    return ap


_HANDLERS: dict[str, Callable[[argparse.Namespace], int]] = {
    "tui": cmd_tui,
    "code": cmd_tui,
    "status": cmd_status,
    "doctor": cmd_doctor,
    "edit": cmd_edit,
    "verify": cmd_verify,
    "model": cmd_model,
    "bench": cmd_bench,
    "train": cmd_train,
    "demo": cmd_demo,
    "gui": cmd_gui,
    "mcp": cmd_mcp,
    "selftest": cmd_selftest,
    "models": cmd_models,
    "eval": cmd_eval,
    "report": cmd_report,
}


def main(argv: Sequence[str] | None = None) -> int:
    raw = list(argv if argv is not None else sys.argv[1:])
    if not raw:
        raw = ["tui"]
    elif raw[0] not in _HANDLERS and raw[0] not in {"-h", "--help", "--plain", "--version"}:
        # A path (or TUI option) is the common case, so `rosetta ../project`
        # is shorthand for `rosetta tui ../project`.
        raw = ["tui", *raw]
    ap = build_parser()
    args = ap.parse_args(raw)
    handler = _HANDLERS.get(args.cmd or "status")
    if handler is None:  # unreachable while _HANDLERS covers every subparser
        ap.print_help()
        return 2
    # `argparse.REMAINDER` keeps a leading `--`; drop it so `-- --help` works.
    extra = getattr(args, "extra", None)
    if extra and extra[0] == "--":
        args.extra = extra[1:]
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
