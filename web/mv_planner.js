import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const EXTENSION_NAME = "xiaoyue.minimax_h3.mv_planner";
const NODE_NAME = "MiniMaxH3_MV_Planner_xiaoyue";
const SHOT_INPUT_NODE_NAME = "Xiaoyue_MV_ShotInput";
const PREVIEW_NODE_NAME = "Xiaoyue_MV_PreviewDesk";
const PLANNER_OUTPUT_COUNT = 8;
const SHOT_OUTPUT_COUNT = 19;
const LOCAL_WHISPER_PROVIDER_NODE_NAME = "Xiaoyue_Local_Whisper_Provider";
const QWEN_MV_REASONING_PROVIDER_NODE_NAME = "Xiaoyue_QwenTE_MV_Reasoning_Provider_xiaoyue";
const LEGACY_PROJECT_NODE_NAME = "MiniMaxH3_MV_Project_xiaoyue";
const LEGACY_PLANNER_INPUT_NAMES = new Set([
    "project",
    "audio",
    "vocals_audio",
    "subtitle_text",
]);
const UNIFIED_WORKBENCH_TITLE = "MiniMax H3 · MV 项目工作台";
const payloadByNode = new WeakMap();
const editorByNode = new WeakMap();
const projectEditorByNode = new WeakMap();
const SHOT_TONE_COUNT = 5;
const H3_PROJECT_PRESETS = [124, 141, 158, 175, 192, 209, 226, 243, 260, 277, 294, 311, 328, 345, 362];
const ASPECT_RATIO_OPTIONS = [
    "1:1 (Square)",
    "2:3 (Portrait Photo)",
    "3:2 (Photo)",
    "3:4 (Portrait Standard)",
    "4:3 (Standard)",
    "9:16 (Portrait Widescreen)",
    "16:9 (Widescreen)",
    "21:9 (Ultrawide)",
];
const DEFAULT_GENERATION_SPEC = Object.freeze({
    aspect_ratio: "16:9 (Widescreen)",
    first_pass_megapixels: 0.4,
    second_pass_megapixels: 0.9,
    pixel_alignment: 32,
});
const DEFAULT_SKILL_TEMPLATES = Object.freeze({
    visual_outline: "xiaoyue-mv-visual-outline",
    h3_prompt: "xiaoyue-h3-mv-prompt",
});
const outputTooltipsByNodeType = new Map();
let shotOutputTooltipInstalled = false;

function normalizeSkillTemplateSelection(value) {
    const source = value && typeof value === "object" ? value : {};
    return {
        visual_outline: String(source.visual_outline || DEFAULT_SKILL_TEMPLATES.visual_outline),
        h3_prompt: String(source.h3_prompt || DEFAULT_SKILL_TEMPLATES.h3_prompt),
    };
}

function normalizeGenerationSpec(value) {
    const source = value && typeof value === "object" ? value : {};
    const aspectRatio = ASPECT_RATIO_OPTIONS.includes(source.aspect_ratio)
        ? source.aspect_ratio : DEFAULT_GENERATION_SPEC.aspect_ratio;
    const firstPass = Math.max(0.1, Math.min(
        16,
        Number(source.first_pass_megapixels) || DEFAULT_GENERATION_SPEC.first_pass_megapixels,
    ));
    const secondPass = Math.max(firstPass, Math.min(
        16,
        Number(source.second_pass_megapixels) || DEFAULT_GENERATION_SPEC.second_pass_megapixels,
    ));
    return {
        aspect_ratio: aspectRatio,
        first_pass_megapixels: Number(firstPass.toFixed(3)),
        second_pass_megapixels: Number(secondPass.toFixed(3)),
        pixel_alignment: 32,
    };
}

function h3ProjectPresetLabel(frames) {
    const seconds = (frames / 24).toFixed(2);
    return `${frames} 帧 / ${seconds} 秒${frames === 226 ? "（甜蜜线）" : ""}`;
}

function shotToneClass(index) {
    return `shot-tone-${Math.abs(Math.floor(Number(index) || 0)) % SHOT_TONE_COUNT}`;
}

function installStyles() {
    const id = "xiaoyue-mv-planner-styles";
    if (document.getElementById(id)) return;
    const link = document.createElement("link");
    link.id = id;
    link.rel = "stylesheet";
    link.href = new URL("./mv_planner.css", import.meta.url).href;
    document.head.appendChild(link);
}

function planWidget(node) {
    return node.widgets?.find((widget) => widget.name === "plan_json");
}

function nodeWidget(node, name) {
    return node?.widgets?.find((widget) => widget.name === name);
}

function parseProjectNodeState(node) {
    const raw = nodeWidget(node, "project_state_json")?.value;
    if (!raw || typeof raw !== "string") return null;
    try {
        const value = JSON.parse(raw);
        return value && typeof value === "object" ? value : null;
    } catch {
        return null;
    }
}

function newProjectId() {
    if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
    const bytes = new Uint8Array(16);
    globalThis.crypto?.getRandomValues?.(bytes);
    if (!bytes.some(Boolean)) {
        for (let index = 0; index < bytes.length; index += 1) {
            bytes[index] = Math.floor(Math.random() * 256);
        }
    }
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = [...bytes].map((value) => value.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function widgetValue(node, name, fallback = "") {
    return node?.widgets?.find((widget) => widget.name === name)?.value ?? fallback;
}

function connectedSourceNode(plannerNode, inputName) {
    const input = plannerNode?.inputs?.find((item) => item.name === inputName);
    if (input?.link === null || input?.link === undefined) return null;
    const graph = plannerNode.graph || app.graph;
    const link = graph?.links?.[input.link];
    return link ? graph?.getNodeById?.(link.origin_id) : null;
}

function connectedSubtitleProvider(plannerNode) {
    const sourceNode = connectedSourceNode(plannerNode, "asr_provider");
    const sourceType = sourceNode?.comfyClass || sourceNode?.type;
    if (sourceType === LOCAL_WHISPER_PROVIDER_NODE_NAME) {
        return { kind: "local_whisper", label: "本地 Whisper Provider", sourceNode };
    }
    return null;
}

function connectedReasoningProviderNode(plannerNode) {
    const sourceNode = connectedSourceNode(plannerNode, "reasoning_provider");
    const sourceType = sourceNode?.comfyClass || sourceNode?.type;
    return sourceType === QWEN_MV_REASONING_PROVIDER_NODE_NAME ? sourceNode : null;
}

function localWhisperSettings(provider) {
    const sourceNode = provider?.sourceNode;
    return {
        method: widgetValue(sourceNode, "model_type", "whisper-large-v3"),
        max_sentence_length: Number(widgetValue(sourceNode, "max_sentence_length", 20)),
        unload_model: Boolean(widgetValue(sourceNode, "unload_model", true)),
    };
}

function providerStatusLabel(provider) {
    if (provider?.kind === "local_whisper") {
        return "已连接本地 Whisper Provider；模型只会在点击识别时加载。";
    }
    return "未连接 ASR Provider；仍可粘贴或连接已有时间戳字幕。";
}

function parsePlan(node) {
    const raw = planWidget(node)?.value;
    if (!raw || typeof raw !== "string") return null;
    try {
        const value = JSON.parse(raw);
        if (!value || typeof value !== "object") return null;
        if (Number(value.h3?.continuation_window_rule_revision || 0) !== 3) {
            value.needs_compile = true;
        }
        return value;
    } catch {
        return null;
    }
}

function plannerPayload(message) {
    let value = message?.xiaoyue_mv_planner
        ?? message?.output?.xiaoyue_mv_planner
        ?? message?.detail?.output?.xiaoyue_mv_planner;
    while (Array.isArray(value)) value = value[0];
    return value?.plan_json ? value : null;
}

function clone(value) {
    return typeof structuredClone === "function"
        ? structuredClone(value)
        : JSON.parse(JSON.stringify(value));
}

function waitMilliseconds(milliseconds) {
    return new Promise((resolve) => window.setTimeout(
        resolve,
        Math.max(0, Number(milliseconds) || 0),
    ));
}

function secondsLabel(seconds) {
    const safe = Math.max(0, Number(seconds) || 0);
    const minutes = Math.floor(safe / 60);
    const remainder = safe - minutes * 60;
    return `${String(minutes).padStart(2, "0")}:${remainder.toFixed(3).padStart(6, "0")}`;
}

function frameSeconds(frame, fps = 24) {
    return Number(frame) / Number(fps || 24);
}

function timestampSeconds(value) {
    if (typeof value === "number" && Number.isFinite(value)) return Math.max(0, value);
    if (typeof value !== "string") return null;
    const text = value.trim().replace(",", ".");
    if (!text) return null;
    const parts = text.split(":").map(Number);
    if (parts.some((part) => !Number.isFinite(part))) return null;
    if (parts.length === 1) return Math.max(0, parts[0]);
    if (parts.length === 2) return Math.max(0, parts[0] * 60 + parts[1]);
    if (parts.length === 3) return Math.max(0, parts[0] * 3600 + parts[1] * 60 + parts[2]);
    return null;
}

function millisecondTimestampSeconds(value) {
    if (typeof value === "number" && Number.isFinite(value)) {
        return Math.max(0, value / 1000);
    }
    if (typeof value !== "string" || !value.trim()) return null;
    const milliseconds = Number(value);
    return Number.isFinite(milliseconds) ? Math.max(0, milliseconds / 1000) : null;
}

function canonicalSubtitleCue(value, index, plan) {
    const fps = Number(plan?.h3?.fps || 24);
    const sampleRate = Number(plan?.audio?.sample_rate || 48000);
    const totalFrames = Math.max(1, Number(plan?.audio?.timeline_frames || Number.MAX_SAFE_INTEGER));
    const totalSamples = Math.max(1, Number(plan?.audio?.total_samples || Number.MAX_SAFE_INTEGER));
    const startSeconds = timestampSeconds(
        value?.start_seconds ?? value?.start ?? value?.from,
    ) ?? millisecondTimestampSeconds(value?.start_ms);
    const endSeconds = timestampSeconds(
        value?.end_seconds ?? value?.end ?? value?.to,
    ) ?? millisecondTimestampSeconds(value?.end_ms);
    const rawStart = Number(value?.absolute_start_frame ?? value?.start_frame);
    const rawEnd = Number(value?.absolute_end_frame ?? value?.end_frame);
    if (!Number.isFinite(rawStart) && startSeconds === null) return null;
    if (!Number.isFinite(rawEnd) && endSeconds === null) return null;
    const startFrame = Math.max(0, Math.min(
        totalFrames,
        Number.isFinite(rawStart) ? Math.round(rawStart) : Math.round((startSeconds || 0) * fps),
    ));
    const endFrame = Math.max(startFrame + 1, Math.min(
        totalFrames,
        Number.isFinite(rawEnd) ? Math.round(rawEnd) : Math.round((endSeconds || 0) * fps),
    ));
    if (startFrame >= totalFrames || endFrame <= startFrame) return null;
    const originalText = String(
        value?.original_text ?? value?.text ?? value?.content ?? value?.word ?? "",
    ).trim();
    const correctedText = String(value?.corrected_text ?? originalText).trim();
    const defaultId = `cue_f${String(startFrame).padStart(7, "0")}_f${String(endFrame).padStart(7, "0")}`;
    const cue = {
        id: String(value?.cue_id || value?.id || defaultId),
        absolute_start_frame: startFrame,
        absolute_end_frame: endFrame,
        absolute_start_sample: Math.min(totalSamples, Math.round(startFrame * sampleRate / fps)),
        absolute_end_sample: Math.min(totalSamples, Math.round(endFrame * sampleRate / fps)),
        start_seconds: Number((startFrame / fps).toFixed(6)),
        end_seconds: Number((endFrame / fps).toFixed(6)),
        original_text: originalText,
        corrected_text: correctedText,
        revision: Math.max(0, Math.round(Number(value?.revision) || 0)),
    };
    const confidence = Number(value?.confidence);
    if (Number.isFinite(confidence)) cue.confidence = confidence;
    return cue;
}

function parseSubtitleSource(source, plan) {
    const text = String(source || "").trim();
    if (!text) return { format: "none", cues: [], untimedText: "" };
    let entries = [];
    let format = "plain";
    if (/^[\[{]/.test(text)) {
        try {
            const value = JSON.parse(text);
            entries = Array.isArray(value)
                ? value
                : ["master_cues", "cues", "segments", "subtitles", "items"]
                    .map((key) => value?.[key])
                    .find(Array.isArray) || [value];
            format = "json";
        } catch {
            entries = [];
        }
    }
    if (!entries.length) {
        const arrow = /(\d{1,2}:\d{2}(?::\d{2})?[,.]\d+)\s*-->\s*(\d{1,2}:\d{2}(?::\d{2})?[,.]\d+)/;
        for (const block of text.split(/\r?\n\s*\r?\n/)) {
            const lines = block.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
            const timingIndex = lines.findIndex((line) => arrow.test(line));
            if (timingIndex < 0) continue;
            const match = lines[timingIndex].match(arrow);
            entries.push({ start: match[1], end: match[2], text: lines.slice(timingIndex + 1).join(" ") });
        }
        if (entries.length) format = "srt";
    }
    if (!entries.length) {
        const lrc = [];
        for (const line of text.split(/\r?\n/)) {
            const matches = [...line.matchAll(/\[(\d{1,3}):(\d{2}(?:\.\d{1,3})?)\]/g)];
            const lyric = line.replace(/\[(\d{1,3}):(\d{2}(?:\.\d{1,3})?)\]/g, "").trim();
            matches.forEach((match) => lrc.push({ start: Number(match[1]) * 60 + Number(match[2]), text: lyric }));
        }
        lrc.sort((left, right) => left.start - right.start);
        entries = lrc.map((entry, index) => ({
            ...entry,
            end: Math.max(entry.start + 0.08, lrc[index + 1]?.start ?? entry.start + 4),
        }));
        if (entries.length) format = "lrc";
    }
    if (!entries.length) {
        for (const line of text.split(/\r?\n/)) {
            const match = line.match(/^\s*[\[(]([0-9:.,]+)\s*[,\-]\s*([0-9:.,]+)[\])]\s*(.*)$/);
            if (match) entries.push({ start: match[1], end: match[2], text: match[3] });
        }
        if (entries.length) format = "timestamp";
    }
    const seen = new Map();
    const cues = entries.map((entry, index) => canonicalSubtitleCue(entry, index, plan))
        .filter(Boolean)
        .map((cue) => {
            const duplicate = seen.get(cue.id) || 0;
            seen.set(cue.id, duplicate + 1);
            if (duplicate) cue.id = `${cue.id}_${String(duplicate + 1).padStart(2, "0")}`;
            return cue;
        })
        .sort((left, right) => left.absolute_start_frame - right.absolute_start_frame);
    return { format, cues, untimedText: cues.length ? "" : text };
}

function srtTimestamp(seconds) {
    const milliseconds = Math.max(0, Math.round(Number(seconds || 0) * 1000));
    const hours = Math.floor(milliseconds / 3600000);
    const minutes = Math.floor(milliseconds % 3600000 / 60000);
    const secs = Math.floor(milliseconds % 60000 / 1000);
    const millis = milliseconds % 1000;
    return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")},${String(millis).padStart(3, "0")}`;
}

function serializeMasterCues(cues, fps = 24) {
    return (cues || []).map((cue, index) => [
        String(index + 1),
        `${srtTimestamp(cue.absolute_start_frame / fps)} --> ${srtTimestamp(cue.absolute_end_frame / fps)}`,
        String(cue.corrected_text || cue.original_text || "").trim(),
    ].join("\n")).join("\n\n");
}

function singleLineSubtitleText(value) {
    return String(value ?? "").replace(/\s+/g, " ").trim();
}

function joinSubtitleContextParts(parts) {
    const result = [];
    for (const value of parts) {
        const text = singleLineSubtitleText(value);
        if (text && text !== result.at(-1)) result.push(text);
    }
    return result.join(" ");
}

function legalH3Frames(requiredFrames, maximumFrames = 226) {
    const required = Math.max(1, Math.round(Number(requiredFrames) || 1));
    const legal = required <= 5 ? 5 : 5 + 17 * Math.ceil((required - 5) / 17);
    return Math.min(Math.max(5, Number(maximumFrames) || 226), legal);
}

function allocateAudioHandles(paddingFrames, beforeTarget, afterTarget) {
    const padding = Math.max(0, Math.round(Number(paddingFrames) || 0));
    const before = Math.max(0, Math.round(Number(beforeTarget) || 0));
    const after = Math.max(0, Math.round(Number(afterTarget) || 0));
    if (!padding) return { before: 0, after: 0 };
    if (!before && !after) return { before: 0, after: padding };
    if (!before) return { before: 0, after: padding };
    if (!after) return { before: padding, after: 0 };
    if (padding === 1) return { before: 0, after: 1 };
    const allocatedBefore = Math.max(1, Math.min(
        padding - 1,
        Math.round(padding * before / (before + after)),
    ));
    return { before: allocatedBefore, after: padding - allocatedBefore };
}

function alphaSuffix(index) {
    let value = Math.max(0, Math.floor(Number(index) || 0)) + 1;
    let result = "";
    while (value > 0) {
        value -= 1;
        result = String.fromCharCode(97 + value % 26) + result;
        value = Math.floor(value / 26);
    }
    return result;
}

function audioUrl(descriptor) {
    if (!descriptor?.filename) return "";
    const params = new URLSearchParams({
        filename: descriptor.filename,
        subfolder: descriptor.subfolder || "",
        type: descriptor.type || "temp",
    });
    return api.apiURL(`/view?${params.toString()}`);
}

function assetSourceKey(descriptor) {
    if (!descriptor?.filename) return "";
    return `${descriptor.type || "input"}:${descriptor.subfolder || ""}/${descriptor.filename}`;
}

function fileSizeLabel(bytes) {
    const value = Math.max(0, Number(bytes) || 0);
    if (value < 1024) return `${value} B`;
    if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`;
    if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`;
    return `${(value / 1024 ** 3).toFixed(1)} GB`;
}

function filenameTitle(filename) {
    return String(filename || "").replace(/\.[^.]+$/, "") || "未命名资产";
}

function markGraphChanged(node) {
    node.graph?.setDirtyCanvas?.(true, true);
    app.graph?.setDirtyCanvas?.(true, true);
}

function hideInternalWidget(widget) {
    if (!widget) return;
    widget._xiaoyueOriginalType ??= widget.type;
    widget.type = "hidden";
    widget.computeSize = () => [0, -4];
}

function parseJsonObject(raw) {
    if (!raw || typeof raw !== "string") return null;
    try {
        const value = JSON.parse(raw);
        return value && typeof value === "object" && !Array.isArray(value)
            ? value
            : null;
    } catch {
        return null;
    }
}

function looksLikeMvPlan(value) {
    return Boolean(value && typeof value === "object" && (
        Array.isArray(value.shots)
        || value.h3
        || value.transitions
        || value.automatic_plan_basis
    ));
}

function legacyPlanFromNodeInfo(node, info) {
    const candidates = [
        info?.widgets_values_named?.plan_json,
        ...(Array.isArray(info?.widgets_values) ? [...info.widgets_values].reverse() : []),
        planWidget(node)?.value,
    ];
    for (const raw of candidates) {
        const parsed = typeof raw === "object" && raw !== null
            ? raw
            : parseJsonObject(raw);
        if (looksLikeMvPlan(parsed)) return parsed;
    }
    return null;
}

function linkById(graph, linkId) {
    if (linkId === null || linkId === undefined) return null;
    return graph?.links?.[linkId] ?? graph?.links?.get?.(linkId) ?? null;
}

function sourceNodeForInput(graph, input) {
    const link = linkById(graph, input?.link);
    return link ? graph?.getNodeById?.(link.origin_id) : null;
}

function nodeHasOutgoingLinks(graph, node) {
    if (!graph || !node) return false;
    const links = graph.links instanceof Map
        ? [...graph.links.values()]
        : Object.values(graph.links || {});
    return links.some((link) => link && String(link.origin_id) === String(node.id));
}

function isPlainLoadAudioNode(node) {
    const type = String(node?.comfyClass || node?.type || "");
    return type === "LoadAudio";
}

function projectStateFromLegacyNode(node) {
    if (!node) return null;
    const state = parseJsonObject(nodeWidget(node, "project_state_json")?.value);
    if (state?.project) return state;
    const projectId = String(nodeWidget(node, "project_id")?.value || "").trim();
    if (!projectId) return null;
    return {
        project: {
            project_id: projectId,
            project_code: String(nodeWidget(node, "project_code")?.value || ""),
            project_name: String(nodeWidget(node, "project_name")?.value || ""),
        },
    };
}

function validProjectId(value) {
    const text = String(value || "").trim();
    return text && !text.startsWith("{") && !text.startsWith("[") && text.length >= 8
        ? text
        : "";
}

function migrateLegacyPlannerNode(node, info) {
    if (node._xiaoyueLegacyInputsMigrated) return;
    const serializedInputs = Array.isArray(info?.inputs) ? info.inputs : [];
    const hasLegacyInputs = [...(node.inputs || []), ...serializedInputs]
        .some((input) => LEGACY_PLANNER_INPUT_NAMES.has(input?.name));
    if (!hasLegacyInputs) return;
    node._xiaoyueLegacyInputsMigrated = true;

    const graph = node.graph || app.graph;
    const legacySources = new Map();
    for (const input of node.inputs || []) {
        if (LEGACY_PLANNER_INPUT_NAMES.has(input?.name)) {
            legacySources.set(input.name, sourceNodeForInput(graph, input));
        }
    }
    const legacyProjectNode = legacySources.get("project");
    const legacyProjectState = projectStateFromLegacyNode(legacyProjectNode);
    const loadAudioCandidates = new Set();
    for (const name of ["audio", "vocals_audio"]) {
        const source = legacySources.get(name);
        if (isPlainLoadAudioNode(source)) loadAudioCandidates.add(source);
    }
    if (legacyProjectNode) {
        for (const input of legacyProjectNode.inputs || []) {
            const source = sourceNodeForInput(graph, input);
            if (isPlainLoadAudioNode(source)) loadAudioCandidates.add(source);
        }
    }

    const plan = legacyPlanFromNodeInfo(node, info);
    const named = info?.widgets_values_named || {};
    const stateProject = legacyProjectState?.project || {};
    const planProject = plan?.project || {};
    const projectId = validProjectId(
        named.project_id || stateProject.project_id || stateProject.id
        || planProject.id || planProject.project_id,
    );
    const projectCode = String(
        named.project_code || stateProject.project_code || stateProject.code
        || planProject.code || planProject.project_code || "",
    );
    const projectName = String(
        named.project_name || stateProject.project_name || stateProject.name
        || planProject.name || planProject.project_name || "",
    );

    graph?.beforeChange?.(node);
    try {
        for (let index = (node.inputs?.length || 0) - 1; index >= 0; index -= 1) {
            if (LEGACY_PLANNER_INPUT_NAMES.has(node.inputs[index]?.name)) {
                node.removeInput(index);
            }
        }

        if (plan) planWidget(node).value = JSON.stringify(plan, null, 2);
        else if (planWidget(node)) planWidget(node).value = "";
        if (nodeWidget(node, "project_id")) nodeWidget(node, "project_id").value = projectId;
        if (nodeWidget(node, "project_code")) nodeWidget(node, "project_code").value = projectId ? projectCode : "";
        if (nodeWidget(node, "project_name")) nodeWidget(node, "project_name").value = projectId ? projectName : "";
        if (nodeWidget(node, "project_state_json")) {
            nodeWidget(node, "project_state_json").value = projectId && legacyProjectState
                ? JSON.stringify(legacyProjectState, null, 2)
                : "";
        }

        if (
            legacyProjectNode
            && String(legacyProjectNode.comfyClass || legacyProjectNode.type || "") === LEGACY_PROJECT_NODE_NAME
            && !nodeHasOutgoingLinks(graph, legacyProjectNode)
        ) {
            graph?.remove?.(legacyProjectNode);
        }
        for (const candidate of loadAudioCandidates) {
            if (!nodeHasOutgoingLinks(graph, candidate)) graph?.remove?.(candidate);
        }

        if (/\u5206\u955c\u89c4\u5212|^\s*③/.test(String(node.title || ""))) {
            node.title = UNIFIED_WORKBENCH_TITLE;
        }
        updateProjectNodeLabel(node);
        updateNodeLabel(node, plan);
        markGraphChanged(node);
    } finally {
        graph?.afterChange?.(node);
    }

    app.extensionManager?.toast?.add?.({
        severity: "info",
        summary: "旧 MV 工作流已自动收口",
        detail: projectId
            ? "已保留项目与镜头计划；主节点现在只保留 ASR 和推理模型两个工具接口。"
            : "已移除旧项目、音频、干声和字幕接口；请在主节点内选择历史项目。",
        life: 7000,
    });
}

function migrateSplitWorkbench(node) {
    // Old saved canvases contain two internal bridges. Merge only the exact
    // project-to-shot/preview pattern; never consume a user-customized edge.
    const graph = node.graph || app.graph;
    if (!graph?._nodes || node._xiaoyueSplitMigrated) return;
    const fromThis = (candidate, name) => sourceNodeForInput(
        graph, candidate.inputs?.find((input) => input.name === name)) === node;
    const shots = graph._nodes.filter((candidate) =>
        candidate.type === SHOT_INPUT_NODE_NAME
        && fromThis(candidate, "mv_plan")
        && fromThis(candidate, "project")
        && fromThis(candidate, "master_audio"));
    if (shots.length > 1) return;
    const shot = shots[0];
    const previews = graph._nodes.filter((candidate) =>
        candidate.type === PREVIEW_NODE_NAME && fromThis(candidate, "mv_plan")
        && !nodeHasOutgoingLinks(graph, candidate));
    if (!shot && !previews.length) return;
    if (shot) {
        const seedInput = shot.inputs?.find((input) => input.name === "seed");
        const seedWidget = shot.widgets?.find((widget) => widget.name === "seed");
        const mainSeed = node.widgets?.find((widget) => widget.name === "seed");
        const expectedInputs = new Set(["mv_plan", "project", "master_audio", "seed"]);
        if (shot.outputs?.length !== SHOT_OUTPUT_COUNT
                || !mainSeed || seedInput?.link != null
                || shot.inputs?.some((input) => !expectedInputs.has(input.name))
                || (node.outputs?.length !== PLANNER_OUTPUT_COUNT
                    && node.outputs?.length !== PLANNER_OUTPUT_COUNT + SHOT_OUTPUT_COUNT)) return;
        if (node.outputs.length === PLANNER_OUTPUT_COUNT + SHOT_OUTPUT_COUNT
                && shot.outputs.some((output, index) =>
                    node.outputs[PLANNER_OUTPUT_COUNT + index]?.name !== output.name)) return;
        const allLinksValid = shot.outputs.every((output) =>
            (output.links || []).every((id) => {
                const link = linkById(graph, id);
                return link && graph.getNodeById?.(link.target_id)?.inputs?.[link.target_slot];
            }));
        if (!allLinksValid) return;
        const transfers = shot.outputs.flatMap((output, index) =>
            (output.links || []).map((id) => {
                const link = linkById(graph, id);
                return { index, target: graph.getNodeById(link.target_id),
                         slot: link.target_slot };
            }));
        const previousSeed = mainSeed.value;
        const mainControl = node.widgets?.find((widget) => widget.name === "control_after_generate");
        const oldControl = shot.widgets?.find((widget) => widget.name === "control_after_generate");
        const previousControl = mainControl?.value;
        const addedOutputs = node.outputs.length === PLANNER_OUTPUT_COUNT;
        graph.beforeChange?.(node);
        try {
            if (addedOutputs) {
                for (const output of shot.outputs) node.addOutput(output.name, output.type);
            }
            if (seedWidget) mainSeed.value = seedWidget.value;
            if (mainControl && oldControl) mainControl.value = oldControl.value;
            for (const { index, target, slot } of transfers) {
                node.connect(PLANNER_OUTPUT_COUNT + index, target, slot);
                const attached = linkById(graph, target.inputs?.[slot]?.link);
                if (!attached || String(attached.origin_id) !== String(node.id)
                        || Number(attached.origin_slot) !== PLANNER_OUTPUT_COUNT + index) {
                    throw new Error("本镜输出转接失败；旧节点已保留。");
                }
            }
            node.properties ||= {};
            node.properties.xiaoyue_mv_backbone_id = 130;
            graph.remove(shot);
            for (const preview of previews) graph.remove(preview);
            node._xiaoyueSplitMigrated = true;
            const minimum = node.computeSize?.();
            if (minimum) node.setSize([Math.max(node.size?.[0] || 0, 500),
                                       Math.max(node.size?.[1] || 0, minimum[1])]);
            markGraphChanged(node);
        } catch (error) {
            for (const { index, target, slot } of transfers) {
                shot.connect(index, target, slot);
            }
            if (addedOutputs && node.outputs?.length === PLANNER_OUTPUT_COUNT + SHOT_OUTPUT_COUNT) {
                for (let index = SHOT_OUTPUT_COUNT - 1; index >= 0; index -= 1) {
                    node.removeOutput(PLANNER_OUTPUT_COUNT + index);
                }
            }
            mainSeed.value = previousSeed;
            if (mainControl) mainControl.value = previousControl;
            app.extensionManager?.toast?.add?.({
                severity: "warn", summary: "旧工作流暂未合并",
                detail: error?.message || String(error), life: 7000,
            });
            return;
        } finally {
            graph.afterChange?.(node);
        }
    } else {
        graph.beforeChange?.(node);
        try {
            for (const preview of previews) graph.remove(preview);
            node._xiaoyueSplitMigrated = true;
            markGraphChanged(node);
        } finally {
            graph.afterChange?.(node);
        }
    }
    app.extensionManager?.toast?.add?.({
        severity: "info", summary: "MV 工作台已合并",
        detail: "本镜输入和验收入口已收进工作台；附件路由与用户生成链保持原接线。请保存工作流。",
        life: 7000,
    });
}

function updateNodeLabel(node, plan = parsePlan(node)) {
    const button = node._xiaoyuePlannerButton;
    if (!button) return;
    if (!plan?.shots?.length) {
        button.name = "② MV 工作台 · 请先运行一次";
        return;
    }
    const pending = plan.needs_compile ? " · 待编译" : "";
    button.name = `② MV 工作台 · ${plan.shots.length} 段${pending}`;
}

function updateProjectNodeLabel(node, state = parseProjectNodeState(node)) {
    const button = node._xiaoyueProjectButton;
    if (!button) return;
    const project = state?.project;
    button.name = project?.project_code
        ? `① 项目设定 · ${project.project_code}`
        : "① 项目设定";
}

function orderWorkbenchButtons(node) {
    const widgets = node.widgets;
    if (!Array.isArray(widgets)) return;
    const buttons = [
        node._xiaoyueProjectButton,
        node._xiaoyuePlannerButton,
        widgets.find((widget) => widget.name === "③ 剪辑台"),
    ].filter(Boolean);
    for (const button of buttons) {
        const index = widgets.indexOf(button);
        if (index >= 0) widgets.splice(index, 1);
    }
    widgets.push(...buttons);
}

function transitionGeometry(transition) {
    const at = Number(transition.at_frame || 0);
    delete transition.context_range;
    delete transition.active_range;
    delete transition.left_safety_range;
    delete transition.right_safety_range;
    delete transition.previous_required_until_frame;
    delete transition.next_required_from_frame;
    if (transition.type === "long_take") {
        const context = Math.max(0, Number(transition.context_frames || 0));
        transition.context_range = [at - context, at];
    } else if (transition.type === "crossfade") {
        const duration = Math.max(2, Number(transition.duration_frames || 12));
        const legacySafety = Math.max(0, Number(transition.safety_frames || 0));
        const beforeSafety = Math.max(0, Number(
            transition.before_safety_frames ?? legacySafety,
        ));
        const afterSafety = Math.max(0, Number(
            transition.after_safety_frames ?? legacySafety,
        ));
        const before = Math.floor(duration / 2);
        const after = duration - before;
        transition.active_range = [at - before, at + after];
        transition.left_safety_range = [at - before - beforeSafety, at - before];
        transition.right_safety_range = [at + after, at + after + afterSafety];
        transition.previous_required_until_frame = at + after + afterSafety;
        transition.next_required_from_frame = at - before - beforeSafety;
        transition.before_safety_frames = beforeSafety;
        transition.after_safety_frames = afterSafety;
        transition.safety_frames = Math.max(beforeSafety, afterSafety);
        transition.safety_mode = "auto_17n5";
    }
}

function newTransition(plan, type, atFrame) {
    const settings = plan.settings || {};
    const transition = {
        type: type || settings.default_transition || "hard_cut",
        at_frame: Math.round(Number(atFrame) || 0),
        at_seconds: Number(frameSeconds(atFrame, plan.h3?.fps || 24).toFixed(6)),
        context_frames: Math.max(0, Number(settings.continuity_frames ?? 22)),
        duration_frames: Math.max(2, Number(settings.crossfade_frames ?? 12)),
        safety_frames: 0,
        before_safety_frames: 0,
        after_safety_frames: 0,
        safety_mode: "auto_17n5",
        note: "",
    };
    transitionGeometry(transition);
    return transition;
}

function updateShotClock(plan, shot) {
    const fps = plan.h3?.fps || 24;
    const sampleRate = plan.audio?.sample_rate || 48000;
    const totalSamples = plan.audio?.total_samples || Number.MAX_SAFE_INTEGER;
    shot.keep_frames = shot.end_frame - shot.start_frame;
    shot.start_seconds = Number((shot.start_frame / fps).toFixed(6));
    shot.end_seconds = Number((shot.end_frame / fps).toFixed(6));
    shot.duration_seconds = Number((shot.keep_frames / fps).toFixed(6));
    shot.start_sample = Math.round(shot.start_frame * sampleRate / fps);
    shot.end_sample = Math.min(totalSamples, Math.round(shot.end_frame * sampleRate / fps));
    normalizeShotSubtitleTrack(shot);
}

function normalizeShotSubtitleTrack(shot) {
    const source = shot?.subtitle_track && typeof shot.subtitle_track === "object"
        ? shot.subtitle_track
        : {};
    const start = Math.max(0, Math.round(Number(shot?.start_frame) || 0));
    const end = Math.max(start, Math.round(Number(shot?.end_frame) || start));
    shot.subtitle_track = {
        leading_text: singleLineSubtitleText(source.leading_text ?? ""),
        text: singleLineSubtitleText(source.text ?? shot?.subtitle_text ?? ""),
        trailing_text: singleLineSubtitleText(source.trailing_text ?? ""),
        revision: Math.max(0, Math.round(Number(source.revision) || 0)),
        absolute_start_frame: start,
        absolute_end_frame: end,
        relative_start_frame: 0,
        relative_end_frame: end - start,
        reference_cue_ids: Array.isArray(shot?.subtitle_ids)
            ? [...shot.subtitle_ids]
            : Array.isArray(source.reference_cue_ids) ? [...source.reference_cue_ids] : [],
        timing_basis: "shot_boundaries",
    };
    delete shot.subtitle_text;
    return shot.subtitle_track;
}

function mergeShotSubtitleText(earlier, later) {
    const first = singleLineSubtitleText(earlier);
    const second = singleLineSubtitleText(later);
    if (!first) return second;
    if (!second || first === second) return first;
    return `${first} ${second}`;
}

class ProjectEditor {
    constructor(node) {
        installStyles();
        projectEditorByNode.get(node)?.destroy();
        projectEditorByNode.set(node, this);
        this.node = node;
        this.savedState = parseProjectNodeState(node);
        this.projects = [];
        this.inputAssetCatalog = [];
        this.selectedInputAssetKeys = new Set();
        this.pendingAudioFiles = { master: null, vocals: null };
        this.skillTemplateLibrary = { visual_outline: [], h3_prompt: [] };
        this.draft = this.projectFromNode();
        this.dialog = this.buildDialog();
        this.bindEvents();
        document.body.appendChild(this.dialog);
        this.dialog.showModal();
        this.renderDraft();
        this.loadProjects();
        this.loadSkillTemplateLibrary();
    }

    projectFromNode() {
        const saved = this.savedState?.project || {};
        return {
            project_id: String(saved.project_id || widgetValue(this.node, "project_id") || newProjectId()),
            project_name: String(saved.project_name || widgetValue(this.node, "project_name") || ""),
            project_code: String(saved.project_code || widgetValue(this.node, "project_code") || ""),
            revision: Number(saved.revision || 0),
            created_at: String(saved.created_at || ""),
            updated_at: String(saved.updated_at || ""),
            storage: saved.storage || null,
            has_plan: Boolean(saved.has_plan),
            has_audio: Boolean(saved.audio?.master?.fingerprint || saved.audio?.fingerprint || saved.has_audio),
            has_vocals: Boolean(saved.audio?.vocals?.fingerprint || saved.has_vocals),
            project_brief: {
                description: String(saved.project_brief?.description || ""),
                overall_prompt: String(saved.project_brief?.overall_prompt || ""),
                negative_prompt: String(saved.project_brief?.negative_prompt || ""),
            },
            planning_settings: {
                target_shot_seconds: Number(saved.planning_settings?.target_shot_seconds || 9.0),
                h3_max_frames: Number(saved.planning_settings?.h3_max_frames || 226),
            },
            generation_spec: normalizeGenerationSpec(saved.generation_spec),
            skill_templates: normalizeSkillTemplateSelection(saved.skill_templates),
            lyrics: {
                source_text: String(saved.lyrics?.source_text || ""),
                format: String(saved.lyrics?.format || "auto"),
            },
            assets: Array.isArray(saved.assets) ? clone(saved.assets) : [],
            audio: saved.audio && typeof saved.audio === "object" ? clone(saved.audio) : {},
        };
    }

    buildDialog() {
        const dialog = document.createElement("dialog");
        const titleId = `xiaoyue-project-title-${String(this.node?.id ?? "new")}`;
        const pickerTitleId = `${titleId}-input-picker`;
        dialog.className = "xiaoyue-project-dialog";
        dialog.setAttribute("aria-labelledby", titleId);
        dialog.innerHTML = `
            <div class="xiaoyue-project-shell">
                <header class="xiaoyue-project-header">
                    <div>
                        <h2 id="${titleId}">MV 工作台 · 项目</h2>
                        <p>项目、声音、字幕与资产统一保存在这里；下一阶段直接进入同一节点的时间轴。</p>
                    </div>
                    <button type="button" class="xiaoyue-project-close" data-action="close" aria-label="关闭项目面板">关闭</button>
                </header>
                <main class="xiaoyue-project-main">
                    <form class="xiaoyue-project-form" action="" method="post" aria-labelledby="${titleId}-form">
                        <div class="xiaoyue-project-section-heading">
                            <div>
                                <h3 id="${titleId}-form">当前项目</h3>
                                <p>项目 ID 会固定在工作流里；改名或重启 ComfyUI 也不会改变存储归属。</p>
                            </div>
                            <button type="button" data-action="new-project">新建空项目</button>
                        </div>
                        <div class="xiaoyue-project-fields">
                            <label for="${titleId}-name">项目名称</label>
                            <input id="${titleId}-name" data-field="project-name" type="text" maxlength="120" autocomplete="off" placeholder="例：天长地久 MV">
                            <label for="${titleId}-code">项目编号</label>
                            <input id="${titleId}-code" data-field="project-code" type="text" maxlength="48" autocomplete="off" placeholder="例：TC99">
                            <small class="project-code-policy" data-field="project-code-policy">创建后锁定，用于后续全部生成文件归属。</small>
                            <label for="${titleId}-id">内部项目 ID</label>
                            <input id="${titleId}-id" data-field="project-id" type="text" readonly>
                        </div>
                        <div class="xiaoyue-project-brief-fields">
                            <label>项目整体描述
                                <textarea data-field="project-description" rows="3" placeholder="主题、情绪曲线、世界观与视觉方向"></textarea>
                            </label>
                            <label>总提示词
                                <textarea data-field="project-overall-prompt" rows="5" placeholder="全片共用的美术、摄影、色彩、表演与连续性要求"></textarea>
                            </label>
                            <label>全局禁用项
                                <textarea data-field="project-negative-prompt" rows="2" placeholder="全片统一避免的内容或画面问题"></textarea>
                            </label>
                        </div>
                        <fieldset class="xiaoyue-project-planning-settings">
                            <legend>自动分镜初稿</legend>
                            <label>目标镜头长度
                                <input data-field="target-shot-seconds" type="number" min="1" max="9.42" step="0.1" inputmode="decimal">
                                <small>自动找点的偏好长度；不会直接改动已经人工调整的镜头。</small>
                            </label>
                            <label>H3 单窗上限
                                <select data-field="h3-max-frames">
                                    ${H3_PROJECT_PRESETS.map((frames) => `<option value="${frames}">${h3ProjectPresetLabel(frames)}</option>`).join("")}
                                </select>
                                <small>只提供符合 17N+5 的 5–15 秒档位。</small>
                            </label>
                            <p class="xiaoyue-project-planning-lock" data-field="planning-settings-lock" hidden>已建立项目的打点参数已锁定。如需修改，请进入 MV 时间轴，在“重新自动找点”中确认清空镜头级数据后重建。</p>
                        </fieldset>
                        <fieldset class="xiaoyue-project-generation-spec">
                            <legend>影片生成规格</legend>
                            <label>影片长宽比
                                <select data-field="aspect-ratio">
                                    ${ASPECT_RATIO_OPTIONS.map((value) => `<option value="${value}">${value}</option>`).join("")}
                                </select>
                                <small>直接对应 ResolutionSelector 的官方画幅档。</small>
                            </label>
                            <label>一采目标 MP
                                <input data-field="first-pass-megapixels" type="number" min="0.1" max="16" step="0.1" inputmode="decimal">
                                <small>默认 0.4 MP，建立基础构图与运动。</small>
                            </label>
                            <label>二采目标 MP
                                <input data-field="second-pass-megapixels" type="number" min="0.1" max="16" step="0.1" inputmode="decimal">
                                <small>默认 0.9 MP，不得低于一采；后续传给 H3 Latent Upscaler。</small>
                            </label>
                        </fieldset>
                        <fieldset class="xiaoyue-project-generation-spec xiaoyue-project-skill-templates">
                            <legend>各阶段 Skill 模板</legend>
                            <label>整片视觉纲领
                                <select data-field="project-outline-skill"></select>
                            </label>
                            <label>单镜 H3 提示词
                                <select data-field="project-h3-skill"></select>
                            </label>
                            <div class="xiaoyue-project-skill-library-tools">
                                <button type="button" data-action="refresh-skill-templates">刷新 Skill 列表</button>
                                <small data-field="skill-template-help" role="status">默认与自定义 Skill 均从本插件目录读取…</small>
                            </div>
                        </fieldset>
                        <fieldset class="xiaoyue-project-sources">
                            <legend>声音与歌词</legend>
                            <div class="source-row required" data-source="master">
                                <strong>母带音乐</strong><span data-field="master-status">未导入</span>
                                <button type="button" data-action="choose-master-audio">导入／替换</button>
                                <input data-field="master-audio-file" type="file" accept="audio/*,video/*,.mkv" hidden>
                                <small>必填 · 保存在项目内 · 全片唯一时间基准</small>
                            </div>
                            <div class="source-row" data-source="vocals">
                                <strong>对齐干声</strong><span data-field="vocals-status">未导入</span>
                                <button type="button" data-action="choose-vocals-audio">导入／替换</button>
                                <input data-field="vocals-audio-file" type="file" accept="audio/*,video/*,.mkv" hidden>
                                <small>可选 · 保存在项目内 · 与母带同一零点</small>
                            </div>
                            <label class="lyrics-field">歌词／字幕
                                <span class="xiaoyue-project-inline-action">
                                    <button type="button" data-action="choose-lyrics-file">读取 LRC／SRT／VTT／TXT</button>
                                    <input data-field="lyrics-file" type="file" accept=".lrc,.srt,.vtt,.txt,text/plain" hidden>
                                </span>
                                <textarea data-field="project-lyrics" rows="5" placeholder="可粘贴歌词、LRC、SRT、VTT 或时间戳文本"></textarea>
                            </label>
                            <p>声音和字幕都归项目统一管理；打开历史项目不会读取画布外的文件节点。</p>
                        </fieldset>
                        <details class="xiaoyue-project-storage">
                            <summary>项目目录</summary>
                            <output data-field="storage-path" class="xiaoyue-project-storage-path">创建后显示项目目录</output>
                        </details>
                        <output class="xiaoyue-project-status" data-field="status" role="status" aria-live="polite"></output>
                        <div class="xiaoyue-project-primary-actions">
                            <button type="submit" data-action="save-project" class="primary">创建并使用此项目</button>
                        </div>
                    </form>
                    <aside class="xiaoyue-project-side">
                        <section class="xiaoyue-project-assets" aria-labelledby="${titleId}-assets">
                            <div class="xiaoyue-project-section-heading compact">
                                <div>
                                    <h3 id="${titleId}-assets">项目公用资产</h3>
                                    <p><span data-field="asset-count">0</span> 个 · 只登记身份，镜头调用顺序以后单独绑定。</p>
                                </div>
                                <div class="xiaoyue-project-asset-actions">
                                    <button type="button" data-action="upload-assets">导入本地</button>
                                    <button type="button" data-action="browse-input-assets">从 input 选择</button>
                                    <input data-field="asset-file-input" type="file" accept="image/*,video/*,.mkv" multiple hidden>
                                </div>
                            </div>
                            <output class="xiaoyue-project-asset-status" data-field="asset-status" role="status">导入后逐一声明为角色、场景或道具。</output>
                            <div class="xiaoyue-project-asset-list" data-field="asset-list"></div>
                        </section>
                        <section class="xiaoyue-project-recents" aria-labelledby="${titleId}-recent">
                        <div class="xiaoyue-project-section-heading">
                            <div>
                                <h3 id="${titleId}-recent">最近项目</h3>
                                <p>打开后只切换当前节点归属，不会删除其他项目。</p>
                            </div>
                            <button type="button" data-action="refresh-projects">刷新</button>
                        </div>
                        <div class="xiaoyue-project-list" data-field="project-list" aria-live="polite"></div>
                        </section>
                    </aside>
                </main>
                <dialog class="xiaoyue-mv-input-picker" aria-labelledby="${pickerTitleId}">
                    <header><div><h3 id="${pickerTitleId}">从 ComfyUI input 选择资产</h3><p>可多选；已登记的文件不会重复加入。</p></div><button type="button" data-action="close-input-picker" aria-label="关闭 input 选择器">×</button></header>
                    <label class="xiaoyue-mv-input-search">搜索 input<input data-field="input-asset-search" type="search" placeholder="文件名或子目录"></label>
                    <div class="xiaoyue-mv-input-asset-list" data-field="input-asset-list"></div>
                    <footer><output data-field="input-picker-status" role="status"></output><button type="button" class="primary" data-action="confirm-input-assets">导入已选资产</button></footer>
                </dialog>
            </div>`;
        return dialog;
    }

    field(name) {
        return this.dialog.querySelector(`[data-field="${name}"]`);
    }

    bindEvents() {
        this.dialog.querySelector('[data-action="close"]').addEventListener("click", () => this.destroy());
        this.dialog.addEventListener("cancel", (event) => {
            event.preventDefault();
            this.destroy();
        });
        this.dialog.querySelector('[data-action="new-project"]').addEventListener("click", () => {
            this.pendingAudioFiles = { master: null, vocals: null };
            this.draft = {
                project_id: newProjectId(), project_name: "", project_code: "",
                revision: 0, storage: null, has_plan: false, has_audio: false,
                has_vocals: false,
                project_brief: { description: "", overall_prompt: "", negative_prompt: "" },
                planning_settings: { target_shot_seconds: 9.0, h3_max_frames: 226 },
                generation_spec: clone(DEFAULT_GENERATION_SPEC),
                skill_templates: normalizeSkillTemplateSelection(null),
                lyrics: { source_text: "", format: "auto" },
                assets: [], audio: {},
            };
            this.renderDraft();
            this.field("project-name").focus();
        });
        this.dialog.querySelector('[data-action="refresh-projects"]')
            .addEventListener("click", () => this.loadProjects());
        this.dialog.querySelector(".xiaoyue-project-form").addEventListener("submit", (event) => {
            event.preventDefault();
            this.saveProject();
        });
        this.field("project-name").addEventListener("input", (event) => {
            this.draft.project_name = event.target.value;
        });
        this.field("project-code").addEventListener("input", (event) => {
            this.draft.project_code = event.target.value;
        });
        for (const [fieldName, key] of [
            ["project-description", "description"],
            ["project-overall-prompt", "overall_prompt"],
            ["project-negative-prompt", "negative_prompt"],
        ]) {
            this.field(fieldName).addEventListener("input", (event) => {
                this.draft.project_brief[key] = event.target.value;
            });
        }
        this.field("target-shot-seconds").addEventListener("input", (event) => {
            this.draft.planning_settings.target_shot_seconds = Number(event.target.value || 9.0);
        });
        this.field("h3-max-frames").addEventListener("change", (event) => {
            const frames = Number(event.target.value || 226);
            this.draft.planning_settings.h3_max_frames = frames;
            const target = this.field("target-shot-seconds");
            const maximum = frames / 24;
            target.max = maximum.toFixed(3);
            if (Number(target.value) > maximum) {
                target.value = maximum.toFixed(2);
                this.draft.planning_settings.target_shot_seconds = Number(target.value);
            }
        });
        this.field("aspect-ratio").addEventListener("change", (event) => {
            this.draft.generation_spec.aspect_ratio = event.target.value;
        });
        this.field("first-pass-megapixels").addEventListener("change", () => {
            this.updateDraftGenerationSpec();
        });
        this.field("second-pass-megapixels").addEventListener("change", () => {
            this.updateDraftGenerationSpec();
        });
        for (const [stage, fieldName] of [
            ["visual_outline", "project-outline-skill"],
            ["h3_prompt", "project-h3-skill"],
        ]) {
            this.field(fieldName).addEventListener("change", (event) => {
                this.draft.skill_templates[stage] = event.target.value;
            });
        }
        this.dialog.querySelector('[data-action="refresh-skill-templates"]')
            .addEventListener("click", () => this.loadSkillTemplateLibrary());
        this.field("project-lyrics").addEventListener("input", (event) => {
            this.draft.lyrics.source_text = event.target.value;
        });
        for (const role of ["master", "vocals"]) {
            const action = role === "master" ? "choose-master-audio" : "choose-vocals-audio";
            const field = role === "master" ? "master-audio-file" : "vocals-audio-file";
            this.dialog.querySelector(`[data-action="${action}"]`).addEventListener("click", () => {
                this.field(field).click();
            });
            this.field(field).addEventListener("change", (event) => {
                const file = event.target.files?.[0];
                event.target.value = "";
                if (file) this.stageAudioFile(role, file);
            });
        }
        this.dialog.querySelector('[data-action="choose-lyrics-file"]').addEventListener("click", () => {
            this.field("lyrics-file").click();
        });
        this.field("lyrics-file").addEventListener("change", async (event) => {
            const file = event.target.files?.[0];
            event.target.value = "";
            if (!file) return;
            try {
                const text = await file.text();
                this.draft.lyrics.source_text = text.replace(/^\uFEFF/, "");
                this.draft.lyrics.format = file.name.split(".").pop()?.toLowerCase() || "auto";
                this.field("project-lyrics").value = this.draft.lyrics.source_text;
                this.setStatus(`已读取字幕文件 ${file.name}；保存项目后生效。`, "success");
            } catch (error) {
                this.setStatus(`读取字幕文件失败：${error?.message || String(error)}`, "error");
            }
        });
        this.dialog.querySelector('[data-action="upload-assets"]').addEventListener("click", () => {
            this.field("asset-file-input").click();
        });
        this.field("asset-file-input").addEventListener("change", (event) => {
            this.uploadLocalAssets([...event.target.files]);
            event.target.value = "";
        });
        this.dialog.querySelector('[data-action="browse-input-assets"]')
            .addEventListener("click", () => this.openInputAssetPicker());
        this.dialog.querySelector('[data-action="close-input-picker"]')
            .addEventListener("click", () => this.closeInputAssetPicker());
        this.dialog.querySelector('[data-action="confirm-input-assets"]')
            .addEventListener("click", () => this.importSelectedInputAssets());
        this.field("input-asset-search").addEventListener("input", () => this.renderInputAssetCatalog());
        this.field("input-asset-list").addEventListener("change", (event) => {
            const checkbox = event.target.closest?.("[data-input-asset-key]");
            if (!checkbox) return;
            if (checkbox.checked) this.selectedInputAssetKeys.add(checkbox.dataset.inputAssetKey);
            else this.selectedInputAssetKeys.delete(checkbox.dataset.inputAssetKey);
            this.updateInputPickerStatus();
        });
        this.dialog.querySelector(".xiaoyue-mv-input-picker").addEventListener("cancel", (event) => {
            event.preventDefault();
            this.closeInputAssetPicker();
        });
        this.field("asset-list").addEventListener("input", (event) => {
            const control = event.target.closest?.("[data-asset-field]");
            const card = event.target.closest?.("[data-asset-id]");
            if (!control || !card) return;
            const asset = this.draft.assets.find((item) => item.id === card.dataset.assetId);
            if (asset) asset[control.dataset.assetField] = event.target.value;
        });
        this.field("asset-list").addEventListener("click", (event) => {
            const button = event.target.closest?.("[data-asset-action='remove']");
            const card = event.target.closest?.("[data-asset-id]");
            if (!button || !card) return;
            this.draft.assets = this.draft.assets.filter((item) => item.id !== card.dataset.assetId);
            this.renderProjectAssets();
        });
    }

    setStatus(text, kind = "") {
        const output = this.field("status");
        output.textContent = text;
        output.dataset.kind = kind;
    }

    stageAudioFile(role, file) {
        const current = this.draft.audio?.[role];
        if (current?.fingerprint) {
            const label = role === "master" ? "母带" : "干声";
            const impact = role === "master"
                ? "替换母带会使当前镜头切点、连接、镜头资产绑定和镜头提示词进入待重规划状态。"
                : "替换干声会要求重新检查 ASR 和字幕对齐，但不会自动移动镜头切点。";
            if (!window.confirm(`${label}已经归档在项目中。\n${impact}\n\n是否选择新文件作为待更新版本？`)) {
                return;
            }
        }
        this.pendingAudioFiles[role] = file;
        this.renderAudioSources();
        this.setStatus(
            `${role === "master" ? "母带" : "干声"} ${file.name} 已暂存；点击保存项目后写入项目目录。`,
            "success",
        );
    }

    renderAudioSources() {
        for (const role of ["master", "vocals"]) {
            const pending = this.pendingAudioFiles[role];
            const item = this.draft.audio?.[role] || {};
            const sourceName = item.source?.original_name || item.source?.path || "";
            const status = pending
                ? `待保存：${pending.name}`
                : sourceName
                    ? `项目内：${sourceName}`
                    : item.fingerprint
                        ? "已有旧记录 · 待安全归档"
                        : "未导入";
            this.field(`${role}-status`).textContent = status;
            this.dialog.querySelector(`[data-source="${role}"]`).dataset.connected = String(
                Boolean(pending || item.fingerprint),
            );
        }
    }

    renderDraft() {
        this.field("project-name").value = this.draft.project_name || "";
        this.field("project-code").value = this.draft.project_code || "";
        this.field("project-id").value = this.draft.project_id || "";
        this.draft.project_brief ||= { description: "", overall_prompt: "", negative_prompt: "" };
        this.draft.planning_settings ||= { target_shot_seconds: 9.0, h3_max_frames: 226 };
        this.draft.generation_spec = normalizeGenerationSpec(this.draft.generation_spec);
        this.draft.skill_templates = normalizeSkillTemplateSelection(this.draft.skill_templates);
        this.draft.lyrics ||= { source_text: "", format: "auto" };
        this.draft.assets = Array.isArray(this.draft.assets) ? this.draft.assets : [];
        this.field("project-description").value = this.draft.project_brief.description || "";
        this.field("project-overall-prompt").value = this.draft.project_brief.overall_prompt || "";
        this.field("project-negative-prompt").value = this.draft.project_brief.negative_prompt || "";
        const h3Frames = H3_PROJECT_PRESETS.includes(Number(this.draft.planning_settings.h3_max_frames))
            ? Number(this.draft.planning_settings.h3_max_frames)
            : 226;
        this.field("h3-max-frames").value = String(h3Frames);
        const targetSeconds = Math.max(1, Math.min(
            h3Frames / 24,
            Number(this.draft.planning_settings.target_shot_seconds || 9.0),
        ));
        this.field("target-shot-seconds").max = (h3Frames / 24).toFixed(3);
        this.field("target-shot-seconds").value = targetSeconds.toFixed(1);
        this.draft.planning_settings = {
            target_shot_seconds: targetSeconds,
            h3_max_frames: h3Frames,
        };
        this.field("aspect-ratio").value = this.draft.generation_spec.aspect_ratio;
        this.field("first-pass-megapixels").value = String(
            this.draft.generation_spec.first_pass_megapixels);
        this.field("second-pass-megapixels").value = String(
            this.draft.generation_spec.second_pass_megapixels);
        this.renderSkillTemplateOptions();
        this.field("project-lyrics").value = this.draft.lyrics.source_text || "";
        this.renderAudioSources();
        this.field("project-lyrics").disabled = false;
        this.field("project-lyrics").placeholder = "可粘贴歌词、LRC、SRT、VTT 或时间戳文本";
        const storage = this.draft.storage?.input;
        this.field("storage-path").textContent = storage
            ? `input/${storage}`
            : `input/xiaoyue_mv_projects/${this.draft.project_id}`;
        const saved = Number(this.draft.revision || 0) > 0;
        this.field("target-shot-seconds").disabled = saved;
        this.field("h3-max-frames").disabled = saved;
        this.dialog.querySelector(".xiaoyue-project-planning-settings").dataset.locked = String(saved);
        this.field("planning-settings-lock").hidden = !saved;
        const codeInput = this.field("project-code");
        codeInput.readOnly = saved;
        codeInput.setAttribute("aria-readonly", String(saved));
        codeInput.title = saved
            ? "历史项目编号已锁定；更换母带、干声、歌词或资产不会改变项目归属。"
            : "项目创建后编号将永久锁定。";
        this.field("project-code-policy").textContent = saved
            ? `已锁定为 ${this.draft.project_code}；本次只会更新该项目的资料。`
            : "创建后锁定，用于后续全部生成文件归属。";
        this.dialog.querySelector('[data-action="save-project"]').textContent = saved
            ? "保存项目资料"
            : "创建并使用此项目";
        this.setStatus(saved
            ? `已选择 ${this.draft.project_code} · revision ${this.draft.revision}`
            : "填写项目资料并连接母带后即可建立项目。");
        this.renderProjectAssets();
        this.renderProjects();
    }

    renderSkillTemplateOptions() {
        for (const [stage, fieldName] of [
            ["visual_outline", "project-outline-skill"],
            ["h3_prompt", "project-h3-skill"],
        ]) {
            const select = this.field(fieldName);
            const selected = this.draft.skill_templates[stage];
            select.replaceChildren();
            const options = Array.isArray(this.skillTemplateLibrary[stage])
                ? this.skillTemplateLibrary[stage] : [];
            for (const descriptor of options) {
                const option = document.createElement("option");
                option.value = String(descriptor.id || "");
                const source = descriptor.source === "bundled" ? " · 内置默认" : " · 插件目录";
                option.textContent = `${descriptor.display_name || descriptor.id} [${descriptor.id}]${descriptor.version && descriptor.version !== "unknown" ? ` · v${descriptor.version}` : ""}${descriptor.output_language ? ` · ${descriptor.output_language}` : ""}${source}`;
                select.append(option);
            }
            if (![...select.options].some((option) => option.value === selected)) {
                const option = document.createElement("option");
                option.value = selected;
                option.textContent = `${selected}（当前项目选择，模板库中未找到）`;
                select.prepend(option);
            }
            select.value = selected;
        }
    }

    async loadSkillTemplateLibrary() {
        try {
            const response = await api.fetchApi("/xiaoyue-mv/skill-templates");
            const data = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(data.error || "读取模板库失败。");
            this.skillTemplateLibrary = data.templates && typeof data.templates === "object"
                ? data.templates : { visual_outline: [], h3_prompt: [] };
            this.renderSkillTemplateOptions();
            this.field("skill-template-help").textContent =
                `Skill 目录：${data.folder_path || "本插件 skills 目录"}。默认模板随插件更新；新增自定义 Skill 后点“刷新 Skill 列表”，选择随项目保存。`;
        } catch (error) {
            this.field("skill-template-help").textContent =
                `模板库读取失败：${error?.message || String(error)}；当前项目选择仍可保存。`;
        }
    }

    updateDraftGenerationSpec() {
        const requested = normalizeGenerationSpec({
            aspect_ratio: this.field("aspect-ratio").value,
            first_pass_megapixels: this.field("first-pass-megapixels").value,
            second_pass_megapixels: this.field("second-pass-megapixels").value,
        });
        this.draft.generation_spec = requested;
        this.field("first-pass-megapixels").value = String(requested.first_pass_megapixels);
        this.field("second-pass-megapixels").value = String(requested.second_pass_megapixels);
    }

    async loadProjects() {
        const list = this.field("project-list");
        list.setAttribute("aria-busy", "true");
        list.textContent = "正在读取项目…";
        try {
            const response = await api.fetchApi("/xiaoyue-mv/projects?limit=100");
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || "读取项目失败。");
            this.projects = Array.isArray(data.projects) ? data.projects : [];
            this.renderProjects();
            if (Number(this.draft.revision || 0) > 0 && this.draft.project_id) {
                await this.selectProject(this.draft.project_id, { forceRestore: false });
            }
        } catch (error) {
            list.textContent = error?.message || String(error);
            this.setStatus("无法读取最近项目；可先检查 ComfyUI 服务日志。", "error");
        } finally {
            list.removeAttribute("aria-busy");
        }
    }

    renderProjects() {
        const list = this.field("project-list");
        if (!list) return;
        list.replaceChildren();
        if (!this.projects.length) {
            const empty = document.createElement("p");
            empty.className = "xiaoyue-project-empty";
            empty.textContent = "还没有已初始化的 MV 项目。";
            list.appendChild(empty);
            return;
        }
        for (const project of this.projects) {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "xiaoyue-project-row";
            button.classList.toggle("selected", project.project_id === this.draft.project_id);
            button.setAttribute("aria-pressed", String(project.project_id === this.draft.project_id));
            const identity = document.createElement("span");
            identity.className = "identity";
            const name = document.createElement("strong");
            name.textContent = project.project_name || "未命名项目";
            const code = document.createElement("span");
            code.textContent = project.project_code || "无编号";
            identity.append(name, code);
            const state = document.createElement("span");
            state.className = "state";
            state.textContent = `${project.has_audio ? "已有母带" : "待导入母带"} · ${project.has_plan ? "已有规划" : "待规划"}`;
            const revision = document.createElement("span");
            revision.className = "revision";
            revision.textContent = `r${project.revision}`;
            button.append(identity, state, revision);
            button.addEventListener("click", () => this.selectProject(
                project.project_id, { forceRestore: true }));
            list.appendChild(button);
        }
    }

    async selectProject(projectId, { forceRestore = true } = {}) {
        this.setStatus("正在读取项目完整资料…", "loading");
        try {
            const response = await api.fetchApi(`/xiaoyue-mv/projects/${encodeURIComponent(projectId)}`);
            const data = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(data.error || "读取项目失败。");
            const manifest = data.project || {};
            this.draft = {
                ...manifest,
                storage: manifest.storage || null,
                has_plan: Boolean(manifest.plan?.path),
                has_audio: Boolean(manifest.audio?.master?.fingerprint || manifest.audio?.fingerprint),
                has_vocals: Boolean(manifest.audio?.vocals?.fingerprint),
                project_brief: clone(manifest.project_brief || {}),
                planning_settings: clone(manifest.planning_settings || {
                    target_shot_seconds: 9.0, h3_max_frames: 226,
                }),
                generation_spec: normalizeGenerationSpec(manifest.generation_spec),
                skill_templates: normalizeSkillTemplateSelection(manifest.skill_templates),
                lyrics: clone(manifest.lyrics || { source_text: "", format: "auto" }),
                assets: clone(manifest.assets || []),
            };
            this.pendingAudioFiles = { master: null, vocals: null };
            this.activateProject(manifest, data.paths || {}, { forceRestore });
            this.renderDraft();
            this.setStatus(
                `已打开 ${manifest.project_code} · 历史项目已设为当前项目；直接关闭面板也可以运行。`,
                "success",
            );
        } catch (error) {
            this.setStatus(error?.message || String(error), "error");
        }
    }

    activateProject(manifest, paths = {}, { forceRestore = false } = {}) {
        const previousProjectId = String(widgetValue(this.node, "project_id") || "");
        const state = { project: manifest, paths };
        nodeWidget(this.node, "project_name").value = manifest.project_name;
        nodeWidget(this.node, "project_code").value = manifest.project_code;
        nodeWidget(this.node, "project_id").value = manifest.project_id;
        nodeWidget(this.node, "project_state_json").value = JSON.stringify(state, null, 2);
        this.savedState = state;
        if (forceRestore || (previousProjectId && previousProjectId !== manifest.project_id)) {
            const widget = planWidget(this.node);
            if (widget) {
                widget.value = "";
                widget.callback?.(widget.value, app.canvas, this.node, widget);
            }
            payloadByNode.delete(this.node);
            updateNodeLabel(this.node, null);
        }
        updateProjectNodeLabel(this.node, state);
        markGraphChanged(this.node);
    }

    async uploadProjectAudio(projectId, role, file) {
        const form = new FormData();
        form.append("project_id", projectId);
        form.append("role", role);
        form.append("file", file, file.name);
        const response = await api.fetchApi("/xiaoyue-mv/projects/audio/upload", {
            method: "POST",
            body: form,
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.error || `导入${role === "master" ? "母带" : "干声"}失败。`);
        return data;
    }

    nextAssetId() {
        const used = new Set(this.draft.assets.map((asset) => asset.id));
        const random = globalThis.crypto?.randomUUID?.().replaceAll("-", "")
            || `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
        let id = `asset_${random.slice(0, 12)}`;
        let suffix = 1;
        while (used.has(id)) id = `asset_${random.slice(0, 10)}_${suffix++}`;
        return id;
    }

    createAssetPreview(source, className = "") {
        const url = audioUrl(source);
        if (!url) {
            const empty = document.createElement("span");
            empty.className = `${className} asset-preview-empty`.trim();
            empty.textContent = "无预览";
            return empty;
        }
        if (source.media_type === "video") {
            const video = document.createElement("video");
            video.className = className;
            video.src = url;
            video.preload = "none";
            video.muted = true;
            video.playsInline = true;
            return video;
        }
        const image = document.createElement("img");
        image.className = className;
        image.src = url;
        image.alt = "";
        image.loading = "lazy";
        image.width = 144;
        image.height = 88;
        return image;
    }

    addAssetFromDescriptor(descriptor) {
        const key = assetSourceKey(descriptor);
        const existing = this.draft.assets.find((asset) => assetSourceKey(asset.source) === key);
        if (existing) return false;
        this.draft.assets.push({
            id: this.nextAssetId(),
            name: filenameTitle(descriptor.filename),
            category: "character",
            declaration: "",
            source: {
                filename: String(descriptor.filename || ""),
                subfolder: String(descriptor.subfolder || ""),
                type: "input",
                media_type: descriptor.media_type === "video" ? "video" : "image",
                size: Math.max(0, Number(descriptor.size) || 0),
                modified_ms: Math.max(0, Number(descriptor.modified_ms) || 0),
            },
        });
        return true;
    }

    renderProjectAssets() {
        const list = this.field("asset-list");
        if (!list) return;
        const assets = Array.isArray(this.draft.assets) ? this.draft.assets : [];
        this.field("asset-count").textContent = String(assets.length);
        this.dialog.querySelector('[data-action="upload-assets"]').disabled = !Number(this.draft.revision || 0);
        list.replaceChildren();
        if (!assets.length) {
            const empty = document.createElement("p");
            empty.className = "xiaoyue-project-empty";
            empty.textContent = "还没有公共资产。可从 input 选择；本地上传需先保存一次项目。";
            list.appendChild(empty);
            return;
        }
        const categoryNames = { character: "角色", scene: "场景", prop: "道具" };
        for (const asset of assets) {
            const card = document.createElement("article");
            card.className = "xiaoyue-project-asset-card";
            card.dataset.assetId = asset.id;
            const preview = this.createAssetPreview(asset.source, "asset-preview");
            const fields = document.createElement("div");
            fields.className = "asset-fields";
            const nameLabel = document.createElement("label");
            nameLabel.textContent = "名称";
            const name = document.createElement("input");
            name.type = "text";
            name.value = asset.name;
            name.dataset.assetField = "name";
            nameLabel.appendChild(name);
            const categoryLabel = document.createElement("label");
            categoryLabel.textContent = "类型";
            const category = document.createElement("select");
            category.dataset.assetField = "category";
            for (const [value, label] of Object.entries(categoryNames)) {
                const option = document.createElement("option");
                option.value = value;
                option.textContent = label;
                option.selected = asset.category === value;
                category.appendChild(option);
            }
            categoryLabel.appendChild(category);
            const declarationLabel = document.createElement("label");
            declarationLabel.className = "asset-declaration";
            declarationLabel.textContent = "统一声明";
            const declaration = document.createElement("textarea");
            declaration.rows = 2;
            declaration.value = asset.declaration || "";
            declaration.placeholder = "身份、外观、环境或全片必须保持的特征";
            declaration.dataset.assetField = "declaration";
            declarationLabel.appendChild(declaration);
            fields.append(nameLabel, categoryLabel, declarationLabel);
            const remove = document.createElement("button");
            remove.type = "button";
            remove.dataset.assetAction = "remove";
            remove.textContent = "移出";
            remove.setAttribute("aria-label", `将 ${asset.name} 移出项目资产库`);
            card.append(preview, fields, remove);
            list.appendChild(card);
        }
    }

    async uploadLocalAssets(files) {
        if (!files.length) return;
        const status = this.field("asset-status");
        if (!Number(this.draft.revision || 0)) {
            status.textContent = "请先保存一次项目，系统建立专属 assets 目录后再上传本地文件。";
            return;
        }
        const trigger = this.dialog.querySelector('[data-action="upload-assets"]');
        trigger.disabled = true;
        let imported = 0;
        try {
            for (let index = 0; index < files.length; index += 1) {
                status.textContent = `正在导入 ${index + 1}/${files.length}：${files[index].name}`;
                const form = new FormData();
                form.append("project_id", this.draft.project_id);
                form.append("file", files[index], files[index].name);
                const response = await api.fetchApi("/xiaoyue-mv/assets/upload", { method: "POST", body: form });
                const result = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(result.error || `导入 ${files[index].name} 失败`);
                if (this.addAssetFromDescriptor(result.asset)) imported += 1;
            }
            this.renderProjectAssets();
            status.textContent = `已导入 ${imported} 个新资产；保存项目后写入 project.json。`;
        } catch (error) {
            status.textContent = error?.message || String(error);
        } finally {
            trigger.disabled = false;
        }
    }

    async openInputAssetPicker() {
        const picker = this.dialog.querySelector(".xiaoyue-mv-input-picker");
        this.selectedInputAssetKeys.clear();
        this.field("input-asset-search").value = "";
        this.field("input-asset-list").replaceChildren();
        this.field("input-picker-status").textContent = "正在读取 input 目录…";
        if (!picker.open) picker.showModal();
        try {
            const response = await api.fetchApi("/xiaoyue-mv/assets/input?limit=1200");
            const result = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(result.error || "读取 input 失败。");
            this.inputAssetCatalog = Array.isArray(result.assets) ? result.assets : [];
            this.renderInputAssetCatalog();
        } catch (error) {
            this.inputAssetCatalog = [];
            this.field("input-picker-status").textContent = error?.message || String(error);
        }
    }

    closeInputAssetPicker() {
        const picker = this.dialog.querySelector(".xiaoyue-mv-input-picker");
        if (picker?.open) picker.close();
    }

    renderInputAssetCatalog() {
        const list = this.field("input-asset-list");
        const query = this.field("input-asset-search").value.trim().toLocaleLowerCase();
        const existing = new Set(this.draft.assets.map((asset) => assetSourceKey(asset.source)));
        const visible = this.inputAssetCatalog.filter((asset) => {
            const path = `${asset.subfolder || ""}/${asset.filename || ""}`.toLocaleLowerCase();
            return !query || path.includes(query);
        });
        list.replaceChildren();
        for (const asset of visible) {
            const key = assetSourceKey(asset);
            const row = document.createElement("label");
            row.className = "xiaoyue-mv-input-asset-row";
            row.classList.toggle("already-added", existing.has(key));
            const checkbox = document.createElement("input");
            checkbox.type = "checkbox";
            checkbox.dataset.inputAssetKey = key;
            checkbox.checked = this.selectedInputAssetKeys.has(key);
            checkbox.disabled = existing.has(key);
            const preview = asset.media_type === "image"
                ? this.createAssetPreview(asset, "input-preview")
                : document.createElement("span");
            if (asset.media_type === "video") {
                preview.className = "input-preview video-placeholder";
                preview.textContent = "VIDEO";
            }
            const copy = document.createElement("span");
            const name = document.createElement("strong");
            name.textContent = asset.filename;
            const meta = document.createElement("small");
            meta.textContent = `${asset.subfolder || "input 根目录"} · ${fileSizeLabel(asset.size)}`;
            copy.append(name, meta);
            row.append(checkbox, preview, copy);
            list.appendChild(row);
        }
        if (!visible.length) {
            const empty = document.createElement("p");
            empty.className = "xiaoyue-mv-input-empty";
            empty.textContent = query ? "没有匹配的 input 资产。" : "input 中没有可用图片或视频。";
            list.appendChild(empty);
        }
        this.updateInputPickerStatus(visible.length);
    }

    updateInputPickerStatus(visibleCount = null) {
        const count = visibleCount ?? this.field("input-asset-list").querySelectorAll("[data-input-asset-key]").length;
        this.field("input-picker-status").textContent = `${count} 个可见文件 · 已选 ${this.selectedInputAssetKeys.size} 个`;
    }

    importSelectedInputAssets() {
        let added = 0;
        for (const descriptor of this.inputAssetCatalog) {
            if (!this.selectedInputAssetKeys.has(assetSourceKey(descriptor))) continue;
            if (this.addAssetFromDescriptor(descriptor)) added += 1;
        }
        this.closeInputAssetPicker();
        this.renderProjectAssets();
        this.field("asset-status").textContent = added
            ? `已登记 ${added} 个 input 资产；保存项目后写入 project.json。`
            : "没有新资产需要导入。";
    }

    async saveProject() {
        const button = this.dialog.querySelector('[data-action="save-project"]');
        const projectName = this.field("project-name").value.trim();
        const projectCode = Number(this.draft.revision || 0) > 0
            ? String(this.draft.project_code || "").trim()
            : this.field("project-code").value.trim();
        if (!projectName || !projectCode) {
            this.setStatus("请同时填写项目名称和项目编号。", "error");
            (!projectName ? this.field("project-name") : this.field("project-code")).focus();
            return;
        }
        if (!this.draft.audio?.master?.fingerprint && !this.pendingAudioFiles.master) {
            this.setStatus("母带音乐是必填项：请在项目面板中导入母带文件。", "error");
            return;
        }
        button.disabled = true;
        button.setAttribute("aria-busy", "true");
        this.setStatus("正在建立项目目录与 project.json…", "loading");
        try {
            const response = await api.fetchApi("/xiaoyue-mv/projects/upsert", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    project_id: this.draft.project_id || newProjectId(),
                    project_name: projectName,
                    project_code: projectCode,
                    project_brief: this.draft.project_brief,
                    planning_settings: this.draft.planning_settings,
                    generation_spec: this.draft.generation_spec,
                    skill_templates: this.draft.skill_templates,
                    lyrics: this.draft.lyrics,
                    assets: this.draft.assets,
                }),
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || "初始化项目失败。");
            let manifest = data.project;
            let paths = data.paths || {};
            for (const role of ["master", "vocals"]) {
                const file = this.pendingAudioFiles[role];
                if (!file) continue;
                this.setStatus(
                    `正在把${role === "master" ? "母带" : "干声"}写入项目目录…`,
                    "loading",
                );
                const uploaded = await this.uploadProjectAudio(manifest.project_id, role, file);
                manifest = uploaded.project;
                paths = uploaded.paths || paths;
            }
            this.pendingAudioFiles = { master: null, vocals: null };
            this.activateProject(manifest, paths);
            this.draft = {
                ...manifest,
                revision: manifest.revision,
                storage: manifest.storage,
                has_plan: Boolean(manifest.plan?.path),
                has_audio: Boolean(manifest.audio?.master?.fingerprint || manifest.audio?.fingerprint),
                has_vocals: Boolean(manifest.audio?.vocals?.fingerprint),
            };
            this.renderDraft();
            this.setStatus(`项目 ${manifest.project_code} 已保存；项目内声音、字幕和资料会一起进入规划链。`, "success");
            await this.loadProjects();
        } catch (error) {
            this.setStatus(error?.message || String(error), "error");
        } finally {
            button.disabled = false;
            button.removeAttribute("aria-busy");
        }
    }

    destroy() {
        if (this.dialog?.open) this.dialog.close();
        this.dialog?.remove();
        if (projectEditorByNode.get(this.node) === this) projectEditorByNode.delete(this.node);
    }
}

function openProjectEditor(node) {
    new ProjectEditor(node);
}

class PlannerEditor {
    constructor(node, plan, payload) {
        installStyles();
        const previousEditor = editorByNode.get(node);
        if (previousEditor && previousEditor !== this) previousEditor.destroy();
        editorByNode.set(node, this);
        this.node = node;
        this.payload = payload || {};
        const providedSkillLibrary = this.payload.skill_template_library;
        this.skillTemplateLibrary = providedSkillLibrary && typeof providedSkillLibrary === "object"
            ? providedSkillLibrary
            : { visual_outline: [], h3_prompt: [] };
        this.skillTemplateFolder = String(payload?.skill_template_folder || "");
        this.plan = clone(plan);
        this.normalizeProjectStructure();
        const migratedLegacySplices = this.migrateLegacySplices();
        this.normalizeEditorialStructure();
        this.subtitleProvider = connectedSubtitleProvider(node);
        this.reasoningProviderNode = connectedReasoningProviderNode(node);
        this.reasoningProvider = payload?.reasoning_provider
            && typeof payload.reasoning_provider === "object"
            ? payload.reasoning_provider
            : { connected: false, available: false, token: "" };
        this.outlineSkill = payload?.outline_skill
            && typeof payload.outline_skill === "object"
            ? payload.outline_skill
            : {
                id: "xiaoyue-mv-visual-outline",
                display_name: "小月 MV 视觉镜头总纲",
                version: "0.1.0",
                entrypoint: "SKILL.cn.md",
                embedded: true,
            };
        const savedUi = this.plan.ui_state || {};
        const payloadWaveforms = payload?.waveforms && typeof payload.waveforms === "object"
            ? payload.waveforms
            : {};
        this.waveforms = {
            master: Array.isArray(payloadWaveforms.master)
                ? payloadWaveforms.master
                : Array.isArray(payload?.waveform) ? payload.waveform : [],
            vocals: Array.isArray(payloadWaveforms.vocals) ? payloadWaveforms.vocals : [],
        };
        this.audioPreviews = payload?.audio_previews && typeof payload.audio_previews === "object"
            ? payload.audio_previews
            : { master: payload?.audio_preview };
        this.audioSource = savedUi.audio_source === "vocals"
            && this.audioPreviews.vocals?.filename
            ? "vocals"
            : "master";
        this.waveform = this.waveforms[this.audioSource] || this.waveforms.master;
        this.selectedShot = Math.max(0, Math.min(
            this.plan.shots.length - 1,
            Math.round(Number(savedUi.selected_shot_index) || 0),
        ));
        this.selectedSplice = null;
        this.dirty = migratedLegacySplices;
        this.changeSerial = 0;
        this.playheadFrame = Number(this.plan.shots[this.selectedShot]?.start_frame || 0);
        this.playbackMode = (node._xiaoyuePlaybackMode || savedUi.playback_mode) === "full"
            ? "full"
            : "shot";
        this.playbackAnimationFrame = null;
        this.playbackShortcutArmed = false;
        this.playbackKeyDownHandler = null;
        this.playbackKeyUpHandler = null;
        this.rulerMode = savedUi.ruler_mode === "frame" ? "frame" : "time";
        this.detailKeys = ["subtitle", "outline", "prompt", "generation"];
        const legacyDetailKey = savedUi.detail_key || savedUi.audit_key;
        const savedDetailKey = legacyDetailKey === "asset" ? "prompt" : legacyDetailKey;
        this.detailKey = this.detailKeys.includes(savedDetailKey) ? savedDetailKey : "subtitle";
        this.savedSidebarScroll = Math.max(0, Number(savedUi.sidebar_scroll_top) || 0);
        this.projectPanelOpen = false;
        this.inputAssetCatalog = [];
        this.selectedInputAssetKeys = new Set();
        this.selectedShotAssetIds = new Set();
        this.assetPreviewImage = null;
        this.assetPreviewZoom = 1;
        this.assetPreviewPan = { x: 0, y: 0 };
        this.assetPreviewBaseSize = { width: 0, height: 0 };
        this.assetPreviewPointer = null;
        this.maximumZoom = 24;
        this.zoom = Math.max(1, Math.min(
            this.maximumZoom,
            Math.round(Number(savedUi.timeline_zoom) || 1),
        ));
        this.dragCleanup = null;
        this.editorWriteQueue = Promise.resolve();
        this.shotCheckpointTimers = new Map();
        this.dialog = this.buildDialog();
        this.generationState = { shots: {} };
        this.generationJobs = [];
        this.generationStarting = false;
        this.h3PromptRunning = false;
        this.h3PromptBatchRunning = false;
        this.h3PromptJobs = new Map();
        this.h3PromptStatuses = new Map();
        this.creativeOutlineRunning = false;
        this.bindStaticEvents();
        document.body.appendChild(this.dialog);
        this.dialog.showModal();
        this.renderAll();
        this.restoreEditorCheckpoint();
        this.refreshShotGenerations();
        this.refreshProjectSkillTemplateLibrary();
        requestAnimationFrame(() => {
            this.revealShotOnTimeline(this.selectedShot);
            const list = this.dialog.querySelector(".xiaoyue-mv-shot-list");
            if (list) list.scrollTop = this.savedSidebarScroll;
        });
        this.resizeObserver = new ResizeObserver(() => this.renderTimeline());
        this.resizeObserver.observe(this.timelineViewport);
        this.promptTextareaWidth = 0;
        this.promptResizeObserver = new ResizeObserver(([entry]) => {
            const width = entry?.contentRect.width || 0;
            if (width > 0 && Math.abs(width - this.promptTextareaWidth) > 0.5) {
                this.promptTextareaWidth = width;
                this.fitPromptTextarea();
            }
        });
        this.promptResizeObserver.observe(this.field("prompt"));
    }

    normalizeProjectStructure() {
        const project = this.plan.project && typeof this.plan.project === "object"
            ? this.plan.project
            : {};
        this.plan.project = {
            id: String(project.id || ""),
            schema: String(project.schema || ""),
            code: String(project.code || ""),
            name: String(project.name || ""),
            description: String(project.description || ""),
            overall_prompt: String(project.overall_prompt || ""),
            negative_prompt: String(project.negative_prompt || ""),
        };
        const creativePlan = this.plan.creative_plan && typeof this.plan.creative_plan === "object"
            ? this.plan.creative_plan
            : {};
        this.plan.creative_plan = {
            overview: String(creativePlan.overview || ""),
            revision: Math.max(0, Math.round(Number(creativePlan.revision) || 0)),
            source_fingerprint: String(creativePlan.source_fingerprint || ""),
            model: String(creativePlan.model || ""),
        };
        this.plan.generation_spec = normalizeGenerationSpec(this.plan.generation_spec);
        const selectedSkills = this.plan.skill_templates
            && typeof this.plan.skill_templates === "object"
            ? this.plan.skill_templates : {};
        this.plan.skill_templates = {
            visual_outline: String(
                selectedSkills.visual_outline || DEFAULT_SKILL_TEMPLATES.visual_outline),
            h3_prompt: String(
                selectedSkills.h3_prompt || DEFAULT_SKILL_TEMPLATES.h3_prompt),
        };
        const categories = new Set(["character", "scene", "prop"]);
        const usedIds = new Set();
        let outlineVisualCount = 0;
        this.plan.assets = (Array.isArray(this.plan.assets) ? this.plan.assets : [])
            .filter((asset) => asset && typeof asset === "object")
            .map((asset, index) => {
                let id = String(asset.id || `asset_${String(index + 1).padStart(3, "0")}`);
                while (usedIds.has(id)) id = `${id}_${usedIds.size + 1}`;
                usedIds.add(id);
                const source = asset.source && typeof asset.source === "object" ? asset.source : {};
                const outlineVisual = asset.outline_visual !== false && outlineVisualCount < 8;
                if (outlineVisual) outlineVisualCount += 1;
                return {
                    id,
                    name: String(asset.name || filenameTitle(source.filename)),
                    category: categories.has(asset.category) ? asset.category : "character",
                    declaration: String(asset.declaration || ""),
                    outline_visual: outlineVisual,
                    source: {
                        filename: String(source.filename || ""),
                        subfolder: String(source.subfolder || ""),
                        type: String(source.type || "input"),
                        media_type: source.media_type === "video" ? "video" : "image",
                        size: Math.max(0, Number(source.size) || 0),
                        modified_ms: Math.max(0, Number(source.modified_ms) || 0),
                    },
                };
            });
        const validIds = new Set(this.plan.assets.map((asset) => asset.id));
        for (const shot of this.plan.shots || []) {
            shot.creative_outline = String(shot.creative_outline || "");
            shot.singing_performance = typeof shot.singing_performance === "boolean"
                ? shot.singing_performance : null;
            shot.creative_asset_needs = (Array.isArray(shot.creative_asset_needs)
                ? shot.creative_asset_needs : [])
                .filter((need) => need && typeof need === "object")
                .map((need) => ({
                    category: ["character", "scene", "prop"].includes(need.category)
                        ? need.category : "prop",
                    description: String(need.description || ""),
                }))
                .filter((need) => need.description);
            let source = Array.isArray(shot.asset_bindings) ? shot.asset_bindings : [];
            if (!source.length && Array.isArray(shot.asset_ids)) {
                source = shot.asset_ids.map((assetId) => ({ asset_id: assetId, usage: "" }));
            }
            const seen = new Set();
            shot.asset_bindings = source
                .map((binding) => typeof binding === "string" ? { asset_id: binding } : binding)
                .filter((binding) => binding && validIds.has(String(binding.asset_id))
                    && !seen.has(String(binding.asset_id)) && seen.add(String(binding.asset_id)))
                .map((binding, index) => ({
                    asset_id: String(binding.asset_id),
                    order: index + 1,
                    usage: String(binding.usage || ""),
                }));
            delete shot.asset_ids;
        }
        this.plan.asset_binding_policy = {
            scope: "per_shot_only",
            internal_identity: "assets[].id",
            reference_order: "shots[].asset_bindings[].order",
            generation_rule: "resolve_only_current_shot_bindings_and_assign_reference_indices_per_shot",
            global_library_order_is_generation_order: false,
        };
    }

    nextAssetId() {
        const used = new Set(this.plan.assets.map((asset) => asset.id));
        const random = globalThis.crypto?.randomUUID?.().replaceAll("-", "")
            || `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
        let id = `asset_${random.slice(0, 12)}`;
        let suffix = 1;
        while (used.has(id)) id = `asset_${random.slice(0, 10)}_${suffix++}`;
        return id;
    }

    migrateLegacySplices() {
        let migrated = false;
        const oldWindows = Array.isArray(this.plan.generation_windows)
            ? this.plan.generation_windows
            : [];
        const sourceShots = this.plan.shots || [];
        const sourceTransitions = this.plan.transitions || [];
        const expandedShots = [];
        const expandedTransitions = [];
        sourceShots.forEach((shot, shotIndex) => {
            let points = Array.isArray(shot.manual_splice_frames)
                ? shot.manual_splice_frames
                : [];
            if (!points.length && shot.allow_stitch) {
                points = oldWindows
                    .filter((window) => window.shot_id === shot.id)
                    .slice(0, -1)
                    .map((window) => window.delivery_timeline_range?.[1]);
                migrated = true;
            }
            points = [...new Set(points
                .map((frame) => Math.round(Number(frame)))
                .filter((frame) => Number.isFinite(frame) && frame > shot.start_frame && frame < shot.end_frame))]
                .sort((left, right) => left - right);
            if (points.length) migrated = true;
            if (Object.hasOwn(shot, "allow_stitch")) {
                delete shot.allow_stitch;
                migrated = true;
            }
            const boundaries = [shot.start_frame, ...points, shot.end_frame];
            for (let partIndex = 0; partIndex < boundaries.length - 1; partIndex += 1) {
                const part = clone(shot);
                part.start_frame = boundaries[partIndex];
                part.end_frame = boundaries[partIndex + 1];
                part.manual_splice_frames = [];
                delete part.allow_stitch;
                updateShotClock(this.plan, part);
                expandedShots.push(part);
                if (partIndex < boundaries.length - 2) {
                    expandedTransitions.push(newTransition(
                        this.plan,
                        "long_take",
                        boundaries[partIndex + 1],
                    ));
                    migrated = true;
                }
            }
            if (shotIndex < sourceShots.length - 1) {
                expandedTransitions.push(clone(sourceTransitions[shotIndex] || newTransition(
                    this.plan,
                    this.plan.settings?.default_transition || "hard_cut",
                    shot.end_frame,
                )));
            }
        });
        if (migrated) {
            this.plan.shots = expandedShots;
            this.plan.transitions = expandedTransitions;
            this.plan.generation_windows = [];
            this.plan.needs_compile = true;
        }
        return migrated;
    }

    normalizeEditorialStructure() {
        const shots = this.plan.shots || [];
        const transitions = this.plan.transitions || [];
        const usedShotUids = new Set();
        shots.forEach((shot, index) => {
            if (!shot.shot_uid) {
                shot.shot_uid = shot.id ? `legacy_${shot.id}` : newProjectId().replaceAll("-", "");
            }
            if (usedShotUids.has(shot.shot_uid)) {
                shot.shot_uid = newProjectId().replaceAll("-", "");
            }
            usedShotUids.add(shot.shot_uid);
            shot.id = `shot_${String(index + 1).padStart(3, "0")}`;
            shot.index = index + 1;
            shot.manual_splice_frames = [];
            updateShotClock(this.plan, shot);
        });
        const normalizedTransitions = [];
        for (let index = 0; index < Math.max(0, shots.length - 1); index += 1) {
            const transition = transitions[index] || newTransition(
                this.plan,
                this.plan.settings?.default_transition || "hard_cut",
                shots[index].end_frame,
            );
            transition.id = `transition_${String(index + 1).padStart(3, "0")}`;
            transition.from_shot = shots[index].id;
            transition.to_shot = shots[index + 1].id;
            transition.at_frame = shots[index].end_frame;
            transition.at_seconds = Number(frameSeconds(
                transition.at_frame,
                this.plan.h3?.fps || 24,
            ).toFixed(6));
            transitionGeometry(transition);
            normalizedTransitions.push(transition);
        }
        this.plan.transitions = normalizedTransitions;
        normalizedTransitions.forEach((transition, index) => {
            if (transition.type !== "crossfade") return;
            const math = this.crossfadeCalculation(index);
            if (!math) return;
            transition.duration_frames = math.duration;
            transitionGeometry(transition);
        });
        this.recalculateAutomaticCrossfadeSafety();
        const sampleRate = Number(this.plan.audio?.sample_rate || 48000);
        const totalSamples = Number(this.plan.audio?.total_samples || 0);
        const points = [0, ...shots.map((shot) => shot.end_frame)];
        this.plan.edit_points = points.map((frame, index) => ({
            id: `edit_point_${String(index).padStart(3, "0")}`,
            index,
            kind: index === 0 ? "program_start" : index === shots.length ? "program_end" : "shot_boundary",
            absolute_frame: frame,
            absolute_sample: Math.min(totalSamples, Math.round(frame * sampleRate / (this.plan.h3?.fps || 24))),
            left_shot_id: index > 0 ? shots[index - 1]?.id || null : null,
            right_shot_id: index < shots.length ? shots[index]?.id || null : null,
            transition_id: index > 0 && index < shots.length
                ? normalizedTransitions[index - 1]?.id || null
                : null,
        }));
        this.normalizeSubtitleStructure();
        this.recalculateSubtitleProjections();
    }

    normalizeSubtitleStructure() {
        const subtitles = this.plan.subtitles && typeof this.plan.subtitles === "object"
            ? this.plan.subtitles
            : {};
        const source = Array.isArray(subtitles.master_cues)
            ? subtitles.master_cues
            : Array.isArray(subtitles.cues)
                ? subtitles.cues
                : [];
        const seen = new Map();
        const masterCues = source
            .map((cue, index) => canonicalSubtitleCue(cue, index, this.plan))
            .filter(Boolean)
            .map((cue) => {
                const duplicate = seen.get(cue.id) || 0;
                seen.set(cue.id, duplicate + 1);
                if (duplicate) cue.id = `${cue.id}_${String(duplicate + 1).padStart(2, "0")}`;
                return cue;
            })
            .sort((left, right) => left.absolute_start_frame - right.absolute_start_frame);
        this.plan.subtitles = {
            ...subtitles,
            format: subtitles.format || (masterCues.length ? "srt" : "none"),
            master_cues: masterCues,
            cue_count: masterCues.length,
            master_role: "editable_reference",
            final_text_authority: "shots[].subtitle_track.text",
            ai_dialogue_authority: "shots[].subtitle_context.ai_dialogue_text",
            shot_timing_policy: "shot_boundaries",
        };
        delete this.plan.subtitles.cues;
        if (masterCues.length) {
            this.plan.subtitle_source = serializeMasterCues(masterCues, this.plan.h3?.fps || 24);
        }
    }

    masterCues() {
        return this.plan.subtitles?.master_cues || [];
    }

    recalculateSubtitleProjections() {
        const masterCues = this.masterCues();
        for (const shot of this.plan.shots || []) {
            const projections = [];
            for (const cue of masterCues) {
                const clipStart = Math.max(shot.start_frame, cue.absolute_start_frame);
                const clipEnd = Math.min(shot.end_frame, cue.absolute_end_frame);
                if (clipEnd <= clipStart) continue;
                const cueFrames = Math.max(1, cue.absolute_end_frame - cue.absolute_start_frame);
                projections.push({
                    shot_id: shot.id,
                    source_cue_id: cue.id,
                    clip_abs_start_frame: clipStart,
                    clip_abs_end_frame: clipEnd,
                    relative_start_frame: clipStart - shot.start_frame,
                    relative_end_frame: clipEnd - shot.start_frame,
                    is_partial: clipStart !== cue.absolute_start_frame || clipEnd !== cue.absolute_end_frame,
                    overlap_ratio: Number(((clipEnd - clipStart) / cueFrames).toFixed(6)),
                });
            }
            shot.subtitle_projections = projections;
            shot.subtitle_ids = projections.map((projection) => projection.source_cue_id);
            normalizeShotSubtitleTrack(shot);
        }
        this.refreshAllShotSubtitleContexts();
    }

    subtitleCuesInRange(start, end) {
        if (end <= start) return [];
        return this.masterCues().filter((cue) =>
            Math.min(end, Number(cue.absolute_end_frame || 0))
            > Math.max(start, Number(cue.absolute_start_frame || 0)));
    }

    refreshShotSubtitleContext(index) {
        const shot = this.plan.shots?.[index];
        if (!shot) return null;
        const track = normalizeShotSubtitleTrack(shot);
        const proxy = this.proxyWindows(index);
        const totalFrames = Math.max(1, Number(this.plan.audio?.timeline_frames || 1));
        const mainStart = Math.max(0, Math.min(totalFrames, Number(shot.start_frame || 0)));
        const mainEnd = Math.max(mainStart, Math.min(totalFrames, Number(shot.end_frame || 0)));
        const sourceStart = Math.max(0, Math.min(
            mainStart,
            ...proxy.segments.map((segment) => Number(segment.sourceStart ?? mainStart)),
        ));
        const sourceEnd = Math.min(totalFrames, Math.max(
            mainEnd,
            ...proxy.segments.map((segment) => Number(segment.sourceEnd ?? mainEnd)),
        ));
        const leadingCues = this.subtitleCuesInRange(sourceStart, mainStart);
        const mainCues = this.subtitleCuesInRange(mainStart, mainEnd);
        const trailingCues = this.subtitleCuesInRange(mainEnd, sourceEnd);
        const leadingText = sourceStart < mainStart
            ? singleLineSubtitleText(track.leading_text)
            : "";
        const mainText = singleLineSubtitleText(track.text);
        const trailingText = sourceEnd > mainEnd
            ? singleLineSubtitleText(track.trailing_text)
            : "";
        const section = (role, start, end, text, cues) => ({
            role,
            range: [start, end],
            frames: Math.max(0, end - start),
            text,
            reference_cue_ids: cues.map((cue) => cue.id),
        });
        shot.subtitle_context = {
            policy: "leading_main_trailing_generation_context",
            source_range: [sourceStart, sourceEnd],
            leading_extension: {
                ...section("leading_extension", sourceStart, mainStart, leadingText, leadingCues),
                text_authority: "shots[].subtitle_track.leading_text",
                text_entry: "user",
            },
            main: {
                ...section("formal_subtitle", mainStart, mainEnd, mainText, mainCues),
                text_authority: "shots[].subtitle_track.text",
                is_final_subtitle_output: true,
            },
            trailing_extension: {
                ...section("trailing_extension", mainEnd, sourceEnd, trailingText, trailingCues),
                text_authority: "shots[].subtitle_track.trailing_text",
                text_entry: "user",
            },
            ai_dialogue_text: joinSubtitleContextParts([
                leadingText, mainText, trailingText]),
            ai_dialogue_policy: "text_only_no_shot_relative_timestamps",
            visible_text_policy: "hidden_context_not_on_screen_typography",
        };
        return shot.subtitle_context;
    }

    refreshAllShotSubtitleContexts() {
        (this.plan.shots || []).forEach((_shot, index) => {
            this.refreshShotSubtitleContext(index);
        });
    }

    renderShotSubtitleContextFields(context) {
        if (!context) return;
        const leading = context.leading_extension;
        const main = context.main;
        const trailing = context.trailing_extension;
        const sections = [
            ["leading", leading],
            ["main", main],
            ["trailing", trailing],
        ];
        const visibleExtensionCount = [leading, trailing]
            .filter((section) => Math.max(0, Number(section?.frames || 0)) > 0)
            .length;
        const extensionWidth = 20;
        const mainWidth = 100 - visibleExtensionCount * extensionWidth;
        this.field("shot-subtitle-final-range").value =
            `H3 音频 F${String(context.source_range[0]).padStart(6, "0")}–F${String(context.source_range[1]).padStart(6, "0")} · 正式字幕只取中段切点`;
        const table = this.field("shot-subtitle-table");
        for (const [name, section] of sections) {
            const cell = this.field(`shot-subtitle-${name}-cell`);
            const heading = this.field(`shot-subtitle-${name}-heading`);
            const frames = Math.max(0, Number(section?.frames || 0));
            cell.hidden = frames <= 0;
            heading.hidden = frames <= 0;
            cell.style.width = frames > 0
                ? `${name === "main" ? mainWidth : extensionWidth}%`
                : "0";
            heading.style.width = cell.style.width;
        }
        table.setAttribute(
            "aria-label",
            `镜头字幕三段轨：前扩 ${Math.max(0, Number(leading.frames || 0))} 帧，正式 ${Math.max(0, Number(main.frames || 0))} 帧，后扩 ${Math.max(0, Number(trailing.frames || 0))} 帧`,
        );
        this.field("shot-subtitle-leading").value = leading.text || "";
        this.field("shot-subtitle-trailing").value = trailing.text || "";
        this.field("shot-subtitle-ai-text").value = context.ai_dialogue_text || "暂无唱词";
    }

    shotToneIndex(index) {
        let group = 0;
        for (let cursor = 0; cursor < Math.max(0, Number(index) || 0); cursor += 1) {
            if (this.plan.transitions[cursor]?.type !== "long_take") group += 1;
        }
        return group;
    }

    buildDialog() {
        const dialog = document.createElement("dialog");
        const helpId = `xiaoyue-mv-help-${String(this.node?.id ?? "planner")}`;
        const warningId = `xiaoyue-mv-warnings-${String(this.node?.id ?? "planner")}`;
        const inputPickerTitleId = `xiaoyue-input-picker-title-${String(this.node?.id ?? "planner")}`;
        const shotAssetPickerTitleId = `xiaoyue-shot-asset-picker-title-${String(this.node?.id ?? "planner")}`;
        const assetPreviewTitleId = `xiaoyue-asset-preview-title-${String(this.node?.id ?? "planner")}`;
        const replanTitleId = `xiaoyue-replan-title-${String(this.node?.id ?? "planner")}`;
        const shotSubtitleId = `xiaoyue-shot-subtitle-${String(this.node?.id ?? "planner")}`;
        const hasTimestampedSubtitles = this.masterCues().length > 0;
        const providerButtonDisabled = this.subtitleProvider ? "" : " disabled";
        const asrAudioSource = this.audioPreviews.vocals?.filename ? "干声" : "母带";
        const providerStatus = `${providerStatusLabel(this.subtitleProvider)} 当前 ASR 自动使用${asrAudioSource}。`;
        const vocalsSourceDisabled = this.audioPreviews.vocals?.filename ? "" : " disabled";
        const reasoningReady = Boolean(
            this.reasoningProvider?.available && this.reasoningProvider?.token,
        );
        const reasoningButtonDisabled = reasoningReady ? "" : " disabled";
        const reasoningModel = reasoningReady
            ? `${this.reasoningProvider.family || "QwenTE"} · ${this.reasoningProvider.model_name || "本地模型"} · ${this.reasoningProvider.visual_capable ? "视觉已就绪" : "未加载 mmproj"}`
            : this.reasoningProviderNode
                ? "推理适配器已连接，但尚未取得模型；请先运行一次工作流。"
                : "未连接 QwenTE · MV 整片纲领推理 Provider。";
        dialog.className = "xiaoyue-mv-dialog";
        dialog.setAttribute("aria-labelledby", "xiaoyue-mv-title");
        dialog.innerHTML = `
            <div class="xiaoyue-mv-shell">
                <header class="xiaoyue-mv-header">
                    <div>
                        <p class="xiaoyue-mv-eyebrow">MINIMAX H3 · 24 FPS EDITORIAL CLOCK</p>
                        <h2 id="xiaoyue-mv-title">MV 规划器 <span>xiaoyue</span></h2>
                    </div>
                    <div class="xiaoyue-mv-header-actions">
                        <button type="button" class="project-control" data-action="open-project">项目总控 · <span data-field="asset-count-header">0</span> 资产</button>
                        <button type="button" data-action="replan">重新自动找点</button>
                        <button type="button" class="warning-check" data-action="show-warnings" popovertarget="${warningId}" aria-label="查看规划检查结果">
                            <span data-field="warning-count">检查</span>
                        </button>
                        <button type="button" class="primary" data-action="save">保存计划</button>
                        <button type="button" class="icon" data-action="close" aria-label="关闭 MV 规划器">×</button>
                    </div>
                </header>
                <section class="xiaoyue-mv-project-panel" data-project-panel hidden aria-label="项目总体规划与公用资产库">
                    <header class="xiaoyue-mv-project-heading">
                        <div>
                            <h3>项目总控</h3>
                            <p>全片只保存一份项目描述和资产声明，镜头通过 ID 绑定。</p>
                        </div>
                        <button type="button" data-action="close-project">返回时间轴</button>
                    </header>
                    <div class="xiaoyue-mv-project-body">
                        <section class="xiaoyue-mv-project-brief" aria-label="项目总体规划">
                            <h4>总体规划</h4>
                            <div class="xiaoyue-mv-project-name-row">
                                <label>项目编号
                                    <input data-field="project-code" type="text" readonly aria-readonly="true" title="项目编号在统一工作台中创建后锁定" autocomplete="off">
                                </label>
                                <label>项目名称
                                    <input data-field="project-name" type="text" placeholder="MV 名称" autocomplete="off">
                                </label>
                            </div>
                            <label>整体描述
                                <textarea data-field="project-description" rows="5" placeholder="故事主题、情绪曲线、世界观和视觉方向"></textarea>
                            </label>
                            <label>总体提示词
                                <textarea data-field="project-overall-prompt" rows="7" placeholder="所有镜头共用的风格、摄影、色彩、画质与连续性约束"></textarea>
                            </label>
                            <label>MV 镜头纲领总览
                                <textarea data-field="creative-plan-overview" rows="5" placeholder="第一轮推理模型生成的全片情绪曲线、叙事推进和镜头安排；它不是 H3 生成提示词"></textarea>
                            </label>
                            <label>全局禁用项
                                <textarea data-field="project-negative-prompt" rows="4" placeholder="所有镜头都应避免的内容或画面问题"></textarea>
                            </label>
                            <fieldset class="xiaoyue-mv-generation-spec">
                                <legend>影片生成规格</legend>
                                <label>长宽比
                                    <select data-field="project-aspect-ratio">
                                        ${ASPECT_RATIO_OPTIONS.map((value) => `<option value="${value}">${value}</option>`).join("")}
                                    </select>
                                </label>
                                <label>一采目标 MP
                                    <input data-field="project-first-pass-megapixels" type="number" min="0.1" max="16" step="0.1" inputmode="decimal">
                                </label>
                                <label>二采目标 MP
                                    <input data-field="project-second-pass-megapixels" type="number" min="0.1" max="16" step="0.1" inputmode="decimal">
                                </label>
                                <small>属于项目级生成规格；修改不会重新打点，后续视频生成节点统一读取。</small>
                            </fieldset>
                            <fieldset class="xiaoyue-mv-generation-spec xiaoyue-mv-skill-templates">
                                <legend>各阶段 Skill 模板</legend>
                                <label>整片视觉纲领
                                    <select data-field="project-outline-skill"></select>
                                </label>
                                <label>单镜 H3 提示词
                                    <select data-field="project-h3-skill"></select>
                                </label>
                                <button type="button" data-action="refresh-project-skill-templates">刷新 Skill 列表</button>
                                <small data-field="project-skill-template-help" role="status">默认与自定义 Skill 均从本插件目录读取。</small>
                            </fieldset>
                            <p class="xiaoyue-mv-project-contract">后续提示词节点会读取：项目总提示词 → 当前镜头自己的有序资产调用 → 镜头提示词。总资产库的排列顺序不参与生成索引。</p>
                        </section>
                        <section class="xiaoyue-mv-asset-library" aria-label="项目公用资产库">
                            <div class="xiaoyue-mv-asset-toolbar">
                                <div>
                                    <h4>公用资产库</h4>
                                    <output data-field="asset-count">尚未导入资产</output>
                                </div>
                                <div class="xiaoyue-mv-asset-actions">
                                    <button type="button" data-action="upload-assets">导入本地资产</button>
                                    <button type="button" data-action="browse-input-assets">从 input 选择</button>
                                    <input data-field="asset-file-input" type="file" accept="image/*,video/*,.mkv" multiple hidden>
                                </div>
                            </div>
                            <output class="xiaoyue-mv-asset-status" data-field="asset-status" role="status">导入后请逐一声明为角色、场景或道具。</output>
                            <div class="xiaoyue-mv-asset-list" data-field="asset-list"></div>
                        </section>
                    </div>
                </section>
                <dialog class="xiaoyue-mv-input-picker" aria-labelledby="${inputPickerTitleId}">
                    <header>
                        <div>
                            <h3 id="${inputPickerTitleId}">从 ComfyUI input 选择资产</h3>
                            <p>可多选，已在项目库中的文件不会重复导入。</p>
                        </div>
                        <button type="button" data-action="close-input-picker" aria-label="关闭 input 选择器">×</button>
                    </header>
                    <label class="xiaoyue-mv-input-search">搜索 input
                        <input data-field="input-asset-search" type="search" placeholder="输入文件名或子目录">
                    </label>
                    <div class="xiaoyue-mv-input-asset-list" data-field="input-asset-list"></div>
                    <footer>
                        <output data-field="input-picker-status" role="status"></output>
                        <button type="button" class="primary" data-action="confirm-input-assets">导入已选资产</button>
                    </footer>
                </dialog>
                <dialog class="xiaoyue-mv-shot-asset-picker" aria-labelledby="${shotAssetPickerTitleId}">
                    <header>
                        <div>
                            <h3 id="${shotAssetPickerTitleId}">从项目资产库加入当前镜头</h3>
                            <p>这里只决定本镜头调用哪些资产；确认后会按选择顺序追加本镜头参考编号。</p>
                        </div>
                        <button type="button" data-action="close-shot-asset-picker" aria-label="关闭镜头资产选择器">×</button>
                    </header>
                    <label class="xiaoyue-mv-input-search">搜索项目资产
                        <input data-field="shot-asset-search" type="search" placeholder="输入资产名称、声明或文件名">
                    </label>
                    <div class="xiaoyue-mv-shot-asset-picker-list" data-field="shot-asset-picker-list"></div>
                    <footer>
                        <output data-field="shot-asset-picker-status" role="status"></output>
                        <button type="button" class="primary" data-action="confirm-shot-assets">加入当前镜头</button>
                    </footer>
                </dialog>
                <dialog class="xiaoyue-mv-asset-preview-dialog" aria-labelledby="${assetPreviewTitleId}">
                    <header>
                        <div>
                            <h3 id="${assetPreviewTitleId}" data-field="asset-preview-title">资产预览</h3>
                            <p data-field="asset-preview-meta"></p>
                        </div>
                        <div class="xiaoyue-mv-asset-preview-header-actions">
                            <div class="xiaoyue-mv-asset-preview-toolbar" data-field="asset-preview-toolbar" role="toolbar" aria-label="图片缩放控制" hidden>
                                <button type="button" data-action="asset-preview-zoom-out">缩小</button>
                                <output data-field="asset-preview-zoom" aria-label="当前缩放比例">100%</output>
                                <button type="button" data-action="asset-preview-zoom-in">放大</button>
                                <button type="button" data-action="asset-preview-fit">适应窗口</button>
                            </div>
                            <button type="button" class="asset-preview-close" data-action="close-asset-preview" aria-label="关闭资产预览">×</button>
                        </div>
                    </header>
                    <div class="xiaoyue-mv-asset-preview-body" data-field="asset-preview-body" tabindex="-1"></div>
                </dialog>
                <dialog class="xiaoyue-mv-replan-dialog" aria-labelledby="${replanTitleId}">
                    <header>
                        <div>
                            <p class="xiaoyue-mv-replan-eyebrow">破坏性时间轴操作</p>
                            <h3 id="${replanTitleId}">重新自动找点</h3>
                            <p>只在这里修改项目的自动打点参数。应用后会重建全部镜头，不会静默覆盖。</p>
                        </div>
                        <button type="button" data-action="close-replan" aria-label="关闭重新打点设置">×</button>
                    </header>
                    <div class="xiaoyue-mv-replan-body">
                        <fieldset>
                            <legend>新的打点参数</legend>
                            <label>目标实际镜头长度
                                <input data-field="replan-target-shot-seconds" type="number" min="1" max="9.417" step="0.1" inputmode="decimal">
                                <small>自动找安全点时的偏好长度，不是最终 H3 生成窗口长度。</small>
                            </label>
                            <label>H3 单窗上限
                                <select data-field="replan-h3-max-frames">
                                    ${H3_PROJECT_PRESETS.map((frames) => `<option value="${frames}">${h3ProjectPresetLabel(frames)}</option>`).join("")}
                                </select>
                                <small>只能选择符合 17N+5 的 5–15 秒档位；默认 226 帧甜蜜线。</small>
                            </label>
                        </fieldset>
                        <div class="xiaoyue-mv-replan-impact">
                            <section>
                                <h4>会清空并重建</h4>
                                <p>切点、镜头顺序、长镜头／叠化标识与参数、镜头资产绑定、镜头提示词、镜头最终字幕校正轨。</p>
                            </section>
                            <section>
                                <h4>会保留</h4>
                                <p>项目身份、母带／干声、项目总提示词、字幕母版绝对时间轴、公用资产库、影片画幅与双采 MP。</p>
                            </section>
                        </div>
                        <output data-field="replan-summary" role="status"></output>
                    </div>
                    <footer>
                        <button type="button" data-action="close-replan">取消</button>
                        <button type="button" class="danger" data-action="confirm-replan">应用参数并重新打点</button>
                    </footer>
                </dialog>
                <dialog class="xiaoyue-mv-generation-picker" aria-labelledby="xiaoyue-mv-generation-picker-title">
                    <header>
                        <h3 id="xiaoyue-mv-generation-picker-title">选择要生成的镜头</h3>
                        <button type="button" data-action="close-generation-picker" aria-label="关闭镜头选择">×</button>
                    </header>
                    <label class="xiaoyue-mv-generation-select-all"><input type="checkbox" data-field="generation-select-all" checked> 全选</label>
                    <div class="xiaoyue-mv-generation-picker-list" data-field="generation-picker-list"></div>
                    <output class="xiaoyue-mv-generation-picker-status" data-field="generation-picker-status" role="status" aria-live="polite" hidden></output>
                    <footer>
                        <output data-field="generation-picker-count"></output>
                        <button type="button" data-action="close-generation-picker">取消</button>
                        <button type="button" class="primary" data-action="confirm-generation-picker">顺序生成</button>
                    </footer>
                </dialog>
                <dialog class="xiaoyue-mv-generation-preview" aria-label="镜头版本放大预览">
                    <header>
                        <strong data-field="generation-preview-title"></strong>
                        <button type="button" data-action="close-generation-preview">关闭</button>
                    </header>
                    <video controls preload="metadata" playsinline></video>
                </dialog>
                <div id="${warningId}" class="xiaoyue-mv-help-popover xiaoyue-mv-warning-popover" popover="auto" aria-labelledby="${warningId}-title">
                    <div class="xiaoyue-mv-help-heading">
                        <h3 id="${warningId}-title">规划检查</h3>
                        <button type="button" popovertarget="${warningId}" popovertargetaction="hide">关闭</button>
                    </div>
                    <section class="xiaoyue-mv-warnings" aria-live="polite"></section>
                </div>
                <section class="xiaoyue-mv-transport" aria-label="母带播放控制">
                    <audio controls preload="metadata"></audio>
                    <div class="xiaoyue-mv-audio-source" role="group" aria-label="波形与试听音轨">
                        <span>对点音轨</span>
                        <div>
                            <button type="button" data-audio-source="master" aria-pressed="true">母带</button>
                            <button type="button" data-audio-source="vocals" aria-pressed="false"${vocalsSourceDisabled}>干声</button>
                        </div>
                    </div>
                    <div class="xiaoyue-mv-playback-scope" role="group" aria-label="播放范围">
                        <span>播放范围</span>
                        <div>
                            <button type="button" data-playback-mode="shot" aria-pressed="true">当前镜头</button>
                            <button type="button" data-playback-mode="full" aria-pressed="false">整首</button>
                        </div>
                    </div>
                    <div class="xiaoyue-mv-summary"></div>
                    <div class="xiaoyue-mv-compile-state" role="status"></div>
                </section>
                <main class="xiaoyue-mv-main">
                    <section class="xiaoyue-mv-stage" aria-label="MV 时间轴">
                        <div class="xiaoyue-mv-timeline-fixed">
                            <div class="xiaoyue-mv-timeline-toolbar">
                                <div class="xiaoyue-mv-timeline-actions" role="group" aria-label="镜头时间轴操作">
                                    <button type="button" data-action="add-splice">＋ 剃刀切镜</button>
                                    <button type="button" class="delete-shot" data-action="remove-splice">删除当前镜头</button>
                                    <button type="button" class="help" popovertarget="${helpId}">操作说明</button>
                                </div>
                                <div class="xiaoyue-mv-timeline-controls">
                                    <div class="xiaoyue-mv-ruler-mode" role="group" aria-label="时间轴标尺单位">
                                        <span>标尺</span>
                                        <button type="button" data-ruler-mode="frame" aria-pressed="false">帧标</button>
                                        <button type="button" data-ruler-mode="time" aria-pressed="true">时间标</button>
                                    </div>
                                    <div class="xiaoyue-mv-zoom" role="group" aria-label="时间轴缩放">
                                        <button type="button" data-action="zoom-out" aria-label="缩小时间轴">−</button>
                                        <input data-field="timeline-zoom" type="range" min="1" max="24" step="1" value="1" aria-label="时间轴缩放倍数">
                                        <button type="button" data-action="zoom-in" aria-label="放大时间轴">＋</button>
                                        <button type="button" class="fit" data-action="zoom-fit">适合全曲</button>
                                        <output data-field="zoom-value" aria-live="polite">1×</output>
                                    </div>
                                </div>
                            </div>
                            <div id="${helpId}" class="xiaoyue-mv-help-popover" popover="auto" aria-labelledby="${helpId}-title">
                                <div class="xiaoyue-mv-help-heading">
                                    <h3 id="${helpId}-title">时间轴操作说明</h3>
                                    <button type="button" popovertarget="${helpId}" popovertargetaction="hide">关闭</button>
                                </div>
                                <div class="xiaoyue-mv-legend" aria-label="接片图例">
                                    <span class="segments">交替色：成片镜头</span>
                                    <span class="solid">实线：独立切镜</span>
                                    <span class="dashed">虚线：连续镜头</span>
                                    <span class="audio-handle">半透明：头尾裁边</span>
                                    <span class="context">紫色：续写重合帧</span>
                                    <span class="dissolve">交叉纹理：可拖动叠化</span>
                                </div>
                                <dl class="xiaoyue-mv-help-examples">
                                    <div><dt>精确试听</dt><dd>点击波形选择镜头，空格播放／暂停；方向键移动 1 帧，Shift + 方向键移动 6 帧。</dd></div>
                                    <div><dt>添加切点</dt><dd>把播放头停在安全落点，点击“剃刀切镜”；连接类型在时间轴下方设置。</dd></div>
                                    <div><dt>叠化</dt><dd>只填写真实叠化帧。左右安全帧由相邻镜头预算自动补到合法 17N+5。</dd></div>
                                </dl>
                            </div>
                            <div class="xiaoyue-mv-timeline-board">
                                <div class="xiaoyue-mv-track-labels" aria-hidden="true">
                                    <span class="ruler"></span>
                                    <span class="audio">音频</span>
                                    <span class="main">成片</span>
                                    <span class="fx">叠化</span>
                                </div>
                                <div class="xiaoyue-mv-timeline-viewport" tabindex="0" aria-label="完整音乐剪辑时间轴；可横向滚动">
                                    <div class="xiaoyue-mv-timeline-content">
                                        <canvas aria-hidden="true"></canvas>
                                        <div class="xiaoyue-mv-clip-layer" aria-label="成片镜头轨道"></div>
                                        <div class="xiaoyue-mv-fx-layer" aria-label="叠化效果轨道"></div>
                                        <div class="xiaoyue-mv-playhead" aria-hidden="true"></div>
                                    </div>
                                </div>
                            </div>
                            <output class="xiaoyue-mv-visually-hidden" data-field="timeline-feedback" aria-live="polite">拖动时间块边缘；松手时自动吸附音乐保护点。</output>
                            <div class="xiaoyue-mv-timeline-edit-row">
                                <div class="xiaoyue-mv-transition-editor" aria-label="当前镜头连接方式">
                                    <label>连接到下一段
                                        <select data-field="transition-type" aria-label="连接到下一段的方式">
                                            <option value="hard_cut">实接（独立切镜）</option>
                                            <option value="long_take">虚接（前后续写）</option>
                                            <option value="crossfade">叠化（分轨）</option>
                                        </select>
                                    </label>
                                    <label data-setting="context">续接尾帧
                                        <select data-field="context-frames">
                                            <option value="5">5 帧</option>
                                            <option value="22">22 帧</option>
                                            <option value="39">39 帧</option>
                                            <option value="56">56 帧</option>
                                            <option value="73">73 帧</option>
                                        </select>
                                    </label>
                                    <label data-setting="duration">叠化帧数
                                        <input data-field="duration-frames" type="number" min="2" max="96" step="1">
                                    </label>
                                    <div class="xiaoyue-mv-auto-safety" data-setting="safety">
                                        <span>自动安全</span>
                                        <output data-field="auto-safety-frames"></output>
                                    </div>
                                    <output class="xiaoyue-mv-transition-math" data-field="transition-math" aria-live="polite"></output>
                                </div>
                            </div>
                        </div>
                        <div class="xiaoyue-mv-details-pane">
                            <nav class="xiaoyue-mv-detail-tabs" aria-label="当前镜头内容">
                                <output class="xiaoyue-mv-active-shot" data-field="active-shot-label">镜头 01</output>
                                <div role="tablist" aria-label="镜头内容分栏">
                                    <button type="button" id="xiaoyue-tab-subtitle" role="tab" data-detail-target="subtitle" aria-controls="xiaoyue-panel-subtitle" aria-selected="true">字幕</button>
                                    <button type="button" id="xiaoyue-tab-outline" role="tab" data-detail-target="outline" aria-controls="xiaoyue-panel-outline" aria-selected="false" tabindex="-1">项目总规划</button>
                                    <button type="button" id="xiaoyue-tab-prompt" role="tab" data-detail-target="prompt" aria-controls="xiaoyue-panel-prompt" aria-selected="false" tabindex="-1">提示词</button>
                                    <button type="button" id="xiaoyue-tab-generation" role="tab" data-detail-target="generation" aria-controls="xiaoyue-panel-generation" aria-selected="false" tabindex="-1">镜头</button>
                                </div>
                            </nav>
                            <section id="xiaoyue-panel-outline" class="xiaoyue-mv-shot-editor xiaoyue-mv-detail-section" role="tabpanel" data-detail-panel="outline" aria-labelledby="xiaoyue-tab-outline" hidden>
                                <div class="xiaoyue-mv-project-outline-fields">
                                    <label>整片总提示词
                                        <textarea data-field="outline-overall-prompt" rows="4" placeholder="全片共用的美术、摄影、色彩、表演与连续性要求"></textarea>
                                    </label>
                                    <label>MV 镜头纲领总览
                                        <textarea data-field="outline-plan-overview" rows="5" placeholder="整片情绪曲线、叙事推进和镜头安排；它不是 H3 生成提示词"></textarea>
                                    </label>
                                </div>
                                <div class="xiaoyue-mv-prompt-stages">
                                    <section class="creative-outline-stage project-outline-stage">
                                        <header class="xiaoyue-mv-outline-heading">
                                            <div>
                                                <div><strong>整片视觉镜头纲领</strong><span>视觉资产＋全片时间轴一次规划 · 非 H3 提示词</span></div>
                                                <button type="button" class="primary" data-action="generate-creative-outline" aria-busy="false"${reasoningButtonDisabled}>生成整片视觉纲领</button>
                                            </div>
                                            <output data-field="creative-outline-status" role="status">${reasoningModel}</output>
                                            <div class="xiaoyue-mv-outline-skill-line">
                                                <span>系统规则</span>
                                                <output data-field="outline-skill-status"></output>
                                            </div>
                                            <details class="xiaoyue-mv-outline-inputs">
                                                <summary>查看本轮输入与 Skill 规则</summary>
                                                <ul>
                                                    <li>内置中文 Skill 统一约束整片叙事、分镜完整性、时间线连续性和严格 JSON 输出</li>
                                                    <li>项目整体描述、总体提示词与全局禁用项</li>
                                                    <li>母带总帧、时长、BPM、节拍与保护点摘要</li>
                                                    <li>全部镜头绝对帧位、连接类型与镜头校正唱词</li>
                                                    <li>下方选定资产的画面、名称、类型和统一声明</li>
                                                    <li>视觉资产 A–H 只是本次理解对照，不会变成镜头参考序号或自动绑定</li>
                                                </ul>
                                                <p>本阶段不调用 H3 Skill，不写 H3 固定字段，也不生成最终单镜头提示词。</p>
                                            </details>
                                        </header>
                                        <fieldset class="xiaoyue-mv-outline-visual-picker">
                                            <legend>给总纲模型看的附件图 <span>本轮最多 8 项</span></legend>
                                            <div class="xiaoyue-mv-outline-visual-toolbar">
                                                <p><strong>从项目总资产库勾选</strong><span>图片直接送给视觉模型，视频读取代表帧；这里不会自动绑定到单镜头。</span></p>
                                                <button type="button" data-action="open-project">导入／管理总资产</button>
                                            </div>
                                            <output data-field="outline-visual-status" role="status"></output>
                                            <div class="xiaoyue-mv-outline-visual-assets" data-field="outline-visual-assets" role="list"></div>
                                        </fieldset>
                                    </section>
                                </div>
                            </section>
                            <section id="xiaoyue-panel-prompt" class="xiaoyue-mv-shot-editor xiaoyue-mv-detail-section" role="tabpanel" data-detail-panel="prompt" aria-labelledby="xiaoyue-tab-prompt" hidden>
                                <div class="xiaoyue-mv-prompt-stages">
                                    <section class="shot-outline-stage">
                                        <header><div><strong>当前镜头纲领</strong><span>来自项目总规划，可人工修订</span></div></header>
                                        <label><span class="xiaoyue-mv-visually-hidden">当前镜头完整分镜描述</span>
                                            <textarea data-field="creative-outline" rows="4" placeholder="用完整自然语言描述机位、景别、环境与连续性、人物、动作、运镜、光影、氛围、情绪及前后承接"></textarea>
                                        </label>
                                    </section>
                                    <section class="shot-assets-stage">
                                        <div class="xiaoyue-mv-shot-asset-actions">
                                            <label class="xiaoyue-mv-singing-toggle">
                                                <input type="checkbox" data-field="singing-performance" name="singing_performance">
                                                <span>口型</span>
                                            </label>
                                            <button type="button" data-action="open-shot-asset-picker">从资产库选择</button>
                                            <button type="button" class="primary" data-action="upload-shot-assets">上传并加入本镜头</button>
                                            <input data-field="shot-asset-file-input" type="file" accept="image/*,video/*,.mkv" multiple hidden>
                                        </div>
                                        <output class="xiaoyue-mv-shot-asset-status" data-field="shot-asset-status" role="status"></output>
                                        <div class="xiaoyue-mv-shot-asset-bindings" data-field="shot-asset-bindings"></div>
                                    </section>
                                    <section class="generation-prompt-stage">
                                        <header class="xiaoyue-mv-h3-prompt-heading">
                                            <div class="xiaoyue-mv-h3-prompt-copy">
                                                <strong>本镜头 H3 提示词</strong>
                                                <span id="xiaoyue-h3-overwrite-warning">成功即覆盖目标镜头；失败保留旧词，批量继续。</span>
                                            </div>
                                            <div class="xiaoyue-mv-h3-prompt-actions">
                                                <button type="button" data-action="generate-h3-current" aria-describedby="xiaoyue-h3-overwrite-warning" aria-busy="false"${reasoningButtonDisabled}>生成本镜</button>
                                                <button type="button" class="primary" data-action="generate-h3-all" aria-describedby="xiaoyue-h3-overwrite-warning" aria-busy="false"${reasoningButtonDisabled}>批量全生</button>
                                            </div>
                                        </header>
                                        <output class="xiaoyue-mv-h3-prompt-status" data-field="h3-prompt-status" role="status"></output>
                                        <label><span class="xiaoyue-mv-visually-hidden">本镜头 H3 提示词正文</span>
                                            <textarea data-field="prompt" rows="4" placeholder="当前镜头的主体、动作、镜头和场景要求"></textarea>
                                        </label>
                                    </section>
                                </div>
                            </section>
                            <section id="xiaoyue-panel-generation" class="xiaoyue-mv-shot-editor xiaoyue-mv-detail-section xiaoyue-mv-generation-panel" role="tabpanel" data-detail-panel="generation" aria-labelledby="xiaoyue-tab-generation" hidden>
                                <header class="xiaoyue-mv-generation-heading">
                                    <div><strong>镜头生成与版本</strong><span>保留完整生成窗口；最终选片后按剪切表处理</span></div>
                                    <div class="xiaoyue-mv-generation-actions">
                                        <button type="button" data-action="generate-shot-current">生成</button>
                                        <button type="button" class="primary" data-action="generate-shot-batch">全部生成…</button>
                                        <button type="button" data-action="download-shot-cut-list">导出剪切表</button>
                                        <button type="button" class="danger" data-action="cancel-shot-generation" disabled>取消</button>
                                    </div>
                                </header>
                                <output class="xiaoyue-mv-generation-status" data-field="generation-status" role="status">正在读取项目镜头版本…</output>
                                <div class="xiaoyue-mv-generation-gallery" data-field="generation-gallery"></div>
                            </section>
                            <section id="xiaoyue-panel-subtitle" class="xiaoyue-mv-subtitles xiaoyue-mv-detail-section" role="tabpanel" data-detail-panel="subtitle" aria-labelledby="xiaoyue-tab-subtitle">
                                <div class="xiaoyue-mv-subtitle-heading">
                                    <h3>字幕／歌词时间戳</h3>
                                    <button type="button" class="subtitle-recognize" data-action="recognize-subtitles" aria-busy="false"${providerButtonDisabled}>${hasTimestampedSubtitles ? "重跑 ASR 母版" : "运行 ASR 母版"} · ${asrAudioSource}</button>
                                </div>
                                <output class="xiaoyue-mv-subtitle-status" data-field="subtitle-status" role="status">${providerStatus}</output>
                                <section class="xiaoyue-mv-subtitle-final" aria-labelledby="${shotSubtitleId}-label">
                                    <div class="xiaoyue-mv-subtitle-final-heading">
                                        <div>
                                            <strong id="${shotSubtitleId}-label" data-field="shot-subtitle-final-heading">镜头字幕校正轨</strong>
                                            <output data-field="shot-subtitle-final-range"></output>
                                        </div>
                                        <button type="button" data-action="fill-shot-subtitle">参考填入中间</button>
                                    </div>
                                    <p id="${shotSubtitleId}-hint">三格文字都由用户填写；只有中间正式字幕用于最终字幕输出，前＋中＋后仅合并为 H3 唱词上下文。</p>
                                    <label class="xiaoyue-mv-visually-hidden" for="${shotSubtitleId}">当前镜头正式字幕</label>
                                    <table class="xiaoyue-mv-subtitle-table" data-field="shot-subtitle-table" aria-describedby="${shotSubtitleId}-hint">
                                        <thead>
                                            <tr>
                                                <th id="${shotSubtitleId}-leading-heading" scope="col" data-field="shot-subtitle-leading-heading">前扩</th>
                                                <th id="${shotSubtitleId}-main-heading" scope="col" data-field="shot-subtitle-main-heading">正式字幕</th>
                                                <th id="${shotSubtitleId}-trailing-heading" scope="col" data-field="shot-subtitle-trailing-heading">后扩</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            <tr>
                                                <td class="extension leading" data-field="shot-subtitle-leading-cell">
                                                    <input data-field="shot-subtitle-leading" type="text" aria-labelledby="${shotSubtitleId}-leading-heading" spellcheck="true" placeholder="输入前扩歌词">
                                                </td>
                                                <td class="main" data-field="shot-subtitle-main-cell">
                                                    <input id="${shotSubtitleId}" data-field="shot-subtitle-track" type="text" aria-labelledby="${shotSubtitleId}-main-heading" aria-describedby="${shotSubtitleId}-hint" spellcheck="true" placeholder="当前镜头正式字幕">
                                                </td>
                                                <td class="extension trailing" data-field="shot-subtitle-trailing-cell">
                                                    <input data-field="shot-subtitle-trailing" type="text" aria-labelledby="${shotSubtitleId}-trailing-heading" spellcheck="true" placeholder="输入后扩歌词">
                                                </td>
                                            </tr>
                                        </tbody>
                                    </table>
                                    <output data-field="shot-subtitle-ai-text" hidden></output>
                                </section>
                                <div class="xiaoyue-mv-subtitle-scope">
                                    <div>
                                        <strong data-field="shot-subtitle-heading">当前镜头附近母版参考</strong>
                                        <span>前后一条仅供复制；“填入当前参考”只取与镜头相交的字幕。</span>
                                    </div>
                                    <div class="xiaoyue-mv-cue-list" data-field="shot-subtitle-list"></div>
                                </div>
                                <details class="xiaoyue-mv-master-subtitles">
                                    <summary><span data-field="master-subtitle-heading">母版字幕总表</span></summary>
                                    <p>这里修改错字会按 cue ID 同步到所有母版参考区；不会覆盖已经整理好的镜头校正轨。</p>
                                    <div class="xiaoyue-mv-cue-list" data-field="master-subtitle-list"></div>
                                </details>
                                <details class="xiaoyue-mv-subtitle-import">
                                    <summary>粘贴／替换时间戳字幕</summary>
                                    <p>支持 SRT、LRC、<code>(start, end) text</code> 或 JSON；点击应用后才替换母版。</p>
                                    <textarea rows="6" data-field="subtitle-source" placeholder="在此粘贴字幕或歌词"></textarea>
                                    <button type="button" data-action="apply-subtitle-source">应用到母版</button>
                                </details>
                            </section>
                        </div>
                    </section>
                    <aside class="xiaoyue-mv-sidebar">
                        <div class="xiaoyue-mv-sidebar-title">
                            <div>
                                <p class="xiaoyue-mv-kicker">逻辑分镜</p>
                                <h3>音乐段落</h3>
                            </div>
                            <span data-field="shot-count"></span>
                        </div>
                        <div class="xiaoyue-mv-shot-list" role="list"></div>
                    </aside>
                </main>
            </div>`;
        return dialog;
    }

    field(name) {
        return this.dialog.querySelector(`[data-field="${name}"]`);
    }

    openShotAssetPicker() {
        this.selectedShotAssetIds.clear();
        this.field("shot-asset-search").value = "";
        this.renderShotAssetPicker();
        const picker = this.dialog.querySelector(".xiaoyue-mv-shot-asset-picker");
        if (!picker.open) picker.showModal();
    }

    closeShotAssetPicker() {
        const picker = this.dialog.querySelector(".xiaoyue-mv-shot-asset-picker");
        if (picker?.open) picker.close();
    }

    renderShotAssetPicker() {
        const list = this.field("shot-asset-picker-list");
        const shot = this.plan.shots[this.selectedShot];
        const bound = new Set((shot?.asset_bindings || []).map((binding) => binding.asset_id));
        const query = this.field("shot-asset-search").value.trim().toLocaleLowerCase();
        const categoryNames = { character: "角色", scene: "场景", prop: "道具" };
        const available = this.plan.assets.filter((asset) => {
            if (bound.has(asset.id)) return false;
            const haystack = [
                asset.name,
                asset.declaration,
                asset.source?.filename,
                asset.source?.subfolder,
                categoryNames[asset.category],
            ].join(" ").toLocaleLowerCase();
            return !query || haystack.includes(query);
        });
        list.replaceChildren();
        for (const asset of available) {
            const card = document.createElement("article");
            card.className = "xiaoyue-mv-shot-asset-picker-card";
            card.dataset.shotPickerCardId = asset.id;
            card.appendChild(this.createAssetPreviewButton(asset, "picker-preview"));
            const copy = document.createElement("div");
            const name = document.createElement("strong");
            name.textContent = asset.name;
            const meta = document.createElement("span");
            meta.textContent = `${categoryNames[asset.category] || "资产"} · ${asset.declaration || "尚未填写统一声明"}`;
            copy.append(name, meta);
            const select = document.createElement("label");
            select.className = "picker-select";
            const checkbox = document.createElement("input");
            checkbox.type = "checkbox";
            checkbox.dataset.shotPickerAssetId = asset.id;
            checkbox.checked = this.selectedShotAssetIds.has(asset.id);
            checkbox.setAttribute("aria-label", `选择资产 ${asset.name}`);
            const selectText = document.createElement("span");
            selectText.textContent = "加入";
            select.append(checkbox, selectText);
            card.append(copy, select);
            list.appendChild(card);
        }
        if (!available.length) {
            const empty = document.createElement("p");
            empty.className = "xiaoyue-mv-shot-asset-empty";
            empty.textContent = query
                ? "没有匹配且尚未绑定的项目资产。"
                : "项目资产库中没有可继续加入当前镜头的资产。";
            list.appendChild(empty);
        }
        this.updateShotAssetPickerStatus(available.length);
    }

    updateShotAssetPickerStatus(visibleCount = null) {
        const count = visibleCount ?? this.field("shot-asset-picker-list")
            .querySelectorAll("[data-shot-picker-asset-id]").length;
        this.field("shot-asset-picker-status").textContent =
            `${count} 个可选资产 · 已选 ${this.selectedShotAssetIds.size} 个`;
        this.dialog.querySelector('[data-action="confirm-shot-assets"]').disabled =
            this.selectedShotAssetIds.size === 0;
    }

    confirmShotAssetSelection() {
        const shot = this.plan.shots[this.selectedShot];
        if (!shot || !this.selectedShotAssetIds.size) return;
        shot.asset_bindings ||= [];
        let added = 0;
        for (const assetId of this.selectedShotAssetIds) {
            if (shot.asset_bindings.some((binding) => binding.asset_id === assetId)) continue;
            shot.asset_bindings.push({ asset_id: assetId, usage: "" });
            added += 1;
        }
        shot.asset_bindings.forEach((binding, index) => { binding.order = index + 1; });
        this.setDirty();
        this.closeShotAssetPicker();
        this.renderShotAssetBindings();
        this.renderPromptContext();
        this.renderShotCards([this.selectedShot]);
        this.field("shot-asset-status").textContent =
            `已把 ${added} 个项目资产加入当前镜头；参考编号按选择顺序追加。`;
    }

    bindStaticEvents() {
        this.timelineViewport = this.dialog.querySelector(".xiaoyue-mv-timeline-viewport");
        this.timelineContent = this.dialog.querySelector(".xiaoyue-mv-timeline-content");
        this.canvas = this.timelineContent.querySelector("canvas");
        this.clipLayer = this.timelineContent.querySelector(".xiaoyue-mv-clip-layer");
        this.fxLayer = this.timelineContent.querySelector(".xiaoyue-mv-fx-layer");
        this.playhead = this.timelineContent.querySelector(".xiaoyue-mv-playhead");
        this.audio = this.dialog.querySelector("audio");
        const source = audioUrl(this.audioPreviews[this.audioSource]);
        if (source) this.audio.src = source;
        else this.audio.hidden = true;

        for (const button of this.dialog.querySelectorAll('[data-action="open-project"]')) {
            button.addEventListener("click", () => this.setProjectPanel(true));
        }
        this.dialog.querySelector('[data-action="close-project"]').addEventListener("click", () => {
            this.setProjectPanel(false);
        });
        const projectFields = {
            "project-name": "name",
            "project-description": "description",
            "project-overall-prompt": "overall_prompt",
            "project-negative-prompt": "negative_prompt",
        };
        for (const [fieldName, key] of Object.entries(projectFields)) {
            this.field(fieldName).addEventListener("input", (event) => {
                this.plan.project[key] = event.target.value;
                if (key === "overall_prompt") {
                    this.field("outline-overall-prompt").value = event.target.value;
                }
                this.setDirty();
                if (key === "name" || key === "overall_prompt") this.renderPromptContext();
            });
        }
        this.field("creative-plan-overview").addEventListener("input", (event) => {
            this.plan.creative_plan.overview = event.target.value;
            this.field("outline-plan-overview").value = event.target.value;
            this.plan.creative_plan.revision += 1;
            this.setDirty("MV 镜头纲领总览已修改；独立镜头提示词应按新纲领重新审阅。", false);
            this.renderPromptContext();
        });
        this.field("outline-overall-prompt").addEventListener("input", (event) => {
            this.plan.project.overall_prompt = event.target.value;
            this.field("project-overall-prompt").value = event.target.value;
            this.setDirty();
            this.renderPromptContext();
        });
        this.field("outline-plan-overview").addEventListener("input", (event) => {
            this.plan.creative_plan.overview = event.target.value;
            this.field("creative-plan-overview").value = event.target.value;
            this.plan.creative_plan.revision += 1;
            this.setDirty("MV 镜头纲领总览已修改；独立镜头提示词应按新纲领重新审阅。", false);
            this.renderPromptContext();
        });
        this.field("project-aspect-ratio").addEventListener("change", () => {
            this.updateProjectGenerationSpec();
        });
        this.field("project-first-pass-megapixels").addEventListener("change", () => {
            this.updateProjectGenerationSpec();
        });
        this.field("project-second-pass-megapixels").addEventListener("change", () => {
            this.updateProjectGenerationSpec();
        });
        this.field("project-outline-skill").addEventListener("change", (event) => {
            this.updateProjectSkillTemplate("visual_outline", event.target.value);
        });
        this.field("project-h3-skill").addEventListener("change", (event) => {
            this.updateProjectSkillTemplate("h3_prompt", event.target.value);
        });
        this.dialog.querySelector('[data-action="refresh-project-skill-templates"]')
            .addEventListener("click", () => this.refreshProjectSkillTemplateLibrary());
        this.dialog.querySelector('[data-action="upload-assets"]').addEventListener("click", () => {
            this.field("asset-file-input").click();
        });
        this.field("asset-file-input").addEventListener("change", (event) => {
            this.uploadLocalAssets([...event.target.files]);
            event.target.value = "";
        });
        this.dialog.querySelector('[data-action="browse-input-assets"]').addEventListener("click", () => {
            this.openInputAssetPicker();
        });
        this.dialog.querySelector('[data-action="close-input-picker"]').addEventListener("click", () => {
            this.closeInputAssetPicker();
        });
        this.dialog.querySelector('[data-action="confirm-input-assets"]').addEventListener("click", () => {
            this.importSelectedInputAssets();
        });
        this.field("input-asset-search").addEventListener("input", () => this.renderInputAssetCatalog());
        const inputPicker = this.dialog.querySelector(".xiaoyue-mv-input-picker");
        inputPicker.addEventListener("cancel", (event) => {
            event.preventDefault();
            this.closeInputAssetPicker();
        });
        this.dialog.querySelector('[data-action="open-shot-asset-picker"]').addEventListener("click", () => {
            this.openShotAssetPicker();
        });
        this.dialog.querySelector('[data-action="close-shot-asset-picker"]').addEventListener("click", () => {
            this.closeShotAssetPicker();
        });
        this.dialog.querySelector('[data-action="confirm-shot-assets"]').addEventListener("click", () => {
            this.confirmShotAssetSelection();
        });
        this.field("shot-asset-search").addEventListener("input", () => this.renderShotAssetPicker());
        this.field("shot-asset-picker-list").addEventListener("change", (event) => {
            const checkbox = event.target.closest?.("[data-shot-picker-asset-id]");
            if (!checkbox) return;
            if (checkbox.checked) this.selectedShotAssetIds.add(checkbox.dataset.shotPickerAssetId);
            else this.selectedShotAssetIds.delete(checkbox.dataset.shotPickerAssetId);
            this.updateShotAssetPickerStatus();
        });
        this.field("shot-asset-picker-list").addEventListener("click", (event) => {
            if (event.target.closest?.(".picker-preview-button, .picker-select")) return;
            const card = event.target.closest?.("[data-shot-picker-card-id]");
            if (!card) return;
            card.querySelector("[data-shot-picker-asset-id]")?.click();
        });
        const shotAssetPicker = this.dialog.querySelector(".xiaoyue-mv-shot-asset-picker");
        shotAssetPicker.addEventListener("cancel", (event) => {
            event.preventDefault();
            this.closeShotAssetPicker();
        });
        this.dialog.querySelector('[data-action="upload-shot-assets"]').addEventListener("click", () => {
            this.field("shot-asset-file-input").click();
        });
        this.field("shot-asset-file-input").addEventListener("change", (event) => {
            this.uploadLocalAssets([...event.target.files], {
                bindCurrentShot: true,
                statusField: "shot-asset-status",
                triggerAction: "upload-shot-assets",
            });
            event.target.value = "";
        });
        this.dialog.querySelector('[data-action="close-asset-preview"]').addEventListener("click", () => {
            this.closeAssetPreview();
        });
        const assetPreviewDialog = this.dialog.querySelector(".xiaoyue-mv-asset-preview-dialog");
        assetPreviewDialog.addEventListener("cancel", (event) => {
            event.preventDefault();
            this.closeAssetPreview();
        });
        this.dialog.querySelector('[data-action="asset-preview-zoom-out"]').addEventListener("click", () => {
            this.setAssetPreviewZoom(this.assetPreviewZoom / 1.25);
        });
        this.dialog.querySelector('[data-action="asset-preview-zoom-in"]').addEventListener("click", () => {
            this.setAssetPreviewZoom(this.assetPreviewZoom * 1.25);
        });
        this.dialog.querySelector('[data-action="asset-preview-fit"]').addEventListener("click", () => {
            this.resetAssetPreviewTransform();
        });
        const assetPreviewBody = this.field("asset-preview-body");
        assetPreviewBody.addEventListener("wheel", (event) => {
            if (!this.assetPreviewImage) return;
            event.preventDefault();
            const lineHeight = 16;
            const pageHeight = Math.max(1, assetPreviewBody.clientHeight);
            const delta = event.deltaMode === WheelEvent.DOM_DELTA_LINE
                ? event.deltaY * lineHeight
                : event.deltaMode === WheelEvent.DOM_DELTA_PAGE
                    ? event.deltaY * pageHeight
                    : event.deltaY;
            this.setAssetPreviewZoom(
                this.assetPreviewZoom * Math.exp(-delta * 0.0015),
                event.clientX,
                event.clientY,
            );
        }, { passive: false });
        assetPreviewBody.addEventListener("pointerdown", (event) => {
            if (!this.assetPreviewImage || this.assetPreviewZoom <= 1 || event.button !== 0) return;
            this.assetPreviewPointer = {
                id: event.pointerId,
                clientX: event.clientX,
                clientY: event.clientY,
                panX: this.assetPreviewPan.x,
                panY: this.assetPreviewPan.y,
            };
            assetPreviewBody.setPointerCapture(event.pointerId);
            assetPreviewBody.classList.add("dragging");
            event.preventDefault();
        });
        assetPreviewBody.addEventListener("pointermove", (event) => {
            const pointer = this.assetPreviewPointer;
            if (!pointer || pointer.id !== event.pointerId
                || !assetPreviewBody.hasPointerCapture(event.pointerId)) return;
            this.assetPreviewPan.x = pointer.panX + event.clientX - pointer.clientX;
            this.assetPreviewPan.y = pointer.panY + event.clientY - pointer.clientY;
            this.applyAssetPreviewTransform();
        });
        const stopAssetPreviewDrag = (event) => {
            if (this.assetPreviewPointer?.id !== event.pointerId) return;
            if (assetPreviewBody.hasPointerCapture(event.pointerId)) {
                assetPreviewBody.releasePointerCapture(event.pointerId);
            }
            this.assetPreviewPointer = null;
            assetPreviewBody.classList.remove("dragging");
        };
        assetPreviewBody.addEventListener("pointerup", stopAssetPreviewDrag);
        assetPreviewBody.addEventListener("pointercancel", stopAssetPreviewDrag);
        assetPreviewBody.addEventListener("lostpointercapture", () => {
            this.assetPreviewPointer = null;
            assetPreviewBody.classList.remove("dragging");
        });
        assetPreviewBody.addEventListener("keydown", (event) => {
            if (!this.assetPreviewImage) return;
            if (event.key === "+" || event.key === "=") {
                event.preventDefault();
                this.setAssetPreviewZoom(this.assetPreviewZoom * 1.25);
                return;
            }
            if (event.key === "-") {
                event.preventDefault();
                this.setAssetPreviewZoom(this.assetPreviewZoom / 1.25);
                return;
            }
            if (event.key === "0") {
                event.preventDefault();
                this.resetAssetPreviewTransform();
                return;
            }
            if (this.assetPreviewZoom <= 1) return;
            const panSteps = {
                ArrowLeft: [40, 0],
                ArrowRight: [-40, 0],
                ArrowUp: [0, 40],
                ArrowDown: [0, -40],
            };
            const step = panSteps[event.key];
            if (!step) return;
            event.preventDefault();
            this.assetPreviewPan.x += step[0];
            this.assetPreviewPan.y += step[1];
            this.applyAssetPreviewTransform();
        });
        this.assetPreviewResizeObserver = new ResizeObserver(() => {
            if (this.assetPreviewImage) this.fitAssetPreviewImage();
        });
        this.assetPreviewResizeObserver.observe(assetPreviewBody);
        this.dialog.addEventListener("click", (event) => {
            const preview = event.target.closest?.("[data-asset-preview-id]");
            if (!preview) return;
            this.openAssetPreview(preview.dataset.assetPreviewId);
        });
        this.field("input-asset-list").addEventListener("change", (event) => {
            const checkbox = event.target.closest?.('[data-input-asset-key]');
            if (!checkbox) return;
            if (checkbox.checked) this.selectedInputAssetKeys.add(checkbox.dataset.inputAssetKey);
            else this.selectedInputAssetKeys.delete(checkbox.dataset.inputAssetKey);
            this.updateInputPickerStatus();
        });
        this.field("asset-list").addEventListener("input", (event) => {
            const control = event.target.closest?.("[data-asset-field]");
            const card = event.target.closest?.("[data-asset-id]");
            if (!control || !card) return;
            const asset = this.plan.assets.find((item) => item.id === card.dataset.assetId);
            if (!asset) return;
            asset[control.dataset.assetField] = event.target.value;
            this.setDirty();
            this.renderOutlineVisualAssets();
        });
        this.field("outline-visual-assets").addEventListener("change", (event) => {
            const checkbox = event.target.closest?.("[data-outline-visual-id]");
            if (!checkbox) return;
            const asset = this.plan.assets.find(
                (item) => item.id === checkbox.dataset.outlineVisualId,
            );
            if (!asset) return;
            const selectedCount = this.plan.assets.filter((item) => item.outline_visual).length;
            if (checkbox.checked && !asset.outline_visual && selectedCount >= 8) {
                checkbox.checked = false;
                this.field("outline-visual-status").value = "单次视觉理解最多 8 项；请先取消一项。";
                return;
            }
            asset.outline_visual = checkbox.checked;
            this.setDirty("总纲视觉资产已调整；下次生成会按新选择重新理解。", false);
            this.renderOutlineVisualAssets();
        });
        this.field("asset-list").addEventListener("click", (event) => {
            const button = event.target.closest?.("[data-asset-action]");
            const card = event.target.closest?.("[data-asset-id]");
            if (!button || !card) return;
            if (button.dataset.assetAction === "bind-current") {
                this.toggleShotAsset(card.dataset.assetId);
            } else if (button.dataset.assetAction === "remove") {
                this.removeProjectAsset(card.dataset.assetId);
            }
        });
        this.field("shot-asset-bindings").addEventListener("click", (event) => {
            const button = event.target.closest?.("[data-shot-asset-action]");
            if (!button) return;
            const assetId = button.dataset.bindingAssetId;
            if (button.dataset.shotAssetAction === "add") this.setShotAssetBinding(assetId, true);
            if (button.dataset.shotAssetAction === "remove") this.setShotAssetBinding(assetId, false);
            if (button.dataset.shotAssetAction === "move-up") this.moveShotAssetBinding(assetId, -1);
            if (button.dataset.shotAssetAction === "move-down") this.moveShotAssetBinding(assetId, 1);
            if (button.dataset.shotAssetAction === "spread-all") {
                this.spreadShotAssetToAllShots(assetId);
            }
        });
        this.field("shot-asset-bindings").addEventListener("input", (event) => {
            const input = event.target.closest?.("[data-binding-usage]");
            if (!input) return;
            const shot = this.plan.shots[this.selectedShot];
            const binding = shot?.asset_bindings?.find(
                (item) => item.asset_id === input.dataset.bindingAssetId,
            );
            if (!binding) return;
            binding.usage = input.value;
            const spreadButton = input.closest(".xiaoyue-mv-bound-asset-row")
                ?.querySelector('[data-shot-asset-action="spread-all"]');
            if (spreadButton) {
                const hasUsage = Boolean(input.value.trim());
                spreadButton.disabled = !hasUsage || this.plan.shots.length <= 1;
                spreadButton.title = this.plan.shots.length <= 1
                    ? "当前项目只有一个镜头"
                    : hasUsage
                        ? `把当前用法铺到其余 ${this.plan.shots.length - 1} 个镜头`
                        : "请先填写本镜头用法";
            }
            this.setDirty();
        });

        for (const button of this.dialog.querySelectorAll("[data-playback-mode]")) {
            button.addEventListener("click", () => this.setPlaybackMode(button.dataset.playbackMode));
        }
        for (const button of this.dialog.querySelectorAll("[data-audio-source]")) {
            button.addEventListener("click", () => this.setAudioSource(button.dataset.audioSource));
        }
        for (const button of this.dialog.querySelectorAll("[data-ruler-mode]")) {
            button.addEventListener("click", () => this.setRulerMode(button.dataset.rulerMode));
        }
        for (const button of this.dialog.querySelectorAll("[data-detail-target]")) {
            button.addEventListener("click", () => this.setDetailSection(button.dataset.detailTarget));
            button.addEventListener("keydown", (event) => {
                if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
                event.preventDefault();
                const current = this.detailKeys.indexOf(button.dataset.detailTarget);
                const direction = event.key === "ArrowRight" ? 1 : -1;
                const next = (current + direction + this.detailKeys.length) % this.detailKeys.length;
                this.setDetailSection(this.detailKeys[next]);
                this.dialog.querySelector(`[data-detail-target="${this.detailKeys[next]}"]`)?.focus();
            });
        }
        this.dialog.querySelector('[data-action="close"]').addEventListener("click", () => this.close());
        this.dialog.querySelector('[data-action="save"]').addEventListener("click", () => this.save(false));
        this.dialog.querySelector('[data-action="generate-creative-outline"]').addEventListener("click", () => {
            this.runCreativeOutline();
        });
        this.dialog.querySelector('[data-action="generate-h3-current"]').addEventListener("click", () => {
            this.runH3PromptGeneration("current");
        });
        this.dialog.querySelector('[data-action="generate-h3-all"]').addEventListener("click", () => {
            this.runH3PromptGeneration("all");
        });
        this.dialog.querySelector('[data-action="generate-shot-current"]').addEventListener("click", () => {
            this.startShotGeneration([String(this.plan.shots[this.selectedShot]?.id || "")]);
        });
        this.dialog.querySelector('[data-action="generate-shot-batch"]').addEventListener("click", () => {
            try {
                this.openGenerationPicker();
            } catch (error) {
                const message = `镜头选择框未打开：${error.message || error}`;
                this.generationStatus(message);
                app.extensionManager?.toast?.add?.({
                    severity: "error", summary: "无法选择批量镜头",
                    detail: message, life: 8000,
                });
            }
        });
        this.dialog.querySelector('[data-action="cancel-shot-generation"]').addEventListener("click", () => {
            this.cancelShotGeneration();
        });
        this.dialog.querySelector('[data-action="download-shot-cut-list"]').addEventListener("click", () => {
            this.downloadShotCutList();
        });
        for (const button of this.dialog.querySelectorAll('[data-action="close-generation-picker"]')) {
            button.addEventListener("click", () => this.dialog.querySelector(".xiaoyue-mv-generation-picker").close());
        }
        this.dialog.querySelector('[data-action="confirm-generation-picker"]').addEventListener("click", async () => {
            const ids = [...this.field("generation-picker-list").querySelectorAll('input[type="checkbox"]:checked')]
                .map((input) => input.value);
            const status = this.field("generation-picker-status");
            if (!ids.length) {
                status.textContent = "请至少选择一个有提示词的镜头。";
                status.hidden = false;
                return;
            }
            status.textContent = "正在校验工作流并启动任务…";
            status.hidden = false;
            const result = await this.startShotGeneration(ids, { confirmed: true });
            if (result.started) {
                this.dialog.querySelector(".xiaoyue-mv-generation-picker").close();
            } else {
                status.textContent = result.error;
            }
            this.updateGenerationPickerCount();
        });
        this.field("generation-select-all").addEventListener("change", (event) => {
            for (const input of this.field("generation-picker-list").querySelectorAll('input[type="checkbox"]')) {
                if (!input.disabled) input.checked = event.target.checked;
            }
            this.updateGenerationPickerCount();
        });
        this.field("generation-picker-list").addEventListener("change", () => this.updateGenerationPickerCount());
        this.dialog.querySelector('[data-action="close-generation-preview"]').addEventListener("click", () => {
            this.closeGenerationPreview();
        });
        this.dialog.querySelector(".xiaoyue-mv-generation-preview").addEventListener("close", () => {
            const video = this.dialog.querySelector(".xiaoyue-mv-generation-preview video");
            video.pause();
            video.removeAttribute("src");
            video.load();
        });
        this.dialog.querySelector('[data-action="recognize-subtitles"]').addEventListener("click", () => {
            this.runSubtitleRecognition();
        });
        this.dialog.querySelector('[data-action="apply-subtitle-source"]').addEventListener("click", () => {
            this.applySubtitleSource();
        });
        this.dialog.querySelector('[data-action="fill-shot-subtitle"]').addEventListener("click", () => {
            this.fillShotSubtitleFromReference();
        });
        this.dialog.querySelector('[data-action="add-splice"]').addEventListener("click", () => {
            this.splitShot(this.selectedShot);
        });
        this.dialog.querySelector('[data-action="remove-splice"]').addEventListener("click", () => {
            this.deleteShot(this.selectedShot);
        });
        this.dialog.querySelector('[data-action="replan"]').addEventListener("click", () => {
            this.openAutomaticReplanDialog();
        });
        for (const button of this.dialog.querySelectorAll('[data-action="close-replan"]')) {
            button.addEventListener("click", () => this.closeAutomaticReplanDialog());
        }
        this.dialog.querySelector('[data-action="confirm-replan"]').addEventListener("click", () => {
            this.requestAutomaticReplan();
        });
        this.field("replan-h3-max-frames").addEventListener("change", () => {
            this.updateAutomaticReplanSummary();
        });
        this.field("replan-target-shot-seconds").addEventListener("input", () => {
            this.updateAutomaticReplanSummary();
        });
        const replanDialog = this.dialog.querySelector(".xiaoyue-mv-replan-dialog");
        replanDialog.addEventListener("cancel", (event) => {
            event.preventDefault();
            this.closeAutomaticReplanDialog();
        });
        this.dialog.addEventListener("cancel", (event) => {
            event.preventDefault();
            this.close();
        });
        this.dialog.addEventListener("close", () => this.destroy());
        this.dialog.addEventListener("pointerdown", (event) => {
            this.playbackShortcutArmed = this.isPlaybackShortcutZone(event.target);
        }, true);
        this.dialog.addEventListener("focusin", (event) => {
            this.playbackShortcutArmed = this.isPlaybackShortcutZone(event.target);
        }, true);
        this.playbackKeyDownHandler = (event) => this.handlePlaybackKeyDown(event);
        this.playbackKeyUpHandler = (event) => this.handlePlaybackKeyUp(event);
        window.addEventListener("keydown", this.playbackKeyDownHandler, true);
        window.addEventListener("keyup", this.playbackKeyUpHandler, true);

        this.timelineContent.addEventListener("click", (event) => {
            if (event.target.closest(".xiaoyue-mv-clip, .xiaoyue-mv-boundary")) return;
            this.timelineViewport.focus({ preventScroll: true });
            const rect = this.timelineContent.getBoundingClientRect();
            const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
            const total = this.plan.audio.timeline_frames;
            this.playheadFrame = Math.round(total * ratio);
            const shotIndex = this.shotIndexAtFrame(this.playheadFrame);
            if (shotIndex >= 0) {
                this.selectedShot = shotIndex;
                this.selectedSplice = null;
                this.renderTracks();
                this.updateShotListSelection();
                this.renderShotEditor();
            }
            if (!this.audio.hidden) this.audio.currentTime = frameSeconds(this.playheadFrame, this.plan.h3.fps);
            this.renderPlayhead();
            if (shotIndex >= 0) {
                const scope = this.playbackMode === "shot" ? "当前镜头出点" : "整首结尾";
                this.setTimelineFeedback(
                    `已激活镜头 ${this.shotEditorialLabel(shotIndex)}；空格从 ${secondsLabel(frameSeconds(this.playheadFrame, this.plan.h3.fps))} 播放到${scope}。`,
                    "snapped",
                );
            }
        });
        const zoom = this.field("timeline-zoom");
        zoom.addEventListener("input", () => this.setZoom(Number(zoom.value)));
        this.dialog.querySelector('[data-action="zoom-out"]').addEventListener("click", () => {
            this.setZoom(this.zoom - 1);
        });
        this.dialog.querySelector('[data-action="zoom-in"]').addEventListener("click", () => {
            this.setZoom(this.zoom + 1);
        });
        this.dialog.querySelector('[data-action="zoom-fit"]').addEventListener("click", () => {
            this.setZoom(1);
            this.timelineViewport.scrollLeft = 0;
        });
        this.timelineViewport.addEventListener("wheel", (event) => {
            if (!event.ctrlKey) return;
            event.preventDefault();
            this.setZoom(this.zoom + (event.deltaY < 0 ? 1 : -1));
        }, { passive: false });
        this.audio.addEventListener("timeupdate", () => this.syncPlayheadFromAudio());
        this.audio.addEventListener("play", () => {
            if (this.playbackMode === "shot") {
                const range = this.selectedPlaybackRange();
                if (this.audio.currentTime < range.start || this.audio.currentTime >= range.end) {
                    this.seekAudio(range.start);
                }
            }
            this.startPlaybackMonitor();
        });
        this.audio.addEventListener("pause", () => this.stopPlaybackMonitor());
        this.audio.addEventListener("ended", () => this.stopPlaybackMonitor());

        const editable = {
            "material-kind": (shot, value) => { shot.material.kind = value; },
            "material-reference": (shot, value) => { shot.material.reference = value; },
            "creative-outline": (shot, value) => { shot.creative_outline = value; },
            prompt: (shot, value) => { shot.prompt = value; },
        };
        for (const [name, apply] of Object.entries(editable)) {
            const control = this.field(name);
            if (!control) continue;
            control.addEventListener("input", (event) => {
                const editedShotIndex = this.selectedShot;
                const shot = this.plan.shots[editedShotIndex];
                apply(shot, event.target.value);
                this.setDirty("镜头内容已修改；保存后由提示词或生成链路读取。", false);
                this.renderShotList();
                if (name === "prompt") {
                    this.fitPromptTextarea();
                    this.updateH3PromptStatus(editedShotIndex);
                    this.renderShotGenerationGallery();
                }
                if (name === "prompt" || name === "creative-outline") {
                    this.scheduleShotCheckpoint(shot);
                }
            });
        }
        this.field("singing-performance").addEventListener("change", (event) => {
            const shot = this.plan.shots[this.selectedShot];
            if (!shot) return;
            shot.singing_performance = event.target.checked;
            this.setDirty("口型开关已修改；重新生成本镜提示词会直接读取当前状态，无需先保存计划。", false);
            this.persistShotPatch(shot);
            this.renderShotList();
        });

        this.field("transition-type").addEventListener("change", (event) => {
            const transition = this.plan.transitions[this.selectedShot];
            if (!transition) return;
            transition.type = event.target.value;
            if (transition.type === "crossfade") {
                this.setCrossfadeDuration(this.selectedShot, transition.duration_frames);
            } else {
                this.recalculateAutomaticCrossfadeSafety();
            }
            this.setDirty();
            this.renderAll();
        });
        for (const [name, key] of [
            ["context-frames", "context_frames"],
            ["duration-frames", "duration_frames"],
        ]) {
            this.field(name).addEventListener("input", (event) => {
                const transition = this.plan.transitions[this.selectedShot];
                if (!transition) return;
                transition[key] = Number(event.target.value);
                if (key === "duration_frames") {
                    this.setCrossfadeDuration(this.selectedShot, transition[key]);
                } else {
                    this.recalculateAutomaticCrossfadeSafety();
                }
                this.setDirty();
                this.renderTracks();
                this.renderShotEditor();
                this.renderShotCards([this.selectedShot, this.selectedShot + 1]);
                this.renderWarnings();
            });
        }
        this.field("subtitle-source").addEventListener("input", () => {
            this.field("subtitle-status").value = "粘贴内容尚未应用，不会覆盖当前母版字幕。";
        });
        this.field("shot-subtitle-track").addEventListener("input", (event) => {
            this.updateShotSubtitleTrack(event.target.value);
        });
        this.field("shot-subtitle-leading").addEventListener("input", (event) => {
            this.updateShotSubtitleExtension("leading_text", event.target.value);
        });
        this.field("shot-subtitle-trailing").addEventListener("input", (event) => {
            this.updateShotSubtitleExtension("trailing_text", event.target.value);
        });
        this.dialog.querySelector(".xiaoyue-mv-subtitles").addEventListener("input", (event) => {
            const input = event.target.closest?.("[data-subtitle-cue-id]");
            if (!input) return;
            this.updateMasterCueText(input.dataset.subtitleCueId, input.value, input);
        });
    }

    setProjectPanel(open) {
        this.projectPanelOpen = Boolean(open);
        const panel = this.dialog.querySelector("[data-project-panel]");
        panel.hidden = !this.projectPanelOpen;
        this.dialog.querySelector(".xiaoyue-mv-main").inert = this.projectPanelOpen;
        this.dialog.querySelector(".xiaoyue-mv-transport").inert = this.projectPanelOpen;
        if (this.projectPanelOpen) {
            this.audio?.pause();
            this.renderProjectPanel();
            requestAnimationFrame(() => this.field("project-name")?.focus({ preventScroll: true }));
        } else {
            this.closeInputAssetPicker();
            this.renderShotEditor();
        }
    }

    renderProjectSummary() {
        const count = this.plan.assets.length;
        this.field("asset-count-header").textContent = String(count);
        this.field("asset-count").textContent = count ? `${count} 个公用资产` : "尚未导入资产";
    }

    renderProjectPanel() {
        const project = this.plan.project;
        const generationSpec = normalizeGenerationSpec(this.plan.generation_spec);
        this.plan.generation_spec = generationSpec;
        this.field("project-code").value = project.code;
        this.field("project-code").readOnly = true;
        this.field("project-name").value = project.name;
        this.field("project-description").value = project.description;
        this.field("project-overall-prompt").value = project.overall_prompt;
        this.field("creative-plan-overview").value = this.plan.creative_plan.overview;
        this.field("project-negative-prompt").value = project.negative_prompt;
        this.field("project-aspect-ratio").value = generationSpec.aspect_ratio;
        this.field("project-first-pass-megapixels").value = String(
            generationSpec.first_pass_megapixels);
        this.field("project-second-pass-megapixels").value = String(
            generationSpec.second_pass_megapixels);
        this.renderProjectSkillTemplates();
        this.renderProjectSummary();
        this.renderProjectAssets();
    }

    renderProjectSkillTemplates() {
        const stages = [
            ["visual_outline", "project-outline-skill"],
            ["h3_prompt", "project-h3-skill"],
        ];
        for (const [stage, fieldName] of stages) {
            const select = this.field(fieldName);
            const options = Array.isArray(this.skillTemplateLibrary?.[stage])
                ? this.skillTemplateLibrary[stage] : [];
            select.replaceChildren();
            for (const descriptor of options) {
                const option = document.createElement("option");
                option.value = String(descriptor.id || "");
                const version = descriptor.version && descriptor.version !== "unknown"
                    ? ` · v${descriptor.version}` : "";
                const language = descriptor.output_language
                    ? ` · ${descriptor.output_language}` : "";
                const source = descriptor.source === "bundled" ? " · 内置默认" : " · 插件目录";
                option.textContent = `${descriptor.display_name || descriptor.id} [${descriptor.id}]${version}${language}${source}`;
                select.append(option);
            }
            const selected = String(
                this.plan.skill_templates?.[stage] || DEFAULT_SKILL_TEMPLATES[stage]);
            if (![...select.options].some((option) => option.value === selected)) {
                const fallback = document.createElement("option");
                fallback.value = selected;
                fallback.textContent = `${selected}（当前项目记录，模板未安装）`;
                select.prepend(fallback);
            }
            select.value = selected;
        }
        this.field("project-skill-template-help").textContent = this.skillTemplateFolder
            ? `Skill 目录：${this.skillTemplateFolder}。默认模板随插件更新；新增自定义 Skill 后点“刷新 Skill 列表”。`
            : "默认与自定义 Skill 均从本插件目录读取。";
    }

    async refreshProjectSkillTemplateLibrary() {
        try {
            const response = await api.fetchApi("/xiaoyue-mv/skill-templates");
            const data = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(data.error || "读取模板库失败。");
            this.skillTemplateLibrary = data.templates && typeof data.templates === "object"
                ? data.templates : { visual_outline: [], h3_prompt: [] };
            this.skillTemplateFolder = String(data.folder_path || "");
            this.renderProjectSkillTemplates();
        } catch (error) {
            this.field("project-skill-template-help").textContent =
                `模板库读取失败：${error?.message || String(error)}。`;
        }
    }

    updateProjectSkillTemplate(stage, skillId) {
        if (!(stage in DEFAULT_SKILL_TEMPLATES)) return;
        this.plan.skill_templates[stage] = String(skillId || DEFAULT_SKILL_TEMPLATES[stage]);
        const stageName = stage === "visual_outline" ? "整片视觉纲领" : "单镜 H3 提示词";
        this.setDirty(`${stageName} Skill 模板已更新；下一次生成使用新模板，现有结果不会自动覆盖。`, false);
        if (stage === "visual_outline") {
            const descriptor = (this.skillTemplateLibrary.visual_outline || [])
                .find((item) => item.id === this.plan.skill_templates.visual_outline);
            if (descriptor) this.outlineSkill = descriptor;
        }
        this.renderPromptContext();
    }

    updateProjectGenerationSpec() {
        const generationSpec = normalizeGenerationSpec({
            aspect_ratio: this.field("project-aspect-ratio").value,
            first_pass_megapixels: this.field("project-first-pass-megapixels").value,
            second_pass_megapixels: this.field("project-second-pass-megapixels").value,
        });
        this.plan.generation_spec = generationSpec;
        this.field("project-aspect-ratio").value = generationSpec.aspect_ratio;
        this.field("project-first-pass-megapixels").value = String(
            generationSpec.first_pass_megapixels);
        this.field("project-second-pass-megapixels").value = String(
            generationSpec.second_pass_megapixels);
        this.setDirty("影片画幅与双采 MP 已更新；时间轴和所有切点保持不变。", false);
        this.renderPromptContext();
    }

    createAssetPreview(source, className = "", { full = false, alt = "" } = {}) {
        const url = audioUrl(source);
        if (!url) {
            const empty = document.createElement("span");
            empty.className = `${className} asset-preview-empty`.trim();
            empty.textContent = "无预览";
            return empty;
        }
        if (source.media_type === "video") {
            const video = document.createElement("video");
            video.className = className;
            video.src = url;
            video.preload = "metadata";
            video.controls = full;
            video.muted = true;
            video.playsInline = true;
            if (!full) {
                video.tabIndex = -1;
                video.setAttribute("aria-hidden", "true");
            }
            return video;
        }
        const image = document.createElement("img");
        image.className = className;
        image.src = url;
        image.alt = alt;
        if (!full) image.loading = "lazy";
        return image;
    }

    createAssetPreviewButton(asset, previewClass) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = `xiaoyue-mv-asset-preview-button ${previewClass}-button`;
        button.dataset.assetPreviewId = asset.id;
        button.setAttribute("aria-label", `放大预览资产 ${asset.name}`);
        button.appendChild(this.createAssetPreview(asset.source, previewClass));
        return button;
    }

    assetPreviewInnerSize() {
        const viewport = this.field("asset-preview-body");
        if (!viewport) return { width: 0, height: 0 };
        const style = getComputedStyle(viewport);
        return {
            width: Math.max(0, viewport.clientWidth
                - parseFloat(style.paddingLeft || 0)
                - parseFloat(style.paddingRight || 0)),
            height: Math.max(0, viewport.clientHeight
                - parseFloat(style.paddingTop || 0)
                - parseFloat(style.paddingBottom || 0)),
        };
    }

    fitAssetPreviewImage() {
        const image = this.assetPreviewImage;
        if (!image?.naturalWidth || !image?.naturalHeight) return;
        const inner = this.assetPreviewInnerSize();
        if (!inner.width || !inner.height) return;
        const fitScale = Math.min(
            inner.width / image.naturalWidth,
            inner.height / image.naturalHeight,
        );
        this.assetPreviewBaseSize = {
            width: Math.max(1, image.naturalWidth * fitScale),
            height: Math.max(1, image.naturalHeight * fitScale),
        };
        image.style.width = `${this.assetPreviewBaseSize.width}px`;
        image.style.height = `${this.assetPreviewBaseSize.height}px`;
        this.applyAssetPreviewTransform();
    }

    clampAssetPreviewPan() {
        const inner = this.assetPreviewInnerSize();
        const scaledWidth = this.assetPreviewBaseSize.width * this.assetPreviewZoom;
        const scaledHeight = this.assetPreviewBaseSize.height * this.assetPreviewZoom;
        const maxX = Math.max(0, (scaledWidth - inner.width) / 2);
        const maxY = Math.max(0, (scaledHeight - inner.height) / 2);
        this.assetPreviewPan.x = Math.max(-maxX, Math.min(maxX, this.assetPreviewPan.x));
        this.assetPreviewPan.y = Math.max(-maxY, Math.min(maxY, this.assetPreviewPan.y));
    }

    applyAssetPreviewTransform() {
        const image = this.assetPreviewImage;
        if (!image) return;
        if (this.assetPreviewZoom <= 1) {
            this.assetPreviewZoom = 1;
            this.assetPreviewPan = { x: 0, y: 0 };
        }
        this.clampAssetPreviewPan();
        image.style.transform = `translate3d(${this.assetPreviewPan.x}px, ${this.assetPreviewPan.y}px, 0) scale(${this.assetPreviewZoom})`;
        const viewport = this.field("asset-preview-body");
        viewport.classList.toggle("zoomed", this.assetPreviewZoom > 1);
        const zoomOutput = this.field("asset-preview-zoom");
        zoomOutput.value = `${Math.round(this.assetPreviewZoom * 100)}%`;
        zoomOutput.textContent = zoomOutput.value;
        this.dialog.querySelector('[data-action="asset-preview-zoom-out"]').disabled =
            this.assetPreviewZoom <= 1;
        this.dialog.querySelector('[data-action="asset-preview-fit"]').disabled =
            this.assetPreviewZoom <= 1;
        this.dialog.querySelector('[data-action="asset-preview-zoom-in"]').disabled =
            this.assetPreviewZoom >= 8;
    }

    setAssetPreviewZoom(nextZoom, clientX = null, clientY = null) {
        if (!this.assetPreviewImage) return;
        const previousZoom = this.assetPreviewZoom;
        const zoom = Math.max(1, Math.min(8, Number(nextZoom) || 1));
        if (zoom === previousZoom) return;
        const viewport = this.field("asset-preview-body");
        const rect = viewport.getBoundingClientRect();
        const pointerX = Number.isFinite(clientX) ? clientX - (rect.left + rect.width / 2) : 0;
        const pointerY = Number.isFinite(clientY) ? clientY - (rect.top + rect.height / 2) : 0;
        const ratio = zoom / previousZoom;
        this.assetPreviewPan.x = pointerX - (pointerX - this.assetPreviewPan.x) * ratio;
        this.assetPreviewPan.y = pointerY - (pointerY - this.assetPreviewPan.y) * ratio;
        this.assetPreviewZoom = zoom;
        this.applyAssetPreviewTransform();
    }

    resetAssetPreviewTransform() {
        this.assetPreviewZoom = 1;
        this.assetPreviewPan = { x: 0, y: 0 };
        this.fitAssetPreviewImage();
        this.applyAssetPreviewTransform();
    }

    openAssetPreview(assetId) {
        const asset = this.plan.assets.find((item) => item.id === assetId);
        if (!asset) return;
        this.field("asset-preview-title").textContent = asset.name;
        const categoryNames = { character: "角色", scene: "场景", prop: "道具" };
        const isImage = asset.source.media_type !== "video";
        this.field("asset-preview-meta").textContent = [
            `${categoryNames[asset.category] || "资产"} · ${asset.declaration || "尚未填写统一声明"}`,
            isImage ? "滚轮缩放 · 拖动查看 · 键盘＋/－缩放、方向键移动、0 适应" : "视频保持完整适应显示",
        ].join(" · ");
        const body = this.field("asset-preview-body");
        const media = this.createAssetPreview(
            asset.source,
            "asset-preview-large",
            { full: true, alt: asset.name },
        );
        body.replaceChildren(media);
        body.classList.toggle("image-viewer", isImage);
        body.classList.toggle("video-viewer", !isImage);
        this.field("asset-preview-toolbar").hidden = !isImage;
        this.assetPreviewPointer = null;
        this.assetPreviewZoom = 1;
        this.assetPreviewPan = { x: 0, y: 0 };
        this.assetPreviewBaseSize = { width: 0, height: 0 };
        this.assetPreviewImage = isImage && media instanceof HTMLImageElement ? media : null;
        if (this.assetPreviewImage) {
            this.assetPreviewImage.draggable = false;
            body.tabIndex = 0;
            body.setAttribute("role", "group");
            body.setAttribute("aria-label", `图片查看器：${asset.name}。滚轮缩放，拖动查看。`);
        } else {
            body.tabIndex = -1;
            body.removeAttribute("role");
            body.removeAttribute("aria-label");
        }
        const dialog = this.dialog.querySelector(".xiaoyue-mv-asset-preview-dialog");
        if (!dialog.open) dialog.showModal();
        if (this.assetPreviewImage) {
            const prepare = () => {
                if (this.assetPreviewImage !== media) return;
                this.resetAssetPreviewTransform();
            };
            if (this.assetPreviewImage.complete) requestAnimationFrame(prepare);
            else this.assetPreviewImage.addEventListener("load", () => requestAnimationFrame(prepare), { once: true });
        }
    }

    closeAssetPreview() {
        const dialog = this.dialog.querySelector(".xiaoyue-mv-asset-preview-dialog");
        const body = this.field("asset-preview-body");
        const video = body?.querySelector("video");
        video?.pause();
        if (this.assetPreviewPointer && body?.hasPointerCapture(this.assetPreviewPointer.id)) {
            body.releasePointerCapture(this.assetPreviewPointer.id);
        }
        this.assetPreviewPointer = null;
        this.assetPreviewImage = null;
        body?.classList.remove("dragging", "zoomed");
        if (dialog?.open) dialog.close();
    }

    renderProjectAssets() {
        const list = this.field("asset-list");
        list.replaceChildren();
        this.renderProjectSummary();
        this.renderOutlineVisualAssets();
        if (!this.plan.assets.length) {
            const empty = document.createElement("div");
            empty.className = "xiaoyue-mv-asset-empty";
            const title = document.createElement("strong");
            title.textContent = "还没有项目公用资产";
            const copy = document.createElement("span");
            copy.textContent = "从本地上传，或直接引用 ComfyUI input 中的图片和视频。";
            empty.append(title, copy);
            list.appendChild(empty);
            return;
        }
        const currentIds = new Set(
            (this.plan.shots[this.selectedShot]?.asset_bindings || [])
                .map((binding) => binding.asset_id),
        );
        const categoryNames = { character: "角色", scene: "场景", prop: "道具" };
        for (const asset of this.plan.assets) {
            const card = document.createElement("article");
            card.className = "xiaoyue-mv-asset-card";
            card.dataset.assetId = asset.id;
            const preview = document.createElement("figure");
            preview.appendChild(this.createAssetPreviewButton(asset, "asset-preview"));
            const sourceCaption = document.createElement("figcaption");
            sourceCaption.textContent = `${asset.source.subfolder ? `${asset.source.subfolder}/` : ""}${asset.source.filename}`;
            sourceCaption.title = sourceCaption.textContent;
            preview.appendChild(sourceCaption);

            const fields = document.createElement("div");
            fields.className = "asset-fields";
            const nameLabel = document.createElement("label");
            nameLabel.textContent = "资产名称";
            const name = document.createElement("input");
            name.type = "text";
            name.value = asset.name;
            name.dataset.assetField = "name";
            nameLabel.appendChild(name);
            const categoryLabel = document.createElement("label");
            categoryLabel.textContent = "统一类型";
            const category = document.createElement("select");
            category.dataset.assetField = "category";
            for (const [value, label] of Object.entries(categoryNames)) {
                const option = document.createElement("option");
                option.value = value;
                option.textContent = label;
                option.selected = asset.category === value;
                category.appendChild(option);
            }
            categoryLabel.appendChild(category);
            const declarationLabel = document.createElement("label");
            declarationLabel.className = "asset-declaration";
            declarationLabel.textContent = "统一声明";
            const declaration = document.createElement("textarea");
            declaration.rows = 3;
            declaration.value = asset.declaration;
            declaration.placeholder = "身份、外观、服装、环境、材质或在全片中必须保持的特征";
            declaration.dataset.assetField = "declaration";
            declarationLabel.appendChild(declaration);
            fields.append(nameLabel, categoryLabel, declarationLabel);

            const actions = document.createElement("footer");
            const info = document.createElement("span");
            info.textContent = `${asset.source.media_type === "video" ? "视频" : "图片"} · ${fileSizeLabel(asset.source.size)}`;
            const bind = document.createElement("button");
            bind.type = "button";
            bind.dataset.assetAction = "bind-current";
            bind.className = "bind-current";
            bind.textContent = currentIds.has(asset.id)
                ? `已加入镜头 ${this.shotEditorialLabel(this.selectedShot)}`
                : `加到镜头 ${this.shotEditorialLabel(this.selectedShot)} 末位`;
            bind.setAttribute("aria-pressed", String(currentIds.has(asset.id)));
            const remove = document.createElement("button");
            remove.type = "button";
            remove.dataset.assetAction = "remove";
            remove.className = "remove-asset";
            remove.textContent = "移出资产库";
            actions.append(info, bind, remove);
            card.append(preview, fields, actions);
            list.appendChild(card);
        }
    }

    addAssetFromDescriptor(descriptor) {
        const key = assetSourceKey(descriptor);
        const existing = this.plan.assets.find((asset) => assetSourceKey(asset.source) === key);
        if (existing) return { asset: existing, added: false };
        const asset = {
            id: this.nextAssetId(),
            name: filenameTitle(descriptor.filename),
            category: "character",
            declaration: "",
            outline_visual: this.plan.assets.filter((item) => item.outline_visual).length < 8,
            source: {
                filename: String(descriptor.filename || ""),
                subfolder: String(descriptor.subfolder || ""),
                type: String(descriptor.type || "input"),
                media_type: descriptor.media_type === "video" ? "video" : "image",
                size: Math.max(0, Number(descriptor.size) || 0),
                modified_ms: Math.max(0, Number(descriptor.modified_ms) || 0),
            },
        };
        this.plan.assets.push(asset);
        return { asset, added: true };
    }

    async uploadLocalAssets(files, {
        bindCurrentShot = false,
        statusField = "asset-status",
        triggerAction = "upload-assets",
    } = {}) {
        if (!files.length) return;
        const status = this.field(statusField);
        const trigger = this.dialog.querySelector(`[data-action="${triggerAction}"]`);
        if (trigger) trigger.disabled = true;
        let imported = 0;
        const importedAssets = [];
        try {
            for (let index = 0; index < files.length; index += 1) {
                status.textContent = `正在导入 ${index + 1}/${files.length}：${files[index].name}`;
                const form = new FormData();
                form.append("project_id", String(this.plan.project?.id || ""));
                form.append("file", files[index], files[index].name);
                const response = await api.fetchApi("/xiaoyue-mv/assets/upload", {
                    method: "POST",
                    body: form,
                });
                const result = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(result.error || `导入 ${files[index].name} 失败`);
                const importedAsset = this.addAssetFromDescriptor(result.asset);
                importedAssets.push(importedAsset.asset);
                if (importedAsset.added) imported += 1;
            }
            if (bindCurrentShot) {
                const shot = this.plan.shots[this.selectedShot];
                shot.asset_bindings ||= [];
                for (const asset of importedAssets) {
                    if (!shot.asset_bindings.some((binding) => binding.asset_id === asset.id)) {
                        shot.asset_bindings.push({ asset_id: asset.id, usage: "" });
                    }
                }
                shot.asset_bindings.forEach((binding, index) => { binding.order = index + 1; });
            }
            this.setDirty();
            if (this.projectPanelOpen) this.renderProjectAssets();
            else this.renderProjectSummary();
            this.renderShotAssetBindings();
            this.renderPromptContext();
            this.renderShotCards([this.selectedShot]);
            status.textContent = bindCurrentShot
                ? `已上传并加入当前镜头 ${importedAssets.length} 个资产；文件已进入项目资产库。`
                : `已导入 ${imported} 个新资产；请补全类型和统一声明。`;
        } catch (error) {
            status.textContent = error?.message || String(error);
        } finally {
            if (trigger) trigger.disabled = false;
        }
    }

    async openInputAssetPicker() {
        const picker = this.dialog.querySelector(".xiaoyue-mv-input-picker");
        const status = this.field("input-picker-status");
        this.selectedInputAssetKeys.clear();
        this.field("input-asset-search").value = "";
        this.field("input-asset-list").replaceChildren();
        status.textContent = "正在读取 input 目录…";
        if (!picker.open) picker.showModal();
        try {
            const response = await api.fetchApi("/xiaoyue-mv/assets/input?limit=1200");
            const result = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(result.error || "读取 input 失败。");
            this.inputAssetCatalog = Array.isArray(result.assets) ? result.assets : [];
            this.renderInputAssetCatalog();
        } catch (error) {
            this.inputAssetCatalog = [];
            status.textContent = error?.message || String(error);
        }
    }

    closeInputAssetPicker() {
        const picker = this.dialog.querySelector(".xiaoyue-mv-input-picker");
        if (picker?.open) picker.close();
    }

    renderInputAssetCatalog() {
        const list = this.field("input-asset-list");
        const query = this.field("input-asset-search").value.trim().toLocaleLowerCase();
        const existing = new Set(this.plan.assets.map((asset) => assetSourceKey(asset.source)));
        const visible = this.inputAssetCatalog.filter((asset) => {
            const path = `${asset.subfolder || ""}/${asset.filename || ""}`.toLocaleLowerCase();
            return !query || path.includes(query);
        });
        list.replaceChildren();
        for (const asset of visible) {
            const key = assetSourceKey(asset);
            const row = document.createElement("label");
            row.className = "xiaoyue-mv-input-asset-row";
            row.classList.toggle("already-added", existing.has(key));
            const checkbox = document.createElement("input");
            checkbox.type = "checkbox";
            checkbox.dataset.inputAssetKey = key;
            checkbox.checked = this.selectedInputAssetKeys.has(key);
            checkbox.disabled = existing.has(key);
            const preview = asset.media_type === "image"
                ? this.createAssetPreview(asset, "input-preview")
                : document.createElement("span");
            if (asset.media_type === "video") {
                preview.className = "input-preview video-placeholder";
                preview.textContent = "VIDEO";
            }
            const copy = document.createElement("span");
            const name = document.createElement("strong");
            name.textContent = asset.filename;
            const meta = document.createElement("small");
            meta.textContent = `${asset.subfolder || "input 根目录"} · ${asset.media_type === "video" ? "视频" : "图片"} · ${fileSizeLabel(asset.size)}${existing.has(key) ? " · 已导入" : ""}`;
            copy.append(name, meta);
            row.append(checkbox, preview, copy);
            list.appendChild(row);
        }
        if (!visible.length) {
            const empty = document.createElement("p");
            empty.className = "xiaoyue-mv-input-empty";
            empty.textContent = query ? "没有匹配的 input 资产。" : "input 中没有可用的图片或视频。";
            list.appendChild(empty);
        }
        this.updateInputPickerStatus(visible.length);
    }

    updateInputPickerStatus(visibleCount = null) {
        const count = visibleCount ?? this.field("input-asset-list").querySelectorAll("[data-input-asset-key]").length;
        this.field("input-picker-status").textContent = `${count} 个可见文件 · 已选 ${this.selectedInputAssetKeys.size} 个`;
    }

    importSelectedInputAssets() {
        let added = 0;
        for (const descriptor of this.inputAssetCatalog) {
            if (!this.selectedInputAssetKeys.has(assetSourceKey(descriptor))) continue;
            if (this.addAssetFromDescriptor(descriptor).added) added += 1;
        }
        if (added) this.setDirty();
        this.closeInputAssetPicker();
        this.renderProjectAssets();
        this.renderShotAssetBindings();
        this.field("asset-status").textContent = added
            ? `已从 input 导入 ${added} 个资产；请逐一完成统一声明。`
            : "没有新资产需要导入。";
    }

    setShotAssetBinding(assetId, enabled) {
        const shot = this.plan.shots[this.selectedShot];
        if (!shot) return;
        shot.asset_bindings ||= [];
        const exists = shot.asset_bindings.some((binding) => binding.asset_id === assetId);
        if (enabled && !exists) {
            shot.asset_bindings.push({ asset_id: assetId, usage: "" });
        } else if (!enabled && exists) {
            shot.asset_bindings = shot.asset_bindings.filter(
                (binding) => binding.asset_id !== assetId,
            );
        }
        shot.asset_bindings.forEach((binding, index) => { binding.order = index + 1; });
        this.setDirty();
        this.renderShotAssetBindings();
        this.renderPromptContext();
        this.renderShotCards([this.selectedShot]);
        if (this.projectPanelOpen) this.renderProjectAssets();
    }

    toggleShotAsset(assetId) {
        const shot = this.plan.shots[this.selectedShot];
        const enabled = !(shot?.asset_bindings || []).some(
            (binding) => binding.asset_id === assetId,
        );
        this.setShotAssetBinding(assetId, enabled);
    }

    moveShotAssetBinding(assetId, direction) {
        const shot = this.plan.shots[this.selectedShot];
        const bindings = shot?.asset_bindings || [];
        const current = bindings.findIndex((binding) => binding.asset_id === assetId);
        const target = current + direction;
        if (current < 0 || target < 0 || target >= bindings.length) return;
        [bindings[current], bindings[target]] = [bindings[target], bindings[current]];
        bindings.forEach((binding, index) => { binding.order = index + 1; });
        this.setDirty();
        this.renderShotAssetBindings();
        this.renderPromptContext();
        if (this.projectPanelOpen) this.renderProjectAssets();
    }

    spreadShotAssetToAllShots(assetId) {
        const sourceShot = this.plan.shots[this.selectedShot];
        const sourceBinding = sourceShot?.asset_bindings?.find(
            (binding) => binding.asset_id === assetId,
        );
        const asset = this.plan.assets.find((item) => item.id === assetId);
        if (!sourceBinding || !asset) return;
        const usage = String(sourceBinding.usage || "").trim();
        const targetCount = Math.max(0, this.plan.shots.length - 1);
        if (!usage) {
            this.field("shot-asset-status").textContent =
                `请先填写“${asset.name}”的本镜头用法，再铺到全片。`;
            return;
        }
        if (!targetCount) {
            this.field("shot-asset-status").textContent = "当前项目只有一个镜头，不需要铺设。";
            return;
        }
        const usagePreview = usage.replace(/\s+/g, " ");
        const shortenedUsage = usagePreview.length > 80
            ? `${usagePreview.slice(0, 80)}…`
            : usagePreview;
        const confirmed = window.confirm(
            `把资产“${asset.name}”铺到其余 ${targetCount} 个镜头？\n\n`
            + `统一用法：${shortenedUsage}\n\n`
            + "目标镜头已有该资产：保留原参考槽位并替换用法。\n"
            + "目标镜头没有该资产：追加到该镜头参考顺序末位。",
        );
        if (!confirmed) return;

        let replaced = 0;
        let appended = 0;
        this.plan.shots.forEach((shot, shotIndex) => {
            if (shotIndex === this.selectedShot) return;
            shot.asset_bindings ||= [];
            const existingIndex = shot.asset_bindings.findIndex(
                (binding) => binding.asset_id === assetId,
            );
            if (existingIndex >= 0) {
                shot.asset_bindings[existingIndex] = {
                    ...shot.asset_bindings[existingIndex],
                    asset_id: assetId,
                    usage,
                };
                replaced += 1;
            } else {
                shot.asset_bindings.push({ asset_id: assetId, usage });
                appended += 1;
            }
            shot.asset_bindings.forEach((binding, index) => { binding.order = index + 1; });
        });
        this.setDirty(
            `资产“${asset.name}”已铺到全片；原槽位替换 ${replaced} 个，末位追加 ${appended} 个。请保存计划。`,
            false,
        );
        this.renderShotAssetBindings();
        this.renderPromptContext();
        this.renderShotList();
        if (this.projectPanelOpen) this.renderProjectAssets();
        this.field("shot-asset-status").textContent =
            `已铺到其余 ${targetCount} 个镜头：原槽位替换 ${replaced} 个，末位追加 ${appended} 个；各镜头参考顺序保持独立。`;
    }

    removeProjectAsset(assetId) {
        const asset = this.plan.assets.find((item) => item.id === assetId);
        if (!asset) return;
        const boundCount = this.plan.shots.reduce(
            (count, shot) => count + Number((shot.asset_bindings || []).some(
                (binding) => binding.asset_id === assetId,
            )),
            0,
        );
        const question = boundCount
            ? `资产“${asset.name}”已绑定 ${boundCount} 个镜头。移出项目资产库后会同时解除这些绑定，但不会删除 input 原文件。继续吗？`
            : `将“${asset.name}”移出项目资产库？input 原文件不会被删除。`;
        if (!window.confirm(question)) return;
        this.plan.assets = this.plan.assets.filter((item) => item.id !== assetId);
        for (const shot of this.plan.shots) {
            shot.asset_bindings = (shot.asset_bindings || [])
                .filter((binding) => binding.asset_id !== assetId);
            shot.asset_bindings.forEach((binding, index) => { binding.order = index + 1; });
        }
        this.setDirty();
        this.renderProjectAssets();
        this.renderShotAssetBindings();
        this.renderPromptContext();
        this.renderShotList();
        this.field("asset-status").textContent = `已将“${asset.name}”移出项目资产库；input 原文件保留。`;
    }

    renderShotAssetBindings() {
        const list = this.field("shot-asset-bindings");
        if (!list) return;
        list.replaceChildren();
        const shot = this.plan.shots[this.selectedShot];
        this.field("shot-asset-status").textContent = "";
        if (!this.plan.assets.length) {
            const empty = document.createElement("p");
            empty.className = "xiaoyue-mv-shot-asset-empty";
            empty.textContent = "项目资产库为空；请先在“项目总控”中导入。";
            list.appendChild(empty);
            return;
        }
        const categoryNames = { character: "角色", scene: "场景", prop: "道具" };
        const assetById = new Map(this.plan.assets.map((asset) => [asset.id, asset]));
        const bindings = (shot?.asset_bindings || [])
            .filter((binding) => assetById.has(binding.asset_id));
        const boundSection = document.createElement("section");
        boundSection.className = "xiaoyue-mv-bound-assets";
        const boundTitle = document.createElement("h4");
        boundTitle.textContent = `本镜头已调用 · ${bindings.length}`;
        boundSection.appendChild(boundTitle);
        if (!bindings.length) {
            const empty = document.createElement("p");
            empty.className = "xiaoyue-mv-shot-asset-empty";
            empty.textContent = "当前镜头还没有调用项目资产。";
            boundSection.appendChild(empty);
        }
        bindings.forEach((binding, index) => {
            const asset = assetById.get(binding.asset_id);
            const row = document.createElement("article");
            row.className = "xiaoyue-mv-bound-asset-row";
            const preview = this.createAssetPreviewButton(asset, "binding-preview");
            const reference = document.createElement("strong");
            reference.className = "binding-reference";
            reference.textContent = `参考 ${index + 1}`;
            const identity = document.createElement("div");
            identity.className = "binding-identity";
            const name = document.createElement("strong");
            name.textContent = asset.name;
            const meta = document.createElement("span");
            meta.textContent = `${categoryNames[asset.category] || "资产"} · ${asset.declaration || "尚未填写统一声明"}`;
            identity.append(name, meta);
            const usageLabel = document.createElement("label");
            usageLabel.textContent = "本镜头用法";
            const usage = document.createElement("input");
            usage.type = "text";
            usage.value = binding.usage || "";
            usage.placeholder = "例：女主为前景主体，保持发型与外套";
            usage.dataset.bindingUsage = "true";
            usage.dataset.bindingAssetId = asset.id;
            usageLabel.appendChild(usage);
            const actions = document.createElement("div");
            actions.className = "binding-actions";
            for (const [action, label, disabled] of [
                ["spread-all", "铺到全片", !String(binding.usage || "").trim() || this.plan.shots.length <= 1],
                ["move-up", "上移", index === 0],
                ["move-down", "下移", index === bindings.length - 1],
                ["remove", "解除", false],
            ]) {
                const button = document.createElement("button");
                button.type = "button";
                button.dataset.shotAssetAction = action;
                button.dataset.bindingAssetId = asset.id;
                button.textContent = label;
                button.disabled = disabled;
                if (action === "spread-all") {
                    button.className = "spread-all";
                    button.title = this.plan.shots.length <= 1
                        ? "当前项目只有一个镜头"
                        : String(binding.usage || "").trim()
                            ? `把当前用法铺到其余 ${this.plan.shots.length - 1} 个镜头`
                            : "请先填写本镜头用法";
                    button.setAttribute("aria-label", `${label}：${asset.name}`);
                }
                actions.appendChild(button);
            }
            row.append(preview, reference, identity, usageLabel, actions);
            boundSection.appendChild(row);
        });
        list.appendChild(boundSection);

        const selected = new Set(bindings.map((binding) => binding.asset_id));
        const available = this.plan.assets.filter((asset) => !selected.has(asset.id));
        const availableSection = document.createElement("section");
        availableSection.className = "xiaoyue-mv-available-assets";
        const summary = document.createElement("span");
        summary.textContent = available.length
            ? `项目资产库还有 ${available.length} 个资产可加入。`
            : "项目库中的资产已全部加入当前镜头。";
        availableSection.appendChild(summary);
        list.appendChild(availableSection);
    }

    renderPromptContext() {
        const summary = this.field("prompt-context-summary");
        if (!summary) return;
        const shot = this.plan.shots[this.selectedShot];
        const assetById = new Map(this.plan.assets.map((asset) => [asset.id, asset]));
        const bound = (shot?.asset_bindings || [])
            .map((binding, index) => ({ binding, asset: assetById.get(binding.asset_id), index }))
            .filter((item) => item.asset);
        const projectName = this.plan.project.name || this.plan.project.code || "未命名项目";
        const references = bound.length
            ? ` · ${bound.map((item) => `参考${item.index + 1} ${item.asset.name}`).join("／")}`
            : "";
        const outlineState = this.plan.creative_plan.overview || shot?.creative_outline
            ? "镜头纲领已建立"
            : "镜头纲领未建立";
        const generationSpec = normalizeGenerationSpec(this.plan.generation_spec);
        summary.textContent =
            `${projectName} · ${generationSpec.aspect_ratio.split(" ")[0]} · ${generationSpec.first_pass_megapixels}→${generationSpec.second_pass_megapixels} MP · ${outlineState} · 项目总提示词${this.plan.project.overall_prompt ? "已填" : "未填"} · 当前镜头调用 ${bound.length} 个资产${references}`;
    }

    renderCreativeAssetNeeds() {
        const container = this.field("creative-asset-needs");
        if (!container) return;
        const shot = this.plan.shots[this.selectedShot];
        const needs = Array.isArray(shot?.creative_asset_needs)
            ? shot.creative_asset_needs.filter((need) => need?.description)
            : [];
        container.replaceChildren();
        if (!needs.length) {
            const empty = document.createElement("span");
            empty.textContent = "第一阶段尚未给出本镜头的建议资产。";
            container.appendChild(empty);
            return;
        }
        const title = document.createElement("strong");
        title.textContent = "建议准备";
        const list = document.createElement("ul");
        const categoryNames = { character: "角色", scene: "场景", prop: "道具" };
        for (const need of needs) {
            const item = document.createElement("li");
            const category = document.createElement("b");
            category.textContent = categoryNames[need.category] || "素材";
            item.append(category, document.createTextNode(` ${need.description}`));
            list.appendChild(item);
        }
        container.append(title, list);
    }

    renderOutlineVisualAssets() {
        const container = this.field("outline-visual-assets");
        const status = this.field("outline-visual-status");
        if (!container || !status) return;
        const skillStatus = this.field("outline-skill-status");
        if (skillStatus) {
            const skillName = this.outlineSkill?.display_name || "小月 MV 视觉镜头总纲";
            const skillVersion = this.outlineSkill?.version && this.outlineSkill.version !== "unknown"
                ? `v${this.outlineSkill.version}` : "版本未知";
            skillStatus.textContent = `${skillName} · ${skillVersion} · ${this.outlineSkill?.entrypoint || "SKILL.cn.md"} · 内置调用`;
        }
        container.replaceChildren();
        const selectedCount = this.plan.assets.filter((asset) => asset.outline_visual).length;
        const visualReady = Boolean(this.reasoningProvider?.visual_capable);
        status.value = this.plan.assets.length
            ? `总纲附件已选 ${selectedCount}/8 项 · ${visualReady ? "模型已加载视觉投影" : "当前模型未加载 mmproj"}`
            : "项目总资产库为空；请先点‘导入／管理总资产’，否则本轮只使用文本、歌词与时间轴。";
        if (!this.plan.assets.length) {
            const empty = document.createElement("p");
            empty.className = "xiaoyue-mv-outline-visual-empty";
            empty.textContent = "导入角色、场景或道具后，附件缩略图会出现在这里，直接勾选本轮要给总纲模型看的项目。";
            container.appendChild(empty);
            return;
        }
        const categoryNames = { character: "角色", scene: "场景", prop: "道具" };
        this.plan.assets.forEach((asset, index) => {
            const row = document.createElement("article");
            row.className = "xiaoyue-mv-outline-visual-row";
            row.classList.toggle("selected", asset.outline_visual);
            row.setAttribute("role", "listitem");
            const checkboxId = `xiaoyue-outline-visual-${String(this.node?.id || "mv")}-${index}`;
            const checkbox = document.createElement("input");
            checkbox.type = "checkbox";
            checkbox.id = checkboxId;
            checkbox.checked = Boolean(asset.outline_visual);
            checkbox.disabled = !asset.outline_visual && selectedCount >= 8;
            checkbox.dataset.outlineVisualId = asset.id;
            const preview = this.createAssetPreviewButton(asset, "outline-visual-preview");
            const copy = document.createElement("div");
            const label = document.createElement("label");
            label.htmlFor = checkboxId;
            label.textContent = `${asset.outline_visual ? `视觉资产 ${String.fromCharCode(65 + this.plan.assets.filter((item, itemIndex) => itemIndex <= index && item.outline_visual).length - 1)} · ` : ""}${asset.name}`;
            const meta = document.createElement("span");
            meta.textContent = `${categoryNames[asset.category] || "资产"} · ${asset.source.media_type === "video" ? "读取视频代表帧" : "读取图像"}`;
            const declaration = document.createElement("p");
            declaration.textContent = asset.declaration || "尚未填写统一声明";
            copy.append(label, meta, declaration);
            row.append(checkbox, preview, copy);
            container.appendChild(row);
        });
    }

    async runCreativeOutline() {
        const button = this.dialog.querySelector('[data-action="generate-creative-outline"]');
        const status = this.field("creative-outline-status");
        const provider = this.reasoningProvider;
        if (this.creativeOutlineRunning || this.h3PromptRunning
            || this.generationStarting || this.activeGenerationJobs().length) {
            status.value = "视频或提示词任务正在进行；请等待完成后再生成整片纲领。";
            return;
        }
        const visualAssets = this.plan.assets.filter((asset) => asset.outline_visual);
        if (!provider?.available || !provider?.token) {
            status.value = this.reasoningProviderNode
                ? "适配器已连接但模型尚未注册。请保存计划，关闭工作台后重新运行一次工作流。"
                : "请把 QwenTE · MV 整片纲领推理 Provider 连接到主工作台的 reasoning_provider。";
            return;
        }
        if (visualAssets.length && !provider.visual_capable) {
            status.value = `已选 ${visualAssets.length} 个视觉资产，但当前 QwenTE 模型未加载 mmproj。请在模型加载器中选择匹配的视觉投影并重新运行工作流。`;
            return;
        }
        const hasExisting = Boolean(
            this.plan.creative_plan?.overview
            || this.plan.shots.some((shot) => shot.creative_outline),
        );
        if (hasExisting && !window.confirm(
            "重新生成会覆盖当前 MV 镜头纲领总览、每镜大体内容、演唱开关和建议资产；不会修改切点、字幕、资产绑定或最终 H3 提示词。是否继续？",
        )) {
            status.value = "已取消重新生成；现有镜头纲领保持不变。";
            return;
        }
        this.creativeOutlineRunning = true;
        this.updateGenerationActionStates();
        button.setAttribute("aria-busy", "true");
        button.textContent = "正在生成视觉纲领…";
        status.value = `正在调用 ${provider.family || "QwenTE"} · ${provider.model_name || "本地模型"} · ${visualAssets.length ? `读取 ${visualAssets.length} 项视觉资产` : "纯文本规划"}；完整结果校验成功前不会覆盖现有纲领。`;
        try {
            const startResponse = await api.fetchApi("/xiaoyue-mv/creative/outline", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    provider_token: provider.token,
                    plan: this.plan,
                }),
            });
            const started = await startResponse.json().catch(() => ({}));
            if (!startResponse.ok) {
                throw new Error(started.error || `无法启动镜头纲领推理（HTTP ${startResponse.status}）`);
            }
            if (!started.job_id) throw new Error("镜头纲领任务没有返回任务编号。");
            const pollDelay = Math.max(700, Number(started.poll_after_ms) || 1000);
            const pollStartedAt = Date.now();
            let result = null;
            while (Date.now() - pollStartedAt < 60 * 60 * 1000) {
                await waitMilliseconds(pollDelay);
                const response = await api.fetchApi(
                    `/xiaoyue-mv/creative/outline/${encodeURIComponent(started.job_id)}`,
                );
                const job = await response.json().catch(() => ({}));
                if (!response.ok) {
                    throw new Error(job.error || `读取镜头纲领任务失败（HTTP ${response.status}）`);
                }
                if (job.state === "completed") {
                    result = job.result;
                    break;
                }
                if (job.state === "failed") {
                    throw new Error(job.error || "整片镜头纲领推理失败。");
                }
                status.value = `正在生成整片镜头纲领，已等待 ${Math.ceil(Number(job.elapsed_seconds) || (Date.now() - pollStartedAt) / 1000)} 秒；旧内容仍保持不变。`;
            }
            if (!result?.outline) throw new Error("镜头纲领任务超时或没有返回完整结果。");
            const rows = Array.isArray(result.outline.shots) ? result.outline.shots : [];
            const byId = new Map(rows.map((row) => [String(row.shot_id || ""), row]));
            if (!String(result.outline.overview || "").trim()
                || byId.size !== this.plan.shots.length
                || this.plan.shots.some((shot) => !String(byId.get(shot.id)?.creative_outline || "").trim())) {
                throw new Error("模型结果没有完整覆盖当前全部镜头；现有纲领未改变。");
            }
            this.plan.creative_plan.overview = String(result.outline.overview).trim();
            this.plan.creative_plan.revision = Math.max(
                0, Number(this.plan.creative_plan.revision) || 0,
            ) + 1;
            this.plan.creative_plan.source_fingerprint = String(result.source_fingerprint || "");
            this.plan.creative_plan.model = [
                result.model?.family,
                result.model?.model_name,
            ].filter(Boolean).join(" / ") || "QwenTE";
            for (const shot of this.plan.shots) {
                const row = byId.get(shot.id);
                shot.creative_outline = String(row.creative_outline).trim();
                shot.singing_performance = typeof row.singing_performance === "boolean"
                    ? row.singing_performance
                    : Boolean(shot.subtitle_context?.ai_dialogue_text);
                shot.creative_asset_needs = (Array.isArray(row.asset_needs) ? row.asset_needs : [])
                    .map((need) => ({
                        category: ["character", "scene", "prop"].includes(need?.category)
                            ? need.category : "prop",
                        description: String(need?.description || "").trim(),
                    }))
                    .filter((need) => need.description);
            }
            this.field("creative-plan-overview").value = this.plan.creative_plan.overview;
            this.field("outline-plan-overview").value = this.plan.creative_plan.overview;
            this.setDirty(
                "整片视觉镜头纲领已生成并写入全部镜头；请审阅后为各镜头绑定实际资产。",
                false,
            );
            this.renderShotEditor();
            this.renderShotList();
            await this.save(false);
            const summary = result.input_summary || {};
            status.value = `已生成 ${summary.shot_count || this.plan.shots.length} 个完整分镜描述 · Skill ${summary.skill?.display_name || this.outlineSkill?.display_name || "内置视觉总纲"} · 读取 ${summary.visual_asset_count || 0} 项视觉资产 · ${summary.lyric_shot_count || 0} 个镜头含校正唱词 · 模型 ${this.plan.creative_plan.model}。`;
            app.extensionManager?.toast?.add?.({
                severity: "success",
                summary: "整片视觉镜头纲领已生成",
                detail: "已写入全片总览、每镜头完整自然语言分镜描述与建议资产。",
                life: 7000,
            });
        } catch (error) {
            const message = error?.message || String(error);
            status.value = `生成失败：${message} 原有镜头纲领没有改变。`;
            app.extensionManager?.toast?.add?.({
                severity: "error",
                summary: "整片镜头纲领生成失败",
                detail: status.value,
                life: 10000,
            });
        } finally {
            this.creativeOutlineRunning = false;
            this.updateGenerationActionStates();
            button.setAttribute("aria-busy", "false");
            button.textContent = "生成整片视觉纲领";
        }
    }

    h3PromptChainConflict(shotId) {
        if (this.h3PromptJobs.has(shotId)) return true;
        const ancestors = (id) => {
            const result = new Set();
            let index = this.plan.shots.findIndex((shot) => String(shot.id) === id);
            while (index > 0 && this.plan.transitions?.[index - 1]?.type === "long_take") {
                index -= 1;
                const parentId = String(this.plan.shots[index].id);
                if (result.has(parentId)) break;
                result.add(parentId);
            }
            return result;
        };
        const targetAncestors = ancestors(shotId);
        for (const activeId of this.h3PromptJobs.keys()) {
            if (targetAncestors.has(activeId) || ancestors(activeId).has(shotId)) return true;
        }
        return false;
    }

    setH3PromptJobStatus(shotIds, message) {
        for (const shotId of shotIds) this.h3PromptStatuses.set(shotId, message);
        const selectedId = String(this.plan.shots[this.selectedShot]?.id || "");
        if (shotIds.includes(selectedId)) this.field("h3-prompt-status").value = message;
    }

    async runH3PromptGeneration(scope) {
        const provider = this.reasoningProvider;
        const status = this.field("h3-prompt-status");
        const currentButton = this.dialog.querySelector('[data-action="generate-h3-current"]');
        const allButton = this.dialog.querySelector('[data-action="generate-h3-all"]');
        const activeButton = scope === "all" ? allButton : currentButton;
        const targetIndices = scope === "all"
            ? this.plan.shots.map((_shot, index) => index)
            : [this.selectedShot];
        const shotIds = targetIndices.map((index) => String(this.plan.shots[index].id));
        if (this.h3PromptBatchRunning || (scope === "all" && this.h3PromptRunning)
            || (scope !== "all" && this.h3PromptChainConflict(shotIds[0]))
            || this.creativeOutlineRunning
            || this.generationStarting || this.activeGenerationJobs().length) {
            status.value = "本镜头、续写链或批量任务正在生成，或视频任务占用本地模型；独立镜头的提示词可继续提交排队。";
            return;
        }
        if (!provider?.available || !provider?.token) {
            status.value = this.reasoningProviderNode
                ? "推理适配器尚未注册模型；请保存计划，关闭工作台后重新运行一次工作流。"
                : "请把 QwenTE 推理 Provider 连接到主工作台的 reasoning_provider。";
            return;
        }
        // The control is the user's last visible choice. Synchronize it at
        // submission time as well as on change, without requiring a plan save.
        const visibleShot = this.plan.shots[this.selectedShot];
        const visibleSwitch = this.field("singing-performance");
        if (visibleShot && visibleSwitch && targetIndices.includes(this.selectedShot)) {
            const checked = visibleSwitch.checked;
            if (visibleShot.singing_performance !== checked) {
                visibleShot.singing_performance = checked;
                this.setDirty("口型选择已同步到本次提示词请求。", false);
            }
        }
        for (const index of targetIndices) this.refreshShotSubtitleContext(index);
        const submittedChoice = scope === "all" ? "" :
            `本次提交：口型${this.plan.shots[targetIndices[0]]?.singing_performance ? "开" : "关"}，唱词 ${String(this.plan.shots[targetIndices[0]]?.subtitle_context?.ai_dialogue_text || "").length} 字。`;
        const missingOutlines = targetIndices.filter(
            (index) => !String(this.plan.shots[index]?.creative_outline || "").trim(),
        );
        if (missingOutlines.length) {
            const labels = missingOutlines.slice(0, 6)
                .map((index) => this.shotEditorialLabel(index)).join("、");
            const suffix = missingOutlines.length > 6 ? ` 等 ${missingOutlines.length} 镜` : "";
            status.value = `镜头 ${labels}${suffix} 尚未填写镜头纲领；请先完成第一阶段规划。`;
            return;
        }
        if (scope !== "all" && this.selectedShot > 0
            && this.plan.transitions?.[this.selectedShot - 1]?.type === "long_take") {
            const previousShot = this.plan.shots[this.selectedShot - 1];
            if (!String(previousShot?.prompt || "").trim()) {
                const previousLabel = this.shotEditorialLabel(this.selectedShot - 1);
                status.value = `镜头 ${this.shotEditorialLabel(this.selectedShot)} 是续写镜头；请先成功生成上一镜 ${previousLabel} 的 H3 提示词。`;
                app.extensionManager?.toast?.add?.({
                    severity: "warn",
                    summary: "缺少续写前置提示词",
                    detail: status.value,
                    life: 7000,
                });
                return;
            }
        }
        const targetsWithAssets = targetIndices.filter(
            (index) => (this.plan.shots[index]?.asset_bindings || []).length > 0,
        );
        if (targetsWithAssets.length && !provider.visual_capable) {
            status.value = `目标范围有 ${targetsWithAssets.length} 个镜头绑定了视觉资产，但当前 QwenTE 未加载 mmproj；请选择匹配的视觉投影并重新运行工作流。`;
            return;
        }
        const targetLabels = targetIndices.map((index) => this.shotEditorialLabel(index));
        const currentTargetLabel = targetLabels[0];
        const targetIndexById = new Map(
            targetIndices.map((index) => [String(this.plan.shots[index].id), index]),
        );
        const sourceStateForShot = (index) => JSON.stringify({
            project: this.plan.project,
            generation_spec: this.plan.generation_spec,
            creative_plan: this.plan.creative_plan,
            analysis: { bpm: this.plan.analysis?.bpm },
            transitions: this.plan.transitions,
            generation_windows: this.plan.generation_windows,
            assets: this.plan.assets,
            shot: (() => {
                const shot = this.plan.shots[index];
                return {
                    id: shot.id,
                    start_frame: shot.start_frame,
                    end_frame: shot.end_frame,
                    keep_frames: shot.keep_frames,
                    creative_outline: shot.creative_outline,
                    singing_performance: shot.singing_performance,
                    prompt: shot.prompt,
                    connection_requirement: shot.connection_requirement,
                    subtitle_context: shot.subtitle_context,
                    asset_bindings: shot.asset_bindings,
                };
            })(),
        });
        const requestSourceStates = new Map(
            targetIndices.map((index) => [
                String(this.plan.shots[index].id),
                sourceStateForShot(index),
            ]),
        );
        const confirmation = scope === "all"
            ? `批量全生会依次处理全部 ${shotIds.length} 个镜头：每镜成功后立即覆盖该镜提示词；续写链前段失败时跳过后续连续段，遇到硬切或叠化后继续。是否继续？`
            : `生成本镜会覆盖镜头 ${currentTargetLabel} 现有的 H3 提示词；生成或校验失败不会覆盖。是否继续？`;
        if (!window.confirm(confirmation)) {
            status.value = "已取消生成；现有 H3 提示词保持不变。";
            return;
        }
        const requestPlan = JSON.parse(JSON.stringify(this.plan));
        for (const shotId of shotIds) this.h3PromptJobs.set(shotId, scope);
        this.h3PromptBatchRunning = scope === "all";
        this.h3PromptRunning = true;
        this.updateGenerationActionStates();
        activeButton.setAttribute("aria-busy", "true");
        this.setH3PromptJobStatus(shotIds, scope === "all"
            ? `正在按镜头顺序生成 ${shotIds.length} 条 H3 提示词；续写严格依赖上一段，硬切和叠化镜头不受前一条续写链失败影响。`
            : `${submittedChoice} 镜头 ${currentTargetLabel} 已提交，等待本地模型顺序生成；成功前不会覆盖现有内容。`);
        const handledResultIds = new Set();
        const appliedShotIds = new Set();
        const failuresById = new Map();
        const skippedById = new Map();
        const reviewWarningsById = new Map();
        const modelReceivedById = new Map();
        let lastResult = null;
        const collectFailures = (result) => {
            for (const failure of Array.isArray(result?.failures) ? result.failures : []) {
                const shotId = String(failure?.shot_id || "");
                if (!targetIndexById.has(shotId)) continue;
                failuresById.set(shotId, {
                    label: String(failure?.editorial_label || shotId),
                    error: String(failure?.error || "生成失败"),
                });
            }
            for (const row of Array.isArray(result?.skipped) ? result.skipped : []) {
                const shotId = String(row?.shot_id || "");
                if (!targetIndexById.has(shotId)) continue;
                skippedById.set(shotId, {
                    label: String(row?.editorial_label || shotId),
                    reason: String(row?.reason || "续写前置镜头未成功，已跳过。"),
                });
            }
        };
        const applyAvailablePrompts = (result) => {
            collectFailures(result);
            for (const row of Array.isArray(result?.prompts) ? result.prompts : []) {
                const shotId = String(row?.shot_id || "");
                if (handledResultIds.has(shotId) || !targetIndexById.has(shotId)) continue;
                handledResultIds.add(shotId);
                const index = this.plan.shots.findIndex((shot) => String(shot.id) === shotId);
                if (index < 0) {
                    failuresById.set(shotId, {
                        label: row.editorial_label || shotId,
                        error: "生成期间镜头已删除，本次结果未写入。",
                    });
                    continue;
                }
                const label = String(row?.editorial_label || this.shotEditorialLabel(index));
                const prompt = String(row?.prompt || "").trim();
                if (typeof row.singing_performance === "boolean") {
                    modelReceivedById.set(shotId, {
                        singing: row.singing_performance,
                        lyricsChars: Number(row.lyrics_chars) || 0,
                    });
                }
                if (!prompt) {
                    failuresById.set(shotId, { label, error: "模型返回了空提示词。" });
                    continue;
                }
                if (sourceStateForShot(index) !== requestSourceStates.get(shotId)) {
                    failuresById.set(shotId, {
                        label,
                        error: "生成期间该镜头被编辑过，为避免覆盖新内容，本次结果未写入。",
                    });
                    continue;
                }
                this.plan.shots[index].prompt = prompt;
                this.persistShotPatch(this.plan.shots[index]);
                appliedShotIds.add(shotId);
                if (row.quality_warning) {
                    reviewWarningsById.set(shotId, {
                        label,
                        reason: String(row.quality_warning),
                    });
                }
                if (index === this.selectedShot) {
                    this.field("prompt").value = prompt;
                    this.fitPromptTextarea();
                }
                this.setDirty(`镜头 ${label} 的 H3 提示词已生成并覆盖；批量任务继续处理后续镜头。`, false);
                this.renderShotCards([index]);
                this.updateH3PromptStatus(index);
            }
        };
        try {
            const startResponse = await api.fetchApi("/xiaoyue-mv/h3/prompts", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    provider_token: provider.token,
                    plan: requestPlan,
                    shot_ids: shotIds,
                    batch: scope === "all",
                }),
            });
            const started = await startResponse.json().catch(() => ({}));
            if (!startResponse.ok) {
                throw new Error(started.error || `无法启动 H3 提示词任务（HTTP ${startResponse.status}）`);
            }
            if (!started.job_id) throw new Error("H3 提示词任务没有返回任务编号。");
            const pollDelay = Math.max(700, Number(started.poll_after_ms) || 1000);
            const pollStartedAt = Date.now();
            let result = null;
            while (Date.now() - pollStartedAt < 4 * 60 * 60 * 1000) {
                await waitMilliseconds(pollDelay);
                const response = await api.fetchApi(
                    `/xiaoyue-mv/h3/prompts/${encodeURIComponent(started.job_id)}`,
                );
                const job = await response.json().catch(() => ({}));
                if (!response.ok) {
                    throw new Error(job.error || `读取 H3 提示词任务失败（HTTP ${response.status}）`);
                }
                lastResult = job.result || lastResult;
                applyAvailablePrompts(job.result);
                const completedCount = Number(job.completed_count) || 0;
                const totalCount = Number(job.total_count) || shotIds.length;
                const currentLabel = job.current_label ? `，正在处理镜头 ${job.current_label}` : "";
                this.setH3PromptJobStatus(shotIds, job.state === "queued"
                    ? `${submittedChoice} 镜头 ${currentTargetLabel} 已排队，等待本地模型。`
                    : `${submittedChoice} H3 提示词已处理 ${completedCount}/${totalCount} · 成功 ${appliedShotIds.size} · 失败 ${failuresById.size} · 续写跳过 ${skippedById.size}${currentLabel}。`);
                if (job.state === "completed") {
                    result = job.result || lastResult;
                    break;
                }
                if (job.state === "failed") {
                    throw new Error(job.error || "H3 提示词生成失败。");
                }
            }
            if (!result) {
                throw new Error("H3 提示词任务等待超时。已成功镜头保持写入，其余镜头保留旧提示词。");
            }
            applyAvailablePrompts(result);
            for (const shotId of shotIds) {
                if (!appliedShotIds.has(shotId) && !failuresById.has(shotId)
                    && !skippedById.has(shotId)) {
                    const index = targetIndexById.get(shotId);
                    failuresById.set(shotId, {
                        label: this.shotEditorialLabel(index),
                        error: "任务未返回该镜头结果。",
                    });
                }
            }
            const modelName = [result.model?.family, result.model?.model_name]
                .filter(Boolean).join(" / ") || "QwenTE";
            const failedLabels = [...failuresById.values()]
                .slice(0, 5).map((failure) => failure.label).join("、");
            const failureSuffix = failuresById.size
                ? `；失败 ${failuresById.size} 镜（${failedLabels}${failuresById.size > 5 ? "…" : ""}）保留旧提示词`
                : "";
            const failureDetails = [...failuresById.values()]
                .slice(0, 5)
                .map(({ label, error }) => `镜头 ${label}：${error}`)
                .join("；");
            const failureReason = failureDetails
                ? `。失败原因：${failureDetails}${failuresById.size > 5 ? `；另有 ${failuresById.size - 5} 镜失败` : ""}`
                : "";
            const skippedLabels = [...skippedById.values()]
                .slice(0, 5).map((row) => row.label).join("、");
            const skippedSuffix = skippedById.size
                ? `；续写依赖跳过 ${skippedById.size} 镜（${skippedLabels}${skippedById.size > 5 ? "…" : ""}）`
                : "";
            const reviewDetails = [...reviewWarningsById.values()]
                .slice(0, 3).map(({ label, reason }) => `镜头 ${label}：${reason}`).join("；");
            const reviewSuffix = reviewWarningsById.size
                ? `；${reviewWarningsById.size} 镜已保留模型草稿，请人工检查（${reviewDetails}）`
                : "";
            const received = scope === "all" ? null : modelReceivedById.get(shotIds[0]);
            const receipt = received
                ? `服务端实际用于模型：口型${received.singing ? "开" : "关"}，唱词 ${received.lyricsChars} 字。`
                : "";
            this.setH3PromptJobStatus(shotIds, `${submittedChoice} ${receipt} H3 提示词任务完成：成功覆盖 ${appliedShotIds.size} 镜${failureSuffix}${skippedSuffix}${reviewSuffix} · ${modelName}${failureReason}。`);
            app.extensionManager?.toast?.add?.({
                severity: failuresById.size || skippedById.size || reviewWarningsById.size
                    ? (appliedShotIds.size ? "warn" : "error") : "success",
                summary: failuresById.size || skippedById.size
                    ? `H3 提示词完成，失败 ${failuresById.size} 镜、续写跳过 ${skippedById.size} 镜`
                    : reviewWarningsById.size
                        ? "H3 提示词已生成，请人工复核"
                        : (scope === "all" ? "全片 H3 提示词已生成" : "本镜 H3 提示词已生成"),
                detail: this.h3PromptStatuses.get(shotIds[0]),
                life: 7000,
            });
        } catch (error) {
            const message = error?.message || String(error);
            this.setH3PromptJobStatus(shotIds, appliedShotIds.size
                ? `${submittedChoice} 任务中断：${message} 已成功覆盖 ${appliedShotIds.size} 镜；其余镜头保留旧提示词。`
                : `${submittedChoice} 生成失败：${message} 目标镜头原有 H3 提示词保持不变。`);
            app.extensionManager?.toast?.add?.({
                severity: appliedShotIds.size ? "warn" : "error",
                summary: appliedShotIds.size ? "H3 提示词任务部分完成" : "H3 提示词生成失败",
                detail: this.h3PromptStatuses.get(shotIds[0]),
                life: 10000,
            });
        } finally {
            if (appliedShotIds.size && !await this.save(false)) {
                this.setH3PromptJobStatus(shotIds,
                    `${this.h3PromptStatuses.get(shotIds[0]) || ""} 项目草稿未落盘，请点击保存重试。`);
            }
            for (const shotId of shotIds) this.h3PromptJobs.delete(shotId);
            if (scope === "all") this.h3PromptBatchRunning = false;
            this.h3PromptRunning = this.h3PromptJobs.size > 0;
            this.updateGenerationActionStates();
        }
    }

    shouldHandlePlaybackShortcut(target) {
        if (!(target instanceof Element)) return true;
        if (target.closest('input, textarea, select, audio, [contenteditable]:not([contenteditable="false"])')) {
            return false;
        }
        const interactive = target.closest('button, a[href], summary');
        if (interactive) {
            return interactive.matches(
                '.xiaoyue-mv-clip, .xiaoyue-mv-boundary, .xiaoyue-mv-shot-card',
            );
        }
        return true;
    }

    isPlaybackShortcutZone(target) {
        if (!(target instanceof Element)) return false;
        return Boolean(target.closest([
            ".xiaoyue-mv-transport",
            ".xiaoyue-mv-timeline-toolbar",
            ".xiaoyue-mv-timeline-board",
            ".xiaoyue-mv-sidebar",
        ].join(", ")));
    }

    shouldCapturePlaybackSpace(event) {
        if (this.projectPanelOpen
            || this.dialog.querySelector(".xiaoyue-mv-input-picker")?.open
            || !this.dialog.open || event.code !== "Space"
            || event.ctrlKey || event.altKey || event.metaKey) {
            return false;
        }
        const insideDialog = event.target instanceof Node && this.dialog.contains(event.target);
        if (insideDialog && !this.shouldHandlePlaybackShortcut(event.target)) return false;
        if (this.playbackShortcutArmed) return true;
        return insideDialog;
    }

    handlePlaybackKeyDown(event) {
        if (!this.shouldCapturePlaybackSpace(event)) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        if (!event.repeat) this.togglePlayback();
    }

    handlePlaybackKeyUp(event) {
        if (!this.shouldCapturePlaybackSpace(event)) return;
        event.preventDefault();
        event.stopImmediatePropagation();
    }

    hasAudio() {
        return !this.audio.hidden && Boolean(this.audio.getAttribute("src"));
    }

    selectedPlaybackRange() {
        const shot = this.plan.shots[this.selectedShot];
        if (this.playbackMode === "shot" && shot) {
            return {
                start: Number(shot.start_seconds || 0),
                end: Number(shot.end_seconds || 0),
            };
        }
        return {
            start: 0,
            end: Number(this.audio.duration || this.plan.audio?.duration_seconds || 0),
        };
    }

    seekAudio(seconds) {
        const next = Math.max(0, Number(seconds) || 0);
        this.playheadFrame = Math.round(next * (this.plan.h3?.fps || 24));
        if (this.hasAudio()) {
            try {
                this.audio.currentTime = next;
            } catch {
                // Metadata may still be loading; the visual playhead remains authoritative until it is ready.
            }
        }
        this.renderPlayhead();
    }

    selectShot(index, { seek = true, revealTimeline = false } = {}) {
        const last = this.plan.shots.length - 1;
        if (last < 0) return;
        this.selectedShot = Math.max(0, Math.min(last, Number(index) || 0));
        if (seek) this.seekAudio(this.plan.shots[this.selectedShot].start_seconds);
        this.renderTracks();
        this.updateShotListSelection();
        this.renderShotEditor();
        if (revealTimeline) this.revealShotOnTimeline(this.selectedShot);
    }

    shotIndexAtFrame(frame) {
        const requested = Math.max(0, Number(frame) || 0);
        return this.plan.shots.findIndex((shot, index) =>
            requested >= shot.start_frame &&
            (requested < shot.end_frame || index === this.plan.shots.length - 1));
    }

    revealSelectedShotCard({ center = false } = {}) {
        const list = this.dialog.querySelector(".xiaoyue-mv-shot-list");
        const selected = this.dialog.querySelector(
            `.xiaoyue-mv-shot-card[data-shot-index="${this.selectedShot}"]`,
        );
        if (!list || !selected) return;
        const listRect = list.getBoundingClientRect();
        const selectedRect = selected.getBoundingClientRect();
        const itemTop = list.scrollTop + selectedRect.top - listRect.top;
        const itemBottom = itemTop + selectedRect.height;
        const requested = center
            ? itemTop - (list.clientHeight - selectedRect.height) / 2
            : itemTop < list.scrollTop
                ? itemTop
                : itemBottom > list.scrollTop + list.clientHeight
                    ? itemBottom - list.clientHeight
                    : list.scrollTop;
        const maximum = Math.max(0, list.scrollHeight - list.clientHeight);
        list.scrollTop = Math.max(0, Math.min(maximum, requested));
    }

    syncSelectedShotToPlayhead({ followSidebar = false } = {}) {
        const shotIndex = this.shotIndexAtFrame(this.playheadFrame);
        if (shotIndex < 0 || shotIndex === this.selectedShot) return;
        this.selectedShot = shotIndex;
        this.selectedSplice = null;
        this.renderTracks();
        this.updateShotListSelection();
        this.renderShotEditor();
        if (followSidebar) this.revealSelectedShotCard({ center: true });
    }

    setAudioSource(requested) {
        const source = requested === "vocals" ? "vocals" : "master";
        const preview = this.audioPreviews[source];
        if (!preview?.filename) {
            this.setTimelineFeedback("当前项目没有可用的对齐干声。", "blocked");
            return;
        }
        if (source === this.audioSource) return;
        const currentTime = Number(this.audio.currentTime || 0);
        const wasPlaying = !this.audio.paused;
        this.audio.pause();
        this.audioSource = source;
        this.waveform = this.waveforms[source] || this.waveforms.master || [];
        this.plan.ui_state = {
            ...(this.plan.ui_state || {}),
            audio_source: source,
        };
        const restorePosition = () => {
            const maximum = Number.isFinite(this.audio.duration)
                ? Math.max(0, this.audio.duration - 0.001)
                : currentTime;
            this.audio.currentTime = Math.min(currentTime, maximum);
            if (wasPlaying) {
                this.audio.play().catch((error) => {
                    this.setTimelineFeedback(`音频播放失败：${error?.message || error}`, "blocked");
                });
            }
        };
        this.audio.addEventListener("loadedmetadata", restorePosition, { once: true });
        this.audio.src = audioUrl(preview);
        this.audio.hidden = false;
        this.audio.load();
        this.updateAudioSourceUI();
        this.renderTimeline();
        this.setDirty(
            source === "vocals"
                ? "已切换到干声波形与试听；镜头帧位仍以母带时间轴为准。"
                : "已切换到母带波形与试听。",
            false,
        );
    }

    updateAudioSourceUI() {
        for (const button of this.dialog.querySelectorAll("[data-audio-source]")) {
            const available = Boolean(this.audioPreviews[button.dataset.audioSource]?.filename);
            button.disabled = !available;
            button.setAttribute(
                "aria-pressed",
                String(available && button.dataset.audioSource === this.audioSource),
            );
        }
    }

    automaticReplanSettingsFromDialog() {
        const requestedFrames = Number(this.field("replan-h3-max-frames").value || 226);
        const h3MaxFrames = H3_PROJECT_PRESETS.includes(requestedFrames)
            ? requestedFrames : 226;
        const maximumSeconds = h3MaxFrames / 24;
        const targetShotSeconds = Math.max(1, Math.min(
            maximumSeconds,
            Number(this.field("replan-target-shot-seconds").value || 9),
        ));
        return {
            target_shot_seconds: Number(targetShotSeconds.toFixed(3)),
            h3_max_frames: h3MaxFrames,
        };
    }

    updateAutomaticReplanSummary() {
        const settings = this.automaticReplanSettingsFromDialog();
        const target = this.field("replan-target-shot-seconds");
        target.max = (settings.h3_max_frames / 24).toFixed(3);
        target.value = settings.target_shot_seconds.toFixed(2);
        const current = this.plan.settings || {};
        this.field("replan-summary").value =
            `当前：目标 ${Number(current.target_shot_seconds || 9).toFixed(2)} 秒 / ${Number(current.h3_max_frames || 226)} 帧上限 → ` +
            `新设置：目标 ${settings.target_shot_seconds.toFixed(2)} 秒 / ${settings.h3_max_frames} 帧上限。` +
            ` 确认后将重建当前 ${this.plan.shots.length} 个镜头。`;
    }

    openAutomaticReplanDialog() {
        if (this.node._xiaoyuePlanning) return;
        const dialog = this.dialog.querySelector(".xiaoyue-mv-replan-dialog");
        const pending = this.plan.pending_replan;
        const requested = pending?.requested || this.plan.settings || {};
        const h3MaxFrames = H3_PROJECT_PRESETS.includes(Number(requested.h3_max_frames))
            ? Number(requested.h3_max_frames) : 226;
        this.field("replan-h3-max-frames").value = String(h3MaxFrames);
        this.field("replan-target-shot-seconds").value = String(
            Math.max(1, Math.min(
                h3MaxFrames / 24,
                Number(requested.target_shot_seconds || 9),
            )),
        );
        this.updateAutomaticReplanSummary();
        if (!dialog.open) dialog.showModal();
        requestAnimationFrame(() => this.field("replan-target-shot-seconds").focus());
    }

    closeAutomaticReplanDialog() {
        const dialog = this.dialog.querySelector(".xiaoyue-mv-replan-dialog");
        if (dialog?.open) dialog.close();
        this.setTimelineFeedback("未应用新参数；原有切点保持不变。", "snapped");
    }

    async requestAutomaticReplan() {
        if (this.node._xiaoyuePlanning) return;
        const requested = this.automaticReplanSettingsFromDialog();
        this.plan.settings = {
            ...(this.plan.settings || {}),
            target_shot_seconds: requested.target_shot_seconds,
            maximum_shot_seconds: requested.h3_max_frames / 24,
            h3_max_frames: requested.h3_max_frames,
        };
        this.closeAutomaticReplanDialog();
        const button = this.dialog.querySelector('[data-action="replan"]');
        this.plan.request_replan = true;
        this.plan.needs_compile = true;
        this.setDirty("正在按新参数重新自动找点；完成前保留当前界面。", true);
        this.renderWarnings();
        this.save(false);
        this.node._xiaoyuePlanning = true;
        this.node._xiaoyueOpenAfterRun = true;
        button.disabled = true;
        button.setAttribute("aria-busy", "true");
        button.textContent = "正在重新找点…";
        this.audio.pause();
        app.extensionManager?.toast?.add?.({
            severity: "info",
            summary: "正在重建全部镜头",
            detail: "已确认清空镜头级数据；项目基础资料与总资产库会保留。",
            life: 5000,
        });
        try {
            await app.queuePrompt(0, 1, [String(this.node.id)]);
        } catch (error) {
            this.node._xiaoyuePlanning = false;
            this.node._xiaoyueOpenAfterRun = false;
            button.disabled = false;
            button.setAttribute("aria-busy", "false");
            button.textContent = "重新自动找点";
            this.setTimelineFeedback(
                `重新找点未能提交：${error?.message || error}。旧切点仍在，可再次重试。`,
                "blocked",
            );
        }
    }

    setPlaybackMode(mode) {
        this.playbackMode = mode === "full" ? "full" : "shot";
        this.node._xiaoyuePlaybackMode = this.playbackMode;
        if (this.playbackMode === "shot") {
            const range = this.selectedPlaybackRange();
            if (this.audio.currentTime < range.start || this.audio.currentTime >= range.end) {
                this.seekAudio(range.start);
            }
        }
        this.updatePlaybackModeUI();
    }

    setRulerMode(mode) {
        this.rulerMode = mode === "frame" ? "frame" : "time";
        this.renderTimeline();
        this.renderShotList();
        this.renderShotEditor();
        this.updateRulerModeUI();
    }

    replaceMasterCues(values, format = "srt") {
        const previous = this.masterCues();
        const previousByRange = new Map(previous.map((cue) => [
            `${cue.absolute_start_frame}:${cue.absolute_end_frame}`,
            cue,
        ]));
        const seen = new Map();
        const masterCues = values
            .map((value, index) => canonicalSubtitleCue(value, index, this.plan))
            .filter(Boolean)
            .map((cue) => {
                const old = previousByRange.get(`${cue.absolute_start_frame}:${cue.absolute_end_frame}`);
                if (old && cue.original_text === old.original_text) {
                    cue.corrected_text = old.corrected_text;
                    cue.revision = old.revision;
                }
                const duplicate = seen.get(cue.id) || 0;
                seen.set(cue.id, duplicate + 1);
                if (duplicate) cue.id = `${cue.id}_${String(duplicate + 1).padStart(2, "0")}`;
                return cue;
            })
            .sort((left, right) => left.absolute_start_frame - right.absolute_start_frame);
        if (!masterCues.length) {
            throw new Error("识别结果没有可用的时间戳分段；原字幕母版保持不变。");
        }
        this.plan.subtitles = {
            ...(this.plan.subtitles || {}),
            format,
            master_cues: masterCues,
            cue_count: masterCues.length,
            master_role: "editable_reference",
            final_text_authority: "shots[].subtitle_track.text",
            ai_dialogue_authority: "shots[].subtitle_context.ai_dialogue_text",
            shot_timing_policy: "shot_boundaries",
            untimed_text: "",
        };
        delete this.plan.subtitles.cues;
        this.plan.subtitle_source = serializeMasterCues(masterCues, this.plan.h3?.fps || 24);
        this.recalculateSubtitleProjections();
        this.setDirty("字幕母版已更新；参考归属已按当前边界重算，镜头校正轨保持不变。", true);
        this.renderSubtitleEditor();
        this.renderWarnings();
        this.field("subtitle-source").value = this.plan.subtitle_source;
    }

    applySubtitleSource() {
        const source = this.field("subtitle-source").value;
        const parsed = parseSubtitleSource(source, this.plan);
        if (!parsed.cues.length) {
            this.field("subtitle-status").value = parsed.untimedText
                ? "没有识别到可用时间戳；纯文本不会伪造字幕时间。"
                : "没有可应用的字幕。";
            return;
        }
        this.replaceMasterCues(parsed.cues, parsed.format);
        this.field("subtitle-status").value = `已载入 ${parsed.cues.length} 条母版字幕；镜头校正轨保持不变。`;
    }

    async runSubtitleRecognition() {
        const source = this.audioPreviews.vocals?.filename ? "vocals" : "master";
        const button = this.dialog.querySelector('[data-action="recognize-subtitles"]');
        const status = this.field("subtitle-status");
        const provider = connectedSubtitleProvider(this.node);
        if (!provider) {
            status.value = "未连接本地 Whisper Provider；也可以直接粘贴时间戳字幕。";
            return;
        }
        this.subtitleProvider = provider;
        const preview = this.audioPreviews[source];
        if (!preview?.filename) {
            status.value = source === "vocals"
                ? "当前项目没有干声音频；请在“项目／声音／资产”中导入干声并保存。"
                : "当前项目没有可识别的母带；请在“项目／声音／资产”中导入母带并运行主节点。";
            return;
        }
        const audioLabel = source === "vocals" ? "对齐干声" : "母带";
        if (this.masterCues().length) {
            const correctedCount = this.masterCues().filter((cue) => Number(cue.revision || 0) > 0).length;
            const correctionWarning = correctedCount
                ? `其中 ${correctedCount} 条包含人工修正，时间段变化时修正文字可能无法保留。`
                : "";
            if (!window.confirm(
                `将用${audioLabel}重跑 ASR，并替换当前字幕母版的识别结果。${correctionWarning}` +
                "\n镜头切点和已经整理好的镜头校正轨不会改变；成功后只重算母版参考归属。是否继续？",
            )) {
                status.value = `已取消用${audioLabel}重跑 ASR；现有字幕母版保持不变。`;
                return;
            }
        }
        button.disabled = true;
        button.setAttribute("aria-busy", "true");
        button.textContent = "本地 Whisper 识别中…";
        status.value = `正在用${provider.label}识别${audioLabel}；已有字幕只会在成功返回后替换。`;
        try {
            const mediaSource = preview.server_path
                ? { source_type: "local", local_path: preview.server_path }
                : {
                    source_type: "url",
                    url: new URL(audioUrl(preview), window.location.href).href,
                };
            const requestBody = {
                fps: Number(this.plan.h3?.fps || 24),
                ...localWhisperSettings(provider),
                media_type: "audio",
                ...mediaSource,
            };
            const response = await api.fetchApi(
                "/easy-media/subtitles/recognize",
                {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(requestBody),
                },
            );
            const result = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(result.error || `字幕识别失败（HTTP ${response.status}）`);
            const segments = Array.isArray(result.segments) ? result.segments : [];
            if (!segments.length) throw new Error(`模型没有返回带时间戳的字幕。请确认${audioLabel}中有人声。`);
            this.replaceMasterCues(segments, "asr_whisper_large_v3");
            status.value = `${provider.label}已从${audioLabel}识别 ${segments.length} 条（模型分段时间戳）；镜头校正轨保持不变。`;
        } catch (error) {
            const message = error?.message || String(error);
            status.value = `${provider.label} · ${audioLabel}识别失败：${message}；原字幕母版未改变。`;
            app.extensionManager?.toast?.add?.({
                severity: "error",
                summary: "字幕识别失败",
                detail: status.value,
                life: 9000,
            });
        } finally {
            const hasMaster = this.masterCues().length > 0;
            button.disabled = !provider || !this.audioPreviews[source]?.filename;
            button.setAttribute("aria-busy", "false");
            button.textContent = `${hasMaster ? "重跑 ASR 母版" : "运行 ASR 母版"} · ${audioLabel}`;
        }
    }

    updateMasterCueText(cueId, text, sourceInput) {
        const cue = this.masterCues().find((item) => item.id === cueId);
        if (!cue) return;
        cue.corrected_text = String(text || "");
        cue.revision = Math.max(0, Number(cue.revision) || 0) + 1;
        this.plan.subtitle_source = serializeMasterCues(this.masterCues(), this.plan.h3?.fps || 24);
        for (const input of this.dialog.querySelectorAll("[data-subtitle-cue-id]")) {
            if (input !== sourceInput && input.dataset.subtitleCueId === cueId) input.value = cue.corrected_text;
        }
        this.field("subtitle-source").value = this.plan.subtitle_source;
        this.field("subtitle-status").value = `已同步 ${cueId} 到母版与所有参考区；镜头校正轨和时间戳不变。`;
        this.refreshAllShotSubtitleContexts();
        this.renderShotSubtitleContextFields(
            this.plan.shots[this.selectedShot]?.subtitle_context,
        );
        this.setDirty("字幕错字已回写母版；运行本节点后更新保护点。", true);
        this.renderWarnings();
    }

    updateShotSubtitleTrack(text) {
        const shot = this.plan.shots[this.selectedShot];
        if (!shot) return;
        const track = normalizeShotSubtitleTrack(shot);
        const nextText = singleLineSubtitleText(text);
        if (track.text === nextText) return;
        track.text = nextText;
        track.revision += 1;
        const context = this.refreshShotSubtitleContext(this.selectedShot);
        this.renderShotSubtitleContextFields(context);
        this.field("subtitle-status").value =
            `镜头 ${this.shotEditorialLabel(this.selectedShot)} 校正轨已修改；母版参考未改变。`;
        this.setDirty("镜头最终字幕已修改；保存计划后由后续提示词与生成链路读取。", false);
        this.renderWarnings();
    }

    updateShotSubtitleExtension(key, text) {
        if (!["leading_text", "trailing_text"].includes(key)) return;
        const shot = this.plan.shots[this.selectedShot];
        if (!shot) return;
        const track = normalizeShotSubtitleTrack(shot);
        const nextText = singleLineSubtitleText(text);
        if (track[key] === nextText) return;
        track[key] = nextText;
        track.revision += 1;
        const context = this.refreshShotSubtitleContext(this.selectedShot);
        this.field("shot-subtitle-ai-text").value = context?.ai_dialogue_text || "暂无唱词";
        const position = key === "leading_text" ? "前扩" : "后扩";
        this.field("subtitle-status").value =
            `镜头 ${this.shotEditorialLabel(this.selectedShot)} ${position}歌词已修改；正式字幕与母版参考未改变。`;
        this.setDirty("镜头前后扩歌词已修改；保存后由提示词链路读取。", false);
        this.renderWarnings();
    }

    fillShotSubtitleFromReference() {
        const shot = this.plan.shots[this.selectedShot];
        if (!shot) return;
        const cueById = new Map(this.masterCues().map((cue) => [cue.id, cue]));
        const seen = new Set();
        const text = (shot.subtitle_projections || [])
            .map((projection) => cueById.get(projection.source_cue_id))
            .filter((cue) => cue && !seen.has(cue.id) && seen.add(cue.id))
            .map((cue) => String(cue.corrected_text || "")
                .replace(/\s*\r?\n+\s*/g, " ")
                .trim())
            .filter(Boolean)
            .join(" ");
        if (!text) {
            this.field("subtitle-status").value = "当前镜头没有相交的母版字幕；可直接在校正轨中输入。";
            return;
        }
        const track = normalizeShotSubtitleTrack(shot);
        if (track.text && track.text !== text && !window.confirm(
            `镜头 ${this.shotEditorialLabel(this.selectedShot)} 已有校正文字。\n\n` +
            "继续会用当前相交的母版参考替换它，是否继续？",
        )) return;
        const input = this.field("shot-subtitle-track");
        input.value = text;
        this.updateShotSubtitleTrack(text);
        input.focus({ preventScroll: true });
        this.field("subtitle-status").value = "已填入与当前镜头相交的母版文字；前后一条附近参考未带入。";
    }

    createCueRow(cue, projection = null, relation = "intersecting") {
        const row = document.createElement("label");
        row.className = "xiaoyue-mv-cue-row";
        if (relation !== "intersecting") row.classList.add("is-adjacent");
        const timing = document.createElement("span");
        timing.className = "cue-timing";
        const absolute = `总 F${String(projection?.clip_abs_start_frame ?? cue.absolute_start_frame).padStart(6, "0")}–F${String(projection?.clip_abs_end_frame ?? cue.absolute_end_frame).padStart(6, "0")}`;
        timing.textContent = projection
            ? `${absolute} · 镜头 +F${String(projection.relative_start_frame).padStart(4, "0")}–+F${String(projection.relative_end_frame).padStart(4, "0")}`
            : absolute;
        if (projection?.is_partial) {
            const partial = document.createElement("em");
            partial.textContent = "跨镜头裁切";
            timing.append(" ", partial);
        }
        if (relation !== "intersecting") {
            const nearby = document.createElement("em");
            nearby.textContent = relation === "previous" ? "前一条参考" : "后一条参考";
            timing.append(" ", nearby);
        }
        const input = document.createElement("textarea");
        input.rows = 2;
        input.value = cue.corrected_text || "";
        input.dataset.subtitleCueId = cue.id;
        input.setAttribute("aria-label", `${cue.id} 母版字幕文字`);
        input.spellcheck = true;
        row.append(timing, input);
        return row;
    }

    renderSubtitleEditor() {
        const shot = this.plan.shots[this.selectedShot];
        const masterCues = this.masterCues();
        const projections = shot?.subtitle_projections || [];
        const track = shot ? normalizeShotSubtitleTrack(shot) : null;
        const context = this.refreshShotSubtitleContext(this.selectedShot);
        const shotLabel = this.shotEditorialLabel(this.selectedShot);
        this.field("shot-subtitle-final-heading").textContent = `镜头 ${shotLabel} · 最终字幕校正轨`;
        this.field("shot-subtitle-leading").value = track?.leading_text || "";
        this.field("shot-subtitle-track").value = track?.text || "";
        this.field("shot-subtitle-trailing").value = track?.trailing_text || "";
        this.renderShotSubtitleContextFields(context);
        const projectionByCueId = new Map(
            projections.map((projection) => [projection.source_cue_id, projection]),
        );
        const nearbyIndexes = new Set();
        const intersectingIndexes = masterCues
            .map((cue, index) => projectionByCueId.has(cue.id) ? index : -1)
            .filter((index) => index >= 0);
        for (const index of intersectingIndexes) nearbyIndexes.add(index);
        if (intersectingIndexes.length) {
            const first = Math.min(...intersectingIndexes);
            const last = Math.max(...intersectingIndexes);
            if (first > 0) nearbyIndexes.add(first - 1);
            if (last + 1 < masterCues.length) nearbyIndexes.add(last + 1);
        } else if (shot) {
            const next = masterCues.findIndex((cue) => cue.absolute_start_frame >= shot.end_frame);
            if (next >= 0) nearbyIndexes.add(next);
            const previous = (next >= 0 ? next : masterCues.length) - 1;
            if (previous >= 0) nearbyIndexes.add(previous);
        }
        const nearbyEntries = [...nearbyIndexes]
            .sort((left, right) => left - right)
            .map((index) => ({
                cue: masterCues[index],
                projection: projectionByCueId.get(masterCues[index].id) || null,
                relation: projectionByCueId.has(masterCues[index].id)
                    ? "intersecting"
                    : masterCues[index].absolute_end_frame <= Number(shot?.start_frame || 0)
                        ? "previous"
                        : "next",
            }));
        this.field("shot-subtitle-heading").textContent =
            `镜头 ${shotLabel} 附近母版参考 · 相交 ${projections.length} 条`;
        this.field("master-subtitle-heading").textContent = `母版字幕总表 · ${masterCues.length} 条`;
        const shotList = this.field("shot-subtitle-list");
        const masterList = this.field("master-subtitle-list");
        shotList.replaceChildren();
        masterList.replaceChildren();
        for (const entry of nearbyEntries) {
            shotList.appendChild(this.createCueRow(entry.cue, entry.projection, entry.relation));
        }
        for (const cue of masterCues) masterList.appendChild(this.createCueRow(cue));
        if (!shotList.childElementCount) {
            const empty = document.createElement("p");
            empty.className = "subtitle-empty";
            empty.textContent = masterCues.length ? "当前镜头没有字幕交集。" : "尚无字幕，可运行已连接的 ASR Provider 或粘贴时间戳字幕。";
            shotList.appendChild(empty);
        }
        if (!masterList.childElementCount) {
            const empty = document.createElement("p");
            empty.className = "subtitle-empty";
            empty.textContent = "母版字幕为空。";
            masterList.appendChild(empty);
        }
    }

    updateRulerModeUI() {
        for (const button of this.dialog.querySelectorAll("[data-ruler-mode]")) {
            button.setAttribute("aria-pressed", String(button.dataset.rulerMode === this.rulerMode));
        }
    }

    setDetailSection(requested) {
        const key = this.detailKeys.includes(requested) ? requested : "subtitle";
        this.detailKey = key;
        for (const button of this.dialog.querySelectorAll("[data-detail-target]")) {
            const selected = button.dataset.detailTarget === key;
            button.setAttribute("aria-selected", String(selected));
            button.tabIndex = selected ? 0 : -1;
        }
        for (const panel of this.dialog.querySelectorAll("[data-detail-panel]")) {
            panel.hidden = panel.dataset.detailPanel !== key;
        }
        if (key === "prompt") this.fitPromptTextarea();
        if (key === "generation") {
            this.renderShotGenerationGallery();
            this.refreshShotGenerations();
        }
    }

    generationStatus(message) {
        const output = this.field("generation-status");
        if (output) output.value = message;
    }

    activeGenerationJobs() {
        return this.generationJobs.filter((job) => job?.state === "running");
    }

    generationConflict(shotIds) {
        if (this.plan.needs_compile && this.activeGenerationJobs().length) return true;
        const windows = this.plan.generation_windows || [];
        const byLabel = new Map(windows.map((window) => [window.editorial_label, window.shot_id]));
        const parentById = new Map(windows.map((window) => [
            window.shot_id, byLabel.get(window.continuity_parent_label) || "",
        ]));
        const isAncestor = (ancestor, child) => {
            const seen = new Set();
            while (child && !seen.has(child)) {
                seen.add(child);
                child = parentById.get(child);
                if (child === ancestor) return true;
            }
            return false;
        };
        for (const job of this.activeGenerationJobs()) {
            const busyIds = job.shot_ids || [];
            if (!busyIds.length || shotIds.length > 1 || busyIds.length > 1) return true;
            for (const shotId of shotIds) {
                for (const busyId of busyIds) {
                    if (shotId === busyId || isAncestor(shotId, busyId)
                        || isAncestor(busyId, shotId)) return true;
                }
            }
        }
        return false;
    }

    activeGenerationJobForShot(shotId) {
        const jobs = this.activeGenerationJobs();
        return jobs.find((job) => (job.shot_ids || []).includes(shotId))
            || jobs.find((job) => (job.shot_ids || []).length > 1);
    }

    updateGenerationActionStates() {
        const shot = this.plan.shots[this.selectedShot];
        const shotId = String(shot?.id || "");
        const activeJobs = this.activeGenerationJobs();
        const modelBusy = this.h3PromptRunning || this.creativeOutlineRunning;
        const starting = Boolean(this.generationStarting);
        const ready = Boolean(String(shot?.prompt || "").trim());
        const currentBusy = shot && this.generationConflict([String(shot.id)]);
        this.dialog.querySelector('[data-action="generate-shot-current"]').disabled =
            starting || modelBusy || !ready || currentBusy;
        this.dialog.querySelector('[data-action="generate-shot-batch"]').disabled =
            starting || modelBusy || activeJobs.length > 0;
        const cancelButton = this.dialog.querySelector('[data-action="cancel-shot-generation"]');
        const cancelJob = shot && this.activeGenerationJobForShot(String(shot.id));
        cancelButton.disabled = !cancelJob;
        cancelButton.textContent = cancelJob?.shot_ids?.length > 1 ? "取消批量" : "取消";
        const providerReady = Boolean(this.reasoningProvider?.available && this.reasoningProvider?.token);
        const currentPrompt = this.dialog.querySelector('[data-action="generate-h3-current"]');
        const batchPrompt = this.dialog.querySelector('[data-action="generate-h3-all"]');
        const outline = this.dialog.querySelector('[data-action="generate-creative-outline"]');
        currentPrompt.disabled = !providerReady || this.creativeOutlineRunning
            || this.h3PromptBatchRunning || this.h3PromptChainConflict(shotId)
            || starting || activeJobs.length > 0;
        currentPrompt.setAttribute("aria-busy", String(this.h3PromptJobs.has(shotId)));
        currentPrompt.textContent = this.h3PromptJobs.has(shotId) ? "本镜生成中…" : "生成本镜";
        batchPrompt.disabled = !providerReady || modelBusy || starting || activeJobs.length > 0;
        batchPrompt.setAttribute("aria-busy", String(this.h3PromptBatchRunning));
        batchPrompt.textContent = this.h3PromptBatchRunning ? "批量生成中…" : "批量全生";
        outline.disabled = !providerReady || modelBusy || starting || activeJobs.length > 0;
        outline.setAttribute("aria-busy", String(this.creativeOutlineRunning));
    }

    async refreshShotGenerations() {
        const projectId = String(this.plan.project?.id || "");
        if (!projectId || this.destroyed) return;
        try {
            const response = await api.fetchApi(
                `/xiaoyue-mv/generation/${encodeURIComponent(projectId)}`,
            );
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(payload.error || `读取版本失败（HTTP ${response.status}）`);
            if (this.destroyed) return;
            this.generationState = payload.versions || { shots: {} };
            this.generationJobs = Array.isArray(payload.jobs)
                ? payload.jobs : payload.job ? [payload.job] : [];
            this.renderShotGenerationGallery();
            if (this.activeGenerationJobs().length && !this.generationPolling) {
                this.pollShotGenerations();
            }
        } catch (error) {
            this.generationStatus(`读取镜头版本失败：${error.message || error}`);
        }
    }

    renderShotGenerationGallery() {
        const gallery = this.field("generation-gallery");
        if (!gallery) return;
        const shot = this.plan.shots[this.selectedShot];
        if (!shot) {
            gallery.replaceChildren();
            delete gallery.dataset.shotId;
            return;
        }
        const shotUid = shot.shot_uid || `legacy_${shot.id}`;
        if (gallery.dataset.shotId !== shotUid) {
            gallery.replaceChildren();
            gallery.dataset.shotId = shotUid;
        }
        const record = this.generationState?.shots?.[shotUid] || {};
        const versions = Array.isArray(record.versions) ? [...record.versions].reverse() : [];
        const completedVersions = versions.filter((version) => version.status === "completed");
        const selectionRequired = completedVersions.length > 1 && !record.selection_confirmed;
        const activeJobs = this.activeGenerationJobs();
        const busy = activeJobs.length > 0 || Boolean(this.generationStarting);
        const ready = Boolean(String(shot.prompt || "").trim());
        this.updateGenerationActionStates();
        if (this.generationStarting) {
            this.generationStatus("正在校验镜头工作流输入…");
        } else if (busy) {
            const selectedJob = this.activeGenerationJobForShot(String(shot.id));
            const job = selectedJob || activeJobs[0];
            const current = this.plan.shots.find((item) => item.id === job?.current_shot_id);
            const label = current?.editorial_label || job?.current_shot_id || "排队中";
            this.generationStatus(`${activeJobs.length} 个镜头任务已提交 · ${job?.shot_ids?.length > 1 ? "顺序批量" : "单镜"} ${job?.completed_count || 0}/${job?.total_count || 0} · 当前 ${label}`
                + (job?.cancel_requested ? " · 正在安全取消…" : ""));
        } else if (this.plan.needs_compile) {
            this.generationStatus("时间轴或镜头结构待编译；点击生成时会先保存并自动重算窗口，旧版本仍可查看。");
        } else if (!ready) {
            this.generationStatus(`镜头 ${this.shotEditorialLabel(this.selectedShot)} 缺少 H3 提示词；先到“提示词”页补齐。`);
        } else if (selectionRequired) {
            this.generationStatus(`镜头 ${this.shotEditorialLabel(this.selectedShot)} 有 ${completedVersions.length} 个成功版本；请点选一个“最终使用”，续写镜头才会读取它。`);
        } else if (versions.length) {
            const attempts = versions.length - completedVersions.length;
            this.generationStatus(`镜头 ${this.shotEditorialLabel(this.selectedShot)} 有 ${completedVersions.length} 个成功版本${attempts ? `、${attempts} 次未成功尝试` : ""}；失败与取消保留记录，不占版本号。`);
        } else {
            this.generationStatus(`镜头 ${this.shotEditorialLabel(this.selectedShot)} 尚无生成版本。`);
        }
        if (!versions.length) {
            if (!gallery.querySelector(".xiaoyue-mv-generation-empty")) {
                const empty = document.createElement("p");
                empty.className = "xiaoyue-mv-generation-empty";
                empty.textContent = "生成后，各次视频会在这里预览。第一版成功时自动设为最终采用版。";
                gallery.appendChild(empty);
            }
            return;
        }
        gallery.querySelector(".xiaoyue-mv-generation-empty")?.remove();
        const presentIds = new Set(versions.map((version) => String(version.id)));
        for (const card of gallery.querySelectorAll("[data-generation-version-id]")) {
            if (!presentIds.has(card.dataset.generationVersionId)) card.remove();
        }
        const stateNames = { queued: "排队中", running: "生成中", completed: "已完成", failed: "失败", cancelled: "已取消" };
        for (let index = 0; index < versions.length; index += 1) {
            const version = versions[index];
            let card = [...gallery.children].find(
                (item) => item.dataset.generationVersionId === String(version.id),
            );
            if (!card) {
                card = document.createElement("article");
                card.className = "xiaoyue-mv-generation-version";
                card.dataset.generationVersionId = String(version.id);
                const heading = document.createElement("div");
                heading.className = "xiaoyue-mv-generation-version-heading";
                heading.append(document.createElement("strong"), document.createElement("span"));
                card.appendChild(heading);
                gallery.insertBefore(card, gallery.children[index] || null);
            }
            const title = card.querySelector(".xiaoyue-mv-generation-version-heading strong");
            const state = card.querySelector(".xiaoyue-mv-generation-version-heading span");
            const numberLabel = version.status === "completed"
                ? `v${version.number}`
                : `尝试 #${version.attempt_number || version.number || versions.length - index}`;
            title.textContent = `${this.shotEditorialLabel(this.selectedShot)} · ${numberLabel}`;
            state.textContent = `${stateNames[version.status] || version.status}${version.stale ? " · 与当前切点或选版不同" : this.plan.needs_compile ? " · 待重新编译确认" : ""}`;
            state.dataset.state = version.status;
            let cutPoints = card.querySelector(".xiaoyue-mv-generation-cut-points");
            if (version.status === "completed" && version.frame_space === "raw") {
                if (!cutPoints) {
                    cutPoints = document.createElement("p");
                    cutPoints.className = "xiaoyue-mv-generation-cut-points";
                    card.appendChild(cutPoints);
                }
                const lead = Number(version.deliver_from_raw_frame || 0);
                const deliver = Number(version.deliver_frames || 0);
                const raw = Number(version.raw_frames || 0);
                const timelineIn = version.timeline_in_frame;
                const timelineOut = version.timeline_out_frame_exclusive;
                const timelineLabel = timelineIn == null || timelineOut == null
                    ? "总轨旧版记录" : `总轨 F${timelineIn}–F${timelineOut}`;
                cutPoints.textContent = `${timelineLabel} · 原片 F${lead}–F${lead + deliver} · 尾弃 ${Math.max(0, raw - lead - deliver)}f`;
            } else {
                cutPoints?.remove();
            }
            if (version.status === "completed" && version.output?.filename && !card.querySelector("video")) {
                const video = document.createElement("video");
                video.controls = true;
                video.preload = "metadata";
                video.setAttribute("loading", "lazy");
                video.width = 640;
                video.height = 360;
                video.playsInline = true;
                video.src = audioUrl(version.output);
                video.addEventListener("loadedmetadata", () => {
                    // A tiny seek makes the first decoded frame visible while
                    // leaving the preview paused for the user's play action.
                    if (video.currentTime === 0 && video.duration > 0.001) {
                        try { video.currentTime = 0.001; } catch (_error) { /* keep controls usable */ }
                    }
                }, { once: true });
                video.setAttribute("aria-label", `${title.textContent} 视频预览`);
                card.appendChild(video);
                const actions = document.createElement("div");
                actions.className = "xiaoyue-mv-generation-version-actions";
                const enlarge = document.createElement("button");
                enlarge.type = "button";
                enlarge.textContent = "放大查看";
                enlarge.addEventListener("click", () => {
                    const latest = this.generationState?.shots?.[shotUid]?.versions
                        ?.find((item) => item.id === version.id) || version;
                    this.openGenerationPreview(latest);
                });
                const choose = document.createElement("label");
                const radio = document.createElement("input");
                radio.type = "radio";
                radio.name = `generation-final-${shotUid}`;
                radio.addEventListener("change", () => this.selectShotGeneration(shot.id, version.id));
                choose.append(radio, document.createTextNode("最终使用"));
                actions.append(enlarge, choose);
                card.appendChild(actions);
            }
            const radio = card.querySelector('input[type="radio"]');
            if (radio) {
                radio.checked = !selectionRequired && record.selected_version_id === version.id;
                radio.disabled = Boolean(this.activeGenerationJobForShot(String(shot.id)))
                    || this.plan.needs_compile;
            }
            let error = card.querySelector(".xiaoyue-mv-generation-error");
            if (version.error) {
                if (!error) {
                    error = document.createElement("p");
                    error.className = "xiaoyue-mv-generation-error";
                    card.appendChild(error);
                }
                error.textContent = version.error;
            } else if (error) {
                error.remove();
            }
        }
    }

    openGenerationPicker() {
        if (this.activeGenerationJobs().length || this.generationStarting
            || this.h3PromptRunning || this.creativeOutlineRunning) return;
        const list = this.field("generation-picker-list");
        const status = this.field("generation-picker-status");
        status.textContent = "";
        status.hidden = true;
        list.replaceChildren();
        for (let index = 0; index < this.plan.shots.length; index += 1) {
            const shot = this.plan.shots[index];
            const row = document.createElement("label");
            const checkbox = document.createElement("input");
            checkbox.type = "checkbox";
            checkbox.value = shot.id;
            checkbox.disabled = !String(shot.prompt || "").trim();
            const versions = this.generationState?.shots?.[shot.shot_uid || `legacy_${shot.id}`]?.versions || [];
            const successfulCount = versions.filter((version) => version.status === "completed").length;
            checkbox.checked = !checkbox.disabled && successfulCount === 0;
            row.append(checkbox, document.createTextNode(
                `镜头 ${this.shotEditorialLabel(index)} · ${checkbox.disabled ? "缺提示词" : successfulCount ? `${successfulCount} 版` : "未生成成功版"}`,
            ));
            list.appendChild(row);
        }
        this.updateGenerationPickerCount();
        this.dialog.querySelector(".xiaoyue-mv-generation-picker").showModal();
    }

    updateGenerationPickerCount() {
        const inputs = [...this.field("generation-picker-list").querySelectorAll('input[type="checkbox"]:not(:disabled)')];
        const chosen = inputs.filter((input) => input.checked).length;
        this.field("generation-picker-count").value = `已选 ${chosen}/${inputs.length} 镜 · 按时间轴顺序`;
        const selectAll = this.field("generation-select-all");
        selectAll.checked = chosen === inputs.length;
        selectAll.indeterminate = chosen > 0 && chosen < inputs.length;
        this.dialog.querySelector('[data-action="confirm-generation-picker"]').disabled =
            chosen === 0 || Boolean(this.generationStarting)
            || this.activeGenerationJobs().length > 0
            || this.h3PromptRunning || this.creativeOutlineRunning;
    }

    applyCompiledPlanForGeneration(plan, payload) {
        this.payload = payload;
        this.plan = clone(plan);
        this.normalizeProjectStructure();
        this.normalizeEditorialStructure();
        this.reasoningProvider = payload.reasoning_provider || this.reasoningProvider;
        const waveforms = payload.waveforms || {};
        this.waveforms = {
            master: Array.isArray(waveforms.master) ? waveforms.master : this.waveforms.master,
            vocals: Array.isArray(waveforms.vocals) ? waveforms.vocals : [],
        };
        this.audioPreviews = payload.audio_previews || this.audioPreviews;
        if (!this.audioPreviews[this.audioSource]?.filename) this.audioSource = "master";
        this.waveform = this.waveforms[this.audioSource] || this.waveforms.master;
        const previewUrl = audioUrl(this.audioPreviews[this.audioSource]);
        if (previewUrl && this.audio.getAttribute("src") !== previewUrl) {
            this.audio.pause();
            this.audio.src = previewUrl;
            this.audio.load();
        }
        this.dirty = false;
        this.renderAll();
    }

    async compilePlanForGeneration() {
        if (this.plan.pending_replan || this.plan.request_replan) {
            throw new Error("分镜参数正在等待确认重建；请先完成重新自动找点。");
        }
        if (this.node._xiaoyuePlanning || this.node._xiaoyueCompileWaiter) {
            throw new Error("MV 工作台正在运行；请等待当前任务完成后重试。");
        }
        const beforeSaveSerial = this.changeSerial;
        if (!await this.save(false)) {
            throw new Error("计划保存失败；请查看时间轴上方的保存提示后重试。");
        }
        if (this.changeSerial !== beforeSaveSerial) {
            throw new Error("保存期间镜头又被修改；新修改已保留，请再次点击生成。");
        }
        const node = this.node;
        const savedSerial = this.changeSerial;
        this.generationStatus("计划已保存，正在自动编译镜头窗口…");
        await new Promise((resolve, reject) => {
            let settled = false;
            let timeout = 0;
            const failOnExecutionError = (event) => {
                const detail = event.detail || {};
                const failedNodeId = String(detail.node_id ?? detail.node ?? "");
                if (failedNodeId !== String(node.id)) return;
                finish(new Error(detail.exception_message || detail.error
                    || "MV 工作台编译失败；请查看 ComfyUI 执行日志。"));
            };
            const finish = (error, compiledPlan, compiledPayload) => {
                if (settled) return;
                settled = true;
                window.clearTimeout(timeout);
                api.removeEventListener("execution_error", failOnExecutionError);
                api.removeEventListener("execution_interrupted", failOnExecutionError);
                if (node._xiaoyueCompileWaiter === waiter) delete node._xiaoyueCompileWaiter;
                node._xiaoyuePlanning = false;
                if (error) updateNodeLabel(node, this.plan);
                if (!error && (this.destroyed || !this.dialog.open)) {
                    error = new Error("工作台已关闭；自动生成已停止，已编译的计划仍保留。");
                }
                if (!error && this.changeSerial !== savedSerial) {
                    this.plan.needs_compile = true;
                    const widget = planWidget(node);
                    if (widget) widget.value = JSON.stringify(this.plan, null, 2);
                    markGraphChanged(node);
                    error = new Error("编译期间镜头又被修改；新修改已保留，请再次点击生成。");
                }
                if (!error && compiledPlan?.needs_compile) {
                    error = new Error("镜头窗口仍未通过编译；请检查时间轴上的警告。");
                }
                if (error) {
                    reject(error);
                    return;
                }
                try {
                    this.applyCompiledPlanForGeneration(compiledPlan, compiledPayload);
                    resolve();
                } catch (applyError) {
                    reject(applyError);
                }
            };
            const waiter = { editor: this, finish };
            node._xiaoyueCompileWaiter = waiter;
            node._xiaoyuePlanning = true;
            api.addEventListener("execution_error", failOnExecutionError);
            api.addEventListener("execution_interrupted", failOnExecutionError);
            timeout = window.setTimeout(() => finish(new Error(
                "自动编译等待超时；请检查 ComfyUI 队列或日志后重试。")), 300000);
            try {
                Promise.resolve(app.queuePrompt(0, 1, [String(node.id)]))
                    .catch((error) => finish(error));
            } catch (error) {
                finish(error);
            }
        });
    }

    async startShotGeneration(shotIds, { confirmed = false } = {}) {
        if (this.generationStarting || this.h3PromptRunning || this.creativeOutlineRunning
            || this.generationConflict(shotIds)) {
            const error = "目标镜头或续写链已有任务，或批量／提示词任务正在进行；请等待冲突任务结束。";
            this.generationStatus(error);
            return { started: false, error };
        }
        let validIds = shotIds.filter(Boolean);
        if (!validIds.length) return { started: false, error: "没有选中可生成的镜头。" };
        const text = validIds.length > 1
            ? `将按时间轴顺序生成选中的 ${validIds.length} 镜；已有版本不会覆盖。确认启动？`
            : `生成镜头 ${this.shotEditorialLabel(this.selectedShot)}？将新增独立版本，不覆盖旧视频。`;
        if (!confirmed && !window.confirm(text)) {
            return { started: false, error: "已取消生成。" };
        }
        this.generationStarting = true;
        this.renderShotGenerationGallery();
        this.updateGenerationPickerCount();
        let failureText = "";
        try {
            if (this.plan.needs_compile) {
                const selectedShots = validIds.map((id) => {
                    const shot = this.plan.shots.find((item) => item.id === id);
                    if (!shot) throw new Error(`镜头 ${id} 已不在当前计划中。`);
                    return {
                        uid: shot.shot_uid,
                        start: shot.start_frame,
                        end: shot.end_frame,
                    };
                });
                await this.compilePlanForGeneration();
                validIds = selectedShots.map((selected) => {
                    const shot = this.plan.shots.find((item) => item.shot_uid === selected.uid);
                    if (!shot || shot.start_frame !== selected.start || shot.end_frame !== selected.end) {
                        throw new Error("编译后镜头范围发生变化；请检查时间轴并重新选择要生成的镜头。");
                    }
                    return shot.id;
                });
            }
            const canvasWorkflow = (this.node.graph || app.graph)?.serialize?.();
            if (!canvasWorkflow) throw new Error("无法读取当前 ComfyUI 画布工作流。");
            const preflightResponse = await api.fetchApi("/xiaoyue-mv/generation/preflight", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    project_id: this.plan.project.id,
                    plan: this.plan,
                    shot_id: validIds[0],
                    canvas_workflow: canvasWorkflow,
                }),
            });
            const preflight = await preflightResponse.json().catch(() => ({}));
            if (!preflightResponse.ok || !preflight.valid) {
                const detail = preflight.error?.message || preflight.error?.details
                    || preflight.error || `工作流预检未通过（HTTP ${preflightResponse.status}）`;
                throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
            }
            const response = await api.fetchApi("/xiaoyue-mv/generation/start", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    project_id: this.plan.project.id,
                    plan: this.plan,
                    shot_ids: validIds,
                    canvas_workflow: canvasWorkflow,
                }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(payload.error || `启动失败（HTTP ${response.status}）`);
            this.generationJobs.push({ ...payload, shot_ids: validIds,
                total_count: validIds.length, completed_count: 0 });
            this.renderShotGenerationGallery();
            this.pollShotGenerations();
        } catch (error) {
            failureText = `镜头生成未启动：${error.message || error}`;
        } finally {
            this.generationStarting = false;
            this.renderShotGenerationGallery();
            this.updateGenerationPickerCount();
            if (failureText) {
                this.generationStatus(failureText);
                app.extensionManager?.toast?.add?.({
                    severity: "error",
                    summary: "镜头生成未启动",
                    detail: failureText,
                    life: 10000,
                });
            }
        }
        return failureText
            ? { started: false, error: failureText }
            : { started: true, error: "" };
    }

    async pollShotGenerations() {
        if (this.generationPolling) return;
        this.generationPolling = true;
        try {
            while (!this.destroyed && this.activeGenerationJobs().length) {
                const priorJobs = this.activeGenerationJobs();
                await waitMilliseconds(1800);
                await this.refreshShotGenerations();
                const activeIds = new Set(this.activeGenerationJobs().map((job) => job.job_id));
                for (const prior of priorJobs.filter((job) => !activeIds.has(job.job_id))) {
                    const response = await api.fetchApi(
                        `/xiaoyue-mv/generation/jobs/${encodeURIComponent(prior.job_id)}`,
                    );
                    const job = await response.json().catch(() => ({}));
                    if (!response.ok) throw new Error(job.error || "生成任务状态不可用；请刷新版本列表。");
                    const succeeded = (job.results || []).filter((item) => item.status === "completed").length;
                    const failures = (job.results || []).filter((item) => item.status === "failed");
                    const failed = failures.length;
                    const firstFailure = failures[0];
                    const reason = firstFailure?.error || job.error || "";
                    const message =
                        `镜头任务${job.state === "cancelled" ? "已取消" : job.state === "failed" ? "失败" : "已结束"}：成功 ${succeeded}，失败 ${failed}。已有版本均已保留。`
                        + (reason ? ` 首个失败镜头 ${firstFailure?.shot_id || ""}：${reason}` : "");
                    this.generationStatus(message);
                    if (reason && !succeeded) {
                        app.extensionManager?.toast?.add?.({
                            severity: "error", summary: "镜头任务未能入队或生成失败",
                            detail: message, life: 12000,
                        });
                    }
                }
            }
        } catch (error) {
            this.generationStatus(`镜头任务状态中断：${error.message || error}；可重新打开页面查看版本。`);
        } finally {
            this.generationPolling = false;
        }
    }

    async cancelShotGeneration() {
        const job = this.activeGenerationJobForShot(String(this.plan.shots[this.selectedShot]?.id || ""));
        const jobId = job?.job_id;
        if (!jobId) return;
        try {
            const response = await api.fetchApi(
                `/xiaoyue-mv/generation/jobs/${encodeURIComponent(jobId)}/cancel`,
                { method: "POST" },
            );
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(payload.error || "取消请求失败。");
            job.cancel_requested = true;
            this.generationStatus("已请求取消当前镜头并停止后续镜头；已有成功版本保留。 ");
            this.renderShotGenerationGallery();
        } catch (error) {
            this.generationStatus(`取消失败：${error.message || error}`);
        }
    }

    async selectShotGeneration(shotId, versionId) {
        try {
            const response = await api.fetchApi("/xiaoyue-mv/generation/select", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ project_id: this.plan.project.id, shot_id: shotId, version_id: versionId }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(payload.error || "版本选择失败。");
            this.generationState = payload.versions;
            this.renderShotGenerationGallery();
            this.generationStatus("最终版本已切换；已有下游版本仍保留原父版关系，可按需要检查衔接。");
        } catch (error) {
            this.renderShotGenerationGallery();
            this.generationStatus(`版本选择失败：${error.message || error}`);
        }
    }

    async downloadShotCutList() {
        const projectId = String(this.plan.project?.id || "");
        if (!projectId) return;
        try {
            const response = await api.fetchApi(
                `/xiaoyue-mv/generation/${encodeURIComponent(projectId)}/cut-list?format=csv`,
            );
            if (!response.ok) {
                const payload = await response.json().catch(() => ({}));
                throw new Error(payload.error || `导出失败（HTTP ${response.status}）`);
            }
            const url = URL.createObjectURL(await response.blob());
            const link = document.createElement("a");
            link.href = url;
            link.download = `${projectId}_cut_list.csv`;
            document.body.appendChild(link);
            link.click();
            link.remove();
            window.setTimeout(() => URL.revokeObjectURL(url), 60000);
            this.generationStatus("剪切表已导出；镜头原片保留，最终剪辑请使用完整原曲母带。");
        } catch (error) {
            this.generationStatus(`剪切表未导出：${error.message || error}`);
        }
    }

    openGenerationPreview(version) {
        const dialog = this.dialog.querySelector(".xiaoyue-mv-generation-preview");
        const video = dialog.querySelector("video");
        this.field("generation-preview-title").value = `镜头 ${version.editorial_label} · v${version.number}`;
        video.src = audioUrl(version.output);
        dialog.showModal();
    }

    closeGenerationPreview() {
        const dialog = this.dialog?.querySelector(".xiaoyue-mv-generation-preview");
        if (dialog?.open) dialog.close();
    }

    fitPromptTextarea() {
        const textarea = this.field("prompt");
        if (!textarea || textarea.closest("[hidden]")) return;
        textarea.style.height = "auto";
        textarea.style.height = `${Math.max(104, textarea.scrollHeight + 2)}px`;
    }

    updatePlaybackModeUI() {
        for (const button of this.dialog.querySelectorAll("[data-playback-mode]")) {
            button.setAttribute("aria-pressed", String(button.dataset.playbackMode === this.playbackMode));
        }
    }

    togglePlayback() {
        if (!this.hasAudio()) {
            this.setTimelineFeedback("当前没有可播放的音频预览。", "blocked");
            return;
        }
        if (!this.audio.paused) {
            this.audio.pause();
            return;
        }
        if (this.playbackMode === "shot") {
            const range = this.selectedPlaybackRange();
            if (this.audio.currentTime < range.start || this.audio.currentTime >= range.end - 0.01) {
                this.seekAudio(range.start);
            }
        }
        this.audio.play().catch((error) => {
            this.setTimelineFeedback(`音频播放失败：${error?.message || error}`, "blocked");
        });
    }

    syncPlayheadFromAudio() {
        this.playheadFrame = Math.round(this.audio.currentTime * (this.plan.h3?.fps || 24));
        this.renderPlayhead();
        if (this.playbackMode === "full") {
            this.syncSelectedShotToPlayhead({ followSidebar: !this.audio.paused });
        }
        this.followPlayheadDuringFullPlayback();
    }

    followPlayheadDuringFullPlayback() {
        if (this.playbackMode !== "full" || this.audio.paused || this.zoom <= 1) return;
        const viewport = this.timelineViewport;
        const viewportWidth = viewport.clientWidth;
        const contentWidth = this.timelineContent.clientWidth;
        if (!viewportWidth || contentWidth <= viewportWidth) return;
        const total = Math.max(1, this.plan.audio.timeline_frames);
        const playheadX = Math.max(0, Math.min(contentWidth, this.playheadFrame / total * contentWidth));
        const followPoint = viewportWidth * 0.78;
        const visibleLeft = viewport.scrollLeft;
        const visibleRight = visibleLeft + followPoint;
        if (playheadX > visibleRight || playheadX < visibleLeft) {
            const maximum = Math.max(0, contentWidth - viewportWidth);
            viewport.scrollLeft = Math.max(0, Math.min(maximum, playheadX - followPoint));
        }
    }

    startPlaybackMonitor() {
        this.stopPlaybackMonitor();
        const tick = () => {
            this.syncPlayheadFromAudio();
            if (this.playbackMode === "shot") {
                const range = this.selectedPlaybackRange();
                if (this.audio.currentTime >= range.end - 0.01) {
                    this.audio.pause();
                    this.seekAudio(range.end);
                    return;
                }
            }
            if (!this.audio.paused) this.playbackAnimationFrame = requestAnimationFrame(tick);
        };
        this.playbackAnimationFrame = requestAnimationFrame(tick);
    }

    stopPlaybackMonitor() {
        if (this.playbackAnimationFrame !== null) {
            cancelAnimationFrame(this.playbackAnimationFrame);
            this.playbackAnimationFrame = null;
        }
    }

    setDirty(message = "修改已记录；点击保存或关闭面板时写入节点，再次运行后校验 H3 窗口。", needsCompile = true) {
        this.dirty = true;
        this.changeSerial += 1;
        if (needsCompile) this.plan.needs_compile = true;
        const state = this.dialog.querySelector(".xiaoyue-mv-compile-state");
        state.textContent = message;
        state.classList.remove("blocked");
    }

    queueEditorWrite(path, payload) {
        const projectId = String(this.plan.project?.id || "");
        const request = async () => {
            const response = await api.fetchApi(
                `/xiaoyue-mv/editor/${encodeURIComponent(projectId)}/${path}`,
                { method: "POST", headers: { "Content-Type": "application/json" },
                  body: JSON.stringify(payload) },
            );
            const result = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(result.error || `保存失败（HTTP ${response.status}）`);
        };
        const pending = this.editorWriteQueue.then(request, request);
        this.editorWriteQueue = pending.catch(() => {});
        return pending;
    }

    async restoreEditorCheckpoint() {
        const projectId = String(this.plan.project?.id || "");
        if (!projectId) return;
        try {
            const response = await api.fetchApi(
                `/xiaoyue-mv/editor/${encodeURIComponent(projectId)}`,
            );
            const state = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(state.error || "读取编辑检查点失败。");
            if (this.destroyed || this.dirty) return;
            const draft = state.draft;
            const revision = Number(this.plan.editor_revision || 0);
            const audioFingerprint = String(this.plan.audio?.fingerprint || "");
            if (draft?.plan && Number(draft.revision || 0) > revision
                    && String(draft.plan.audio?.fingerprint || "") !== audioFingerprint) {
                this.setTimelineFeedback("项目母带已变化，旧编辑草稿未自动覆盖当前计划。", "blocked");
            }
            if (draft?.plan && Number(draft.revision || 0) > revision
                    && String(draft.plan.audio?.fingerprint || "") === audioFingerprint) {
                this.plan = clone(draft.plan);
                this.normalizeProjectStructure();
                this.normalizeEditorialStructure();
                this.selectedShot = Math.max(0, Math.min(this.selectedShot, this.plan.shots.length - 1));
                this.dirty = true;
            }
            const checkpoint = state.checkpoint;
            const currentRevision = Number(this.plan.editor_revision || 0);
            const rows = checkpoint?.shots;
            const matches = Array.isArray(rows) && rows.length === this.plan.shots.length
                && Number(checkpoint.base_revision || 0) === currentRevision
                && String(checkpoint.audio_fingerprint || "") === String(this.plan.audio?.fingerprint || "")
                && rows.every((row, index) => row.shot_uid === this.plan.shots[index].shot_uid
                    && Number.isInteger(row.start_frame) && Number.isInteger(row.end_frame)
                    && row.end_frame > row.start_frame
                    && (index === 0 || row.start_frame === rows[index - 1].end_frame))
                && rows[0]?.start_frame === this.plan.shots[0]?.start_frame
                && rows.at(-1)?.end_frame === this.plan.shots.at(-1)?.end_frame
                && checkpoint.transitions?.length === this.plan.transitions.length;
            const changed = matches && (rows.some((row, index) =>
                row.start_frame !== this.plan.shots[index].start_frame
                || row.end_frame !== this.plan.shots[index].end_frame)
                || JSON.stringify(checkpoint.transitions) !== JSON.stringify(this.plan.transitions));
            if (changed) {
                rows.forEach((row, index) => {
                    this.plan.shots[index].start_frame = row.start_frame;
                    this.plan.shots[index].end_frame = row.end_frame;
                    updateShotClock(this.plan, this.plan.shots[index]);
                });
                this.plan.transitions = clone(checkpoint.transitions);
                this.plan.generation_windows = [];
                this.plan.needs_compile = true;
                this.recalculateSubtitleProjections();
                this.recalculateAutomaticCrossfadeSafety();
                this.dirty = true;
            }
            for (const patch of state.shot_patches || []) {
                if (Number(patch.base_revision || 0) !== currentRevision) continue;
                if (String(patch.audio_fingerprint || "") !== String(this.plan.audio?.fingerprint || "")) continue;
                const shot = this.plan.shots.find((item) => item.shot_uid === patch.shot_uid);
                if (!shot) continue;
                if (shot.prompt !== patch.prompt
                        || shot.creative_outline !== patch.creative_outline
                        || shot.singing_performance !== patch.singing_performance) {
                    shot.prompt = patch.prompt;
                    shot.creative_outline = patch.creative_outline;
                    shot.singing_performance = patch.singing_performance;
                    this.dirty = true;
                }
            }
            if (this.dirty) {
                this.renderAll();
                this.setTimelineFeedback("已恢复项目目录中的未归档编辑；请检查后保存并运行节点。", "snapped");
            }
        } catch (error) {
            this.setTimelineFeedback(`编辑检查点未恢复：${error.message || error}`, "blocked");
        }
    }

    persistTimelineCheckpoint() {
        if (!this.plan.project?.id) return;
        const payload = {
            base_revision: Number(this.plan.editor_revision || 0),
            audio_fingerprint: String(this.plan.audio?.fingerprint || ""),
            shots: this.plan.shots.map((shot) => ({
                shot_uid: shot.shot_uid,
                start_frame: shot.start_frame,
                end_frame: shot.end_frame,
            })),
            transitions: clone(this.plan.transitions),
        };
        this.queueEditorWrite("timeline", payload).catch((error) => {
            this.setTimelineFeedback(`时间轴暂未落盘：${error.message || error}；请点击保存。`, "blocked");
        });
    }

    persistShotPatch(shot) {
        if (!shot?.shot_uid || !this.plan.project?.id) return;
        const timer = this.shotCheckpointTimers.get(shot.shot_uid);
        if (timer) clearTimeout(timer);
        this.shotCheckpointTimers.delete(shot.shot_uid);
        const payload = {
            base_revision: Number(this.plan.editor_revision || 0),
            audio_fingerprint: String(this.plan.audio?.fingerprint || ""),
            shot_uid: shot.shot_uid,
            prompt: String(shot.prompt || ""),
            creative_outline: String(shot.creative_outline || ""),
            singing_performance: shot.singing_performance ?? null,
        };
        this.queueEditorWrite("shot", payload).catch((error) => {
            this.setTimelineFeedback(`镜头草稿暂未落盘：${error.message || error}；请点击保存。`, "blocked");
        });
    }

    scheduleShotCheckpoint(shot) {
        if (!shot?.shot_uid) return;
        const prior = this.shotCheckpointTimers.get(shot.shot_uid);
        if (prior) clearTimeout(prior);
        const timer = setTimeout(() => {
            this.shotCheckpointTimers.delete(shot.shot_uid);
            this.persistShotPatch(shot);
        }, 1200);
        this.shotCheckpointTimers.set(shot.shot_uid, timer);
    }

    async save(closeAfter = false) {
        const widget = planWidget(this.node);
        if (!widget) return false;
        this.normalizeEditorialStructure();
        this.plan.editor_revision = Number(this.plan.editor_revision || 0) + 1;
        const savingSerial = this.changeSerial;
        this.plan.ui_state = {
            timeline_zoom: this.zoom,
            playback_mode: this.playbackMode,
            audio_source: this.audioSource,
            selected_shot_index: this.selectedShot,
            ruler_mode: this.rulerMode,
            detail_key: this.detailKey,
            sidebar_scroll_top: this.dialog.querySelector(".xiaoyue-mv-shot-list")?.scrollTop || 0,
        };
        widget.value = JSON.stringify(this.plan, null, 2);
        widget.callback?.(widget.value, app.canvas, this.node, widget);
        markGraphChanged(this.node);
        updateNodeLabel(this.node, this.plan);
        try {
            await this.queueEditorWrite("save", { plan: clone(this.plan) });
            if (savingSerial === this.changeSerial) this.dirty = false;
        } catch (error) {
            this.dirty = true;
            const status = this.dialog.querySelector(".xiaoyue-mv-compile-state");
            status.textContent = `计划已写入节点，但项目草稿落盘失败：${error.message || error}`;
            status.classList.add("blocked");
            return false;
        }
        const overBudgetCount = this.plan.shots.reduce(
            (count, _shot, index) => count + Number(!this.proxyWindows(index).valid),
            0,
        );
        const state = this.dialog.querySelector(".xiaoyue-mv-compile-state");
        state.classList.toggle("blocked", overBudgetCount > 0);
        state.textContent = overBudgetCount > 0
            ? `计划已写入节点；${overBudgetCount} 个镜头超出 H3 单窗上限，请移动边界或用剃刀切镜。还需保存 ComfyUI 工作流。`
            : this.plan.needs_compile
                ? "计划已保存；点击生成镜头时会自动编译窗口。请另存 ComfyUI 工作流以保留节点状态。"
                : "计划已写入节点；请保存 ComfyUI 工作流。";
        if (closeAfter) this.dialog.close();
        return true;
    }

    async close() {
        if (!await this.save(false)) return;
        this.dialog.close();
    }

    destroy() {
        if (this.destroyed) return;
        if (this.node._xiaoyueCompileWaiter?.editor === this) {
            this.node._xiaoyueCompileWaiter.finish(
                new Error("工作台已关闭；自动编译和镜头生成已停止。"));
        }
        this.destroyed = true;
        for (const timer of this.shotCheckpointTimers.values()) clearTimeout(timer);
        this.shotCheckpointTimers.clear();
        this.dragCleanup?.();
        if (this.playbackKeyDownHandler) {
            window.removeEventListener("keydown", this.playbackKeyDownHandler, true);
        }
        if (this.playbackKeyUpHandler) {
            window.removeEventListener("keyup", this.playbackKeyUpHandler, true);
        }
        this.audio?.pause();
        this.stopPlaybackMonitor();
        this.resizeObserver?.disconnect();
        this.promptResizeObserver?.disconnect();
        this.closeGenerationPreview();
        this.assetPreviewResizeObserver?.disconnect();
        this.dialog.remove();
        if (editorByNode.get(this.node) === this) editorByNode.delete(this.node);
    }

    renderAll() {
        this.renderProjectSummary();
        this.renderSummary();
        this.renderTimeline();
        this.renderShotList();
        this.renderShotEditor();
        this.renderWarnings();
        this.updateAudioSourceUI();
        this.updatePlaybackModeUI();
        this.updateRulerModeUI();
        this.setDetailSection(this.detailKey);
        this.field("subtitle-source").value = this.plan.subtitle_source || "";
    }

    renderSummary() {
        const bpm = Number(this.plan.analysis?.bpm || 0).toFixed(2);
        const duration = secondsLabel(this.plan.audio?.duration_seconds || 0);
        const windows = this.plan.shots.reduce(
            (total, _shot, index) => total + this.proxyWindows(index).count,
            0,
        );
        this.dialog.querySelector(".xiaoyue-mv-summary").textContent =
            `${duration} · ${this.plan.shots.length} 个镜头 · ${windows} 个 H3 窗口 · BPM ${bpm}`;
        const status = this.dialog.querySelector(".xiaoyue-mv-compile-state");
        const overBudgetCount = this.plan.shots.reduce(
            (count, _shot, index) => count + Number(!this.proxyWindows(index).valid),
            0,
        );
        const pendingReplan = this.plan.pending_replan;
        status.textContent = pendingReplan
            ? "分段参数已变化；旧切点尚未覆盖，请确认后重新自动找点。"
            : overBudgetCount
            ? `${overBudgetCount} 个镜头超出 H3 单窗上限；允许保存，请移动边界或剃刀切镜。`
            : this.plan.needs_compile
                ? "当前编辑待编译；点击生成镜头时会自动保存并编译。"
                : "计划已通过后端校验。";
        status.classList.toggle("blocked", overBudgetCount > 0);
        status.classList.toggle("pending", Boolean(this.plan.needs_compile || pendingReplan));
    }

    setTimelineFeedback(message, state = "") {
        const feedback = this.field("timeline-feedback");
        feedback.textContent = message;
        feedback.dataset.state = state;
    }

    setZoom(requested) {
        const viewport = this.timelineViewport;
        this.zoom = Math.max(1, Math.min(this.maximumZoom, Math.round(Number(requested) || 1)));
        this.renderTimeline();
        if (this.zoom <= 1) viewport.scrollLeft = 0;
        else this.revealShotOnTimeline(this.selectedShot);
    }

    revealShotOnTimeline(index) {
        if (this.zoom <= 1) return;
        const shot = this.plan.shots[index];
        if (!shot) return;
        const total = Math.max(1, Number(this.plan.audio?.timeline_frames || 1));
        const centerFrame = (Number(shot.start_frame) + Number(shot.end_frame)) / 2;
        const requested = centerFrame / total * this.timelineContent.scrollWidth -
            this.timelineViewport.clientWidth / 2;
        const maximum = Math.max(0,
            this.timelineContent.scrollWidth - this.timelineViewport.clientWidth);
        this.timelineViewport.scrollLeft = Math.max(0, Math.min(maximum, requested));
    }

    transitionCosts(index) {
        const transitions = this.plan.transitions || [];
        const incoming = index > 0 ? transitions[index - 1] : null;
        const outgoing = index < transitions.length ? transitions[index] : null;
        let leading = 0;
        let trailing = 0;
        if (incoming?.type === "long_take") {
            leading = Math.max(0, Number(incoming.context_frames || 0));
        } else if (incoming?.type === "crossfade") {
            leading = Math.floor(Number(incoming.duration_frames || 12) / 2) +
                Math.max(0, Number(
                    incoming.before_safety_frames ?? incoming.safety_frames ?? 0,
                ));
        }
        if (outgoing?.type === "crossfade") {
            const duration = Math.max(2, Number(outgoing.duration_frames || 12));
            trailing = duration - Math.floor(duration / 2) +
                Math.max(0, Number(
                    outgoing.after_safety_frames ?? outgoing.safety_frames ?? 0,
                ));
        }
        return { incoming, outgoing, leading, trailing };
    }

    recalculateAutomaticCrossfadeSafety() {
        const transitions = this.plan.transitions || [];
        const shots = this.plan.shots || [];
        const maximumFrames = Math.max(5, Number(this.plan.h3?.max_window_frames || 226));
        for (const transition of transitions) {
            if (transition.type !== "crossfade") continue;
            transition.before_safety_frames = 0;
            transition.after_safety_frames = 0;
            transition.safety_frames = 0;
            transition.safety_mode = "auto_17n5";
        }
        shots.forEach((shot, index) => {
            const incoming = index > 0 ? transitions[index - 1] : null;
            const outgoing = index < transitions.length ? transitions[index] : null;
            let required = Math.max(0, Number(shot.keep_frames || 0));
            const sides = [];
            if (incoming?.type === "long_take") {
                required += Math.max(0, Number(incoming.context_frames || 0));
            } else if (incoming?.type === "crossfade") {
                required += Math.floor(Math.max(2, Number(incoming.duration_frames || 12)) / 2);
                sides.push({ transition: incoming, key: "before_safety_frames" });
            }
            if (outgoing?.type === "crossfade") {
                const duration = Math.max(2, Number(outgoing.duration_frames || 12));
                required += duration - Math.floor(duration / 2);
                sides.push({ transition: outgoing, key: "after_safety_frames" });
            }
            if (!sides.length || required > maximumFrames) return;
            const padding = Math.max(0, legalH3Frames(required, maximumFrames) - required);
            if (sides.length === 1) {
                sides[0].transition[sides[0].key] = padding;
                return;
            }
            const first = Math.floor(padding / 2);
            sides[0].transition[sides[0].key] = first;
            sides[1].transition[sides[1].key] = padding - first;
        });
        for (const transition of transitions) {
            if (transition.type !== "crossfade") {
                transitionGeometry(transition);
                continue;
            }
            transition.safety_frames = Math.max(
                Number(transition.before_safety_frames || 0),
                Number(transition.after_safety_frames || 0),
            );
            transitionGeometry(transition);
        }
    }

    crossfadeCalculation(index, requestedDuration = null) {
        const transition = this.plan.transitions?.[index];
        const leftShot = this.plan.shots?.[index];
        const rightShot = this.plan.shots?.[index + 1];
        if (!transition || !leftShot || !rightShot) return null;
        const at = Math.round(Number(transition.at_frame ?? leftShot.end_frame) || 0);
        const leftFrames = Math.max(0, at - Number(leftShot.start_frame || 0));
        const rightFrames = Math.max(0, Number(rightShot.end_frame || at) - at);
        const maximumDuration = Math.max(2, Math.min(
            96,
            leftFrames * 2 + 1,
            rightFrames * 2,
        ));
        const sourceDuration = requestedDuration === null
            ? transition.duration_frames
            : requestedDuration;
        const duration = Math.max(2, Math.min(
            maximumDuration,
            Math.round(Number(sourceDuration) || 2),
        ));
        const before = Math.floor(duration / 2);
        const after = duration - before;
        const beforeSafety = Math.max(0, Math.round(Number(
            transition.before_safety_frames ?? transition.safety_frames ?? 0,
        )));
        const afterSafety = Math.max(0, Math.round(Number(
            transition.after_safety_frames ?? transition.safety_frames ?? 0,
        )));
        return {
            at,
            duration,
            before,
            after,
            beforeSafety,
            afterSafety,
            activeStart: at - before,
            activeEnd: at + after,
            leftRequiredUntil: at + after + afterSafety,
            rightRequiredFrom: at - before - beforeSafety,
            totalGenerationOverlap: duration + beforeSafety + afterSafety,
            leftFrames,
            rightFrames,
            maximumDuration,
        };
    }

    renderTransitionMath(index) {
        const transition = this.plan.transitions?.[index];
        const output = this.field("transition-math");
        if (!transition || !output) return;
        const at = Math.round(Number(transition.at_frame) || 0);
        const frameLabel = (frame) => `F${String(Math.round(frame)).padStart(6, "0")}`;
        if (transition.type === "crossfade") {
            const math = this.crossfadeCalculation(index);
            if (!math) return;
            output.value = [
                `叠化 ${math.duration}f：${frameLabel(math.activeStart)}–${frameLabel(math.activeEnd)}`,
                `左尾 ${math.after}f + 自动 ${math.afterSafety}f`,
                `右头 ${math.before}f + 自动 ${math.beforeSafety}f`,
                `共 ${math.totalGenerationOverlap}f · 上限 ${math.maximumDuration}f`,
            ].join(" · ");
            return;
        }
        if (transition.type === "long_take") {
            output.value = `续接 ${frameLabel(at)} · 后镜头复用左镜尾 ${Math.max(0, Number(transition.context_frames || 0))}f`;
            return;
        }
        output.value = `切点 ${frameLabel(at)} · 两侧分别按 17N+5 补齐，半透明区为最终裁边`;
    }

    shotBudget(index) {
        const shot = this.plan.shots[index];
        const fps = Number(this.plan.h3?.fps || 24);
        const maximumWindowFrames = Number(this.plan.h3?.max_window_frames || 226);
        const configuredMaximum = Math.max(1, Math.floor(
            Number(this.plan.settings?.maximum_shot_seconds || maximumWindowFrames / fps) * fps));
        const costs = this.transitionCosts(index);
        const maximumContentFrames = Math.max(1, Math.min(
            configuredMaximum,
            maximumWindowFrames - costs.leading - costs.trailing,
        ));
        const keepFrames = Number(shot?.keep_frames || 0);
        return {
            ...costs,
            maximumWindowFrames,
            maximumContentFrames,
            requiredFrames: keepFrames + costs.leading + costs.trailing,
        };
    }

    proxyWindows(index) {
        const shot = this.plan.shots[index];
        const budget = this.shotBudget(index);
        const hiddenContext = Math.max(0, Number(this.plan.settings?.hidden_continuity_frames || 22));
        const audioHandle = Math.max(0, Number(this.plan.settings?.audio_handle_frames ?? 22));
        const totalFrames = Math.max(1, Number(this.plan.audio?.timeline_frames || 1));
        const incomingCrossfade = budget.incoming?.type === "crossfade" ? budget.leading : 0;
        const outgoingCrossfade = budget.outgoing?.type === "crossfade" ? budget.trailing : 0;
        const coverageStart = shot.start_frame - incomingCrossfade;
        const coverageEnd = shot.end_frame + outgoingCrossfade;
        const breaks = [...new Set((shot.manual_splice_frames || [])
            .map((frame) => Math.round(Number(frame)))
            .filter((frame) => Number.isFinite(frame) && frame > shot.start_frame && frame < shot.end_frame))]
            .sort((left, right) => left - right);
        const boundaries = [...breaks, coverageEnd];
        const segments = [];
        let cursor = coverageStart;
        boundaries.forEach((deliveryEnd, segmentIndex) => {
            const isFirstSegment = segmentIndex === 0;
            const isLastSegment = segmentIndex === boundaries.length - 1;
            const context = segmentIndex === 0 && budget.incoming?.type === "long_take"
                ? budget.leading
                : segmentIndex === 0 ? 0 : hiddenContext;
            const requiredFrames = context + Math.max(0, deliveryEnd - cursor);
            const outgoingVirtualCut = Boolean(
                isLastSegment && budget.outgoing?.type === "long_take",
            );
            const continuesOnRight = context > 0 && (!isLastSegment || outgoingVirtualCut);
            const beforeTarget = context === 0 && (cursor > 0 || outgoingVirtualCut) && !(
                isFirstSegment && budget.incoming?.type === "crossfade"
            ) ? audioHandle : 0;
            const afterTarget = context > 0 || (deliveryEnd < totalFrames && !(
                isLastSegment && ["crossfade", "long_take"].includes(budget.outgoing?.type)
            )) ? audioHandle : 0;
            const exactH3Length = legalH3Frames(requiredFrames, budget.maximumWindowFrames)
                === requiredFrames;
            const preferredFrames = Math.min(
                budget.maximumWindowFrames,
                continuesOnRight || (context > 0 && exactH3Length)
                    ? requiredFrames : requiredFrames + beforeTarget + afterTarget,
            );
            const rawFrames = legalH3Frames(preferredFrames, budget.maximumWindowFrames);
            const handles = allocateAudioHandles(
                Math.max(0, rawFrames - requiredFrames),
                beforeTarget,
                afterTarget,
            );
            segments.push({
                start: cursor,
                end: deliveryEnd,
                context,
                requiredFrames,
                rawFrames,
                audioHandleBefore: handles.before,
                audioHandleAfter: handles.after,
                continuationTailBleed: continuesOnRight ? handles.after : 0,
                sourceStart: cursor - context - handles.before,
                sourceEnd: cursor - context - handles.before + rawFrames,
                valid: requiredFrames <= budget.maximumWindowFrames,
            });
            cursor = deliveryEnd;
        });
        return {
            breaks,
            count: segments.length,
            segments,
            valid: segments.every((segment) => segment.valid),
            maximumRequiredFrames: Math.max(0, ...segments.map((segment) => segment.requiredFrames)),
            coverageStart,
            coverageEnd,
        };
    }

    editorialLabels() {
        const labels = this.plan.shots.map(() => []);
        let groupStart = 0;
        let major = 1;
        while (groupStart < this.plan.shots.length) {
            let groupEnd = groupStart;
            while (groupEnd < (this.plan.transitions || []).length &&
                   this.plan.transitions[groupEnd]?.type === "long_take") {
                groupEnd += 1;
            }
            const parts = [];
            for (let shotIndex = groupStart; shotIndex <= groupEnd; shotIndex += 1) {
                const count = this.proxyWindows(shotIndex).count;
                for (let partIndex = 0; partIndex < count; partIndex += 1) {
                    parts.push({ shotIndex, partIndex });
                }
            }
            const base = String(major).padStart(2, "0");
            parts.forEach((part, partIndex) => {
                labels[part.shotIndex][part.partIndex] = parts.length > 1
                    ? `${base}_${alphaSuffix(partIndex)}`
                    : base;
            });
            groupStart = groupEnd + 1;
            major += 1;
        }
        return labels;
    }

    shotEditorialLabel(index, allLabels = this.editorialLabels()) {
        const labels = allLabels[index] || [];
        if (!labels.length) return String(index + 1).padStart(2, "0");
        return labels.length === 1 ? labels[0] : `${labels[0]}–${labels.at(-1)}`;
    }

    renderTimeline() {
        const viewportWidth = Math.round(this.timelineViewport.clientWidth);
        if (!viewportWidth) return;
        const contentWidth = Math.max(viewportWidth, Math.round(viewportWidth * this.zoom));
        this.timelineContent.style.width = `${contentWidth}px`;
        const rect = this.canvas.getBoundingClientRect();
        const dpr = window.devicePixelRatio || 1;
        const width = Math.round(rect.width);
        const height = Math.round(rect.height);
        if (!width || !height) return;
        this.canvas.width = Math.round(width * dpr);
        this.canvas.height = Math.round(height * dpr);
        const ctx = this.canvas.getContext("2d");
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, width, height);

        const total = Math.max(1, this.plan.audio.timeline_frames);
        const duration = this.plan.audio.duration_seconds || frameSeconds(total);
        const rulerHeight = 27;
        ctx.fillStyle = "#0b1118";
        ctx.fillRect(0, 0, width, height);
        ctx.fillStyle = "#0e1820";
        ctx.fillRect(0, rulerHeight, width, height - rulerHeight);

        ctx.font = "11px ui-monospace, SFMono-Regular, Consolas, monospace";
        ctx.textBaseline = "top";
        const drawTick = (x, label, labelWidth) => {
            ctx.strokeStyle = "rgba(178, 207, 218, .13)";
            ctx.beginPath();
            ctx.moveTo(x, rulerHeight);
            ctx.lineTo(x, height);
            ctx.stroke();
            ctx.fillStyle = "rgba(209, 226, 232, .68)";
            ctx.fillText(label, Math.min(width - labelWidth, x + 5), 6);
        };
        if (this.rulerMode === "frame") {
            const pixelsPerFrame = width / total;
            const frameSteps = [1, 2, 5, 10, 12, 17, 24, 48, 72, 120, 240, 480, 960, 1920, 3840];
            const tickFrames = frameSteps.find((step) => step * pixelsPerFrame >= 72) || 7680;
            for (let frame = 0; frame <= total; frame += tickFrames) {
                drawTick(
                    frame / total * width,
                    `F${String(frame).padStart(6, "0")}`,
                    72,
                );
            }
        } else {
            const pixelsPerSecond = width / Math.max(0.001, duration);
            const steps = [0.25, 0.5, 1, 2, 5, 10, 15, 30, 60];
            const tickSeconds = steps.find((step) => step * pixelsPerSecond >= 72) || 120;
            for (let second = 0; second <= duration + tickSeconds / 2; second += tickSeconds) {
                const label = tickSeconds < 1
                    ? secondsLabel(second)
                    : secondsLabel(second).slice(0, -4);
                drawTick(
                    second / duration * width,
                    label,
                    tickSeconds < 1 ? 70 : 45,
                );
            }
        }

        if (this.waveform.length) {
            ctx.strokeStyle = "rgba(211, 232, 239, .82)";
            ctx.lineWidth = 1;
            ctx.beginPath();
            const center = rulerHeight + (height - rulerHeight) / 2;
            const amplitude = (height - rulerHeight) * .42;
            this.waveform.forEach((value, index) => {
                const x = index / Math.max(1, this.waveform.length - 1) * width;
                const y = center - value * amplitude;
                if (index === 0) ctx.moveTo(x, y);
                else ctx.lineTo(x, y);
            });
            for (let index = this.waveform.length - 1; index >= 0; index--) {
                const x = index / Math.max(1, this.waveform.length - 1) * width;
                const y = center + this.waveform[index] * amplitude;
                ctx.lineTo(x, y);
            }
            ctx.closePath();
            ctx.fillStyle = "rgba(126, 190, 211, .13)";
            ctx.fill();
            ctx.stroke();
        } else {
            ctx.strokeStyle = "rgba(224, 240, 246, .25)";
            ctx.beginPath();
            ctx.moveTo(0, height * .55);
            ctx.lineTo(width, height * .55);
            ctx.stroke();
        }

        this.field("timeline-zoom").value = String(this.zoom);
        this.field("zoom-value").textContent = `${this.zoom}×`;
        this.dialog.querySelector('[data-action="zoom-out"]').disabled = this.zoom <= 1;
        this.dialog.querySelector('[data-action="zoom-in"]').disabled = this.zoom >= this.maximumZoom;
        this.renderTracks();
        this.renderPlayhead();
    }

    renderPlayhead() {
        const total = Math.max(1, this.plan.audio.timeline_frames);
        this.playhead.style.left = `${Math.max(0, Math.min(100, this.playheadFrame / total * 100))}%`;
    }

    createH3PromptStatusIcon(ready) {
        const status = document.createElement("span");
        status.className = `xiaoyue-mv-prompt-status-icon ${ready ? "ready" : "missing"}`;
        status.setAttribute("aria-hidden", "true");
        const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        svg.setAttribute("viewBox", "0 0 12 12");
        svg.setAttribute("focusable", "false");
        const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
        path.setAttribute("d", ready
            ? "M2 6.2 4.65 8.7 10 3.2"
            : "M3 3 9 9 M9 3 3 9");
        svg.appendChild(path);
        status.appendChild(svg);
        return status;
    }

    updateH3PromptStatus(index) {
        const ready = Boolean(String(this.plan.shots[index]?.prompt || "").trim());
        const stateText = ready ? "H3 提示词已生成" : "H3 提示词未生成";
        for (const clip of this.clipLayer.querySelectorAll(
            `.xiaoyue-mv-clip[data-shot-index="${index}"]`,
        )) {
            clip.dataset.h3PromptStatus = ready ? "ready" : "missing";
            const ariaLabelBase = clip.dataset.ariaLabelBase || "";
            clip.setAttribute("aria-label", `${ariaLabelBase}，${stateText}`);
            for (const icon of clip.querySelectorAll(".xiaoyue-mv-prompt-status-icon")) {
                icon.replaceWith(this.createH3PromptStatusIcon(ready));
            }
        }
    }

    renderTracks() {
        const total = Math.max(1, this.plan.audio.timeline_frames);
        const hiddenContext = Math.max(0, Number(this.plan.settings?.hidden_continuity_frames || 22));
        const editorialLabels = this.editorialLabels();
        this.clipLayer.replaceChildren();
        this.fxLayer.replaceChildren();

        this.plan.shots.forEach((shot, index) => {
            const proxy = this.proxyWindows(index);
            const clip = document.createElement("button");
            clip.type = "button";
            clip.className = "xiaoyue-mv-clip";
            clip.classList.add(shotToneClass(this.shotToneIndex(index)));
            clip.classList.toggle("selected", index === this.selectedShot);
            clip.classList.toggle("over-budget", !proxy.valid);
            clip.dataset.shotIndex = String(index);
            clip.style.left = `${shot.start_frame / total * 100}%`;
            clip.style.width = `${Math.max(0, shot.keep_frames / total * 100)}%`;
            const promptReady = Boolean(String(shot.prompt || "").trim());
            const ariaLabelBase = `镜头 ${this.shotEditorialLabel(index, editorialLabels)}，${secondsLabel(shot.start_seconds)} 到 ${secondsLabel(shot.end_seconds)}，${shot.duration_seconds.toFixed(3)} 秒`;
            clip.dataset.ariaLabelBase = ariaLabelBase;
            clip.dataset.h3PromptStatus = promptReady ? "ready" : "missing";
            clip.setAttribute("aria-label",
                `${ariaLabelBase}，H3 提示词${promptReady ? "已生成" : "未生成"}`);
            const parts = document.createElement("span");
            parts.className = "parts";
            const coreBoundaries = [shot.start_frame, ...proxy.breaks, shot.end_frame];
            (editorialLabels[index] || []).forEach((label, partIndex) => {
                const part = document.createElement("span");
                part.className = "name";
                part.style.left = `${(coreBoundaries[partIndex] - shot.start_frame) / Math.max(1, shot.keep_frames) * 100}%`;
                part.style.width = `${(coreBoundaries[partIndex + 1] - coreBoundaries[partIndex]) / Math.max(1, shot.keep_frames) * 100}%`;
                part.append(
                    document.createTextNode(label),
                    this.createH3PromptStatusIcon(promptReady),
                );
                parts.appendChild(part);
            });
            const duration = document.createElement("span");
            duration.className = "duration";
            const fps = Number(this.plan.h3?.fps || 24);
            const rawSummary = proxy.count === 1
                ? `H3 ${(proxy.segments[0].rawFrames / fps).toFixed(2)}s`
                : `${proxy.count}片 · H3 ${proxy.segments.map((segment) =>
                    (segment.rawFrames / fps).toFixed(2)).join("/")}s`;
            const tailBleed = proxy.segments.at(-1)?.continuationTailBleed || 0;
            duration.textContent = `成片 ${shot.duration_seconds.toFixed(2)}s · ${rawSummary}${tailBleed ? ` · 尾弃 ${tailBleed}f` : ""}`;
            clip.append(parts, duration);
            clip.addEventListener("click", (event) => {
                event.stopPropagation();
                this.selectShot(index);
            });
            this.clipLayer.appendChild(clip);

            if (index >= this.plan.shots.length - 1) return;
            const transition = this.plan.transitions[index];
            const transitionType = transition?.type || "hard_cut";
            let hardCutHandles = null;
            if (transitionType === "hard_cut") {
                const previousWindow = proxy.segments.at(-1);
                const nextWindow = this.proxyWindows(index + 1).segments[0];
                hardCutHandles = {
                    beforeFrames: Math.max(0, Number(nextWindow?.audioHandleBefore || 0)),
                    afterFrames: Math.max(0, Number(previousWindow?.audioHandleAfter || 0)),
                    previousRawFrames: Math.max(0, Number(previousWindow?.rawFrames || 0)),
                    nextRawFrames: Math.max(0, Number(nextWindow?.rawFrames || 0)),
                };
                this.appendAudioCutHandles({
                    frame: shot.end_frame,
                    total,
                    previousShotIndex: index,
                    nextShotIndex: index + 1,
                    ...hardCutHandles,
                });
            }
            if (transition?.type === "long_take") {
                this.appendContinuityOverlap({
                    frame: shot.end_frame,
                    overlapFrames: Math.max(0, Number(transition.context_frames || 0)),
                    total,
                    kind: "long-take",
                });
            }
            const continuationTailBleed = Math.max(0,
                Number(proxy.segments.at(-1)?.continuationTailBleed || 0));
            if (continuationTailBleed) {
                this.appendContinuationTailBleed({
                    frame: shot.end_frame,
                    bleedFrames: continuationTailBleed,
                    guideFrames: Number(transition.context_frames || 0),
                    total,
                });
            }
            if (transition?.type === "crossfade") {
                this.appendCrossfadeColumn({ transition, index, total });
            }
            const boundary = document.createElement("button");
            boundary.type = "button";
            boundary.className = `xiaoyue-mv-boundary ${transition?.type || "hard_cut"}`;
            boundary.style.left = `${shot.end_frame / total * 100}%`;
            boundary.dataset.index = String(index);
            const typeName = transition?.type === "long_take" ? "虚接" :
                transition?.type === "crossfade" ? "叠化" : "实接";
            boundary.setAttribute("aria-label",
                `${typeName}边界 ${index + 1}，${secondsLabel(frameSeconds(shot.end_frame, this.plan.h3.fps))}${transition?.type === "long_take" ? `，向前重合 ${transition.context_frames || 0} 帧` : ""}${continuationTailBleed ? `，前镜尾部出血 ${continuationTailBleed} 帧最终丢弃` : ""}${hardCutHandles ? `，线前头部裁边 ${hardCutHandles.beforeFrames} 帧，线后尾部裁边 ${hardCutHandles.afterFrames} 帧` : ""}`);
            const grip = document.createElement("span");
            grip.setAttribute("aria-hidden", "true");
            boundary.appendChild(grip);
            boundary.addEventListener("click", (event) => {
                event.stopPropagation();
                this.selectShot(index);
            });
            boundary.addEventListener("keydown", (event) => {
                if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
                event.preventDefault();
                const amount = event.shiftKey ? 6 : 1;
                const delta = event.key === "ArrowLeft" ? -amount : amount;
                this.moveBoundary(index, shot.end_frame + delta, false);
                this.renderTracks();
                this.renderShotEditor();
                this.renderShotCards([index, index + 1]);
                this.renderWarnings();
                this.clipLayer.querySelector(`.xiaoyue-mv-boundary[data-index="${index}"]`)?.focus();
            });
            boundary.addEventListener("pointerdown", (event) => this.beginBoundaryDrag(event, index));
            this.clipLayer.appendChild(boundary);
        });

        (this.plan.transitions || []).forEach((transition, index) => {
            if (transition.type !== "crossfade" || !transition.active_range) return;
            const start = Math.max(0, transition.left_safety_range?.[0] ?? transition.active_range[0]);
            const end = Math.min(total, transition.right_safety_range?.[1] ?? transition.active_range[1]);
            const activeStart = Math.max(start, transition.active_range[0]);
            const activeEnd = Math.min(end, transition.active_range[1]);
            const proxy = document.createElement("div");
            proxy.className = "xiaoyue-mv-crossfade-proxy";
            proxy.dataset.index = String(index);
            proxy.style.left = `${start / total * 100}%`;
            proxy.style.width = `${Math.max(0, (end - start) / total * 100)}%`;
            const sourceA = document.createElement("span");
            sourceA.className = "source-a";
            sourceA.classList.add(shotToneClass(this.shotToneIndex(index)));
            sourceA.textContent = "A";
            const sourceB = document.createElement("span");
            sourceB.className = "source-b";
            sourceB.classList.add(shotToneClass(this.shotToneIndex(index + 1)));
            sourceB.textContent = "B";
            proxy.append(sourceA, sourceB);
            const overlap = document.createElement("button");
            overlap.type = "button";
            overlap.className = "overlap";
            overlap.style.left = `${(activeStart - start) / Math.max(1, end - start) * 100}%`;
            overlap.style.width = `${(activeEnd - activeStart) / Math.max(1, end - start) * 100}%`;
            overlap.setAttribute("aria-label", `叠化 ${index + 1}，拖动边缘调整时长，当前 ${transition.duration_frames || 12} 帧`);
            overlap.innerHTML = '<span class="edge left" aria-hidden="true"></span><span class="edge right" aria-hidden="true"></span>';
            overlap.addEventListener("click", (event) => event.stopPropagation());
            overlap.addEventListener("pointerdown", (event) => {
                const rect = overlap.getBoundingClientRect();
                const edge = event.clientX < rect.left + rect.width / 2 ? "left" : "right";
                this.beginCrossfadeDrag(event, index, edge);
            });
            overlap.addEventListener("keydown", (event) => {
                if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
                event.preventDefault();
                const amount = event.shiftKey ? 6 : 1;
                const delta = event.key === "ArrowLeft" ? -amount : amount;
                this.setCrossfadeDuration(index, Number(transition.duration_frames || 12) + delta);
                this.renderTracks();
                this.renderShotEditor();
                this.renderShotCards([index, index + 1]);
                this.renderWarnings();
                this.fxLayer.querySelector(`.xiaoyue-mv-crossfade-proxy[data-index="${index}"] .overlap`)?.focus();
            });
            proxy.appendChild(overlap);
            this.fxLayer.appendChild(proxy);
        });
    }

    appendAudioCutHandles({ frame, beforeFrames, afterFrames, total,
        previousShotIndex = 0, nextShotIndex = 0,
        previousRawFrames = 0, nextRawFrames = 0 }) {
        const before = Math.max(0, Math.round(Number(beforeFrames) || 0));
        const after = Math.max(0, Math.round(Number(afterFrames) || 0));
        if (!before && !after) return;
        const start = Math.max(0, Number(frame) - before);
        const cut = Math.max(start, Math.min(total, Number(frame)));
        const end = Math.max(cut, Math.min(total, Number(frame) + after));
        const width = Math.max(1, end - start);
        const split = (cut - start) / width * 100;
        const band = document.createElement("span");
        band.className = "xiaoyue-mv-audio-cut-handles";
        band.style.left = `${start / total * 100}%`;
        band.style.width = `${(end - start) / total * 100}%`;
        band.setAttribute("aria-hidden", "true");
        band.title = `实际 H3 输入：下一镜头前裁 ${before}f / ${(nextRawFrames / (this.plan.h3?.fps || 24)).toFixed(3)}s；上一镜头尾裁 ${after}f / ${(previousRawFrames / (this.plan.h3?.fps || 24)).toFixed(3)}s`;
        const head = document.createElement("span");
        head.className = "next-head";
        head.classList.add(shotToneClass(this.shotToneIndex(nextShotIndex)));
        head.style.width = `${split}%`;
        const tail = document.createElement("span");
        tail.className = "previous-tail";
        tail.classList.add(shotToneClass(this.shotToneIndex(previousShotIndex)));
        tail.style.left = `${split}%`;
        tail.style.width = `${100 - split}%`;
        const headCaption = document.createElement("span");
        headCaption.className = "handle-caption head-caption";
        headCaption.classList.add(shotToneClass(this.shotToneIndex(nextShotIndex)));
        headCaption.style.right = `${100 - split}%`;
        headCaption.textContent = `头裁 ${before}f`;
        const tailCaption = document.createElement("span");
        tailCaption.className = "handle-caption tail-caption";
        tailCaption.classList.add(shotToneClass(this.shotToneIndex(previousShotIndex)));
        tailCaption.style.left = `${split}%`;
        tailCaption.textContent = `尾裁 ${after}f`;
        band.append(head, tail, headCaption, tailCaption);
        this.clipLayer.appendChild(band);
    }

    appendContinuityOverlap({ frame, overlapFrames, total, kind, blocked = false }) {
        const frames = Math.max(0, Math.round(Number(overlapFrames) || 0));
        if (!frames) return;
        const start = Math.max(0, Number(frame) - frames);
        const end = Math.max(start, Math.min(total, Number(frame)));
        const band = document.createElement("span");
        band.className = `xiaoyue-mv-continuity-overlap ${kind}`;
        band.classList.toggle("blocked", blocked);
        band.style.left = `${start / total * 100}%`;
        band.style.width = `${(end - start) / total * 100}%`;
        band.setAttribute("aria-hidden", "true");
        const shared = document.createElement("span");
        shared.className = "shared-tail";
        shared.style.width = "100%";
        const caption = document.createElement("span");
        caption.className = "overlap-caption";
        caption.style.right = "0";
        caption.textContent = `重合 ${frames}f`;
        band.append(shared, caption);
        this.clipLayer.appendChild(band);
    }

    appendContinuationTailBleed({ frame, bleedFrames, guideFrames, total }) {
        const frames = Math.max(0, Math.round(Number(bleedFrames) || 0));
        const visible = Math.max(0, Math.min(frames, total - frame));
        if (!visible) return;
        const band = document.createElement("span");
        band.className = "xiaoyue-mv-continuation-tail-bleed";
        band.style.left = `${frame / total * 100}%`;
        band.style.width = `${visible / total * 100}%`;
        band.setAttribute("aria-hidden", "true");
        band.title = `续写镜头尾部出血 ${frames} 帧；下一镜从出血前的交付尾部取 ${guideFrames} 帧引导`;
        const caption = document.createElement("span");
        caption.textContent = `尾弃 ${frames}f`;
        band.appendChild(caption);
        this.clipLayer.appendChild(band);
    }

    appendCrossfadeColumn({ transition, index, total }) {
        if (!transition?.active_range) return;
        const start = Math.max(0, Number(transition.active_range[0] || 0));
        const end = Math.min(total, Number(transition.active_range[1] || 0));
        if (end <= start) return;
        const at = Math.max(start, Math.min(end, Number(transition.at_frame || start)));
        const split = (at - start) / (end - start) * 100;
        const band = document.createElement("span");
        band.className = "xiaoyue-mv-crossfade-column";
        band.style.left = `${start / total * 100}%`;
        band.style.width = `${(end - start) / total * 100}%`;
        band.setAttribute("aria-hidden", "true");
        const left = document.createElement("span");
        left.className = "owner owner-left";
        left.classList.add(shotToneClass(this.shotToneIndex(index)));
        left.style.width = `${split}%`;
        const right = document.createElement("span");
        right.className = "owner owner-right";
        right.classList.add(shotToneClass(this.shotToneIndex(index + 1)));
        right.style.left = `${split}%`;
        right.style.width = `${100 - split}%`;
        const stripes = document.createElement("span");
        stripes.className = "stripes";
        const caption = document.createElement("span");
        caption.className = "crossfade-caption";
        caption.textContent = `叠化 ${transition.duration_frames || 12}f`;
        band.append(left, right, stripes, caption);
        this.clipLayer.appendChild(band);
    }

    setCrossfadeDuration(index, requested) {
        const transition = this.plan.transitions[index];
        if (!transition || transition.type !== "crossfade") return;
        const math = this.crossfadeCalculation(index, requested);
        if (!math) return;
        transition.duration_frames = math.duration;
        this.recalculateAutomaticCrossfadeSafety();
        this.selectedShot = index;
        this.setDirty();
        return this.crossfadeCalculation(index);
    }

    beginCrossfadeDrag(event, index, edge) {
        if (event.button !== 0) return;
        event.preventDefault();
        event.stopPropagation();
        this.selectedShot = index;
        this.updateShotListSelection();
        this.renderShotEditor();
        const move = (pointerEvent) => {
            const rect = this.timelineContent.getBoundingClientRect();
            const ratio = Math.max(0, Math.min(1, (pointerEvent.clientX - rect.left) / rect.width));
            const frame = Math.round(this.plan.audio.timeline_frames * ratio);
            const transition = this.plan.transitions[index];
            const at = Number(transition.at_frame || this.plan.shots[index]?.end_frame || 0);
            const halfFrames = edge === "left" ? at - frame : frame - at;
            const isOdd = Math.abs(Number(transition.duration_frames || 0)) % 2;
            const requested = edge === "left"
                ? halfFrames * 2 + isOdd
                : halfFrames * 2 - isOdd;
            const math = this.setCrossfadeDuration(index, Math.max(2, requested));
            this.renderTracks();
            this.renderShotEditor();
            this.renderShotCards([index, index + 1]);
            if (math) {
                this.setTimelineFeedback(
                    `叠化 ${math.duration}f：F${String(math.activeStart).padStart(6, "0")}–F${String(math.activeEnd).padStart(6, "0")}；自动安全为左尾 ${math.afterSafety}f、右头 ${math.beforeSafety}f。`,
                    "snapped",
                );
            }
        };
        const up = () => {
            cleanup();
            this.renderTracks();
            this.renderShotEditor();
            this.renderShotCards([index, index + 1]);
            this.renderWarnings();
            this.persistTimelineCheckpoint();
        };
        const cleanup = () => {
            window.removeEventListener("pointermove", move);
            window.removeEventListener("pointerup", up);
            window.removeEventListener("pointercancel", up);
            this.dragCleanup = null;
        };
        this.dragCleanup?.();
        this.dragCleanup = cleanup;
        window.addEventListener("pointermove", move);
        window.addEventListener("pointerup", up, { once: true });
        window.addEventListener("pointercancel", up, { once: true });
    }

    beginBoundaryDrag(event, index) {
        if (event.button !== 0) return;
        event.preventDefault();
        event.stopPropagation();
        const move = (pointerEvent) => {
            const rect = this.timelineContent.getBoundingClientRect();
            const ratio = Math.max(0, Math.min(1, (pointerEvent.clientX - rect.left) / rect.width));
            const frame = Math.round(this.plan.audio.timeline_frames * ratio);
            this.moveBoundary(index, frame, false);
            this.renderTracks();
            this.renderShotEditor();
            this.renderShotCards([index, index + 1]);
        };
        const up = () => {
            const current = this.plan.shots[index].end_frame;
            this.moveBoundary(index, current, true);
            cleanup();
            this.renderTracks();
            this.renderShotEditor();
            this.renderShotCards([index, index + 1]);
            this.renderWarnings();
            this.persistTimelineCheckpoint();
        };
        const cleanup = () => {
            window.removeEventListener("pointermove", move);
            window.removeEventListener("pointerup", up);
            window.removeEventListener("pointercancel", up);
            this.dragCleanup = null;
        };
        this.dragCleanup?.();
        this.dragCleanup = cleanup;
        window.addEventListener("pointermove", move);
        window.addEventListener("pointerup", up, { once: true });
        window.addEventListener("pointercancel", up, { once: true });
    }

    nearestProtection(frame, threshold = 10, minimum = -Infinity, maximum = Infinity) {
        const candidates = this.plan.analysis?.protection_candidates || [];
        let best = frame;
        let distance = threshold + 1;
        let score = -1;
        for (const candidate of candidates) {
            if (candidate.frame < minimum || candidate.frame > maximum) continue;
            const current = Math.abs(candidate.frame - frame);
            if (current <= threshold && (current < distance || (current === distance && candidate.score > score))) {
                best = candidate.frame;
                distance = current;
                score = candidate.score;
            }
        }
        return best;
    }

    bestProtectionInRange(minimum, maximum, target) {
        const candidates = (this.plan.analysis?.protection_candidates || [])
            .filter((candidate) => candidate.frame >= minimum && candidate.frame <= maximum);
        if (!candidates.length) return Math.round(Math.max(minimum, Math.min(maximum, target)));
        return candidates.reduce((best, candidate) => {
            const candidateRank = Number(candidate.score || 0) * 12 - Math.abs(candidate.frame - target);
            const bestRank = Number(best.score || 0) * 12 - Math.abs(best.frame - target);
            return candidateRank > bestRank ? candidate : best;
        }).frame;
    }

    splitShot(shotIndex) {
        const shot = this.plan.shots[shotIndex];
        if (!shot || shot.end_frame - shot.start_frame < 2) {
            this.setTimelineFeedback("当前镜头不足 2 帧，无法再切分。", "blocked");
            return;
        }
        const minimum = shot.start_frame + 1;
        const maximum = shot.end_frame - 1;
        const playheadInside = this.playheadFrame >= minimum && this.playheadFrame <= maximum;
        const frame = playheadInside
            ? Math.round(this.playheadFrame)
            : Math.round(this.bestProtectionInRange(minimum, maximum, (minimum + maximum) / 2));
        const rightShot = clone(shot);
        // Each half is a new generation role, even when its text was cloned.
        shot.prompt = "";
        rightShot.prompt = "";
        shot.end_frame = frame;
        rightShot.start_frame = frame;
        shot.manual_splice_frames = [];
        rightShot.manual_splice_frames = [];
        updateShotClock(this.plan, shot);
        updateShotClock(this.plan, rightShot);
        this.plan.shots.splice(shotIndex + 1, 0, rightShot);
        this.plan.transitions.splice(
            shotIndex,
            0,
            newTransition(this.plan, this.plan.settings?.default_transition || "hard_cut", frame),
        );
        this.plan.generation_windows = [];
        this.normalizeEditorialStructure();
        this.selectedShot = shotIndex;
        this.selectedSplice = null;
        this.playheadFrame = frame;
        this.setDirty();
        this.setTimelineFeedback(
            `已在母带 F${String(frame).padStart(6, "0")} 切成两个独立镜头；连接类型可在时间轴下方设置。`,
            "snapped",
        );
        this.renderAll();
        this.revealShotOnTimeline(this.selectedShot);
        void this.save(false);
    }

    deleteShot(shotIndex) {
        const shots = this.plan.shots;
        if (!shots?.[shotIndex]) return;
        if (shots.length <= 1) {
            this.setTimelineFeedback("整首至少保留一个镜头；当前镜头不能删除。", "blocked");
            return;
        }
        const removedLabel = this.shotEditorialLabel(shotIndex);
        const removedShot = shots[shotIndex];
        const removedBindings = clone(removedShot.asset_bindings || []);
        const removedTrack = normalizeShotSubtitleTrack(removedShot);
        const mergeBindings = (target, additions) => {
            target.asset_bindings ||= [];
            for (const binding of additions) {
                const existing = target.asset_bindings.find(
                    (item) => item.asset_id === binding.asset_id,
                );
                if (!existing) target.asset_bindings.push(binding);
                else if (!existing.usage && binding.usage) existing.usage = binding.usage;
            }
            target.asset_bindings.forEach((binding, index) => { binding.order = index + 1; });
        };
        if (shotIndex === 0) {
            const targetTrack = normalizeShotSubtitleTrack(shots[1]);
            targetTrack.leading_text = removedTrack.leading_text;
            targetTrack.text = mergeShotSubtitleText(removedTrack.text, targetTrack.text);
            targetTrack.revision = Math.max(targetTrack.revision, removedTrack.revision) + 1;
            shots[1].start_frame = shots[0].start_frame;
            mergeBindings(shots[1], removedBindings);
            updateShotClock(this.plan, shots[1]);
            shots.splice(0, 1);
            this.plan.transitions.splice(0, 1);
            this.selectedShot = 0;
        } else {
            const targetTrack = normalizeShotSubtitleTrack(shots[shotIndex - 1]);
            targetTrack.text = mergeShotSubtitleText(targetTrack.text, removedTrack.text);
            targetTrack.trailing_text = removedTrack.trailing_text;
            targetTrack.revision = Math.max(targetTrack.revision, removedTrack.revision) + 1;
            shots[shotIndex - 1].end_frame = shots[shotIndex].end_frame;
            mergeBindings(shots[shotIndex - 1], removedBindings);
            updateShotClock(this.plan, shots[shotIndex - 1]);
            shots.splice(shotIndex, 1);
            this.plan.transitions.splice(shotIndex - 1, 1);
            this.selectedShot = shotIndex - 1;
        }
        this.plan.generation_windows = [];
        this.normalizeEditorialStructure();
        // Only the merged shot and its uninterrupted continuation descendants
        // need new prompts. Independent cuts elsewhere retain their work.
        for (let index = this.selectedShot; index < shots.length; index += 1) {
            if (index > this.selectedShot
                    && this.plan.transitions[index - 1]?.type !== "long_take") break;
            shots[index].prompt = "";
        }
        this.selectedSplice = null;
        this.setDirty();
        const overBudget = !this.proxyWindows(this.selectedShot).valid;
        this.setTimelineFeedback(
            overBudget
                ? `已删除镜头 ${removedLabel} 并合并覆盖范围；合并后的镜头超出 H3 上限，已标红。`
                : `已删除镜头 ${removedLabel}；相邻片段已合并，母带总时长不变。`,
            overBudget ? "blocked" : "snapped",
        );
        this.renderAll();
        this.revealShotOnTimeline(this.selectedShot);
        void this.save(false);
    }

    moveBoundary(index, requested, snap) {
        const left = this.plan.shots[index];
        const right = this.plan.shots[index + 1];
        const fps = Number(this.plan.h3?.fps || 24);
        const lower = left.start_frame + 1;
        const upper = right.end_frame - 1;
        let frame = snap
            ? this.nearestProtection(requested, 10, lower, upper)
            : requested;
        const unclamped = frame;
        frame = Math.round(Math.max(lower, Math.min(upper, frame)));
        if (frame !== unclamped) {
            this.setTimelineFeedback("边界已到相邻镜头的最小 1 帧范围。", "blocked");
        } else if (snap && frame !== requested) {
            this.setTimelineFeedback(`已吸附到母带 F${String(frame).padStart(6, "0")} 的音乐保护点。`, "snapped");
        } else {
            this.setTimelineFeedback("拖动时间块边缘；松手时自动吸附音乐保护点。");
        }
        left.end_frame = frame;
        right.start_frame = frame;
        updateShotClock(this.plan, left);
        updateShotClock(this.plan, right);
        this.recalculateSubtitleProjections();
        const transition = this.plan.transitions[index];
        if (transition) {
            transition.at_frame = frame;
            transition.at_seconds = Number(frameSeconds(frame, this.plan.h3.fps).toFixed(6));
            if (transition.type === "crossfade") {
                const math = this.crossfadeCalculation(index);
                if (math) transition.duration_frames = math.duration;
            }
        }
        this.recalculateAutomaticCrossfadeSafety();
        this.selectedShot = index;
        this.setDirty();
        const overBudget = [index, index + 1]
            .filter((shotIndex) => !this.proxyWindows(shotIndex).valid)
            .map((shotIndex) => this.shotEditorialLabel(shotIndex));
        if (overBudget.length) {
            this.setTimelineFeedback(
                `镜头 ${overBudget.join("、")} 超出 H3 单窗上限，已标红；可继续移动边界或用剃刀切镜。`,
                "blocked",
            );
        }
        return frame;
    }

    shotCutMetrics(index) {
        const shot = this.plan.shots[index];
        const fps = Number(this.plan.h3?.fps || 24);
        const proxy = this.proxyWindows(index);
        const firstWindow = proxy.segments[0];
        const lastWindow = proxy.segments.at(-1);
        const usedFrames = proxy.segments.reduce(
            (total, segment) => total + Math.max(0, Number(segment.rawFrames || 0)),
            0,
        );
        const frontCutFrames = Math.max(
            0,
            Number(shot.start_frame || 0) - Number(firstWindow?.sourceStart ?? shot.start_frame),
        );
        const backCutFrames = Math.max(
            0,
            Number(lastWindow?.sourceEnd ?? shot.end_frame) - Number(shot.end_frame || 0),
        );
        return {
            proxy,
            fps,
            keptFrames: Math.max(0, Number(shot.keep_frames || 0)),
            usedFrames,
            frontCutFrames,
            backCutFrames,
        };
    }

    appendShotMetric(parent, label, frames, fps, detail = "") {
        const metric = document.createElement("span");
        metric.className = "shot-metric";
        const name = document.createElement("span");
        name.className = "metric-label";
        name.textContent = detail ? `${label} · ${detail}` : label;
        const value = document.createElement("span");
        value.className = "metric-value";
        value.textContent = `${frames}f`;
        metric.append(name, value);
        parent.appendChild(metric);
    }

    shotRangeLabel(shot) {
        return `F${String(shot.start_frame).padStart(6, "0")} — F${String(shot.end_frame).padStart(6, "0")}`;
    }

    createShotCard(shot, index, editorialLabels = this.editorialLabels()) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "xiaoyue-mv-shot-card";
        button.classList.add(shotToneClass(this.shotToneIndex(index)));
        button.dataset.shotIndex = String(index);
        button.classList.toggle("selected", index === this.selectedShot);
        button.setAttribute("role", "listitem");
        const transition = this.plan.transitions[index];
        const transitionName = transition?.type === "long_take" ? "虚接" :
            transition?.type === "crossfade" ? "叠化" : transition ? "实接" : "结束";
        const proxy = this.proxyWindows(index);
        button.classList.toggle("over-budget", !proxy.valid);
        const generationMode = proxy.count > 1 ? `续接链 × ${proxy.count}` : "单窗";
        const metrics = this.shotCutMetrics(index);
        const assetById = new Map(this.plan.assets.map((asset) => [asset.id, asset]));
        const boundAssets = (shot.asset_bindings || [])
            .map((binding) => assetById.get(binding.asset_id))
            .filter(Boolean);
        const reference = boundAssets.length
            ? boundAssets.map((asset) => asset.name).join("、")
            : shot.material?.reference || "未指定素材";
        const title = document.createElement("strong");
        title.textContent = `镜头 ${this.shotEditorialLabel(index, editorialLabels)}`;
        const time = document.createElement("span");
        time.className = "time";
        time.textContent = this.shotRangeLabel(shot);
        const meta = document.createElement("span");
        meta.className = "meta";
        meta.textContent = `${transitionName} · ${generationMode}`;
        const metricGrid = document.createElement("span");
        metricGrid.className = "shot-metrics";
        this.appendShotMetric(metricGrid, "实切", metrics.keptFrames, metrics.fps);
        this.appendShotMetric(
            metricGrid,
            "H3 使用",
            metrics.usedFrames,
            metrics.fps,
            metrics.proxy.count > 1 ? `${metrics.proxy.count}窗合计` : "",
        );
        this.appendShotMetric(metricGrid, "前切", metrics.frontCutFrames, metrics.fps);
        this.appendShotMetric(metricGrid, "后切", metrics.backCutFrames, metrics.fps);
        button.append(title, time, meta, metricGrid);
        button.setAttribute(
            "aria-label",
            `镜头 ${this.shotEditorialLabel(index, editorialLabels)}，母带总帧入点 ${shot.start_frame}，出点 ${shot.end_frame}，实切 ${metrics.keptFrames} 帧，H3 使用 ${metrics.usedFrames} 帧，前切 ${metrics.frontCutFrames} 帧，后切 ${metrics.backCutFrames} 帧，${transitionName}，${reference}`,
        );
        button.addEventListener("click", () => {
            this.selectShot(index, { revealTimeline: true });
        });
        return button;
    }

    renderShotList({ preserveScroll = true } = {}) {
        const list = this.dialog.querySelector(".xiaoyue-mv-shot-list");
        const scrollTop = preserveScroll ? list.scrollTop : 0;
        const editorialLabels = this.editorialLabels();
        list.replaceChildren();
        this.field("shot-count").textContent = `${this.plan.shots.length} 段`;
        this.plan.shots.forEach((shot, index) => {
            list.appendChild(this.createShotCard(shot, index, editorialLabels));
        });
        list.scrollTop = scrollTop;
    }

    renderShotCards(indices) {
        const list = this.dialog.querySelector(".xiaoyue-mv-shot-list");
        const scrollTop = list.scrollTop;
        const labels = this.editorialLabels();
        for (const index of new Set(indices)) {
            const shot = this.plan.shots[index];
            const current = list.querySelector(`.xiaoyue-mv-shot-card[data-shot-index="${index}"]`);
            if (shot && current) current.replaceWith(this.createShotCard(shot, index, labels));
        }
        list.scrollTop = scrollTop;
    }

    updateShotListSelection() {
        for (const card of this.dialog.querySelectorAll(".xiaoyue-mv-shot-card")) {
            card.classList.toggle("selected", Number(card.dataset.shotIndex) === this.selectedShot);
        }
    }

    renderShotEditor() {
        const shot = this.plan.shots[this.selectedShot];
        if (!shot) return;
        shot.material ||= { kind: "image", reference: "", note: "" };
        shot.asset_bindings ||= [];
        const shotLabel = this.shotEditorialLabel(this.selectedShot);
        this.dialog.querySelector('[data-action="add-splice"]')
            ?.setAttribute("aria-label", `在镜头 ${shotLabel} 的播放头位置切镜`);
        const removeSplice = this.dialog.querySelector('[data-action="remove-splice"]');
        removeSplice.disabled = false;
        removeSplice.setAttribute("aria-label", `删除镜头 ${shotLabel} 并与相邻镜头合并`);
        if (this.field("material-kind")) {
            this.field("material-kind").value = shot.material.kind || "image";
        }
        if (this.field("material-reference")) {
            this.field("material-reference").value = shot.material.reference || "";
        }
        this.field("creative-outline").value = shot.creative_outline || "";
        this.field("singing-performance").checked = typeof shot.singing_performance === "boolean"
            ? shot.singing_performance
            : Boolean(shot.subtitle_context?.ai_dialogue_text);
        this.field("prompt").value = shot.prompt || "";
        this.field("h3-prompt-status").value =
            this.h3PromptStatuses.get(String(shot.id)) || "";
        this.fitPromptTextarea();
        this.field("outline-overall-prompt").value = this.plan.project.overall_prompt || "";
        this.field("outline-plan-overview").value = this.plan.creative_plan.overview || "";
        this.field("active-shot-label").value = `镜头 ${shotLabel}`;
        this.renderShotGenerationGallery();
        this.renderShotAssetBindings();
        this.renderPromptContext();
        this.renderCreativeAssetNeeds();
        this.renderOutlineVisualAssets();
        this.renderSubtitleEditor();
        this.updateGenerationActionStates();

        const transition = this.plan.transitions[this.selectedShot];
        const editor = this.dialog.querySelector(".xiaoyue-mv-transition-editor");
        editor.classList.toggle("disabled", !transition);
        for (const control of editor.querySelectorAll("input, select")) control.disabled = !transition;
        if (!transition) {
            for (const setting of editor.querySelectorAll("[data-setting]")) setting.hidden = true;
            this.field("transition-math").value = "最后镜头 · 无下一段连接";
            return;
        }
        this.field("transition-type").value = transition.type;
        this.field("context-frames").value = String(transition.context_frames || 22);
        this.field("duration-frames").value = String(transition.duration_frames || 12);
        editor.querySelector('[data-setting="context"]').hidden = transition.type !== "long_take";
        editor.querySelector('[data-setting="duration"]').hidden = transition.type !== "crossfade";
        editor.querySelector('[data-setting="safety"]').hidden = transition.type !== "crossfade";
        if (transition.type === "crossfade") {
            const math = this.crossfadeCalculation(this.selectedShot);
            this.field("auto-safety-frames").value = math
                ? `左尾 ${math.afterSafety}f / 右头 ${math.beforeSafety}f`
                : "自动计算";
        }
        this.renderTransitionMath(this.selectedShot);
    }

    renderWarnings() {
        const panel = this.dialog.querySelector(".xiaoyue-mv-warnings");
        panel.replaceChildren();
        const syncNotices = Array.isArray(this.plan.project_sync?.notices)
            ? this.plan.project_sync.notices
            : [];
        if (syncNotices.length) {
            const sync = document.createElement("section");
            sync.className = "project-sync";
            const heading = document.createElement("strong");
            heading.textContent = "本次项目资料同步";
            const updates = document.createElement("ul");
            for (const message of syncNotices) {
                const item = document.createElement("li");
                item.textContent = message;
                updates.appendChild(item);
            }
            sync.append(heading, updates);
            panel.appendChild(sync);
        }
        if (this.plan.needs_compile) {
            const pending = document.createElement("p");
            pending.className = "pending";
            pending.textContent = "时间轴已修改；生成镜头前会自动重算隐藏重叠、补齐和采样位置。";
            panel.appendChild(pending);
        }
        const warnings = [...(this.plan.warnings || [])];
        const pendingReplan = this.plan.pending_replan;
        if (pendingReplan) {
            warnings.unshift(
                pendingReplan.message
                || "自动分镜参数已变化；旧打点暂时保留，确认重新找点后才会覆盖。",
            );
        }
        this.plan.shots.forEach((_shot, index) => {
            const proxy = this.proxyWindows(index);
            if (!proxy.valid) {
                warnings.unshift(
                    `镜头 ${this.shotEditorialLabel(index)} 需要 ${proxy.maximumRequiredFrames} 帧，超过 H3 单窗上限；计划可以保存，但生成前需要移动边界或剃刀切镜。`,
                );
            }
        });
        const button = this.dialog.querySelector('[data-action="show-warnings"]');
        const replanButton = this.dialog.querySelector('[data-action="replan"]');
        if (!this.node._xiaoyuePlanning) {
            replanButton.textContent = pendingReplan ? "确认覆盖并重新找点" : "重新自动找点";
            replanButton.disabled = false;
            replanButton.setAttribute("aria-busy", "false");
        }
        replanButton.classList.toggle("pending", Boolean(pendingReplan));
        const count = this.field("warning-count");
        button.classList.toggle("has-warnings", warnings.length > 0);
        button.classList.toggle("pending", Boolean(this.plan.needs_compile));
        count.textContent = warnings.length
            ? `警告 ${warnings.length}`
            : this.plan.needs_compile
                ? "待校验"
                : syncNotices.length
                    ? "已同步"
                : "检查通过";
        button.setAttribute(
            "aria-label",
            warnings.length
                ? `查看 ${warnings.length} 条规划警告`
                : this.plan.needs_compile
                    ? "查看待校验的规划信息"
                    : "查看规划检查结果，当前通过",
        );
        if (!warnings.length) {
            const okay = document.createElement("p");
            okay.className = "okay";
            okay.textContent = syncNotices.length
                ? "项目更新已按上述规则处理；当前没有 H3 长度或连续链风险。"
                : "当前后端计划没有发现 H3 长度或连续链风险。";
            panel.appendChild(okay);
            return;
        }
        const list = document.createElement("ul");
        for (const warning of warnings) {
            const item = document.createElement("li");
            item.textContent = warning;
            list.appendChild(item);
        }
        panel.appendChild(list);
    }
}

async function openPlanner(node) {
    installStyles();
    if (!String(widgetValue(node, "project_id") || "").trim()) {
        app.extensionManager?.toast?.add?.({
            severity: "info",
            summary: "请先选择 MV 项目",
            detail: "项目、声音、字幕和资产都在同一个主节点中管理。",
            life: 5000,
        });
        openProjectEditor(node);
        return;
    }
    const plan = parsePlan(node);
    const payload = payloadByNode.get(node);
    const needsInitialPlan = !plan?.shots?.length;
    const needsAudioRestore = !needsInitialPlan && (
        !payload?.audio_preview?.filename
        || !(Array.isArray(payload?.waveforms?.master) || Array.isArray(payload?.waveform))
    );
    // Project-owned audio, lyrics and detachable providers can change without
    // changing the node's visible surface. Refresh on every explicit open so
    // the workbench never presents a stale project snapshot.
    if (node._xiaoyuePlanning) return;
    node._xiaoyuePlanning = true;
    node._xiaoyueOpenAfterRun = true;
    if (node._xiaoyuePlannerButton) {
        node._xiaoyuePlannerButton.name = needsInitialPlan
            ? "② MV 工作台 · 正在分析母带…"
            : needsAudioRestore
                ? "② MV 工作台 · 正在恢复音轨…"
                : "② MV 工作台 · 正在同步项目资料…";
    }
    node.setDirtyCanvas?.(true, true);
    app.extensionManager?.toast?.add?.({
        severity: "info",
        summary: needsInitialPlan ? "正在分析完整母带" : "正在同步 MV 工作台",
        detail: needsInitialPlan
            ? "分析完成后会自动打开 MV 规划器。"
            : "正在检查新干声、歌词、字幕和分段参数；旧切点不会被静默覆盖。",
        life: 5000,
    });
    try {
        await app.queuePrompt(0, 1, [String(node.id)]);
    } catch (error) {
        node._xiaoyuePlanning = false;
        node._xiaoyueOpenAfterRun = false;
        updateNodeLabel(node);
        app.extensionManager?.toast?.add?.({
            severity: "error",
            summary: "MV 规划器运行失败",
            detail: error?.message || String(error),
            life: 8000,
        });
        if (plan?.shots?.length) new PlannerEditor(node, plan, payload);
    }
}

function installShotOutputLabelTooltips() {
    if (shotOutputTooltipInstalled) return;
    shotOutputTooltipInstalled = true;
    const tooltip = document.createElement("div");
    tooltip.className = "xiaoyue-mv-output-tooltip";
    tooltip.setAttribute("role", "tooltip");
    tooltip.hidden = true;
    const title = document.createElement("strong");
    const detail = document.createElement("span");
    tooltip.append(title, detail);
    document.body.appendChild(tooltip);
    const measure = document.createElement("canvas").getContext("2d");
    if (measure) measure.font = "14px Arial";
    let timer = 0;
    let activeKey = "";

    function hide() {
        window.clearTimeout(timer);
        timer = 0;
        activeKey = "";
        tooltip.hidden = true;
    }

    function position(x, y) {
        const bounds = tooltip.getBoundingClientRect();
        const left = x + 14 + bounds.width <= window.innerWidth - 8
            ? x + 14 : x - bounds.width - 14;
        const above = y - bounds.height - 12;
        tooltip.style.left = `${Math.max(8, left)}px`;
        tooltip.style.top = `${Math.max(8, above >= 8 ? above : y + 12)}px`;
    }

    window.addEventListener("mousemove", (event) => {
        if (tooltip.contains(event.target)) return;
        const canvas = app.canvas;
        const node = canvas?.node_over;
        if (event.target?.nodeName !== "CANVAS"
                || ![SHOT_INPUT_NODE_NAME, NODE_NAME].includes(node?.type)
                || node.flags?.collapsed || !canvas.graph_mouse || !node.getOutputPos) {
            hide();
            return;
        }
        const [x, y] = canvas.graph_mouse;
        const index = node.outputs?.findIndex((output, slotIndex) => {
            const [slotX, slotY] = node.getOutputPos(slotIndex);
            if (Math.abs(y - slotY) > 10) return false;
            const label = String(output.label || output.localized_name || output.name || "");
            const width = (measure?.measureText(label).width || label.length * 8) + 16;
            return x >= Math.max(node.pos[0] + 8, slotX - width)
                && x <= slotX - 12;
        }) ?? -1;
        const text = index >= 0 ? outputTooltipsByNodeType.get(node.type)?.[index] : "";
        if (!text) {
            hide();
            return;
        }
        const key = `${node.id}:${index}`;
        if (key === activeKey) return;
        hide();
        activeKey = key;
        timer = window.setTimeout(() => {
            title.textContent = node.outputs[index].name;
            detail.textContent = text;
            tooltip.hidden = false;
            position(event.clientX, event.clientY);
        }, 450);
    }, { passive: true });
    tooltip.addEventListener("mouseleave", hide);
    window.addEventListener("click", hide);
    window.addEventListener("keydown", (event) => {
        if (event.key === "Escape") hide();
    });
}

function removeLegacyShotControls(node, removeShotId) {
    const obsolete = new Set(["manual_prompt"]);
    if (removeShotId) obsolete.add("shot_id");
    const inputIndices = (node.inputs || []).flatMap((input, index) =>
        obsolete.has(input.name) ? [index] : []);
    const widgetIndices = (node.widgets || []).flatMap((widget, index) =>
        obsolete.has(widget.name) ? [index] : []);
    if (!inputIndices.length && !widgetIndices.length) return;
    const graph = node.graph || app.graph;
    graph?.beforeChange?.(node);
    try {
        for (const index of inputIndices.reverse()) node.removeInput(index);
        for (const index of widgetIndices.reverse()) {
            if (obsolete.has(node.widgets?.[index]?.name)) node.widgets.splice(index, 1);
        }
        if (removeShotId) delete node.properties?.ue_properties?.widget_ue_connectable?.shot_id;
        const minimum = node.computeSize?.();
        if (minimum && node.size) node.setSize([node.size[0], minimum[1]]);
        markGraphChanged(node);
    } finally {
        graph?.afterChange?.(node);
    }
}

app.registerExtension({
    name: EXTENSION_NAME,
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name === SHOT_INPUT_NODE_NAME) {
            outputTooltipsByNodeType.set(nodeData.name, Array.isArray(nodeData.output_tooltips)
                ? nodeData.output_tooltips : (nodeData.outputs || []).map((output) => output.tooltip));
            const removeShotId = !Object.prototype.hasOwnProperty.call(
                nodeData.input?.required || {}, "shot_id");
            installStyles();
            installShotOutputLabelTooltips();
            const onNodeCreated = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function () {
                onNodeCreated?.apply(this, arguments);
                removeLegacyShotControls(this, removeShotId);
            };
            const onConfigure = nodeType.prototype.onConfigure;
            nodeType.prototype.onConfigure = function (info) {
                onConfigure?.apply(this, arguments);
                // Older saved graphs can restore removed prompt and shot-id widgets.
                window.setTimeout(() => removeLegacyShotControls(this, removeShotId), 0);
            };
            return;
        }
        if (nodeData.name !== NODE_NAME) return;
        outputTooltipsByNodeType.set(nodeData.name, Array.isArray(nodeData.output_tooltips)
            ? nodeData.output_tooltips : (nodeData.outputs || []).map((output) => output.tooltip)
        );
        installShotOutputLabelTooltips();
        installStyles();
        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            onNodeCreated?.apply(this, arguments);
            hideInternalWidget(nodeWidget(this, "project_name"));
            hideInternalWidget(nodeWidget(this, "project_code"));
            hideInternalWidget(nodeWidget(this, "project_id"));
            hideInternalWidget(nodeWidget(this, "project_state_json"));
            hideInternalWidget(planWidget(this));
            this._xiaoyueProjectButton = this.addWidget(
                "button",
                "① 项目设定",
                null,
                () => openProjectEditor(this),
                { serialize: false },
            );
            this._xiaoyuePlannerButton = this.addWidget(
                "button",
                "② MV 工作台 · 请先运行一次",
                null,
                () => openPlanner(this),
                { serialize: false },
            );
            orderWorkbenchButtons(this);
            this.setSize([Math.max(this.size?.[0] || 0, 430), Math.max(this.size?.[1] || 0, 210)]);
            updateProjectNodeLabel(this);
            updateNodeLabel(this);
        };

        const onConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function (info) {
            onConfigure?.apply(this, arguments);
            // LiteGraph restores serialized inputs after node creation. Defer
            // one task so every upstream node/link from the old workflow is
            // available before obsolete sockets and orphan helpers are removed.
            window.setTimeout(() => {
                migrateLegacyPlannerNode(this, info);
                migrateSplitWorkbench(this);
                const seed = nodeWidget(this, "seed");
                if (seed && (seed.value === null || seed.value === undefined)) seed.value = -1;
            }, 0);
        };

        const onExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message) {
            onExecuted?.apply(this, arguments);
            this._xiaoyuePlanning = false;
            const compileWaiter = this._xiaoyueCompileWaiter;
            const payload = plannerPayload(message);
            if (!payload?.plan_json) {
                compileWaiter?.finish(new Error("MV 工作台没有返回已编译计划；镜头生成未启动。"));
                return;
            }
            const previousPlan = parsePlan(this);
            let outputPlan;
            try {
                outputPlan = JSON.parse(payload.plan_json);
            } catch (error) {
                compileWaiter?.finish(error);
                return;
            }
            if (payload.project_json) {
                let manifest;
                try {
                    manifest = JSON.parse(payload.project_json);
                } catch (error) {
                    compileWaiter?.finish(error);
                    return;
                }
                const previousState = parseProjectNodeState(this) || {};
                const state = { ...previousState, project: manifest };
                nodeWidget(this, "project_name").value = manifest.project_name || "";
                nodeWidget(this, "project_code").value = manifest.project_code || "";
                nodeWidget(this, "project_id").value = manifest.project_id || "";
                nodeWidget(this, "project_state_json").value = JSON.stringify(state, null, 2);
                updateProjectNodeLabel(this, state);
            }
            if (previousPlan?.ui_state && !Object.keys(outputPlan.ui_state || {}).length) {
                outputPlan.ui_state = clone(previousPlan.ui_state);
            }
            const serializedPlan = JSON.stringify(outputPlan, null, 2);
            const restoredPayload = { ...payload, plan_json: serializedPlan };
            payloadByNode.set(this, restoredPayload);
            const widget = planWidget(this);
            if (widget) widget.value = serializedPlan;
            updateNodeLabel(this, outputPlan);
            markGraphChanged(this);
            if (compileWaiter) {
                this._xiaoyueOpenAfterRun = false;
                compileWaiter.finish(null, outputPlan, restoredPayload);
                return;
            }
            const activeEditor = editorByNode.get(this);
            const refreshOpenEditor = Boolean(activeEditor?.dialog?.open);
            if (activeEditor) activeEditor.destroy();
            const shouldOpen = Boolean(this._xiaoyueOpenAfterRun || refreshOpenEditor);
            this._xiaoyueOpenAfterRun = false;
            if (shouldOpen) {
                queueMicrotask(() => new PlannerEditor(this, outputPlan, restoredPayload));
            }
        };
    },
});

export { PlannerEditor };
