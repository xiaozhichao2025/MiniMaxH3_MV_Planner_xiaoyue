from __future__ import annotations

import json
import math
import secrets
import shutil
import uuid
import wave
from pathlib import Path
from typing import Any

import folder_paths
import torch

from .asr_provider import (
    ASR_PROVIDER_TYPE,
    build_local_whisper_provider,
    public_provider_summary,
)
from .creative_outline import outline_skill_descriptor
from .skill_library import skill_template_folder, skill_template_library
from .planner_core import (
    FPS,
    H3_DEFAULT_MAX_FRAMES,
    H3_WINDOW_PRESETS,
    beat_frames,
    compile_plan,
    estimate_bpm,
    report_plan,
)
from .project_audio import audio_fingerprint as _audio_fingerprint
from .project_audio import load_audio_path, load_project_audio
from .project_store import (
    PROJECT_SCHEMA,
    PROJECT_TYPE,
    load_project,
    load_project_plan,
    record_plan,
    update_project_audio_role,
)
from .project_sync import reconcile_project_state
from .reasoning_provider import (
    REASONING_PROVIDER_TYPE,
    client_reasoning_provider_descriptor,
    public_reasoning_provider_summary,
    register_qwen_te_provider,
)


PLAN_TYPE = "XIAOYUE_MV_PLAN"


def _waveform_2d(audio: dict[str, Any]) -> tuple[torch.Tensor, int]:
    if not isinstance(audio, dict) or "waveform" not in audio or "sample_rate" not in audio:
        raise ValueError("MV Planner 需要标准 ComfyUI AUDIO 输入。")
    waveform = audio["waveform"]
    if not torch.is_tensor(waveform):
        raise TypeError("MV Planner 的 AUDIO waveform 必须是 Tensor。")
    if waveform.ndim == 3:
        waveform = waveform[0]
    elif waveform.ndim == 1:
        waveform = waveform.unsqueeze(0)
    if waveform.ndim != 2 or waveform.shape[-1] < 1:
        raise ValueError("MV Planner 需要非空的 [channels, samples] 音频。")
    sample_rate = int(audio["sample_rate"])
    if sample_rate < 1000:
        raise ValueError("MV Planner 收到无效采样率。")
    return waveform.detach().to(device="cpu", dtype=torch.float32), sample_rate


def _normalized_envelope(mono: torch.Tensor, sample_rate: int,
                         envelope_rate: int = 100) -> tuple[list[float], list[float], float]:
    hop = max(1, int(round(sample_rate / envelope_rate)))
    count = int(math.ceil(mono.shape[-1] / hop))
    padded = torch.nn.functional.pad(mono, (0, count * hop - mono.shape[-1]))
    blocks = padded.view(count, hop)
    rms = torch.sqrt(torch.mean(blocks * blocks, dim=1) + 1e-12)
    scale = torch.quantile(rms, 0.95).clamp_min(1e-6)
    energy = (rms / scale).clamp(0, 1.5)
    history = torch.nn.functional.avg_pool1d(
        torch.nn.functional.pad(energy.view(1, 1, -1), (4, 0), mode="replicate"),
        kernel_size=5,
        stride=1,
    ).view(-1)[:energy.shape[0]]
    onset = torch.relu(energy - history)
    onset_scale = torch.quantile(onset, 0.95).clamp_min(1e-6)
    onset = (onset / onset_scale).clamp(0, 2.0)
    actual_rate = sample_rate / float(hop)
    return energy.tolist(), onset.tolist(), actual_rate


def _frame_energy(envelope: list[float], envelope_rate: float,
                  total_frames: int) -> list[float]:
    if not envelope:
        return [0.0] * (total_frames + 1)
    result = []
    radius = max(1, int(round(envelope_rate * 0.04)))
    for frame in range(total_frames + 1):
        center = int(round(frame / FPS * envelope_rate))
        start = max(0, center - radius)
        end = min(len(envelope), center + radius + 1)
        result.append(round(sum(envelope[start:end]) / max(1, end - start), 5))
    peak = max(result, default=1.0)
    if peak > 1.0:
        result = [round(value / peak, 5) for value in result]
    return result


