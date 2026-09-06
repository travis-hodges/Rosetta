"""Exercise source installation and CLI input boundaries without a runtime."""

import contextlib
import importlib.util
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


def load_branding_module():
    path = cli.ROOT / "scripts" / "rosetta-brand.py"
    spec = importlib.util.spec_from_file_location("rosetta_brand", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BrandingLauncherTests(unittest.TestCase):
    def test_old_direct_binary_alias_is_migrated_to_source_launcher(self):
        branding = load_branding_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "opencode"
            binary.write_text("#!/bin/sh\n")
            link = root / "rosetta"
            link.symlink_to(binary)

            installed = branding.install_command(binary, root)

            self.assertEqual(installed.resolve(), cli.ROOT / "bin" / "rosetta")
            self.assertEqual(branding.install_command(binary, root), installed)

    def test_branding_installer_never_overwrites_an_unrelated_command(self):
        branding = load_branding_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "opencode"
            binary.write_text("#!/bin/sh\n")
            link = root / "rosetta"
            link.write_text("user-owned\n")

            with self.assertRaises(SystemExit):
                branding.install_command(binary, root)

            self.assertEqual(link.read_text(), "user-owned\n")


class BrandingFontTests(unittest.TestCase):
    """The marked glyphs are what the TUI paints, and nothing previews them.

    A marker only describes an enclosed counter, so these check that no marker
    escapes the letter it belongs to. Marking r's open right side once gave the
    glyph a shadow rectangle and a bottom bar it does not have, and the TUI home
    screen drew a blob instead of an r.
    """

    INK = "\u2588\u2580\u2584"  # full block, upper half, lower half

    def setUp(self):
        self.branding = load_branding_module()

    def test_plain_and_marked_tables_describe_the_same_glyphs(self):
        plain, marked = self.branding.PLAIN, self.branding.MARKED
        self.assertEqual(sorted(plain), sorted(marked))
        for name, table in (("PLAIN", plain), ("MARKED", marked)):
            for glyph, rows in table.items():
                self.assertEqual(len(rows), 4, f"{name}[{glyph!r}] is not 4 rows")
                for row in rows:
                    self.assertEqual(len(row), 4, f"{name}[{glyph!r}] row {row!r} is not 4 wide")

    def test_every_marker_stands_where_the_plain_glyph_allows_it(self):
        for glyph, marked_rows in self.branding.MARKED.items():
            plain_rows = self.branding.PLAIN[glyph]
            for row, (marked, plain) in enumerate(zip(marked_rows, plain_rows)):
                for column, (mark, ink) in enumerate(zip(marked, plain)):
                    where = f"{glyph!r} row {row} column {column}"
                    if mark == "_":       # counter fill: plain leaves it empty
                        self.assertEqual(ink, " ", f"fill over ink at {where}")
                    elif mark == "^":     # half stroke over that fill
                        self.assertEqual(ink, "\u2580", f"stroke off-stroke at {where}")
                    elif mark == "~":     # shadow-coloured half stroke
                        self.assertEqual(ink, " ", f"shadow over ink at {where}")
                    else:
                        self.assertEqual(mark, ink, f"marked glyph diverges at {where}")

    def test_a_counter_fill_is_closed_on_the_right_or_below(self):
        for glyph, rows in self.branding.MARKED.items():
            for index, row in enumerate(rows):
                below = rows[index + 1] if index + 1 < len(rows) else "    "
                for column, mark in enumerate(row):
                    if mark != "_":
                        continue
                    closed_right = any(ch in self.INK for ch in row[column + 1:])
                    closed_below = below[column] in self.INK
                    self.assertTrue(
                        closed_right or closed_below,
                        f"{glyph!r} fills open page at row {index} column {column}: "
                        f"no stroke to the right and none beneath it",
                    )

    def test_a_shadow_stroke_is_bounded_by_real_strokes(self):
        for glyph, rows in self.branding.MARKED.items():
            for index, row in enumerate(rows):
                for column, mark in enumerate(row):
                    if mark != "~":
                        continue
                    left = any(ch in self.INK for ch in row[:column])
                    right = any(ch in self.INK for ch in row[column + 1:])
                    self.assertTrue(
                        left and right,
                        f"{glyph!r} trails a shadow stroke at row {index} column {column}: "
                        f"it must sit between two real strokes",
                    )

    def test_the_wordmark_matches_the_shape_docs_branding_promises(self):
        # docs/BRANDING.md prints this under "What you get". Both tables have to
        # agree on it: the plain wordmark is the CLI's, the marked one the TUI's.
        expected = [
            "\u2588\u2580\u2580\u2584 \u2588\u2580\u2580\u2588 \u2588\u2580\u2580\u2580 \u2588\u2580\u2580\u2588 \u2580\u2588\u2580\u2580 \u2580\u2588\u2580\u2580 \u2584\u2580\u2580\u2588",
            "\u2588    \u2588  \u2588 \u2580\u2580\u2580\u2588 \u2588\u2580\u2580\u2580  \u2588    \u2588   \u2588\u2580\u2580\u2588",
            "\u2580    \u2580\u2580\u2580\u2580 \u2580\u2580\u2580\u2580 \u2580\u2580\u2580\u2580  \u2580\u2580   \u2580\u2580  \u2580\u2580\u2580\u2580",
        ]
        plain = self.branding.rows(self.branding.PLAIN, self.branding.brand)
        self.assertEqual(plain[1:], expected)

        # The TUI draws the wordmark in two halves; joined, they are the same shape.
        left = self.branding.rows(self.branding.MARKED, "ro")
        right = self.branding.rows(self.branding.MARKED, "setta")
        joined = [
            "".join({"_": " ", "^": "\u2580", "~": "\u2580"}.get(ch, ch) for ch in a + " " + b)
            for a, b in zip(left, right)
        ]
        self.assertEqual(joined[1:], expected)

    def test_mumps_prompt_label_has_no_visible_padding(self):
        patches = {
            name: (old, new)
            for name, old, new, _required in self.branding.build_patches()
        }
        old, new = patches["prompt label"]
        self.assertEqual(len(old), len(new))
        self.assertIn(b"MUMPS change", new)


class SourceInstallationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="rosetta install '")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.checkout = self.directory / "source $with `characters`"
        self.checkout.mkdir()
        shutil.copytree(cli.ROOT / "rosetta", self.checkout / "rosetta", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(cli.ROOT / ".opencode" / "agent", self.checkout / ".opencode" / "agent")
        shutil.copytree(cli.ROOT / ".opencode" / "plugins", self.checkout / ".opencode" / "plugins")
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

    def test_help_and_status_from_unrelated_directory(self):
        self.assertEqual(self.run_cli("--help").returncode, 0)
        self.fake_opencode("exit 0\n")
        result = self.run_cli("status", "--plain")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("workflows", result.stdout)
        self.assertIn("this checkout", result.stdout)

    def test_missing_opencode_blocks_the_tui(self):
        self.assertEqual(self.run_cli("code", "--prompt", "hello").returncode, 2)

    def test_launcher_forwards_cwd_arguments_and_child_exit(self):
        self.fake_opencode('pwd\nprintf "%s\\n" "$@"\nexit 7\n')
        result = self.run_cli("code", "--model", "local/specialist", "--prompt", "hello '$` world")
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertIn(str(self.directory.resolve()), result.stdout)
        self.assertIn("local/specialist\n", result.stdout)
        self.assertIn("hello '$` world\n", result.stdout)
        self.assertEqual(self.run_cli("models").returncode, 7)

    def test_bare_launcher_opens_tui_in_current_project(self):
        self.fake_opencode('pwd\nprintf "%s\\n" "$@"\nexit 0\n')
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], str(self.directory.resolve()))
        self.assertIn(str(self.directory.resolve()), lines)
        self.assertIn("--agent", lines)
        self.assertIn("rosetta-agent", lines)

    def test_project_path_is_the_default_tui_and_mumps_corpus(self):
        project = self.directory / "target project"
        project.mkdir()
        self.fake_opencode('printf "%s\\n" "$PWD" "$ROSETTA_CORPUS_DIR" "$@"\nexit 0\n')
        result = self.run_cli(str(project))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[:2], [str(project.resolve()), str(project.resolve())])

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


