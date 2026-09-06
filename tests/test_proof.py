"""Proof receipts expose execution provenance without changing core truth."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rosetta.proof import build_receipt, persist_receipt, trace_digest


class ProofReceiptTests(unittest.TestCase):
    def receipt(self, backend: str = "core") -> dict:
        return build_receipt(
            routine="AJETIU2",
            baseline_src="AJETIU2 ; baseline\n Q\n",
            candidate_src="AJETIU2 ; candidate\n Q\n",
            result={
                "equivalent": False,
                "n_cases": 4,
                "n_diverged": 3,
                "n_void": 0,
                "by_kind": {"output": 3},
                "refs_touched": ["stdout"],
            },
            backend=backend,
            cases_origin="caller-supplied",
            elapsed_ms=2858,
        )

    def test_live_receipt_names_yottadb_and_rollback(self) -> None:
        receipt = self.receipt()
        self.assertEqual(receipt["provenance"], "LIVE YottaDB")
        self.assertTrue(receipt["live"])
        self.assertTrue(receipt["isolation"]["restored"])
        self.assertEqual(receipt["n_diverged"], 3)
        self.assertEqual(len(receipt["receipt_sha256"]), 64)
        json.dumps(receipt)

    def test_non_core_backend_never_claims_live_or_restored(self) -> None:
        receipt = self.receipt("fake")
        self.assertFalse(receipt["live"])
        self.assertFalse(receipt["isolation"]["restored"])
        self.assertEqual(receipt["provenance"], "FAKE BACKEND")

    def test_receipt_is_content_addressed_and_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"ROSETTA_PROOF_DIR": tmp}
        ):
            receipt = self.receipt()
            first = Path(persist_receipt(receipt))
            before = first.read_bytes()
            second = Path(persist_receipt(receipt))
            self.assertEqual(first, second)
            self.assertEqual(second.read_bytes(), before)
            self.assertIn(receipt["receipt_sha256"][:12], first.name)

    def test_trace_digest_covers_exact_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.jsonl"
            path.write_bytes(b'{"kind":"verdict"}\n')
            first = trace_digest(path)
            path.write_bytes(b'{"kind":"verdict","changed":true}\n')
            self.assertNotEqual(first, trace_digest(path))


if __name__ == "__main__":
    unittest.main()
