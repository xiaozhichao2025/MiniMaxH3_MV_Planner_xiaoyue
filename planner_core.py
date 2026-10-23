from __future__ import annotations

import bisect
import json
import math
import re
import uuid
from copy import deepcopy
from typing import Any, Iterable


FPS = 24
H3_STEP = 17
H3_OFFSET = 5
H3_WINDOW_PRESETS = (124, 141, 158, 175, 192, 209, 226, 243, 260, 277, 294, 311, 328, 345, 362)
H3_DEFAULT_MAX_FRAMES = 226
H3_MAX_FRAMES = max(H3_WINDOW_PRESETS)
H3_MAX_SECONDS = H3_MAX_FRAMES / FPS
CONTINUATION_WINDOW_RULE_REVISION = 3
SCHEMA = "xiaoyue.mvplan/1.0"
TRANSITION_TYPES = {"hard_cut", "long_take", "crossfade"}


def legal_h3_frames(required_frames: int) -> int:
    required = max(1, int(required_frames))
    if required <= H3_OFFSET:
        return H3_OFFSET
    return H3_OFFSET + H3_STEP * math.ceil((required - H3_OFFSET) / H3_STEP)


def frame_to_sample(frame: int, sample_rate: int) -> int:
    return int(round(int(frame) * int(sample_rate) / FPS))


def sample_to_frame(sample: int, sample_rate: int) -> int:
    return int(round(int(sample) * FPS / int(sample_rate)))


def seconds_to_frame(seconds: float) -> int:
    return int(round(float(seconds) * FPS))