def _section_changes(energy: list[float]) -> list[int]:
    if len(energy) < FPS * 3:
        return []
    raw = []
    radius = FPS
    for frame in range(radius, len(energy) - radius, FPS // 2):
        before = sum(energy[frame - radius:frame]) / radius
        after = sum(energy[frame:frame + radius]) / radius
        change = abs(after - before)
        if change >= 0.2:
            raw.append((change, frame))
    raw.sort(reverse=True)
    selected = []
    for _score, frame in raw:
        if all(abs(frame - other) >= FPS * 2 for other in selected):
            selected.append(frame)
    return sorted(selected[:64])


def _waveform_preview(mono: torch.Tensor, points: int = 1800) -> list[float]:
    length = int(mono.shape[-1])
    bins = min(points, length)
    width = int(math.ceil(length / bins))
    padded = torch.nn.functional.pad(mono.abs(), (0, bins * width - length))
    peaks = padded.view(bins, width).amax(dim=1)
    scale = torch.quantile(peaks, 0.98).clamp_min(1e-6)
    peaks = (peaks / scale).clamp(0, 1)
    return [round(float(value), 4) for value in peaks]


def _aligned_waveform_preview(
        waveform: torch.Tensor,
        sample_rate: int,
        timeline_seconds: float,
        points: int,
) -> list[float]:
    """Build a preview aligned to the master timeline without stretching it."""
    mono = waveform.mean(dim=0)
    target_samples = max(1, int(round(float(timeline_seconds) * int(sample_rate))))
    if mono.shape[-1] < target_samples:
        mono = torch.nn.functional.pad(mono, (0, target_samples - int(mono.shape[-1])))
    elif mono.shape[-1] > target_samples:
        mono = mono[:target_samples]
    return _waveform_preview(mono, max(1, int(points)))


def _analyze(waveform: torch.Tensor, sample_rate: int,
             known_bpm: float) -> tuple[dict[str, Any], list[float]]:
    mono = waveform.mean(dim=0)
    total_samples = int(mono.shape[-1])
    total_frames = max(1, int(round(total_samples * FPS / sample_rate)))
    envelope, onset, envelope_rate = _normalized_envelope(mono, sample_rate)
    tempo = estimate_bpm(onset, envelope_rate, known_bpm)
    energy = _frame_energy(envelope, envelope_rate, total_frames)
    analysis = {
        "bpm": tempo["bpm"],
        "bpm_confidence": tempo["confidence"],
        "beat_frames": beat_frames(tempo, envelope_rate, total_frames),
        "frame_energy": energy,
        "section_change_frames": _section_changes(energy),
    }
    preview_points = min(12000, max(1800, total_frames + 1))
    return analysis, _waveform_preview(mono, preview_points)


def _write_audio_preview(waveform: torch.Tensor, sample_rate: int,
                         fingerprint: str,
                         project: dict[str, Any] | None = None,
                         role: str = "master") -> dict[str, str]:
    project_id = str((project or {}).get("project_id") or "").strip()
    if project_id:
        # These WAV files drive playback and optional ASR after the planner
        # has run. ComfyUI clears temp on restart, so project previews belong
        # in the persistent project input tree rather than temp/waveform.
        subfolder = f"xiaoyue_mv_projects/{project_id}/source/audio_previews"
        storage_type = "input"
        directory = Path(folder_paths.get_input_directory()) / subfolder
    else:
        subfolder = "xiaoyue_mv_planner"
        storage_type = "temp"
        directory = Path(folder_paths.get_temp_directory()) / subfolder
    directory.mkdir(parents=True, exist_ok=True)
    safe_role = "vocals" if role == "vocals" else "master"
    filename = f"{safe_role}_{fingerprint[:20]}.wav"
    path = directory / filename
    if not path.exists():
        channels = waveform[:2].clamp(-1, 1)
        pcm = (channels * 32767).round().to(torch.int16).transpose(0, 1).contiguous().numpy()
        with wave.open(str(path), "wb") as output:
            output.setnchannels(int(channels.shape[0]))
            output.setsampwidth(2)
            output.setframerate(sample_rate)
            output.writeframes(pcm.tobytes())
    return {
        "filename": filename,
        "subfolder": subfolder,
        "type": storage_type,
        "server_path": str(path),
        "role": safe_role,
    }


def _existing_plan(value: str) -> dict[str, Any] | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        plan = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"MV Planner 保存的计划 JSON 无法解析：{exc}") from exc
    if not isinstance(plan, dict):
        raise ValueError("MV Planner 的计划 JSON 必须是对象。")
    return plan


class Xiaoyue_Local_Whisper_Provider:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model_type": (["whisper-large-v3"], {
                    "default": "whisper-large-v3",
                    "tooltip": "复用 ComfyUI-Easy-Media 的本地 Whisper Large V3。",
                }),
                "max_sentence_length": ("INT", {
                    "default": 20, "min": 1, "max": 500, "step": 1,
                    "tooltip": "Easy Media 对识别结果进行智能分句时的最大字符数。",
                }),
                "unload_model": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "识别完成后把 ASR 模型移回 CPU 并清理加速器缓存。",
                }),
            },
        }

    RETURN_TYPES = (ASR_PROVIDER_TYPE,)
    RETURN_NAMES = ("asr_provider",)
    FUNCTION = "provide"
    CATEGORY = "Xiaoyue/Audio/ASR Providers"
    DESCRIPTION = (
        "只提供本地 Whisper 配置，不接 AUDIO、不加载模型。"
        "真正识别由 MV Planner 工作台通过 ComfyUI-Easy-Media 按需执行。"
    )

    def provide(self, model_type, max_sentence_length, unload_model):
        return (build_local_whisper_provider(
            model_type, max_sentence_length, unload_model,
        ),)