class TuiConfigurationTests(unittest.TestCase):
    def test_external_project_receives_every_rosetta_command(self):
        config = cli._config()
        self.assertEqual(
            set(config["command"]),
            {
                "benchmark", "demo", "doctor", "evaluate", "globals",
                "moneymoment", "pipeline", "report", "routine", "start",
                "train", "verify",
            },
        )
        self.assertTrue(config["command"]["doctor"]["subtask"])
        self.assertIn("-m rosetta doctor", config["command"]["doctor"]["template"])
        self.assertIn(str(cli.ROOT), config["command"]["doctor"]["template"])
        self.assertEqual(config["default_agent"], "rosetta-agent")
        self.assertEqual(len(config["plugin"]), 1)
        self.assertTrue(config["plugin"][0].endswith("/.opencode/plugins/rosetta-experience.js"))
        self.assertEqual(config["model"], "opencode/big-pickle")
        self.assertEqual(
            config["provider"]["opencode"]["models"]["big-pickle"]["name"],
            "Translator 1.0",
        )
        primary = {
            name
            for name, profile in config["agent"].items()
            if profile.get("mode") == "primary" and not profile.get("disable")
        }
        self.assertEqual(
            primary,
            {"rosetta-agent", "rosetta-plan", "rosetta-verify"},
        )
        self.assertTrue(config["agent"]["build"]["disable"])
        self.assertTrue(config["agent"]["plan"]["disable"])
        for command in config["command"].values():
            self.assertIn(command["agent"], config["agent"])

    def test_user_surfaces_are_merged_without_displacing_rosetta(self):
        base = cli._config()
        merged = cli._merge_user_config(base, json.dumps({
            "provider": {"local": {}},
            "mcp": {"user-server": {"type": "remote", "url": "https://example.invalid"}},
            "agent": {"reviewer": {"mode": "subagent"}},
            "command": {"ship": {"template": "Ship it"}},
            "instructions": ["/tmp/user-instructions.md"],
            "plugin": ["example-plugin"],
            "default_agent": "reviewer",
        }))
        self.assertIn("local", merged["provider"])
        self.assertIn("user-server", merged["mcp"])
        self.assertIn("rosetta", merged["mcp"])
        self.assertIn("reviewer", merged["agent"])
        self.assertIn("rosetta-agent", merged["agent"])
        self.assertIn("ship", merged["command"])
        self.assertIn("verify", merged["command"])
        self.assertIn("/tmp/user-instructions.md", merged["instructions"])
        self.assertIn(str(cli.ROOT / ".opencode" / "instructions.md"), merged["instructions"])
        self.assertEqual(merged["plugin"][0], "example-plugin")
        self.assertTrue(merged["plugin"][-1].endswith("rosetta-experience.js"))
        self.assertEqual(merged["default_agent"], "rosetta-agent")

    def test_model_label_merge_preserves_user_provider_settings(self):
        merged = cli._merge_user_config(cli._config(), json.dumps({
            "provider": {
                "opencode": {
                    "options": {"timeout": 90000},
                    "models": {
                        "big-pickle": {"options": {"temperature": 0.2}},
                        "another": {"name": "Another"},
                    },
                }
            }
        }))
        provider = merged["provider"]["opencode"]
        self.assertEqual(provider["options"]["timeout"], 90000)
        self.assertIn("another", provider["models"])
        self.assertEqual(provider["models"]["big-pickle"]["name"], "Translator 1.0")
        self.assertEqual(
            provider["models"]["big-pickle"]["options"]["temperature"],
            0.2,
        )


if __name__ == "__main__":
    unittest.main()
