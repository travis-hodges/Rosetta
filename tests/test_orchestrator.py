import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from orchestration.orchestrator import Issue, Orchestrator, slugify, truncate


class UtilityTests(unittest.TestCase):
    def test_slugify_is_branch_safe_and_bounded(self) -> None:
        result = slugify("[Build] Add OAuth + tests! " * 4)
        self.assertRegex(result, r"^[a-z0-9-]+$")
        self.assertLessEqual(len(result), 42)

    def test_slugify_has_fallback(self) -> None:
        self.assertEqual(slugify("!!!"), "task")

    def test_truncate_preserves_short_text(self) -> None:
        self.assertEqual(truncate("hello", 100), "hello")

    def test_truncate_marks_long_text(self) -> None:
        self.assertIn("truncated", truncate("x" * 500, 120))


class PromptTests(unittest.TestCase):
    def test_prompt_contains_assignment_and_subagent_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".git").mkdir()
            with mock.patch.dict(os.environ, {"ROSETTA_SANDBOX": "workspace-write"}, clear=False):
                orchestrator = Orchestrator(root)
            orchestrator.github.recent_context = mock.Mock(return_value="- #1 prior task")
            issue = Issue(
                number=7,
                title="Build the parser",
                body="Acceptance: tests pass",
                author="travis-hodges",
                labels=("agent:ready", "agent:builder"),
                url="https://example.test/issues/7",
                comments=({"author": {"login": "travis-hodges"}, "body": "Keep API stable"},),
            )
            prompt = orchestrator.build_prompt(issue)
            self.assertIn("issue #7", prompt)
            self.assertIn("Role selected on GitHub: builder", prompt)
            self.assertIn("spawn at least one appropriate project subagent", prompt)
            self.assertIn("Keep API stable", prompt)
            self.assertIn("#1 prior task", prompt)

    def test_poll_once_does_not_claim_more_than_worker_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".git").mkdir()
            with mock.patch.dict(os.environ, {"ROSETTA_MAX_WORKERS": "2"}, clear=False):
                orchestrator = Orchestrator(root)
            issues = [
                Issue(number=n, title=f"Task {n}", body="", author="owner", labels=("agent:ready",), url="")
                for n in range(1, 5)
            ]
            orchestrator.github.ready_issues = mock.Mock(return_value=issues)
            orchestrator.claim = mock.Mock(return_value=False)
            orchestrator.poll_once()
            self.assertEqual(orchestrator.claim.call_count, 2)


if __name__ == "__main__":
    unittest.main()
