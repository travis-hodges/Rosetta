import tomllib
import unittest
from pathlib import Path


class AgentProfileTests(unittest.TestCase):
    def test_project_agent_profiles_are_usable(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        profile_paths = sorted((repository_root / ".codex" / "agents").glob("*.toml"))
        required_profiles = {"explorer", "implementer", "verifier"}
        discovered_profiles = set()

        for profile_path in profile_paths:
            with self.subTest(profile=profile_path.name):
                with profile_path.open("rb") as profile_file:
                    profile = tomllib.load(profile_file)

                for field in ("name", "description", "developer_instructions"):
                    value = profile.get(field)
                    self.assertIsInstance(value, str, f"{profile_path} must define {field}")
                    self.assertTrue(value.strip(), f"{profile_path} must define a non-empty {field}")

                discovered_profiles.add(profile["name"])

        missing_profiles = required_profiles - discovered_profiles
        self.assertFalse(
            missing_profiles,
            f"Missing required agent profiles: {', '.join(sorted(missing_profiles))}",
        )


if __name__ == "__main__":
    unittest.main()
