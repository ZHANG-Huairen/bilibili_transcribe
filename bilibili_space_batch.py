#!/usr/bin/env python3
"""
批量抓取并转写 Bilibili UP 主投稿视频。

说明：
- 复用 bilibili_transcribe.py 中现有的下载、转写、整理逻辑。
- 负责列出空间页全部视频，按顺序逐条处理。
- 对没有可用音频流的视频写入占位结果，并记录到批量清单中。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import time
import traceback
from pathlib import Path

import bilibili_transcribe as core


DEFAULT_NETWORK_RETRIES = 3
DEFAULT_RETRY_WAIT_SECONDS = 3.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="批量抓取并转写 Bilibili UP 主视频")
    parser.add_argument(
        "space_url",
        help="UP 主页或投稿页链接，例如 https://space.bilibili.com/<uid>/upload/video",
    )
    parser.add_argument(
        "--model",
        default=core.DEFAULT_MODEL,
        help="Whisper 模型名（默认 small，可选 tiny/base/small/medium/large-v3）",
    )
    parser.add_argument(
        "--backend",
        default=core.DEFAULT_BACKEND,
        choices=["auto", "faster-whisper", "openai-whisper"],
        help="转写后端，默认 auto",
    )
    parser.add_argument(
        "--config",
        default=str(core.DEFAULT_CONFIG_PATH),
        help="配置文件路径，默认当前目录下的 config.json",
    )
    parser.add_argument(
        "--skip-formatting",
        action="store_true",
        help="只生成原始转写，跳过 AI 整理",
    )
    parser.add_argument(
        "--start-index",
        type=int,
        default=1,
        help="从第几条视频开始处理，默认 1",
    )
    parser.add_argument(
        "--end-index",
        type=int,
        default=0,
        help="处理到第几条视频结束，默认 0 表示全部",
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help="批量输出目录；默认自动按时间戳创建",
    )
    return parser.parse_args()


def run_with_retries(label: str, func, attempts: int = DEFAULT_NETWORK_RETRIES, wait_seconds: float = DEFAULT_RETRY_WAIT_SECONDS):
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            return func()
        except Exception as exc:
            last_error = exc
            if attempt >= attempts:
                raise
            core.log(
                f"{label} 失败，准备在 {wait_seconds:.0f} 秒后重试（{attempt}/{attempts - 1}）。原因：{exc}",
                "WARN",
            )
            time.sleep(wait_seconds)
    raise last_error


def build_space_yt_dlp_options(space_url: str, auth_cfg: core.BilibiliAuthConfig) -> dict:
    headers = dict(auth_cfg.http_headers)
    headers["Referer"] = space_url
    ydl_opts = {
        "extract_flat": True,
        "quiet": True,
        "skip_download": True,
        "lazy_playlist": False,
        "http_headers": headers,
    }
    if auth_cfg.cookie_file is not None:
        ydl_opts["cookiefile"] = str(auth_cfg.cookie_file)
    if auth_cfg.cookies_from_browser is not None:
        ydl_opts["cookiesfrombrowser"] = auth_cfg.cookies_from_browser
    return ydl_opts


def list_space_videos(space_url: str, auth_cfg: core.BilibiliAuthConfig) -> tuple[str, list[dict]]:
    import yt_dlp

    def _extract() -> dict:
        with yt_dlp.YoutubeDL(build_space_yt_dlp_options(space_url, auth_cfg)) as ydl:
            return ydl.extract_info(space_url, download=False)

    info = run_with_retries("拉取空间页视频列表", _extract)

    playlist_id = str((info or {}).get("id") or "").strip() or "unknown_uid"
    entries = []
    for index, entry in enumerate((info or {}).get("entries") or [], start=1):
        if not isinstance(entry, dict):
            continue
        video_url = str(entry.get("url") or "").strip()
        bvid = str(entry.get("id") or "").strip() or None
        if video_url and not video_url.startswith("http") and bvid:
            video_url = f"https://www.bilibili.com/video/{bvid}"
        if not video_url:
            continue
        entries.append(
            {
                "index": index,
                "bvid": bvid,
                "url": video_url,
            }
        )
    return playlist_id, entries


def update_manifest(manifest_path: Path, manifest: dict) -> None:
    manifest["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def build_placeholder_transcript(title: str) -> str:
    return (
        f"视频《{title}》未生成语音转写。\n"
        "原因：当前可获取到的视频格式中没有可用音频流。"
    )


def process_video(
    item: dict,
    output_root: Path,
    auth_cfg: core.BilibiliAuthConfig,
    args: argparse.Namespace,
    llm_cfg: dict | None,
) -> dict:
    video_index = item["index"]
    bvid = item.get("bvid")
    video_url = item["url"]
    work_dir = output_root / f"{video_index:03d}_{bvid or 'unknown'}"
    work_dir.mkdir(parents=True, exist_ok=True)

    core.log(f"[{video_index}] 开始处理：{video_url}")
    probe_opts = core.build_yt_dlp_options(work_dir, auth_cfg)
    info, _ = run_with_retries(
        f"[{video_index}] 预检视频信息",
        lambda: core.collect_video_info(video_url, probe_opts),
    )
    title = str((info or {}).get("title") or bvid or f"video_{video_index}").strip()

    result = {
        "index": video_index,
        "bvid": bvid,
        "url": video_url,
        "title": title,
        "output_dir": str(work_dir.resolve()),
        "status": "pending",
    }

    if not core.has_usable_audio_formats(info):
        placeholder_text = build_placeholder_transcript(title)
        raw_path = core.save_text_file(placeholder_text, work_dir, title, "_transcript_raw", ".txt")
        result.update(
            {
                "status": "no_audio",
                "raw_transcript": str(raw_path.resolve()),
                "formatted_transcript": None,
            }
        )
        meta_path = core.save_json_file(result, work_dir, title, "_run_meta")
        result["meta"] = str(meta_path.resolve())
        core.log(f"[{video_index}] 该视频没有可用音频流，已写入占位结果。", "WARN")
        return result

    audio_path, title, video_type_label = run_with_retries(
        f"[{video_index}] 下载音频",
        lambda: core.download_audio(video_url, work_dir, auth_cfg),
    )
    result["title"] = title
    result["video_type"] = video_type_label
    result["audio_path"] = str(audio_path.resolve())

    transcript = core.transcribe_audio(audio_path, model_name=args.model, backend=args.backend)
    raw_path = core.save_text_file(transcript.text, work_dir, title, "_transcript_raw", ".txt")
    result["raw_transcript"] = str(raw_path.resolve())

    if args.skip_formatting:
        result["status"] = "raw_only"
        result["formatted_transcript"] = None
    else:
        if llm_cfg is None or not llm_cfg.get("enabled"):
            result["status"] = "raw_only"
            result["formatted_transcript"] = None
        else:
            formatted_text, formatting_had_fallback = core.format_transcript_with_llm(transcript, title, llm_cfg)
            formatted_path = core.save_text_file(
                formatted_text,
                work_dir,
                title,
                "_transcript_formatted",
                ".txt",
            )
            result["formatted_transcript"] = str(formatted_path.resolve())
            result["formatting_had_fallback"] = formatting_had_fallback
            result["status"] = "formatting_fallback" if formatting_had_fallback else "completed"

    meta_path = core.save_json_file(result, work_dir, title, "_run_meta")
    result["meta"] = str(meta_path.resolve())
    core.log(f"[{video_index}] 处理完成：{title}")
    return result


def main() -> None:
    args = parse_args()
    config_path = Path(args.config).expanduser()
    if not config_path.is_absolute():
        config_path = Path.cwd() / config_path

    if args.start_index <= 0:
        raise ValueError("--start-index 必须大于 0。")
    if args.end_index < 0:
        raise ValueError("--end-index 不能小于 0。")
    if args.end_index and args.end_index < args.start_index:
        raise ValueError("--end-index 不能小于 --start-index。")

    core.ensure_dependencies()
    auth_cfg = core.load_bilibili_auth_config(config_path)
    llm_cfg = None if args.skip_formatting else core.load_llm_formatting_config(config_path)

    output_dir_raw = str(args.output_dir or "").strip()
    if output_dir_raw:
        batch_root = Path(output_dir_raw).expanduser()
        if not batch_root.is_absolute():
            batch_root = Path.cwd() / batch_root
    else:
        timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        batch_root = Path.cwd() / "transcript_output" / f"space_batch_{timestamp}"
    batch_root.mkdir(parents=True, exist_ok=True)
    manifest_path = batch_root / "batch_manifest.json"

    core.log(f"批量输出目录：{batch_root}")
    core.log(f"B站登录态：{core.describe_bilibili_auth_config(auth_cfg)}")
    playlist_id, entries = list_space_videos(args.space_url, auth_cfg)
    total_entries = len(entries)
    core.log(f"共发现 {total_entries} 个视频，UID={playlist_id}")

    selected_entries = [
        item
        for item in entries
        if item["index"] >= args.start_index and (args.end_index == 0 or item["index"] <= args.end_index)
    ]
    core.log(f"本次将处理 {len(selected_entries)} 个视频。")

    manifest = {
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "space_url": args.space_url,
        "uid": playlist_id,
        "model": args.model,
        "backend": args.backend,
        "skip_formatting": args.skip_formatting,
        "output_dir": str(batch_root.resolve()),
        "total_videos": total_entries,
        "selected_videos": len(selected_entries),
        "items": [],
    }
    update_manifest(manifest_path, manifest)

    success_count = 0
    fail_count = 0
    no_audio_count = 0
    total_start = time.perf_counter()

    for item in selected_entries:
        started_at = time.perf_counter()
        try:
            result = process_video(item, batch_root, auth_cfg, args, llm_cfg)
            result["elapsed_seconds"] = round(time.perf_counter() - started_at, 1)
            manifest["items"].append(result)
            if result["status"] == "completed":
                success_count += 1
            elif result["status"] == "no_audio":
                no_audio_count += 1
            else:
                success_count += 1
        except Exception as exc:
            fail_count += 1
            failure = {
                "index": item["index"],
                "bvid": item.get("bvid"),
                "url": item["url"],
                "status": "failed",
                "error": str(exc),
                "traceback": traceback.format_exc(),
                "elapsed_seconds": round(time.perf_counter() - started_at, 1),
            }
            manifest["items"].append(failure)
            core.log(f"[{item['index']}] 处理失败：{exc}", "ERROR")
        update_manifest(manifest_path, manifest)

    total_elapsed = round(time.perf_counter() - total_start, 1)
    summary = {
        "completed_or_raw_only": success_count,
        "no_audio": no_audio_count,
        "failed": fail_count,
        "elapsed_seconds": total_elapsed,
    }
    manifest["summary"] = summary
    update_manifest(manifest_path, manifest)
    core.log(
        "批量处理结束："
        f"成功/仅原始={success_count}，"
        f"无音频={no_audio_count}，"
        f"失败={fail_count}，"
        f"总耗时={total_elapsed} 秒"
    )
    core.log(f"批量清单：{manifest_path}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        core.log("用户中断批量任务。", "WARN")
        raise SystemExit(130)
