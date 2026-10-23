from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "xiaoyue_skill_library", ROOT / "skill_library.py")
library = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = library
SPEC.loader.exec_module(library)


class SkillLibraryTests(unittest.TestCase):
    def test_plugin_directory_refresh_selection_and_actual_body(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            skill_root = root / "MiniMaxH3_MV_Planner_xiaoyue" / "skills"
            skill_root.mkdir(parents=True)
            te_root = root / "comfyUI-llama-TE" / "skills"
            te_root.mkdir(parents=True)
            custom = skill_root / "my-outline"
            custom.mkdir()
            (custom / "SKILL.md").write_text(
                "---\nname: English Outline\n---\nEnglish body", encoding="utf-8")
            (custom / "SKILL.cn.md").write_text(
                "---\nname: 中文总纲\n---\n自定义中文正文", encoding="utf-8")
            (custom / "references").mkdir()
            (custom / "references" / "notes.md").write_text(
                "not a separate skill", encoding="utf-8")
            (skill_root / "loose.md").write_text("独立文件正文", encoding="utf-8")
            (te_root / "te-only.md").write_text("不应读取", encoding="utf-8")
            for stage, default_id in library.SKILL_STAGE_DEFAULTS.items():
                packaged = skill_root / default_id
                packaged.mkdir()
                (packaged / "SKILL.cn.md").write_text(
                    f"插件默认 {stage}", encoding="utf-8")
                duplicate = te_root / default_id
                duplicate.mkdir()
                (duplicate / "SKILL.cn.md").write_text("旧 TE 同名副本", encoding="utf-8")

            with patch.object(library, "SKILL_ROOT", skill_root):
                self.assertEqual(library.skill_template_folder(), str(skill_root.resolve()))
                for stage, default_id in library.SKILL_STAGE_DEFAULTS.items():
                    descriptor = library.skill_template_descriptor(stage, default_id)
                    self.assertEqual(descriptor["source"], "bundled")
                    self.assertTrue(descriptor["embedded"])
                    self.assertEqual(
                        library.load_skill_body(stage, default_id),
                        f"插件默认 {stage}",
                    )
                for stage in library.SKILL_STAGE_DEFAULTS:
                    choices = library.skill_template_library()[stage]
                    ids = {item["id"] for item in choices}
                    self.assertIn("my-outline", ids)
                    self.assertIn("loose.md", ids)
                    self.assertNotIn("te-only.md", ids)
                    self.assertNotIn("my-outline/references/notes.md", ids)
                    self.assertNotIn("my-outline/SKILL.md", ids)
                    self.assertNotIn("my-outline/SKILL.cn.md", ids)
                custom_descriptor = library.skill_template_descriptor(
                    "visual_outline", "my-outline")
                self.assertEqual(custom_descriptor["source"], "local")
                self.assertFalse(custom_descriptor["embedded"])
                selected = library.normalize_skill_templates({
                    "visual_outline": "my-outline", "h3_prompt": "loose.md",
                })
                self.assertEqual(selected["visual_outline"], "my-outline")
                self.assertEqual(
                    library.load_skill_body("visual_outline", "my-outline"),
                    "自定义中文正文",
                )
                self.assertEqual(
                    library.load_skill_body("h3_prompt", "loose.md"),
                    "独立文件正文",
                )

                (skill_root / "added.txt").write_text("新加入正文", encoding="utf-8")
                refreshed = library.skill_template_library()["h3_prompt"]
                self.assertIn("added.txt", {item["id"] for item in refreshed})
                self.assertEqual(
                    library.load_skill_body("h3_prompt", "added.txt"),
                    "新加入正文",
                )
                (skill_root / "added.txt").unlink()
                with self.assertRaisesRegex(ValueError, "已不存在"):
                    library.normalize_skill_templates({"h3_prompt": "added.txt"})

    def test_real_packaged_defaults_exist(self):
        self.assertEqual(library.skill_template_folder(), str(library.SKILL_ROOT.resolve()))
        choices = library.skill_template_library()
        for stage, default in library.SKILL_STAGE_DEFAULTS.items():
            self.assertIn(default, {item["id"] for item in choices[stage]})
            self.assertEqual(library.skill_template_descriptor(stage)["source"], "bundled")
