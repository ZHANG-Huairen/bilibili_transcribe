"""检查启动器的配置降级、标准输入、退出码与安装锁。"""

import contextlib
import io
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))
import bilibili_transcribe as transcriber
import launcher_support


class FormattingTests(unittest.TestCase):
    def run_with_config(self, args, config=None, error=None):
        with patch.object(sys, "argv", ["launcher_support.py"]), patch.dict(os.environ), \
             patch.object(transcriber, "main") as main, \
             patch.object(transcriber, "load_llm_formatting_config", return_value=config, side_effect=error) as load, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            launcher_support.run_transcription(args)
            main.assert_called_once_with()
            return sys.argv.copy(), output.getvalue(), load.call_count

    def test_missing_key_falls_back_without_leaking_config(self):
        argv, output, _ = self.run_with_config(["--model", "tiny"], error=RuntimeError("private-config-value"))
        self.assertEqual(argv[1:], ["--model", "tiny", "--skip-formatting"])
        self.assertNotIn("private-config-value", output)

    def test_valid_formatting_config_and_explicit_skip(self):
        argv, _, count = self.run_with_config(["--url", "https://b23.tv/example"], config={"enabled": True})
        self.assertNotIn("--skip-formatting", argv)
        self.assertEqual(count, 1)
        argv, _, count = self.run_with_config(["--skip-formatting"])
        self.assertEqual(argv[1:], ["--skip-formatting"])
        self.assertEqual(count, 0)


class ShellTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bili 启动器 ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.launcher = self.root / "开始视频转写.command"
        shutil.copy2(PROJECT_DIR / self.launcher.name, self.launcher)
        (self.root / "bilibili_transcribe.py").touch()
        (self.root / "requirements.txt").touch()
        (self.root / "launcher_support.py").write_text(
            "import sys\n"
            "if sys.argv[1:] == ['--check']:\n"
            "    print('check passed', flush=True)\n"
            "    sys.exit(0)\n"
            "print('received: ' + input(), flush=True)\n"
            "sys.exit(7 if '--fail' in sys.argv else 0)\n", encoding="utf-8")
        python = self.root / ".launcher/venv/bin/python"
        python.parent.mkdir(parents=True)
        python.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} \"$@\"\n")
        python.chmod(0o755)
        self.lock = self.root / ".launcher/setup.lock"

    def run_launcher(self, *args):
        return subprocess.run([str(self.launcher), *args], input="test video URL\n", text=True,
                              capture_output=True, cwd="/", timeout=30)

    def test_spaces_stdin_logs_and_failure_status(self):
        result = self.run_launcher("--fail")
        self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
        self.assertIn("received: test video URL", result.stdout)
        self.assertIn("启动或转写失败", result.stdout)
        self.assertFalse(self.lock.exists())
        logs = list((self.root / ".launcher/logs").glob("*.log"))
        self.assertEqual(len(logs), 1)
        self.assertIn("received: test video URL", logs[0].read_text())

    def test_live_setup_lock_is_preserved(self):
        self.lock.mkdir()
        (self.lock / "pid").write_text(str(os.getpid()))
        result = self.run_launcher("--check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("另一个窗口", result.stdout)
        self.assertEqual((self.lock / "pid").read_text(), str(os.getpid()))

    def test_abandoned_setup_lock_is_recovered(self):
        self.lock.mkdir()
        result = self.run_launcher("--check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("环境准备完成", result.stdout)
        self.assertFalse(self.lock.exists())


if __name__ == "__main__":
    unittest.main()