class Xiaoyue_QwenTE_MV_Reasoning_Provider_xiaoyue:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "qwen_model": ("QWENLLAMA", {
                    "tooltip": (
                        "连接 Qwen llama TE 模型加载器。要让整片纲领和单镜头 H3"
                        "提示词真实读取资产画面，必须选中与主模型匹配的 mmproj。"
                    ),
                }),
                "max_tokens": ("INT", {
                    "default": 8192, "min": 512, "max": 32768, "step": 256,
                    "tooltip": "整片镜头较多或 H3 提示词较长时需要足够 token。",
                }),
                "temperature": ("FLOAT", {
                    "default": 0.35, "min": 0.0, "max": 2.0, "step": 0.05,
                }),
                "top_p": ("FLOAT", {
                    "default": 0.85, "min": 0.0, "max": 1.0, "step": 0.05,
                }),
                "top_k": ("INT", {
                    "default": 20, "min": 0, "max": 200, "step": 1,
                }),
                "repeat_penalty": ("FLOAT", {
                    "default": 1.05, "min": 0.5, "max": 2.0, "step": 0.01,
                }),
                "seed": ("INT", {
                    "default": 0, "min": 0, "max": 0xffffffffffffffff, "step": 1,
            "tooltip": "固定参数和固定种子用于获得可复现的纲领与 H3 提示词。",
                }),
                "unload_after_generate": ("BOOLEAN", {
                    "default": False,
                    "tooltip": (
                        "开启后每次纲领推理完成会卸载 QwenTE 模型；下一次可按加载器设置自动重载。"
                    ),
                }),
            },
        }

    RETURN_TYPES = (REASONING_PROVIDER_TYPE,)
    RETURN_NAMES = ("reasoning_provider",)
    FUNCTION = "provide"
    CATEGORY = "Xiaoyue/MV/Reasoning Providers"
    DESCRIPTION = (
        "把已加载的 QwenTE 本地模型作为 MV 工作台的两阶段推理能力："
        "先生成整片视觉镜头纲领，再生成单镜头或全片 H3 提示词。加载 mmproj"
        "后可真实读取资产画面；节点排队时只注册模型，实际推理由工作台按钮触发。"
    )

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        # The browser-side action needs a live in-memory provider token after
        # every ComfyUI restart and workflow refresh.
        return float("NaN")

    def provide(self, qwen_model, max_tokens, temperature, top_p, top_k,
                repeat_penalty, seed, unload_after_generate):
        provider = register_qwen_te_provider(
            qwen_model,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            repeat_penalty=repeat_penalty,
            seed=seed,
            unload_after_generate=unload_after_generate,
        )
        return (provider,)


def _load_project_owned_audio(project: dict[str, Any]) -> tuple[
        dict[str, Any], dict[str, Any], dict[str, Any] | None, list[str]]:
    """Load project-owned sources and migrate legacy persistent previews once."""
    project_id = str(project.get("project_id") or "")
    migration_notes: list[str] = []

    def load_owned(role: str):
        nonlocal project
        audio = load_project_audio(project, role)
        manifest_audio = project["manifest"].get("audio")
        manifest_audio = manifest_audio if isinstance(manifest_audio, dict) else {}
        item = manifest_audio.get(role) if isinstance(manifest_audio.get(role), dict) else {}
        if audio is None and item.get("fingerprint"):
            fingerprint = str(item.get("fingerprint") or "")
            preview = (
                Path(project["paths"]["input"])
                / "source" / "audio_previews"
                / f"{role}_{fingerprint[:20]}.wav"
            )
            if preview.is_file():
                directory = Path(project["paths"]["input"]) / "source" / "audio"
                directory.mkdir(parents=True, exist_ok=True)
                target = directory / f"{role}_{fingerprint[:16]}_recovered.wav"
                if not target.exists():
                    shutil.copy2(preview, target)
                metadata = {
                    **item,
                    "source": {
                        "path": target.relative_to(
                            Path(project["paths"]["input"])).as_posix(),
                        "format": "media",
                        "original_name": preview.name,
                    },
                }
                update_project_audio_role(project_id, role, metadata)
                project = load_project(project_id)
                audio = load_project_audio(project, role)
                migration_notes.append("母带" if role == "master" else "干声")
        if audio is not None:
            stored = project["manifest"].get("audio", {}).get(role, {})
            audio["xiaoyue_fingerprint"] = str(
                stored.get("fingerprint")
                or _audio_fingerprint(*_waveform_2d(audio)))
        return audio

    master_audio = load_owned("master")
    if master_audio is None:
        raise ValueError("当前项目没有母带文件。请在项目面板中导入母带。")
    vocals_audio = load_owned("vocals")
    return project, master_audio, vocals_audio, migration_notes


SHOT_OUTPUT_TYPES = ("STRING", "AUDIO", "COMBO", "FLOAT", "FLOAT", "FLOAT",
                     "INT", "INT", "STRING", "STRING", "INT", "INT",
                     "STRING", "INT", "INT", "STRING", "STRING", "STRING",
                     "STRING")
SHOT_OUTPUT_NAMES = ("h3_prompt", "shot_audio", "aspect_ratio",
                     "first_pass_megapixels", "second_pass_megapixels",
                     "raw_seconds", "raw_frames", "seed", "output_prefix",
                     "reference_manifest_json", "deliver_from_raw_frame",
                     "deliver_frames", "continuation_video_path",
                     "continuation_frame_count", "continuation_start_frame",
                     "shot_audio_input_name", "input_subfolder",
                     "output_subfolder", "output_filename_stem")
