from __future__ import annotations

import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "xiaoyue_visual_test_package"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules.setdefault(PACKAGE, package)


def load_module(name: str):
    spec = importlib.util.spec_from_file_location(
        f"{PACKAGE}.{name}", ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


load_module("creative_outline")
visual = load_module("visual_assets")


class VisualAssetTests(unittest.TestCase):
    def test_loads_selected_images_in_declared_order(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "assets").mkdir()
            Image.new("RGB", (40, 20), (255, 0, 0)).save(root / "assets" / "lead.png")
            Image.new("RGB", (20, 30), (0, 255, 0)).save(root / "prop.png")
            plan = {"assets": [{
                "name": "女主", "outline_visual": True,
                "source": {"filename": "lead.png", "subfolder": "assets"},
            }, {
                "name": "跳过", "outline_visual": False,
                "source": {"filename": "skip.png"},
            }, {
                "name": "道具", "outline_visual": True,
                "source": {"filename": "prop.png"},
            }]}
            images, metadata = visual.load_outline_visual_inputs(plan, root)
            self.assertEqual(len(images), 2)
            self.assertEqual(tuple(images[0].shape), (1, 20, 40, 3))
            self.assertEqual([item["name"] for item in metadata], ["女主", "道具"])
            self.assertEqual([item["label"] for item in metadata], ["视觉资产 A", "视觉资产 B"])

    def test_missing_selected_asset_fails_before_inference(self):
        with tempfile.TemporaryDirectory() as temporary:
            plan = {"assets": [{
                "name": "丢失的图", "outline_visual": True,
                "source": {"filename": "missing.png"},
            }]}
            with self.assertRaisesRegex(ValueError, "未启动推理"):
                visual.load_outline_visual_inputs(plan, temporary)

    def test_shot_assets_load_in_binding_order_not_project_order(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            Image.new("RGB", (30, 20), (255, 0, 0)).save(root / "first.png")
            Image.new("RGB", (20, 30), (0, 255, 0)).save(root / "second.png")
            plan = {
                "assets": [{
                    "id": "asset-a", "name": "项目第一项",
                    "source": {"filename": "first.png", "media_type": "image"},
                }, {
                    "id": "asset-b", "name": "镜头参考一",
                    "source": {"filename": "second.png", "media_type": "image"},
                }],
                "shots": [{
                    "id": "shot_001",
                    "asset_bindings": [
                        {"asset_id": "asset-b", "order": 1},
                        {"asset_id": "asset-a", "order": 2},
                    ],
                }],
            }
            images, metadata = visual.load_shot_visual_inputs(
                plan, "shot_001", root)
            self.assertEqual(len(images), 2)
            self.assertEqual(
                [item["name"] for item in metadata],
                ["镜头参考一", "项目第一项"],
            )
            self.assertEqual(
                [item["label"] for item in metadata],
                ["本镜头资产 1", "本镜头资产 2"],
            )


if __name__ == "__main__":
    unittest.main()
