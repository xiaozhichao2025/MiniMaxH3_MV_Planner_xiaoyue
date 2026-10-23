from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import torch


_SAFE_EXTENSION = re.compile(r"^\.[A-Za-z0-9]{1,8}$")


def audio_fingerprint(waveform: torch.Tensor, sample_rate: int) -> str:
    """Fingerprint decoded audio content, independent of the container name."""
    if waveform.ndim == 3:
        waveform = waveform[0]
    if waveform.ndim == 1:
        waveform = waveform.unsqueeze(0)
    mono = waveform.mean(dim=0)
    count = min(8192, int(mono.shape[-1]))
    indices = torch.linspace(0, mono.shape[-1] - 1, count).round().to(torch.long)
    sample = (mono[indices].clamp(-1, 1) * 32767).round().to(torch.int16).numpy()
    digest = hashlib.sha256()
    digest.update(f"{int(sample_rate)}:{tuple(waveform.shape)}".encode("ascii"))
    digest.update(sample.tobytes())
    return digest.hexdigest()


def load_audio_path(path: Path) -> dict[str, Any]:
    """Decode a project-owned media file with ComfyUI's audio decoder."""
    from comfy_extras.nodes_audio import load

    waveform, sample_rate = load(str(path))
    if waveform.ndim == 1:
        waveform = waveform.unsqueeze(0)
    if waveform.ndim != 2 or waveform.shape[-1] < 1:
        raise ValueError("音频文件解码后没有有效采样。")
    return {
        "waveform": waveform.unsqueeze(0),
        "sample_rate": int(sample_rate),
    }


def describe_audio(path: Path, *, relative_path: str, original_name: str) -> dict[str, Any]:
    audio = load_audio_path(path)
    waveform = audio["waveform"][0]
    sample_rate = int(audio["sample_rate"])
    total_samples = int(waveform.shape[-1])
    return {
        "fingerprint": audio_fingerprint(waveform, sample_rate),
        "sample_rate": sample_rate,
        "total_samples": total_samples,
        "duration_seconds": total_samples / float(sample_rate),
        "source": {
            "path": str(relative_path).replace("\\", "/"),
            "format": "media",
            "original_name": str(original_name or path.name),
        },
    }


def source_extension(filename: str) -> str:
    extension = Path(str(filename or "")).suffix.lower()
    return extension if _SAFE_EXTENSION.fullmatch(extension) else ".bin"


def resolve_project_audio_path(project: dict[str, Any], role: str) -> Path | None:
    manifest = project.get("manifest") if isinstance(project.get("manifest"), dict) else {}
    audio = manifest.get("audio") if isinstance(manifest.get("audio"), dict) else {}
    item = audio.get(role) if isinstance(audio.get(role), dict) else {}
    source = item.get("source") if isinstance(item.get("source"), dict) else {}
    relative_path = str(source.get("path") or "").replace("\\", "/").strip("/")
    if not relative_path:
        return None
    root = Path(project["paths"]["input"]).resolve()
    path = (root / relative_path).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"项目 {role} 音频路径越界。") from exc
    if not path.is_file():
        raise ValueError(f"项目记录中的 {role} 音频文件不存在：{relative_path}")
    return path


def load_project_audio(project: dict[str, Any], role: str) -> dict[str, Any] | None:
    path = resolve_project_audio_path(project, role)
    return load_audio_path(path) if path else None
