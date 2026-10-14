"""シーンごとの録音時間 (SceneClock) のテスト。画面は作らない"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from scene_clock import SceneClock  # noqa: E402


class SceneClockTest(unittest.TestCase):
    def test_counts_only_while_recording(self):
        c = SceneClock()
        c.add("A", now=0, recording=False)   # 録音前に最初のシーンを決める
        self.assertEqual(c.elapsed(0, now=50), 0)
        c.start(now=100)                     # REC
        self.assertEqual(c.elapsed(0, now=130), 30)
        c.add("B", now=160, recording=True)  # 録音中にシーン切り替え
        self.assertEqual(c.elapsed(0, now=999), 60)
        self.assertEqual(c.elapsed(1, now=170), 10)
        c.pause(now=200)                     # STOP (一時停止)
        self.assertEqual(c.elapsed(1, now=500), 40)
        c.start(now=500)                     # 再開
        self.assertEqual(c.elapsed(1, now=520), 60)
        self.assertEqual(c.total(now=520), 120)

    def test_pause_and_start_are_idempotent(self):
        c = SceneClock()
        c.start(now=0)  # シーンが無くても落ちない
        c.pause(now=0)
        c.add("A", now=0, recording=True)
        c.start(now=5)  # 数えている最中に start しても始点は変わらない
        c.pause(now=10)
        c.pause(now=20)
        self.assertEqual(c.total(now=30), 10)


if __name__ == "__main__":
    unittest.main()
