"""离线验证完整转写和重新整理的输出路径，防止同名任务覆盖。"""

import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bilibili_transcribe as app


class OutputNamesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bili_output_")
        self.addCleanup(self.temp.cleanup)
        previous = Path.cwd()
        self.addCleanup(os.chdir, previous)
        self.root = Path(self.temp.name).resolve()
        os.chdir(self.root)

    def run_main(self, args):
        with patch.object(sys, "argv", ["bilibili_transcribe.py", "--config", "missing.json", *args]), \
             patch.object(app, "launch_in_new_terminal_if_needed"), \
             contextlib.redirect_stdout(io.StringIO()):
            app.main()

    def test_video_title_directory_and_duplicate_preserves_first_result(self):
        info = {"title": "100%视频标题：测试/访谈?"}
        safe_title = "100%视频标题：测试_访谈_"

        class FakeDownloader:
            def __init__(self, options):
                self.options = options

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def extract_info(self, url, download):
                Path(self.options["outtmpl"] % {"ext": "wav"}).write_bytes(b"audio fixture")
                return info

        probe = app.BilibiliVideoProbe("https://b23.tv/test", info["title"], False, "test")
        with patch.dict(sys.modules, {"yt_dlp": SimpleNamespace(YoutubeDL=FakeDownloader)}), \
             patch.object(app, "ensure_dependencies"), \
             patch.object(app, "resolve_ffmpeg_executable", return_value=None), \
             patch.object(app, "probe_bilibili_video", return_value=probe), \
             patch.object(app, "collect_video_info", return_value=(info, app.YtDlpMessageCollector())), \
             patch.object(app, "has_usable_audio_formats", return_value=True), \
             patch.object(app, "transcribe_audio", side_effect=[app.TranscriptResult("第一次", ["第一次"]),
                                                                app.TranscriptResult("第二次", ["第二次"])]):
            for _ in range(2):
                self.run_main(["--url", "https://b23.tv/test", "--skip-formatting"])

        output = self.root / "transcript_output"
        self.assertEqual({p.name for p in output.iterdir()}, {safe_title, safe_title + " (2)"})
        for name, text in [(safe_title, "第一次"), (safe_title + " (2)", "第二次")]:
            self.assertEqual((output / name / f"{safe_title}_transcript_raw.txt").read_text(), text)
            self.assertTrue((output / name / "source_audio.wav").is_file())

    def test_reformat_title_directory_and_metadata(self):
        raw = self.root / "访谈标题_transcript_raw.txt"
        raw.write_text("原始内容", encoding="utf-8")
        config = {"enabled": True, "formatter": {"provider_name": "test", "model": "test"},
                  "batch_chars": 100, "parallel_requests": 1}
        with patch.object(app, "load_llm_formatting_config", return_value=config), \
             patch.object(app, "format_transcript_with_llm", return_value=("整理内容", False)):
            self.run_main(["--raw-transcript", str(raw)])
        output = self.root / "访谈标题_重新整理"
        self.assertTrue((output / "访谈标题_transcript_formatted_rerun_from_raw.txt").is_file())
        metadata = json.loads((output / "访谈标题_reformat_from_raw_meta.json").read_text())
        self.assertEqual(metadata["output_dir"], str(output))
        self.assertEqual(raw.read_text(), "原始内容")


if __name__ == "__main__":
    unittest.main()
