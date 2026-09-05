import tempfile
import unittest
from pathlib import Path
from unittest import mock

from orchestration.expire_service import remove_registered_worktrees, terminate_codex_processes, validate_paths


class ExpirySafetyTests(unittest.TestCase):
    def test_validate_paths_accepts_only_expected_installation_targets(self) -> None:
        repository_root = Path("/tmp/example-rosetta")
        launch_agents = Path.home() / "Library" / "LaunchAgents"
        validate_paths(
            repository_root=repository_root,
            state_dir=repository_root / ".git" / "rosetta-orchestrator",
            service_plist=launch_agents / "com.rosetta.codex-orchestrator.plist",
            expiry_plist=launch_agents / "com.rosetta.codex-orchestrator.expiry.plist",
            support_dir=Path.home() / "Library" / "Application Support" / "RosettaOrchestrator",
        )

    def test_validate_paths_rejects_broad_state_directory(self) -> None:
        repository_root = Path("/tmp/example-rosetta")
        launch_agents = Path.home() / "Library" / "LaunchAgents"
        with self.assertRaisesRegex(RuntimeError, "unsafe cleanup targets"):
            validate_paths(
                repository_root=repository_root,
                state_dir=repository_root,
                service_plist=launch_agents / "com.rosetta.codex-orchestrator.plist",
                expiry_plist=launch_agents / "com.rosetta.codex-orchestrator.expiry.plist",
                support_dir=Path.home() / "Library" / "Application Support" / "RosettaOrchestrator",
            )

    def test_worktree_cleanup_is_bounded_to_children(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository_root = Path(temporary)
            state_dir = repository_root / ".git" / "rosetta-orchestrator"
            (state_dir / "worktrees" / "issue-9").mkdir(parents=True)
            with mock.patch("orchestration.expire_service.subprocess.run") as run_mock:
                remove_registered_worktrees(repository_root, state_dir)
            first_command = run_mock.call_args_list[0].args[0]
            self.assertEqual(first_command[:4], ["git", "worktree", "remove", "--force"])
            self.assertEqual(Path(first_command[4]), state_dir / "worktrees" / "issue-9")

    def test_stale_pid_file_does_not_kill_an_unrelated_process(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary)
            pid_path = state_dir / "logs" / "issue-4" / "codex.pid"
            pid_path.parent.mkdir(parents=True)
            pid_path.write_text("4242", encoding="utf-8")
            ps_result = mock.Mock(stdout="/usr/bin/sleep 30\n")
            with mock.patch("orchestration.expire_service.subprocess.run", return_value=ps_result), mock.patch(
                "orchestration.expire_service.os.killpg"
            ) as kill_mock:
                terminate_codex_processes(state_dir)
            kill_mock.assert_not_called()
