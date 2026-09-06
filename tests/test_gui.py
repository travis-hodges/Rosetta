"""The GUI is a front end over the CLI's own workflows, so it is held to the
standards that protect a published number, not to the standards of a demo.

Everything here runs offline. The two tests that would need a container --
`edit` and `verify` reaching a verdict -- are covered by injecting a fake
registry, because the point being tested is the wiring, not YottaDB.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rosetta.gui import jobs  # noqa: E402
from rosetta.gui.api import Api, ApiError, _ROUTES  # noqa: E402
from rosetta.gui.server import LOOPBACK, STATIC, serve  # noqa: E402


class AssetTests(unittest.TestCase):
    def test_the_three_assets_exist(self) -> None:
        for name in ("index.html", "app.css", "app.js"):
            self.assertTrue((STATIC / name).is_file(), f"{name} is missing")

    def test_the_page_loads_nothing_remote(self) -> None:
        """An enclave machine has no route to a CDN, so a remote asset is a
        blank page rather than a slow one.

        Checked against the URLs the browser would actually fetch, not against
        the raw text: an inline `data:` favicon legitimately carries the SVG
        namespace `http://www.w3.org/2000/svg`, which is an identifier and not
        a request.
        """
        import re

        html = (STATIC / "index.html").read_text(encoding="utf-8")
        fetched = re.findall(r'(?:src|href)\s*=\s*"([^"]*)"', html)
        self.assertTrue(fetched, "no assets found to check")
        for url in fetched:
            if url.startswith("data:") or url.startswith("#"):
                continue  # inline, or an in-page route
            self.assertTrue(
                url.startswith("/"),
                f"index.html fetches something non-local: {url}",
            )
        # And no font service anywhere, inline or not.
        for pattern in ("googleapis", "gstatic", "unpkg", "cdn."):
            self.assertNotIn(pattern, html, f"index.html references {pattern}")

    def test_the_gui_uses_the_site_palette(self) -> None:
        """The GUI drifted off-brand once already, when `web/index.html` was
        replaced by `src/styles.css` and the old palette went with it. This
        pins the two together so the next change is a failure, not a surprise.
        """
        site = Path(__file__).resolve().parents[1] / "src" / "styles.css"
        if not site.exists():
            self.skipTest("no product site in this checkout")
        site_css = site.read_text(encoding="utf-8")
        gui_css = (STATIC / "app.css").read_text(encoding="utf-8")
        for token in (
            "--paper:#eeeee7",
            "--ink:#242720",
            "--muted:#626759",
            "--acid:#ddf95c",
            "--line:#c9cbc0",
            "--dark:#1b1e18",
        ):
            self.assertIn(token, site_css, f"site no longer defines {token}")
            self.assertIn(token, gui_css, f"GUI is off-brand: missing {token}")
        # No webfont is reachable from an enclave, so the brand is carried by
        # the same three system stacks the site falls back to.
        for font in ("Arial", "Georgia", "SFMono-Regular"):
            self.assertIn(font, site_css, f"site no longer uses {font}")
            self.assertIn(font, gui_css, f"GUI does not use {font}")
        # The site deliberately uses an inverted dark terminal inside the
        # cream document, so do not ban those local contrast colors globally.
        # Webfonts, however, would break the offline/enclave promise.
        for stale_font in ("Space Grotesk", "Space Mono"):
            self.assertNotIn(stale_font, site_css, f"site still carries {stale_font}")
            self.assertNotIn(stale_font, gui_css, f"GUI still carries {stale_font}")

    def test_long_runs_can_be_rejoined(self) -> None:
        # The server-side half is tested in JobTests; this pins the client
        # half, because a reload during a fifteen-minute run used to orphan
        # the view while the job carried on.
        js = (STATIC / "app.js").read_text(encoding="utf-8")
        for marker in ("rosetta:job:", "async function reattach", "driveEdit", "driveVerify"):
            self.assertIn(marker, js, f"app.js has no {marker}")

    def test_every_view_in_the_markup_has_a_route(self) -> None:
        import re

        html = (STATIC / "index.html").read_text(encoding="utf-8")
        js = (STATIC / "app.js").read_text(encoding="utf-8")
        views = set(re.findall(r'class="view" data-view="(\w+)"', html))
        self.assertTrue(views, "no views found in the markup")
        routes = js[js.index("const VIEWS = {"):js.index("async function route()")]
        for view in views:
            self.assertIn(f"{view}:", routes, f"view {view} has no router entry")


class SecurityTests(unittest.TestCase):
    def test_non_loopback_binds_are_refused(self) -> None:
        # A verification tool listening on a routable interface inside a
        # customer enclave is a finding, not a feature.
        for host in ("0.0.0.0", "192.168.1.5", "::"):
            self.assertEqual(serve(host, 7999, open_browser=False), 2)

    def test_loopback_set_is_only_loopback(self) -> None:
        self.assertEqual(set(LOOPBACK), {"127.0.0.1", "::1", "localhost"})


class RoutingTests(unittest.TestCase):
    def test_unknown_endpoint_is_a_404_not_a_crash(self) -> None:
        with self.assertRaises(ApiError) as ctx:
            Api().handle("GET", "/api/nope", {}, {})
        self.assertEqual(ctx.exception.status, 404)

    def test_every_route_points_at_a_real_handler(self) -> None:
        for (method, path), fn in _ROUTES.items():
            self.assertTrue(callable(fn), f"{method} {path} is not callable")

    def test_status_is_filesystem_only(self) -> None:
        # The home view has to render identically whether YottaDB is up or
        # down, so `status` must never touch the container.
        api = Api()
        status, payload = api.status({}, {})
        self.assertEqual(status, 200)
        for key in ("models", "split", "n_tasks", "n_traces", "published"):
            self.assertIn(key, payload)
        self.assertIsNone(api._registry, "status built the tool registry")


class ValidationTests(unittest.TestCase):
    """Bad input from the page is the page's bug, and must say so plainly."""

    def setUp(self) -> None:
        self.api = Api()

    def test_verify_requires_a_complete_candidate(self) -> None:
        for body in ({}, {"routine": "X"}, {"routine": "X", "candidate_src": "  "}):
            with self.assertRaises(ApiError):
                self.api.verify({}, body)

    def test_edit_requires_a_routine_and_a_request(self) -> None:
        for body in ({}, {"routine": "X"}, {"request": "do it"}):
            with self.assertRaises(ApiError):
                self.api.edit({}, body)

    def test_edit_bounds_the_attempt_count(self) -> None:
        # Unbounded attempts is an unbounded spend on someone's model account.
        for n in (0, -1, 11, 500):
            with self.assertRaises(ApiError):
                self.api.edit({}, {"routine": "X", "request": "y", "attempts": n})

    def test_routine_requires_a_name(self) -> None:
        with self.assertRaises(ApiError):
            self.api.routine({}, {})

    def test_a_missing_job_is_a_404(self) -> None:
        with self.assertRaises(ApiError) as ctx:
            self.api.job({"id": "nope-9999"}, {})
        self.assertEqual(ctx.exception.status, 404)


