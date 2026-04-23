Bilibili Transcribe

项目简介
这是一个面向 Bilibili 视频的转写工具，包含三个核心脚本：
1. bilibili_transcribe.py
   单个视频转写。支持下载音频、语音识别、保存原始转写，并可调用兼容 OpenAI Chat Completions 的接口做断句和格式整理。
2. bilibili_space_batch.py
   批量抓取某个 UP 主空间下的视频并顺序转写。
3. bilibili_space_supervisor.py
   批量总控程序，支持断点续跑、失败重试、状态清单维护。

当前仓库上传内容
- 核心 Python 代码
- README.txt
- requirements.txt

当前仓库不上传内容
- config.json（含本地密钥和登录态）
- transcript_output/（转写结果、音频、清单）
- __pycache__/、.DS_Store、日志文件
- 仅适用于本机绝对路径的临时 shell 脚本

运行环境
- Python 3.10.x
- ffmpeg（系统命令，当前机器路径为 /opt/homebrew/bin/ffmpeg）
- macOS 更适合当前脚本的默认行为；其中单视频脚本会尝试在新 Terminal 窗口中启动

已验证的 Python 包环境
- yt-dlp 2026.3.13
- openai-whisper 20250625
- torch 2.10.0

安装方式
1. 创建并激活 Python 3.10 虚拟环境
2. 安装 requirements.txt 中的依赖
3. 安装 ffmpeg

示例：
python3.10 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

macOS 安装 ffmpeg：
brew install ffmpeg

配置说明
程序默认读取当前目录下的 config.json。
出于安全原因，真实 config.json 不会上传到 GitHub。

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
        "ssl_verify": false
      },
      "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "api_key": "YOUR_API_KEY",
        "api_key_env": "DASHSCOPE_API_KEY",
        "ssl_verify": false
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
- 单视频默认输出到 transcript_output/<timestamp>/
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
