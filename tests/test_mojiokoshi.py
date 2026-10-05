"""
MojiOkoshi の停止処理・シーン切り替えのテスト (Whisper とマイクは偽物に差し替え、CPUのみで数秒で終わる)
実行: .venv/bin/python -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import threading
import time
import types
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class FakeModel:
    """音声の長さ(秒)を返す偽 Whisper。同時に呼ばれた最大数も記録する。"""

    def __init__(self, delay=0.0):
        self.delay = delay
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

    def transcribe(self, audio, language=None):
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(self.delay)
        with self.lock:
            self.active -= 1
        return {"text": f"{len(audio) / 16000:.0f}s"}


class FakeStream:
    """start/stop だけする偽ストリーム。音声はテストから audio_callback を直接呼んで流す。"""

    def __init__(self, callback=None, blocksize=None):
        self.active = False

    def start(self):
        self.active = True

    def stop(self):
        self.active = False

    def close(self):
        pass


fake_whisper = types.ModuleType("whisper")
fake_whisper.load_model = lambda size: FakeModel()
fake_sd = types.ModuleType("sounddevice")
fake_sd.default = types.SimpleNamespace(device=None, samplerate=None, channels=None)
fake_sd.InputStream = FakeStream
sys.modules["whisper"] = fake_whisper
sys.modules["sounddevice"] = fake_sd

import mojiokoshi  # noqa: E402

SR = mojiokoshi.SAMPLE_RATE


def block(sec=1.0):
    return np.full((int(SR * sec), mojiokoshi.NUM_CHANNEL), 0.1, dtype=np.float32)


def run_with_timeout(func, timeout=10):
    t = threading.Thread(target=func, daemon=True)
    t.start()
    t.join(timeout)
    return not t.is_alive()


class MojiOkoshiTest(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)
        self.m = mojiokoshi.MojiOkoshi()
        self.m.buffer_target_size = 3 * SR  # 3秒たまったらキューへ

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def feed(self, n):
        for _ in range(n):
            self.m.audio_callback(block(), SR, None, None)

    def test_stop_processes_remaining_buffer(self):
        self.m.start()
        self.feed(5)  # 3秒はキューへ、2秒はバッファに残る
        self.assertTrue(run_with_timeout(self.m.stop), "stop() が終わらない")
        self.assertEqual(self.m.scene_transcriptions["default"], ["3s", "2s"])
        self.assertIsNone(self.m.thread)

    def test_scene_switch_assigns_audio_to_recorded_scene(self):
        self.m.model = FakeModel(delay=0.3)  # ワーカーが処理中に切り替える
        self.m.start()
        self.feed(3)  # default: 3秒 (キューへ)
        self.feed(1)  # default: 1秒 (バッファ)
        self.assertTrue(self.m.switch_scene("A"))
        self.feed(2)
        self.assertTrue(self.m.switch_scene("B"))
        self.feed(4)  # B: 3秒 (キューへ) + 1秒 (バッファ)
        self.assertTrue(run_with_timeout(self.m.stop), "stop() が終わらない")
        t = self.m.scene_transcriptions
        self.assertEqual(t["default"], ["3s", "1s"])
        self.assertEqual(t["A"], ["2s"])
        self.assertEqual(t["B"], ["3s", "1s"])
        self.assertEqual(self.m.model.max_active, 1, "モデルが同時に使われた")

    def test_duplicate_scene_rejected(self):
        self.assertTrue(self.m.switch_scene("A"))
        self.assertFalse(self.m.switch_scene("A"))
        self.assertFalse(self.m.switch_scene(""))
        self.assertEqual(self.m.current_scene, "A")

    def test_second_recording_after_stop(self):
        self.m.start()
        self.feed(2)
        self.assertTrue(run_with_timeout(self.m.stop))
        self.m.switch_scene("2回目")
        self.m.start()
        self.feed(1)
        self.assertTrue(run_with_timeout(self.m.stop))
        self.assertEqual(self.m.scene_transcriptions["default"], ["2s"])
        self.assertEqual(self.m.scene_transcriptions["2回目"], ["1s"])

    def test_stop_twice_is_noop(self):
        self.m.start()
        self.feed(1)
        self.assertTrue(run_with_timeout(self.m.stop))
        self.assertTrue(run_with_timeout(self.m.stop))
        self.assertEqual(self.m.scene_transcriptions["default"], ["1s"])

    def test_concurrent_callback_and_switch(self):
        """録音コールバックとシーン切り替えが同時に走っても音声が消えない"""
        self.m.start()
        names = [f"S{i}" for i in range(20)]

        def feeder():
            self.feed(40)

        t = threading.Thread(target=feeder)
        t.start()
        for name in names:
            self.m.switch_scene(name)
            time.sleep(0.005)
        t.join()
        self.assertTrue(run_with_timeout(self.m.stop, timeout=30))
        total = sum(int(x[:-1]) for texts in self.m.scene_transcriptions.values() for x in texts)
        self.assertEqual(total, 40)

    def test_wav_and_text_log_written(self):
        self.m.start()
        wav = self.m.current_wav_path
        log = self.m.current_text_log_path
        self.feed(2)
        self.assertTrue(run_with_timeout(self.m.stop))
        self.assertTrue(os.path.getsize(wav) > 0)
        with open(log, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("2s", content)
        self.assertIn("録音停止", content)


if __name__ == "__main__":
    unittest.main()
