"""main.py の stderr フィルタのテスト (fd 2 を差し替えるので別プロセスで実行する)"""
import os
import subprocess
import sys
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")

SCRIPT = r"""
import os, sys, time
sys.path.insert(0, ROOT)
import main
main.filter_stderr()
os.write(2, b"2026-10-05 python3[1:2] TSM AdjustCapsLockLEDForKeyTransitionHandling - Inhibit\n")
os.write(2, b"2026-10-05 python3[1:2] error messaging the mach port for IMKCFRunLoopWakeUpReliable\n")
os.write(2, b"normal line\n")
os.write(2, b"\rprogress 50%")
os.write(2, b"\rprogress 100%\r")
time.sleep(0.5)
os.write(1, b"MARK\n")  # この時点で進捗表示が届いているか確かめるための目印
sys.stdout.flush()
raise RuntimeError("traceback at exit")
""".replace("ROOT", repr(os.path.abspath(ROOT)))


class FilterStderrTest(unittest.TestCase):
    def test_filter(self):
        # stdout と stderr を同じパイプにまとめ、届いた順番も確かめる
        r = subprocess.run([sys.executable, "-c", SCRIPT], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=30)
        out = r.stdout.decode("utf-8")
        self.assertNotIn("TSM AdjustCapsLockLED", out)
        self.assertNotIn("IMKCFRunLoopWakeUpReliable", out)
        self.assertIn("normal line\n", out)
        self.assertIn("\rprogress 50%\rprogress 100%\r", out)
        # 改行のない進捗表示も MARK より前に届いている (溜め込まれていない)
        self.assertLess(out.index("progress 100%"), out.index("MARK"))
        self.assertIn("RuntimeError: traceback at exit", out)
        self.assertEqual(r.returncode, 1)


if __name__ == "__main__":
    unittest.main()