SHOT_OUTPUT_TOOLTIPS = (
    "当前镜头已保存的 H3 提示词；一采直接接入，二采可在用户工作流里叠加自己的增强词。",
    "按本镜生成窗口切出的原曲音频，含规划的前后补齐；接音频驱动或附件槽位。",
    "项目画幅比例；可接用户自己选用的分辨率节点。",
    "一采目标百万像素量；单采工作流只需连接所用的像素参数。",
    "二采目标百万像素量；仅在用户采用二采工作流时连接。",
    "生成窗口时长，单位秒；精确帧数请优先使用 raw_frames。",
    "本镜精确生成帧数；H3 的 length 应直接接此口，勿再由秒数换算。",
    "本次生成使用的随机种子；应接用户工作流的噪声节点。",
    "相对 ComfyUI output 根目录的目录加文件名前缀；接 SaveVideo.filename_prefix 等单口保存节点。",
    "当前镜头附件的有序清单；接‘本镜附件槽位’节点，不是直接给提示词模型阅读。",
    "交付段在完整生成文件中的起始帧；供最终预览与剪辑定位，生成时不要提前裁切。",
    "本镜最终交付帧数；与交付起始帧一起记录剪辑区间。",
    "续写时选定的前镜版本视频路径；接用户的视频读取节点。独立镜头为空。",
    "续写前置引导需要读取的帧数，取用户所选值；不要固定写成 22。",
    "前镜原始视频中引导帧的起始位置；已避开不交付的尾部出血区。",
    "本镜切窗音频相对 ComfyUI input 根目录的完整路径；由工作台镜头页生成时填写。",
    "切窗音频所在的 input 相对子目录；由工作台镜头页生成时填写。",
    "本次视频相对 ComfyUI output 根目录的目标子目录；可接自定义保存节点的目录口。",
    "本次视频的文件名前缀，不含目录和扩展名；可接自定义保存节点的文件名口。",
)


