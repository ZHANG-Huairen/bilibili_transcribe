#!/usr/bin/env python3
"""
可续跑的 Bilibili UP 主批量转写总控程序。

职责：
- 管理批量任务状态和断点续跑。
- 跳过已完成/无音频的视频。
- 失败重试，并持续更新 supervisor_manifest.json。
- 复用现有下载、转写、AI 断句整理能力。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import time
import traceback
from pathlib import Path

import bilibili_space_batch as batch
import bilibili_transcribe as core


MANIFEST_NAME = "supervisor_manifest.json"
DONE_STATUSES = {"completed", "raw_only", "no_audio"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bilibili UP 主批量转写总控")
    parser.add_argument(
        "space_url",
        help="UP 主页或投稿页链接，例如 https://space.bilibili.com/<uid>/upload/video",
    )
    parser.add_argument(
        "--model",
        default=core.DEFAULT_MODEL,
        help="Whisper 模型名（默认 small）",
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
        help="只生成原始转写，跳过 AI 断句整理",
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help="任务输出目录；存在 manifest 时会自动续跑",
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
        "--max-items-per-run",
        type=int,
        default=0,
        help="本次最多处理多少条，默认 0 表示不限制",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="续跑时重新尝试之前失败的条目",
    )
    return parser.parse_args()


def resolve_output_dir(args: argparse.Namespace) -> Path:
    raw = str(args.output_dir or "").strip()
    if raw:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        return path
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path.cwd() / "transcript_output" / f"space_supervisor_{timestamp}"


def manifest_path_for(output_dir: Path) -> Path:
    return output_dir / MANIFEST_NAME


def load_manifest(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(path: Path, manifest: dict) -> None:
    manifest["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def build_items_index(items: list[dict]) -> dict[int, dict]:
    indexed: dict[int, dict] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        index = item.get("index")
        if isinstance(index, int):
            indexed[index] = item
    return indexed


def rebuild_summary(manifest: dict) -> None:
    items = manifest.get("items") or []
    summary = {
        "completed": 0,
        "raw_only": 0,
        "no_audio": 0,
        "formatting_fallback": 0,
        "failed": 0,
        "pending": 0,
        "processed": 0,
    }
    for item in items:
        status = str(item.get("status") or "pending")
        if status in summary:
            summary[status] += 1
        else:
            summary["pending"] += 1
        if status != "pending":
            summary["processed"] += 1
    manifest["summary"] = summary


def prepare_manifest(
    args: argparse.Namespace,
    output_dir: Path,
    config_path: Path,
    auth_cfg: core.BilibiliAuthConfig,
) -> dict:
    path = manifest_path_for(output_dir)
    existing = load_manifest(path)
    if existing is not None:
        return existing

    playlist_id, catalog = batch.list_space_videos(args.space_url, auth_cfg)
    manifest = {
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "space_url": args.space_url,
        "uid": playlist_id,
        "model": args.model,
        "backend": args.backend,
        "skip_formatting": args.skip_formatting,
        "config_path": str(config_path.resolve()),
        "output_dir": str(output_dir.resolve()),
        "catalog": catalog,
        "items": [],
        "heartbeat": {
            "state": "idle",
            "current_index": None,
            "current_url": None,
            "message": "initialized",
        },
    }
    rebuild_summary(manifest)
    save_manifest(path, manifest)
    return manifest


def select_catalog_items(manifest: dict, args: argparse.Namespace) -> list[dict]:
    catalog = manifest.get("catalog") or []
    selected = [
        item
        for item in catalog
        if item["index"] >= args.start_index and (args.end_index == 0 or item["index"] <= args.end_index)
    ]
    if args.max_items_per_run > 0:
        selected = selected[: args.max_items_per_run]
    return selected


def should_skip(existing: dict | None, retry_failed: bool) -> bool:
    if existing is None:
        return False
    status = str(existing.get("status") or "")
    if status in DONE_STATUSES:
        return True
    if status == "failed" and not retry_failed:
        return True
    return False


def update_heartbeat(manifest: dict, state: str, item: dict | None, message: str) -> None:
    manifest["heartbeat"] = {
        "state": state,
        "current_index": item.get("index") if item else None,
        "current_url": item.get("url") if item else None,
        "message": message,
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
    }


def main() -> None:
    args = parse_args()
    if args.start_index <= 0:
        raise ValueError("--start-index 必须大于 0。")
    if args.end_index < 0:
        raise ValueError("--end-index 不能小于 0。")
    if args.end_index and args.end_index < args.start_index:
        raise ValueError("--end-index 不能小于 --start-index。")
    if args.max_items_per_run < 0:
        raise ValueError("--max-items-per-run 不能小于 0。")

    config_path = Path(args.config).expanduser()
    if not config_path.is_absolute():
        config_path = Path.cwd() / config_path

    core.ensure_dependencies()
    auth_cfg = core.load_bilibili_auth_config(config_path)
    llm_cfg = None if args.skip_formatting else core.load_llm_formatting_config(config_path)

    output_dir = resolve_output_dir(args)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_path_for(output_dir)
    manifest = prepare_manifest(args, output_dir, config_path, auth_cfg)

    core.log(f"总控输出目录：{output_dir}")
    core.log(f"总控清单：{manifest_path}")
    core.log(f"B站登录态：{core.describe_bilibili_auth_config(auth_cfg)}")

    catalog_items = select_catalog_items(manifest, args)
    items_index = build_items_index(manifest.get("items") or [])
    total_selected = len(catalog_items)
    core.log(f"本次选择 {total_selected} 个视频。")

    run_start = time.perf_counter()

    for item in catalog_items:
        existing = items_index.get(item["index"])
        if should_skip(existing, args.retry_failed):
            core.log(f"[{item['index']}] 已有状态 {existing['status']}，跳过。")
            continue

        update_heartbeat(manifest, "running", item, "processing")
        save_manifest(manifest_path, manifest)

        started_at = time.perf_counter()
        attempt = int(existing.get("attempt") or 0) + 1 if existing else 1
        try:
            result = batch.process_video(item, output_dir, auth_cfg, args, llm_cfg)
            result["elapsed_seconds"] = round(time.perf_counter() - started_at, 1)
            result["attempt"] = attempt
            items_index[item["index"]] = result
        except Exception as exc:
            items_index[item["index"]] = {
                "index": item["index"],
                "bvid": item.get("bvid"),
                "url": item["url"],
                "status": "failed",
                "attempt": attempt,
                "elapsed_seconds": round(time.perf_counter() - started_at, 1),
                "error": str(exc),
                "traceback": traceback.format_exc(),
            }
            core.log(f"[{item['index']}] 总控记录失败：{exc}", "ERROR")

        manifest["items"] = [items_index[key] for key in sorted(items_index)]
        rebuild_summary(manifest)
        save_manifest(manifest_path, manifest)

    update_heartbeat(manifest, "idle", None, "run_finished")
    manifest["run_elapsed_seconds"] = round(time.perf_counter() - run_start, 1)
    rebuild_summary(manifest)
    save_manifest(manifest_path, manifest)
    core.log(
        "总控运行结束："
        f"completed={manifest['summary']['completed']}, "
        f"raw_only={manifest['summary']['raw_only']}, "
        f"no_audio={manifest['summary']['no_audio']}, "
        f"failed={manifest['summary']['failed']}"
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        core.log("用户中断总控程序。", "WARN")
        raise SystemExit(130)
