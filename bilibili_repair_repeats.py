#!/usr/bin/env python3
"""Re-recognize consecutive ASR loops from audio with shifted window boundaries."""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import subprocess

import bilibili_recent_batch as batch
import bilibili_transcribe as core


def find_loops(segments):
    groups, start = [], 0
    for n in range(1, len(segments) + 1):
        if n == len(segments) or segments[n]["text"].strip() != segments[start]["text"].strip():
            if n - start >= 4 and segments[start]["text"].strip():
                groups.append((start, n - 1))
            start = n
    return groups


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    task_dir = args.manifest.resolve().parent
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    record_path = task_dir / "quality_repairs.json"
    records = json.loads(record_path.read_text(encoding="utf-8")) if record_path.exists() else []
    os.environ.setdefault("HF_HOME", str(Path(__file__).resolve().parent / ".launcher/huggingface"))
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    ffmpeg = core.ensure_ffmpeg_available_for_subprocess()
    from opencc import OpenCC
    converter = OpenCC("t2s")
    model = None
    for item in manifest["items"]:
        if item["status"] != "completed":
            continue
        path = Path(item["segments_json"])
        result = json.loads(path.read_text(encoding="utf-8"))
        loops = find_loops(result["segments"])
        if not loops:
            continue
        core.log(f"复核 {item['bvid']}：{len(loops)} 处连续重复")
        if model is None:
            import mlx_whisper
            model = mlx_whisper
        repaired = copy.deepcopy(result)
        changes = []
        for first, last in reversed(loops):
            original = result["segments"]
            # Re-read the whole failing decoder window, with context on both sides.
            target_start = max(0, original[first].get("seek", int(original[first]["start"] * 100)) / 100 - 14)
            target_end = min(item["audio_duration_seconds"], original[last]["end"] + 25)
            left = next(i for i, s in enumerate(original) if s["end"] > target_start)
            right = max(i for i, s in enumerate(original) if s["start"] < target_end)
            start, end = original[left]["start"], original[right]["end"]
            clip = task_dir / f"review_{item['bvid']}_{start:.2f}_{end:.2f}.wav"
            subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", str(start), "-i", item["audio_path"], "-t", str(end - start), str(clip)], check=True)
            clip_result = model.transcribe(str(clip), path_or_hf_repo=manifest["model"], language="zh", temperature=0.0,
                                           condition_on_previous_text=False, verbose=None)
            batch.save_json(clip.with_suffix(".json"), clip_result)
            if find_loops(clip_result["segments"]) or not clip_result["segments"]:
                raise ValueError(f"Repetition persists in {item['bvid']} at {start}; requires another audio-based review")
            replacement = []
            for s in clip_result["segments"]:
                s = dict(s)
                s["start"] += start
                s["end"] += start
                s["seek"] += round(start * 100)
                replacement.append(s)
            repaired["segments"][left:right + 1] = replacement
            changes.append({"start": start, "end": end, "reason": "consecutive identical ASR segments",
                            "original_repetition_count": last - first + 1, "review_audio": str(clip),
                            "review_segments": str(clip.with_suffix('.json')), "temperature": 0.0})
        if find_loops(repaired["segments"]):
            raise ValueError(f"Unresolved overlapping loop ranges: {item['bvid']}")
        backup = path.parent / "pre_quality_repair"
        backup.mkdir(exist_ok=True)
        for key in ["raw_transcript", "readable_transcript", "srt", "segments_json"]:
            source = Path(item[key])
            if not (backup / source.name).exists():
                shutil.copy2(source, backup / source.name)
        for i, s in enumerate(repaired["segments"]):
            s["id"] = i
        repaired["text"] = "".join(s["text"] for s in repaired["segments"]).strip()
        repaired.setdefault("quality_repairs", []).extend(changes)
        batch.save_json(path, repaired)
        Path(item["raw_transcript"]).write_text(repaired["text"], encoding="utf-8")
        paragraphs, current = [], ""
        for s in repaired["segments"]:
            current += converter.convert(s["text"].strip())
            if len(current) >= 140 and current.endswith(tuple("。！？.!?")) or len(current) >= 260:
                paragraphs.append(current)
                current = ""
        if current:
            paragraphs.append(current)
        Path(item["readable_transcript"]).write_text("\n\n".join(paragraphs) + "\n", encoding="utf-8")
        srt = "\n\n".join(f"{i}\n{batch.srt_time(s['start'])} --> {batch.srt_time(s['end'])}\n{s['text'].strip()}" for i, s in enumerate(repaired['segments'], 1))
        Path(item["srt"]).write_text(srt + "\n", encoding="utf-8")
        meta_path = path.parent / (core.sanitize_title(item["title"]) + "_run_meta.json")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta.update(quality_repairs=changes, transcript_characters=len(repaired["text"]), segment_count=len(repaired["segments"]))
        batch.save_json(meta_path, meta)
        records.append({"bvid": item["bvid"], "timestamp": batch.now(), "changes": changes})
        batch.save_json(record_path, records)
        core.log(f"完成片段复核：{item['bvid']}")


if __name__ == "__main__":
    main()
