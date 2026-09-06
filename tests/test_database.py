"""Database change planning is testable without a YottaDB installation."""

from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from rosetta.database import (
    DatabaseChangeError,
    Mutation,
    apply_plan,
    capture_plan,
    preview_plan,
    rollback_receipt,
    validate_plan,
    validate_ref,
)


class FakeRuntime:
    def __init__(self, state=None) -> None:
        self.config = SimpleNamespace(container="rosetta-verify", instance="vehu")
        self.state = dict(state or {})
        self.snapshots: dict[str, dict[str, str]] = {}
        self.restored: list[str] = []
        self.fail_apply = False

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return None

    def read_globals(self, refs):
        return {ref: (ref in self.state, self.state.get(ref, "")) for ref in refs}

    def snapshot(self):
        snap = f"snap-{len(self.snapshots) + 1}"
        self.snapshots[snap] = deepcopy(self.state)
        return snap

    def restore(self, snap_id):
        self.state = deepcopy(self.snapshots[snap_id])
        self.restored.append(snap_id)

    def apply_global_changes(self, changes):
        if self.fail_apply:
            raise RuntimeError("simulated write failure")
        for change in changes:
            ref = change["ref"]
            present = ref in self.state
            if present != (change["before_present"] == "1"):
                raise RuntimeError("conflict")
            if present and self.state[ref] != change["before_value"]:
                raise RuntimeError("conflict")
        for change in changes:
            if change["op"] == "set":
                self.state[change["ref"]] = change["value"]
            else:
                self.state.pop(change["ref"], None)
        return self.read_globals([change["ref"] for change in changes])


class ReferenceTests(unittest.TestCase):
    def test_accepts_literal_leaf_references(self):
        self.assertEqual(validate_ref('^dpt(3,0)'), '^DPT(3,0)')
        self.assertEqual(validate_ref('^VA(4,"B","WASHINGTON",1)'),
                         '^VA(4,"B","WASHINGTON",1)')

    def test_refuses_roots_dynamic_code_and_internal_globals(self):
        for value in ('^DPT', '^DPT(X,0)', '^DPT($O(^DPT(1)),0)',
                      '^DPT(1) S ^OWNED(1)=1', '^ROSLOG(1)'):
            with self.subTest(value=value), self.assertRaises(DatabaseChangeError):
                validate_ref(value)


class PlanTests(unittest.TestCase):
    def test_plan_pins_before_state_and_digest(self):
        runtime = FakeRuntime({'^VA(4,10,0)': 'OLD'})
        plan = capture_plan(
            "Rename the facility",
            [Mutation("set", '^VA(4,10,0)', 'NEW'), Mutation("set", '^VA(4,11,0)', 'ADDED')],
            runtime,
        )
        self.assertEqual(plan["operations"][0]["before"]["value"], "OLD")
        self.assertFalse(plan["operations"][1]["before"]["present"])
        self.assertEqual(validate_plan(plan)["change_id"], plan["change_id"])

        edited = json.loads(json.dumps(plan))
        edited["operations"][0]["after"]["value"] = "TAMPERED"
        with self.assertRaisesRegex(DatabaseChangeError, "digest"):
            validate_plan(edited)

    def test_preview_blocks_when_database_changed_since_capture(self):
        runtime = FakeRuntime({'^VA(4,10,0)': 'OLD'})
        plan = capture_plan("Rename", [Mutation("set", '^VA(4,10,0)', 'NEW')], runtime)
        runtime.state['^VA(4,10,0)'] = 'SOMEONE ELSE'
        preview = preview_plan(plan, runtime)
        self.assertFalse(preview["ready"])
        self.assertFalse(preview["operations"][0]["precondition_matches"])

    def test_apply_is_persistent_verified_and_rollbackable(self):
        runtime = FakeRuntime({'^VA(4,10,0)': 'OLD', '^VA(4,12,0)': 'REMOVE'})
        plan = capture_plan(
            "Update facility records",
            [Mutation("set", '^VA(4,10,0)', 'NEW'), Mutation("kill", '^VA(4,12,0)')],
            runtime,
        )
        receipt = apply_plan(plan, runtime)
        self.assertEqual(runtime.state['^VA(4,10,0)'], 'NEW')
        self.assertNotIn('^VA(4,12,0)', runtime.state)
        self.assertTrue(receipt["persistent"])
        self.assertTrue(receipt["verified"])

        rollback = rollback_receipt(receipt, runtime)
        self.assertEqual(runtime.state['^VA(4,10,0)'], 'OLD')
        self.assertEqual(runtime.state['^VA(4,12,0)'], 'REMOVE')
        self.assertTrue(rollback["verified"])

    def test_failed_apply_commits_nothing(self):
        runtime = FakeRuntime({'^VA(4,10,0)': 'OLD'})
        plan = capture_plan("Rename", [Mutation("set", '^VA(4,10,0)', 'NEW')], runtime)
        runtime.fail_apply = True
        with self.assertRaisesRegex(DatabaseChangeError, "no mutation committed"):
            apply_plan(plan, runtime)
        self.assertEqual(runtime.state['^VA(4,10,0)'], 'OLD')
        self.assertEqual(runtime.restored, [])


if __name__ == "__main__":
    unittest.main()