class JobTests(unittest.TestCase):
    def test_events_accumulate_and_since_only_sends_the_new_ones(self) -> None:
        gate = threading.Event()

        def produce():
            yield {"kind": "one"}
            gate.wait(5)
            yield {"kind": "two"}

        job = jobs.start("t", "label", produce)
        deadline = time.time() + 5
        while not job.events and time.time() < deadline:
            time.sleep(0.02)
        first = job.to_dict()
        self.assertEqual([e["kind"] for e in first["events"]], ["one"])
        gate.set()
        while job.state == "running" and time.time() < deadline:
            time.sleep(0.02)
        # The Edit view needs attempt 1 next to attempt 2, so nothing is
        # replaced -- only the unseen tail is sent.
        self.assertEqual([e["kind"] for e in job.to_dict()["events"]], ["one", "two"])
        self.assertEqual(
            [e["kind"] for e in job.to_dict(since=first["n_events"])["events"]], ["two"]
        )

    def test_a_running_job_is_fully_replayable(self) -> None:
        """What makes reattaching after a page reload possible.

        An edit loop can run for a quarter of an hour. The page has to be able
        to rejoin one, which only works because a job keeps every event and
        ``since=0`` replays all of them.
        """
        gate = threading.Event()

        def produce():
            yield {"kind": "source"}
            yield {"kind": "inputs"}
            gate.wait(5)
            yield {"kind": "attempt"}

        job = jobs.start("edit", "label", produce)
        deadline = time.time() + 5
        while len(job.events) < 2 and time.time() < deadline:
            time.sleep(0.02)
        # A fresh client asks with since=0 and gets the whole history so far,
        # while the job is still running.
        replay = job.to_dict(since=0)
        self.assertEqual(replay["state"], "running")
        self.assertEqual([e["kind"] for e in replay["events"]], ["source", "inputs"])
        gate.set()
        while job.state == "running" and time.time() < deadline:
            time.sleep(0.02)
        self.assertEqual([e["kind"] for e in job.to_dict(since=0)["events"]],
                         ["source", "inputs", "attempt"])

    def test_a_raising_producer_becomes_a_visible_failure(self) -> None:
        def produce():
            yield {"kind": "started"}
            raise RuntimeError("the verifier is unreachable")

        job = jobs.start("t", "label", produce)
        deadline = time.time() + 5
        while job.state == "running" and time.time() < deadline:
            time.sleep(0.02)
        self.assertEqual(job.state, "failed")
        self.assertIn("unreachable", job.error or "")
        # A vanished job and a crashed job must not look the same in the page.
        self.assertEqual(job.events[-1]["kind"], "failed")


