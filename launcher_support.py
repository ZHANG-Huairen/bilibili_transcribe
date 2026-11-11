#!/usr/bin/env python3
"""双击启动器的环境检查和原有转写程序适配层。"""

from __future__ import annotations

import importlib.metadata
import os
from pathlib import Path
import subprocess
import sys


PROJECT_DIR = Path(__file__).resolve().parent


def check_environment() -> None:
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError("启动器需要 Python 3.12，将自动重建环境。")
    for line in (PROJECT_DIR / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, expected = line.split("==", 1)
        actual = importlib.metadata.version(name)
        if actual != expected:
            raise RuntimeError(f"{name} 需要 {expected}，当前为 {actual}。")

    # 实际导入原生扩展和执行 ffmpeg，不能仅凭包名存在判定可运行。
    import imageio_ffmpeg
    import torch
    import whisper
    import yt_dlp

    assert torch.ones(2).sum().item() == 2
    subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-version"],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=20,
    )
    print("环境检查通过：Python、下载器、Whisper、PyTorch、ffmpeg 均可运行。", flush=True)


def run_transcription(args: list[str]) -> None:
    import bilibili_transcribe as transcriber

    # 原 CLI 会在转写结束后才读取整理配置，提前判断可避免长视频
    # 转写成功后因为缺少整理密钥而以错误状态结束。
    sys.argv = [str(PROJECT_DIR / "bilibili_transcribe.py"), *args]
    parsed = transcriber.parse_args()
    if not parsed.skip_formatting:
        try:
            cfg = transcriber.load_llm_formatting_config(Path(parsed.config).expanduser())
        except (OSError, ValueError, RuntimeError, TypeError, AttributeError):
            print("AI 整理配置不可用，本次自动仅保存原始转写文本。", flush=True)
            sys.argv.append("--skip-formatting")
        else:
            if cfg.get("enabled"):
                print("已启用现有配置中的 AI 文本整理。", flush=True)

    os.environ["BILI_TRANSCRIBE_CHILD"] = "1"
    transcriber.main()


if __name__ == "__main__":
    try:
        if sys.argv[1:] == ["--check"]:
            check_environment()
        else:
            run_transcription(sys.argv[1:])
    except KeyboardInterrupt:
        print("\n已停止。", flush=True)
        sys.exit(130)
    except Exception as exc:
        print(f"运行失败：{exc}", file=sys.stderr, flush=True)
        sys.exit(1)
