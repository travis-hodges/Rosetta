"""Exercise source installation and CLI input boundaries without a runtime."""

import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from rosetta import cli


class SourceInstallationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="rosetta install '")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.checkout = self.directory / "source $with `characters`"
        self.checkout.mkdir()
        shutil.copytree(cli.ROOT / "rosetta", self.checkout / "rosetta", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(cli.ROOT / ".opencode" / "agent", self.checkout / ".opencode" / "agent")
        shutil.copy(cli.ROOT / ".opencode" / "instructions.md", self.checkout / ".opencode")
        shutil.copy(cli.ROOT / "opencode.json", self.checkout)
        (self.checkout / "data").symlink_to(cli.ROOT / "data", target_is_directory=True)
        (self.checkout / "scripts").mkdir()
        shutil.copy(cli.ROOT / "scripts" / "install.sh", self.checkout / "scripts")
        self.bin_dir = self.directory / "my bin"
        self.env = os.environ.copy()
        self.env["ROSETTA_PYTHON"] = sys.executable
        for key in ("ROSETTA_CORPUS_DIR", "ROSETTA_FILEMAN_CACHE", "OPENCODE_CONFIG_CONTENT"):
            self.env.pop(key, None)
        self.env["PATH"] = str(self.bin_dir)
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.launcher = self.bin_dir / "rosetta"

    def install(self, *args):
        return subprocess.run(
            ["/bin/bash", str(self.checkout / "scripts/install.sh"), *(args or ("--bin-dir", str(self.bin_dir)))],
            cwd=self.directory, env={**self.env, "PATH": "/usr/bin:/bin"},
            text=True, capture_output=True,
        )

    def run_cli(self, *args):
        return subprocess.run([str(self.launcher), *args], cwd=self.directory, env=self.env, text=True, capture_output=True)

    def fake_opencode(self, body):
        executable = self.bin_dir / "opencode"
        executable.write_text("#!/bin/sh\n" + body)
        executable.chmod(0o755)

    def test_help_and_doctor_from_unrelated_directory(self):
        self.assertEqual(self.run_cli("--help").returncode, 0)
        self.fake_opencode("exit 0\n")
        result = self.run_cli("doctor")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report["ok"])
        self.assertEqual(report["mcp_tools"], 8)
        self.assertTrue(report["source_checkout"])

    def test_missing_opencode_doctor_is_not_healthy(self):
        result = self.run_cli("doctor")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse(json.loads(result.stdout)["ok"])
        self.assertEqual(self.run_cli("code", "--prompt", "hello").returncode, 2)

    def test_launcher_forwards_cwd_arguments_and_child_exit(self):
        self.fake_opencode('pwd\nprintf "%s\\n" "$@"\nexit 7\n')
        result = self.run_cli("code", "--model", "local/specialist", "--prompt", "hello '$` world")
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertIn(str(self.directory.resolve()), result.stdout)
        self.assertIn("local/specialist\n", result.stdout)
        self.assertIn("hello '$` world\n", result.stdout)
        self.assertEqual(self.run_cli("models").returncode, 7)

    def test_prompt_timeout_does_not_echo_private_prompt(self):
        self.fake_opencode("exec /bin/sleep 3\n")
        result = self.run_cli("code", "--prompt", "PRIVATE_SENTINEL", "--timeout", "0.05")
        self.assertEqual(result.returncode, 124, result.stderr)
        self.assertNotIn("PRIVATE_SENTINEL", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_reinstall_does_not_overwrite_existing_launcher(self):
        before = self.launcher.read_bytes()
        self.assertNotEqual(self.install().returncode, 0)
        self.assertEqual(self.launcher.read_bytes(), before)

    def test_install_requires_explicit_destination(self):
        result = subprocess.run(["/bin/bash", str(self.checkout / "scripts/install.sh")], cwd=self.directory, capture_output=True)
        self.assertEqual(result.returncode, 2)

    def test_missing_checkout_fails_clearly(self):
        shutil.move(self.checkout, self.directory / "moved checkout")
        result = self.run_cli("--help")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("reinstall the launcher", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


class EvalValidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.baseline = self.directory / "LOCAL.m"
        self.candidate = self.directory / "candidate.m"
        self.cases = self.directory / "cases.json"
        self.baseline.write_text("LOCAL\n Q\n")
        self.candidate.write_text("LOCAL\n Q\n")
        self.cases.write_text('[{"routine":"LOCAL"}]')
        self.argv = ["eval", "--baseline", str(self.baseline), "--candidate", str(self.candidate), "--cases", str(self.cases)]

    def assert_invalid(self, *extra):
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            self.assertEqual(cli.main(self.argv + list(extra)), 2)
        self.assertIn("ERROR:", errors.getvalue())
        self.assertNotIn("Traceback", errors.getvalue())

    def test_report_cannot_overwrite_any_input_or_alias(self):
        for source in (self.baseline, self.candidate, self.cases):
            with self.subTest(source=source.name):
                before = source.read_bytes()
                self.assert_invalid("--out", str(source))
                alias = self.directory / "alias"
                alias.symlink_to(source)
                self.assert_invalid("--out", str(alias))
                alias.unlink()
                os.link(source, alias)
                self.assert_invalid("--out", str(alias))
                alias.unlink()
                self.assertEqual(source.read_bytes(), before)

    def test_malformed_cases_fail_before_execution(self):
        invalid = [None, [], [None], [{}], [{"routine":123}], [{"routine":"LOCAL", "args":[3]}],
                   [{"routine":"LOCAL", "locals_in":{"X":3}}], [{"routine":"LOCAL", "entry":4}],
                   [{"routine":"LOCAL", "typo":3}], [{"routine":"OTHER"}]]
        invalid += [[{"routine":"LOCAL", "timeout_s":value}] for value in [True, 0, -1, float("inf"), float("nan"), 10**400, "10"]]
        for document in invalid:
            with self.subTest(document=document):
                self.cases.write_text(json.dumps(document))
                self.assert_invalid()

    def test_interactive_timeout_rejected(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["code", "--timeout", "1"]), 2)

    def test_nonfinite_prompt_timeout_rejected(self):
        for timeout in ("nan", "inf", "-1", "0"):
            with self.subTest(timeout=timeout), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    cli.main(["code", "--prompt", "hello", "--timeout", timeout])
                self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
