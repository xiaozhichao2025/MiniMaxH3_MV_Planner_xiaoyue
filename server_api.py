from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import aiohttp
import folder_paths
from aiohttp import web
from server import PromptServer

from .creative_outline import build_outline_prompts, parse_outline_response
from .h3_prompt import (
    H3PromptLanguageError,
    build_h3_prompt_prompts,
    build_h3_retry_user_prompt,
    continuation_predecessor,
    extract_h3_prompt_response,
    parse_h3_prompt_response,
)
from .project_audio import describe_audio, source_extension
from .planner_core import CONTINUATION_WINDOW_RULE_REVISION
from .project_store import list_projects as store_list_projects
from .project_store import load_project, load_project_plan, update_project_audio_role, upsert_project
from .generation_store import (
    begin_version, completed_batch_parent_selections, generation_job_conflict, load_versions,
    recover_interrupted_versions, resolve_parent_version,
    select_version, select_versions, update_version, with_stale_flags,
)
from .editorial_export import build_cut_list, cut_list_csv
from .delivery_export import (
    EXPORT_KINDS, ExportCancelled, build_export_snapshot, run_export,
    srt_to_webvtt,
)
from .preview_desk import (
    build_preview_manifest, final_subtitles, selection_chain,
    subtitle_csv, subtitle_srt,
)
from .editor_draft_store import (
    load_editor_state, save_editor_plan, save_shot_patch, save_timeline_checkpoint,
)
from .reasoning_provider import (
    client_reasoning_provider_descriptor, run_qwen_te_provider,
)
from .skill_library import (
    selected_skill_descriptor, skill_template_folder, skill_template_library,
)
from .visual_assets import load_outline_visual_inputs, load_shot_visual_inputs
from .shot_workflow import (
    canvas_backbone, canvas_workflow_fingerprint, compose_shot_workflow,
    delivery_tail_start, history_video_descriptor, prepare_window_audio, window_fingerprint,
    shot_seed_setting,
)


ASSET_IMAGE_SUFFIXES = {
    ".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp",
}
ASSET_VIDEO_SUFFIXES = {".avi", ".mkv", ".mov", ".mp4", ".webm"}
ASSET_SUFFIXES = ASSET_IMAGE_SUFFIXES | ASSET_VIDEO_SUFFIXES
ASSET_UPLOAD_LIMIT = 512 * 1024 * 1024
AUDIO_UPLOAD_LIMIT = 2 * 1024 * 1024 * 1024
OUTLINE_JOB_TTL_SECONDS = 60 * 60
_OUTLINE_JOBS: dict[str, dict[str, Any]] = {}
_OUTLINE_TASKS: dict[str, asyncio.Task[Any]] = {}
H3_PROMPT_JOB_TTL_SECONDS = 60 * 60
_H3_PROMPT_JOBS: dict[str, dict[str, Any]] = {}
_H3_PROMPT_TASKS: dict[str, asyncio.Task[Any]] = {}
_H3_PROMPT_RUN_LOCK = asyncio.Lock()
SHOT_GENERATION_JOB_TTL_SECONDS = 60 * 60 * 6
_SHOT_GENERATION_JOBS: dict[str, dict[str, Any]] = {}
_SHOT_GENERATION_TASKS: dict[str, asyncio.Task[Any]] = {}
_SHOT_GENERATION_START_LOCK = asyncio.Lock()
_DELIVERY_EXPORT_JOBS: dict[str, dict[str, Any]] = {}
_DELIVERY_EXPORT_TASKS: dict[str, asyncio.Task[Any]] = {}
_DELIVERY_EXPORT_CANCEL: dict[str, threading.Event] = {}


@PromptServer.instance.routes.get("/xiaoyue-mv/skill-templates")
async def list_mv_skill_templates(request: web.Request) -> web.Response:
    return web.json_response({
        "templates": skill_template_library(),
        "folder_path": skill_template_folder(),
    })


@PromptServer.instance.routes.get("/xiaoyue-mv/projects")
async def list_mv_projects(request: web.Request) -> web.Response:
    try:
        limit = int(request.query.get("limit", "100"))
        projects = await asyncio.to_thread(store_list_projects, None, limit)
        return web.json_response({"projects": projects, "count": len(projects)})
    except (OSError, TypeError, ValueError) as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except Exception:
        logging.exception("Xiaoyue project listing failed")
        return web.json_response({"error": "读取 MV 项目列表失败。"}, status=500)


