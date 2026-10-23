from __future__ import annotations

from pathlib import Path
from typing import Any

from .creative_outline import selected_outline_assets
from .h3_prompt import MAX_SHOT_VISUAL_ASSETS


_IMAGE_SUFFIXES = {
    ".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp",
}
_VIDEO_SUFFIXES = {".avi", ".mkv", ".mov", ".mp4", ".webm"}
# QwenTE_ImageInfer is called with 最大边长=1024. Decode to that ceiling
# up front so a project with eight references does not temporarily hold eight
# full-resolution photos in browser-job memory only to resize them again.
_MAX_DECODE_EDGE = 1024


def _safe_input_path(source: dict[str, Any], input_root: Path) -> Path:
    filename = str(source.get("filename") or "").strip()
    if not filename or Path(filename).name != filename:
        raise ValueError("总纲视觉资产的文件名无效。")
    subfolder_text = str(source.get("subfolder") or "").strip().replace("\\", "/")
    subfolder = Path(subfolder_text) if subfolder_text else Path()
    if subfolder.is_absolute() or ".." in subfolder.parts:
        raise ValueError("总纲视觉资产的子目录无效。")
    root = input_root.resolve()
    path = (root / subfolder / filename).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("总纲视觉资产越出了 ComfyUI input 目录。") from exc
    if not path.is_file():
        raise ValueError(f"总纲视觉资产不存在：{filename}")
    return path


def _resize_rgb_array(array: Any) -> Any:
    from PIL import Image
    import numpy as np

    image = Image.fromarray(array).convert("RGB")
    if max(image.size) > _MAX_DECODE_EDGE:
        image.thumbnail((_MAX_DECODE_EDGE, _MAX_DECODE_EDGE), Image.Resampling.LANCZOS)
    return np.asarray(image, dtype=np.float32) / 255.0


def _load_image(path: Path) -> Any:
    from PIL import Image, ImageOps
    import numpy as np

    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        if max(image.size) > _MAX_DECODE_EDGE:
            image.thumbnail((_MAX_DECODE_EDGE, _MAX_DECODE_EDGE), Image.Resampling.LANCZOS)
        return np.asarray(image, dtype=np.float32) / 255.0


def _load_video_cover(path: Path) -> Any:
    try:
        import cv2
    except ImportError as exc:
        raise ValueError(
            f"无法读取视频资产 {path.name}：当前 ComfyUI Python 缺少 OpenCV。"
        ) from exc
    capture = cv2.VideoCapture(str(path))
    try:
        if not capture.isOpened():
            raise ValueError(f"无法打开视频资产：{path.name}")
        frame_count = max(0, int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0))
        candidates = [int(frame_count * 0.2), int(frame_count * 0.5), 0] if frame_count else [0]
        frame = None
        for frame_index in candidates:
            capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            okay, candidate = capture.read()
            if okay and candidate is not None:
                frame = candidate
                break
        if frame is None:
            raise ValueError(f"视频资产没有可读取画面：{path.name}")
        return _resize_rgb_array(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    finally:
        capture.release()


def load_outline_visual_inputs(
        plan: dict[str, Any],
        input_root: str | Path,
) -> tuple[list[Any], list[dict[str, Any]]]:
    import torch

    selected = selected_outline_assets(plan)
    images: list[Any] = []
    metadata: list[dict[str, Any]] = []
    failures = []
    root = Path(input_root)
    for index, asset in enumerate(selected):
        name = str(asset.get("name") or f"资产 {index + 1}")
        source = asset.get("source") if isinstance(asset.get("source"), dict) else {}
        try:
            path = _safe_input_path(source, root)
            suffix = path.suffix.lower()
            if suffix in _IMAGE_SUFFIXES:
                array = _load_image(path)
                media_type = "image"
            elif suffix in _VIDEO_SUFFIXES:
                array = _load_video_cover(path)
                media_type = "video_cover"
            else:
                raise ValueError(f"不支持的视觉资产格式：{path.suffix or '无扩展名'}")
            images.append(torch.from_numpy(array).unsqueeze(0))
            metadata.append({
                "label": f"视觉资产 {chr(65 + index)}",
                "name": name,
                "media_type": media_type,
                "width": int(array.shape[1]),
                "height": int(array.shape[0]),
            })
        except Exception as exc:
            failures.append(f"{name}：{exc}")
    if failures:
        images.clear()
        raise ValueError("总纲视觉资产读取失败；未启动推理。" + "；".join(failures))
    return images, metadata


def load_shot_visual_inputs(
        plan: dict[str, Any],
        shot_id: str,
        input_root: str | Path,
) -> tuple[list[Any], list[dict[str, Any]]]:
    """Load only the target shot's ordered bindings for H3 prompt reasoning."""
    import torch

    shots = plan.get("shots") if isinstance(plan, dict) else None
    shot = next((
        item for item in shots or []
        if isinstance(item, dict) and str(item.get("id") or "") == str(shot_id)
    ), None)
    if shot is None:
        raise ValueError(f"找不到目标镜头：{shot_id}。")
    assets = plan.get("assets") if isinstance(plan.get("assets"), list) else []
    asset_by_id = {
        str(asset.get("id") or ""): asset
        for asset in assets if isinstance(asset, dict) and asset.get("id")
    }
    bindings = sorted(
        (
            binding for binding in shot.get("asset_bindings", [])
            if isinstance(binding, dict)
            and str(binding.get("asset_id") or "") in asset_by_id
        ),
        key=lambda binding: int(binding.get("order") or 0),
    )
    if len(bindings) > MAX_SHOT_VISUAL_ASSETS:
        raise ValueError(
            f"本镜头绑定了 {len(bindings)} 个视觉资产；"
            f"QwenTE 单次最多读取 {MAX_SHOT_VISUAL_ASSETS} 个。"
        )
    images: list[Any] = []
    metadata: list[dict[str, Any]] = []
    failures = []
    root = Path(input_root)
    for index, binding in enumerate(bindings):
        asset = asset_by_id[str(binding.get("asset_id") or "")]
        name = str(asset.get("name") or f"资产 {index + 1}")
        source = asset.get("source") if isinstance(asset.get("source"), dict) else {}
        try:
            path = _safe_input_path(source, root)
            suffix = path.suffix.lower()
            if suffix in _IMAGE_SUFFIXES:
                array = _load_image(path)
                media_type = "image"
            elif suffix in _VIDEO_SUFFIXES:
                array = _load_video_cover(path)
                media_type = "video_cover"
            else:
                raise ValueError(f"不支持的视觉资产格式：{path.suffix or '无扩展名'}")
            images.append(torch.from_numpy(array).unsqueeze(0))
            metadata.append({
                "label": f"本镜头资产 {index + 1}",
                "name": name,
                "media_type": media_type,
                "width": int(array.shape[1]),
                "height": int(array.shape[0]),
            })
        except Exception as exc:
            failures.append(f"{name}：{exc}")
    if failures:
        images.clear()
        raise ValueError("本镜头视觉资产读取失败；未覆盖任何提示词。" + "；".join(failures))
    return images, metadata
