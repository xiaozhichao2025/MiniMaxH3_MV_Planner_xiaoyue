"""Compose one MV shot from the visible, user-editable canvas graph.

The editor submits the live LiteGraph snapshot.  Marked backbone nodes keep
stable adapter IDs even if ComfyUI renumbers them; unmarked nodes added by the
user remain in the marked output ancestry. Generation data only crosses
visible wires. Generated windows are saved whole;
delivery in/out points are recorded for the later editorial export.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import secrets
import wave
from pathlib import Path, PurePosixPath
from typing import Any


_WIDGET_TYPES = {"INT", "FLOAT", "STRING", "BOOLEAN", "COMBO",
                 "COMFY_DYNAMICCOMBO_V3"}
BACKBONE_NODE_PROPERTY = "xiaoyue_mv_backbone_id"
MERGED_WORKBENCH_TYPE = "MiniMaxH3_MV_Planner_xiaoyue"
SHOT_INPUT_TYPE = "Xiaoyue_MV_ShotInput"
PLANNER_OUTPUT_COUNT = 8


def delivery_tail_start(deliver_from_raw_frame: int, deliver_frames: int,
                        guide_frames: int) -> int:
    """Locate a selected guide wholly inside the parent version's delivery."""
    lead = int(deliver_from_raw_frame)
    deliver = int(deliver_frames)
    guide = int(guide_frames)
    if lead < 0 or guide <= 0 or deliver < guide:
        raise ValueError(f"前镜交付区不足 {guide} 帧，无法读取所选续写引导。")
    return lead + deliver - guide


