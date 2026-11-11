Bilibili Transcribe

双击开始（本机 Apple Silicon Mac）
1. 在 Finder 中双击“开始视频转写.command”。终端会自动打开。
2. 首次启动会自动下载独立 Python 3.12、转写依赖和内置 ffmpeg；以后会复用并检查环境。
3. 看到提示后粘贴 Bilibili 视频链接并回车。默认使用 small 模型，首次使用会自动下载模型。
4. 完成后在 transcript_output/<视频标题>/ 中查看转写文本；同名任务自动加 (2)、(3) 等序号。窗口会保留结果和错误信息，按回车结束。

无需手动安装 Python、Homebrew、ffmpeg 或激活虚拟环境，也无需管理员密码。
首次准备环境、下载视频和下载模型需要联网。B 站受限内容仍需原有登录权限。
启动器自动读取现有 config.json；AI 整理配置或密钥缺失时自动仅做原始转写。
独立环境和每次启动日志保存在 .launcher/，不会覆盖已有 .venv、配置或转写结果。
启动器需要与 bilibili_transcribe.py、launcher_support.py、requirements.txt 放在同一文件夹；移动时请移动整个项目文件夹。
环境损坏会自动尝试修复；不可执行的环境会保留为 .launcher/venv.backup.*。
自动环境管理使用 uv 官方固定版本 0.13.4，下载后校验 SHA-256：
https://docs.astral.sh/uv/reference/installer/
如需仅检查/准备环境，可在终端运行：./开始视频转写.command --check

项目简介
这是一个面向 Bilibili 视频的转写工具，包含三个核心脚本：
1. bilibili_transcribe.py
   单个视频转写。支持下载音频、语音识别、保存原始转写，并可调用兼容 OpenAI Chat Completions 的接口做断句和格式整理。
2. bilibili_space_batch.py
   批量抓取某个 UP 主空间下的视频并顺序转写。
3. bilibili_space_supervisor.py
   批量总控程序，支持断点续跑、失败重试、状态清单维护。

每日增量监控和公司观点看板
- bilibili_recent_batch.py：按已核对的日期目录转写新视频，校验作者、发布日期和完整音频时长。
- bilibili_repair_repeats.py：复核连续重复的语音识别片段，保留修复备份。
- monitoring/：证据提取、名称频率统计、离线看板渲染、交互样式和每日更新流程。
- 详细安装、运行和任务数据要求见 monitoring/README.txt 与 monitoring/DAILY_UPDATE.md。
- MLX 转写需 Apple Silicon，额外依赖安装：python -m pip install -r requirements-monitoring.txt。
- 当前每日监控由本地 Codex 自动化执行；上传源码不会在 GitHub 上创建定时任务。

当前仓库上传内容
- 核心 Python 代码
- README.txt
- requirements.txt
- requirements-monitoring.txt、config.example.json、双击启动器及 tests/
- monitoring/ 中的可复用脚本和每日更新流程

当前仓库不上传内容
- config.json（含本地密钥和登录态）
- transcript_output/（转写结果、音频、清单）
- __pycache__/、.DS_Store、日志文件
- 仅适用于本机绝对路径的临时 shell 脚本

运行环境
- Python 3.10+（已在本机 Python 3.11 虚拟环境验证）
- ffmpeg：可使用系统 ffmpeg，也可直接使用 Python 包 imageio-ffmpeg 自带的内置二进制
- macOS 更适合当前脚本的默认行为；交互式运行时单视频脚本会尝试在新 Terminal 窗口中启动，非交互运行会直接在当前进程执行

已验证的 Python 包环境
- yt-dlp 2026.8.19
- openai-whisper 20250625
- torch 2.10.0
- imageio-ffmpeg 0.6.0

安装方式
1. 创建并激活 Python 3.10+ 虚拟环境
2. 安装 requirements.txt 中的依赖
3. 如需系统 ffmpeg，可额外安装；若不安装，脚本会自动尝试使用 imageio-ffmpeg

示例：
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

如果你希望继续使用系统 ffmpeg，macOS 可执行：
brew install ffmpeg

配置说明
程序默认读取当前目录下的 config.json。
出于安全原因，真实 config.json 不会上传到 GitHub。
可复制 config.example.json 为 config.json；示例默认关闭 AI 整理，且不读取浏览器登录态。

如果你只是想对公开视频做原始转写，并且命令里使用了 --skip-formatting，那么可以不提供 config.json。

可以按下面结构自行新建 config.json：

{
  "bilibili_auth": {
    "cookie_file": "",
    "cookies_from_browser": {
      "browser": "chrome",
      "profile": "Default",
      "keyring": "",
      "container": ""
    },
    "user_agent": "",
    "referer": "https://www.bilibili.com/",
    "headers": {}
  },
  "llm_formatting": {
    "enabled": true,
    "provider": "deepseek",
    "chunk_max_chars": 4000,
    "request_timeout_seconds": 180,
    "temperature": 0.0,
    "parallel_requests": 4,
    "retry_count": 0,
    "providers": {
      "deepseek": {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "api_key": "YOUR_API_KEY",
        "api_key_env": "DEEPSEEK_API_KEY",
        "ssl_verify": true
      },
      "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "api_key": "YOUR_API_KEY",
        "api_key_env": "DASHSCOPE_API_KEY",
        "ssl_verify": true
      }
    }
  }
}

说明：
- bilibili_auth 用于处理 B 站登录态，适合会员、充电或受限内容下载
- llm_formatting.enabled=false 时，只做原始转写，不调用大模型整理
- api_key 可以直接写在 config.json，也可以通过环境变量提供

使用方法
1. 单视频转写
运行：
python bilibili_transcribe.py

程序会提示输入 Bilibili 视频链接。

也可以直接用命令行参数传入链接，适合脚本化或非交互执行：
python bilibili_transcribe.py --url "https://www.bilibili.com/video/BVxxxxxxxx"

如果只做原始转写、不调用大模型整理：
python bilibili_transcribe.py --url "https://www.bilibili.com/video/BVxxxxxxxx" --skip-formatting

2. 对已有原始转写文本重新整理
运行：
python bilibili_transcribe.py --raw-transcript /path/to/xxx_transcript_raw.txt

3. 批量转写某个 UP 主空间
运行：
python bilibili_space_batch.py "https://space.bilibili.com/<uid>/upload/video" --config config.json

4. 用总控程序执行可续跑批量任务
运行：
python bilibili_space_supervisor.py "https://space.bilibili.com/<uid>/upload/video" --config config.json

输出说明
- 单视频默认输出到 transcript_output/<视频标题>/；同名任务依次使用 <视频标题> (2)/、<视频标题> (3)/，不覆盖已有结果
- 重新整理原始转写时，输出到原文件旁的 <视频标题>_重新整理/；同名目录同样自动加序号
- 批量任务默认输出到 transcript_output/space_batch_<timestamp>/
- 总控任务默认输出到 transcript_output/space_supervisor_<timestamp>/

主要输出文件包括：
- *_transcript_raw.txt
- *_transcript_formatted.txt
- *_run_meta.json
- batch_manifest.json
- supervisor_manifest.json

补充说明
- 代码支持 faster-whisper 和 openai-whisper 两种后端
- 当前 requirements.txt 默认按本机已验证可运行环境整理，使用 openai-whisper 路线即可运行
- 如果后续要改成 faster-whisper，可自行额外安装 faster-whisper

离线测试
python -m unittest discover -s tests -v
