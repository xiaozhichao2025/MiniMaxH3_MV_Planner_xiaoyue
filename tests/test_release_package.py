from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class _Routes:
    def __init__(self):
        self.paths = []

    def get(self, path):
        self.paths.append(("GET", path))
        return lambda handler: handler

    def post(self, path):
        self.paths.append(("POST", path))
        return lambda handler: handler


class ReleasePackageTests(unittest.TestCase):
    def test_release_version_and_readme_assets(self):
        package_init = (ROOT / "__init__.py").read_text("utf-8")
        self.assertIn('__version__ = "0.1.0"', package_init)
        changelog = (ROOT / "CHANGELOG.md").read_text("utf-8")
        self.assertIn("## v0.1.0", changelog)
        for readme in ("README.md", "README_EN.md"):
            body = (ROOT / readme).read_text("utf-8")
            self.assertIn("v0.1.0", body)
            for filename in (
                "01-project-setup.png",
                "02-mv-workbench.png",
                "03-preview-desk.png",
            ):
                self.assertIn(f"assets/screenshots/{filename}", body)
                path = ROOT / "assets" / "screenshots" / filename
                self.assertTrue(path.is_file())
                self.assertEqual(path.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")

    def test_public_readmes_document_example_dependencies_and_license(self):
        license_text = (ROOT / "LICENSE").read_text("utf-8")
        self.assertIn("MIT License", license_text)
        self.assertIn("Permission is hereby granted, free of charge", license_text)
        self.assertFalse((ROOT / "ACKNOWLEDGEMENTS.md").exists())
        workflow = ROOT / "workflows" / "MiniMaxH3_MV_Planner_xiaoyue_MV工作台v1.0.json"
        self.assertTrue(workflow.is_file())
        for readme in ("README.md", "README_EN.md"):
            with self.subTest(readme=readme):
                body = (ROOT / readme).read_text("utf-8")
                self.assertIn("reasoning_provider", body)
                self.assertIn(
                    "## 特别感谢" if readme == "README.md" else "## Acknowledgements",
                    body,
                )
                self.assertNotIn("ACKNOWLEDGEMENTS.md", body)
                for required in (
                    "LICENSE",
                    "workflows/MiniMaxH3_MV_Planner_xiaoyue_MV工作台v1.0.json",
                    "https://github.com/tl2012tl/comfyUI-llama-TE",
                    "https://github.com/T8mars/comfyui-minimax-h3-audio-T8",
                    "https://github.com/swan7-py/ComfyUI_Swan_Bits",
                    "https://github.com/kijai/ComfyUI-KJNodes",
                    "https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite",
                    "https://github.com/yolain/ComfyUI-Easy-Use",
                    "https://github.com/Suzie1/ComfyUI_Comfyroll_CustomNodes",
                    "https://github.com/zhangp365/ComfyUI-utils-nodes",
                    "https://github.com/M1kep/ComfyLiterals",
                    "https://github.com/Windecay/ComfyUI-ReservedVRAM",
                    "minimax_h3_hybrid_fl2va_ref2va_b25-49-int8.safetensors",
                    "minimax_h3_video_vae_int8_convrot.safetensors",
                    "Qwen3.8-27B-UD-IQ3_XXS.gguf",
                    "whisper_large_v3_fp16.safetensors",
                ):
                    self.assertIn(required, body)

    def test_public_runtime_has_no_private_asr_endpoint_or_config_hook(self):
        for relative_path in (
            "nodes.py", "asr_provider.py", "server_api.py",
            "web/mv_planner.js",
            "workflows/MiniMaxH3_MV_Planner_xiaoyue_MV工作台v1.0.json",
        ):
            with self.subTest(path=relative_path):
                source = (ROOT / relative_path).read_text("utf-8")
                self.assertNotIn("192.168.", source)
                self.assertNotIn("XIAOYUE_ASR_API_KEY", source)
                self.assertNotIn("local_config.json", source)

    def test_package_imports_and_registers_public_nodes_without_private_modules(self):
        # ComfyUI supplies these two modules at runtime; a stub lets a clean
        # checkout prove that its own relative imports are complete.
        fake_paths = types.ModuleType("folder_paths")
        fake_paths.models_dir = str(ROOT / "models")
        fake_paths.base_path = str(ROOT)
        fake_paths.get_input_directory = lambda: str(ROOT / "input")
        fake_paths.get_output_directory = lambda: str(ROOT / "output")
        fake_paths.get_temp_directory = lambda: str(ROOT / "temp")
        routes = _Routes()
        fake_server = types.ModuleType("server")
        fake_server.PromptServer = types.SimpleNamespace(
            instance=types.SimpleNamespace(routes=routes))
        package_name = "_xiaoyue_mv_release_probe"
        spec = importlib.util.spec_from_file_location(
            package_name, ROOT / "__init__.py",
            submodule_search_locations=[str(ROOT)])
        self.assertIsNotNone(spec)
        package = importlib.util.module_from_spec(spec)
        injected = {
            "folder_paths": fake_paths,
            "server": fake_server,
            package_name: package,
        }
        previous = {name: sys.modules.get(name) for name in injected}
        try:
            sys.modules.update(injected)
            spec.loader.exec_module(package)
            mappings = package.NODE_CLASS_MAPPINGS
            self.assertIn("MiniMaxH3_MV_Planner_xiaoyue", mappings)
            self.assertIn(
                "reasoning_provider",
                mappings["MiniMaxH3_MV_Planner_xiaoyue"].INPUT_TYPES()["optional"],
            )
            self.assertIn("Xiaoyue_Local_Whisper_Provider", mappings)
            self.assertNotIn("Xiaoyue_LAN_ASR_Provider", mappings)
            self.assertNotIn("MiniMaxH3_MV_Planner_Setup_xiaoyue", mappings)
            self.assertIn(("POST", "/xiaoyue-mv/generation/start"), routes.paths)
            self.assertIn(("POST", "/xiaoyue-mv/delivery/export"), routes.paths)
        finally:
            for name in list(sys.modules):
                if name == package_name or name.startswith(package_name + "."):
                    sys.modules.pop(name, None)
            for name, module in previous.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module


if __name__ == "__main__":
    unittest.main()
