#!/usr/bin/env python3
"""
Bilibili 视频转写脚本

功能：
1. 启动后自动在 macOS 新开终端窗口运行。
2. 提示输入 Bilibili 视频链接。
3. 下载音频并优先使用更快的后端进行转写。
4. 保存原始转写文本。
5. 读取 config.json，调用兼容 OpenAI Chat Completions 的 API 直接整理转写稿。
6. 再发起一次独立的 API 语义复核，检查整理稿是否偏离原意。
7. 输出关键日志，并在结束后给出结果文件位置。

依赖：
- pip install yt-dlp faster-whisper openai-whisper
- ffmpeg (系统命令)
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import html
import http.cookiejar
import importlib.util
import json
import os
import platform
import re
import shlex
import shutil
import ssl
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


SCRIPT_FLAG = "BILI_TRANSCRIBE_CHILD"
DEFAULT_MODEL = "small"
DEFAULT_BACKEND = "auto"
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config.json"
DEFAULT_FORMAT_PROVIDER = "deepseek"
DEFAULT_FORMAT_BATCH_CHARS = 12000
DEFAULT_REQUEST_TIMEOUT = 180
DEFAULT_TEMPERATURE = 0.0
DEFAULT_PARALLEL_REQUESTS = 4
DEFAULT_RETRY_COUNT = 2
DEFAULT_FASTER_WHISPER_CPU_BATCH_SIZE = 8
DEFAULT_FASTER_WHISPER_GPU_BATCH_SIZE = 16
DEFAULT_BILIBILI_PAGE_TIMEOUT = 30
DEFAULT_BILIBILI_REFERER = "https://www.bilibili.com/"
DEFAULT_BILIBILI_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/134.0.0.0 Safari/537.36"
)
SUPPORTED_COOKIE_BROWSERS = {
    "brave",
    "chrome",
    "chromium",
    "edge",
    "firefox",
    "opera",
    "safari",
    "vivaldi",
    "whale",
}

DEFAULT_PROVIDER_SETTINGS = {
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "api_key_env": "DEEPSEEK_API_KEY",
    },
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "api_key_env": "DASHSCOPE_API_KEY",
    },
}

FORMATTER_SYSTEM_PROMPT = """你是一个负责整理转写稿格式的助手。

**绝对要求**
- **不要改变原文意思。**
- **不要删除任何关键信息。**
- **不要补充原文中没有的新事实、新观点、新结论。**
- **不要总结，不要缩写，不要偷懒省略。**

**允许的整理动作**
- **补充标点。**
- **调整分段和换行。**
- **必须完成断句，让口语化长串文本变成通顺、可读的句子。**
- **仅在你非常确定时，做极少量、最小限度的字词修正，例如明显的 ASR 同音错字、明显口误、明显连写断句错误。**
- **输出纯文本格式，重点是清晰分段；优先一到两句一段，不要出现整页只有一大段。**
- **不要使用 Markdown 标题或列表。**

**严格限制**
- **对不确定的人名、公司名、产品名、芯片名、架构名、专有名词、技术术语，宁可保留原文，也不要自作主张标准化。**
- **不要擅自统一原文里反复出现的不同叫法。**
- **不要改数字、单位、型号、代际、缩写，除非你百分之百确定原文只是显而易见的识别错误。**
- **如果你不确定某处该不该改，就保留原文。**

