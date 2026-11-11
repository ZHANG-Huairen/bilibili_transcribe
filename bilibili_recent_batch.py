#!/usr/bin/env python3
"""Run a reviewed, date-bounded video catalog using the existing output naming.

The catalog is exported from the visible upload page. Each video's owner,
publication date, and full audio duration are checked again before transcription.
MLX accelerates the same Whisper small model on Apple Silicon.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import json
import os
from pathlib import Path
import time
import traceback
import wave
from zoneinfo import ZoneInfo

import bilibili_transcribe as core


TZ = ZoneInfo("Asia/Shanghai")


def now():
    return dt.datetime.now(TZ).isoformat(timespec="seconds")


def save_json(path, value):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def audio_duration(path):
    with wave.open(str(path), "rb") as audio:
        return audio.getnframes() / audio.getframerate()


def validate_info(info, row, uid, date_from, date_to):
    if str(info.get("uploader_id")) != uid:
        raise ValueError(f"Unexpected uploader: {info.get('uploader_id')}")
    if str(info.get("id")).split("_")[0] != row["bvid"]:
        raise ValueError("Video ID does not match catalog")
    timestamp = info.get("timestamp")
    if timestamp is None:
        raise ValueError("Missing publication timestamp")
    date = dt.datetime.fromtimestamp(timestamp, TZ).date().isoformat()
    if not date_from <= date <= date_to:
        raise ValueError(f"Publication date {date} outside requested range")
    if date != row["date"]:
        raise ValueError(f"Publication date {date} differs from catalog {row['date']}")
    if info.get("_type") in {"playlist", "multi_video"}:
        raise ValueError("Multi-part video requires an explicit per-part catalog")
    if not core.has_usable_audio_formats(info):
        raise ValueError("No usable audio stream; not a completed transcript")
    expected = sum(int(v) * 60 ** i for i, v in enumerate(reversed(row["duration"].split(":"))))
    duration = float(info.get("duration") or 0)
    if duration <= 0 or abs(expected - duration) > 3:
        raise ValueError(f"Metadata duration {duration} differs from catalog {expected}")
    return date, duration


def srt_time(value):
    ms = round(value * 1000)
    seconds, ms = divmod(ms, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{ms:03d}"


def write_index(task_dir, manifest):
    rows = []
    for item in manifest["items"]:
        title = html.escape(item["title"])
        links = []
        for key, label in [("raw_transcript", "原始转写"), ("readable_transcript", "简体分段"), ("srt", "时间轴")]:
            if item.get(key):
                rel = os.path.relpath(item[key], task_dir)
                links.append(f'<a href="{html.escape(rel, quote=True)}">{label}</a>')
        rows.append(f'<tr><td>{item["date"]}</td><td><a href="{item["url"]}">{title}</a></td><td>{item["duration"]}</td><td>{html.escape(item["status"])}</td><td>{" · ".join(links)}</td></tr>')
    completed = sum(i["status"] == "completed" for i in manifest["items"])
    document = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>美投侃热点转写目录</title>
<style>body{{font:16px/1.6 system-ui;max-width:1400px;margin:40px auto;padding:0 24px;color:#243042}}table{{border-collapse:collapse;width:100%}}td,th{{text-align:left;border-bottom:1px solid #ddd;padding:12px}}a{{color:#1769aa}}h1{{font-size:28px}}td:nth-child(2){{width:55%}}</style>
<h1>美投侃热点 · 视频转写</h1><p>{html.escape(manifest['date_from'])} 至 {html.escape(manifest['date_to'])}（北京时间）；完成 {completed} / {len(manifest['items'])} 个。更新：{manifest['updated_at']}</p>
<p>Whisper small / MLX 本机语音识别。原始稿保留识别结果；简体分段稿只作简繁转换和分段，无内容改写。专名和数字可能识别有误，可结合音频与时间轴核对。</p>
<table><thead><tr><th>发布日期</th><th>标题 / B站原视频</th><th>时长</th><th>状态</th><th>转写文件</th></tr></thead><tbody>{''.join(rows)}</tbody></table></html>'''
    (task_dir / "转写目录.html").write_text(document, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("catalog", type=Path)
    parser.add_argument("--uid", default="1962124785")
    parser.add_argument("--date-from", default="2026-10-01")
    parser.add_argument("--date-to", default="2026-11-07")
    parser.add_argument("--model", default="mlx-community/whisper-small-mlx")
    parser.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args()
    task_dir = args.catalog.resolve().parent
    output_root = task_dir.parent
    os.environ.setdefault("HF_HOME", str(Path(__file__).resolve().parent / ".launcher/huggingface"))
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    rows = list(csv.DictReader(args.catalog.open(encoding="utf-8"), delimiter="\t"))
    if len({r["bvid"] for r in rows}) != len(rows):
        raise ValueError("Duplicate video IDs in reviewed catalog")
    manifest_path = task_dir / "batch_manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if [i["bvid"] for i in manifest["items"]] != [r["bvid"] for r in rows]:
            raise ValueError("Catalog changed; inspect manifest before resuming")
    else:
        manifest = {"created_at": now(), "uid": args.uid, "uploader": "美投侃热点", "date_from": args.date_from,
                    "date_to": args.date_to, "catalog_source": f"https://space.bilibili.com/{args.uid}/upload/video",
                    "catalog_evidence": f"Reviewed catalog at {args.catalog.resolve()}; owner, date, duration and audio checked per video.",
                    "model": args.model, "backend": "mlx-whisper", "items": [dict(r, url=f"https://www.bilibili.com/video/{r['bvid']}/", status="pending") for r in rows]}
    manifest["pid"] = os.getpid()

    def checkpoint():
        manifest["updated_at"] = now()
        manifest["summary"] = {state: sum(i["status"] == state for i in manifest["items"]) for state in {i["status"] for i in manifest["items"]}}
        save_json(manifest_path, manifest)
        write_index(task_dir, manifest)

    checkpoint()
    core.ensure_dependencies()
    core.ensure_ffmpeg_available_for_subprocess()
    auth = core.load_bilibili_auth_config(core.DEFAULT_CONFIG_PATH)
    import mlx_whisper
    from opencc import OpenCC
    converter = OpenCC("t2s")
    for n, item in enumerate(manifest["items"], 1):
        if item["status"] == "completed":
            if not all(Path(item[k]).is_file() and Path(item[k]).stat().st_size for k in ["raw_transcript", "readable_transcript", "srt", "segments_json"]):
                raise ValueError(f"Completed result is missing files: {item['bvid']}")
            continue
        if item["status"] == "failed" and not args.retry_failed:
            continue
        start = time.monotonic()
        try:
            core.log(f"[{n}/{len(rows)}] {item['date']} {item['title']}")
            if not item.get("output_dir"):
                item["output_dir"] = str(core.create_title_output_directory(output_root, item["title"]))
            work_dir = Path(item["output_dir"])
            item["status"] = "checking"
            item["started_at"] = now()
            checkpoint()
            info, _ = core.collect_video_info(item["url"], core.build_yt_dlp_options(work_dir, auth))
            date, duration = validate_info(info, item, args.uid, args.date_from, args.date_to)
            item["verified_publication_date"] = date
            item["verified_duration_seconds"] = duration
            item["verified_title"] = info["title"]
            audio_path = work_dir / "source_audio.wav"
            if not audio_path.exists():
                item["status"] = "downloading"
                checkpoint()
                audio_path, title, category = core.download_audio(item["url"], work_dir, auth)
                item["video_type"] = category
                if title != info["title"]:
                    raise ValueError("Title changed between preflight and download")
            actual_duration = audio_duration(audio_path)
            if abs(actual_duration - duration) > max(3.0, duration * 0.005):
                raise ValueError(f"Audio may be incomplete: {actual_duration:.2f} vs {duration:.2f}s")
            item["audio_path"] = str(audio_path)
            item["audio_duration_seconds"] = actual_duration
            item["status"] = "transcribing"
            checkpoint()
            core.log(f"[{n}/{len(rows)}] 开始 MLX small 转写，完整音频 {actual_duration:.1f} 秒")
            inference_start = time.monotonic()
            result = mlx_whisper.transcribe(str(audio_path), path_or_hf_repo=args.model, language="zh", verbose=False,
                                            temperature=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0), condition_on_previous_text=False)
            item["transcription_seconds"] = round(time.monotonic() - inference_start, 2)
            segments = result.get("segments") or []
            transcript = core.build_transcript_result([s["text"] for s in segments], result.get("text", ""))
            if not segments or max(s["end"] for s in segments) < actual_duration - 30:
                raise ValueError("Transcript does not reach the final 30 seconds; inspect ending")
            item["raw_transcript"] = str(core.save_text_file(transcript.text, work_dir, item["title"], "_transcript_raw", ".txt"))
            paragraphs, current = [], ""
            for segment in segments:
                current += converter.convert(segment["text"].strip())
                if len(current) >= 140 and current.endswith(tuple("。！？.!?")) or len(current) >= 260:
                    paragraphs.append(current)
                    current = ""
            if current:
                paragraphs.append(current)
            item["readable_transcript"] = str(core.save_text_file("\n\n".join(paragraphs) + "\n", work_dir, item["title"], "_简体分段", ".txt"))
            srt = "\n\n".join(f"{i}\n{srt_time(s['start'])} --> {srt_time(s['end'])}\n{s['text'].strip()}" for i, s in enumerate(segments, 1)) + "\n"
            item["srt"] = str(core.save_text_file(srt, work_dir, item["title"], "_transcript", ".srt"))
            item["segments_json"] = str(core.save_json_file(result, work_dir, item["title"], "_segments"))
            item["transcript_characters"] = len(transcript.text)
            item["segment_count"] = len(segments)
            item["last_segment_end"] = max(s["end"] for s in segments)
            item["status"] = "completed"
            item.pop("error", None)
            item["completed_at"] = now()
            item["elapsed_seconds"] = round(time.monotonic() - start, 2)
            core.save_json_file(item, work_dir, item["title"], "_run_meta")
            core.log(f"[{n}/{len(rows)}] 完成：{len(transcript.text)} 字，耗时 {item['elapsed_seconds']} 秒")
        except Exception as exc:
            item["status"] = "failed"
            item["error"] = str(exc)
            item["traceback"] = traceback.format_exc()
            core.log(f"[{n}/{len(rows)}] 失败，保留进度：{exc}", "ERROR")
        checkpoint()
    manifest["finished_at"] = now()
    checkpoint()
    core.log(f"批次结束：{manifest['summary']}")


if __name__ == "__main__":
    main()