class MiniMaxH3_MV_Planner_xiaoyue:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project_name": ("STRING", {
                    "default": "",
                    "tooltip": "由统一 MV 工作台中的项目管理界面维护。",
                }),
                "project_code": ("STRING", {
                    "default": "",
                    "tooltip": "项目生成文件名的稳定编号。",
                }),
                "project_id": ("STRING", {
                    "default": "",
                    "tooltip": "项目稳定 UUID；前端隐藏并自动管理。",
                }),
                "project_state_json": ("STRING", {
                    "default": "", "multiline": True, "dynamicPrompts": False,
                    "tooltip": "统一工作台维护的项目内部状态。",
                }),
                "plan_json": ("STRING", {
                    "default": "", "multiline": True, "dynamicPrompts": False,
                    "tooltip": "由可视化编辑器维护的内部计划。前端会自动隐藏。",
                }),
                "seed": ("INT", {"default": -1, "min": -1,
                                "max": 9223372036854775807,
                                "tooltip": "镜头生成种子：-1 每次随机，非负值固定。"}),
            },
            "optional": {
                "asr_provider": (ASR_PROVIDER_TYPE, {
                    "tooltip": (
                        "连接本地 Whisper Provider。"
                        "Provider 不接音频，工作台按项目资料自动选择干声或母带。"
                    ),
                }),
                "reasoning_provider": (REASONING_PROVIDER_TYPE, {
                    "tooltip": (
                        "连接 QwenTE MV 推理 Provider。工作台中的‘生成整片镜头纲领’"
                        "只在点击时调用，不会因为普通规划排队而自动生成。"
                    ),
                }),
            },
        }

    RETURN_TYPES = (
        PLAN_TYPE, "STRING", "STRING", PROJECT_TYPE,
        "AUDIO", "AUDIO", "STRING", "STRING",
    ) + SHOT_OUTPUT_TYPES
    RETURN_NAMES = (
        "mv_plan", "plan_json", "report", "project",
        "master_audio", "vocals_audio", "lyrics_text", "project_json",
    ) + SHOT_OUTPUT_NAMES
    OUTPUT_TOOLTIPS = (
        "供后续 xiaoyue H3 生成节点直接消费的结构化规划。",
        "可存档、检查或传递给其他工作流的完整 JSON。",
        "镜头数、窗口数、BPM 和连续性风险摘要。",
        "当前项目句柄，供后续生成和归档节点使用。",
        "项目内部管理的完整母带。",
        "项目内部管理的可选零点对齐干声。",
        "项目内部管理的歌词／字幕源文本。",
        "当前 project.json 快照。",
    ) + SHOT_OUTPUT_TOOLTIPS
    FUNCTION = "plan"
    CATEGORY = "Xiaoyue/MiniMax H3"
    DESCRIPTION = (
        "统一管理项目、母带、干声、歌词、资产、MV 时间轴、单镜生成与最终验收。"
        "本镜输出口连接用户自己的生成工作流；附件路由保持独立。"
    )
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        # The visual editor receives its plan through ComfyUI's UI payload.
        # Cached executions do not replay that payload after a workflow is
        # reopened, leaving the editor empty even though the backend has a
        # valid cached result.  Always execute this lightweight planning node
        # so the current browser receives the matching plan and waveform.
        return float("NaN")

    def plan(self, project_name, project_code, project_id, project_state_json,
             plan_json, seed=-1,
             asr_provider=None, reasoning_provider=None):
        if not str(project_id or "").strip():
            raise ValueError("请先在统一 MV 工作台中创建或打开项目。")
        project = load_project(str(project_id or ""))
        project, audio, vocals_audio, migration_notes = _load_project_owned_audio(
            project)
        waveform, sample_rate = _waveform_2d(audio)
        fingerprint = str(
            audio.get("xiaoyue_fingerprint")
            or _audio_fingerprint(waveform, sample_rate)
        )
        analysis, preview = _analyze(waveform, sample_rate, 0.0)
        existing = _existing_plan(plan_json)
        existing = existing if isinstance(existing, dict) else {}
        existing_project = (
            existing.get("project") if isinstance(existing.get("project"), dict) else {}
        )
        restore_project_plan = bool(
            not existing.get("shots")
            or str(existing_project.get("id") or "") != project["project_id"]
        )
        if restore_project_plan:
            stored_plan = load_project_plan(project["project_id"])
            if stored_plan is not None:
                # Historical project loading is a full restore.  Never combine
                # its timeline with the template/current workflow's plan.
                existing = stored_plan
        manifest = project["manifest"]
        reconciliation = reconcile_project_state(
            manifest,
            existing,
            fingerprint,
            subtitle_override=None,
        )
        existing = reconciliation["existing"]
        components = reconciliation["components"]
        brief = components["project_brief"]
        existing["project"] = {
            "schema": PROJECT_SCHEMA,
            "id": project["project_id"],
            "code": project["project_code"],
            "name": str(brief.get("project_name") or project["project_name"]),
            "description": str(brief.get("description", "")),
            "overall_prompt": str(brief.get("overall_prompt", "")),
            "negative_prompt": str(brief.get("negative_prompt", "")),
        }
        existing["project_storage"] = manifest.get("storage", {})
        existing["assets"] = components["assets"]
        existing["generation_spec"] = components["generation_spec"]
        existing["skill_templates"] = components["skill_templates"]
        subtitle_source = str(components["lyrics"].get("source_text", ""))
        request_replan = bool(existing and existing.get("request_replan"))
        requested_replan_settings = (
            existing.get("settings")
            if request_replan and isinstance(existing.get("settings"), dict)
            else None
        )
        planning_settings = (
            requested_replan_settings
            if requested_replan_settings is not None
            else manifest.get("planning_settings")
            if isinstance(manifest.get("planning_settings"), dict)
            else {}
        )
        h3_max_frames = int(planning_settings.get("h3_max_frames") or H3_DEFAULT_MAX_FRAMES)
        if h3_max_frames not in H3_WINDOW_PRESETS:
            h3_max_frames = H3_DEFAULT_MAX_FRAMES
        target_shot_seconds = max(1.0, min(
            h3_max_frames / FPS,
            float(planning_settings.get("target_shot_seconds") or 9.0),
        ))
        settings = {
            "target_shot_seconds": target_shot_seconds,
            "minimum_shot_seconds": 3.0,
            "maximum_shot_seconds": h3_max_frames / FPS,
            "h3_max_frames": h3_max_frames,
            "default_transition": "hard_cut",
            "continuity_frames": 22,
            "crossfade_frames": 12,
            "safety_frames": 5,
            "hidden_continuity_frames": 22,
            "audio_handle_frames": 22,
        }
        compiled = compile_plan(
            audio_fingerprint=fingerprint,
            sample_rate=sample_rate,
            total_samples=int(waveform.shape[-1]),
            analysis=analysis,
            settings=settings,
            subtitle_source=subtitle_source,
            existing=existing,
            request_replan=request_replan,
        )
        compiled["project_storage"] = manifest.get("storage", {})
        compiled["project_sources"] = reconciliation["project_sources"]
        compiled["project_sync"] = reconciliation["project_sync"]
        sync_notices = compiled["project_sync"].setdefault("notices", [])
        if migration_notes:
            sync_notices.append(
                f"已把旧项目的{'、'.join(migration_notes)}安全归档为项目内音源；原预览文件仍保留。"
            )
        if restore_project_plan and existing.get("shots"):
            sync_notices.insert(
                0,
                "已从项目目录完整恢复历史规划；当前工作流模板中的空计划或其他项目计划未参与合并。",
            )
        if isinstance(compiled.get("pending_replan"), dict):
            sync_notices.append(str(compiled["pending_replan"].get("message") or
                                    "旧打点仍然保留；确认后才会重新自动找点。"))
        elif request_replan:
            sync_notices.append(
                "已按用户确认重建全部镜头：旧切点、镜头连接、镜头资产绑定、镜头提示词和镜头字幕归属均已重置；字幕母版绝对时间轴、项目基础资料与总资产库保留。"
            )
        manifest_audio = manifest.get("audio") if isinstance(manifest.get("audio"), dict) else {}
        compiled["audio_sources"] = {
            "master": manifest_audio.get("master", {}),
            "vocals": manifest_audio.get("vocals", {}),
            "alignment": manifest_audio.get("alignment", {}),
        }
        compiled["asr_provider"] = public_provider_summary(asr_provider)
        compiled["reasoning_provider"] = public_reasoning_provider_summary(
            reasoning_provider)
        report = report_plan(compiled)
        sync_notices = compiled["project_sync"].get("notices", [])
        if sync_notices:
            report += "\n项目同步：\n- " + "\n- ".join(sync_notices)
        project = record_plan(project, compiled)
        serialized = json.dumps(compiled, ensure_ascii=False, indent=2)
        project_serialized = json.dumps(
            project["manifest"], ensure_ascii=False, indent=2)
        master_preview = _write_audio_preview(
            waveform, sample_rate, fingerprint, project, "master")
        audio_previews = {"master": master_preview}
        waveforms = {"master": preview}
        if vocals_audio is not None:
            vocals_waveform, vocals_rate = _waveform_2d(vocals_audio)
            vocals_fingerprint = str(
                vocals_audio.get("xiaoyue_fingerprint")
                or _audio_fingerprint(vocals_waveform, vocals_rate)
            )
            audio_previews["vocals"] = _write_audio_preview(
                vocals_waveform, vocals_rate, vocals_fingerprint, project, "vocals")
            waveforms["vocals"] = _aligned_waveform_preview(
                vocals_waveform,
                vocals_rate,
                compiled["audio"]["duration_seconds"],
                len(preview),
            )
        payload = {
            "plan_json": serialized,
            "report": report,
            "waveform": preview,
            "waveforms": waveforms,
            # audio_preview remains as a compatibility alias for older clients.
            "audio_preview": master_preview,
            "audio_previews": audio_previews,
            # The short-lived provider token is intentionally UI-only. It is
            # never written into plan_json or project storage.
            "reasoning_provider": client_reasoning_provider_descriptor(
                reasoning_provider),
            "outline_skill": outline_skill_descriptor(
                str((compiled.get("skill_templates") or {}).get(
                    "visual_outline") or "xiaoyue-mv-visual-outline")
            ),
            "skill_template_library": skill_template_library(),
            "skill_template_folder": skill_template_folder(),
            "project": {
                "project_id": project["project_id"],
                "project_code": project["project_code"],
                "project_name": project["project_name"],
                "revision": project["revision"],
            },
            "project_json": project_serialized,
        }
        return {
            "ui": {"xiaoyue_mv_planner": [payload], "text": [report]},
            "result": (
                compiled, serialized, report, project,
                audio, vocals_audio, subtitle_source, project_serialized,
            ) + (None,) * len(SHOT_OUTPUT_NAMES),
        }


