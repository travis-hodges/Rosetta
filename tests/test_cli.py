"""The front door is the first thing anyone touches, so it is held to two
standards: it works with nothing installed, and it never invents a capability.

Every test here runs offline. No container, no network, no credentials, and no
writes outside a temporary directory -- which is exactly the situation a new
user is in when they type `rosetta` for the first time.
"""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rosetta import models  # noqa: E402
from rosetta.cli import WORKFLOWS, build_parser, main  # noqa: E402
from rosetta.workflow import WorkflowError  # noqa: E402


def run(*argv: str) -> tuple[int, str]:
    """Invoke the CLI, capturing both streams. Returns ``(exit_code, output)``."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = main(list(argv))
    return rc, out.getvalue() + err.getvalue()


class StatusTests(unittest.TestCase):
    def test_bare_invocation_names_every_workflow(self) -> None:
        rc, text = run("--plain")
        self.assertEqual(rc, 0)
        for name, _blurb, _example in WORKFLOWS:
            self.assertIn(name, text)

    def test_status_always_says_what_to_run_next(self) -> None:
        _rc, text = run("status", "--plain")
        self.assertIn("next", text)

    def test_plain_after_the_subcommand_is_accepted(self) -> None:
        # `rosetta status --plain` is what people type; a global-only flag
        # would reject it.
        self.assertEqual(run("status", "--plain")[0], 0)

    def test_status_needs_no_container_or_network(self) -> None:
        """Guards the import discipline the module docstring promises.

        Anything that needs a container, a network or credentials is imported
        inside the handler that needs it. This has to run in a fresh process:
        within one interpreter an earlier test that touched the tool registry
        would have already pulled `rosetta.core` in, and the check would pass
        for the wrong reason.
        """
        probe = (
            "import sys; from rosetta.cli import main; "
            "rc = main(['--plain']); "
            "leaked = [m for m in ('rosetta.core.runtime', 'rosetta.demo.agents') "
            "if m in sys.modules]; "
            "print('RC', rc); print('LEAKED', ','.join(leaked))"
        )
        proc = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=str(Path(__file__).resolve().parents[1]),
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertIn("RC 0", proc.stdout, proc.stderr)
        self.assertIn("LEAKED \n", proc.stdout + "\n",
                      "status imported a module that needs a container")


class ParserTests(unittest.TestCase):
    def test_every_subcommand_has_a_handler(self) -> None:
        from rosetta.cli import _HANDLERS

        ap = build_parser()
        actions = [a for a in ap._actions if getattr(a, "choices", None)
                   and hasattr(a, "dest") and a.dest == "cmd"]
        self.assertTrue(actions, "no subparsers found")
        for name in actions[0].choices:
            self.assertIn(name, _HANDLERS, f"{name} has no handler")

    def test_help_mentions_the_product_workflows(self) -> None:
        text = build_parser().format_help()
        for name, _blurb, _example in WORKFLOWS:
            self.assertIn(name, text)

    def test_browser_gui_is_not_a_product_surface(self) -> None:
        ap = build_parser()
        actions = [a for a in ap._actions if getattr(a, "choices", None)
                   and getattr(a, "dest", None) == "cmd"]
        self.assertNotIn("gui", actions[0].choices)
        self.assertFalse((Path(__file__).resolve().parents[1] / "rosetta" / "gui").exists())


class ModelRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "models.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_missing_file_is_an_empty_registry(self) -> None:
        self.assertEqual(models.load(self.path), [])

    def test_add_then_load_round_trips(self) -> None:
        models.add("local", "ollama/qwen2.5-coder:32b", path=self.path)
        entries = models.load(self.path)
        self.assertEqual([m.name for m in entries], ["local"])
        self.assertEqual(entries[0].model, "ollama/qwen2.5-coder:32b")

    def test_shadowing_a_name_is_refused(self) -> None:
        models.add("m", "a/b", path=self.path)
        with self.assertRaises(models.RegistryError):
            models.add("m", "c/d", path=self.path)
        # Two runs reporting the same name for different models would make
        # both numbers meaningless, so this has to fail rather than warn.
        self.assertEqual(models.load(self.path)[0].model, "a/b")

    def test_replace_is_explicit(self) -> None:
        models.add("m", "a/b", path=self.path)
        models.add("m", "c/d", path=self.path, replace=True)
        self.assertEqual(models.load(self.path)[0].model, "c/d")

    def test_an_id_without_a_provider_is_refused(self) -> None:
        with self.assertRaises(models.RegistryError):
            models.add("m", "just-a-name", path=self.path)

    def test_names_that_would_confuse_a_report_are_refused(self) -> None:
        for bad in ("Has Space", "UPPER", "slash/name", "", "x" * 41):
            with self.assertRaises(models.RegistryError):
                models.add(bad, "a/b", path=self.path)

    def test_unregistered_values_pass_through(self) -> None:
        # An air-gapped operator who registered nothing must still be able to
        # say --model provider/id.
        self.assertEqual(
            models.resolve("anthropic/claude-opus-5", path=self.path),
            "anthropic/claude-opus-5",
        )
        self.assertIsNone(models.resolve(None, path=self.path))

    def test_registered_names_resolve(self) -> None:
        models.add("tuned", "openai/ft:abc", path=self.path)
        self.assertEqual(models.resolve("tuned", path=self.path), "openai/ft:abc")

    def test_removing_something_absent_says_what_is_known(self) -> None:
        models.add("known", "a/b", path=self.path)
        with self.assertRaises(models.RegistryError) as ctx:
            models.remove("absent", path=self.path)
        self.assertIn("known", str(ctx.exception))

    def test_a_corrupt_registry_raises_rather_than_reads_as_empty(self) -> None:
        self.path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(models.RegistryError):
            models.load(self.path)


class HonestyTests(unittest.TestCase):
    """The CLI must never imply it did something it did not do."""

    def test_train_submit_refuses_and_explains(self) -> None:
        rc, text = run("train", "submit")
        self.assertEqual(rc, 0)
        self.assertIn("does not submit it for you", text)

    def test_verify_rejects_a_missing_candidate_file(self) -> None:
        rc, text = run("verify", "DPTLK", "-c", "/definitely/not/here.m")
        self.assertEqual(rc, 1)
        self.assertIn("not found", text)

    def test_mcp_list_reports_which_tools_need_the_container(self) -> None:
        rc, text = run("mcp", "list")
        self.assertEqual(rc, 0)
        self.assertIn("verify_change", text)
        self.assertIn("needs the container", text)


class CaseSourceTests(unittest.TestCase):
    """Verification without inputs is not verification."""

    def test_the_built_task_set_supplies_cases_when_no_suite_exists(self) -> None:
        from rosetta.workflow import cases_for

        class _NoSuites:
            class suites:  # noqa: D106 - test double
                @staticmethod
                def available() -> list[str]:
                    return []

        taskset = Path(__file__).resolve().parents[1] / "data/tasks/eval_tasks.json"
        if not taskset.exists():
            self.skipTest("no task set built in this checkout")
        routine = json.loads(taskset.read_text())["tasks"][0]["routine"].upper()
        cases, origin = cases_for(_NoSuites(), routine, None)
        self.assertTrue(cases, "task-set fallback produced no cases")
        self.assertIn("eval_tasks.json", origin)

    def test_an_explicit_case_file_wins(self) -> None:
        from rosetta.workflow import cases_for

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "cases.json"
            p.write_text(json.dumps([{"routine": "X", "args": ["1"]}]))
            cases, origin = cases_for(None, "X", str(p))
            self.assertEqual(len(cases or []), 1)
            self.assertEqual(origin, str(p))

    def test_an_empty_case_file_is_refused(self) -> None:
        from rosetta.workflow import cases_for

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "cases.json"
            p.write_text("[]")
            with self.assertRaises(WorkflowError):
                cases_for(None, "X", str(p))


class PublishedSurfaceTests(unittest.TestCase):
    """The website is a download channel, so its commands are part of the CLI's
    contract. `rosetta code` and `rosetta eval` were removed while the landing page
    still shipped copy buttons for both, which meant the first thing a visitor
    pasted into a terminal returned exit 2. A page cannot advertise a command the
    parser does not have.
    """

    ROOT = Path(__file__).resolve().parents[1]

    def _subcommands(self) -> set[str]:
        from rosetta.cli import build_parser

        actions = build_parser()._subparsers._group_actions  # type: ignore[union-attr]
        return set(actions[0].choices)

    def _pages(self) -> list[Path]:
        pages = sorted(self.ROOT.glob("*.html"))
        self.assertTrue(pages, "no published pages found")
        return pages

    def _invocations(self, text: str) -> set[str]:
        """Commands the page actually offers: copy-button payloads and <code>
        contents. Prose like "Rosetta pairs a harness" is not an invocation.
        """
        import re

        sources = re.findall(r'data-copy="([^"]*)"', text)
        sources += re.findall(r"data-origin-command=\"([^\"]*)\"", text)
        sources += re.findall(r"<code[^>]*>(.*?)</code>", text, re.S)
        found = set()
        for source in sources:
            for match in re.finditer(r"(?:python3 -m )?\brosetta\s+([a-z][a-z-]*)", source):
                found.add(match.group(1))
        return found

    def test_every_command_the_site_advertises_exists(self) -> None:
        known = self._subcommands()
        for page in self._pages():
            for name in sorted(self._invocations(page.read_text(encoding="utf-8"))):
                self.assertIn(
                    name, known,
                    f"{page.name} offers `rosetta {name}`, which the CLI does not have. "
                    f"Known: {', '.join(sorted(known))}",
                )

    def test_the_install_command_is_not_a_dead_link(self) -> None:
        installer = self.ROOT / "public" / "install.sh"
        self.assertTrue(installer.is_file(), "public/install.sh must exist to be served")
        body = installer.read_text(encoding="utf-8")
        self.assertIn("PINNED_VERSION=", body)
        # The installer delegates launcher creation to the one script that already
        # gets it right; if that path moves, the install breaks after the download.
        self.assertIn("scripts/install.sh", body)
        self.assertTrue((self.ROOT / "scripts" / "install.sh").is_file())

    def test_version_is_reported_and_single_sourced(self) -> None:
        import rosetta

        result = subprocess.run(
            [sys.executable, "-m", "rosetta", "--version"],
            capture_output=True, text=True, cwd=self.ROOT,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), f"rosetta {rosetta.__version__}")
        # pyproject must read the attribute rather than carry its own copy, or a
        # published wheel can disagree with the source it was built from.
        pyproject = (self.ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('version = {attr = "rosetta.__version__"}', pyproject)
        self.assertNotIn(f'version = "{rosetta.__version__}"', pyproject)


if __name__ == "__main__":
    unittest.main()