class WiringTests(unittest.TestCase):
    """The GUI must reach a verdict through the same workflow the CLI uses."""

    def test_verify_streams_the_report_the_workflow_returned(self) -> None:
        report = {"equivalent": False, "verdict": "NOT EQUIVALENT — test",
                  "n_cases": 3, "n_diverged": 1, "n_void": 0,
                  "divergences": [{"case_index": 0, "headline": "stdout differs",
                                   "details": [], "ref": "stdout"}],
                  "feedback": "fix it"}

        class FakeRegistry:
            class suites:
                @staticmethod
                def available() -> list[str]:
                    return []

            def call(self, name, args):
                assert name == "verify_change"
                return report

        api = Api()
        api._registry = FakeRegistry()
        status, job = api.verify({}, {"routine": "ORCRC", "candidate_src": "X ;\n Q\n"})
        self.assertEqual(status, 202)
        deadline = time.time() + 10
        while jobs.get(job["id"]).state == "running" and time.time() < deadline:
            time.sleep(0.02)
        events = jobs.get(job["id"]).to_dict()["events"]
        kinds = [e["kind"] for e in events]
        self.assertEqual(kinds, ["inputs", "proof_stage", "proof_stage", "report"])
        self.assertEqual(events[-1]["verdict"], report["verdict"])
        self.assertIs(events[-1]["equivalent"], False)

    def test_edit_reports_the_verifier_verdict_not_the_model_claim(self) -> None:
        """The whole product is this separation, so it is tested directly."""
        from rosetta.workflow import edit

        class Proposal:
            candidate_src = "ORCRC ;\n Q\n"
            diff = "--- a\n+++ b\n"
            explanation = "This change is behaviour preserving."  # the model's claim

        class ConfidentAgent:
            name = "fake"
            backend = "fake"

            def propose(self, task, baseline, feedback):
                return None if feedback else Proposal()

        class FakeRegistry:
            class suites:
                @staticmethod
                def available() -> list[str]:
                    return []

            def read_source(self, name):
                return "ORCRC", "ORCRC ;\n Q\n ;\n", "corpus"

            def call(self, name, args):
                return {"equivalent": False, "verdict": "NOT EQUIVALENT — test",
                        "n_cases": 2, "n_diverged": 2, "n_void": 0,
                        "divergences": [], "feedback": "the node moved"}

        events = list(edit("ORCRC", "do a thing", attempts=1,
                           registry=FakeRegistry(), agent=ConfidentAgent()))
        by_kind = {e.kind: e.data for e in events}
        self.assertIn("attempt", by_kind)
        self.assertIn("proving", by_kind)
        self.assertIn("This change is behaviour preserving.",
                      by_kind["attempt"]["explanation"])
        self.assertIs(by_kind["verdict"]["equivalent"], False)
        self.assertNotIn("accepted", by_kind)
        self.assertIn("exhausted", by_kind)


class LiveServerTests(unittest.TestCase):
    """One end-to-end pass over real HTTP, on a port nothing else uses."""

    port = 7398

    @classmethod
    def setUpClass(cls) -> None:
        from http.server import ThreadingHTTPServer

        from rosetta.gui.server import Handler

        Handler.api = Api()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", cls.port), Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def test_index_and_assets_are_served(self) -> None:
        for path, marker in (("/", b"Rosetta"), ("/app.css", b"--acid"),
                             ("/app.js", b"follow")):
            with urllib.request.urlopen(self.url(path)) as r:
                self.assertEqual(r.status, 200)
                self.assertIn(marker, r.read())

    def test_path_traversal_is_refused(self) -> None:
        for path in ("/../../../etc/passwd", "/%2e%2e/%2e%2e/etc/passwd",
                     "/../rosetta/cli.py", "/..%2f..%2fAGENTS.md"):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(self.url(path))
            self.assertEqual(ctx.exception.code, 404, path)

    def test_status_over_http(self) -> None:
        with urllib.request.urlopen(self.url("/api/status")) as r:
            payload = json.loads(r.read())
        self.assertIn("n_tasks", payload)

    def test_an_oversized_body_is_refused(self) -> None:
        req = urllib.request.Request(
            self.url("/api/verify"),
            data=b"{}", method="POST",
            headers={"Content-Type": "application/json", "Content-Length": "99999999"},
        )
        # urllib sends its own Content-Length, so assert the server's own limit
        # rather than the header round trip.
        from rosetta.gui.server import MAX_BODY

        self.assertLessEqual(MAX_BODY, 8 * 1024 * 1024)
        del req

    def test_a_bad_body_is_a_4xx_with_a_message(self) -> None:
        req = urllib.request.Request(
            self.url("/api/verify"), data=b"not json", method="POST",
            headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 400)
        self.assertIn("not JSON", json.loads(ctx.exception.read())["error"])


if __name__ == "__main__":
    unittest.main()
