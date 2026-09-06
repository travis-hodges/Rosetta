"""Real YottaDB process lifecycle checks; no runtime mocks or database writes."""

import unittest

from rosetta.core.config import CoreConfig
from rosetta.core.protocol import Request
from rosetta.core.worker import MWorker, WorkerError, WorkerTimeout, _run


class WorkerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.worker = MWorker(CoreConfig())
        try:
            self.worker._check_container()
        except WorkerError as exc:
            self.skipTest(str(exc))
        self.addCleanup(self.worker.stop)
        self.worker.ensure_started()
        self.peer = MWorker(CoreConfig())
        self.addCleanup(self.peer.stop)
        self.peer.ensure_started()
        self.pid = self.remote("cat", self.worker._pid_file).stdout.strip()

    def remote(self, *args, check=True):
        return _run(self.worker._docker("exec", "-u", self.worker.config.instance,
                                        self.worker.config.container, *args), check=check)

    def freeze_worker(self):
        # A stopped real mumps process reproduces an unresponsive execution
        # without running a mutation or touching database state.
        self.remote("kill", "-STOP", self.pid)

    def assert_worker_gone_and_peer_healthy(self):
        result = self.remote("cat", f"/proc/{self.pid}/stat", check=False)
        if result.returncode == 0:
            self.assertEqual(result.stdout.split()[2], "Z", result.stdout)
        self.assertTrue(self.peer.send(Request().add("CMD", "PING")).ok)

    def test_timeout_terminates_remote_worker_and_respawns(self):
        self.freeze_worker()
        with self.assertRaises(WorkerTimeout):
            self.worker.send(Request().add("CMD", "PING"), timeout_s=0.1)
        self.assert_worker_gone_and_peer_healthy()
        self.assertTrue(self.worker.send(Request().add("CMD", "PING")).ok)
        self.assertEqual(self.worker.spawn_count, 2)

    def test_graceful_stop_falls_back_to_remote_termination(self):
        self.freeze_worker()
        self.worker.stop()
        self.assert_worker_gone_and_peer_healthy()

    def test_dead_docker_client_does_not_abandon_remote_worker(self):
        self.freeze_worker()
        self.worker._proc.kill()
        self.worker._proc.wait(timeout=5)
        self.worker.stop()
        self.assert_worker_gone_and_peer_healthy()

    def test_wrong_token_never_signals_another_worker(self):
        token, marker = self.worker._remote_token, self.worker._pid_file
        peer_pid = self.remote("cat", self.peer._pid_file).stdout.strip()
        self.remote("bash", "-c", 'printf "%s\\n" "$1" > "$2"', "marker", peer_pid, marker)
        try:
            self.worker._terminate_remote()
            self.assertTrue(self.peer.send(Request().add("CMD", "PING")).ok)
            self.assertTrue(self.worker.send(Request().add("CMD", "PING")).ok)
        finally:
            self.remote("bash", "-c", 'printf "%s\\n" "$1" > "$2"', "marker", self.pid, marker)
            self.worker._remote_token, self.worker._pid_file = token, marker


if __name__ == "__main__":
    unittest.main()