def canvas_backbone(ui: dict[str, Any]) -> dict[str, Any]:
    """Extract the marked canvas backbone, preserving user-added upstream nodes.

    The merged editor workflow also contains the Planner and providers.  They
    must never enter a shot prompt.  Canonical IDs refer to adapter anchor
    nodes; newly inserted nodes receive IDs outside the dynamic 10000 range.
    """
    if not isinstance(ui, dict) or not isinstance(ui.get("nodes"), list) \
            or not isinstance(ui.get("links"), list):
        raise ValueError("当前画布不是有效的 ComfyUI 工作流。")
    nodes = ui["nodes"]
    links = ui["links"]
    if len(nodes) > 600 or len(links) > 2000:
        raise ValueError("当前画布节点过多，无法安全提交镜头工作流。")
    anchors: dict[int, str] = {}
    raw_nodes: dict[str, dict[str, Any]] = {}
    for node in nodes:
        if not isinstance(node, dict) or "id" not in node:
            raise ValueError("当前画布含无效节点。")
        original = str(node["id"])
        if original in raw_nodes:
            raise ValueError("当前画布存在重复节点 ID。")
        raw_nodes[original] = node
        marker = (node.get("properties") or {}).get(BACKBONE_NODE_PROPERTY)
        if marker is None:
            continue
        try:
            canonical = int(marker)
        except (TypeError, ValueError):
            raise ValueError("生成骨干节点标记无效。") from None
        if canonical < 1 or canonical >= 10000 or canonical in anchors:
            raise ValueError("生成骨干节点标记重复或超出允许范围。")
        anchors[canonical] = original
    root = raw_nodes.get(anchors.get(5, ""))
    if root is None:
        raise ValueError("画布里没有标记为视频输出的节点；请连接并标记输出节点。")
    raw_links: dict[str, list[Any]] = {}
    for link in links:
        if not isinstance(link, list) or len(link) < 5:
            raise ValueError("当前画布存在无效连线。")
        link_id = str(link[0])
        if link_id in raw_links:
            raise ValueError("当前画布存在重复连线 ID。")
        raw_links[link_id] = link
    active_nodes: set[str] = set()
    active_links: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in active_nodes:
            return
        node = raw_nodes.get(node_id)
        if node is None:
            raise ValueError("生成骨干连线指向缺失节点。")
        active_nodes.add(node_id)
        if (node.get("type") == MERGED_WORKBENCH_TYPE
                and node_id == anchors.get(130)):
            # Only its shot-facing sockets belong to this workflow. Project
            # state and providers stay in the planner, never in a shot queue.
            return
        for entry in node.get("inputs", []):
            link_id = entry.get("link")
            if link_id is None:
                continue
            link = raw_links.get(str(link_id))
            if link is None or str(link[3]) != node_id:
                raise ValueError("生成骨干存在断开或错位的连线。")
            active_links.add(str(link_id))
            visit(str(link[1]))

    visit(anchors[5])
    remap = {original: canonical for canonical, original in anchors.items()
             if original in active_nodes}
    next_id = 20000
    for node in nodes:
        original = str(node["id"])
        if original in active_nodes and original not in remap:
            remap[original] = next_id
            next_id += 1
    normalized_nodes = []
    for node in nodes:
        original = str(node["id"])
        if original not in active_nodes:
            continue
        item = copy.deepcopy(node)
        item["id"] = remap[original]
        if (original == anchors.get(130)
                and node.get("type") == MERGED_WORKBENCH_TYPE):
            outputs = item.get("outputs") or []
            if len(outputs) < PLANNER_OUTPUT_COUNT + 19:
                raise ValueError("合并工作台缺少本镜输出口；请更新插件和工作流。")
            named = item.get("widgets_values_named") or {}
            widget_names = [str(entry.get("name")) for entry in item.get("inputs", [])
                            if entry.get("widget")]
            values = item.get("widgets_values") or []
            seed_index = widget_names.index("seed") if "seed" in widget_names else -1
            seed = (values[seed_index] if isinstance(values, list)
                    and 0 <= seed_index < len(values)
                    else named.get("seed", -1))
            if seed is None:
                seed = -1
            item["type"] = SHOT_INPUT_TYPE
            item["inputs"] = [{"name": "seed", "type": "INT",
                               "widget": {"name": "seed"}, "link": None}]
            item["outputs"] = outputs[PLANNER_OUTPUT_COUNT:]
            item["widgets_values"] = [seed]
            item["widgets_values_named"] = {"seed": seed}
        normalized_nodes.append(item)
    normalized_links = []
    for link in links:
        if str(link[0]) not in active_links:
            continue
        source, target = str(link[1]), str(link[3])
        item = list(link)
        item[1], item[3] = remap[source], remap[target]
        if (source == anchors.get(130)
                and raw_nodes[source].get("type") == MERGED_WORKBENCH_TYPE):
            if int(item[2]) < PLANNER_OUTPUT_COUNT:
                raise ValueError("生成链误接了工作台的项目输出；请改接本镜生成输出。")
            item[2] = int(item[2]) - PLANNER_OUTPUT_COUNT
        normalized_links.append(item)
    return {"nodes": normalized_nodes, "links": normalized_links}