def frame_to_seconds(frame: int) -> float:
    return round(int(frame) / FPS, 6)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _timestamp_seconds(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return max(0.0, float(value))
    if not isinstance(value, str):
        return None
    text = value.strip().replace(",", ".")
    if not text:
        return None
    try:
        if ":" not in text:
            return max(0.0, float(text))
        parts = [float(part) for part in text.split(":" )]
    except ValueError:
        return None
    if len(parts) == 2:
        return max(0.0, parts[0] * 60.0 + parts[1])
    if len(parts) == 3:
        return max(0.0, parts[0] * 3600.0 + parts[1] * 60.0 + parts[2])
    return None


def _cue(start: Any, end: Any, text: Any, cue_id: str = "",
         confidence: Any = None) -> dict[str, Any] | None:
    start_seconds = _timestamp_seconds(start)
    end_seconds = _timestamp_seconds(end)
    if start_seconds is None or end_seconds is None or end_seconds <= start_seconds:
        return None
    clean_text = re.sub(r"<[^>]+>", "", str(text or "")).strip()
    start_frame = seconds_to_frame(start_seconds)
    end_frame = seconds_to_frame(end_seconds)
    stable_id = str(cue_id or f"cue_f{start_frame:07d}_f{end_frame:07d}")
    cue = {
        "id": stable_id,
        "start_seconds": round(start_seconds, 6),
        "end_seconds": round(end_seconds, 6),
        "start_frame": start_frame,
        "end_frame": end_frame,
        "text": clean_text,
    }
    if isinstance(confidence, (int, float)) and math.isfinite(float(confidence)):
        cue["confidence"] = round(float(confidence), 6)
    return cue


def _json_cues(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        for key in ("cues", "segments", "subtitles", "items"):
            if isinstance(value.get(key), list):
                value = value[key]
                break
        else:
            value = [value]
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if not isinstance(item, dict):
            continue
        start = item.get("start_seconds", item.get("start", item.get("from")))
        end = item.get("end_seconds", item.get("end", item.get("to")))
        text = item.get("text", item.get("content", item.get("word", "")))
        cue_id = item.get("cue_id", item.get("id", ""))
        parsed = _cue(start, end, text, str(cue_id or ""), item.get("confidence"))
        if parsed:
            result.append(parsed)
    return result


def parse_timed_text(source: str | None) -> dict[str, Any]:
    text = str(source or "").strip()
    if not text:
        return {"format": "none", "cues": [], "untimed_text": ""}

    if text[0] in "[{":
        try:
            cues = _json_cues(json.loads(text))
        except (json.JSONDecodeError, TypeError):
            cues = []
        if cues:
            return {"format": "json", "cues": cues, "untimed_text": ""}

    cues = []
    blocks = re.split(r"\r?\n\s*\r?\n", text)
    arrow = re.compile(
        r"(?P<start>\d{1,2}:\d{2}(?::\d{2})?[,.]\d+)\s*-->\s*"
        r"(?P<end>\d{1,2}:\d{2}(?::\d{2})?[,.]\d+)")
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timing_index = next((i for i, line in enumerate(lines) if arrow.search(line)), None)
        if timing_index is None:
            continue
        match = arrow.search(lines[timing_index])
        body = " ".join(lines[timing_index + 1:])
        parsed = _cue(match.group("start"), match.group("end"), body)
        if parsed:
            cues.append(parsed)
    if cues:
        return {"format": "srt", "cues": cues, "untimed_text": ""}

    lrc_entries: list[tuple[float, str]] = []
    lrc_pattern = re.compile(r"\[(\d{1,3}):(\d{2}(?:\.\d{1,3})?)\]")
    for line in text.splitlines():
        matches = list(lrc_pattern.finditer(line))
        if not matches:
            continue
        lyric = lrc_pattern.sub("", line).strip()
        for match in matches:
            lrc_entries.append((int(match.group(1)) * 60 + float(match.group(2)), lyric))
    if lrc_entries:
        lrc_entries.sort(key=lambda item: item[0])
        for index, (start, lyric) in enumerate(lrc_entries):
            next_start = (lrc_entries[index + 1][0]
                          if index + 1 < len(lrc_entries) else start + 4.0)
            end = max(start + 0.08, next_start)
            parsed = _cue(start, end, lyric)
            if parsed:
                cues.append(parsed)
        return {"format": "lrc", "cues": cues, "untimed_text": ""}

    line_pattern = re.compile(
        r"^\s*[\[(](?P<start>[0-9:.,]+)\s*[,\-]\s*"
        r"(?P<end>[0-9:.,]+)[\])]\s*(?P<text>.*)$")
    for line in text.splitlines():
        match = line_pattern.match(line)
        if not match:
            continue
        parsed = _cue(match.group("start"), match.group("end"), match.group("text"))
        if parsed:
            cues.append(parsed)
    if cues:
        return {"format": "timestamp", "cues": cues, "untimed_text": ""}

    return {"format": "plain", "cues": [], "untimed_text": text}


def _existing_master_cues(existing: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(existing, dict):
        return []
    subtitles = existing.get("subtitles", {})
    if not isinstance(subtitles, dict):
        return []
    cues = subtitles.get("master_cues", subtitles.get("cues", []))
    return [cue for cue in cues if isinstance(cue, dict)] if isinstance(cues, list) else []


def _compile_master_cues(parsed_cues: list[dict[str, Any]], *,
                         existing: dict[str, Any] | None,
                         sample_rate: int, total_frames: int,
                         total_samples: int) -> list[dict[str, Any]]:
    """Build one canonical subtitle table and preserve user corrections by cue identity."""
    old_cues = _existing_master_cues(existing)
    old_by_id = {str(cue.get("id")): cue for cue in old_cues if cue.get("id")}
    old_by_range = {
        (
            int(cue.get("absolute_start_frame", cue.get("start_frame", 0))),
            int(cue.get("absolute_end_frame", cue.get("end_frame", 0))),
        ): cue
        for cue in old_cues
    }
    source_cues = parsed_cues or old_cues
    result: list[dict[str, Any]] = []
    used_ids: dict[str, int] = {}
    for source in source_cues:
        start = max(0, min(
            total_frames,
            int(source.get("absolute_start_frame", source.get("start_frame", 0))),
        ))
        end = max(start + 1, min(
            total_frames,
            int(source.get("absolute_end_frame", source.get("end_frame", start + 1))),
        ))
        if start >= total_frames or end <= start:
            continue
        base_id = str(source.get("id") or f"cue_f{start:07d}_f{end:07d}")
        duplicate = used_ids.get(base_id, 0)
        used_ids[base_id] = duplicate + 1
        cue_id = base_id if duplicate == 0 else f"{base_id}_{duplicate + 1:02d}"
        old = old_by_id.get(cue_id) or old_by_range.get((start, end))
        incoming_text = str(
            source.get("corrected_text", source.get("text", source.get("original_text", "")))
        ).strip()
        old_original = str((old or {}).get("original_text", (old or {}).get("text", ""))).strip()
        old_corrected = str((old or {}).get("corrected_text", (old or {}).get("text", ""))).strip()
        if old and incoming_text in {old_original, old_corrected}:
            original_text = old_original or incoming_text
            corrected_text = old_corrected or incoming_text
            revision = max(0, int(old.get("revision", 0)))
        else:
            original_text = str(source.get("original_text", incoming_text)).strip()
            corrected_text = incoming_text or original_text
            revision = max(0, int(source.get("revision", 0)))
        cue = {
            "id": cue_id,
            "absolute_start_frame": start,
            "absolute_end_frame": end,
            "absolute_start_sample": min(total_samples, frame_to_sample(start, sample_rate)),
            "absolute_end_sample": min(total_samples, frame_to_sample(end, sample_rate)),
            "start_seconds": frame_to_seconds(start),
            "end_seconds": frame_to_seconds(end),
            "original_text": original_text,
            "corrected_text": corrected_text,
            "revision": revision,
        }
        confidence = source.get("confidence", (old or {}).get("confidence"))
        if isinstance(confidence, (int, float)) and math.isfinite(float(confidence)):
            cue["confidence"] = round(float(confidence), 6)
        result.append(cue)
    return sorted(result, key=lambda cue: (
        cue["absolute_start_frame"], cue["absolute_end_frame"], cue["id"],
    ))


def _project_master_cues(shot: dict[str, Any],
                         master_cues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project canonical cues into a shot without copying editable subtitle text."""
    start = int(shot["start_frame"])
    end = int(shot["end_frame"])
    projections = []
    for cue in master_cues:
        cue_start = int(cue["absolute_start_frame"])
        cue_end = int(cue["absolute_end_frame"])
        clip_start = max(start, cue_start)
        clip_end = min(end, cue_end)
        if clip_end <= clip_start:
            continue
        cue_frames = max(1, cue_end - cue_start)
        projections.append({
            "shot_id": shot["id"],
            "source_cue_id": cue["id"],
            "clip_abs_start_frame": clip_start,
            "clip_abs_end_frame": clip_end,
            "relative_start_frame": clip_start - start,
            "relative_end_frame": clip_end - start,
            "is_partial": clip_start != cue_start or clip_end != cue_end,
            "overlap_ratio": round((clip_end - clip_start) / cue_frames, 6),
        })
    return projections


def _single_line_subtitle_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _subtitle_cues_in_range(master_cues: list[dict[str, Any]],
                            start: int, end: int) -> list[dict[str, Any]]:
    if end <= start:
        return []
    return [
        cue for cue in master_cues
        if min(end, int(cue["absolute_end_frame"]))
        > max(start, int(cue["absolute_start_frame"]))
    ]


def _join_subtitle_context_parts(*values: Any) -> str:
    parts = []
    for value in values:
        text = _single_line_subtitle_text(value)
        if text and (not parts or parts[-1] != text):
            parts.append(text)
    return " ".join(parts)


def _compile_shot_subtitle_context(shot: dict[str, Any],
                                   windows: list[dict[str, Any]],
                                   master_cues: list[dict[str, Any]],
                                   total_frames: int) -> dict[str, Any]:
    """Aggregate lyrics for H3's leading/core/trailing audio envelope.

    Cue text remains window-level reference data.  The AI receives only the
    combined words, never fabricated shot-relative or word-level timestamps.
    """
    main_start = max(0, min(total_frames, int(shot["start_frame"])))
    main_end = max(main_start, min(total_frames, int(shot["end_frame"])))
    source_start = min(
        (int(window.get("source_start_frame", main_start)) for window in windows),
        default=main_start,
    )
    source_end = max(
        (int(window.get("source_end_frame", main_end)) for window in windows),
        default=main_end,
    )
    source_start = max(0, min(main_start, source_start))
    source_end = min(total_frames, max(main_end, source_end))

    leading_cues = _subtitle_cues_in_range(master_cues, source_start, main_start)
    main_cues = _subtitle_cues_in_range(master_cues, main_start, main_end)
    trailing_cues = _subtitle_cues_in_range(master_cues, main_end, source_end)
    subtitle_track = shot.get("subtitle_track", {})
    leading_text = _single_line_subtitle_text(
        subtitle_track.get("leading_text", "")) if source_start < main_start else ""
    main_text = _single_line_subtitle_text(subtitle_track.get("text", ""))
    trailing_text = _single_line_subtitle_text(
        subtitle_track.get("trailing_text", "")) if source_end > main_end else ""
    ai_text = _join_subtitle_context_parts(
        leading_text, main_text, trailing_text)

    def section(start: int, end: int, text: str,
                cues: list[dict[str, Any]], role: str) -> dict[str, Any]:
        return {
            "role": role,
            "range": [start, end],
            "frames": max(0, end - start),
            "text": text,
            "reference_cue_ids": [str(cue["id"]) for cue in cues],
        }

    return {
        "policy": "leading_main_trailing_generation_context",
        "source_range": [source_start, source_end],
        "leading_extension": {
            **section(source_start, main_start, leading_text, leading_cues,
                      "leading_extension"),
            "text_authority": "shots[].subtitle_track.leading_text",
            "text_entry": "user",
        },
        "main": {
            **section(main_start, main_end, main_text, main_cues, "formal_subtitle"),
            "text_authority": "shots[].subtitle_track.text",
            "is_final_subtitle_output": True,
        },
        "trailing_extension": {
            **section(main_end, source_end, trailing_text, trailing_cues,
                      "trailing_extension"),
            "text_authority": "shots[].subtitle_track.trailing_text",
            "text_entry": "user",
        },
        "ai_dialogue_text": ai_text,
        "ai_dialogue_policy": "text_only_no_shot_relative_timestamps",
        "visible_text_policy": "hidden_context_not_on_screen_typography",
    }


def estimate_bpm(onset: Iterable[float], envelope_rate: float,
                 known_bpm: float = 0.0) -> dict[str, Any]:
    values = [max(0.0, float(value)) for value in onset]
    if known_bpm > 0:
        bpm = _clamp(float(known_bpm), 30.0, 400.0)
        confidence = 1.0
    elif len(values) < max(16, int(envelope_rate * 4)) or max(values, default=0.0) <= 1e-8:
        return {"bpm": 0.0, "confidence": 0.0, "period": 0, "phase": 0}
    else:
        mean = sum(values) / len(values)
        centered = [value - mean for value in values]
        lag_low = max(1, int(round(envelope_rate * 60.0 / 180.0)))
        lag_high = min(len(values) // 3, int(round(envelope_rate * 60.0 / 60.0)))
        scored = []
        for lag in range(lag_low, lag_high + 1):
            left = centered[:-lag]
            right = centered[lag:]
            numerator = sum(a * b for a, b in zip(left, right))
            denominator = math.sqrt(
                max(1e-12, sum(a * a for a in left) * sum(b * b for b in right)))
            correlation = numerator / denominator
            bpm_for_lag = envelope_rate * 60.0 / lag
            tempo_prior = 1.0 - 0.08 * abs(math.log2(bpm_for_lag / 110.0))
            scored.append((correlation * tempo_prior, lag))
        scored.sort(reverse=True)
        best_score, period = scored[0]
        middle = sorted(score for score, _lag in scored)[len(scored) // 2]
        confidence = _clamp((best_score - middle) * 3.0, 0.0, 1.0)
        bpm = envelope_rate * 60.0 / period

    period = max(1, int(round(envelope_rate * 60.0 / bpm)))
    phase_scores = []
    for phase in range(period):
        indices = range(phase, len(values), period)
        selected = [values[index] for index in indices]
        phase_scores.append((sum(selected) / max(1, len(selected)), phase))
    phase = max(phase_scores)[1] if phase_scores else 0
    return {
        "bpm": round(float(bpm), 3),
        "confidence": round(float(confidence), 4),
        "period": period,
        "phase": phase,
    }


def beat_frames(bpm_result: dict[str, Any], envelope_rate: float,
                total_frames: int) -> list[int]:
    bpm = float(bpm_result.get("bpm", 0.0))
    if bpm <= 0:
        return []
    period_seconds = 60.0 / bpm
    phase_seconds = int(bpm_result.get("phase", 0)) / float(envelope_rate)
    result = []
    time = phase_seconds
    while time * FPS <= total_frames:
        frame = seconds_to_frame(time)
        if 0 < frame < total_frames and (not result or frame != result[-1]):
            result.append(frame)
        time += period_seconds
    return result


def _merge_candidate(store: dict[int, dict[str, Any]], frame: int,
                     score: float, reason: str, total_frames: int) -> None:
    frame = max(0, min(int(total_frames), int(frame)))
    record = store.setdefault(frame, {"frame": frame, "score": 0.0, "reasons": []})
    record["score"] = max(float(record["score"]), float(score))
    if reason and reason not in record["reasons"]:
        record["reasons"].append(reason)


def build_protection_candidates(total_frames: int, frame_energy: list[float],
                                beats: list[int], cues: list[dict[str, Any]],
                                section_changes: list[int] | None = None) -> list[dict[str, Any]]:
    store: dict[int, dict[str, Any]] = {}
    _merge_candidate(store, 0, 1.5, "母带开始", total_frames)
    _merge_candidate(store, total_frames, 1.5, "母带结束", total_frames)

    for index, frame in enumerate(beats):
        score = 0.76 if index % 4 == 0 else 0.55
        reason = "小节重拍" if index % 4 == 0 else "节拍"
        _merge_candidate(store, frame, score, reason, total_frames)

    for cue in cues:
        end_frame = int(cue["end_frame"])
        start_frame = int(cue["start_frame"])
        _merge_candidate(store, end_frame, 1.15, "歌词句尾", total_frames)
        _merge_candidate(store, start_frame, 0.66, "歌词句首", total_frames)

    for frame in section_changes or []:
        _merge_candidate(store, frame, 0.92, "能量段落变化", total_frames)

    if frame_energy:
        count = min(total_frames + 1, len(frame_energy))
        for frame in range(3, count - 3):
            value = frame_energy[frame]
            nearby = frame_energy[frame - 3:frame + 4]
            if value <= min(nearby) + 1e-8 and value < 0.45:
                score = 0.45 + (0.45 - value) * 0.8
                _merge_candidate(store, frame, score, "低能量保护点", total_frames)

    # A quiet fallback grid guarantees a valid dynamic-programming path while
    # remaining less attractive than musical or lyrical evidence.
    for frame in range(12, total_frames, 12):
        energy = frame_energy[frame] if frame < len(frame_energy) else 0.5
        _merge_candidate(store, frame, 0.12 + (1.0 - energy) * 0.12,
                         "时长兜底", total_frames)

    result = []
    for frame in sorted(store):
        record = store[frame]
        record["score"] = round(float(record["score"]), 4)
        record["seconds"] = frame_to_seconds(frame)
        result.append(record)
    return result


def choose_boundaries(total_frames: int, candidates: list[dict[str, Any]],
                      target_frames: int, min_frames: int,
                      max_frames: int) -> list[int]:
    total_frames = max(1, int(total_frames))
    max_frames = max(1, min(int(max_frames), H3_MAX_FRAMES))
    min_frames = max(1, min(int(min_frames), max_frames))
    target_frames = max(min_frames, min(int(target_frames), max_frames))
    if total_frames <= max_frames:
        return [0, total_frames]

    points = sorted({0, total_frames, *(
        int(item["frame"]) for item in candidates if 0 <= int(item["frame"]) <= total_frames)})
    scores = {int(item["frame"]): float(item.get("score", 0.0)) for item in candidates}
    costs = [math.inf] * len(points)
    previous = [-1] * len(points)
    costs[0] = 0.0
    for index in range(1, len(points)):
        point = points[index]
        low = bisect.bisect_left(points, point - max_frames, 0, index)
        high = bisect.bisect_right(points, point - min_frames, low, index)
        for parent in range(low, high):
            if not math.isfinite(costs[parent]):
                continue
            length = point - points[parent]
            length_cost = ((length - target_frames) / max(1, target_frames)) ** 2
            short_cost = 0.35 if length < min_frames * 1.25 else 0.0
            boundary_reward = 0.0 if point == total_frames else scores.get(point, 0.0) * 0.72
            value = costs[parent] + length_cost + short_cost + 0.18 - boundary_reward
            if value < costs[index]:
                costs[index] = value
                previous[index] = parent

    if previous[-1] < 0:
        result = [0]
        while total_frames - result[-1] > max_frames:
            target = result[-1] + target_frames
            upper = min(total_frames - min_frames, result[-1] + max_frames)
            target = min(target, upper)
            choice = min((point for point in points if result[-1] + min_frames <= point <= upper),
                         key=lambda point: abs(point - target), default=upper)
            result.append(int(choice))
        result.append(total_frames)
        return result

    result = []
    cursor = len(points) - 1
    while cursor >= 0:
        result.append(points[cursor])
        cursor = previous[cursor]
    return list(reversed(result))


def default_settings() -> dict[str, Any]:
    return {
        "target_shot_seconds": 7.5,
        "minimum_shot_seconds": 3.0,
        "maximum_shot_seconds": H3_DEFAULT_MAX_FRAMES / FPS,
        "h3_max_frames": H3_DEFAULT_MAX_FRAMES,
        "default_transition": "hard_cut",
        "continuity_frames": 22,
        "crossfade_frames": 12,
        "safety_frames": 5,
        "hidden_continuity_frames": 22,
        # Preferred disposable audio/video handle on each side of an
        # independent editorial shot.  The compiler shrinks these handles
        # when the kept shot is close to the selected H3 ceiling.
        "audio_handle_frames": 22,
    }


def normalize_settings(value: dict[str, Any] | None) -> dict[str, Any]:
    settings = default_settings()
    if isinstance(value, dict):
        settings.update({key: value[key] for key in settings if key in value})
    requested_max = int(settings.get("h3_max_frames", H3_DEFAULT_MAX_FRAMES))
    settings["h3_max_frames"] = (
        requested_max if requested_max in H3_WINDOW_PRESETS else H3_DEFAULT_MAX_FRAMES
    )
    maximum_seconds = settings["h3_max_frames"] / FPS
    settings["maximum_shot_seconds"] = maximum_seconds
    settings["target_shot_seconds"] = _clamp(float(settings["target_shot_seconds"]), 1.0, maximum_seconds)
    settings["minimum_shot_seconds"] = _clamp(float(settings["minimum_shot_seconds"]), 0.5, maximum_seconds)
    if settings["minimum_shot_seconds"] > settings["maximum_shot_seconds"]:
        settings["minimum_shot_seconds"] = settings["maximum_shot_seconds"]
    settings["target_shot_seconds"] = _clamp(
        settings["target_shot_seconds"], settings["minimum_shot_seconds"], settings["maximum_shot_seconds"])
    transition = str(settings["default_transition"])
    settings["default_transition"] = transition if transition in TRANSITION_TYPES else "hard_cut"
    for key, low, high in (
        ("continuity_frames", 0, 90),
        ("crossfade_frames", 2, 96),
        ("safety_frames", 0, 48),
        ("hidden_continuity_frames", 5, 73),
        ("audio_handle_frames", 0, 48),
    ):
        settings[key] = max(low, min(high, int(settings[key])))
    return settings


def automatic_planning_settings_changed(
        previous: dict[str, Any] | None,
        current: dict[str, Any] | None,
) -> bool:
    """Return whether changing the public auto-plan controls needs new cuts.

    Other settings affect generation-window math and can be recompiled without
    destroying editorial boundaries. These two controls define the automatic
    boundary search itself, so they require an explicit destructive replan.
    """
    if not isinstance(previous, dict) or not previous:
        return False
    if not any(key in previous for key in ("target_shot_seconds", "h3_max_frames")):
        return False
    old = normalize_settings(previous)
    new = normalize_settings(current)
    return (
        not math.isclose(
            float(old["target_shot_seconds"]),
            float(new["target_shot_seconds"]),
            rel_tol=0.0,
            abs_tol=1e-6,
        )
        or int(old["h3_max_frames"]) != int(new["h3_max_frames"])
    )


def _overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> int:
    return max(0, min(a_end, b_end) - max(a_start, b_start))


def _old_shot_for_range(old_shots: list[dict[str, Any]], start: int,
                        end: int) -> dict[str, Any] | None:
    if not old_shots:
        return None
    return max(old_shots, key=lambda shot: _overlap(
        start, end, int(shot.get("start_frame", 0)), int(shot.get("end_frame", 0))))


def _shot_metadata(old_shot: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(old_shot, dict):
        return {
            "shot_uid": uuid.uuid4().hex,
            "material": {"kind": "image", "reference": "", "note": ""},
            "asset_bindings": [],
            "audio_references": [],
            "creative_outline": "",
            "singing_performance": None,
            "creative_asset_needs": [],
            "prompt": "",
            "subtitle_track": {
                "leading_text": "",
                "text": "",
                "trailing_text": "",
                "revision": 0,
            },
            "connection_requirement": "",
            "note": "",
            "manual_splice_frames": [],
        }
    material = old_shot.get("material")
    if not isinstance(material, dict):
        material = {"kind": "image", "reference": "", "note": ""}
    raw_bindings = old_shot.get("asset_bindings", [])
    if not isinstance(raw_bindings, list):
        raw_bindings = []
    if not raw_bindings and isinstance(old_shot.get("asset_ids"), list):
        raw_bindings = [{"asset_id": asset_id, "usage": ""}
                        for asset_id in old_shot["asset_ids"]]
    raw_subtitle_track = old_shot.get("subtitle_track", {})
    if not isinstance(raw_subtitle_track, dict):
        raw_subtitle_track = {}
    return {
        "shot_uid": str(old_shot.get("shot_uid") or (
            f"legacy_{old_shot['id']}" if old_shot.get("id") else uuid.uuid4().hex)),
        "material": deepcopy(material),
        "asset_bindings": deepcopy(raw_bindings),
        "audio_references": deepcopy([
            reference for reference in old_shot.get("audio_references", [])
            if isinstance(reference, dict)
        ]) if isinstance(old_shot.get("audio_references"), list) else [],
        "creative_outline": str(old_shot.get("creative_outline", "")),
        "singing_performance": (
            old_shot.get("singing_performance")
            if isinstance(old_shot.get("singing_performance"), bool) else None
        ),
        "creative_asset_needs": deepcopy([
            need for need in old_shot.get("creative_asset_needs", [])
            if isinstance(need, dict)
        ]) if isinstance(old_shot.get("creative_asset_needs", []), list) else [],
        "prompt": str(old_shot.get("prompt", "")),
        "subtitle_track": {
            "leading_text": str(raw_subtitle_track.get("leading_text", "")),
            "text": str(raw_subtitle_track.get(
                "text", old_shot.get("subtitle_text", ""))),
            "trailing_text": str(raw_subtitle_track.get("trailing_text", "")),
            "revision": max(0, int(raw_subtitle_track.get("revision", 0))),
        },
        "connection_requirement": str(old_shot.get("connection_requirement", "")),
        "note": str(old_shot.get("note", "")),
        "manual_splice_frames": list(old_shot.get("manual_splice_frames", [])),
    }


def _project_metadata(existing: dict[str, Any] | None) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Preserve one project brief and one canonical shared asset library."""
    source = existing.get("project", {}) if isinstance(existing, dict) else {}
    if not isinstance(source, dict):
        source = {}
    project = {
        "code": str(source.get("code", "")),
        "name": str(source.get("name", "")),
        "description": str(source.get("description", "")),
        "overall_prompt": str(source.get("overall_prompt", "")),
        "negative_prompt": str(source.get("negative_prompt", "")),
    }
    if source.get("id"):
        project["id"] = str(source["id"])
    if source.get("schema"):
        project["schema"] = str(source["schema"])
    raw_assets = existing.get("assets", []) if isinstance(existing, dict) else []
    assets = deepcopy([asset for asset in raw_assets if isinstance(asset, dict)]) \
        if isinstance(raw_assets, list) else []
    return project, assets


def _generation_spec_metadata(existing: dict[str, Any] | None) -> dict[str, Any]:
    source = existing.get("generation_spec", {}) if isinstance(existing, dict) else {}
    if not isinstance(source, dict):
        source = {}
    return {
        "aspect_ratio": str(source.get("aspect_ratio") or "16:9 (Widescreen)"),
        "first_pass_megapixels": float(source.get("first_pass_megapixels") or 0.4),
        "second_pass_megapixels": float(source.get("second_pass_megapixels") or 0.9),
        "pixel_alignment": int(source.get("pixel_alignment") or 32),
    }


def _skill_template_metadata(existing: dict[str, Any] | None) -> dict[str, str]:
    source = existing.get("skill_templates", {}) if isinstance(existing, dict) else {}
    if not isinstance(source, dict):
        source = {}
    return {
        "visual_outline": str(
            source.get("visual_outline") or "xiaoyue-mv-visual-outline"),
        "h3_prompt": str(source.get("h3_prompt") or "xiaoyue-h3-mv-prompt"),
    }


def _creative_plan_metadata(existing: dict[str, Any] | None,
                            reset: bool = False) -> dict[str, Any]:
    """Keep the non-H3 whole-MV outline separate from generation prompts."""
    source = existing.get("creative_plan", {}) if isinstance(existing, dict) else {}
    if reset or not isinstance(source, dict):
        source = {}
    return {
        "overview": str(source.get("overview", "")),
        "revision": max(0, int(source.get("revision", 0))),
        "source_fingerprint": str(source.get("source_fingerprint", "")),
        "model": str(source.get("model", "")),
    }


def _apply_transition_geometry(transition: dict[str, Any], at_frame: int) -> dict[str, Any]:
    for key in (
        "context_range", "active_range", "left_safety_range", "right_safety_range",
        "previous_required_until_frame", "next_required_from_frame",
    ):
        transition.pop(key, None)
    if transition["type"] == "long_take":
        context = transition["context_frames"]
        transition["context_range"] = [at_frame - context, at_frame]
    elif transition["type"] == "crossfade":
        before = transition["duration_frames"] // 2
        after = transition["duration_frames"] - before
        legacy_safety = max(0, int(transition.get("safety_frames", 0)))
        before_safety = max(0, int(transition.get(
            "before_safety_frames", legacy_safety)))
        after_safety = max(0, int(transition.get(
            "after_safety_frames", legacy_safety)))
        transition["active_range"] = [at_frame - before, at_frame + after]
        transition["left_safety_range"] = [
            at_frame - before - before_safety, at_frame - before]
        transition["right_safety_range"] = [
            at_frame + after, at_frame + after + after_safety]
        transition["previous_required_until_frame"] = at_frame + after + after_safety
        transition["next_required_from_frame"] = at_frame - before - before_safety
        transition["before_safety_frames"] = before_safety
        transition["after_safety_frames"] = after_safety
        transition["safety_frames"] = max(before_safety, after_safety)
        transition["safety_mode"] = "auto_17n5"
    return transition


def _normalize_transition(old: dict[str, Any] | None, index: int,
                          at_frame: int, settings: dict[str, Any]) -> dict[str, Any]:
    old = old if isinstance(old, dict) else {}
    kind = str(old.get("type", settings["default_transition"]))
    if kind not in TRANSITION_TYPES:
        kind = "hard_cut"
    transition = {
        "id": f"transition_{index + 1:03d}",
        "from_shot": f"shot_{index + 1:03d}",
        "to_shot": f"shot_{index + 2:03d}",
        "at_frame": int(at_frame),
        "at_seconds": frame_to_seconds(at_frame),
        "type": kind,
        "context_frames": max(0, min(90, int(old.get(
            "context_frames", settings["continuity_frames"])))),
        "duration_frames": max(2, min(96, int(old.get(
            "duration_frames", settings["crossfade_frames"])))),
        "safety_frames": 0,
        "before_safety_frames": 0,
        "after_safety_frames": 0,
        "safety_mode": "auto_17n5",
        "note": str(old.get("note", "")),
    }
    return _apply_transition_geometry(transition, at_frame)


def _assign_auto_crossfade_safety(shots: list[dict[str, Any]],
                                  transitions: list[dict[str, Any]],
                                  maximum_frames: int) -> None:
    """Allocate only the padding each adjacent shot needs to land on 17N+5."""
    for transition in transitions:
        if transition["type"] != "crossfade":
            continue
        transition["before_safety_frames"] = 0
        transition["after_safety_frames"] = 0
        transition["safety_frames"] = 0
        transition["safety_mode"] = "auto_17n5"

    for index, shot in enumerate(shots):
        incoming = transitions[index - 1] if index > 0 else None
        outgoing = transitions[index] if index < len(transitions) else None
        required = max(0, int(shot["keep_frames"]))
        sides: list[tuple[dict[str, Any], str]] = []
        if incoming and incoming["type"] == "long_take":
            required += max(0, int(incoming["context_frames"]))
        elif incoming and incoming["type"] == "crossfade":
            required += int(incoming["duration_frames"]) // 2
            sides.append((incoming, "before_safety_frames"))
        if outgoing and outgoing["type"] == "crossfade":
            duration = int(outgoing["duration_frames"])
            required += duration - duration // 2
            sides.append((outgoing, "after_safety_frames"))
        if not sides or required > maximum_frames:
            continue
        padding = max(0, legal_h3_frames(required) - required)
        if len(sides) == 1:
            sides[0][0][sides[0][1]] = padding
        else:
            first = padding // 2
            sides[0][0][sides[0][1]] = first
            sides[1][0][sides[1][1]] = padding - first

    for transition in transitions:
        if transition["type"] == "crossfade":
            transition["safety_frames"] = max(
                int(transition["before_safety_frames"]),
                int(transition["after_safety_frames"]),
            )
        _apply_transition_geometry(transition, int(transition["at_frame"]))


def _validate_boundaries(boundaries: list[int], total_frames: int) -> list[int]:
    values = [int(value) for value in boundaries]
    if len(values) < 2 or values[0] != 0 or values[-1] != total_frames:
        raise ValueError("MV 计划的首尾切点必须覆盖完整母带。")
    if any(right <= left for left, right in zip(values, values[1:])):
        raise ValueError("MV 计划中的切点必须严格递增。")
    return values


def _window_audio_clock(start_frame: int, raw_frames: int, sample_rate: int,
                        total_samples: int) -> dict[str, int]:
    raw_start = frame_to_sample(start_frame, sample_rate)
    raw_end = frame_to_sample(start_frame + raw_frames, sample_rate)
    clipped_start = max(0, min(total_samples, raw_start))
    clipped_end = max(clipped_start, min(total_samples, raw_end))
    return {
        "source_start_sample": clipped_start,
        "source_end_sample": clipped_end,
        "requested_start_sample": raw_start,
        "requested_end_sample": raw_end,
        "requested_samples": raw_end - raw_start,
        "pad_before_samples": clipped_start - raw_start,
        "pad_after_samples": raw_end - clipped_end,
    }


def _allocate_audio_handles(padding: int, before_target: int,
                            after_target: int) -> tuple[int, int]:
    """Split disposable H3 padding across the available edit handles."""
    padding = max(0, int(padding))
    before_target = max(0, int(before_target))
    after_target = max(0, int(after_target))
    if not padding:
        return 0, 0
    if before_target <= 0 and after_target <= 0:
        # A one-window song has no source audio outside either edge.  Keep
        # the leading edge clean and let the audio extractor zero-pad tail.
        return 0, padding
    if before_target <= 0:
        return 0, padding
    if after_target <= 0:
        return padding, 0
    target_total = before_target + after_target
    before = (padding * before_target + target_total // 2) // target_total
    before = max(1, min(padding - 1, before)) if padding > 1 else 0
    return before, padding - before


def _compile_shot_windows(shot: dict[str, Any], incoming: dict[str, Any] | None,
                          outgoing: dict[str, Any] | None,
                          settings: dict[str, Any], sample_rate: int,
                          total_samples: int, window_offset: int) -> list[dict[str, Any]]:
    start = int(shot["start_frame"])
    end = int(shot["end_frame"])
    left_extra = 0
    right_extra = 0
    first_context = 0
    first_reason = "independent"
    if incoming and incoming["type"] == "long_take":
        first_context = int(incoming["context_frames"])
        first_reason = "long_take"
    elif incoming and incoming["type"] == "crossfade":
        left_extra = (int(incoming["duration_frames"]) // 2 +
                      int(incoming.get("before_safety_frames",
                                       incoming.get("safety_frames", 0))))
        first_reason = "crossfade_handle"
    if outgoing and outgoing["type"] == "crossfade":
        right_extra = (int(outgoing["duration_frames"]) -
                       int(outgoing["duration_frames"]) // 2 +
                       int(outgoing.get("after_safety_frames",
                                        outgoing.get("safety_frames", 0))))

    coverage_start = start - left_extra
    coverage_end = end + right_extra
    cursor = coverage_start
    windows = []
    hidden_context = int(settings["hidden_continuity_frames"])
    audio_handle = int(settings["audio_handle_frames"])
    maximum_frames = int(settings["h3_max_frames"])
    total_frames = sample_to_frame(total_samples, sample_rate)
    manual_splices = sorted({
        int(frame) for frame in shot.get("manual_splice_frames", [])
        if start < int(frame) < end
    })
    manual_splice_set = set(manual_splices)
    splice_index = 0
    while cursor < coverage_end:
        context = first_context if not windows else hidden_context
        capacity = maximum_frames - context
        if capacity <= 0:
            raise ValueError(f"续接上下文已占满 H3 的 {maximum_frames} 帧窗口。")
        while splice_index < len(manual_splices) and manual_splices[splice_index] <= cursor:
            splice_index += 1
        requested_end = (manual_splices[splice_index]
                         if splice_index < len(manual_splices) else coverage_end)
        deliver = min(capacity, requested_end - cursor)
        if deliver <= 0:
            requested_end = coverage_end
            deliver = min(capacity, coverage_end - cursor)
        delivery_end = cursor + deliver
        minimum_raw_frames = context + deliver
        is_first_window = not windows
        is_last_window = delivery_end >= coverage_end
        outgoing_virtual_cut = bool(
            is_last_window and outgoing and outgoing["type"] == "long_take")
        continues_on_right = context > 0 and (not is_last_window or outgoing_virtual_cut)
        # A continuation consumes the selected number of frames from the
        # preceding delivery's tail. Moving H3 alignment padding to its left shifts audio ahead
        # of that visual guide; keep all such padding after delivery instead.
        can_take_before = (context == 0 and (cursor > 0 or outgoing_virtual_cut) and not (
            is_first_window and incoming and incoming["type"] == "crossfade"))
        can_take_after = context > 0 or (delivery_end < total_frames and not (
            is_last_window and outgoing and outgoing["type"] in {"crossfade", "long_take"}))
        before_target = audio_handle if can_take_before else 0
        after_target = audio_handle if can_take_after else 0
        # Only a window continued on both sides needs disposable alignment
        # frames immediately after delivery.  At a real cut, retain the
        # ordinary tail handle, unless delivery already ends on a legal H3
        # length (then it lands exactly on the cut, with no handle at all).
        exact_h3_length = legal_h3_frames(minimum_raw_frames) == minimum_raw_frames
        preferred_raw_frames = min(
            maximum_frames,
            minimum_raw_frames if continues_on_right or (context and exact_h3_length)
            else minimum_raw_frames + before_target + after_target,
        )
        raw_frames = legal_h3_frames(preferred_raw_frames)
        if raw_frames > maximum_frames:
            raw_frames = maximum_frames
        if raw_frames < minimum_raw_frames:
            deliver -= minimum_raw_frames - raw_frames
            delivery_end = cursor + deliver
            minimum_raw_frames = context + deliver
        padding = raw_frames - minimum_raw_frames
        handle_before, handle_after = _allocate_audio_handles(
            padding, before_target, after_target)
        window_start = cursor - context - handle_before
        window_end = window_start + raw_frames
        window_id = f"window_{window_offset + len(windows) + 1:04d}"
        audio_clock = _window_audio_clock(
            window_start, raw_frames, sample_rate, total_samples)
        core_start = max(cursor, start)
        core_end = min(delivery_end, end)
        windows.append({
            "id": window_id,
            "shot_id": shot["id"],
            "shot_uid": shot["shot_uid"],
            "shot_window_index": len(windows) + 1,
            "continuation_reason": (first_reason if not windows else
                                    "manual_splice" if cursor in manual_splice_set else
                                    "hidden_continuation"),
            "source_start_frame": window_start,
            "source_end_frame": window_end,
            "source_start_seconds": frame_to_seconds(window_start),
            "source_end_seconds": frame_to_seconds(window_end),
            "raw_frames": raw_frames,
            "raw_seconds": frame_to_seconds(raw_frames),
            "context_frames": context,
            "audio_handle_before_frames": handle_before,
            "audio_handle_after_frames": handle_after,
            "trim_leading_frames": context + handle_before,
            "deliver_from_raw_frame": context + handle_before,
            "deliver_frames": deliver,
            "delivery_timeline_range": [cursor, delivery_end],
            "core_timeline_range": [core_start, max(core_start, core_end)],
            "tail_padding_frames": handle_after,
            "continuation_tail_bleed_frames": handle_after if continues_on_right else 0,
            "h3_formula_n": (raw_frames - H3_OFFSET) // H3_STEP,
            **audio_clock,
        })
        cursor = delivery_end
        if splice_index < len(manual_splices) and cursor >= manual_splices[splice_index]:
            splice_index += 1
    return windows


def _long_take_groups(shots: list[dict[str, Any]], transitions: list[dict[str, Any]],
                      window_by_shot: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    groups = []
    index = 0
    while index < len(shots):
        end = index
        while end < len(transitions) and transitions[end]["type"] == "long_take":
            end += 1
        if end > index:
            members = shots[index:end + 1]
            window_count = sum(len(window_by_shot[shot["id"]]) for shot in members)
            group = {
                "id": f"long_take_{len(groups) + 1:02d}",
                "shot_ids": [shot["id"] for shot in members],
                "shot_count": len(members),
                "generation_window_count": window_count,
                "start_frame": members[0]["start_frame"],
                "end_frame": members[-1]["end_frame"],
            }
            groups.append(group)
            for position, shot in enumerate(members, 1):
                shot["long_take_group"] = group["id"]
                shot["long_take_position"] = position
        index = max(index + 1, end + 1)
    return groups


def _alpha_suffix(index: int) -> str:
    """Return spreadsheet-style lowercase suffixes: a..z, aa..az, ..."""
    value = max(0, int(index)) + 1
    result = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(97 + remainder) + result
    return result


def _assign_editorial_labels(shots: list[dict[str, Any]],
                             transitions: list[dict[str, Any]],
                             window_by_shot: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Name hard-cut shots 01/02 and continuation parts 01_a/01_b.

    A long-take transition keeps both logical shots inside the same editorial
    number. Manual H3 splices inside a shot do the same. ``_vN`` is reserved
    solely for alternate generations of the same part.
    """
    chains = []
    group_start = 0
    major = 1
    while group_start < len(shots):
        group_end = group_start
        while (group_end < len(transitions)
               and transitions[group_end].get("type") == "long_take"):
            group_end += 1

        members = shots[group_start:group_end + 1]
        windows = [
            window
            for shot in members
            for window in window_by_shot.get(shot["id"], [])
        ]
        base = f"{major:02d}"
        multiple = len(windows) > 1
        for part_index, window in enumerate(windows):
            label = f"{base}_{_alpha_suffix(part_index)}" if multiple else base
            version = max(1, int(window.get("generation_version", 1)))
            window["editorial_label"] = label
            window["scene_code"] = f"SC{label}"
            window["generation_version"] = version
            window["clip_name"] = f"SC{label}_v{version}"
            window["clip_name_template"] = f"{{project_code}}_SC{label}_v{version}"

        if multiple:
            labels = [window["editorial_label"] for window in windows]
            chains.append({
                "id": f"continuity_{base}",
                "editorial_labels": labels,
                "regeneration_policy": "upstream_change_invalidates_downstream",
            })
            for part_index, window in enumerate(windows):
                window["continuity_chain_id"] = f"continuity_{base}"
                window["continuity_parent_label"] = (
                    labels[part_index - 1] if part_index > 0 else None
                )
                window["downstream_regeneration_labels"] = labels[part_index + 1:]

        for shot in members:
            labels = [
                window["editorial_label"]
                for window in window_by_shot.get(shot["id"], [])
            ]
            shot["editorial_labels"] = labels
            shot["editorial_label"] = (
                labels[0] if len(labels) <= 1 else f"{labels[0]}–{labels[-1]}"
            )

        major += 1
        group_start = group_end + 1
    return chains


def compile_plan(*, audio_fingerprint: str, sample_rate: int, total_samples: int,
                 analysis: dict[str, Any], settings: dict[str, Any] | None = None,
                 subtitle_source: str = "", existing: dict[str, Any] | None = None,
                 request_replan: bool = False) -> dict[str, Any]:
    settings = normalize_settings(settings)
    existing_settings = existing.get("settings") if isinstance(existing, dict) else None
    existing_basis = (
        existing.get("automatic_plan_basis") if isinstance(existing, dict) else None
    )
    comparison_settings = existing_basis if isinstance(existing_basis, dict) else existing_settings
    settings_changed = automatic_planning_settings_changed(comparison_settings, settings)
    prior_pending = (
        existing.get("pending_replan") if isinstance(existing, dict) else None
    )
    destructive_replan_pending = bool(
        isinstance(prior_pending, dict)
        and prior_pending.get("reason") in {
            "automatic_settings_changed",
            "legacy_plan_basis_unverified",
        }
    )
    total_frames = max(1, sample_to_frame(total_samples, sample_rate))
    parsed_subtitles = parse_timed_text(subtitle_source)
    parsed_cues = [
        cue for cue in parsed_subtitles["cues"]
        if int(cue.get("start_frame", 0)) < total_frames
    ]
    master_cues = _compile_master_cues(
        parsed_cues,
        existing=existing,
        sample_rate=sample_rate,
        total_frames=total_frames,
        total_samples=total_samples,
    )
    protection_cues = [{
        "id": cue["id"],
        "start_frame": cue["absolute_start_frame"],
        "end_frame": cue["absolute_end_frame"],
        "text": cue["corrected_text"],
    } for cue in master_cues]

    candidates = build_protection_candidates(
        total_frames,
        list(analysis.get("frame_energy", [])),
        list(analysis.get("beat_frames", [])),
        protection_cues,
        list(analysis.get("section_change_frames", [])),
    )
    old_shots = deepcopy(list(existing.get("shots", []))) if isinstance(existing, dict) else []
    old_windows = list(existing.get("generation_windows", [])) if isinstance(existing, dict) else []
    for old_shot in old_shots:
        if old_shot.get("manual_splice_frames") or not old_shot.get("allow_stitch"):
            continue
        start = int(old_shot.get("start_frame", 0))
        end = int(old_shot.get("end_frame", 0))
        old_shot["manual_splice_frames"] = sorted({
            int(window.get("delivery_timeline_range", [0, 0])[1])
            for window in old_windows
            if window.get("shot_id") == old_shot.get("id")
            and isinstance(window.get("delivery_timeline_range"), list)
            and len(window["delivery_timeline_range"]) >= 2
            and start < int(window["delivery_timeline_range"][1]) < end
        })
    same_audio = bool(existing and existing.get("audio", {}).get("fingerprint") == audio_fingerprint)
    legacy_basis_missing = bool(
        existing and same_audio and old_shots and not isinstance(existing_basis, dict)
    )
    # Automatic controls never silently erase hand-edited boundaries. A first
    # compile records the pending destructive change; the editor then asks the
    # user to confirm and submits request_replan on the next execution.
    keep_manual = bool(existing and same_audio and not request_replan and old_shots)
    if keep_manual:
        boundaries = [int(old_shots[0].get("start_frame", 0))]
        boundaries.extend(int(shot.get("end_frame", 0)) for shot in old_shots)
        boundaries[-1] = total_frames
        boundaries = _validate_boundaries(boundaries, total_frames)
    else:
        boundaries = choose_boundaries(
            total_frames,
            candidates,
            seconds_to_frame(settings["target_shot_seconds"]),
            seconds_to_frame(settings["minimum_shot_seconds"]),
            int(settings["h3_max_frames"]),
        )

    shots = []
    used_shot_uids: set[str] = set()
    for index, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
        if request_replan:
            # A confirmed automatic replan is a destructive shot rebuild. Keep
            # only project-level sources/assets; never attach old shot prompts,
            # bindings, transitions or manual splice decisions to new indices.
            old = None
        elif keep_manual and index < len(old_shots):
            old = old_shots[index]
        else:
            old = _old_shot_for_range(old_shots, start, end)
        metadata = _shot_metadata(old)
        if metadata["shot_uid"] in used_shot_uids:
            metadata["shot_uid"] = uuid.uuid4().hex
        used_shot_uids.add(metadata["shot_uid"])
        shot = {
            "id": f"shot_{index + 1:03d}",
            "index": index + 1,
            "start_frame": start,
            "end_frame": end,
            "keep_frames": end - start,
            "start_seconds": frame_to_seconds(start),
            "end_seconds": frame_to_seconds(end),
            "duration_seconds": frame_to_seconds(end - start),
            "start_sample": frame_to_sample(start, sample_rate),
            "end_sample": min(total_samples, frame_to_sample(end, sample_rate)),
            **metadata,
        }
        shot["manual_splice_frames"] = sorted({
            int(frame) for frame in shot.get("manual_splice_frames", [])
            if start < int(frame) < end
        })
        shot["subtitle_projections"] = _project_master_cues(shot, master_cues)
        shot["subtitle_ids"] = [
            projection["source_cue_id"]
            for projection in shot["subtitle_projections"]
        ]
        subtitle_track = shot.get("subtitle_track", {})
        if not isinstance(subtitle_track, dict):
            subtitle_track = {}
        shot["subtitle_track"] = {
            "leading_text": _single_line_subtitle_text(
                subtitle_track.get("leading_text", "")),
            "text": str(subtitle_track.get("text", "")),
            "trailing_text": _single_line_subtitle_text(
                subtitle_track.get("trailing_text", "")),
            "revision": max(0, int(subtitle_track.get("revision", 0))),
            "absolute_start_frame": start,
            "absolute_end_frame": end,
            "relative_start_frame": 0,
            "relative_end_frame": end - start,
            "reference_cue_ids": list(shot["subtitle_ids"]),
            "timing_basis": "shot_boundaries",
        }
        shots.append(shot)

    old_transitions = (
        list(existing.get("transitions", []))
        if isinstance(existing, dict) and keep_manual else []
    )
    transitions = []
    for index, shot in enumerate(shots[:-1]):
        old = old_transitions[index] if index < len(old_transitions) else None
        transition = _normalize_transition(old, index, shot["end_frame"], settings)
        if transition["type"] == "crossfade":
            at_frame = int(shot["end_frame"])
            left_frames = max(0, at_frame - int(shot["start_frame"]))
            right_frames = max(0, int(shots[index + 1]["end_frame"]) - at_frame)
            maximum_duration = max(2, min(
                96,
                left_frames * 2 + 1,
                right_frames * 2,
            ))
            transition["duration_frames"] = min(
                int(transition["duration_frames"]),
                maximum_duration,
            )
            _apply_transition_geometry(transition, at_frame)
        transitions.append(transition)
    _assign_auto_crossfade_safety(shots, transitions, int(settings["h3_max_frames"]))

    edit_points = [{
        "id": "edit_point_000",
        "index": 0,
        "kind": "program_start",
        "absolute_frame": 0,
        "absolute_sample": 0,
        "left_shot_id": None,
        "right_shot_id": shots[0]["id"] if shots else None,
        "transition_id": None,
    }]
    for index, transition in enumerate(transitions):
        frame = int(transition["at_frame"])
        edit_points.append({
            "id": f"edit_point_{index + 1:03d}",
            "index": index + 1,
            "kind": "shot_boundary",
            "absolute_frame": frame,
            "absolute_sample": min(total_samples, frame_to_sample(frame, sample_rate)),
            "left_shot_id": shots[index]["id"],
            "right_shot_id": shots[index + 1]["id"],
            "transition_id": transition["id"],
        })
    edit_points.append({
        "id": f"edit_point_{len(shots):03d}",
        "index": len(shots),
        "kind": "program_end",
        "absolute_frame": total_frames,
        "absolute_sample": total_samples,
        "left_shot_id": shots[-1]["id"] if shots else None,
        "right_shot_id": None,
        "transition_id": None,
    })

    windows = []
    window_by_shot: dict[str, list[dict[str, Any]]] = {}
    warnings = []
    for index, shot in enumerate(shots):
        incoming = transitions[index - 1] if index > 0 else None
        outgoing = transitions[index] if index < len(transitions) else None
        shot_windows = _compile_shot_windows(
            shot, incoming, outgoing, settings, sample_rate, total_samples, len(windows))
        window_by_shot[shot["id"]] = shot_windows
        windows.extend(shot_windows)
        shot["generation_window_ids"] = [window["id"] for window in shot_windows]
        shot["stitch_required"] = len(shot_windows) > 1
        shot["subtitle_context"] = _compile_shot_subtitle_context(
            shot, shot_windows, master_cues, total_frames)
        if len(shot_windows) > 1:
            compiled_breaks = [
                int(window["delivery_timeline_range"][1])
                for window in shot_windows[:-1]
            ]
            manual_splices = set(int(frame) for frame in shot.get("manual_splice_frames", []))
            unplanned_breaks = [frame for frame in compiled_breaks if frame not in manual_splices]
            if not unplanned_breaks:
                warnings.append(
                    f"{shot['id']} 按旧版兼容切点编译为 {len(shot_windows)} 个 H3 生成窗口；请在规划器中迁移为独立镜头。")
            else:
                warnings.append(
                    f"{shot['id']} 的内容、上下文和叠化把手合计超过 {settings['h3_max_frames']} 帧；请移动镜头边界或用剃刀切成独立镜头。")

    continuity_chains = _assign_editorial_labels(shots, transitions, window_by_shot)
    long_take_groups = _long_take_groups(shots, transitions, window_by_shot)
    for group in long_take_groups:
        if group["shot_count"] > 3 or group["generation_window_count"] > 3:
            warnings.append(
                f"{group['id']} 连续 {group['shot_count']} 个逻辑镜头、共 {group['generation_window_count']} 个 H3 窗口；超过实测 3 段舒适区，存在逐段劣化风险。")

    for transition in transitions:
        if transition["type"] == "crossfade":
            active = transition["duration_frames"]
            left_shot = shots[int(transition["from_shot"].split("_")[-1]) - 1]
            right_shot = shots[int(transition["to_shot"].split("_")[-1]) - 1]
            if active >= min(left_shot["keep_frames"], right_shot["keep_frames"]):
                warnings.append(f"{transition['id']} 的叠化长度已接近或超过相邻镜头长度，请缩短叠化。")

    for shot in shots:
        if shot["keep_frames"] > settings["h3_max_frames"]:
            warnings.append(
                f"{shot['id']} 的真实镜头超过 {settings['h3_max_frames']} 帧，"
                "已使用隐藏续接，不再是单次 H3 生成。")

    bpm_value = float(analysis.get("bpm", 0.0))
    project, assets = _project_metadata(existing)
    generation_spec = _generation_spec_metadata(existing)
    skill_templates = _skill_template_metadata(existing)
    creative_plan = _creative_plan_metadata(existing, reset=request_replan)
    valid_asset_ids = {
        str(asset.get("id")) for asset in assets
        if str(asset.get("id", "")).strip()
    }
    for shot in shots:
        bindings = []
        seen_asset_ids = set()
        for value in shot.get("asset_bindings", []):
            binding = value if isinstance(value, dict) else {"asset_id": value}
            asset_id = str(binding.get("asset_id", "")).strip()
            if asset_id not in valid_asset_ids or asset_id in seen_asset_ids:
                continue
            seen_asset_ids.add(asset_id)
            bindings.append({
                "asset_id": asset_id,
                "order": len(bindings) + 1,
                "usage": str(binding.get("usage", "")),
            })
        shot["asset_bindings"] = bindings
        shot.pop("asset_ids", None)

    pending_replan = None
    if keep_manual and (settings_changed or destructive_replan_pending or legacy_basis_missing):
        previous = normalize_settings(comparison_settings)
        legacy_pending = legacy_basis_missing and not settings_changed
        pending_replan = {
            "reason": (
                "legacy_plan_basis_unverified" if legacy_pending
                else "automatic_settings_changed"
            ),
            "message": (
                "现有切点来自旧版规划记录，无法确认与当前分段参数一致；旧打点暂时保留，确认重新找点后才会覆盖。"
                if legacy_pending else
                "自动分镜参数已变化；旧打点暂时保留，确认重新找点后才会覆盖。"
            ),
            "previous": {
                "target_shot_seconds": float(previous["target_shot_seconds"]),
                "h3_max_frames": int(previous["h3_max_frames"]),
            },
            "requested": {
                "target_shot_seconds": float(settings["target_shot_seconds"]),
                "h3_max_frames": int(settings["h3_max_frames"]),
            },
        }

    plan = {
        "schema": SCHEMA,
        "audio": {
            "fingerprint": audio_fingerprint,
            "sample_rate": int(sample_rate),
            "total_samples": int(total_samples),
            "duration_seconds": round(total_samples / float(sample_rate), 6),
            "timeline_frames": total_frames,
            "final_audio_policy": "replace_with_original_master",
        },
        "h3": {
            "fps": FPS,
            "length_rule": "17N+5",
            "continuation_window_rule_revision": CONTINUATION_WINDOW_RULE_REVISION,
            "max_window_frames": int(settings["h3_max_frames"]),
            "max_window_seconds": round(settings["h3_max_frames"] / FPS, 6),
        },
        "naming": {
            "dictionary": "EDITORIAL_NAMING.md",
            "template": "{project_code}_SC{shot_number}[_{continuation_part}]_v{version}",
            "upstream_change_policy": "invalidate_downstream_continuation_parts",
        },
        "settings": settings,
        "automatic_plan_basis": (
            {
                "target_shot_seconds": float(settings["target_shot_seconds"]),
                "h3_max_frames": int(settings["h3_max_frames"]),
            }
            if request_replan or not keep_manual
            else {
                "target_shot_seconds": float(previous["target_shot_seconds"]),
                "h3_max_frames": int(previous["h3_max_frames"]),
            }
            if pending_replan
            else {
                "target_shot_seconds": float(settings["target_shot_seconds"]),
                "h3_max_frames": int(settings["h3_max_frames"]),
            }
        ),
        "project": project,
        "generation_spec": generation_spec,
        "skill_templates": skill_templates,
        "creative_plan": creative_plan,
        "assets": assets,
        "asset_binding_policy": {
            "scope": "per_shot_only",
            "internal_identity": "assets[].id",
            "reference_order": "shots[].asset_bindings[].order",
            "generation_rule": "resolve_only_current_shot_bindings_and_assign_reference_indices_per_shot",
            "global_library_order_is_generation_order": False,
        },
        "analysis": {
            "bpm": round(bpm_value, 3),
            "bpm_confidence": round(float(analysis.get("bpm_confidence", 0.0)), 4),
            "beat_frames": [int(frame) for frame in analysis.get("beat_frames", [])],
            "protection_candidates": candidates,
        },
        "subtitle_source": subtitle_source,
        "ui_state": deepcopy(existing.get("ui_state", {})) if isinstance(existing, dict) else {},
        "subtitles": {
            "format": parsed_subtitles["format"],
            "master_cues": master_cues,
            "cue_count": len(master_cues),
            "master_role": "editable_reference",
            "final_text_authority": "shots[].subtitle_track.text",
            "ai_dialogue_authority": "shots[].subtitle_context.ai_dialogue_text",
            "shot_timing_policy": "shot_boundaries",
            "untimed_text": parsed_subtitles["untimed_text"],
        },
        "shots": shots,
        "transitions": transitions,
        "edit_points": edit_points,
        "generation_windows": windows,
        "continuity_chains": continuity_chains,
        "long_take_groups": long_take_groups,
        "warnings": warnings,
        "stats": {
            "shot_count": len(shots),
            "generation_window_count": len(windows),
            "long_take_group_count": len(long_take_groups),
            "maximum_raw_frames": max((window["raw_frames"] for window in windows), default=0),
        },
        "request_replan": False,
        "needs_compile": False,
        "editor_revision": int((existing or {}).get("editor_revision") or 0),
    }
    if pending_replan:
        plan["pending_replan"] = pending_replan
    return plan


def report_plan(plan: dict[str, Any]) -> str:
    audio = plan["audio"]
    stats = plan["stats"]
    bpm = plan["analysis"]["bpm"]
    lines = [
        (f"母带 {audio['duration_seconds']:.3f}s · {stats['shot_count']} 个逻辑镜头 · "
         f"{stats['generation_window_count']} 个 H3 窗口 · BPM {bpm:.2f}"),
        (f"单窗上限 {plan['h3']['max_window_frames']} 帧 / "
         f"{plan['h3']['max_window_seconds']:.4f}s；最终音频：完整原始母带"),
    ]
    pending_replan = plan.get("pending_replan")
    if isinstance(pending_replan, dict):
        lines.append(str(
            pending_replan.get("message")
            or "自动分镜参数变化，等待用户确认重新找点。"
        ))
    if plan["warnings"]:
        lines.append("警告：" + " | ".join(plan["warnings"]))
    else:
        lines.append(
            "校验通过：所有 H3 窗口均满足 17N+5 且不超过 "
            f"{plan['h3']['max_window_frames']} 帧。")
    return "\n".join(lines)
