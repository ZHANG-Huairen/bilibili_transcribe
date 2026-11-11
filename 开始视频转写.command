#!/bin/bash
# Finder 双击 .command 文件会自动使用 Terminal 打开。
set -euo pipefail
umask 077

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
STATE_DIR="$PROJECT_DIR/.launcher"
ENV_DIR="$STATE_DIR/venv"
PYTHON="$ENV_DIR/bin/python"
HELPER="$PROJECT_DIR/launcher_support.py"
LOCK_DIR="$STATE_DIR/setup.lock"
LOCKED=0
DOWNLOAD_DIR=""

release_lock() {
    if [ "$LOCKED" = 1 ]; then
        /bin/rm -f "$LOCK_DIR/pid"
        /bin/rmdir "$LOCK_DIR"
        LOCKED=0
    fi
}

finish() {
    result=$?
    trap - EXIT
    release_lock
    if [ -n "$DOWNLOAD_DIR" ]; then
        /bin/rm -rf "$DOWNLOAD_DIR"
    fi
    if [ "$result" -eq 0 ] && [ "$CHECK_ONLY" = 1 ]; then
        printf '\n环境准备完成，可以双击启动转写。\n'
    elif [ "$result" -eq 0 ]; then
        printf '\n运行结束。结果保存在：%s/transcript_output\n' "$PROJECT_DIR"
    elif [ "$result" -eq 130 ]; then
        printf '\n已停止转写。\n'
    else
        printf '\n启动或转写失败。请查看上方错误；修复网络后可再次双击重试。\n'
    fi
    printf '本次日志：%s\n' "$LOG_FILE"
    if [ -t 0 ] && [ "$CHECK_ONLY" != 1 ]; then
        read -r -p '按回车结束窗口中的程序……' _ || true
    fi
    exit "$result"
}

cd "$PROJECT_DIR"
/bin/mkdir -p "$STATE_DIR/logs"
if [ "${1:-}" = "--launcher-child" ]; then
    LOG_FILE="$2"
    shift 2
else
    LOG_FILE="$STATE_DIR/logs/start_$(/bin/date +%Y%m%d_%H%M%S)_$$.log"
    # 普通管道兼容受限终端；子 bash 仍保留原始标准输入。
    set +e
    /bin/bash "$PROJECT_DIR/${BASH_SOURCE[0]##*/}" --launcher-child "$LOG_FILE" "$@" 2>&1 | /usr/bin/tee -a "$LOG_FILE"
    statuses=("${PIPESTATUS[@]}")
    if [ "${statuses[0]}" -ne 0 ]; then exit "${statuses[0]}"; fi
    exit "${statuses[1]}"
fi
# 保存启动参数，供 EXIT trap 决定是否需要等待回车。
CHECK_ONLY=0
if [ "${1:-}" = "--check" ]; then CHECK_ONLY=1; fi
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

printf '\nBilibili 视频转写\n\n'
printf '正在检查运行环境；首次启动会自动下载 Python 和转写依赖。\n'
if [ "$(/usr/bin/uname -s)" != Darwin ]; then
    printf '此双击启动器适用于 macOS。\n' >&2
    exit 1
fi
for required in bilibili_transcribe.py launcher_support.py requirements.txt; do
    if [ ! -f "$PROJECT_DIR/$required" ]; then
        printf '缺少 %s，请将启动器与项目文件放在同一目录。\n' "$required" >&2
        exit 1
    fi
done

# 只在检查/安装期间加锁，避免同时双击造成两个安装进程。
if ! /bin/mkdir "$LOCK_DIR" 2>/dev/null; then
    /bin/sleep 1
    owner="$(/bin/cat "$LOCK_DIR/pid" 2>/dev/null || true)"
    if [[ "$owner" =~ ^[0-9]+$ ]] && kill -0 "$owner" 2>/dev/null; then
        printf '另一个窗口正在准备环境，请等待该窗口完成。\n' >&2
        exit 1
    fi
    /bin/rm -f "$LOCK_DIR/pid"
    /bin/rmdir "$LOCK_DIR" 2>/dev/null || true
    /bin/mkdir "$LOCK_DIR"
fi
LOCKED=1
printf '%s\n' "$$" > "$LOCK_DIR/pid"

# 不继承其他项目激活的 Python 环境，也不修改用户的 shell 配置。
unset PYTHONHOME PYTHONPATH VIRTUAL_ENV
export PYTHONNOUSERSITE=1 PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8
export BILI_TRANSCRIBE_CHILD=1
export UV_PYTHON_INSTALL_DIR="$STATE_DIR/python"
export UV_CACHE_DIR="$STATE_DIR/cache"
export UV_NO_CONFIG=1

if [ ! -x "$PYTHON" ] || ! "$PYTHON" "$HELPER" --check; then
    printf '\n正在自动准备独立环境，无需安装 Homebrew 或输入管理员密码。\n'
    UV="$STATE_DIR/bin/uv"
    if [ ! -x "$UV" ] || ! "$UV" --version >/dev/null 2>&1; then
        # 固定官方发布版本与 SHA-256，下载完成后校验再执行。
        UV_VERSION=0.13.4
        case "$(/usr/bin/uname -m)" in
            arm64)
                target=aarch64-apple-darwin
                digest=e0b1d4743ce64c55d9f0f4a444afa6b81125680070ee04e853fcd0f6c1ebf8f1
                ;;
            *)
                printf '当前自动安装环境按本机 Apple Silicon Mac 配置。\n' >&2
                exit 1
                ;;
        esac
        DOWNLOAD_DIR="$(/usr/bin/mktemp -d "$STATE_DIR/download.XXXXXX")"
        archive="uv-$target.tar.gz"
        /usr/bin/curl --proto '=https' --tlsv1.2 --fail --location \
            --retry 2 --connect-timeout 15 --max-time 300 \
            "https://releases.astral.sh/github/uv/releases/download/$UV_VERSION/$archive" \
            --output "$DOWNLOAD_DIR/$archive"
        (cd "$DOWNLOAD_DIR" && printf '%s  %s\n' "$digest" "$archive" | /usr/bin/shasum -a 256 -c -)
        /usr/bin/tar -xzf "$DOWNLOAD_DIR/$archive" -C "$DOWNLOAD_DIR"
        /bin/mkdir -p "$STATE_DIR/bin"
        /usr/bin/install -m 755 "$DOWNLOAD_DIR/uv-$target/uv" "$UV"
        /bin/rm -rf "$DOWNLOAD_DIR"
        DOWNLOAD_DIR=""
    fi

    if [ -e "$ENV_DIR" ] || [ -L "$ENV_DIR" ]; then
        /bin/mv "$ENV_DIR" "$STATE_DIR/venv.backup.$(/bin/date +%Y%m%d_%H%M%S).$$"
    fi
    "$UV" venv --python 3.12 --managed-python "$ENV_DIR"
    "$UV" pip install --python "$PYTHON" -r "$PROJECT_DIR/requirements.txt"
    "$PYTHON" "$HELPER" --check
fi
release_lock

if [ "$CHECK_ONLY" = 1 ]; then exit 0; fi
printf '\n环境已就绪。粘贴 Bilibili 视频链接并按回车即可开始。\n'
printf '首次使用某个 Whisper 模型时会自动下载模型，请保持联网。\n\n'
"$PYTHON" "$HELPER" "$@"