class Xiaoyue_MV_PreviewDesk:
    """Legacy canvas entry; the unified workbench now owns the preview UI."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "mv_plan": (PLAN_TYPE, {
                "tooltip": "从 MV 项目工作台的 mv_plan 输出接入；剪辑台读取同一工程的镜头版本和原曲。",
            }),
        }}

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("project_id",)
    FUNCTION = "prepare"
    CATEGORY = "Xiaoyue/MiniMax H3/旧版兼容"
    OUTPUT_NODE = True
    DESCRIPTION = "旧工作流兼容节点；新版从统一 MV 工作台直接打开最终验收剪辑台。"

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("NaN")

    def prepare(self, mv_plan):
        if not isinstance(mv_plan, dict):
            raise ValueError("请把 MV 项目工作台的 mv_plan 输出连到剪辑台。")
        project_id = str((mv_plan.get("project") or {}).get("id") or "")
        if not project_id:
            raise ValueError("接入的 MV 计划缺少工程 ID。")
        return {
            "ui": {"xiaoyue_mv_preview_desk": [{"project_id": project_id}]},
            "result": (project_id,),
        }


class Xiaoyue_MV_ShotInput:
    """Legacy canvas adapter; new templates expose shot ports on the workbench."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "mv_plan": (PLAN_TYPE, {"tooltip": "接 MV 项目工作台的 mv_plan；提供镜头计划、提示词与附件绑定。"}),
                "project": (PROJECT_TYPE, {"tooltip": "接同一个 MV 项目工作台的 project；用于核对项目身份与输出命名。"}),
                "master_audio": ("AUDIO", {"tooltip": "接项目母带；本节点按当前镜头窗口截取音频，供生成时驱动角色。"}),
                "seed": ("INT", {"default": -1, "min": -1,
                                  "max": 9223372036854775807,
                                  "tooltip": "-1 为每次生成随机种子；非负值固定，便于复现。"}),
            },
        }

    RETURN_TYPES = SHOT_OUTPUT_TYPES
    RETURN_NAMES = SHOT_OUTPUT_NAMES
    OUTPUT_TOOLTIPS = SHOT_OUTPUT_TOOLTIPS
    FUNCTION = "select"
    CATEGORY = "Xiaoyue/MiniMax H3/旧版兼容"
    DESCRIPTION = (
        "旧工作流兼容节点。新版工作台直接提供本镜输出口；"
        "镜头 ID 仍由工作台镜头页的生成流程确定，不在画布手填。"
    )

    def select(self, mv_plan, project, master_audio, seed=-1, shot_id=None):
        shot_id = str(shot_id or "").strip()
        if not shot_id:
            raise ValueError("请在 MV 工作台的镜头页选择镜头并点击生成；本镜输入不接受手填 shot_id。")
        project_id = str((mv_plan.get("project") or {}).get("id") or "")
        if project_id != str(project.get("project_id") or ""):
            raise ValueError("镜头规划与项目句柄不匹配。")
        shot = next((item for item in mv_plan.get("shots", [])
                     if item.get("id") == shot_id), None)
        window = next((item for item in mv_plan.get("generation_windows", [])
                       if item.get("shot_id") == shot_id), None)
        if shot is None or window is None:
            raise ValueError(f"规划里没有镜头 {shot_id} 的单镜窗口。")
        if int(window.get("context_frames") or 0):
            raise ValueError("续写镜头请在工作台镜头页选定前镜版本后生成，不能直接排队画布。")
        if shot.get("audio_references"):
            raise ValueError("当前镜头的用户音频引用尚未接线，不能错位占用原曲槽位。")
        prompt = str(shot.get("prompt") or "").strip()
        if not prompt:
            raise ValueError(f"镜头 {shot_id} 缺少 H3 提示词。")
        waveform = master_audio["waveform"]
        if waveform.ndim != 3:
            raise ValueError("母带必须是 [batch, channels, samples] AUDIO。")
        first = int(window["source_start_sample"])
        last = int(window["source_end_sample"])
        before = int(window["pad_before_samples"])
        after = int(window["pad_after_samples"])
        requested = int(window["requested_samples"])
        if (first < 0 or last < first or last > waveform.shape[-1]
                or before < 0 or after < 0 or before + last - first + after != requested):
            raise ValueError("镜头音频窗口样本坐标无效。")
        segment = waveform[..., first:last]
        if before or after:
            segment = torch.nn.functional.pad(segment, (before, after))
        visual_assets = {str(item.get("id") or ""): item
                         for item in mv_plan.get("assets", [])}
        input_root = Path(folder_paths.get_input_directory()).resolve()
        references = []
        image_number = video_number = 0
        for binding in sorted(shot.get("asset_bindings") or [],
                              key=lambda item: int(item.get("order") or 0)):
            asset = visual_assets.get(str(binding.get("asset_id") or ""))
            if not asset:
                raise ValueError("镜头绑定了已移除的视觉资产。")
            source = asset.get("source") or {}
            filename = str(source.get("filename") or "")
            subfolder = str(source.get("subfolder") or "").replace("\\", "/")
            relative = Path(subfolder) / filename
            if (not filename or Path(filename).name != filename
                    or relative.is_absolute() or ".." in relative.parts):
                raise ValueError("镜头资产路径无效。")
            absolute = (input_root / relative).resolve()
            absolute.relative_to(input_root)
            if not absolute.is_file():
                raise ValueError(f"镜头资产文件不存在：{filename}")
            if source.get("media_type") == "video":
                slot = f"ref_videos.ref_video_{video_number}"
                video_number += 1
            else:
                slot = f"ref_images.ref_image_{image_number}"
                image_number += 1
            references.append({"asset_id": asset["id"], "slot": slot,
                               "order": int(binding.get("order") or 0),
                               "media_type": source.get("media_type"),
                               "input_name": relative.as_posix(),
                               "absolute_path": str(absolute)})
        specification = mv_plan.get("generation_spec") or {}
        actual_seed = secrets.randbits(63) if int(seed) < 0 else int(seed)
        prefix = (f"xiaoyue_mv_projects/{project_id}/generations/"
                  f"{project['project_code']}_{window['scene_code']}_manual_"
                  f"{uuid.uuid4().hex[:8]}")
        audio = {"waveform": segment,
                 "sample_rate": int(master_audio["sample_rate"])}
        return (
            prompt, audio, str(specification["aspect_ratio"]),
            float(specification["first_pass_megapixels"]),
            float(specification["second_pass_megapixels"]),
            int(window["raw_frames"]) / FPS, int(window["raw_frames"]),
            actual_seed, prefix,
            json.dumps({"visual_references": references,
                        "original_song_audio_slot": 1}, ensure_ascii=False),
            int(window["deliver_from_raw_frame"]), int(window["deliver_frames"]),
            "", 0, 0, "", "", prefix.rsplit("/", 1)[0],
            prefix.rsplit("/", 1)[-1],
        )


