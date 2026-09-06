"""``python -m rosetta.core.selftest`` -- stream A's definition of done.

Snapshot capability, a real routine, a real mutation, a divergence that names
the specific thing that moved, and a clean environment afterwards. Nothing is
mocked: every step runs against YottaDB in the verification container.

    python -m rosetta.core.selftest             # fast path, target < 5s
    python -m rosetta.core.selftest --snapshot  # also do a real .dat round trip
    python -m rosetta.core.selftest -v          # show the runtime log

On the snapshot step: ``snapshot()`` copies a 3.4GB region and costs ~20s, so
it cannot run inside a 5s budget. The fast path proves the regions are
discovered and the snapshot directory is writable; ``--snapshot`` does the full
copy-and-restore and reports the measured cost. TP rollback -- not the .dat
copy -- is the per-case isolation mechanism the contract specifies.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .config import CoreConfig
from .interface import ExecSpec, VerifyReport
from .runtime import Runtime

ROUTINE_DIR = Path(__file__).resolve().parents[2] / "data" / "routines"

# --- case 1: the cleanest routine in the corpus. GSA UEI checksum: zero
# globals, zero calls, pure modular arithmetic. A CMP_FLIP here moves stdout.
UEI_MUTATION = ('I $E(PRCSTR)="0" Q 0', 'I $E(PRCSTR)\'="0" Q 0')

UEI_CASES = [
    ExecSpec(routine="PRCHUEI", entry="$$VALIDUEI", args=["ZQGGH7C1MJM3"]),
    ExecSpec(routine="PRCHUEI", entry="$$VALIDUEI", args=["CJ7NM3RCJ1S8"]),
    ExecSpec(routine="PRCHUEI", entry="$$VALIDUEI", args=["ZQGGH7C1MJM4"]),
    ExecSpec(routine="PRCHUEI", entry="$$VALIDUEI", args=["0BCDEFGHJKL1"]),
    ExecSpec(routine="PRCHUEI", entry="$$VALIDUEI", args=[""]),
]

# --- case 2: a real routine that writes a real production global. A CMP_FLIP
# on the guard makes it write where the baseline returned early, so the
# divergence must name ^PXRMINDX(...) specifically.
PXVSC_MUTATION = ('I VISIT="" Q', 'I VISIT\'="" Q')

_VISIT = "9999901"
_PX_CASES = [
    ExecSpec(
        routine="PXVSC",
        entry="SVSC",
        locals_in={
            "X(1)": "10D", "X(2)": "Z00.00", "X(3)": "777", "X(4)": _VISIT,
            "DA": "4242",
        },
        args=[".X", "4242"],
        globals_in={f'^AUPNVSIT({_VISIT},0)': "3250101^^^^^^^"},
    ),
    ExecSpec(
        routine="PXVSC",
        entry="SVSC",
        locals_in={
            "X(1)": "ICD", "X(2)": "E11.9", "X(3)": "778", "X(4)": _VISIT,
            "X(5)": "3240202", "DA": "4343",
        },
        args=[".X", "4343"],
        globals_in={f'^AUPNVSIT({_VISIT},0)': "3250101^^^^^^^"},
    ),
]


@dataclass
class Step:
    name: str
    ok: bool
    detail: str
    seconds: float


class SelfTestFailure(AssertionError):
    pass


def _load(name: str) -> str:
    path = ROUTINE_DIR / f"{name}.m"
    if not path.is_file():
        raise SelfTestFailure(f"missing corpus routine {path}")
    return path.read_text(encoding="utf-8", errors="replace")


def _mutate(source: str, before: str, after: str, label: str, occurrence: int = 1) -> str:
    """Replace the nth command-position occurrence of `before`."""
    idx = -1
    for _ in range(occurrence):
        idx = source.find(before, idx + 1)
        if idx < 0:
            raise SelfTestFailure(
                f"{label}: mutation anchor #{occurrence} not found: {before!r}"
            )
    return source[:idx] + after + source[idx + len(before):]


def run(full_snapshot: bool = False, config: CoreConfig | None = None) -> list[Step]:
    steps: list[Step] = []

    def step(name: str):
        started = time.monotonic()

        def done(ok: bool, detail: str) -> None:
            steps.append(Step(name, ok, detail, time.monotonic() - started))
            if not ok:
                raise SelfTestFailure(f"{name}: {detail}")

        return done

    overall = time.monotonic()
    rt = Runtime(config)

    done = step("worker starts")
    rt.worker.ensure_started()
    done(rt.ping(), f"container={rt.config.container}")

    done = step("regions discovered")
    regions = rt.regions()
    done(bool(regions), ", ".join(f"{r}={p}" for r, p in regions))

    if full_snapshot:
        done = step("snapshot")
        snap = rt.snapshot()
        done(True, snap)
        done = step("restore")
        rt.restore(snap)
        done(rt.ping(), snap)
    else:
        done = step("snapshot path available")
        rt._sh(f"test -d {rt.config.snapshot_dir} && test -w {rt.config.snapshot_dir}")
        done(True, f"{rt.config.snapshot_dir} writable (full copy skipped; use --snapshot)")

    done = step("clean_state asserts $TLEVEL==0")
    with rt.clean_state():
        pass
    done(True, "entered and left with no open transaction")

    # ---- output divergence on a pure corpus routine
    baseline = _load("PRCHUEI")
    done = step("baseline PRCHUEI is self-consistent")
    report = rt.verify_equivalence("PRCHUEI", baseline, baseline, UEI_CASES)
    done(report.equivalent and report.n_void == 0,
         f"{report.n_cases} cases, {report.n_diverged} diverged, {report.n_void} void")

    done = step("CMP_FLIP on PRCHUEI is detected")
    mutant = _mutate(baseline, *UEI_MUTATION, "PRCHUEI")
    report = rt.verify_equivalence("PRCHUEI", baseline, mutant, UEI_CASES)
    named = [d for d in report.divergences if d.kind == "output" and d.ref == "stdout"]
    done(
        (not report.equivalent) and bool(named) and report.n_void == 0,
        f"{report.n_diverged}/{report.n_cases} cases diverged; "
        + "; ".join(f"case {d.case_index} stdout {d.expected!r}->{d.actual!r}"
                    for d in named[:3]),
    )

    # ---- global divergence on a real routine that writes a production global
    baseline = _load("PXVSC")
    done = step("CMP_FLIP on PXVSC names the global that moved")
    # occurrence 2: the guard inside SVSC. KVSC has an identical line.
    mutant = _mutate(baseline, *PXVSC_MUTATION, "PXVSC", occurrence=2)
    report = rt.verify_equivalence("PXVSC", baseline, mutant, _PX_CASES)
    globals_moved = [d for d in report.divergences if d.kind == "global"]
    done(
        (not report.equivalent) and bool(globals_moved) and report.n_void == 0,
        f"{report.n_diverged}/{report.n_cases} cases diverged; "
        + "; ".join(f"{d.ref} {d.expected!r}->{d.actual!r}" for d in globals_moved[:3]),
    )

    # ---- nothing leaked into the live database
    done = step("environment restored clean")
    residue = rt._sh(
        "source /home/vehu/etc/env && $gtm_dist/mumps -run %XCMD "
        "'W $D(^PXRMINDX(9000010.71,\"IP\",\"10D\")),\"/\","
        "$D(^AUPNVSIT(" + _VISIT + ")),\"/\",$D(^ROSLOG),\"/\",$TLEVEL'"
    ).strip()
    done(residue == "0/0/0/0", f"$D probes = {residue} (want 0/0/0/0)")

    rt.close()
    steps.append(Step("TOTAL", True, "", time.monotonic() - overall))
    return steps


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m rosetta.core.selftest")
    ap.add_argument("--snapshot", action="store_true",
                    help="also do a real .dat snapshot/restore round trip (~40s)")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--budget", type=float, default=None,
                    help="optionally fail if runtime exceeds this many seconds")
    args = ap.parse_args(argv)
    if args.budget is not None and args.budget <= 0:
        ap.error("--budget must be positive")

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    budget = args.budget
    try:
        steps = run(full_snapshot=args.snapshot)
    except SelfTestFailure as exc:
        print(f"\nFAIL  {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 -- fail loudly, with the reason
        print(f"\nERROR {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    total = steps[-1].seconds
    for s in steps[:-1]:
        print(f"  ok   {s.seconds:6.2f}s  {s.name}\n              {s.detail}")
    print(f"\n  TOTAL {total:6.2f}s")
    if budget is not None and total > budget:
        print(f"\nFAIL  over the {budget:g}s budget", file=sys.stderr)
        return 1
    print("  PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