**输出要求**
- **只输出整理后的正文，不要解释。**
"""


@dataclass
class TranscriptResult:
    text: str
    segments: list[str]


@dataclass
class BilibiliAuthConfig:
    cookie_file: Path | None
    cookies_from_browser: tuple[str, str | None, str | None, str | None] | None
    http_headers: dict[str, str]

    @property
    def has_auth(self) -> bool:
        return self.cookie_file is not None or self.cookies_from_browser is not None


@dataclass
class BilibiliVideoProbe:
    resolved_url: str
    title: str | None
    is_charged: bool
    detection_source: str

    @property
    def category_label(self) -> str:
        return "充电视频" if self.is_charged else "普通视频"


class YtDlpMessageCollector:
    """收集 yt-dlp 日志，便于识别试看/权限类提示。"""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def debug(self, msg: str) -> None:
        if isinstance(msg, str):
            self.messages.append(msg)

    def warning(self, msg: str) -> None:
        if isinstance(msg, str):
            self.messages.append(msg)

    def error(self, msg: str) -> None:
        if isinstance(msg, str):
            self.messages.append(msg)


def log(msg: str, level: str = "INFO") -> None:
    """统一日志输出格式。"""
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] [{level}] {msg}", flush=True)


def has_module(module_name: str) -> bool:
    """轻量检查模块是否可用，避免导入大模块造成额外耗时。"""
    return importlib.util.find_spec(module_name) is not None


def ensure_dependencies() -> None:
    """检查运行所需依赖。"""
    missing = []

    if not has_module("yt_dlp"):
        missing.append("yt-dlp")

    if not (has_module("faster_whisper") or has_module("whisper")):
        missing.append("faster-whisper 或 openai-whisper")

    if shutil.which("ffmpeg") is None:
        missing.append("ffmpeg（系统命令）")

    if missing:
        raise RuntimeError(
            "缺少依赖："
            + "、".join(missing)
            + "\n请先执行：pip install yt-dlp faster-whisper openai-whisper\n"
            + "并安装 ffmpeg（例如 macOS: brew install ffmpeg）"
        )


def is_valid_bilibili_url(url: str) -> bool:
    """简单校验是否是常见 B 站链接。"""
    url = url.strip()
    patterns = [
        r"^https?://(www\.)?bilibili\.com/video/",
        r"^https?://b23\.tv/",
        r"^https?://(www\.)?bilibili\.com/medialist/",
    ]
    return any(re.search(p, url, re.IGNORECASE) for p in patterns)


def launch_in_new_terminal_if_needed() -> None:
    """如果尚未在子终端中运行，则新开终端窗口后退出当前进程。"""
    if os.environ.get(SCRIPT_FLAG) == "1":
        return

    if os.environ.get("TERM_PROGRAM", "").lower() == "vscode":
        log("检测到 VS Code 集成终端，将直接在当前终端执行。")
        return

    if platform.system() != "Darwin":
        log("当前系统不是 macOS，将在当前终端继续执行。", "WARN")
        return

    script_path = Path(__file__).resolve()
    args = " ".join(shlex.quote(a) for a in sys.argv[1:])
    cmd = (
        f"cd {shlex.quote(str(script_path.parent))} && "
        f"{SCRIPT_FLAG}=1 {shlex.quote(sys.executable)} {shlex.quote(str(script_path))}"
    )
    if args:
        cmd += f" {args}"

    escaped = cmd.replace("\\", "\\\\").replace('"', '\\"')
    applescript = (
        'tell application "Terminal"\n'
        "activate\n"
        f'do script "{escaped}"\n'
        "end tell"
    )

    try:
        subprocess.run(["osascript", "-e", applescript], check=True)
        log("已在新终端窗口启动转写流程。", "INFO")
        sys.exit(0)
    except Exception as exc:
        log(f"新终端窗口启动失败，将在当前终端继续。原因：{exc}", "WARN")


def sanitize_title(title: str) -> str:
    """生成安全的文件名标题。"""
    return re.sub(r"[\\/:*?\"<>|]", "_", title).strip() or "bilibili_video"


def load_cookie_jar_from_file(cookie_file: Path | None) -> http.cookiejar.CookieJar | None:
    """加载 Netscape cookies.txt，供页面探测复用。"""
    if cookie_file is None:
        return None

    cookie_jar = http.cookiejar.MozillaCookieJar(str(cookie_file))
    try:
        cookie_jar.load(ignore_discard=True, ignore_expires=True)
    except Exception as exc:
        raise RuntimeError(
            f"无法读取 bilibili_auth.cookie_file：{cookie_file}。请确认它是标准 cookies.txt 格式。"
        ) from exc

    return cookie_jar


def build_bilibili_page_opener(auth_cfg: BilibiliAuthConfig) -> urllib.request.OpenerDirector:
    """为页面探测构造带 cookies 的 opener。"""
    handlers: list[object] = []
    cookie_jar = load_cookie_jar_from_file(auth_cfg.cookie_file)
    if cookie_jar is not None:
        handlers.append(urllib.request.HTTPCookieProcessor(cookie_jar))
    return urllib.request.build_opener(*handlers)


def extract_embedded_json(page_text: str, variable_name: str) -> dict | None:
    """从页面脚本中提取嵌入的 JSON。"""
    pattern = re.escape(variable_name) + r"\s*=\s*(\{.*?\})\s*;"
    match = re.search(pattern, page_text, flags=re.DOTALL)
    if not match:
        return None

    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None

    return payload if isinstance(payload, dict) else None


def extract_title_from_html(page_text: str) -> str | None:
    """从 HTML 标题中兜底提取视频标题。"""
    match = re.search(r"<title[^>]*>(.*?)</title>", page_text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None

    title = html.unescape(re.sub(r"\s+", " ", match.group(1))).strip()
    title = re.sub(r"_哔哩哔哩_bilibili$", "", title).strip()
    return title or None


def infer_bilibili_charge_flag(initial_state: dict | None, page_text: str) -> bool | None:
    """优先从页面初始状态判断是否为充电专属视频。"""
    if isinstance(initial_state, dict):
        video_data = initial_state.get("videoData") or initial_state.get("videoInfo") or {}
        if isinstance(video_data, dict) and "is_upower_exclusive" in video_data:
            return bool(video_data.get("is_upower_exclusive"))

    match = re.search(r'"is_upower_exclusive"\s*:\s*(true|false)', page_text)
    if match:
        return match.group(1) == "true"

    return None


def probe_bilibili_video(url: str, auth_cfg: BilibiliAuthConfig) -> BilibiliVideoProbe:
    """访问视频页面，识别普通/充电视频并尽量提取标题。"""
    opener = build_bilibili_page_opener(auth_cfg)
    request = urllib.request.Request(url, headers=auth_cfg.http_headers, method="GET")

    with opener.open(request, timeout=DEFAULT_BILIBILI_PAGE_TIMEOUT) as response:
        resolved_url = response.geturl()
        charset = response.headers.get_content_charset() or "utf-8"
        page_text = response.read().decode(charset, errors="replace")

    initial_state = extract_embedded_json(page_text, "window.__INITIAL_STATE__")
    title = None
    if isinstance(initial_state, dict):
        video_data = initial_state.get("videoData") or initial_state.get("videoInfo") or {}
        if isinstance(video_data, dict):
            title = str(video_data.get("title") or "").strip() or None
    if title is None:
        title = extract_title_from_html(page_text)

    charge_flag = infer_bilibili_charge_flag(initial_state, page_text)
    if charge_flag is None:
        charge_flag = False
        detection_source = "fallback_default"
    else:
        detection_source = "page_initial_state"

    return BilibiliVideoProbe(
        resolved_url=resolved_url,
        title=title,
        is_charged=charge_flag,
        detection_source=detection_source,
    )


def build_yt_dlp_options(work_dir: Path, auth_cfg: BilibiliAuthConfig) -> dict:
    """构造统一的 yt-dlp 参数。"""
    ydl_opts = {
        "format": "bestaudio/best*[acodec!=none]/best[acodec!=none]",
        "outtmpl": str(work_dir / "source_audio.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "http_headers": auth_cfg.http_headers,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "wav",
            }
        ],
        "postprocessor_args": ["-ar", "16000", "-ac", "1", "-vn"],
    }

    if auth_cfg.cookie_file is not None:
        ydl_opts["cookiefile"] = str(auth_cfg.cookie_file)
    if auth_cfg.cookies_from_browser is not None:
        ydl_opts["cookiesfrombrowser"] = auth_cfg.cookies_from_browser

    return ydl_opts


def looks_like_supporter_only_message(message: str) -> bool:
    """识别 yt-dlp 关于充电视频试看/权限不足的提示。"""
    lower = message.lower()
    return (
        "supporter-only video" in lower
        or ("supporter" in lower and "preview" in lower)
        or "试看" in message
    )


def collect_video_info(url: str, ydl_opts: dict) -> tuple[dict, YtDlpMessageCollector]:
    """预检视频 metadata，避免直接下载到试看音频。"""
    import yt_dlp

    collector = YtDlpMessageCollector()
    probe_opts = dict(ydl_opts)
    probe_opts.pop("format", None)
    probe_opts.pop("postprocessors", None)
    probe_opts.pop("postprocessor_args", None)
    probe_opts["logger"] = collector
    probe_opts["no_warnings"] = False

    with yt_dlp.YoutubeDL(probe_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    return info, collector


def has_usable_audio_formats(info: dict) -> bool:
    """判断 yt-dlp 返回的格式列表里是否存在可用音频流。"""
    formats = info.get("formats") or []
    for fmt in formats:
        if not isinstance(fmt, dict):
            continue
        if fmt.get("vcodec") == "none":
            return True
        acodec = str(fmt.get("acodec") or "").strip().lower()
        if acodec and acodec not in {"none", "unknown"}:
            return True
    return False


def raise_charge_video_access_error(auth_cfg: BilibiliAuthConfig, detail: str | None = None) -> None:
    """统一抛出充电视频的登录态错误。"""
    if auth_cfg.has_auth:
        message = "该视频是充电视频，但当前登录态无法提取完整音频。"
    else:
        message = "该视频是充电视频，当前未配置可用的 B 站登录态。"

    hint = (
        "请在 config.json 的 bilibili_auth 中配置 cookie_file 或 cookies_from_browser，"
        "并确认使用的是已经为该视频充电的 B 站账号。"
    )
    if detail:
        raise RuntimeError(f"{message}{hint}\n原始原因：{detail}")
    raise RuntimeError(f"{message}{hint}")


def download_audio(url: str, work_dir: Path, auth_cfg: BilibiliAuthConfig) -> tuple[Path, str, str]:
    """下载 B 站视频音频，返回音频文件路径、视频标题和视频类型。"""
    import yt_dlp

    audio_path = work_dir / "source_audio.wav"
    ydl_opts = build_yt_dlp_options(work_dir, auth_cfg)
    detected_category: str | None = None
    title: str | None = None

    try:
        page_probe = probe_bilibili_video(url, auth_cfg)
        detected_category = page_probe.category_label
        title = page_probe.title
        log(f"视频类型识别：该视频是{detected_category}。")
    except Exception as exc:
        log(f"视频页面预探测失败，将回退到 yt-dlp 元数据预检。原因：{exc}", "WARN")

    if detected_category == "充电视频" and not auth_cfg.has_auth:
        raise_charge_video_access_error(auth_cfg)

    try:
        info, collector = collect_video_info(url, ydl_opts)
    except Exception as exc:
        error_text = str(exc)
        if detected_category != "充电视频" and looks_like_supporter_only_message(error_text):
            detected_category = "充电视频"
            log("视频类型识别更新：该视频是充电视频。")
        if detected_category == "充电视频":
            try:
                raise_charge_video_access_error(auth_cfg, detail=error_text)
            except RuntimeError as charge_exc:
                raise charge_exc from exc
        raise

    if title is None:
        title = str((info or {}).get("title") or "").strip() or None

    if any(looks_like_supporter_only_message(message) for message in collector.messages):
        if detected_category != "充电视频":
            detected_category = "充电视频"
            log("视频类型识别更新：该视频是充电视频。")
        raise_charge_video_access_error(
            auth_cfg,
            detail="当前账号态只能拿到试看片段，请刷新 cookies 或更换为已为该视频充电的账号。",
        )

    if detected_category is None:
        detected_category = "普通视频"
        log(f"视频类型识别：该视频是{detected_category}。")
    elif detected_category == "充电视频":
        log("检测到充电视频，将使用已配置的 B 站登录态提取完整音频。")

    if not has_usable_audio_formats(info):
        raise RuntimeError("该视频没有可用音频流，无法执行语音转写。")

    log("开始下载并提取音频...")
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    title = str((info or {}).get("title") or title or "unknown_title").strip() or "unknown_title"

    if not audio_path.exists():
        audio_files = sorted(
            list(work_dir.glob("*.wav")) + list(work_dir.glob("*.mp3")) + list(work_dir.glob("*.m4a")),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not audio_files:
            raise RuntimeError("音频下载完成但未找到可用音频文件。")
        audio_path = audio_files[0]

    log(f"音频文件已生成：{audio_path}")
    return audio_path, title, detected_category


def detect_torch_device() -> str:
    """检测可用推理设备。"""
    if not has_module("torch"):
        return "cpu"

    try:
        import torch
    except Exception as exc:
        log(f"torch 导入失败，回退到 CPU。原因：{exc}", "WARN")
        return "cpu"

    if torch.cuda.is_available():
        return "cuda"
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def normalize_segments(segments: list[str]) -> list[str]:
    """清理空分段，并保留原有顺序。"""
    normalized = [segment for segment in segments if isinstance(segment, str) and segment]
    return normalized


def build_transcript_result(segments: list[str], fallback_text: str = "") -> TranscriptResult:
    """用分段结果构建统一转写对象。"""
    segments = normalize_segments(segments)
    text = "".join(segments).strip() if segments else fallback_text.strip()
    if not text:
        raise RuntimeError("转写结果为空，请检查视频音频是否可用。")
    if not segments:
        segments = [text]
    return TranscriptResult(text=text, segments=segments)


def split_text_for_batches(text: str, max_chars: int) -> list[str]:
    """把超长文本按标点优先切成更小单元。"""
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    units = [unit for unit in re.split(r"(?<=[。！？!?；;，,])", text) if unit]
    if not units:
        units = [text]

    chunks: list[str] = []
    current = ""
    for unit in units:
        if len(unit) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            for start in range(0, len(unit), max_chars):
                piece = unit[start : start + max_chars].strip()
                if piece:
                    chunks.append(piece)
            continue

        if len(current) + len(unit) <= max_chars:
            current += unit
        else:
            if current:
                chunks.append(current)
            current = unit

    if current:
        chunks.append(current)

    return [chunk.strip() for chunk in chunks if chunk.strip()]


def choose_faster_whisper_runtime() -> tuple[str, str, int]:
    """为 faster-whisper 选择更快的运行参数。"""
    torch_device = detect_torch_device()
    cpu_threads = os.cpu_count() or 4

    if torch_device == "cuda":
        return "cuda", "float16", DEFAULT_FASTER_WHISPER_GPU_BATCH_SIZE

    batch_size = min(DEFAULT_FASTER_WHISPER_CPU_BATCH_SIZE, max(4, cpu_threads // 2))
    return "cpu", "int8", batch_size


def transcribe_with_faster_whisper(audio_path: Path, model_name: str) -> TranscriptResult:
    """使用 faster-whisper 进行快速转写。"""
    try:
        from faster_whisper import BatchedInferencePipeline, WhisperModel
    except ImportError:
        from faster_whisper import WhisperModel

        BatchedInferencePipeline = None

    cpu_threads = os.cpu_count() or 4
    device, compute_type, batch_size = choose_faster_whisper_runtime()
    log(
        "使用 faster-whisper 后端"
        f"（device={device}, compute_type={compute_type}, cpu_threads={cpu_threads}, batch_size={batch_size}）"
    )
    log(f"加载模型：{model_name}（首次运行会下载模型）")

    model = WhisperModel(model_name, device=device, compute_type=compute_type, cpu_threads=cpu_threads)

    transcribe_kwargs = {
        "audio": str(audio_path),
        "language": "zh",
        "beam_size": 1,
        "best_of": 1,
        "temperature": 0.0,
        "condition_on_previous_text": False,
        "vad_filter": True,
        "without_timestamps": True,
    }

    log("开始转写音频（快速模式）...")
    if BatchedInferencePipeline is not None:
        pipeline = BatchedInferencePipeline(model=model)
        segments_iter, info = pipeline.transcribe(batch_size=batch_size, **transcribe_kwargs)
    else:
        segments_iter, info = model.transcribe(**transcribe_kwargs)

    if getattr(info, "duration", None):
        log(f"检测到音频时长：{info.duration:.1f} 秒")

    segments: list[str] = []
    seg_count = 0
    for seg_count, segment in enumerate(segments_iter, start=1):
        segments.append(segment.text)
        if seg_count % 40 == 0:
            log(f"已完成 {seg_count} 段转写...")

    log(f"转写分段数：{seg_count}")
    return build_transcript_result(segments)


def transcribe_with_openai_whisper(audio_path: Path, model_name: str) -> TranscriptResult:
    """使用 openai-whisper 转写（支持 CUDA/MPS）。"""
    import whisper

    device = detect_torch_device()
    if device == "mps":
        log("检测到 MPS，但当前 openai-whisper 在此环境下可能触发 SparseMPS 错误，改用 CPU。", "WARN")
        device = "cpu"

    use_fp16 = device == "cuda"
    log(f"使用 openai-whisper 后端（device={device}, fp16={use_fp16}）")
    log(f"加载模型：{model_name}（首次运行会下载模型）")
    try:
        model = whisper.load_model(model_name, device=device)
    except NotImplementedError as exc:
        if device != "cpu" and ("SparseMPS" in str(exc) or "_sparse_coo_tensor" in str(exc)):
            log("openai-whisper 在当前设备加载失败，回退到 CPU。", "WARN")
            device = "cpu"
            use_fp16 = False
            model = whisper.load_model(model_name, device=device)
        else:
            raise

    log("开始转写音频...")
    result = model.transcribe(
        str(audio_path),
        language="zh",
        verbose=False,
        fp16=use_fp16,
        beam_size=1,
        best_of=1,
        condition_on_previous_text=False,
        temperature=0.0,
    )

    raw_text = str((result or {}).get("text", "")).strip()
    raw_segments = []
    for segment in (result or {}).get("segments", []) or []:
        text = str((segment or {}).get("text", "") or "")
        if text:
            raw_segments.append(text)

    return build_transcript_result(raw_segments, fallback_text=raw_text)


def pick_auto_backend() -> str:
    """为 auto 模式挑选更快的默认后端。"""
    if has_module("faster_whisper"):
        return "faster-whisper"

    if has_module("whisper"):
        return "openai-whisper"

    raise RuntimeError("没有可用的转写后端，请安装 faster-whisper 或 openai-whisper。")


def transcribe_audio(
    audio_path: Path,
    model_name: str = DEFAULT_MODEL,
    backend: str = DEFAULT_BACKEND,
) -> TranscriptResult:
    """
    转写分发函数。
    backend:
    - auto: 自动选择更快的后端
    - faster-whisper
    - openai-whisper
    """
    backend = backend.strip().lower()
    if backend not in {"auto", "faster-whisper", "openai-whisper"}:
        raise ValueError(f"不支持的 backend: {backend}")

    if backend == "auto":
        backend = pick_auto_backend()
        log(f"auto 模式已选择后端：{backend}")

    if backend == "faster-whisper":
        if not has_module("faster_whisper"):
            raise RuntimeError("当前环境未安装 faster-whisper。")
        return transcribe_with_faster_whisper(audio_path, model_name)

    if backend == "openai-whisper":
        if not has_module("whisper"):
            raise RuntimeError("当前环境未安装 openai-whisper。")
        return transcribe_with_openai_whisper(audio_path, model_name)

    raise RuntimeError("没有可用的转写后端。")


def save_text_file(text: str, output_dir: Path, title: str, suffix: str, extension: str) -> Path:
    """保存文本文件。"""
    safe_title = sanitize_title(title)
    output_path = output_dir / f"{safe_title}{suffix}{extension}"
    output_path.write_text(text, encoding="utf-8")
    return output_path


def save_json_file(payload: dict, output_dir: Path, title: str, suffix: str) -> Path:
    """保存 JSON 文件。"""
    safe_title = sanitize_title(title)
    output_path = output_dir / f"{safe_title}{suffix}.json"
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def parse_json_text(json_text: str, config_label: str) -> dict:
    """解析 JSON 文本。"""
    try:
        config = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{config_label} JSON 解析失败：{exc}") from exc

    if not isinstance(config, dict):
        raise RuntimeError(f"{config_label} 顶层必须是 JSON 对象。")

    return config


def load_optional_json_config(config_path: Path) -> dict:
    """按需加载 JSON 配置，不存在时返回空对象。"""
    if not config_path.exists():
        return {}

    return parse_json_text(config_path.read_text(encoding="utf-8"), str(config_path))


def load_json_config(config_path: Path) -> dict:
    """加载 JSON 配置文件。"""
    if not config_path.exists():
        raise RuntimeError(f"未找到配置文件：{config_path}。请先填写 config.json。")

    return parse_json_text(config_path.read_text(encoding="utf-8"), str(config_path))


def resolve_path_from_config(base_dir: Path, raw_path: str) -> Path:
    """把配置中的相对路径解析成绝对路径。"""
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def normalize_http_headers(
    user_agent: str,
    referer: str,
    custom_headers: dict | None,
) -> dict[str, str]:
    """构造稳定的页面/下载请求头。"""
    headers = {
        "User-Agent": user_agent.strip() or DEFAULT_BILIBILI_USER_AGENT,
        "Referer": referer.strip() or DEFAULT_BILIBILI_REFERER,
    }

    if isinstance(custom_headers, dict):
        for key, value in custom_headers.items():
            header_name = str(key).strip()
            header_value = str(value).strip()
            if header_name and header_value:
                headers[header_name] = header_value

    return headers


def parse_browser_cookie_spec(cookie_cfg: dict | None) -> tuple[str, str | None, str | None, str | None] | None:
    """把 config 中的浏览器 cookie 配置转成 yt-dlp 需要的结构。"""
    if not isinstance(cookie_cfg, dict):
        return None

    browser = str(cookie_cfg.get("browser") or "").strip().lower()
    if not browser:
        return None

    if browser not in SUPPORTED_COOKIE_BROWSERS:
        supported = "、".join(sorted(SUPPORTED_COOKIE_BROWSERS))
        raise RuntimeError(
            "bilibili_auth.cookies_from_browser.browser 不受支持："
            f"{browser}。当前支持：{supported}"
        )

    profile = str(cookie_cfg.get("profile") or "").strip() or None
    keyring = str(cookie_cfg.get("keyring") or "").strip() or None
    container = str(cookie_cfg.get("container") or "").strip() or None
    return (browser, profile, keyring, container)


def load_bilibili_auth_config(config_path: Path) -> BilibiliAuthConfig:
    """读取 B 站下载鉴权配置。"""
    config = load_optional_json_config(config_path)
    auth_cfg = config.get("bilibili_auth") or {}
    if auth_cfg and not isinstance(auth_cfg, dict):
        raise RuntimeError("config.json 中的 bilibili_auth 必须是 JSON 对象。")

    cookie_file: Path | None = None
    cookie_file_raw = str(auth_cfg.get("cookie_file") or "").strip()
    if cookie_file_raw:
        cookie_file = resolve_path_from_config(config_path.parent, cookie_file_raw)
        if not cookie_file.exists():
            raise RuntimeError(f"bilibili_auth.cookie_file 指向的文件不存在：{cookie_file}")

    cookies_from_browser = parse_browser_cookie_spec(auth_cfg.get("cookies_from_browser"))
    user_agent = str(auth_cfg.get("user_agent") or DEFAULT_BILIBILI_USER_AGENT).strip()
    referer = str(auth_cfg.get("referer") or DEFAULT_BILIBILI_REFERER).strip()
    http_headers = normalize_http_headers(user_agent, referer, auth_cfg.get("headers"))

    return BilibiliAuthConfig(
        cookie_file=cookie_file,
        cookies_from_browser=cookies_from_browser,
        http_headers=http_headers,
    )


def describe_bilibili_auth_config(auth_cfg: BilibiliAuthConfig) -> str:
    """生成人类可读的鉴权配置摘要。"""
    details: list[str] = []
    if auth_cfg.cookie_file is not None:
        details.append(f"cookie_file={auth_cfg.cookie_file}")
    if auth_cfg.cookies_from_browser is not None:
        browser, profile, _, container = auth_cfg.cookies_from_browser
        browser_desc = browser
        if profile:
            browser_desc += f"/{profile}"
        if container:
            browser_desc += f"/container={container}"
        details.append(f"cookies_from_browser={browser_desc}")

    return "未配置 B 站登录态" if not details else "；".join(details)


def merge_provider_config(provider_name: str, provider_cfg: dict | None) -> dict:
    """合并默认 provider 配置和用户配置。"""
    merged = dict(DEFAULT_PROVIDER_SETTINGS.get(provider_name, {}))
    if isinstance(provider_cfg, dict):
        merged.update(provider_cfg)
    return merged


def is_placeholder_api_key(api_key: str) -> bool:
    """判断 API Key 是否仍是占位符。"""
    upper = api_key.upper()
    return upper.startswith("YOUR_") or "请填写" in api_key or "REPLACE_ME" in upper


def resolve_api_key(provider_name: str, provider_cfg: dict) -> str:
    """从配置或环境变量中读取 API Key。"""
    api_key = str(provider_cfg.get("api_key") or "").strip()
    api_key_env = str(provider_cfg.get("api_key_env") or "").strip()

    if api_key and not is_placeholder_api_key(api_key):
        return api_key

    if api_key_env:
        env_value = os.environ.get(api_key_env, "").strip()
        if env_value:
            return env_value
        raise RuntimeError(
            f"未找到 {provider_name} 的 API Key。请在 config.json 中填写 api_key，或设置环境变量 {api_key_env}。"
        )

    raise RuntimeError(f"未配置 {provider_name} 的 API Key。")


def load_llm_formatting_config(config_path: Path) -> dict:
    """读取并校验 AI 整理配置。"""
    config = load_json_config(config_path)
    llm_cfg = config.get("llm_formatting")
    if not isinstance(llm_cfg, dict):
        raise RuntimeError("config.json 缺少 llm_formatting 配置段。")

    enabled = bool(llm_cfg.get("enabled", True))
    provider_name = str(llm_cfg.get("provider") or DEFAULT_FORMAT_PROVIDER).strip().lower()
    providers_cfg = llm_cfg.get("providers") or {}
    provider_cfg = merge_provider_config(provider_name, providers_cfg.get(provider_name))

    if not enabled:
        return {"enabled": False}

    batch_chars = int(llm_cfg.get("chunk_max_chars", DEFAULT_FORMAT_BATCH_CHARS))
    parallel_requests = int(llm_cfg.get("parallel_requests", DEFAULT_PARALLEL_REQUESTS))
    retry_count = int(llm_cfg.get("retry_count", DEFAULT_RETRY_COUNT))

    if batch_chars <= 0:
        raise RuntimeError("chunk_max_chars 必须大于 0。")
    if parallel_requests <= 0:
        raise RuntimeError("parallel_requests 必须大于 0。")
    if retry_count < 0:
        raise RuntimeError("retry_count 不能小于 0。")

    def build_request_config(
        cfg_name: str,
        selected_provider_name: str,
        selected_provider_cfg: dict,
        timeout_value: int,
        temperature_value: float,
        model_override: str | None = None,
    ) -> dict:
        base_url = str(selected_provider_cfg.get("base_url") or "").strip().rstrip("/")
        model = str(model_override or selected_provider_cfg.get("model") or "").strip()
        max_tokens = selected_provider_cfg.get("max_tokens")
        max_tokens = int(max_tokens) if max_tokens is not None else None
        ssl_verify = bool(selected_provider_cfg.get("ssl_verify", True))

        if not base_url:
            raise RuntimeError(f"{cfg_name} 的 base_url 未配置。")
        if not model:
            raise RuntimeError(f"{cfg_name} 的 model 未配置。")
        if timeout_value <= 0:
            raise RuntimeError(f"{cfg_name} 的 request_timeout_seconds 必须大于 0。")

        return {
            "provider_name": selected_provider_name,
            "base_url": base_url,
            "model": model,
            "api_key": resolve_api_key(selected_provider_name, selected_provider_cfg),
            "timeout": timeout_value,
            "temperature": temperature_value,
            "max_tokens": max_tokens,
            "ssl_verify": ssl_verify,
        }

    formatter_request = build_request_config(
        cfg_name="formatter",
        selected_provider_name=provider_name,
        selected_provider_cfg=provider_cfg,
        timeout_value=int(llm_cfg.get("request_timeout_seconds", DEFAULT_REQUEST_TIMEOUT)),
        temperature_value=float(llm_cfg.get("temperature", DEFAULT_TEMPERATURE)),
    )

    return {
        "enabled": True,
        "batch_chars": batch_chars,
        "parallel_requests": parallel_requests,
        "retry_count": retry_count,
        "formatter": formatter_request,
    }


def build_text_batches(segments: list[str], max_chars: int) -> list[dict]:
    """按字符数切分原始转写，供 AI 做整理和复核。"""
    expanded_segments: list[str] = []
    for segment in segments:
        expanded_segments.extend(split_text_for_batches(segment, max_chars))

    batches: list[dict] = []
    current_segments: list[str] = []
    current_chars = 0
    start_index = 1

    for index, segment in enumerate(expanded_segments, start=1):
        segment_cost = len(segment) + 1
        if current_segments and current_chars + segment_cost > max_chars:
            batches.append(
                {
                    "start_index": start_index,
                    "end_index": index - 1,
                    "segments": current_segments,
                    "text": "".join(current_segments).strip(),
                }
            )
            current_segments = []
            current_chars = 0
            start_index = index

        current_segments.append(segment)
        current_chars += segment_cost

    if current_segments:
        batches.append(
            {
                "start_index": start_index,
                "end_index": start_index + len(current_segments) - 1,
                "segments": current_segments,
                "text": "".join(current_segments).strip(),
            }
        )

    return batches


def build_formatter_user_prompt(batch: dict, title: str, batch_index: int, total_batches: int) -> str:
    """构造整理正文的 prompt。"""
    return f"""请整理下面这段中文转写稿。

