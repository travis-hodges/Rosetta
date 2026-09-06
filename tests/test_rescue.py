"""Integration seams: explicit corpora, CLI launch, and benchmark isolation."""
import gzip
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from rosetta import cli
from rosetta.demo.agents import RosettaAgent
from rosetta.tools import fileman, sources
from rosetta.tools.cases import SuiteStore, TaskStore


class CorpusConfigurationTests(unittest.TestCase):
    def test_custom_corpus_does_not_contact_va_or_inherit_va_schema(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"ROSETTA_CORPUS_DIR": directory}, clear=True):
            Path(directory, "LOCAL.m").write_text("LOCAL\n Q\n")
            store = sources.RoutineStore()
            self.assertIsNone(store.container)
            self.assertEqual(store.list()[0], ["LOCAL"])
            self.assertIn("LOCAL", store.read("LOCAL").source)
            self.assertFalse(fileman.default_dictionary().available)
            with self.assertRaises(sources.RoutineNotFound):
                store.read("XLFDT")

    def test_explicit_missing_corpus_is_an_error(self):
        with patch.dict(os.environ, {"ROSETTA_CORPUS_DIR": "/no/such/rosetta-corpus"}):
            with self.assertRaises(ValueError):
                sources.RoutineStore()

    def test_explicit_dictionary_errors_are_not_silently_hidden(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "dd.json.gz")
            with gzip.open(path, "wt") as stream:
                stream.write("broken json")
            with patch.dict(os.environ, {"ROSETTA_FILEMAN_CACHE": str(path)}):
                with self.assertRaises(ValueError):
                    fileman.default_dictionary()

    def test_external_suites_and_tasks_stay_in_selected_directory(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"ROSETTA_TASKS_DIR": directory}, clear=True):
            self.assertEqual(TaskStore().tasks_dir, Path(directory))
            self.assertEqual(SuiteStore().suites_dir, Path(directory, "suites"))
            for store, key in [(TaskStore(), "../secrets"), (SuiteStore(), "../secrets")]:
                with self.assertRaises(ValueError):
                    store.load(key)


class TerminalTests(unittest.TestCase):
    def test_status_source_checkout_runs_without_runtime(self):
        result = subprocess.run(
            [os.sys.executable, "-m", "rosetta", "status", "--plain"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("workflows", result.stdout)
        self.assertIn("this checkout", result.stdout)

    def test_launch_forwards_custom_model_and_corpus(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(cli, "_harness", return_value="opencode"), patch.object(cli.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
                self.assertEqual(cli.main(["code", directory, "--model", "local/specialist", "--corpus", directory, "--prompt", "Explain LOCAL"]), 0)
                command = run.call_args.args[0]
                self.assertIn("local/specialist", command)
                self.assertEqual(run.call_args.kwargs["env"]["PWD"], str(Path(directory).resolve()))
                self.assertEqual(run.call_args.kwargs["env"]["ROSETTA_CORPUS_DIR"], str(Path(directory).resolve()))
                config = json.loads(run.call_args.kwargs["env"]["OPENCODE_CONFIG_CONTENT"])
                self.assertEqual(config["mcp"]["rosetta"]["environment"]["PYTHONPATH"], str(cli.ROOT))

    def test_bare_launch_uses_project_as_workspace_and_default_corpus(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(cli, "_harness", return_value="opencode"), patch.object(
                cli.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)
            ) as run, patch.object(Path, "cwd", return_value=Path(directory)):
                self.assertEqual(cli.main([]), 0)
            command = run.call_args.args[0]
            environment = run.call_args.kwargs["env"]
            config = json.loads(environment["OPENCODE_CONFIG_CONTENT"])
            self.assertEqual(command[1], str(Path(directory).resolve()))
            self.assertEqual(environment["ROSETTA_PROJECT_DIR"], str(Path(directory).resolve()))
            self.assertEqual(environment["ROSETTA_CORPUS_DIR"], str(Path(directory).resolve()))
            self.assertEqual(
                config["mcp"]["rosetta"]["environment"]["ROSETTA_CORPUS_DIR"],
                str(Path(directory).resolve()),
            )
            self.assertIn("verify", config["command"])

    def test_benchmark_environment_disables_inherited_tools(self):
        agent = RosettaAgent(isolated=True, tools_on=True)
        try:
            with patch.dict(os.environ, {"OPENCODE_CONFIG_CONTENT": json.dumps({"tools": {"read": True}, "provider": {"local": {}}})}):
                config = json.loads(agent._environment()["OPENCODE_CONFIG_CONTENT"])
            self.assertEqual(config["tools"], {"*": False})
            self.assertEqual(config["permission"], {"*": "deny"})
            self.assertIn("local", config["provider"])
            self.assertNotEqual(Path(agent._workdir()), cli.ROOT)
            self.assertEqual(agent._environment()["PWD"], agent._workdir())
            with patch("rosetta.demo.agents.shutil.which", return_value="/usr/bin/opencode"):
                command = agent._command("example")
            self.assertEqual(command[command.index("--dir") + 1], agent._workdir())
        finally:
            agent.close()


if __name__ == "__main__":
    unittest.main()