class Xiaoyue_MV_RuntimeShot:
    """Queue-time values replacing the planner dependency without changing ports."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "h3_prompt": ("STRING", {"default": "", "multiline": True}),
            "shot_audio": ("AUDIO",),
            "aspect_ratio": ("STRING", {"default": "16:9 (Widescreen)"}),
            "first_pass_megapixels": ("FLOAT", {"default": 0.4}),
            "second_pass_megapixels": ("FLOAT", {"default": 0.9}),
            "raw_seconds": ("FLOAT", {"default": 5.0}),
            "raw_frames": ("INT", {"default": 124}),
            "seed": ("INT", {"default": 0, "min": 0,
                            "max": 9223372036854775807}),
            "output_prefix": ("STRING", {"default": "xiaoyue_mv/shot"}),
            "reference_manifest_json": ("STRING", {"default": "{}"}),
            "deliver_from_raw_frame": ("INT", {"default": 0}),
            "deliver_frames": ("INT", {"default": 124}),
            "continuation_video_path": ("STRING", {"default": ""}),
            "continuation_frame_count": ("INT", {"default": 0}),
            "continuation_start_frame": ("INT", {"default": 0}),
            "shot_audio_input_name": ("STRING", {"default": ""}),
            "input_subfolder": ("STRING", {"default": ""}),
            "output_subfolder": ("STRING", {"default": ""}),
            "output_filename_stem": ("STRING", {"default": "shot"}),
        }}

    RETURN_TYPES = Xiaoyue_MV_ShotInput.RETURN_TYPES
    RETURN_NAMES = Xiaoyue_MV_ShotInput.RETURN_NAMES
    OUTPUT_TOOLTIPS = Xiaoyue_MV_ShotInput.OUTPUT_TOOLTIPS
    FUNCTION = "values"
    CATEGORY = "Xiaoyue/MiniMax H3"
    DESCRIPTION = "镜头页生成时注入的独立镜头值；保持与可见本镜输入的输出口完全一致。"

    def values(self, **kwargs):
        return tuple(kwargs[name] for name in self.RETURN_NAMES)


class Xiaoyue_MV_ReferenceSlots:
    """Typed, shot-local attachment ports reusable by both H3 passes."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "reference_manifest_json": ("STRING", {"default": "{}", "tooltip": "接本镜输入的同名附件清单；附件顺序只对当前镜头有效。"}),
            "shot_audio": ("AUDIO", {"tooltip": "接本镜输入的 shot_audio；原曲目前占音频第 1 槽。"}),
        }}

    RETURN_TYPES = ("IMAGE",) * 12 + ("AUDIO",) * 3
    RETURN_NAMES = (tuple(f"image_{index}" for index in range(1, 10))
                    + tuple(f"video_{index}" for index in range(1, 4))
                    + tuple(f"audio_{index}" for index in range(1, 4)))
    OUTPUT_TOOLTIPS = (
        tuple(f"当前镜头第 {index} 张参考图；未绑定时为空，可接所用生成节点的对应图像槽。"
              for index in range(1, 10))
        + tuple(f"当前镜头第 {index} 段参考视频的帧；未绑定时为空。"
                for index in range(1, 4))
        + tuple(("当前镜头切出的原曲音频；供人物演唱与口型驱动。" if index == 1
                 else f"当前镜头第 {index} 个音频槽；目前没有额外用户音频绑定。")
                for index in range(1, 4))
    )
    FUNCTION = "load"
    CATEGORY = "Xiaoyue/MiniMax H3"
    DESCRIPTION = (
        "按当前镜头绑定顺序输出最多 9 图、3 视频和 3 独立音频槽。"
        "未绑定槽位返回空值，H3 会跳过；同一输出可同时接一采和二采。"
    )

    def load(self, reference_manifest_json, shot_audio):
        try:
            manifest = json.loads(reference_manifest_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("本镜附件槽位清单不是有效 JSON。") from exc
        if not isinstance(manifest, dict):
            raise ValueError("本镜附件槽位清单格式无效。")
        result: list[Any] = [None] * 15
        input_root = Path(folder_paths.get_input_directory()).resolve()
        seen = set()
        for asset in manifest.get("visual_references") or []:
            if not isinstance(asset, dict):
                raise ValueError("镜头附件条目格式无效。")
            slot = str(asset.get("slot") or "")
            if slot in seen:
                raise ValueError("镜头附件槽位重复。")
            seen.add(slot)
            if slot.startswith("ref_images.ref_image_"):
                try:
                    index = int(slot.rsplit("_", 1)[1])
                except ValueError:
                    raise ValueError("镜头图片槽位编号无效。") from None
                if not 0 <= index < 9:
                    raise ValueError("镜头图片槽位超过 9 个。")
                output_index = index
                loader_name = "LoadImage"
            elif slot.startswith("ref_videos.ref_video_"):
                try:
                    index = int(slot.rsplit("_", 1)[1])
                except ValueError:
                    raise ValueError("镜头视频槽位编号无效。") from None
                if not 0 <= index < 3:
                    raise ValueError("镜头视频槽位超过 3 个。")
                output_index = 9 + index
                loader_name = "VHS_LoadVideoPath"
            else:
                raise ValueError("镜头附件槽位类型不受支持。")
            relative = Path(str(asset.get("input_name") or ""))
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("镜头附件路径越界。")
            absolute = (input_root / relative).resolve()
            absolute.relative_to(input_root)
            if not absolute.is_file():
                raise ValueError(f"镜头附件文件不存在：{relative.name}")
            import nodes as comfy_nodes

            loader_type = comfy_nodes.NODE_CLASS_MAPPINGS.get(loader_name)
            if loader_type is None:
                raise ValueError(f"缺少镜头附件加载节点：{loader_name}")
            if loader_name == "LoadImage":
                result[output_index] = loader_type().load_image(relative.as_posix())[0]
            else:
                result[output_index] = loader_type().load_video(
                    video=str(absolute), force_rate=24.0,
                    custom_width=0, custom_height=0,
                    frame_load_cap=0, skip_first_frames=0,
                    select_every_nth=1)[0]
        song_slot = int(manifest.get("original_song_audio_slot") or 0)
        if song_slot != 1:
            raise ValueError("当前只支持原曲独立音频第 1 槽；用户音频尚未接线。")
        result[12] = shot_audio
        return tuple(result)


NODE_CLASS_MAPPINGS = {
    "MiniMaxH3_MV_Planner_xiaoyue": MiniMaxH3_MV_Planner_xiaoyue,
    "Xiaoyue_MV_PreviewDesk": Xiaoyue_MV_PreviewDesk,
    "Xiaoyue_MV_ShotInput": Xiaoyue_MV_ShotInput,
    "Xiaoyue_MV_RuntimeShot": Xiaoyue_MV_RuntimeShot,
    "Xiaoyue_MV_ReferenceSlots": Xiaoyue_MV_ReferenceSlots,
    "Xiaoyue_Local_Whisper_Provider": Xiaoyue_Local_Whisper_Provider,
    "Xiaoyue_QwenTE_MV_Reasoning_Provider_xiaoyue": Xiaoyue_QwenTE_MV_Reasoning_Provider_xiaoyue,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MiniMaxH3_MV_Planner_xiaoyue": "MiniMax H3 · MV 项目工作台 (xiaoyue)",
    "Xiaoyue_MV_PreviewDesk": "MV 最终验收剪辑台 (xiaoyue)",
    "Xiaoyue_MV_ShotInput": "MV 本镜输入｜规划与原曲 (xiaoyue)",
    "Xiaoyue_MV_RuntimeShot": "MV 本镜运行值 (xiaoyue)",
    "Xiaoyue_MV_ReferenceSlots": "MV 本镜附件槽位 (xiaoyue)",
    "Xiaoyue_Local_Whisper_Provider": "本地 Whisper Provider (xiaoyue)",
    "Xiaoyue_QwenTE_MV_Reasoning_Provider_xiaoyue": "QwenTE · MV 两阶段推理 Provider (xiaoyue)",
}
