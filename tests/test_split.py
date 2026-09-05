"""Tests for the train/eval split lock.

The lock exists to make "did you train on your eval?" answerable with a file.
These tests assert the properties that claim depends on.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from rosetta.bench import split as S


class TestUnits(unittest.TestCase):
    def setUp(self) -> None:
        self.candidates = json.loads(S.DEFAULT_CANDIDATES.read_text())
        self.units = S.build_units(self.candidates)

    def test_every_routine_lands_in_exactly_one_unit(self) -> None:
        names = [c["name"] for c in self.candidates["candidates"]]
        seen = [r for u in self.units for r in u.routines]
        self.assertEqual(sorted(seen), sorted(names))
        self.assertEqual(len(seen), len(set(seen)), "a routine appears in two units")

    def test_known_duplicate_pair_shares_a_unit(self) -> None:
        """IVMCME1/IVMUCHK1 are a real near-duplicate pair in the shortlist."""
        home = {r: u.key for u in self.units for r in u.routines}
        if "IVMCME1" in home and "IVMUCHK1" in home:
            self.assertEqual(home["IVMCME1"], home["IVMUCHK1"])


class TestPartition(unittest.TestCase):
    def setUp(self) -> None:
        self.candidates = json.loads(S.DEFAULT_CANDIDATES.read_text())
        self.units = S.build_units(self.candidates)
        self.split = S.partition(self.units)

    def test_no_cluster_straddles_the_split(self) -> None:
        """The property the whole amendment exists for."""
        train, ev = set(self.split.train), set(self.split.eval)
        for unit in self.units:
            if not unit.is_cluster:
                continue
            in_train = [r for r in unit.routines if r in train]
            in_eval = [r for r in unit.routines if r in ev]
            self.assertTrue(
                not (in_train and in_eval),
                f"cluster {unit.key} straddles the split: "
                f"train={in_train} eval={in_eval}",
            )

    def test_sides_are_disjoint_and_cover_everything(self) -> None:
        train, ev = set(self.split.train), set(self.split.eval)
        self.assertEqual(train & ev, set(), "a routine is on both sides")
        expected = {c["name"] for c in self.candidates["candidates"]}
        self.assertEqual(train | ev, expected)

    def test_eval_share_is_close_to_the_requested_fraction(self) -> None:
        total = len(self.split.train) + len(self.split.eval)
        share = len(self.split.eval) / total
        self.assertAlmostEqual(share, S.DEFAULT_EVAL_FRACTION, delta=0.02)

    def test_partition_is_deterministic(self) -> None:
        again = S.partition(self.units)
        self.assertEqual(self.split.train, again.train)
        self.assertEqual(self.split.eval, again.eval)

    def test_a_different_seed_gives_a_different_split(self) -> None:
        other = S.partition(self.units, seed=S.DEFAULT_SEED + 1)
        self.assertNotEqual(self.split.eval, other.eval)


class TestLockDocument(unittest.TestCase):
    def test_hash_covers_source_not_just_names(self) -> None:
        """A corpus edited underneath the lock must invalidate it."""
        split = S.Split(train=["A"], eval=["B"])
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "A.m").write_text("A ;\n Q 1\n")
            (d / "B.m").write_text("B ;\n Q 2\n")
            before = S.content_hash(split, d)
            (d / "A.m").write_text("A ;\n Q 999\n")
            self.assertNotEqual(before, S.content_hash(split, d))

    def test_write_refuses_to_clobber(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "split.lock.json"
            path.write_text("{}")
            with self.assertRaises(S.SplitLockExists):
                S.write_lock(path)

    def test_committed_lock_is_internally_consistent(self) -> None:
        """If the lock is committed, it must still describe the real corpus."""
        if not S.DEFAULT_LOCK.exists():
            self.skipTest("split lock not written yet")
        doc = S.load_lock()
        self.assertEqual(doc["schema"], S.SCHEMA)
        self.assertEqual(
            len(doc["train"]) + len(doc["eval"]), doc["counts"]["routines"]
        )
        self.assertEqual(set(doc["train"]) & set(doc["eval"]), set())
        for members in doc["clusters_held_together"]:
            sides = {("eval" if m in set(doc["eval"]) else "train") for m in members}
            self.assertEqual(len(sides), 1, f"cluster {members} straddles the lock")
        self.assertTrue(S.verify_lock(), "content hash no longer matches the corpus")


if __name__ == "__main__":
    unittest.main()
