from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT.parents[1] / "docs" / "MiniMaxH3_MV_项目工作台_xiaoyue.json"
CURRENT_TEMPLATE = ROOT / "workflows" / "MiniMaxH3_MV_Planner_xiaoyue_MV工作台v1.0.json"


class UnifiedWorkbenchTests(unittest.TestCase):
    def test_template_is_one_workbench_with_valid_external_wires(self):
        workflow = json.loads(CURRENT_TEMPLATE.read_text("utf-8"))
        nodes = {node["id"]: node for node in workflow["nodes"]}
        main = nodes[1002]
        self.assertEqual(main["properties"]["xiaoyue_mv_backbone_id"], 130)
        self.assertEqual(len(main["outputs"]), 27)
        self.assertEqual(main["widgets_values_named"]["seed"], -1)
        self.assertFalse({2144, 2152} & nodes.keys())
        links = {link[0]: link for link in workflow["links"]}
        for link in links.values():
            source, output, target, input_index = link[1:5]
            self.assertIn(source, nodes)
            self.assertIn(target, nodes)
            self.assertLess(output, len(nodes[source]["outputs"]))
            self.assertLess(input_index, len(nodes[target]["inputs"]))
            self.assertEqual(nodes[target]["inputs"][input_index]["link"], link[0])
            self.assertIn(link[0], nodes[source]["outputs"][output]["links"])
        self.assertFalse({1004, 1005, 1006, 1069} & links.keys())

    def test_source_separation_is_external_to_workbench(self):
        workflow = json.loads(CURRENT_TEMPLATE.read_text("utf-8"))
        self.assertFalse(any(node["type"] == "MiniMaxH3_MV_Planner_Setup_xiaoyue"
                             for node in workflow["nodes"]))
        self.assertNotIn("HDEMUCS", json.dumps(workflow, ensure_ascii=False))

    def test_release_workflow_has_no_private_asr_adapter(self):
        workflow = json.loads(CURRENT_TEMPLATE.read_text("utf-8"))
        types = {node["type"] for node in workflow["nodes"]}
        self.assertNotIn("Xiaoyue_LAN_ASR_Provider", types)
        self.assertIn("Xiaoyue_Local_Whisper_Provider", types)
        descriptions = [str(node.get("widgets_values_named", {}).get("text") or "")
                        for node in workflow["nodes"] if node["type"] == "MarkdownNote"]
        self.assertEqual(sum("## 样例工作流的扩展节点" in text
                             for text in descriptions), 1)
        help_text = next(text for text in descriptions
                         if "## 样例工作流的扩展节点" in text)
        self.assertIn("## MV 工作台的两个 Skill", help_text)
        self.assertIn(
            "custom_nodes/MiniMaxH3_MV_Planner_xiaoyue/skills/"
            "xiaoyue-mv-visual-outline/SKILL.cn.md", help_text)
        self.assertIn(
            "custom_nodes/MiniMaxH3_MV_Planner_xiaoyue/skills/"
            "xiaoyue-h3-mv-prompt/SKILL.cn.md", help_text)
        self.assertIn("用户自定义 Skill 也放在上述 MV 插件的 `skills/` 目录", help_text)
        self.assertNotIn("custom_nodes/comfyUI-llama-TE/skills/", help_text)
        self.assertIn("QwenTE 只负责推理，不扫描或决定工作台的 Skill 目录", help_text)
        self.assertIn("## 特别感谢", help_text)
        self.assertIn("https://space.bilibili.com/515231056", help_text)
        self.assertIn("https://github.com/T8mars?tab=repositories", help_text)
        self.assertIn("https://github.com/swan7-py/ComfyUI_Swan_Bits", help_text)
        self.assertIn("## 本地 ASR／Whisper（可选）", help_text)
        self.assertIn("https://github.com/yolain/ComfyUI-Easy-Media", help_text)
        self.assertIn(
            "https://huggingface.co/Comfy-Org/HuMo_ComfyUI/resolve/main/"
            "split_files/audio_encoders/whisper_large_v3_fp16.safetensors",
            help_text,
        )
        self.assertIn(
            "ComfyUI/models/audio_encoders/whisper_large_v3_fp16.safetensors",
            help_text,
        )
        self.assertLess(help_text.index("## 本地 ASR／Whisper（可选）"),
                        help_text.index("## 样例选用的模型"))
        self.assertNotIn("HDEMUCS", help_text)

    def test_current_template_has_no_external_prompt_entry(self):
        workflow = json.loads(CURRENT_TEMPLATE.read_text("utf-8"))
        self.assertFalse(any(node["id"] == 2 for node in workflow["nodes"]))
        self.assertFalse(any(node["type"] in ("Xiaoyue_MV_ShotInput",
                                               "Xiaoyue_MV_PreviewDesk")
                             for node in workflow["nodes"]))
        shot_input = next(node for node in workflow["nodes"]
                          if node["type"] == "MiniMaxH3_MV_Planner_xiaoyue")
        self.assertEqual(
            [item["name"] for item in shot_input["outputs"][23:]],
            ["shot_audio_input_name", "input_subfolder",
             "output_subfolder", "output_filename_stem"])
        self.assertNotIn("manual_prompt", [entry["name"] for entry in shot_input["inputs"]])
        self.assertNotIn("manual_prompt", shot_input.get("widgets_values_named", {}))
        self.assertNotIn("shot_id", [entry["name"] for entry in shot_input["inputs"]])
        self.assertNotIn("shot_id", shot_input.get("widgets_values_named", {}))
        h3_nodes = [node for node in workflow["nodes"]
                    if node["type"] == "MiniMaxH3ReferenceToVideo"]
        self.assertEqual(len(h3_nodes), 2)
        first_prompt = next(entry["link"] for entry in h3_nodes[0]["inputs"]
                            if entry["name"] == "prompt")
        first_link = next(link for link in workflow["links"] if link[0] == first_prompt)
        self.assertEqual(first_link[1:3], [shot_input["id"], 8])
        enhancer = next(node for node in workflow["nodes"]
                        if node["type"] == "CR Prompt Text")
        concat = next(node for node in workflow["nodes"]
                      if node["type"] == "ConcatTextOfUtils")
        enhanced_prompt = next(entry["link"] for entry in h3_nodes[1]["inputs"]
                               if entry["name"] == "prompt")
        enhanced_link = next(link for link in workflow["links"]
                             if link[0] == enhanced_prompt)
        self.assertEqual(enhanced_link[1], concat["id"])
        text_inputs = {entry["name"]: entry["link"] for entry in concat["inputs"]}
        self.assertEqual(next(link for link in workflow["links"]
                              if link[0] == text_inputs["text1"])[1], enhancer["id"])
        self.assertEqual(next(link for link in workflow["links"]
                              if link[0] == text_inputs["text2"])[1:3],
                         [shot_input["id"], 8])
        continuation = next(node for node in workflow["nodes"]
                            if node["type"] == "VHS_LoadVideoPath"
                            and node["properties"].get("xiaoyue_mv_backbone_id") == 132)
        connections = {entry["name"]: entry["link"] for entry in continuation["inputs"]}
        for output_name, input_name in (
            ("continuation_video_path", "video"),
            ("continuation_frame_count", "frame_load_cap"),
            ("continuation_start_frame", "skip_first_frames"),
        ):
            output_index = next(index for index, output in enumerate(shot_input["outputs"])
                                if output["name"] == output_name)
            link = next(link for link in workflow["links"]
                        if link[0] == connections[input_name])
            self.assertEqual(link[1:5], [shot_input["id"], output_index,
                                         continuation["id"], next(
                                             index for index, entry in enumerate(continuation["inputs"])
                                             if entry["name"] == input_name)])
        source = (ROOT / "nodes.py").read_text("utf-8")
        shot_definition = source.split("class Xiaoyue_MV_ShotInput:", 1)[1].split(
            "class Xiaoyue_MV_RuntimeShot:", 1)[0]
        self.assertNotIn("manual_prompt", shot_definition)
        self.assertNotIn('"shot_id":', shot_definition.split("RETURN_TYPES", 1)[0])
        self.assertIn('shot.get("prompt")', shot_definition)

    def test_shot_output_tooltips_cover_every_port(self):
        source = (ROOT / "nodes.py").read_text("utf-8")
        module = ast.parse(source)
        attributes = {target.id: ast.literal_eval(statement.value)
                      for statement in module.body if isinstance(statement, ast.Assign)
                      for target in statement.targets if isinstance(target, ast.Name)
                      and target.id in {"SHOT_OUTPUT_NAMES", "SHOT_OUTPUT_TOOLTIPS"}}
        self.assertEqual(len(attributes["SHOT_OUTPUT_NAMES"]),
                         len(attributes["SHOT_OUTPUT_TOOLTIPS"]))
        self.assertTrue(all(attributes["SHOT_OUTPUT_TOOLTIPS"]))
        planner = next(node for node in module.body if isinstance(node, ast.ClassDef)
                       and node.name == "MiniMaxH3_MV_Planner_xiaoyue")
        planner_tooltips = next(statement.value.left for statement in planner.body
                                if isinstance(statement, ast.Assign)
                                and any(isinstance(target, ast.Name)
                                        and target.id == "OUTPUT_TOOLTIPS"
                                        for target in statement.targets))
        self.assertEqual(len(ast.literal_eval(planner_tooltips)), 8)
        frontend = (ROOT / "web" / "mv_planner.js").read_text("utf-8")
        self.assertIn('if (removeShotId) obsolete.add("shot_id")', frontend)
        self.assertIn("installShotOutputLabelTooltips()", frontend)
        self.assertIn("outputTooltipsByNodeType.get(node.type)?.[index]", frontend)
        self.assertNotIn(".slice(PLANNER_OUTPUT_COUNT);", frontend)

    def test_old_project_entry_is_not_registered(self):
        source = (ROOT / "nodes.py").read_text("utf-8")
        mapping = source.split("NODE_CLASS_MAPPINGS =", 1)[1]
        self.assertNotIn('"MiniMaxH3_MV_Project_xiaoyue":', mapping)
        self.assertIn(
            '"MiniMaxH3_MV_Planner_xiaoyue": MiniMaxH3_MV_Planner_xiaoyue',
            mapping,
        )

    def test_main_node_hides_all_project_state_widgets(self):
        source = (ROOT / "web" / "mv_planner.js").read_text("utf-8")
        for field in (
            "project_name",
            "project_code",
            "project_id",
            "project_state_json",
        ):
            self.assertIn(f'hideInternalWidget(nodeWidget(this, "{field}"))', source)
        self.assertIn("hideInternalWidget(planWidget(this))", source)
        self.assertIn('"① 项目设定"', source)
        self.assertIn('"② MV 工作台 · 请先运行一次"', source)
        self.assertIn('widgets.find((widget) => widget.name === "③ 剪辑台")', source)
        self.assertIn('orderWorkbenchButtons(this)', source)
        preview = (ROOT / "web" / "mv_preview.js").read_text("utf-8")
        self.assertIn('? "③ 剪辑台" : "最终验收剪辑台"', preview)

    def test_shot_generation_compiles_pending_plan_without_closing_editor(self):
        source = (ROOT / "web" / "mv_planner.js").read_text("utf-8")
        compile_method = source.split("async compilePlanForGeneration() {", 1)[1].split(
            "async startShotGeneration(", 1)[0]
        generation_method = source.split("async startShotGeneration(", 1)[1].split(
            "async pollShotGeneration(", 1)[0]
        self.assertIn("if (!await this.save(false))", compile_method)
        self.assertIn("app.queuePrompt(0, 1, [String(node.id)])", compile_method)
        self.assertIn('api.addEventListener("execution_error"', compile_method)
        self.assertIn("await this.compilePlanForGeneration()", generation_method)
        self.assertIn("selected.uid", generation_method)
        self.assertNotIn("先保存并运行 MV 工作台节点", generation_method)
        self.assertIn("compileWaiter.finish(null, outputPlan, restoredPayload)", source)
        self.assertIn('镜头最终字幕已修改；保存计划后由后续提示词与生成链路读取。", false', source)
        self.assertIn('镜头前后扩歌词已修改；保存后由提示词链路读取。", false', source)

    def test_old_workflow_inputs_are_migrated_to_tool_only_surface(self):
        source = (ROOT / "web" / "mv_planner.js").read_text("utf-8")
        self.assertIn("nodeType.prototype.onConfigure = function (info)", source)
        self.assertIn("migrateLegacyPlannerNode(this, info)", source)
        for field in ("project", "audio", "vocals_audio", "subtitle_text"):
            self.assertIn(f'    "{field}",', source)
        self.assertIn("node.removeInput(index)", source)
        self.assertIn("LEGACY_PROJECT_NODE_NAME", source)
        self.assertIn("isPlainLoadAudioNode", source)
        self.assertIn("!nodeHasOutgoingLinks(graph, candidate)", source)

    def test_clean_workflow_has_one_main_node_and_only_provider_wires(self):
        workflow = json.loads(CURRENT_TEMPLATE.read_text("utf-8"))
        node_types = [node["type"] for node in workflow["nodes"]]
        self.assertEqual(node_types.count("MiniMaxH3_MV_Planner_xiaoyue"), 1)
        self.assertNotIn("MiniMaxH3_MV_Project_xiaoyue", node_types)
        self.assertFalse(any("LoadAudio" in node_type for node_type in node_types))

        planner = next(
            node for node in workflow["nodes"]
            if node["type"] == "MiniMaxH3_MV_Planner_xiaoyue"
        )
        inputs = {item["name"]: item for item in planner["inputs"]}
        self.assertEqual(
            {name for name, item in inputs.items() if item.get("link") is not None},
            {"asr_provider", "reasoning_provider"},
        )
        self.assertEqual(
            set(inputs) - {"asr_provider", "reasoning_provider"},
            {"project_name", "project_code", "project_id",
             "project_state_json", "plan_json", "seed"},
        )
        named = planner["widgets_values_named"]
        self.assertEqual(
            {key for key in named if named[key] is not None},
            {"project_name", "project_code", "project_id",
             "project_state_json", "plan_json", "seed"},
        )
        self.assertTrue(all(named[key] == "" for key in (
            "project_name", "project_code", "project_id",
            "project_state_json", "plan_json")))
        self.assertEqual(named["seed"], -1)

    def test_editor_tabs_follow_subtitle_outline_prompt_generation_work_order(self):
        source = (ROOT / "web" / "mv_planner.js").read_text("utf-8")
        self.assertIn('this.detailKeys = ["subtitle", "outline", "prompt", "generation"]', source)
        subtitle = source.index('data-detail-target="subtitle"')
        outline = source.index('data-detail-target="outline"')
        prompt = source.index('data-detail-target="prompt"')
        generation = source.index('data-detail-target="generation"')
        self.assertLess(subtitle, outline)
        self.assertLess(outline, prompt)
        self.assertLess(prompt, generation)
        self.assertNotIn('data-detail-target="asset"', source)
        self.assertIn("项目总规划", source)
        self.assertIn('class="shot-assets-stage"', source)

    def test_shot_lyric_context_joins_all_three_editable_fields(self):
        source = (ROOT / "web" / "mv_planner.js").read_text("utf-8")
        self.assertIn(
            "ai_dialogue_text: joinSubtitleContextParts([\n"
            "                leadingText, mainText, trailingText]),",
            source,
        )

    def test_batch_picker_prefers_unfinished_shots_and_recovers_stale_job(self):
        source = (ROOT / "web" / "mv_planner.js").read_text("utf-8")
        self.assertIn(
            "checkbox.checked = !checkbox.disabled && successfulCount === 0;",
            source,
        )
        self.assertIn("this.generationJobs = Array.isArray(payload.jobs)", source)
        self.assertIn("this.pollShotGenerations();", source)
        self.assertIn("this.refreshShotGenerations();", source)
        self.assertIn('summary: "镜头生成未启动"', source)

    def test_single_shot_generation_uses_one_button_and_scoped_busy_state(self):
        source = (ROOT / "web" / "mv_planner.js").read_text("utf-8")
        self.assertIn('data-action="generate-shot-current">生成</button>', source)
        self.assertNotIn('data-action="regenerate-shot-current"', source)
        self.assertIn("this.generationConflict([String(shot.id)])", source)
        self.assertIn("this.h3PromptRunning || this.creativeOutlineRunning", source)
        self.assertIn("activeJobs.length > 0", source)

    def test_replan_parameters_are_locked_at_entry_and_editable_in_confirmation_dialog(self):
        source = (ROOT / "web" / "mv_planner.js").read_text("utf-8")
        self.assertIn('this.field("target-shot-seconds").disabled = saved', source)
        self.assertIn('data-field="replan-target-shot-seconds"', source)
        self.assertIn('data-field="replan-h3-max-frames"', source)
        self.assertIn("应用参数并重新打点", source)
        self.assertIn("镜头资产绑定", source)
        self.assertIn("影片画幅与双采 MP", source)

    def test_shot_asset_picker_methods_belong_to_planner_editor(self):
        source = (ROOT / "web" / "mv_planner.js").read_text("utf-8")
        project_editor = source.split("class ProjectEditor {", 1)[1].split(
            "class PlannerEditor {", 1
        )[0]
        planner_editor = source.split("class PlannerEditor {", 1)[1]
        for method in (
            "openShotAssetPicker()",
            "closeShotAssetPicker()",
            "renderShotAssetPicker()",
            "updateShotAssetPickerStatus(visibleCount = null)",
            "confirmShotAssetSelection()",
        ):
            self.assertNotIn(method, project_editor)
            self.assertIn(method, planner_editor)
        self.assertIn("this.selectedShotAssetIds.add", planner_editor)
        self.assertIn("this.selectedShotAssetIds.delete", planner_editor)
        self.assertIn("data-shot-picker-card-id", planner_editor)
        self.assertIn("card.querySelector(\"[data-shot-picker-asset-id]\")?.click()", planner_editor)
        self.assertIn("this.selectedShotAssetIds.size === 0", planner_editor)

    def test_shot_asset_usage_can_spread_without_reordering_existing_slots(self):
        source = (ROOT / "web" / "mv_planner.js").read_text("utf-8")
        planner_editor = source.split("class PlannerEditor {", 1)[1]
        spread_method = planner_editor.split(
            "spreadShotAssetToAllShots(assetId) {", 1
        )[1].split("\n    removeProjectAsset(assetId)", 1)[0]
        self.assertIn('["spread-all", "铺到全片"', planner_editor)
        self.assertIn("shot.asset_bindings.findIndex", spread_method)
        self.assertIn("shot.asset_bindings[existingIndex] = {", spread_method)
        self.assertIn("...shot.asset_bindings[existingIndex]", spread_method)
        self.assertIn("shot.asset_bindings.push({ asset_id: assetId, usage })", spread_method)
        self.assertIn("binding.order = index + 1", spread_method)
        self.assertIn("保留原参考槽位并替换用法", spread_method)
        self.assertIn("追加到该镜头参考顺序末位", spread_method)
        self.assertIn('querySelector(\'[data-shot-asset-action="spread-all"]\')', planner_editor)
        self.assertIn("spreadButton.disabled = !hasUsage", planner_editor)

    def test_asset_preview_fits_then_supports_zoom_and_pan(self):
        source = (ROOT / "web" / "mv_planner.js").read_text("utf-8")
        styles = (ROOT / "web" / "mv_planner.css").read_text("utf-8")
        for action in (
            "asset-preview-zoom-out",
            "asset-preview-zoom-in",
            "asset-preview-fit",
        ):
            self.assertIn(f'data-action="{action}"', source)
        self.assertIn("fitAssetPreviewImage()", source)
        self.assertIn("Math.min(\n            inner.width / image.naturalWidth", source)
        self.assertIn("setAssetPreviewZoom(nextZoom", source)
        self.assertIn('addEventListener("wheel"', source)
        self.assertIn("{ passive: false }", source)
        self.assertIn("setPointerCapture(event.pointerId)", source)
        self.assertIn('addEventListener("pointermove"', source)
        self.assertIn('event.key === "0"', source)
        self.assertIn("translate3d(", source)
        self.assertIn("touch-action: none", styles)
        self.assertIn("cursor: grab", styles)
        self.assertIn("object-fit: contain", styles)

    def test_prompt_workspace_keeps_text_and_assets_compact_without_duplicate_guidance(self):
        source = (ROOT / "web" / "mv_planner.js").read_text("utf-8")
        styles = (ROOT / "web" / "mv_planner.css").read_text("utf-8")
        self.assertNotIn('class="xiaoyue-mv-prompt-context"', source)
        self.assertNotIn('data-field="creative-asset-needs"', source)
        self.assertNotIn('class="xiaoyue-mv-legacy-material"', source)
        self.assertNotIn('rule.className = "xiaoyue-mv-binding-rule"', source)
        self.assertNotIn("管理总资产库", source)
        self.assertNotIn("当前镜头资产调用与顺序", source)
        self.assertIn('data-field="creative-outline" rows="4"', source)
        self.assertIn('data-field="prompt" rows="4"', source)
        self.assertIn(
            '<span class="xiaoyue-mv-visually-hidden">当前镜头完整分镜描述</span>',
            source,
        )
        self.assertIn(
            '<span class="xiaoyue-mv-visually-hidden">本镜头 H3 提示词正文</span>',
            source,
        )
        self.assertIn('data-action="open-shot-asset-picker"', source)
        self.assertIn('data-action="upload-shot-assets"', source)
        self.assertIn('if (!summary) return;', source)
        self.assertIn('if (!container) return;', source)
        self.assertIn(".xiaoyue-mv-shot-asset-status:empty", styles)
        self.assertNotIn(".xiaoyue-mv-binding-rule", styles)
        self.assertNotIn(".xiaoyue-mv-legacy-material", styles)

    def test_h3_prompt_actions_apply_each_success_and_keep_failed_shots(self):
        source = (ROOT / "web" / "mv_planner.js").read_text("utf-8")
        styles = (ROOT / "web" / "mv_planner.css").read_text("utf-8")
        server = (ROOT / "server_api.py").read_text("utf-8")
        for action in ("generate-h3-current", "generate-h3-all"):
            self.assertIn(f'data-action="{action}"', source)
        self.assertIn("生成本镜", source)
        self.assertIn("批量全生", source)
        self.assertIn("成功即覆盖目标镜头；失败保留旧词，批量继续。", source)
        self.assertIn("批量全生会依次处理全部", source)
        self.assertIn("每镜成功后立即覆盖该镜提示词", source)
        self.assertIn("续写链前段失败时跳过后续连续段", source)
        self.assertIn("遇到硬切或叠化后继续", source)
        self.assertIn('api.fetchApi("/xiaoyue-mv/h3/prompts"', source)
        self.assertIn("applyAvailablePrompts(job.result)", source)
        self.assertIn("sourceStateForShot(index) !== requestSourceStates.get(shotId)", source)
        self.assertIn("目标镜头原有 H3 提示词保持不变", source)
        self.assertIn("xiaoyue-mv-prompt-status-icon", source)
        self.assertIn("H3 提示词已生成", source)
        self.assertIn("H3 提示词未生成", source)
        self.assertIn('error: String(failure?.error || "生成失败")', source)
        self.assertIn('`镜头 ${label}：${error}`', source)
        self.assertIn("失败原因：${failureDetails}", source)
        self.assertIn(".xiaoyue-mv-prompt-status-icon.ready", styles)
        self.assertIn(".xiaoyue-mv-prompt-status-icon.missing", styles)
        self.assertIn(
            '@PromptServer.instance.routes.post("/xiaoyue-mv/h3/prompts")',
            server,
        )
        self.assertIn("for position, shot_id in enumerate(shot_ids, 1)", server)
        self.assertIn('result["successful_count"] = len(results)', server)
        self.assertIn('result["failed_count"] = len(failures)', server)
        self.assertIn('result["skipped_count"] = len(skipped)', server)
        self.assertIn('outcomes[shot_id] = "skipped"', server)
        self.assertIn('source_shot["prompt"] = prompt', server)
        self.assertIn('"state": "completed"', server)

    def test_independent_h3_prompts_queue_without_global_button_lock(self):
        source = (ROOT / "web" / "mv_planner.js").read_text("utf-8")
        server = (ROOT / "server_api.py").read_text("utf-8")
        self.assertIn("this.h3PromptJobs = new Map()", source)
        self.assertIn("this.h3PromptChainConflict(shotId)", source)
        self.assertIn("this.h3PromptBatchRunning || (scope === \"all\" && this.h3PromptRunning)", source)
        self.assertIn("this.setH3PromptJobStatus(shotIds, job.state === \"queued\"", source)
        self.assertIn('batch: scope === "all"', source)
        self.assertIn("async with _H3_PROMPT_RUN_LOCK:", server)
        self.assertIn('"state": "queued"', server)
        self.assertIn('job.get("state") in ("queued", "running")', server)

    def test_project_settings_expose_stage_skill_template_selectors(self):
        source = (ROOT / "web" / "mv_planner.js").read_text("utf-8")
        project_editor = source.split("class ProjectEditor {", 1)[1].split(
            "class PlannerEditor {", 1)[0]
        self.assertLess(
            project_editor.index("各阶段 Skill 模板"),
            project_editor.index("声音与歌词"),
        )
        self.assertIn('api.fetchApi("/xiaoyue-mv/skill-templates")', project_editor)
        self.assertIn("skill_templates: this.draft.skill_templates", project_editor)
        self.assertIn('data-field="project-outline-skill"', source)
        self.assertIn('data-field="project-h3-skill"', source)
        self.assertIn("skill_template_library", source)
        self.assertIn("this.plan.skill_templates[stage]", source)

    def test_lip_sync_toggle_shares_asset_action_row_with_short_label(self):
        source = (ROOT / "web" / "mv_planner.js").read_text("utf-8")
        styles = (ROOT / "web" / "mv_planner.css").read_text("utf-8")
        actions = source.split('<div class="xiaoyue-mv-shot-asset-actions">', 1)[1].split(
            '</div>', 1)[0]
        self.assertLess(
            actions.index('data-field="singing-performance"'),
            actions.index('data-action="open-shot-asset-picker"'),
        )
        self.assertIn('<span>口型</span>', actions)
        self.assertIn('flex-direction: row;', styles.split(
            '.xiaoyue-mv-dialog .xiaoyue-mv-singing-toggle {', 1)[1].split('}', 1)[0])

    def test_h3_prompt_textarea_grows_with_content_and_shot_switches(self):
        source = (ROOT / "web" / "mv_planner.js").read_text("utf-8")
        styles = (ROOT / "web" / "mv_planner.css").read_text("utf-8")
        self.assertIn('textarea.style.height = "auto";', source)
        self.assertIn('textarea.scrollHeight + 2', source)
        self.assertIn('if (key === "prompt") this.fitPromptTextarea();', source)
        self.assertIn('this.field("prompt").value = shot.prompt || "";', source)
        self.assertIn('this.field("h3-prompt-status").value =', source)
        self.assertIn('this.fitPromptTextarea();', source)
        self.assertIn('this.promptResizeObserver.observe(this.field("prompt"));', source)
        prompt_style = styles.split(
            '.xiaoyue-mv-prompt-stages .generation-prompt-stage textarea {', 1
        )[1].split('}', 1)[0]
        self.assertIn('overflow-y: hidden;', prompt_style)

    def test_shot_detail_tabs_stay_visible_when_detail_content_scrolls(self):
        styles = (ROOT / "web" / "mv_planner.css").read_text("utf-8")
        pane_style = styles.split('.xiaoyue-mv-details-pane {', 1)[1].split('}', 1)[0]
        tabs_style = styles.split('.xiaoyue-mv-detail-tabs {', 1)[1].split('}', 1)[0]
        self.assertIn('overflow-y: auto;', pane_style)
        self.assertIn('position: sticky;', tabs_style)
        self.assertIn('top: 0;', tabs_style)
        self.assertIn('background: #0b1017;', tabs_style)


if __name__ == "__main__":
    unittest.main()