@PromptServer.instance.routes.post("/xiaoyue-mv/projects/upsert")
async def upsert_mv_project(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError("项目请求格式无效。")
        project = await asyncio.to_thread(
            upsert_project,
            str(data.get("project_id") or ""),
            str(data.get("project_name") or ""),
            str(data.get("project_code") or ""),
            None,
            project_brief=data.get("project_brief"),
            planning_settings=data.get("planning_settings"),
            generation_spec=data.get("generation_spec"),
            skill_templates=data.get("skill_templates"),
            assets=data.get("assets"),
            lyrics=data.get("lyrics"),
        )
        return web.json_response({
            "project": project["manifest"],
            "paths": project["paths"],
        })
    except (json.JSONDecodeError, OSError, TypeError, ValueError) as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except Exception:
        logging.exception("Xiaoyue project upsert failed")
        return web.json_response({"error": "初始化 MV 项目失败。"}, status=500)


@PromptServer.instance.routes.get("/xiaoyue-mv/projects/{project_id}")
async def get_mv_project(request: web.Request) -> web.Response:
    try:
        project = await asyncio.to_thread(load_project, request.match_info["project_id"])
        return web.json_response({
            "project": project["manifest"],
            "paths": project["paths"],
        })
    except (TypeError, ValueError) as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except Exception:
        logging.exception("Xiaoyue project read failed")
        return web.json_response({"error": "读取 MV 项目失败。"}, status=500)


@PromptServer.instance.routes.post("/xiaoyue-mv/projects/audio/upload")
async def upload_project_audio(request: web.Request) -> web.Response:
    temporary: Path | None = None
    final_path: Path | None = None
    try:
        reader = await request.multipart()
        project_id = ""
        role = ""
        upload = None
        while True:
            part = await reader.next()
            if part is None:
                break
            if part.name == "project_id":
                project_id = (await part.text()).strip()
            elif part.name == "role":
                role = (await part.text()).strip()
            elif part.name == "file" and part.filename:
                upload = part
                break
        if not project_id:
            raise ValueError("请先创建或打开项目，再导入项目声音。")
        if role not in {"master", "vocals"}:
            raise ValueError("项目声音类型必须是母带或干声。")
        if upload is None:
            raise ValueError("没有收到音频文件。")

        project = await asyncio.to_thread(load_project, project_id)
        project_root = Path(project["paths"]["input"]).resolve()
        directory = (project_root / "source" / "audio").resolve()
        directory.relative_to(project_root)
        directory.mkdir(parents=True, exist_ok=True)
        extension = source_extension(upload.filename)
        temporary = directory / f".{role}.{uuid.uuid4().hex}{extension}"
        written = 0
        with temporary.open("wb") as output:
            while True:
                chunk = await upload.read_chunk(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > AUDIO_UPLOAD_LIMIT:
                    raise ValueError("单个项目音频不能超过 2 GB。")
                output.write(chunk)
        if written <= 0:
            raise ValueError("音频文件为空。")

        provisional_relative = temporary.relative_to(project_root).as_posix()
        metadata = await asyncio.to_thread(
            describe_audio,
            temporary,
            relative_path=provisional_relative,
            original_name=upload.filename,
        )
        safe_original = re.sub(
            r"[\x00-\x1f<>:\"/\\|?*]+", "_", Path(upload.filename).stem
        ).strip(" .") or role
        final_name = f"{role}_{metadata['fingerprint'][:16]}_{safe_original}{extension}"
        final_path = _available_upload_path(directory, final_name)
        os.replace(temporary, final_path)
        temporary = None
        metadata["source"]["path"] = final_path.relative_to(project_root).as_posix()
        updated = await asyncio.to_thread(
            update_project_audio_role, project_id, role, metadata)
        return web.json_response({
            "project": updated["manifest"],
            "paths": updated["paths"],
            "audio": updated["manifest"].get("audio", {}).get(role, {}),
        })
    except (TypeError, ValueError, web.HTTPException) as exc:
        if temporary and temporary.exists():
            temporary.unlink(missing_ok=True)
        return web.json_response({"error": str(exc)}, status=400)
    except Exception:
        if temporary and temporary.exists():
            temporary.unlink(missing_ok=True)
        logging.exception("Xiaoyue project audio upload failed")
        return web.json_response({"error": "导入项目声音失败。"}, status=500)


def _asset_media_type(path: Path) -> str:
    return "video" if path.suffix.lower() in ASSET_VIDEO_SUFFIXES else "image"


def _asset_descriptor(path: Path, root: Path) -> dict[str, Any]:
    relative = path.resolve().relative_to(root.resolve())
    stat = path.stat()
    return {
        "filename": relative.name,
        "subfolder": relative.parent.as_posix() if relative.parent != Path(".") else "",
        "type": "input",
        "media_type": _asset_media_type(path),
        "size": int(stat.st_size),
        "modified_ms": int(stat.st_mtime * 1000),
    }


def _list_input_assets(query: str = "", limit: int = 600) -> list[dict[str, Any]]:
    root = Path(folder_paths.get_input_directory()).resolve()
    needle = str(query or "").strip().casefold()
    candidates = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in ASSET_SUFFIXES:
            continue
        relative = path.relative_to(root).as_posix()
        if needle and needle not in relative.casefold():
            continue
        candidates.append(path)
    candidates.sort(key=lambda item: item.stat().st_mtime_ns, reverse=True)
    return [_asset_descriptor(path, root) for path in candidates[:max(1, min(2000, limit))]]


def _safe_upload_filename(filename: str) -> str:
    name = Path(str(filename or "")).name.strip()
    suffix = Path(name).suffix.lower()
    if not name or suffix not in ASSET_SUFFIXES:
        raise ValueError("只支持常用图片或视频资产文件。")
    stem = Path(name).stem.strip() or "asset"
    stem = re.sub(r"[\x00-\x1f<>:\"/\\|?*]+", "_", stem).strip(" .") or "asset"
    return f"{stem}{suffix}"


def _available_upload_path(directory: Path, filename: str) -> Path:
    requested = directory / filename
    if not requested.exists():
        return requested
    stem = requested.stem
    suffix = requested.suffix
    for index in range(1, 10000):
        candidate = directory / f"{stem}_{index:03d}{suffix}"
        if not candidate.exists():
            return candidate
    raise ValueError("同名资产版本过多，请先整理当前项目的 assets 目录。")


def _prompt_queue_is_busy() -> bool:
    prompt_queue = getattr(PromptServer.instance, "prompt_queue", None)
    if prompt_queue is None:
        return False
    running, queued = prompt_queue.get_current_queue_volatile()
    return bool(running or queued)


def _prune_outline_jobs(now: float | None = None) -> None:
    cutoff = (time.time() if now is None else now) - OUTLINE_JOB_TTL_SECONDS
    expired = [
        job_id for job_id, job in _OUTLINE_JOBS.items()
        if job.get("finished_at", float("inf")) < cutoff
    ]
    for job_id in expired:
        _OUTLINE_JOBS.pop(job_id, None)
        _OUTLINE_TASKS.pop(job_id, None)


async def _run_outline_job(job_id: str, data: dict[str, Any]) -> None:
    job = _OUTLINE_JOBS[job_id]
    visual_images = []
    try:
        plan = data.get("plan")
        system_prompt, user_prompt, input_summary, fingerprint = build_outline_prompts(plan)
        visual_images, visual_assets = await asyncio.to_thread(
            load_outline_visual_inputs,
            plan,
            folder_paths.get_input_directory(),
        )
        expected_ids = [str(shot.get("id") or "") for shot in plan["shots"]]
        raw_text, model = await asyncio.to_thread(
            run_qwen_te_provider,
            str(data.get("provider_token") or ""),
            system_prompt,
            user_prompt,
            images=visual_images,
        )
        input_summary["visual_assets"] = visual_assets
        outline = parse_outline_response(raw_text, expected_ids)
        job.update({
            "state": "completed",
            "result": {
                "outline": outline,
                "model": model,
                "input_summary": input_summary,
                "source_fingerprint": fingerprint,
            },
            "finished_at": time.time(),
        })
    except (TypeError, ValueError, RuntimeError) as exc:
        job.update({
            "state": "failed",
            "error": str(exc),
            "finished_at": time.time(),
        })
    except Exception:
        logging.exception("Xiaoyue MV creative outline generation failed")
        job.update({
            "state": "failed",
            "error": "生成整片镜头纲领时发生未预期错误；原有纲领没有改变。",
            "finished_at": time.time(),
        })
    finally:
        visual_images.clear()
        data.clear()
        _OUTLINE_TASKS.pop(job_id, None)


@PromptServer.instance.routes.post("/xiaoyue-mv/creative/outline")
async def start_creative_outline(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError("镜头纲领请求格式无效。")
        token = str(data.get("provider_token") or "").strip()
        plan = data.get("plan")
        if not token:
            raise ValueError("没有可用的推理 Provider；请先连接并运行 QwenTE 适配器。")
        # Validate the complete request before returning a job id.
        build_outline_prompts(plan)
        if _prompt_queue_is_busy():
            return web.json_response({
                "error": "ComfyUI 队列仍有任务。请等待队列空闲后再生成整片镜头纲领。",
            }, status=409)
        if any(job.get("state") == "running" for job in _SHOT_GENERATION_JOBS.values()):
            return web.json_response({"error": "本地视频生成任务仍在进行；请等待它完成。"}, status=409)
        _prune_outline_jobs()
        if any(job.get("state") == "running" for job in _OUTLINE_JOBS.values()):
            return web.json_response({
                "error": "已有整片镜头纲领任务正在运行，请等待它完成。",
            }, status=409)
        if any(job.get("state") in ("queued", "running")
               for job in _H3_PROMPT_JOBS.values()):
            return web.json_response({
                "error": "H3 提示词任务仍在排队或生成；请等待完成后再生成整片纲领。",
            }, status=409)
        job_id = uuid.uuid4().hex
        _OUTLINE_JOBS[job_id] = {
            "job_id": job_id,
            "state": "running",
            "started_at": time.time(),
        }
        task_data = {"provider_token": token, "plan": plan}
        task = asyncio.create_task(_run_outline_job(job_id, task_data))
        _OUTLINE_TASKS[job_id] = task
        return web.json_response({
            "job_id": job_id,
            "state": "running",
            "poll_after_ms": 1000,
        }, status=202)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except Exception:
        logging.exception("Xiaoyue MV creative outline start failed")
        return web.json_response({"error": "无法启动整片镜头纲领任务。"}, status=500)


@PromptServer.instance.routes.get("/xiaoyue-mv/creative/outline/{job_id}")
async def get_creative_outline(request: web.Request) -> web.Response:
    _prune_outline_jobs()
    job_id = request.match_info["job_id"]
    job = _OUTLINE_JOBS.get(job_id)
    if job is None:
        return web.json_response({"error": "镜头纲领任务不存在或已过期。"}, status=404)
    payload = {
        "job_id": job_id,
        "state": job["state"],
        "elapsed_seconds": round(
            max(0.0, (job.get("finished_at") or time.time()) - job["started_at"]),
            3,
        ),
    }
    if job["state"] == "completed":
        payload["result"] = job["result"]
    elif job["state"] == "failed":
        payload["error"] = job["error"]
    return web.json_response(payload)


def _prune_h3_prompt_jobs(now: float | None = None) -> None:
    cutoff = (time.time() if now is None else now) - H3_PROMPT_JOB_TTL_SECONDS
    expired = [
        job_id for job_id, job in _H3_PROMPT_JOBS.items()
        if job.get("finished_at", float("inf")) < cutoff
    ]
    for job_id in expired:
        _H3_PROMPT_JOBS.pop(job_id, None)
        _H3_PROMPT_TASKS.pop(job_id, None)


async def _run_h3_prompt_job(job_id: str, data: dict[str, Any]) -> None:
    # Local QwenTE has one inference slot. Accept independent shots while it
    # is occupied, then execute their jobs in submission order.
    async with _H3_PROMPT_RUN_LOCK:
        job = _H3_PROMPT_JOBS[job_id]
        job.update({"state": "running", "started_at": time.time()})
        await _execute_h3_prompt_job(job_id, data)


async def _execute_h3_prompt_job(job_id: str, data: dict[str, Any]) -> None:
    job = _H3_PROMPT_JOBS[job_id]
    visual_images: list[Any] = []
    try:
        plan = data["plan"]
        shot_ids = data["shot_ids"]
        result = job["result"]
        results = result["prompts"]
        failures = result["failures"]
        skipped = result["skipped"]
        outcomes: dict[str, str] = {}
        requested_ids = set(shot_ids)
        model = None
        for position, shot_id in enumerate(shot_ids, 1):
            response_chars = 0
            response_ends_with_brace = False
            attempt = 0
            quality_warning = ""
            job.update({
                "current_shot_id": shot_id,
                "current_label": shot_id,
                "completed_count": position - 1,
            })
            editorial_label = shot_id
            predecessor = continuation_predecessor(plan, shot_id)
            predecessor_id = str((predecessor or {}).get("shot_id") or "")
            predecessor_outcome = outcomes.get(predecessor_id)
            if (predecessor is not None
                    and predecessor_id in requested_ids
                    and predecessor_outcome != "success"):
                reason = (
                    f"续写前置镜头 {predecessor['editorial_label']} "
                    f"本批次{('生成失败' if predecessor_outcome == 'failed' else '已跳过')}；"
                    "当前镜头及其连续后段跳过，遇到硬切或叠化后自动恢复。"
                )
                skipped.append({
                    "shot_id": shot_id,
                    "editorial_label": predecessor["target_label"],
                    "reason": reason,
                    "dependency_shot_id": predecessor_id,
                })
                outcomes[shot_id] = "skipped"
                job.update({
                    "current_label": predecessor["target_label"],
                    "completed_count": position,
                })
                result["skipped_count"] = len(skipped)
                continue
            try:
                system_prompt, user_prompt, input_summary, fingerprint = \
                    build_h3_prompt_prompts(plan, shot_id)
                editorial_label = str(
                    input_summary.get("editorial_label") or shot_id)
                job["current_label"] = editorial_label
                visual_images, visual_assets = await asyncio.to_thread(
                    load_shot_visual_inputs,
                    plan,
                    shot_id,
                    folder_paths.get_input_directory(),
                )
                provider = client_reasoning_provider_descriptor(
                    str(data.get("provider_token") or ""))
                logging.info(
                    "Xiaoyue MV H3 diagnostic start job=%s shot=%s mode=%s "
                    "singing=%s lyrics_chars=%s images=%s max_tokens=%s "
                    "system_chars=%s user_chars=%s",
                    job_id, shot_id, input_summary["mode"],
                    input_summary["singing_performance"],
                    input_summary["lyrics_chars"], len(visual_images),
                    provider.get("max_tokens"), len(system_prompt), len(user_prompt),
                )
                attempt_user_prompt = user_prompt
                first_validation_error = ""
                for attempt in (1, 2):
                    response_chars = 0
                    response_ends_with_brace = False
                    try:
                        raw_text, model = await asyncio.to_thread(
                            run_qwen_te_provider,
                            str(data.get("provider_token") or ""),
                            system_prompt,
                            attempt_user_prompt,
                            images=visual_images,
                        )
                    except RuntimeError as exc:
                        if attempt == 2:
                            raise RuntimeError(
                                f"首次输出不合格（{first_validation_error}）；"
                                f"自动重试推理失败：{exc}"
                            ) from exc
                        raise
                    response_chars = len(raw_text)
                    response_ends_with_brace = raw_text.rstrip().endswith("}")
                    logging.info(
                        "Xiaoyue MV H3 diagnostic response job=%s shot=%s "
                        "attempt=%s response_chars=%s ends_with_brace=%s "
                        "finish_reason=%s prompt_tokens=%s completion_tokens=%s "
                        "max_tokens=%s",
                        job_id, shot_id, attempt, response_chars,
                        response_ends_with_brace,
                        model.get("finish_reason", "unknown"),
                        model.get("prompt_tokens", "unknown"),
                        model.get("completion_tokens", "unknown"),
                        model.get("max_tokens", "unknown"),
                    )
                    try:
                        prompt = parse_h3_prompt_response(
                            raw_text,
                            shot_id,
                            str(input_summary["mode"]),
                            str(input_summary.get("output_language") or "zh-CN"),
                            original_song_audio_label=str(
                                input_summary.get("original_song_audio_label") or ""),
                            singing_lyrics=str(
                                input_summary.get("singing_lyrics") or ""),
                        )
                        break
                    except ValueError as exc:
                        if attempt == 2:
                            # H3 prompts are reviewed by a person. Keep a
                            # non-empty, correctly routed draft even when a
                            # content heuristic still dislikes it.
                            prompt = extract_h3_prompt_response(
                                raw_text, shot_id, str(input_summary["mode"]))
                            quality_warning = str(exc)
                            logging.warning(
                                "Xiaoyue MV H3 diagnostic review job=%s shot=%s "
                                "quality_warning=%s", job_id, shot_id,
                                quality_warning,
                            )
                            break
                        first_validation_error = str(exc)
                        logging.warning(
                            "Xiaoyue MV H3 diagnostic retry job=%s shot=%s "
                            "first_error=%s finish_reason=%s completion_tokens=%s",
                            job_id, shot_id, first_validation_error,
                            model.get("finish_reason", "unknown"),
                            model.get("completion_tokens", "unknown"),
                        )
                        attempt_user_prompt = build_h3_retry_user_prompt(
                            user_prompt, str(input_summary["mode"]),
                            first_validation_error,
                            rejected_text=raw_text,
                            language_failure=isinstance(exc, H3PromptLanguageError),
                            output_language=str(
                                input_summary.get("output_language") or "zh-CN"),
                        )
                model["attempts"] = attempt
                results.append({
                    "shot_id": shot_id,
                    "editorial_label": editorial_label,
                    "mode": input_summary["mode"],
                    "prompt": prompt,
                    "asset_count": input_summary["asset_count"],
                    "visual_assets": visual_assets,
                    "source_fingerprint": fingerprint,
                    "quality_warning": quality_warning,
                    "singing_performance": input_summary["singing_performance"],
                    "lyrics_chars": input_summary["lyrics_chars"],
                })
                # The task owns a private JSON copy of the plan. Feed each newly
                # generated prompt into the next continuation shot so an incoming
                # long-take uses the actual predecessor result, not a stale prompt.
                for source_shot in plan.get("shots") or []:
                    if (isinstance(source_shot, dict)
                            and str(source_shot.get("id") or "") == shot_id):
                        source_shot["prompt"] = prompt
                        break
                outcomes[shot_id] = "success"
                logging.info(
                    "Xiaoyue MV H3 diagnostic success job=%s shot=%s "
                    "prompt_chars=%s attempts=%s",
                    job_id, shot_id, len(prompt), attempt,
                )
            except (TypeError, ValueError, RuntimeError) as exc:
                logging.warning(
                    "Xiaoyue MV H3 diagnostic failure job=%s shot=%s "
                    "attempt=%s response_chars=%s ends_with_brace=%s error=%s",
                    job_id, shot_id, attempt, response_chars,
                    response_ends_with_brace, exc,
                )
                failures.append({
                    "shot_id": shot_id,
                    "editorial_label": editorial_label,
                    "error": str(exc),
                })
                outcomes[shot_id] = "failed"
            except Exception:
                logging.exception(
                    "Xiaoyue MV H3 prompt generation failed for shot %s",
                    shot_id,
                )
                failures.append({
                    "shot_id": shot_id,
                    "editorial_label": editorial_label,
                    "error": "生成该镜头 H3 提示词时发生未预期错误。",
                })
                outcomes[shot_id] = "failed"
            finally:
                visual_images.clear()
                job["completed_count"] = position
                result["successful_count"] = len(results)
                result["failed_count"] = len(failures)
                result["skipped_count"] = len(skipped)
                if model:
                    result["model"] = model
        job.update({
            "state": "completed",
            "current_shot_id": "",
            "current_label": "",
            "finished_at": time.time(),
        })
    except (TypeError, ValueError, RuntimeError) as exc:
        job.update({
            "state": "failed",
            "error": str(exc),
            "finished_at": time.time(),
        })
    except Exception:
        logging.exception("Xiaoyue MV H3 prompt generation failed")
        job.update({
            "state": "failed",
            "error": "H3 提示词任务意外中断；已成功镜头仍可写入，其余镜头保留旧提示词。",
            "finished_at": time.time(),
        })
    finally:
        visual_images.clear()
        data.clear()
        _H3_PROMPT_TASKS.pop(job_id, None)


def _h3_prompt_job_conflict(
    plan: dict[str, Any], shot_ids: list[str], batch: bool = False,
) -> str:
    active_jobs = [job for job in _H3_PROMPT_JOBS.values()
                   if job.get("state") in ("queued", "running")]
    if not active_jobs:
        return ""
    if batch or len(shot_ids) > 1 or any(job.get("batch") for job in active_jobs):
        return "批量 H3 提示词任务与其它提示词任务互斥，请等待当前任务完成。"
    project_id = str((plan.get("project") or {}).get("id") or "")
    scoped_jobs = [job for job in active_jobs
                   if job.get("project_id", "") == project_id]
    shot_id = shot_ids[0]
    active_ids = {active_id for job in scoped_jobs
                  for active_id in job.get("shot_ids", [])}
    if shot_id in active_ids:
        return "本镜头的 H3 提示词已经在队列中，请勿重复提交。"
    # A continuation must not snapshot its predecessor's old prompt while
    # either section of the same long-take chain is still being regenerated.
    def ancestors(candidate_id: str) -> set[str]:
        original_id = candidate_id
        found: set[str] = set()
        while candidate_id not in found:
            found.add(candidate_id)
            predecessor = continuation_predecessor(plan, candidate_id)
            candidate_id = str((predecessor or {}).get("shot_id") or "")
            if not candidate_id:
                break
        found.discard(original_id)
        return found

    if active_ids.intersection(ancestors(shot_id)) or any(
        shot_id in ancestors(active_id) for active_id in active_ids
    ):
        return "同一续写链的前后镜头仍有提示词任务；请等它完成后再提交，确保使用最新前置提示词。"
    return ""


@PromptServer.instance.routes.post("/xiaoyue-mv/h3/prompts")
async def start_h3_prompts(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError("H3 提示词请求格式无效。")
        token = str(data.get("provider_token") or "").strip()
        plan = data.get("plan")
        raw_shot_ids = data.get("shot_ids")
        if not token:
            raise ValueError("没有可用的推理 Provider；请先连接并运行 QwenTE 适配器。")
        if not isinstance(raw_shot_ids, list) or not raw_shot_ids:
            raise ValueError("H3 提示词请求没有指定镜头。")
        shot_ids = [str(value or "").strip() for value in raw_shot_ids]
        if any(not value for value in shot_ids) or len(set(shot_ids)) != len(shot_ids):
            raise ValueError("H3 提示词请求包含空值或重复 shot_id。")
        if not isinstance(plan, dict):
            raise ValueError("H3 提示词请求没有有效的 MV 计划。")
        selected_skill = selected_skill_descriptor(plan, "h3_prompt")
        if _prompt_queue_is_busy():
            return web.json_response({
                "error": "ComfyUI 队列仍有任务。请等待队列空闲后再生成 H3 提示词。",
            }, status=409)
        if any(job.get("state") == "running" for job in _SHOT_GENERATION_JOBS.values()):
            return web.json_response({"error": "本地视频生成任务仍在进行；请等待它完成。"}, status=409)
        _prune_outline_jobs()
        _prune_h3_prompt_jobs()
        if any(job.get("state") == "running" for job in _OUTLINE_JOBS.values()):
            return web.json_response({
                "error": "整片镜头纲领正在生成，请等待完成后再生成 H3 提示词。",
            }, status=409)
        batch = bool(data.get("batch")) or len(shot_ids) > 1
        conflict = _h3_prompt_job_conflict(plan, shot_ids, batch)
        if conflict:
            return web.json_response({
                "error": conflict,
            }, status=409)
        job_id = uuid.uuid4().hex
        queued_count = sum(job.get("state") in ("queued", "running")
                           for job in _H3_PROMPT_JOBS.values())
        _H3_PROMPT_JOBS[job_id] = {
            "job_id": job_id,
            "state": "queued",
            "queued_at": time.time(),
            "project_id": str((plan.get("project") or {}).get("id") or ""),
            "shot_ids": shot_ids,
            "batch": batch,
            "completed_count": 0,
            "total_count": len(shot_ids),
            "current_shot_id": shot_ids[0],
            "current_label": "",
            "result": {
                "prompts": [],
                "failures": [],
                "skipped": [],
                "successful_count": 0,
                "failed_count": 0,
                "skipped_count": 0,
                "model": {},
                "skill": selected_skill,
            },
        }
        task_data = {
            "provider_token": token,
            "plan": plan,
            "shot_ids": shot_ids,
        }
        task = asyncio.create_task(_run_h3_prompt_job(job_id, task_data))
        _H3_PROMPT_TASKS[job_id] = task
        return web.json_response({
            "job_id": job_id,
            "state": "queued",
            "queue_position": queued_count + 1,
            "poll_after_ms": 1000,
            "total_count": len(shot_ids),
        }, status=202)
    except (json.JSONDecodeError, TypeError, ValueError, RuntimeError) as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except Exception:
        logging.exception("Xiaoyue MV H3 prompt start failed")
        return web.json_response({"error": "无法启动 H3 提示词生成任务。"}, status=500)


@PromptServer.instance.routes.get("/xiaoyue-mv/h3/prompts/{job_id}")
async def get_h3_prompts(request: web.Request) -> web.Response:
    _prune_h3_prompt_jobs()
    job_id = request.match_info["job_id"]
    job = _H3_PROMPT_JOBS.get(job_id)
    if job is None:
        return web.json_response({"error": "H3 提示词任务不存在或已过期。"}, status=404)
    payload = {
        "job_id": job_id,
        "state": job["state"],
        "elapsed_seconds": round(
            max(0.0, (job.get("finished_at") or time.time())
                - job.get("started_at", job["queued_at"])),
            3,
        ),
        "completed_count": int(job.get("completed_count") or 0),
        "total_count": int(job.get("total_count") or 0),
        "current_shot_id": str(job.get("current_shot_id") or ""),
        "current_label": str(job.get("current_label") or ""),
    }
    if job.get("result") is not None:
        payload["result"] = job["result"]
    if job["state"] == "failed":
        payload["error"] = job["error"]
    return web.json_response(payload)


def _shot_generation_url() -> str:
    port = int(getattr(PromptServer.instance, "port", 8188))
    return f"http://127.0.0.1:{port}"


@PromptServer.instance.routes.get("/xiaoyue-mv/editor/{project_id}")
async def get_editor_state(request: web.Request) -> web.Response:
    try:
        state = await asyncio.to_thread(load_editor_state, request.match_info["project_id"])
        return web.json_response(state)
    except (OSError, TypeError, ValueError) as exc:
        return web.json_response({"error": str(exc)}, status=400)


@PromptServer.instance.routes.post("/xiaoyue-mv/editor/{project_id}/save")
async def save_editor_state(request: web.Request) -> web.Response:
    try:
        project_id = request.match_info["project_id"]
        payload = await request.json()
        if not isinstance(payload, dict):
            raise ValueError("编辑草稿请求格式无效。")
        await asyncio.to_thread(save_editor_plan, project_id, payload.get("plan"))
        return web.json_response({"saved": True})
    except (json.JSONDecodeError, OSError, TypeError, ValueError) as exc:
        return web.json_response({"error": str(exc)}, status=400)


@PromptServer.instance.routes.post("/xiaoyue-mv/editor/{project_id}/timeline")
async def save_editor_timeline(request: web.Request) -> web.Response:
    try:
        project_id = request.match_info["project_id"]
        payload = await request.json()
        if not isinstance(payload, dict):
            raise ValueError("时间轴检查点请求格式无效。")
        await asyncio.to_thread(save_timeline_checkpoint, project_id, payload)
        return web.json_response({"saved": True})
    except (json.JSONDecodeError, OSError, TypeError, ValueError) as exc:
        return web.json_response({"error": str(exc)}, status=400)


@PromptServer.instance.routes.post("/xiaoyue-mv/editor/{project_id}/shot")
async def save_editor_shot(request: web.Request) -> web.Response:
    try:
        project_id = request.match_info["project_id"]
        payload = await request.json()
        await asyncio.to_thread(save_shot_patch, project_id, payload)
        return web.json_response({"saved": True})
    except (json.JSONDecodeError, OSError, TypeError, ValueError) as exc:
        return web.json_response({"error": str(exc)}, status=400)


def _stable_shot_id(item: dict[str, Any]) -> str:
    return str(item.get("shot_uid") or f"legacy_{item.get('shot_id') or item.get('id') or ''}")


def _shot_window_fingerprints(plan: dict[str, Any]) -> dict[str, dict[str, str]]:
    audio = str((plan.get("audio") or {}).get("fingerprint") or "")
    specification = plan.get("generation_spec") or {}
    windows = [item for item in plan.get("generation_windows", [])
               if isinstance(item, dict) and item.get("shot_id")]
    by_label = {str(item.get("editorial_label") or ""): item for item in windows}
    def parent_uid(window: dict[str, Any]) -> str:
        label = str(window.get("continuity_parent_label") or "")
        return _stable_shot_id(by_label[label]) if label in by_label else ""

    return {
        _stable_shot_id(window): {
            "current": window_fingerprint(
                window, specification, audio, parent_uid=parent_uid(window)),
            "legacy": window_fingerprint(window, specification, audio, legacy=True),
        }
        for window in windows
    }


def _require_current_window_rule(plan: dict[str, Any]) -> None:
    revision = int((plan.get("h3") or {}).get("continuation_window_rule_revision") or 0)
    if revision != CONTINUATION_WINDOW_RULE_REVISION:
        raise ValueError("镜头窗口使用旧版续写切点；请先运行 MV 工作台节点更新计划。")


def _shot_generation_assets(plan: dict[str, Any], shot: dict[str, Any]) -> list[dict[str, str]]:
    library = {str(asset.get("id") or ""): asset for asset in (plan.get("assets") or [])
               if isinstance(asset, dict)}
    input_root = Path(folder_paths.get_input_directory()).resolve()
    result = []
    bindings = sorted((binding for binding in shot.get("asset_bindings", [])
                       if isinstance(binding, dict)),
                      key=lambda binding: int(binding.get("order") or 0))
    for binding in bindings:
        asset = library.get(str(binding.get("asset_id") or ""))
        if asset is None:
            raise ValueError("镜头绑定的资产已从项目资产库移除。")
        source = asset.get("source") if isinstance(asset.get("source"), dict) else {}
        filename = str(source.get("filename") or "")
        subfolder = str(source.get("subfolder") or "").replace("\\", "/")
        if not filename or Path(filename).name != filename:
            raise ValueError("镜头视觉资产文件名无效。")
        relative = Path(subfolder) / filename
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("镜头视觉资产路径越界。")
        absolute = (input_root / relative).resolve()
        absolute.relative_to(input_root)
        if not absolute.is_file():
            raise ValueError(f"镜头视觉资产不存在：{asset.get('name') or filename}")
        result.append({
            "media_type": "video" if source.get("media_type") == "video" else "image",
            "input_name": relative.as_posix(),
            "absolute_path": str(absolute),
        })
    return result


def _shot_generation_parent(plan: dict[str, Any], window: dict[str, Any],
                            state: dict[str, Any],
                            fresh: dict[str, dict[str, Any]],
                            project: dict[str, Any]) -> tuple[str, str, int, int]:
    label = str(window.get("continuity_parent_label") or "")
    if not label:
        return "", "", 0, 0
    parent_window = next((item for item in plan.get("generation_windows", [])
                          if item.get("editorial_label") == label), None)
    if parent_window is None:
        raise ValueError(f"找不到续写前段 {label} 的生成窗口。")
    parent_shot_id = str(parent_window["shot_id"])
    parent_uid = _stable_shot_id(parent_window)
    parent = resolve_parent_version(
        state, parent_uid, label, fresh.get(parent_shot_id))
    descriptor = parent.get("output") or {}
    filename = str(descriptor.get("filename") or "")
    subfolder = str(descriptor.get("subfolder") or "").replace("\\", "/")
    if not filename or Path(filename).name != filename:
        raise ValueError("续写前段输出文件记录无效。")
    output_root = Path(folder_paths.get_output_directory()).resolve()
    path = (output_root / subfolder / filename).resolve()
    path.relative_to(Path(project["paths"]["output"]).resolve() / "generations")
    if not path.is_file():
        raise ValueError("续写前段的最终视频文件已不存在。")
    recorded_frames = int(descriptor.get("frame_count") or 0)
    planned_frames = int(parent.get("raw_frames") or 0)
    guide_frames = int(window.get("context_frames") or 0)
    tail_start = delivery_tail_start(
        int(parent.get("deliver_from_raw_frame") or 0),
        int(parent.get("deliver_frames") or 0), guide_frames)
    if ((recorded_frames and planned_frames and recorded_frames != planned_frames)
            or (recorded_frames and tail_start + guide_frames > recorded_frames)):
        raise ValueError(
            f"续写前段视频帧数与规划切点不一致，不能安全读取交付区末 {guide_frames} 帧。")
    return (str(parent["id"]), str(path), int(parent.get("deliver_frames") or 0),
            int(parent.get("deliver_from_raw_frame") or 0))


async def _shot_generation_request(session: aiohttp.ClientSession,
                                   method: str, endpoint: str,
                                   payload: dict[str, Any] | None = None) -> dict[str, Any]:
    async with session.request(method, _shot_generation_url() + endpoint,
                               json=payload) as response:
        data = await response.json(content_type=None)
        if response.status >= 400:
            detail = data.get("error") or data
            if isinstance(detail, dict):
                detail = detail.get("message") or detail.get("details") or str(detail)
            raise ValueError(f"ComfyUI {endpoint}：{detail}")
        return data


async def _await_shot_history(session: aiohttp.ClientSession, prompt_id: str,
                              job: dict[str, Any]) -> dict[str, Any]:
    deadline = time.monotonic() + 3 * 60 * 60
    cancellation_sent = False
    while time.monotonic() < deadline:
        history = await _shot_generation_request(
            session, "GET", f"/history/{prompt_id}")
        if prompt_id in history:
            return history[prompt_id]
        if job.get("cancel_requested"):
            queue = await _shot_generation_request(session, "GET", "/queue")
            running = {item[1] for item in queue.get("queue_running", [])}
            pending = {item[1] for item in queue.get("queue_pending", [])}
            if prompt_id in pending:
                await _shot_generation_request(session, "POST", "/queue",
                                               {"delete": [prompt_id]})
                raise asyncio.CancelledError()
            if prompt_id in running and not cancellation_sent:
                await _shot_generation_request(session, "POST", "/interrupt",
                                               {"prompt_id": prompt_id})
                cancellation_sent = True
            if prompt_id not in running:
                raise asyncio.CancelledError()
        await asyncio.sleep(2)
    raise TimeoutError("镜头工作流等待超过 3 小时；可点击取消并检查 ComfyUI 状态。")


def _shot_history_output(history: dict[str, Any], project: dict[str, Any],
                         expected_raw_frames: int) -> dict[str, Any]:
    status = history.get("status") or {}
    if status.get("status_str") != "success":
        messages = status.get("messages") or []
        reason = next((str(message[1].get("exception_message") or "")
                       for message in reversed(messages)
                       if isinstance(message, list) and len(message) > 1
                       and isinstance(message[1], dict)
                       and message[0] == "execution_error"), "")
        raise ValueError(reason or "ComfyUI 镜头工作流执行失败。")
    descriptor = history_video_descriptor(history)
    if descriptor is None:
        raise ValueError("工作流已结束，但标记的视频输出节点没有返回视频文件。")
    filename = str(descriptor.get("filename") or "")
    subfolder = str(descriptor.get("subfolder") or "").replace("\\", "/")
    output_root = Path(folder_paths.get_output_directory()).resolve()
    path = (output_root / subfolder / filename).resolve()
    path.relative_to(Path(project["paths"]["output"]).resolve() / "generations")
    if not path.is_file():
        raise ValueError("工作流返回的视频不在项目输出目录中。")
    metadata = descriptor.get("metadata") or {}
    frame_count = int(metadata.get("frame_count") or 0) if isinstance(metadata, dict) else 0
    fps = float(metadata.get("fps") or 0) if isinstance(metadata, dict) else 0.0
    if frame_count and frame_count != expected_raw_frames:
        raise ValueError(
            f"视频输出节点实际输出 {frame_count} 帧，规划要求 {expected_raw_frames} 帧；"
            "请检查输出链是否改变了帧数。")
    if fps and abs(fps - 24.0) > 0.001:
        raise ValueError(f"视频输出节点实际输出 {fps:g} fps，续写链要求 24 fps。")
    return {"filename": filename, "subfolder": subfolder,
            "type": "output", "size": path.stat().st_size,
            "frame_count": frame_count, "fps": fps}


async def _run_shot_generation_job(job_id: str, data: dict[str, Any]) -> None:
    job = _SHOT_GENERATION_JOBS[job_id]
    project = data["project"]
    plan = data["plan"]
    project_id = project["manifest"]["project_id"]
    windows = {str(item.get("shot_id") or ""): item
               for item in plan["generation_windows"] if isinstance(item, dict)}
    shots = {str(item.get("id") or ""): item for item in plan["shots"]
             if isinstance(item, dict)}
    label_to_shot_id = {
        str(window.get("editorial_label") or ""): shot_id
        for shot_id, window in windows.items()
    }
    parent_by_id = {
        shot_id: label_to_shot_id.get(
            str(window.get("continuity_parent_label") or ""), "")
        for shot_id, window in windows.items()
    }
    selected_ids = set(data["shot_ids"])
    batch_parent_ids = {
        parent_by_id[shot_id]
        for shot_id in selected_ids
        if parent_by_id.get(shot_id) in selected_ids
    }
    shot_uid_by_id = {
        shot_id: _stable_shot_id(window)
        for shot_id, window in windows.items()
    }
    ui = data["canvas_workflow"]
    fresh: dict[str, dict[str, Any]] = {}
    try:
        timeout = aiohttp.ClientTimeout(total=45)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            node_info = await _shot_generation_request(session, "GET", "/object_info")
            for shot_id in data["shot_ids"]:
                if job.get("cancel_requested"):
                    break
                job["current_shot_id"] = shot_id
                shot = shots[shot_id]
                window = windows[shot_id]
                shot_uid = _stable_shot_id(window)
                version = None
                try:
                    parent_label = str(window.get("continuity_parent_label") or "")
                    if parent_label:
                        parent_window = next(
                            (item for item in plan["generation_windows"]
                             if item.get("editorial_label") == parent_label), None)
                        parent_shot_id = str((parent_window or {}).get("shot_id") or "")
                        if (parent_shot_id in data["shot_ids"]
                                and parent_shot_id not in fresh):
                            job["results"].append({
                                "shot_id": shot_id, "status": "skipped",
                                "error": f"本批次续写前段 {parent_label} 未成功，保留现有版本。",
                            })
                            continue
                    state = with_stale_flags(
                        await asyncio.to_thread(load_versions, project_id),
                        _shot_window_fingerprints(plan))
                    parent_id, parent_path, parent_frames, parent_lead = _shot_generation_parent(
                        plan, window, state, fresh, project)
                    prompt = str(shot.get("prompt") or "").strip()
                    requested_seed = shot_seed_setting(ui)
                    actual_seed = (secrets.randbits(63) if requested_seed < 0
                                   else requested_seed)
                    version = await asyncio.to_thread(
                        begin_version, project_id, shot_uid,
                        editorial_label=str(window.get("editorial_label") or shot_id),
                        window_id=str(window["id"]),
                        prompt_fingerprint=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                        window_fingerprint=_shot_window_fingerprints(plan)[shot_uid]["current"],
                        workflow_fingerprint=job["workflow_fingerprint"],
                        deliver_frames=int(window["deliver_frames"]),
                        raw_frames=int(window["raw_frames"]),
                        deliver_from_raw_frame=int(window["deliver_from_raw_frame"]),
                        timeline_in_frame=int(window["delivery_timeline_range"][0]),
                        timeline_out_frame_exclusive=int(window["delivery_timeline_range"][1]),
                        seed=actual_seed,
                        parent_version_id=parent_id)
                    job["current_version_id"] = version["id"]
                    audio_name = await asyncio.to_thread(
                        prepare_window_audio, project, window, version["id"])
                    assets = await asyncio.to_thread(_shot_generation_assets, plan, shot)
                    prefix = (f"xiaoyue_mv_projects/{project_id}/generations/"
                              f"{project['manifest']['project_code']}_"
                              f"{window['scene_code']}_v{version['pending_number']}_"
                              f"a{version['attempt_number']:03d}_{version['id'][:8]}")
                    graph = compose_shot_workflow(
                        ui, node_info, prompt=prompt, window=window,
                        generation_spec=plan["generation_spec"],
                        audio_input_name=audio_name, visual_inputs=assets,
                        output_prefix=prefix, seed=actual_seed,
                        parent_video_path=parent_path,
                        parent_deliver_frames=parent_frames,
                        parent_deliver_from_raw_frame=parent_lead)
                    if job.get("cancel_requested"):
                        raise asyncio.CancelledError()
                    queued = await _shot_generation_request(
                        session, "POST", "/prompt",
                        {"prompt": graph, "client_id": f"xiaoyue-mv-{job_id}"})
                    prompt_id = str(queued.get("prompt_id") or "")
                    if not prompt_id:
                        raise ValueError("ComfyUI 未返回镜头生成任务 ID。")
                    job["current_prompt_id"] = prompt_id
                    await asyncio.to_thread(update_version, project_id, shot_uid,
                                            version["id"], status="running",
                                            prompt_id=prompt_id)
                    history = await _await_shot_history(session, prompt_id, job)
                    if job.get("cancel_requested"):
                        raise asyncio.CancelledError()
                    output = await asyncio.to_thread(
                        _shot_history_output, history, project,
                        int(window["raw_frames"]))
                    completed = await asyncio.to_thread(
                        update_version, project_id, shot_uid, version["id"],
                        status="completed", output=output,
                        preserve_existing_selection=shot_id in batch_parent_ids)
                    fresh[shot_id] = completed
                    job["results"].append({"shot_id": shot_id, "status": "completed",
                                           "version_id": version["id"]})
                except asyncio.CancelledError:
                    if version:
                        await asyncio.to_thread(update_version, project_id, shot_uid,
                                                version["id"], status="cancelled")
                    job["results"].append({"shot_id": shot_id, "status": "cancelled"})
                    break
                except Exception as exc:
                    logging.exception("Xiaoyue MV shot generation failed: %s", shot_id)
                    if version:
                        await asyncio.to_thread(update_version, project_id, shot_uid,
                                                version["id"], status="failed",
                                                error=str(exc))
                    job["results"].append({"shot_id": shot_id, "status": "failed",
                                           "error": str(exc)})
                finally:
                    job["completed_count"] += 1
                    job["current_prompt_id"] = ""
                    job["current_version_id"] = ""
        if not job.get("cancel_requested"):
            selections = completed_batch_parent_selections(
                data["shot_ids"], parent_by_id, shot_uid_by_id, fresh)
            if selections:
                await asyncio.to_thread(select_versions, project_id, selections)
            job["auto_selected_parent_count"] = len(selections)
        job["state"] = "cancelled" if job.get("cancel_requested") else "completed"
    except Exception as exc:
        logging.exception("Xiaoyue MV shot generation job failed")
        job.update({"state": "failed", "error": str(exc)})
    finally:
        job["current_shot_id"] = ""
        job["finished_at"] = time.time()


@PromptServer.instance.routes.get("/xiaoyue-mv/generation/{project_id}")
async def get_shot_generations(request: web.Request) -> web.Response:
    try:
        project_id = request.match_info["project_id"]
        plan = await asyncio.to_thread(load_project_plan, project_id)
        async with _SHOT_GENERATION_START_LOCK:
            active = [job for job in _SHOT_GENERATION_JOBS.values()
                      if job.get("project_id") == project_id
                      and job.get("state") == "running"]
            state = with_stale_flags(
                await asyncio.to_thread(
                    load_versions if active else recover_interrupted_versions,
                    project_id),
                _shot_window_fingerprints(plan) if plan else {})
        return web.json_response({"versions": state, "jobs": active,
                                  "job": active[0] if active else None})
    except (TypeError, ValueError) as exc:
        return web.json_response({"error": str(exc)}, status=400)


@PromptServer.instance.routes.get("/xiaoyue-mv/generation/{project_id}/cut-list")
async def export_shot_cut_list(request: web.Request) -> web.Response:
    try:
        project_id = request.match_info["project_id"]
        export_format = request.query.get("format", "csv").lower()
        if export_format not in {"csv", "json"}:
            raise ValueError("剪切表只支持 CSV 或 JSON。")
        project = await asyncio.to_thread(load_project, project_id)
        plan = await asyncio.to_thread(load_project_plan, project_id)
        if not plan:
            raise ValueError("项目尚无已编译镜头计划。")
        state = with_stale_flags(
            await asyncio.to_thread(load_versions, project_id),
            _shot_window_fingerprints(plan))
        payload = await asyncio.to_thread(
            build_cut_list, plan, state,
            Path(folder_paths.get_output_directory()), project)
        if export_format == "json":
            return web.json_response(payload, headers={
                "Content-Disposition": f'attachment; filename="{project_id}_cut_list.json"'})
        return web.Response(body=cut_list_csv(payload).encode("utf-8-sig"),
                            content_type="text/csv", charset="utf-8",
                            headers={"Content-Disposition":
                                     f'attachment; filename="{project_id}_cut_list.csv"'})
    except (KeyError, TypeError, ValueError) as exc:
        return web.json_response({"error": str(exc)}, status=400)


@PromptServer.instance.routes.get("/xiaoyue-mv/preview/{project_id}")
async def get_mv_preview_desk(request: web.Request) -> web.Response:
    try:
        project_id = request.match_info["project_id"]
        project = await asyncio.to_thread(load_project, project_id)
        plan = await asyncio.to_thread(load_project_plan, project_id)
        if not plan:
            raise ValueError("请先在主 MV 工作台保存并编译镜头计划。")
        _require_current_window_rule(plan)
        if plan.get("needs_compile"):
            raise ValueError("镜头计划已有未编译改动；请先运行主 MV 工作台更新切点。")
        state = with_stale_flags(
            await asyncio.to_thread(load_versions, project_id),
            _shot_window_fingerprints(plan))
        manifest = await asyncio.to_thread(
            build_preview_manifest, plan, state, project,
            Path(folder_paths.get_output_directory()))
        return web.json_response(manifest)
    except (KeyError, TypeError, ValueError, OSError) as exc:
        return web.json_response({"error": str(exc)}, status=400)


@PromptServer.instance.routes.post("/xiaoyue-mv/preview/select")
async def select_mv_preview_version(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        project_id = str(data.get("project_id") or "")
        shot_uid = str(data.get("shot_uid") or "")
        version_id = str(data.get("version_id") or "")
        project = await asyncio.to_thread(load_project, project_id)
        plan = await asyncio.to_thread(load_project_plan, project_id)
        if not plan:
            raise ValueError("请先在主 MV 工作台保存并编译镜头计划。")
        _require_current_window_rule(plan)
        if plan.get("needs_compile"):
            raise ValueError("镜头计划已有未编译改动；请先运行主 MV 工作台更新切点。")
        if shot_uid not in {
                str(shot.get("shot_uid") or f"legacy_{shot.get('id')}")
                for shot in plan.get("shots") or []}:
            raise ValueError("这个镜头已不在当前工程中。")
        state = await asyncio.to_thread(load_versions, project_id)
        fingerprints = _shot_window_fingerprints(plan)
        selections = selection_chain(state, shot_uid, version_id, plan)
        before = await asyncio.to_thread(
            build_preview_manifest, plan, with_stale_flags(state, fingerprints),
            project, Path(folder_paths.get_output_directory()))
        by_uid = {shot["shot_uid"]: shot for shot in before["shots"]}
        for uid, selected_id in selections.items():
            version = next((item for item in by_uid[uid]["versions"]
                            if item["id"] == selected_id), None)
            if not version or not version["playable"]:
                raise ValueError("所选续写链有素材缺失或交付帧范围无效，不能验收。")
        state = await asyncio.to_thread(select_versions, project_id, selections)
        state = with_stale_flags(state, fingerprints)
        manifest = await asyncio.to_thread(
            build_preview_manifest, plan, state, project,
            Path(folder_paths.get_output_directory()))
        return web.json_response({"manifest": manifest, "selected_ancestors": selections})
    except (KeyError, TypeError, ValueError, OSError) as exc:
        return web.json_response({"error": str(exc)}, status=400)


@PromptServer.instance.routes.get("/xiaoyue-mv/preview/{project_id}/subtitles")
async def export_mv_final_subtitles(request: web.Request) -> web.Response:
    try:
        project_id = request.match_info["project_id"]
        output_format = str(request.query.get("format") or "srt").lower()
        if output_format not in {"srt", "csv", "json"}:
            raise ValueError("最终字幕表只支持 SRT、CSV 或 JSON。")
        plan = await asyncio.to_thread(load_project_plan, project_id)
        if not plan or str((plan.get("project") or {}).get("id") or "") != project_id:
            raise ValueError("工程缺少可用的最终字幕计划。")
        rows = final_subtitles(plan)
        filename = f"{project_id}_final_subtitles.{output_format}"
        headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
        if output_format == "json":
            return web.json_response({"fps": 24, "subtitles": rows}, headers=headers)
        body = subtitle_srt(rows) if output_format == "srt" else subtitle_csv(rows)
        return web.Response(
            body=("\ufeff" + body).encode("utf-8") if output_format == "csv"
            else body.encode("utf-8"),
            content_type="text/plain" if output_format == "srt" else "text/csv",
            charset="utf-8", headers=headers)
    except (KeyError, TypeError, ValueError) as exc:
        return web.json_response({"error": str(exc)}, status=400)


def _delivery_job_public(job: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in job.items()
            if key not in {"snapshot", "export_root"}}


async def _run_delivery_export_job(job_id: str) -> None:
    job = _DELIVERY_EXPORT_JOBS[job_id]
    cancel = _DELIVERY_EXPORT_CANCEL[job_id]

    def report(message: str, fraction: float) -> None:
        job["message"] = message
        job["progress"] = round(max(0.0, min(1.0, fraction)), 4)

    try:
        result = await asyncio.to_thread(
            run_export, job["snapshot"], job["kind"], Path(job["export_root"]),
            job_id, cancel, report)
        job["state"] = "completed"
        job["message"] = "导出完成"
        job["progress"] = 1.0
        job["download_url"] = (f"/xiaoyue-mv/preview/export/"
                               f"{job['project_id']}/{job_id}/download")
        if job["kind"] == "movie":
            job["preview_url"] = (f"/xiaoyue-mv/delivery/movie/"
                                  f"{job['project_id']}/{job_id}")
        job["folder"] = result["folder"]
    except ExportCancelled:
        job["state"] = "cancelled"
        job["message"] = "已取消；原始镜头与验收选择未改动"
    except Exception as exc:
        logging.exception("Xiaoyue MV delivery export failed: %s", job_id)
        job["state"] = "failed"
        job["message"] = str(exc) or "导出失败，请检查服务日志。"
    finally:
        job["finished_at"] = time.time()
        job.pop("snapshot", None)
        _DELIVERY_EXPORT_TASKS.pop(job_id, None)
        _DELIVERY_EXPORT_CANCEL.pop(job_id, None)


# Keep this outside /preview/{project_id}: aiohttp otherwise resolves the
# earlier dynamic GET resource first and returns 405 for this POST.
@PromptServer.instance.routes.post("/xiaoyue-mv/delivery/export")
async def start_mv_delivery_export(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        project_id = str(data.get("project_id") or "")
        kind = str(data.get("kind") or "")
        if kind not in EXPORT_KINDS:
            raise ValueError("请选择无字成片或 FCP XML 通用工程包；若仍看到旧按钮，请刷新页面。")
        if any(item["project_id"] == project_id and item["state"] == "running"
               for item in _DELIVERY_EXPORT_JOBS.values()):
            raise ValueError("这个工程已有导出任务在运行；请先等待或取消。")
        project = await asyncio.to_thread(load_project, project_id)
        plan = await asyncio.to_thread(load_project_plan, project_id)
        if not plan:
            raise ValueError("请先保存并编译完整镜头计划。")
        _require_current_window_rule(plan)
        if plan.get("needs_compile"):
            raise ValueError("镜头计划有未编译改动，请先更新切点。")
        output_root = Path(folder_paths.get_output_directory())
        state = with_stale_flags(
            await asyncio.to_thread(load_versions, project_id),
            _shot_window_fingerprints(plan))
        manifest = await asyncio.to_thread(
            build_preview_manifest, plan, state, project, output_root)
        snapshot = await asyncio.to_thread(
            build_export_snapshot, manifest, project, output_root)
        export_root = Path(project["paths"]["output"]) / "delivery" / "exports"
        free = shutil.disk_usage(export_root.parent).free
        estimated = snapshot["source_bytes"] * 2 + (1024 ** 3 if kind == "movie" else 64 * 1024 ** 2)
        if free < estimated:
            raise ValueError(f"导出空间不足：预计至少需要 {estimated / 1024 ** 3:.1f} GiB 可用空间。")
        job_id = uuid.uuid4().hex
        job = {
            "job_id": job_id, "project_id": project_id, "kind": kind,
            "state": "running", "message": "已冻结验收选版，准备导出",
            "progress": 0.0, "created_at": time.time(),
            "source_bytes": snapshot["source_bytes"],
            "shot_count": len(snapshot["clips"]),
            "snapshot": snapshot, "export_root": str(export_root),
        }
        _DELIVERY_EXPORT_JOBS[job_id] = job
        _DELIVERY_EXPORT_CANCEL[job_id] = threading.Event()
        _DELIVERY_EXPORT_TASKS[job_id] = asyncio.create_task(
            _run_delivery_export_job(job_id))
        return web.json_response(_delivery_job_public(job))
    except (KeyError, TypeError, ValueError, OSError, subprocess.SubprocessError) as exc:
        return web.json_response({"error": str(exc)}, status=400)


@PromptServer.instance.routes.get("/xiaoyue-mv/preview/export/{job_id}")
async def get_mv_delivery_export(request: web.Request) -> web.Response:
    job = _DELIVERY_EXPORT_JOBS.get(request.match_info["job_id"])
    if not job:
        return web.json_response({"error": "导出任务不存在或服务已重启。"}, status=404)
    return web.json_response(_delivery_job_public(job))


@PromptServer.instance.routes.get("/xiaoyue-mv/preview/{project_id}/exports")
async def list_mv_delivery_exports(request: web.Request) -> web.Response:
    try:
        project_id = request.match_info["project_id"]
        project = await asyncio.to_thread(load_project, project_id)
        root = Path(project["paths"]["output"]) / "delivery" / "exports"
        archives = sorted(
            (item for item in root.glob("*.zip")
             if re.fullmatch(r"[0-9a-f]{32}", item.stem) and item.is_file()),
            key=lambda item: item.stat().st_mtime, reverse=True)
        result = []
        for archive in archives:
            package = root / archive.stem
            kind_file = package / "export_kind.txt"
            movie = package / "MV_无字成片.mp4"
            subtitles = package / "最终字幕.srt"
            if kind_file.is_file():
                kind = kind_file.read_text(encoding="ascii").strip()
            elif movie.is_file():
                kind = "movie"
            elif (package / "FCP7_通用工程.xml").is_file():
                kind = "fcp_xml"
            elif (package / "Premiere_工程.xml").is_file():
                kind = "premiere"
            elif any(package.glob("*/draft_content.json")):
                kind = "jianying"
            else:
                kind = "unknown"
            result.append({
                "job_id": archive.stem, "kind": kind,
                "bytes": archive.stat().st_size,
                "created_at": archive.stat().st_mtime,
                "download_url": f"/xiaoyue-mv/preview/export/{project_id}/{archive.stem}/download",
                "preview_url": (f"/xiaoyue-mv/delivery/movie/{project_id}/{archive.stem}"
                                if movie.is_file() else None),
                "subtitle_url": (f"/xiaoyue-mv/delivery/subtitles/{project_id}/{archive.stem}"
                                 if movie.is_file() and subtitles.is_file() else None),
            })
        active = next((job for job in _DELIVERY_EXPORT_JOBS.values()
                       if job["project_id"] == project_id and job["state"] == "running"), None)
        return web.json_response({
            "exports": result,
            "active_job": _delivery_job_public(active) if active else None,
        })
    except (KeyError, TypeError, ValueError, OSError) as exc:
        return web.json_response({"error": str(exc)}, status=400)


@PromptServer.instance.routes.post("/xiaoyue-mv/preview/export/{job_id}/cancel")
async def cancel_mv_delivery_export(request: web.Request) -> web.Response:
    job_id = request.match_info["job_id"]
    job = _DELIVERY_EXPORT_JOBS.get(job_id)
    if not job:
        return web.json_response({"error": "导出任务不存在。"}, status=404)
    if job["state"] == "running":
        _DELIVERY_EXPORT_CANCEL[job_id].set()
        job["message"] = "正在取消导出…"
    return web.json_response(_delivery_job_public(job))


@PromptServer.instance.routes.get(
    "/xiaoyue-mv/preview/export/{project_id}/{job_id}/download")
async def download_mv_delivery_export(request: web.Request) -> web.StreamResponse:
    try:
        project_id = request.match_info["project_id"]
        job_id = request.match_info["job_id"]
        if not re.fullmatch(r"[0-9a-f]{32}", job_id):
            raise ValueError("导出编号无效。")
        project = await asyncio.to_thread(load_project, project_id)
        archive = (Path(project["paths"]["output"]) / "delivery" /
                   "exports" / f"{job_id}.zip")
        if not archive.is_file():
            raise ValueError("导出包不存在或尚未完成。")
        return web.FileResponse(archive, headers={
            "Content-Disposition": f'attachment; filename="{job_id}.zip"'})
    except (KeyError, TypeError, ValueError, OSError) as exc:
        return web.json_response({"error": str(exc)}, status=404)


@PromptServer.instance.routes.get("/xiaoyue-mv/delivery/movie/{project_id}/{job_id}")
async def preview_mv_delivery_movie(request: web.Request) -> web.StreamResponse:
    try:
        project_id = request.match_info["project_id"]
        job_id = request.match_info["job_id"]
        if not re.fullmatch(r"[0-9a-f]{32}", job_id):
            raise ValueError("导出编号无效。")
        project = await asyncio.to_thread(load_project, project_id)
        movie = (Path(project["paths"]["output"]) / "delivery" / "exports" /
                 job_id / "MV_无字成片.mp4")
        if not movie.is_file():
            raise ValueError("这次导出没有可预览的成片。")
        return web.FileResponse(movie, headers={
            "Content-Disposition": 'inline; filename="MV_movie.mp4"',
            "Content-Type": "video/mp4"})
    except (KeyError, TypeError, ValueError, OSError) as exc:
        return web.json_response({"error": str(exc)}, status=404)


@PromptServer.instance.routes.get("/xiaoyue-mv/delivery/subtitles/{project_id}/{job_id}")
async def preview_mv_delivery_subtitles(request: web.Request) -> web.Response:
    try:
        project_id = request.match_info["project_id"]
        job_id = request.match_info["job_id"]
        if not re.fullmatch(r"[0-9a-f]{32}", job_id):
            raise ValueError("导出编号无效。")
        project = await asyncio.to_thread(load_project, project_id)
        subtitles = (Path(project["paths"]["output"]) / "delivery" / "exports" /
                     job_id / "最终字幕.srt")
        if not subtitles.is_file():
            raise ValueError("这次导出没有可预览的字幕。")
        srt_text = await asyncio.to_thread(subtitles.read_text, encoding="utf-8-sig")
        return web.Response(text=srt_to_webvtt(srt_text), content_type="text/vtt",
                            charset="utf-8")
    except (KeyError, TypeError, ValueError, OSError) as exc:
        return web.json_response({"error": str(exc)}, status=404)


@PromptServer.instance.routes.post("/xiaoyue-mv/generation/start")
async def start_shot_generations(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        project_id = str(data.get("project_id") or "")
        plan = data.get("plan")
        requested = data.get("shot_ids")
        if not isinstance(plan, dict) or not isinstance(requested, list) or not requested:
            raise ValueError("请选择至少一个已规划镜头。")
        project = await asyncio.to_thread(load_project, project_id)
        if str((plan.get("project") or {}).get("id") or "") != project_id:
            raise ValueError("镜头计划与当前项目不匹配。")
        if plan.get("needs_compile"):
            raise ValueError("镜头计划尚未编译；请先保存并运行 MV 项目工作台节点。")
        _require_current_window_rule(plan)
        if str((plan.get("audio") or {}).get("fingerprint") or "") != str(
                (project["manifest"].get("audio") or {}).get("fingerprint") or ""):
            raise ValueError("镜头计划与当前项目母带不一致；请先重新运行规划节点。")
        shot_map = {str(shot.get("id") or ""): shot for shot in plan.get("shots", [])
                    if isinstance(shot, dict)}
        window_map: dict[str, list[dict[str, Any]]] = {}
        for window in plan.get("generation_windows", []):
            if isinstance(window, dict):
                window_map.setdefault(str(window.get("shot_id") or ""), []).append(window)
        selected = {str(value or "") for value in requested}
        if len(selected) != len(requested) or not selected.issubset(shot_map):
            raise ValueError("选择的镜头 ID 无效或重复。")
        for shot_id in selected:
            if not str(shot_map[shot_id].get("prompt") or "").strip():
                raise ValueError(f"镜头 {shot_id} 没有 H3 提示词。")
            if len(window_map.get(shot_id, [])) != 1:
                raise ValueError(f"镜头 {shot_id} 不是单个 H3 窗口；请先在时间轴切镜并重新运行规划节点。")
            if shot_map[shot_id].get("audio_references"):
                raise ValueError(f"镜头 {shot_id} 有尚未接线的用户音频引用；不能错位绑定原曲。")
        canvas_workflow = canvas_backbone(data.get("canvas_workflow"))
        graph_fingerprint = canvas_workflow_fingerprint(canvas_workflow)
        ordered_ids = [str(shot["id"]) for shot in plan["shots"]
                       if str(shot.get("id") or "") in selected]
        parent_by_id = {
            str(window["shot_id"]): next(
                (str(candidate["shot_id"])
                 for windows in window_map.values() for candidate in windows
                 if candidate.get("editorial_label") == window.get("continuity_parent_label")),
                "")
            for windows in window_map.values() for window in windows
        }
        async with _SHOT_GENERATION_START_LOCK:
            conflict = generation_job_conflict(
                list(_SHOT_GENERATION_JOBS.values()), project_id, selected, parent_by_id)
            if conflict:
                return web.json_response({"error": conflict}, status=409)
            if any(job.get("state") in ("queued", "running") for job in
                   [*_OUTLINE_JOBS.values(), *_H3_PROMPT_JOBS.values()]):
                return web.json_response({"error": "提示词任务仍在运行；请等它完成。"}, status=409)
            # Recover orphan records only when this project has no live jobs;
            # otherwise recovery would mark another queued clip as failed.
            has_active = any(job.get("project_id") == project_id
                             and job.get("state") == "running"
                             for job in _SHOT_GENERATION_JOBS.values())
            version_state = with_stale_flags(
                await asyncio.to_thread(
                    load_versions if has_active else recover_interrupted_versions,
                    project_id),
                _shot_window_fingerprints(plan))
            for shot_id in selected:
                parent_label = str(window_map[shot_id][0].get("continuity_parent_label") or "")
                if not parent_label:
                    continue
                parent_window = next((item for windows in window_map.values()
                                      for item in windows
                                      if item.get("editorial_label") == parent_label), None)
                if parent_window is None:
                    raise ValueError(f"找不到续写前段 {parent_label} 的生成窗口。")
                parent_id = str(parent_window.get("shot_id") or "")
                if parent_id in selected:
                    continue
                resolve_parent_version(
                    version_state, _stable_shot_id(parent_window), parent_label)
            if any(job.get("state") in ("queued", "running") for job in
                   [*_OUTLINE_JOBS.values(), *_H3_PROMPT_JOBS.values()]):
                return web.json_response({
                    "error": "提示词任务仍在排队或生成；请等它完成。",
                }, status=409)
            job_id = uuid.uuid4().hex
            _SHOT_GENERATION_JOBS[job_id] = {
                "job_id": job_id, "project_id": project_id, "state": "running",
                "shot_ids": ordered_ids,
                "started_at": time.time(), "total_count": len(ordered_ids),
                "completed_count": 0, "current_shot_id": "",
                "current_prompt_id": "", "current_version_id": "",
                "cancel_requested": False, "results": [],
                "workflow_fingerprint": graph_fingerprint,
            }
            _SHOT_GENERATION_TASKS[job_id] = asyncio.create_task(
                _run_shot_generation_job(job_id, {
                    "project": project, "plan": plan, "shot_ids": ordered_ids,
                    "canvas_workflow": canvas_workflow,
                }))
        return web.json_response({"job_id": job_id, "state": "running"}, status=202)
    except (json.JSONDecodeError, TypeError, ValueError, KeyError) as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except Exception:
        logging.exception("Xiaoyue MV shot generation start failed")
        return web.json_response({"error": "无法启动镜头生成。"}, status=500)


@PromptServer.instance.routes.get("/xiaoyue-mv/generation/jobs/{job_id}")
async def get_shot_generation_job(request: web.Request) -> web.Response:
    job = _SHOT_GENERATION_JOBS.get(request.match_info["job_id"])
    if job is None:
        return web.json_response({"error": "镜头生成任务不存在或服务已重启。"}, status=404)
    return web.json_response(job)


@PromptServer.instance.routes.post("/xiaoyue-mv/generation/jobs/{job_id}/cancel")
async def cancel_shot_generation_job(request: web.Request) -> web.Response:
    job = _SHOT_GENERATION_JOBS.get(request.match_info["job_id"])
    if job is None:
        return web.json_response({"error": "镜头生成任务不存在。"}, status=404)
    if job["state"] == "running":
        job["cancel_requested"] = True
    return web.json_response({"job_id": job["job_id"],
                              "state": job["state"],
                              "cancel_requested": job["cancel_requested"]})


@PromptServer.instance.routes.post("/xiaoyue-mv/generation/select")
async def select_shot_generation(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        project_id = str(data.get("project_id") or "")
        shot_id = str(data.get("shot_id") or "")
        plan = await asyncio.to_thread(load_project_plan, project_id)
        shot = next((item for item in (plan or {}).get("shots", [])
                     if isinstance(item, dict) and item.get("id") == shot_id), None)
        if shot is None:
            raise ValueError("当前计划中找不到该镜头；不能选择已删除镜头的版本。")
        state = await asyncio.to_thread(
            select_version, project_id, _stable_shot_id(shot),
            str(data.get("version_id") or ""))
        return web.json_response({"versions": with_stale_flags(
            state, _shot_window_fingerprints(plan or {}))})
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        return web.json_response({"error": str(exc)}, status=400)


@PromptServer.instance.routes.post("/xiaoyue-mv/generation/preflight")
async def preflight_shot_generation(request: web.Request) -> web.Response:
    """Validate the composed graph with ComfyUI without queueing a sampler."""
    try:
        data = await request.json()
        project_id = str(data.get("project_id") or "")
        shot_id = str(data.get("shot_id") or "")
        plan = data.get("plan") or {}
        project = await asyncio.to_thread(load_project, project_id)
        if (plan.get("project") or {}).get("id") != project_id:
            raise ValueError("镜头计划与项目不匹配。")
        _require_current_window_rule(plan)
        shot = next((item for item in plan.get("shots", [])
                     if item.get("id") == shot_id), None)
        windows = [item for item in plan.get("generation_windows", [])
                   if item.get("shot_id") == shot_id]
        if shot is None or len(windows) != 1:
            raise ValueError("预检只支持一个已编译的镜头窗口。")
        if shot.get("audio_references"):
            raise ValueError("当前镜头有尚未接线的用户音频引用；预检和生成都会阻止错位绑定原曲。")
        window = windows[0]
        state = with_stale_flags(
            await asyncio.to_thread(load_versions, project_id),
            _shot_window_fingerprints(plan))
        _parent_id, parent_path, parent_frames, parent_lead = _shot_generation_parent(
            plan, window, state, {}, project)
        master = (((project["manifest"].get("audio") or {}).get("master") or {})
                  .get("source") or {}).get("path")
        if not master:
            raise ValueError("项目没有母带文件。")
        audio_name = f"xiaoyue_mv_projects/{project_id}/{str(master).replace(chr(92), '/')}"
        ui = canvas_backbone(data.get("canvas_workflow"))
        requested_seed = shot_seed_setting(ui)
        assets = _shot_generation_assets(plan, shot)
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
            info = await _shot_generation_request(session, "GET", "/object_info")
        graph = compose_shot_workflow(
            ui, info, prompt=str(shot.get("prompt") or ""), window=window,
            generation_spec=plan["generation_spec"],
            audio_input_name=audio_name, visual_inputs=assets,
            output_prefix=f"xiaoyue_mv_projects/{project_id}/generations/preflight",
            seed=0 if requested_seed < 0 else requested_seed,
            parent_video_path=parent_path, parent_deliver_frames=parent_frames,
            parent_deliver_from_raw_frame=parent_lead)
        import execution

        validation = await execution.validate_prompt(str(uuid.uuid4()), graph, None)
        return web.json_response({
            "valid": bool(validation[0]),
            "error": validation[1] if not validation[0] else None,
            "node_errors": validation[3],
            "node_count": len(graph),
            "workflow_fingerprint": canvas_workflow_fingerprint(ui),
        })
    except (json.JSONDecodeError, TypeError, ValueError, KeyError) as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except Exception:
        logging.exception("Xiaoyue MV shot preflight failed")
        return web.json_response({"error": "镜头工作流预检失败。"}, status=500)


@PromptServer.instance.routes.get("/xiaoyue-mv/assets/input")
async def list_input_assets(request: web.Request) -> web.Response:
    try:
        query = request.query.get("q", "")
        limit = int(request.query.get("limit", "600"))
        assets = await asyncio.to_thread(_list_input_assets, query, limit)
        return web.json_response({"assets": assets, "count": len(assets)})
    except (TypeError, ValueError) as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except Exception:
        logging.exception("Xiaoyue input asset listing failed")
        return web.json_response({"error": "读取 ComfyUI input 资产失败。"}, status=500)


@PromptServer.instance.routes.post("/xiaoyue-mv/assets/upload")
async def upload_project_asset(request: web.Request) -> web.Response:
    target: Path | None = None
    try:
        reader = await request.multipart()
        upload = None
        project_id = ""
        while True:
            part = await reader.next()
            if part is None:
                break
            if part.name == "project_id":
                project_id = (await part.text()).strip()
                continue
            if part.name == "file" and part.filename:
                upload = part
                break
        if upload is None:
            raise ValueError("没有收到要导入的资产文件。")
        if not project_id:
            raise ValueError("请先初始化并选择 MV 项目。")
        filename = _safe_upload_filename(upload.filename)
        root = Path(folder_paths.get_input_directory()).resolve()
        project = await asyncio.to_thread(load_project, project_id)
        directory = (Path(project["paths"]["input"]) / "assets").resolve()
        directory.relative_to(root)
        directory.mkdir(parents=True, exist_ok=True)
        target = _available_upload_path(directory, filename)
        written = 0
        with target.open("wb") as output:
            while True:
                chunk = await upload.read_chunk(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > ASSET_UPLOAD_LIMIT:
                    raise ValueError("单个资产不能超过 512 MB。")
                output.write(chunk)
        if written <= 0:
            raise ValueError("资产文件为空。")
        return web.json_response({"asset": _asset_descriptor(target, root)})
    except (TypeError, ValueError, web.HTTPException) as exc:
        if target and target.exists():
            target.unlink(missing_ok=True)
        return web.json_response({"error": str(exc)}, status=400)
    except Exception:
        if target and target.exists():
            target.unlink(missing_ok=True)
        logging.exception("Xiaoyue project asset upload failed")
        return web.json_response({"error": "导入项目资产失败。"}, status=500)
