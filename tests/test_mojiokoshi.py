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

    def transcribe(self, audio, language=None, fp16=True):
        self.last_fp16 = fp16
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
        self.assertTrue(self.m.model_ready.wait(5), "偽モデルの読み込みが終わらない")
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
        self.assertFalse(self.m.model.last_fp16, "CPU では fp16=False を渡す")

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



class BackgroundModelLoadTest(unittest.TestCase):
    """モデルを裏で読み込んでいる間の録音・停止"""

    def setUp(self):
        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)
        self.release = threading.Event()
        self._orig_load = fake_whisper.load_model

        def slow_load(size):
            self.release.wait(10)
            if getattr(self, "load_should_fail", False):
                raise RuntimeError("読み込み失敗(テスト)")
            return FakeModel()

        fake_whisper.load_model = slow_load
        self.m = mojiokoshi.MojiOkoshi()
        self.m.buffer_target_size = 3 * SR

    def tearDown(self):
        self.release.set()
        fake_whisper.load_model = self._orig_load
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def feed(self, n):
        for _ in range(n):
            self.m.audio_callback(block(), SR, None, None)

    def test_record_while_loading_then_transcribe(self):
        self.m.start()  # モデル読み込み前でも録音を開始できる
        self.assertFalse(self.m.model_ready.is_set())
        self.feed(4)
        self.release.set()  # 停止前に読み込み完了
        self.assertTrue(self.m.model_ready.wait(5))
        self.feed(1)
        self.assertTrue(run_with_timeout(self.m.stop))
        self.assertFalse(self.m.last_stop_skipped)
        self.assertEqual(self.m.scene_transcriptions["default"], ["3s", "2s"])

    def test_stop_before_model_loaded_skips(self):
        self.m.start()
        self.feed(4)
        self.assertTrue(run_with_timeout(self.m.stop), "読み込み前の停止で固まった")
        self.assertTrue(self.m.last_stop_skipped)
        self.assertEqual(self.m.scene_transcriptions.get("default", []), [])
        self.assertTrue(os.path.getsize(self.m.last_wav_path) > 0)
        # 後から読み込みが終わったら、次の録音は普通に文字起こしされる
        self.release.set()
        self.assertTrue(self.m.model_ready.wait(5))
        self.m.switch_scene("次")
        self.m.start()
        self.feed(1)
        self.assertTrue(run_with_timeout(self.m.stop))
        self.assertFalse(self.m.last_stop_skipped)
        self.assertEqual(self.m.scene_transcriptions["次"], ["1s"])

    def test_wav_creation_failure_reports_no_path(self):
        self.m.voice_log_dir = os.path.join(self._tmp.name, "存在しないフォルダ")
        self.m.start()
        self.feed(1)
        self.assertTrue(run_with_timeout(self.m.stop))
        self.assertTrue(self.m.last_stop_skipped)
        self.assertIsNone(self.m.last_wav_path)

    def test_model_load_failure(self):
        self.load_should_fail = True
        self.m.start()
        self.feed(4)
        self.release.set()
        self.assertTrue(self.m.model_ready.wait(5))
        self.assertIsNone(self.m.model)
        self.assertIn("読み込み失敗", self.m.model_error)
        self.assertTrue(run_with_timeout(self.m.stop))
        self.assertTrue(self.m.last_stop_skipped)



def seg(text, no_speech=0.1):
    return {"text": text, "no_speech_prob": no_speech}


class CleanTranscriptionTest(unittest.TestCase):
    """無音の幻の文 (ご視聴ありがとうございました 等) を消す処理"""

    def clean(self, *segments):
        return mojiokoshi.clean_transcription({"text": "".join(s["text"] for s in segments),
                                               "segments": list(segments)})

    def test_drop_phrase_segment(self):
        self.assertEqual(self.clean(seg("こんにちは。"), seg("ご視聴ありがとうございました")), "こんにちは。")
        self.assertEqual(self.clean(seg(" ご視聴ありがとうございました。")), "")

    def test_keep_other_segments_regardless_of_no_speech_prob(self):
        # 無音判定 (no_speech_prob) では消さない。定型文以外はそのまま残す
        self.assertEqual(self.clean(seg("では始めます"), seg("小声の一言", no_speech=0.9)), "では始めます小声の一言")

    def test_keep_phrase_inside_real_speech(self):
        # 定型文が他の発言と同じ区間に混ざっている場合は消さない
        text = "皆さんご視聴ありがとうございましたと言って終わります"
        self.assertEqual(self.clean(seg(text)), text)

    def test_without_segments(self):
        self.assertEqual(mojiokoshi.clean_transcription({"text": "ご視聴ありがとうございました"}), "")
        self.assertEqual(mojiokoshi.clean_transcription({"text": "普通の発言"}), "普通の発言")


class HallucinationWorkerTest(unittest.TestCase):
    """ワーカー経由で、幻の文だけの音声はシーンに何も追加されない"""

    def setUp(self):
        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)
        self.m = mojiokoshi.MojiOkoshi()
        self.assertTrue(self.m.model_ready.wait(5))
        outputs = iter([
            {"text": "ご視聴ありがとうございました", "segments": [seg("ご視聴ありがとうございました", 0.2)]},
            {"text": "本題です", "segments": [seg("本題です")]},
        ])
        self.m.model = types.SimpleNamespace(transcribe=lambda audio, language=None, fp16=True: next(outputs))
        self.m.buffer_target_size = SR

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def test_worker_skips_empty_text(self):
        self.m.start()
        self.m.audio_callback(block(), SR, None, None)
        self.m.audio_callback(block(), SR, None, None)
        self.assertTrue(run_with_timeout(self.m.stop))
        self.assertEqual(self.m.scene_transcriptions["default"], ["本題です"])
        self.assertEqual(self.m.text_results, ["本題です"])


if __name__ == "__main__":
    unittest.main()