**绝对要求**
- **不要改变原文意思。**
- **不要删除重要内容。**
- **不要补充原文没有的新事实、新观点、新结论。**
- **不要总结，不要缩写。**

**允许的整理动作**
- **补充标点。**
- **调整分段和换行。**
- **必须完成断句，让每一句读起来通顺自然。**
- **在不改变原意的前提下，做极少量、最小限度的字词修正，例如明显的 ASR 同音错字、明显口误、明显连写断句错误。**
- **输出纯文本格式，重点是清晰分段；优先一到两句一段，不要整段堆在一起。**
- **不要使用 Markdown 标题或列表。**

**输出要求**
- **只输出整理后的正文，不要解释。**
- **如果你不确定某处该不该改，就保留原文。**

视频标题：{title}
片段：{batch_index}/{total_batches}
原始分段范围：{batch['start_index']}-{batch['end_index']}

原始转写如下：
<<<RAW_TRANSCRIPT>>>
{batch['text']}
<<<END>>>
"""


def parse_chat_completion_content(response_payload: dict) -> str:
    """兼容不同 OpenAI 风格响应中的文本内容。"""
    choices = response_payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError(f"API 响应中缺少 choices：{response_payload}")

    message = choices[0].get("message") or {}
    content = message.get("content")

    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("text"), str):
                parts.append(item["text"])
            elif isinstance(item.get("content"), str):
                parts.append(item["content"])
        text = "".join(parts).strip()
        if text:
            return text

    raise RuntimeError(f"API 响应中未找到可用文本内容：{response_payload}")


def call_openai_compatible_chat_api(messages: list[dict[str, str]], llm_cfg: dict) -> str:
    """调用兼容 OpenAI Chat Completions 的接口。"""
    payload: dict[str, object] = {
        "model": llm_cfg["model"],
        "messages": messages,
        "temperature": llm_cfg["temperature"],
    }

    if llm_cfg.get("max_tokens") is not None:
        payload["max_tokens"] = llm_cfg["max_tokens"]

    request = urllib.request.Request(
        url=f"{llm_cfg['base_url']}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {llm_cfg['api_key']}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    ssl_context = None
    if not llm_cfg.get("ssl_verify", True):
        ssl_context = ssl._create_unverified_context()

    try:
        with urllib.request.urlopen(request, timeout=llm_cfg["timeout"], context=ssl_context) as response:
            response_text = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"AI API 请求失败（HTTP {exc.code}）：{detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"AI API 请求失败：{exc}") from exc

    try:
        response_payload = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"AI API 返回了非 JSON 响应：{response_text}") from exc

    return parse_chat_completion_content(response_payload)


def strip_code_fence(text: str) -> str:
    """去掉模型可能附带的代码块包裹。"""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    return stripped.strip()


def normalize_formatted_output(text: str) -> str:
    """清理模型输出外层包裹。"""
    normalized = strip_code_fence(text).strip()
    return normalized


def format_batch(
    batch: dict,
    title: str,
    batch_index: int,
    total_batches: int,
    llm_cfg: dict,
) -> tuple[str, bool]:
    """整理单个批次。"""
    log(f"开始整理第 {batch_index}/{total_batches} 批...")
    formatter_messages = [
        {"role": "system", "content": FORMATTER_SYSTEM_PROMPT},
        {"role": "user", "content": build_formatter_user_prompt(batch, title, batch_index, total_batches)},
    ]

    attempts = llm_cfg["retry_count"] + 1
    last_error = ""

    for attempt in range(1, attempts + 1):
        try:
            formatted_text = normalize_formatted_output(
                call_openai_compatible_chat_api(formatter_messages, llm_cfg["formatter"])
            )
            if not formatted_text:
                raise RuntimeError("整理结果为空。")
            log(
                f"第 {batch_index}/{total_batches} 批整理完成。"
                f" formatter={llm_cfg['formatter']['provider_name']}/{llm_cfg['formatter']['model']}"
            )
            return formatted_text, False
        except Exception as exc:
            last_error = str(exc)
            if attempt < attempts:
                log(
                    f"第 {batch_index}/{total_batches} 批整理失败，准备重试 {attempt}/{attempts - 1}。原因：{exc}",
                    "WARN",
                )
            else:
                log(
                    f"第 {batch_index}/{total_batches} 批整理失败，回退原始文本。原因：{exc}",
                    "WARN",
                )

    if last_error:
        log(f"第 {batch_index}/{total_batches} 批最终使用原始文本。最后错误：{last_error}", "WARN")
    return batch["text"], True


def format_transcript_with_llm(transcript: TranscriptResult, title: str, llm_cfg: dict) -> tuple[str, bool]:
    """调用 LLM 整理正文。"""
    batches = build_text_batches(transcript.segments, llm_cfg["batch_chars"])
    total_batches = len(batches)

    if total_batches == 1:
        log("开始 AI 整理：1 个批次。")
        return format_batch(batches[0], title, 1, 1, llm_cfg)

    workers = min(llm_cfg["parallel_requests"], total_batches)
    log(f"开始 AI 整理：{total_batches} 个批次，并发 {workers} 个请求。")
    results: dict[int, str] = {}
    had_fallback = False
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {
            executor.submit(format_batch, batch, title, idx, total_batches, llm_cfg): idx
            for idx, batch in enumerate(batches, start=1)
        }
        for future in concurrent.futures.as_completed(future_map):
            idx = future_map[future]
            chunk_text, chunk_fallback = future.result()
            results[idx] = chunk_text.strip()
            had_fallback = had_fallback or chunk_fallback

    ordered_chunks = [results[idx] for idx in range(1, total_batches + 1)]
    ordered_chunks = [chunk for chunk in ordered_chunks if chunk]
    return "\n\n".join(ordered_chunks).strip(), had_fallback


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="Bilibili 视频转写工具")
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="Whisper 模型名（默认 small，可选 tiny/base/small/medium/large-v3）",
    )
    parser.add_argument(
        "--backend",
        default=DEFAULT_BACKEND,
        choices=["auto", "faster-whisper", "openai-whisper"],
        help="转写后端，默认 auto（自动选择更快的可用后端）",
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="AI 整理配置文件路径，默认当前目录下的 config.json",
    )
    parser.add_argument(
        "--skip-formatting",
        action="store_true",
        help="仅执行转写，不调用 AI 做格式整理",
    )
    parser.add_argument(
        "--raw-transcript",
        help="直接使用已有的原始转写文本文件，跳过下载和语音识别，仅执行 AI 整理；输出会落到单独的重跑目录",
    )
    return parser.parse_args()


def main() -> None:
    launch_in_new_terminal_if_needed()
    args = parse_args()

    config_path = Path(args.config).expanduser()
    if not config_path.is_absolute():
        config_path = Path.cwd() / config_path
    bilibili_auth_cfg = load_bilibili_auth_config(config_path)
    raw_transcript_input = None
    if args.raw_transcript:
        raw_transcript_input = Path(args.raw_transcript).expanduser()
        if not raw_transcript_input.is_absolute():
            raw_transcript_input = Path.cwd() / raw_transcript_input
        if not raw_transcript_input.exists():
            raise ValueError(f"--raw-transcript 指定的文件不存在：{raw_transcript_input}")

    url = ""
    if raw_transcript_input is None:
        print("请输入您要转写的bilibili视频链接：", end="", flush=True)
        url = input().strip()

        if not url:
            raise ValueError("未输入链接，程序已终止。")

        if not is_valid_bilibili_url(url):
            raise ValueError("输入的链接不是常见的 Bilibili 视频链接格式。")

    if raw_transcript_input is None:
        log("开始检查依赖环境...")
        ensure_dependencies()
        log("依赖检查通过。")
    else:
        log("已指定 --raw-transcript，跳过下载和转写依赖检查。")
    log(
        "当前参数："
        f"model={args.model}, "
        f"backend={args.backend}, "
        f"config={config_path}, "
        f"skip_formatting={args.skip_formatting}, "
        f"raw_transcript={raw_transcript_input}"
    )
    log(f"B站登录态：{describe_bilibili_auth_config(bilibili_auth_cfg)}")

    now = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_mode = "reformat_from_raw" if raw_transcript_input is not None else "full_pipeline"
    if raw_transcript_input is None:
        base_dir = Path.cwd() / "transcript_output" / now
    else:
        base_dir = raw_transcript_input.parent / f"reformat_{now}"
    base_dir.mkdir(parents=True, exist_ok=True)
    log(f"本次任务输出目录：{base_dir}")

    total_start = time.perf_counter()
    video_type_label: str | None = None

    if raw_transcript_input is None:
        step_start = time.perf_counter()
        audio_path, title, video_type_label = download_audio(url, base_dir, bilibili_auth_cfg)
        log(f"音频准备耗时：{time.perf_counter() - step_start:.1f} 秒")

        step_start = time.perf_counter()
        transcript = transcribe_audio(audio_path, model_name=args.model, backend=args.backend)
        log(f"转写耗时：{time.perf_counter() - step_start:.1f} 秒")

        raw_transcript_path = save_text_file(transcript.text, base_dir, title, "_transcript_raw", ".txt")
        log(f"原始转写文本已存储至 {raw_transcript_path}")
    else:
        title = raw_transcript_input.stem
        title = re.sub(r"_transcript_raw$", "", title)
        transcript_text = raw_transcript_input.read_text(encoding="utf-8").strip()
        transcript = build_transcript_result([transcript_text], fallback_text=transcript_text)
        raw_transcript_path = raw_transcript_input
        log(f"已载入现有原始转写文本：{raw_transcript_path}")

    formatted_transcript_path: Path | None = None
    metadata_path: Path | None = None
    if args.skip_formatting:
        log("已按参数跳过 AI 整理步骤。", "WARN")
    else:
        llm_cfg = load_llm_formatting_config(config_path)
        if not llm_cfg["enabled"]:
            log("config.json 已禁用 AI 整理步骤。", "WARN")
        else:
            log(
                f"开始调用 AI 整理转写稿：formatter={llm_cfg['formatter']['provider_name']}/{llm_cfg['formatter']['model']}, "
                f"batch_chars={llm_cfg['batch_chars']}, "
                f"parallel_requests={llm_cfg['parallel_requests']}"
            )
            step_start = time.perf_counter()
            formatted_text, formatting_had_fallback = format_transcript_with_llm(transcript, title, llm_cfg)
            log(f"AI 整理耗时：{time.perf_counter() - step_start:.1f} 秒")
            if formatting_had_fallback:
                log("AI 整理部分批次回退到了原始文本。", "WARN")
            formatted_suffix = "_transcript_formatted"
            if raw_transcript_input is not None:
                formatted_suffix = "_transcript_formatted_rerun_from_raw"
            formatted_transcript_path = save_text_file(
                formatted_text,
                base_dir,
                title,
                formatted_suffix,
                ".txt",
            )
            log(f"整理后的转写稿已存储至 {formatted_transcript_path}")
            metadata_suffix = "_run_meta"
            if raw_transcript_input is not None:
                metadata_suffix = "_reformat_from_raw_meta"
            metadata_path = save_json_file(
                {
                    "created_at": dt.datetime.now().isoformat(timespec="seconds"),
                    "mode": run_mode,
                    "source_raw_transcript": str(raw_transcript_path.resolve()),
                    "output_dir": str(base_dir.resolve()),
                    "formatted_output": str(formatted_transcript_path.resolve()),
                    "video_type": video_type_label,
                    "formatter": {
                        "provider": llm_cfg["formatter"]["provider_name"],
                        "model": llm_cfg["formatter"]["model"],
                    },
                    "formatting_had_fallback": formatting_had_fallback,
                    "batch_chars": llm_cfg["batch_chars"],
                    "parallel_requests": llm_cfg["parallel_requests"],
                },
                base_dir,
                title,
                metadata_suffix,
            )
            log(f"本次运行元信息已存储至 {metadata_path}")

    log(f"总耗时：{time.perf_counter() - total_start:.1f} 秒")
    if formatted_transcript_path is not None:
        log(
            "转写完成！"
            f"\n- 原始文本：{raw_transcript_path}"
            f"\n- 整理稿：{formatted_transcript_path}",
            "SUCCESS",
        )
        if metadata_path is not None:
            log(f"元信息：{metadata_path}", "SUCCESS")
    else:
        log(f"转写完成！文本已经存储至 {raw_transcript_path}", "SUCCESS")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("用户中断执行。", "WARN")
        sys.exit(130)
    except Exception as exc:
        log(f"执行失败：{exc}", "ERROR")
        log("详细错误堆栈如下：", "ERROR")
        traceback.print_exc()
        sys.exit(1)
