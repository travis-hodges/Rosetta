"""The FileMan proof-of-concept accepts only simple top-level records."""

from __future__ import annotations

import unittest

from rosetta.fileman import FileManError, apply_record


class FakeRuntime:
    def __init__(self):
        self.calls = []

    def apply_fileman_record(self, file_number, iens, fields):
        self.calls.append((file_number, iens, fields))
        return {"file": file_number, "ien": "42", "fields": fields, "persistent": True}


class FileManTests(unittest.TestCase):
    def test_create_uses_adding_iens_and_requires_name(self):
        runtime = FakeRuntime()
        result = apply_record("4", {".01": "ROSETTA TEST FACILITY"}, runtime)
        self.assertEqual(result["ien"], "42")
        self.assertEqual(runtime.calls, [("4", "+1,", {".01": "ROSETTA TEST FACILITY"})])

    def test_update_uses_existing_iens(self):
        runtime = FakeRuntime()
        apply_record("4", {"99": "ACTIVE"}, runtime, ien="42")
        self.assertEqual(runtime.calls[0][1], "42,")

    def test_dynamic_identifiers_are_refused(self):
        for file_number, fields, ien in (
            ("4 S ^BAD=1", {".01": "X"}, None),
            ("4", {"X": "Y"}, None),
            ("4", {".01": "X"}, "1,2"),
        ):
            with self.subTest(file=file_number, ien=ien), self.assertRaises(FileManError):
                apply_record(file_number, fields, FakeRuntime(), ien=ien)

    def test_create_without_point_zero_one_is_refused(self):
        with self.assertRaisesRegex(FileManError, "field .01"):
            apply_record("4", {"99": "X"}, FakeRuntime())


if __name__ == "__main__":
    unittest.main()
