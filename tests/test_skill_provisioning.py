from pathlib import Path
import tempfile
import unittest

from scopex.agent.skills import DEFAULT_BUILTIN_SKILLS, prepare_workspace_skills


class SkillProvisioningTests(unittest.TestCase):
    def test_default_product_skills_match_business_v1(self):
        self.assertEqual(
            DEFAULT_BUILTIN_SKILLS,
            (
                "system-health",
                "image-quality-diagnosis",
                "nipple-recognition-analysis",
                "encoder-health",
                "log-context",
            ),
        )
        self.assertNotIn("cow-disinfect-diagnosis", DEFAULT_BUILTIN_SKILLS)

    def test_builtin_skill_is_copied_into_workspace(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = root / "workspace"
            builtin = root / "builtin"
            workspace.mkdir()
            source = builtin / "image-quality-diagnosis"
            source.mkdir(parents=True)
            (source / "SKILL.md").write_text("image skill\n", encoding="utf-8")

            skills = prepare_workspace_skills(
                workspace=workspace,
                skill_names=("image-quality-diagnosis",),
                builtin_root=builtin,
            )

            self.assertEqual(skills, ("image-quality-diagnosis",))
            self.assertEqual(
                (workspace / "skills" / "image-quality-diagnosis" / "SKILL.md").read_text(),
                "image skill\n",
            )

    def test_builtin_refresh_replaces_stale_copy(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = root / "workspace"
            builtin = root / "builtin"
            workspace.mkdir()
            source = builtin / "s1"
            source.mkdir(parents=True)
            (source / "SKILL.md").write_text("new\n", encoding="utf-8")
            target = workspace / "skills" / "s1"
            target.mkdir(parents=True)
            (target / "SKILL.md").write_text("old\n", encoding="utf-8")
            (target / "removed.txt").write_text("stale\n", encoding="utf-8")

            prepare_workspace_skills(
                workspace=workspace,
                skill_names=("s1",),
                builtin_root=builtin,
            )

            self.assertEqual((target / "SKILL.md").read_text(), "new\n")
            self.assertFalse((target / "removed.txt").exists())

    def test_custom_workspace_skill_is_allowed_without_builtin_copy(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = root / "workspace"
            workspace.mkdir()
            custom = workspace / "skills" / "custom-skill"
            custom.mkdir(parents=True)
            (custom / "SKILL.md").write_text("custom\n", encoding="utf-8")

            skills = prepare_workspace_skills(
                workspace=workspace,
                skill_names=("custom-skill",),
                builtin_root=root / "missing-builtins",
            )

            self.assertEqual(skills, ("custom-skill",))
            self.assertEqual((custom / "SKILL.md").read_text(), "custom\n")

    def test_invalid_or_missing_skill_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = root / "workspace"
            workspace.mkdir()
            with self.assertRaises(ValueError):
                prepare_workspace_skills(
                    workspace=workspace,
                    skill_names=("../escape",),
                    builtin_root=root / "builtin",
                )
            with self.assertRaises(ValueError):
                prepare_workspace_skills(
                    workspace=workspace,
                    skill_names=("missing",),
                    builtin_root=root / "builtin",
                )


if __name__ == "__main__":
    unittest.main()
