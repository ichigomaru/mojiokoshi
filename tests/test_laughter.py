"""モデルを読み込まず、CPUだけで笑い検出と保存を確認する。"""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from laughter import LaughterDetector, laugh_before, laugh_cores
import session_log


class LaughterTest(unittest.TestCase):
    def test_before(self):
        texts = [(7.9, '外'), (8, ' 前 '), (10, '中'), (20, '末尾'),
                 (20.1, '後'), (12, '前'), (13, '  '),
                 (14, 'ご視聴ありがとうございました。'), (15, '本当の発言')]
        segments = [dict(start=end - 1, end=end, text=text) for end, text in texts]
        # 笑いごとに、範囲の少し前から範囲内までに終わる発言の最後の2つ(同じ文は1つ)
        self.assertEqual(laugh_before(segments, [(10, 20, .2), (10, 20, .3)]), ['前', '本当の発言'])
        self.assertEqual(laugh_before(segments, [(10, 20, .2)], lead=0), ['本当の発言'])
        self.assertEqual(laugh_before(segments, [(10, 20, .2)], per_laugh=5), ['前', '本当の発言'])
        self.assertEqual(laugh_before(segments, []), [])

    def test_cores(self):
        self.assertEqual(laugh_cores([(0, 10, .2)]), [(2.5, 7.5)])
        self.assertEqual(laugh_cores([(5, 15, .2), (0, 10, .3)]), [(5, 10)])
        self.assertEqual(laugh_cores([(0, 10, .2), (20, 30, .2)]), [(2.5, 7.5), (22.5, 27.5)])
        # 長く続いた笑いも、始まりの範囲になる(逆転しない)
        self.assertEqual(laugh_cores([(0, 10, .2), (5, 15, .2), (10, 20, .2), (15, 25, .2)]), [(5, 10)])
        self.assertEqual(laugh_cores([]), [])

    def test_prepare_downloads_only_at_startup(self):
        detector = LaughterDetector()
        with patch.object(detector, '_load') as load:
            detector.prepare()
            load.assert_called_once_with(download=True)
        with patch.dict(os.environ, MOJIOKOSHI_LAUGHTER='0'), patch.object(detector, '_load') as load:
            detector.prepare()
            load.assert_not_called()

    def test_windows(self):
        for seconds, expected in [(0, []), (3, [(0, 3)]), (10, [(0, 10)]),
                                  (12, [(0, 10), (5, 12)]),
                                  (20, [(0, 10), (5, 15), (10, 20)])]:
            with self.subTest(seconds=seconds):
                detector = LaughterDetector()
                chunks = []
                def probabilities(batch):
                    chunks.extend(batch)
                    return [.2] * len(batch)
                with patch.object(detector, '_load', return_value=True), patch.object(
                        detector, '_probabilities', side_effect=probabilities):
                    detected = detector.detect(np.ones(seconds * 16000, dtype=np.float32))
                self.assertEqual([(a, b) for a, b, _ in detected], expected)
                for chunk in chunks:
                    self.assertAlmostEqual(float(np.sqrt(np.mean(chunk ** 2))), .1, places=6)

    def test_disabled_and_failed(self):
        detector = LaughterDetector()
        with patch.dict(os.environ, MOJIOKOSHI_LAUGHTER='0'), patch.object(detector, '_load') as load:
            self.assertEqual(detector.detect(np.ones(100)), [])
            load.assert_not_called()
        detector.failed = True
        self.assertEqual(detector.detect(np.ones(100)), [])

    def test_threshold_and_silence(self):
        detector = LaughterDetector()
        with patch.object(detector, '_load', return_value=True), patch.object(
                detector, '_probabilities', return_value=[.09, .1]) as probabilities:
            self.assertEqual(detector.detect(np.zeros(12 * 16000)), [(5, 12, .1)])
            self.assertTrue(all(np.isfinite(chunk).all() for chunk in probabilities.call_args.args[0]))

    def test_save(self):
        segments = session_log.build_segments('題', session_log.group_segments(
            {'A': '本文', 'S': '秘密', 'B': '続き'}, {'S': (True, '人')},
            {'A': ['文'], 'S': ['秘密の文']}))
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, HATSUGEN_HOME=directory):
            for segment, expected in zip(segments, [['文'], ['秘密の文'], []]):
                path = session_log.save_log('題', '', '日', segment, ['url'])
                with open(path, encoding='utf-8') as stream:
                    self.assertEqual(json.load(stream)['laughBefore'], expected)
            legacy = dict(secret=False, who='', scenes={'A': ''}, plain_text='')
            path = session_log.save_log('題', '', '日', legacy, ['url'])
            with open(path, encoding='utf-8') as stream:
                self.assertEqual(json.load(stream)['laughBefore'], [])