def canvas_workflow_fingerprint(ui: dict[str, Any]) -> str:
    """Audit the submitted live graph without treating node layout as config."""
    facts = {
        "nodes": [
            {"id": node["id"], "type": node.get("type"),
             "mode": node.get("mode"),
             "widgets_values": node.get("widgets_values"),
             "inputs": [(entry.get("name"), entry.get("link"))
                        for entry in node.get("inputs", [])]}
            for node in ui["nodes"]
        ],
        "links": [link[:5] for link in ui["links"]],
    }
    return hashlib.sha256(json.dumps(facts, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def shot_seed_setting(ui: dict[str, Any]) -> int:
    """Read the visible shot-input seed control; -1 means new seed per attempt."""
    node = next((item for item in ui.get("nodes", [])
                 if int(item.get("id") or 0) == 130), None)
    if node is None:
        raise ValueError("画布缺少本镜输入节点。")
    seed_input = next((item for item in node.get("inputs", [])
                       if item.get("name") == "seed"), None)
    if seed_input is None:
        return -1  # Existing saved canvases predate the seed control.
    if seed_input and seed_input.get("link") is not None:
        raise ValueError("镜头页自动生成暂不支持外接种子线；请在本镜输入节点设置固定种子或 -1。")
    values = node.get("widgets_values") or []
    widget_names = [str(entry.get("name")) for entry in node.get("inputs", [])
                    if entry.get("widget")]
    index = widget_names.index("seed") if "seed" in widget_names else 0
    named = node.get("widgets_values_named") or {}
    value = (values[index] if isinstance(values, list) and index < len(values)
             else named.get("seed", -1))
    try:
        seed = int(value)
    except (TypeError, ValueError):
        raise ValueError("本镜输入节点的种子必须是整数。") from None
    if seed < -1 or seed > 9223372036854775807:
        raise ValueError("本镜输入节点的种子超出范围。")
    return seed


def _widget_info(node_info: dict[str, Any], class_type: str) -> dict[str, Any]:
    info = node_info.get(class_type)
    if not isinstance(info, dict):
        raise ValueError(f"59 上缺少镜头工作流节点：{class_type}")
    inputs = info.get("input") or {}
    return {**(inputs.get("required") or {}), **(inputs.get("optional") or {})}


def _is_widget(spec: Any) -> bool:
    if not isinstance(spec, (tuple, list)) or not spec:
        return False
    kind = spec[0]
    return isinstance(kind, list) or kind in _WIDGET_TYPES


def ui_to_api_workflow(ui: dict[str, Any], node_info: dict[str, Any],
                       output_id: str = "5") -> dict[str, dict[str, Any]]:
    """Convert only the marked video-output ancestry, retaining the live graph."""
    raw_nodes = {str(node["id"]): node for node in ui.get("nodes", [])}
    links = {str(link[0]): link for link in ui.get("links", [])}
    if output_id not in raw_nodes:
        raise ValueError("镜头工作流缺少标记的视频输出节点。")

    def source(link_id: Any) -> list[Any]:
        link = links.get(str(link_id))
        if link is None:
            raise ValueError(f"主干工作流存在断开的连线：{link_id}")
        upstream, slot = str(link[1]), int(link[2])
        node = raw_nodes[upstream]
        if node.get("type") == "Reroute":
            incoming = next((entry.get("link") for entry in
                             node.get("inputs", [])
                             if entry.get("link") is not None), None)
            return source(incoming)
        mode = int(node.get("mode") or 0)
        if mode == 4:
            outputs = node.get("outputs") or []
            output_type = str(outputs[slot].get("type") or "") \
                if slot < len(outputs) else ""
            candidates = [entry for entry in node.get("inputs", [])
                          if entry.get("link") is not None
                          and entry.get("type") in (output_type, "*")]
            if len(candidates) != 1:
                raise ValueError(
                    f"画布节点 {upstream} 已绕过，但无法确定输入到输出的直通连线。")
            return source(candidates[0]["link"])
        if mode != 0:
            raise ValueError(f"生成链节点 {upstream} 已静音；请恢复或删除该节点。")
        return [upstream, slot]

    reachable: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in reachable:
            return
        node = raw_nodes.get(node_id)
        if node is None:
            raise ValueError(f"主干工作流缺少节点 {node_id}。")
        reachable.add(node_id)
        for entry in node.get("inputs", []):
            if entry.get("link") is not None:
                visit(source(entry["link"])[0])

    visit(output_id)
    result: dict[str, dict[str, Any]] = {}
    for node_id in reachable:
        node = raw_nodes[node_id]
        class_type = str(node.get("type") or "")
        schema = _widget_info(node_info, class_type)
        values = node.get("widgets_values") or []
        if not isinstance(values, (list, dict)):
            values = [values]
        cursor = 0
        inputs: dict[str, Any] = {}
        for entry in node.get("inputs", []):
            name = str(entry.get("name") or "")
            widget = bool(entry.get("widget")) or (name in schema and _is_widget(schema[name]))
            if isinstance(values, dict):
                value = values.get(name)
            else:
                value = values[cursor] if widget and cursor < len(values) else None
                if widget:
                    cursor += 1
            if entry.get("link") is not None:
                inputs[name] = source(entry["link"])
            elif widget:
                if value is None and name in schema:
                    options = schema[name][1] if len(schema[name]) > 1 else {}
                    value = options.get("default") if isinstance(options, dict) else None
                if value is not None:
                    inputs[name] = value
        result[node_id] = {"class_type": class_type, "inputs": inputs}
    return result


def _add(graph: dict[str, dict[str, Any]], node_id: int, class_type: str,
         **inputs: Any) -> list[Any]:
    key = str(node_id)
    if key in graph:
        raise ValueError(f"动态节点编号 {key} 与主干冲突。")
    graph[key] = {"class_type": class_type, "inputs": inputs}
    return [key, 0]


def _relative_parts(value: str, label: str) -> tuple[str, str]:
    """Split a ComfyUI-root-relative path without permitting a root escape."""
    raw = str(value or "")
    if not raw or "\\" in raw or raw.startswith("/") or ":" in raw:
        raise ValueError(f"{label}必须是非空的 ComfyUI 相对路径。")
    path = PurePosixPath(raw)
    if any(part in ("", ".", "..") for part in raw.split("/")) or not path.name:
        raise ValueError(f"{label}包含无效目录。")
    return ("" if str(path.parent) == "." else str(path.parent), path.name)


def _validate_routed_h3_passes(graph: dict[str, dict[str, Any]]) -> None:
    """Validate only H3 passes actually attached to the MV slot router."""
    routers = {node_id for node_id, node in graph.items()
               if node["class_type"] == "Xiaoyue_MV_ReferenceSlots"}
    for router_id in routers:
        inputs = graph[router_id]["inputs"]
        if inputs.get("reference_manifest_json") != ["130", 9] \
                or inputs.get("shot_audio") != ["130", 1]:
            raise ValueError("本镜附件路由必须从本镜输入接收附件清单和音频。")
    for node_id, node in graph.items():
        if node["class_type"] != "MiniMaxH3ReferenceToVideo":
            continue
        inputs = node["inputs"]
        routed = [(name, value) for name, value in inputs.items()
                  if name.startswith(("ref_images.", "ref_videos.",
                                      "ref_audios."))
                  and isinstance(value, list) and len(value) == 2
                  and str(value[0]) in routers]
        if not routed:
            continue  # This H3 node is not controlled by the MV slot router.
        if inputs.get("length") != ["130", 6]:
            raise ValueError(f"已接附件路由的 H3 节点 {node_id} 必须连本镜原始帧数。")
        for name, value in routed:
            prefix, _, suffix = name.rpartition("_")
            try:
                slot = int(suffix)
            except ValueError:
                raise ValueError(f"H3 节点 {node_id} 的附件槽位名无效：{name}") from None
            offset = (0 if prefix == "ref_images.ref_image" else
                      9 if prefix == "ref_videos.ref_video" else
                      12 if prefix == "ref_audios.ref_audio" else -1)
            limit = 9 if offset == 0 else 3
            if offset < 0 or not 0 <= slot < limit or int(value[1]) != offset + slot:
                raise ValueError(f"H3 节点 {node_id} 的附件槽位 {name} 接线错位。")


def _validate_output_naming(graph: dict[str, dict[str, Any]]) -> None:
    """The user's marked saver must receive the workbench naming by wire."""
    naming_ports: set[int] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visited:
            return
        visited.add(node_id)
        for value in graph[node_id]["inputs"].values():
            if not isinstance(value, list) or len(value) != 2:
                continue
            upstream = str(value[0])
            if upstream == "130":
                naming_ports.add(int(value[1]))
            elif upstream in graph:
                visit(upstream)

    visit("5")
    if 8 not in naming_ports and not {17, 18}.issubset(naming_ports):
        raise ValueError(
            "视频输出节点必须从本镜输入接收 output_prefix，或同时接收输出目录和文件名。")


def _prune_api_ancestry(graph: dict[str, dict[str, Any]], root: str = "5") \
        -> dict[str, dict[str, Any]]:
    """Drop the Planner/providers after replacing the project-to-shot edge."""
    keep: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in keep:
            return
        if node_id not in graph:
            raise ValueError(f"镜头工作流引用了缺失节点 {node_id}。")
        keep.add(node_id)
        for value in graph[node_id]["inputs"].values():
            if (isinstance(value, list) and len(value) == 2
                    and str(value[0]) in graph):
                visit(str(value[0]))

    visit(root)
    return {node_id: node for node_id, node in graph.items() if node_id in keep}


def history_video_descriptor(history: dict[str, Any],
                             output_id: str = "5") -> dict[str, Any] | None:
    """Find SaveVideo's file across ComfyUI's video and image-style outputs."""
    output = (history.get("outputs") or {}).get(output_id) or {}
    for key in ("videos", "gifs"):
        for item in output.get(key) or []:
            if isinstance(item, dict) and item.get("filename"):
                return item
    for item in output.get("images") or []:
        if not isinstance(item, dict) or not item.get("filename"):
            continue
        metadata = item.get("metadata") or {}
        suffix = Path(str(item["filename"])).suffix.lower()
        if (metadata.get("kind") == "video"
                or suffix in {".mp4", ".mov", ".mkv", ".webm", ".avi"}):
            return item
    return None


def compose_shot_workflow(ui: dict[str, Any], node_info: dict[str, Any], *,
                          prompt: str, window: dict[str, Any],
                          generation_spec: dict[str, Any],
                          audio_input_name: str,
                          visual_inputs: list[dict[str, str]],
                          output_prefix: str,
                          seed: int | None = None,
                          parent_video_path: str = "",
                          parent_deliver_frames: int = 0,
                          parent_deliver_from_raw_frame: int = 0) -> dict[str, dict[str, Any]]:
    raw_frames = int(window["raw_frames"])
    lead = int(window["deliver_from_raw_frame"])
    deliver = int(window["deliver_frames"])
    context = int(window.get("context_frames") or 0)
    if (raw_frames < 5 or (raw_frames - 5) % 17 or deliver <= 0
            or lead < 0 or lead + deliver > raw_frames):
        raise ValueError("镜头 H3 帧预算无效；请重新运行项目规划节点。")
    if context and (not parent_video_path or parent_deliver_frames < context):
        raise ValueError(
            f"续写镜头必须选定交付区至少有 {context} 帧的上一镜视频版本。")
    if not prompt.strip():
        raise ValueError("当前镜头没有 H3 提示词；请先生成或填写。")
    ui = copy.deepcopy(ui)
    if not context:
        for node in ui.get("nodes", []):
            if str((node.get("properties") or {}).get(BACKBONE_NODE_PROPERTY)) in ("133", "134"):
                node["mode"] = 4  # CONDITIONING positive is the passthrough.
    graph = ui_to_api_workflow(ui, node_info)
    required = {"5", "130"}
    if not required.issubset(graph):
        raise ValueError("画布缺少已接线的本镜输入或视频输出；请重新打开新版工作流。")
    actual_seed = secrets.randbits(63) if seed is None else int(seed)
    if actual_seed < 0 or actual_seed > 9223372036854775807:
        raise ValueError("镜头种子超出 H3 可用范围。")
    input_subfolder, _ = _relative_parts(audio_input_name, "镜头音频输入路径")
    output_subfolder, output_filename_stem = _relative_parts(
        output_prefix, "镜头视频输出前缀")
    # Replace only the shot-data source at queue time. Every downstream node
    # and its explicit connections remain the user's live canvas graph.
    audio = _add(graph, 10000, "LoadAudio", audio=audio_input_name)
    reference_manifest = []
    image_slot = video_slot = 0
    for asset in visual_inputs:
        media = asset.get("media_type")
        if media == "image":
            slot = f"ref_images.ref_image_{image_slot}"
            image_slot += 1
        elif media == "video":
            slot = f"ref_videos.ref_video_{video_slot}"
            video_slot += 1
        else:
            raise ValueError("镜头绑定了不支持的视觉资产类型。")
        reference_manifest.append({"slot": slot, **asset})
    if image_slot > 9 or video_slot > 3:
        raise ValueError("H3 单镜最多支持 9 张参考图和 3 段参考视频。")
    graph["130"] = {"class_type": "Xiaoyue_MV_RuntimeShot", "inputs": {
        "h3_prompt": prompt, "shot_audio": audio,
        "aspect_ratio": generation_spec["aspect_ratio"],
        "first_pass_megapixels": float(generation_spec["first_pass_megapixels"]),
        "second_pass_megapixels": float(generation_spec["second_pass_megapixels"]),
        "raw_seconds": raw_frames / 24.0, "raw_frames": raw_frames,
        "seed": actual_seed, "output_prefix": output_prefix,
        "reference_manifest_json": json.dumps({
            "visual_references": reference_manifest,
            "original_song_audio_slot": 1}, ensure_ascii=False),
        "deliver_from_raw_frame": lead, "deliver_frames": deliver,
        "continuation_video_path": parent_video_path if context else "",
        "continuation_frame_count": context,
        "continuation_start_frame": delivery_tail_start(
            parent_deliver_from_raw_frame, parent_deliver_frames, context)
        if context else 0,
        "shot_audio_input_name": audio_input_name,
        "input_subfolder": input_subfolder,
        "output_subfolder": output_subfolder,
        "output_filename_stem": output_filename_stem,
    }}
    graph = _prune_api_ancestry(graph)
    _validate_routed_h3_passes(graph)
    _validate_output_naming(graph)
    if context:
        used_outputs = {int(value[1]) for node in graph.values()
                        for value in node["inputs"].values()
                        if isinstance(value, list) and len(value) == 2
                        and str(value[0]) == "130"}
        if not {12, 13, 14}.issubset(used_outputs):
            raise ValueError(
                "续写镜头必须将前镜视频路径、引导帧数和起始帧从本镜输入连线到生成工作流。")
    return graph


def workflow_fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def window_fingerprint(window: dict[str, Any], generation_spec: dict[str, Any],
                       audio_fingerprint: str, *, legacy: bool = False,
                       parent_uid: str = "") -> str:
    window_keys = (
        "source_start_frame", "source_end_frame", "raw_frames",
        "context_frames", "deliver_from_raw_frame", "deliver_frames",
        "delivery_timeline_range", "core_timeline_range",
    )
    # The visual shot number is allowed to change after insert/delete. Keep
    # the old digest available only to recognize pre-migration versions.
    facts = {
        "audio_fingerprint": audio_fingerprint,
        "generation_spec": generation_spec,
        "window": {key: window.get(key) for key in
                   (("shot_id",) + window_keys if legacy else window_keys)},
    }
    if not legacy:
        facts["parent_uid"] = parent_uid
    return hashlib.sha256(json.dumps(facts, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def prepare_window_audio(project: dict[str, Any], window: dict[str, Any],
                         version_id: str) -> str:
    """Cut the original master to an exact H3 window, including edge silence."""
    try:
        from .project_audio import load_project_audio
    except ImportError:
        from project_audio import load_project_audio
    payload = load_project_audio(project, "master")
    if payload is None:
        raise ValueError("项目缺少原曲母带。")
    source = payload["waveform"][0]
    sample_rate = int(payload["sample_rate"])
    planned_rate = int((project["manifest"].get("audio") or {}).get("sample_rate") or 0)
    if sample_rate != planned_rate:
        raise ValueError("母带采样率与已编译镜头计划不一致；请重新运行规划节点。")
    requested = int(window["requested_samples"])
    first = int(window["source_start_sample"])
    last = int(window["source_end_sample"])
    before = int(window["pad_before_samples"])
    after = int(window["pad_after_samples"])
    if (requested < 1 or first < 0 or last < first or before < 0 or after < 0
            or before + last - first + after != requested
            or last > source.shape[-1]):
        raise ValueError("镜头音频窗口样本坐标无效。")
    import torch

    segment = source[:, first:last]
    if before or after:
        segment = torch.nn.functional.pad(segment, (before, after))
    samples = (segment.clamp(-1, 1).transpose(0, 1).contiguous().numpy() * 32767).round()
    samples = samples.astype("<i2", copy=False)
    input_root = Path(project["paths"]["input"]).resolve()
    directory = (input_root / "runtime_audio").resolve()
    directory.relative_to(input_root)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"shot_{version_id}.wav"
    temporary = target.with_name(f".{target.name}.tmp")
    try:
        with wave.open(str(temporary), "wb") as output:
            output.setnchannels(int(segment.shape[0]))
            output.setsampwidth(2)
            output.setframerate(sample_rate)
            output.writeframes(samples.tobytes())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return f"xiaoyue_mv_projects/{project['manifest']['project_id']}/runtime_audio/{target.name}"
