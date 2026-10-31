"""マイク音声の笑い検出と、その直前の発言の抽出。"""
import os

import numpy as np

MODEL_REPO = 'MIT/ast-finetuned-audioset-10-10-0.4593'
THRESHOLD = 0.1
LAUGHTER_CLASSES = [16, 18, 19, 20, 21]
# 無音時の幻の文は文字起こしと笑いの前の文の両方から除く。
HALLUCINATION_PHRASES = ('ご視聴ありがとうございました',)
_PUNCTUATION = ' 　。、．，.,!！?？…・「」'


class LaughterDetector:
    def __init__(self):
        self.model = None
        self.failed = False

    def prepare(self):
        """アプリ起動時に読み込む(無ければここで一度だけダウンロードし、録音中には落とさない)"""
        if os.environ.get('MOJIOKOSHI_LAUGHTER') != '0':
            self._load(download=True)

    def _load(self, download=False):
        if self.model is not None:
            return True
        if self.failed:
            return False
        try:
            import torch
            from transformers import ASTFeatureExtractor, ASTForAudioClassification
            self.torch = torch
            self.device = 'mps' if torch.backends.mps.is_available() else 'cpu'
            # 録音中はキャッシュだけを使う(ダウンロードは prepare のときだけ)
            self.extractor = ASTFeatureExtractor.from_pretrained(MODEL_REPO, local_files_only=not download)
            self.model = ASTForAudioClassification.from_pretrained(
                MODEL_REPO, local_files_only=not download).to(self.device).eval()
            return True
        except Exception as e:
            self.model = None
            self.failed = True
            print(f'笑い検出モデルの読み込みに失敗しました: {e}')
            return False

    def _probabilities(self, chunks):
        # 窓をまとめて1回で判定する(1窓ずつより速い)
        inputs = self.extractor(chunks, sampling_rate=16000, return_tensors='pt')
        inputs = {key: value.to(self.device) for key, value in inputs.items()}
        with self.torch.inference_mode():
            logits = self.model(**inputs).logits
            return logits.sigmoid()[:, LAUGHTER_CLASSES].max(dim=1).values.cpu().tolist()

    def detect(self, mono16k):
        if os.environ.get('MOJIOKOSHI_LAUGHTER') == '0':
            return []
        audio = np.asarray(mono16k, dtype=np.float32)
        if not audio.size or not self._load():
            return []
        window, step = 160000, 80000
        # 末尾を含む最後の窓まで調べる。短い音声は1窓だけ。
        starts = range(0, max(1, len(audio) - window + step), step)
        chunks = []
        for start in starts:
            chunk = audio[start:start + window]
            rms = float(np.sqrt(np.mean(chunk.astype(np.float64) ** 2)))
            chunks.append(chunk * (0.1 / rms) if rms > 0 else chunk)
        probs = self._probabilities(chunks)
        return [(start / 16000, (start + len(chunk)) / 16000, prob)
                for start, chunk, prob in zip(starts, chunks, probs) if prob >= THRESHOLD]


def laugh_cores(laughs):
    """続いた笑いの窓をまとめ、笑いがありそうな狭い範囲にする。
    窓が続けば最初の2窓の重なり、1窓だけなら窓の真ん中の半分。"""
    cores = []
    run = []
    for laugh in sorted(laughs) + [None]:
        if run and (laugh is None or laugh[0] > run[-1][1]):
            if len(run) > 1:
                # 笑いの始まり(最初の2窓の重なり)。長く続いても範囲が逆転しない
                cores.append((run[1][0], run[0][1]))
            else:
                start, stop, _ = run[0]
                cores.append((start + (stop - start) / 4, stop - (stop - start) / 4))
            run = []
        if laugh is not None:
            run.append(laugh)
    return cores


def laugh_before(segments, laughs, lead=2.0, per_laugh=2):
    """笑いの範囲の少し前から範囲内までに終わる発言のうち、笑いごとに最後の数個を入力順で返す。"""
    usable = []
    for segment in segments:
        text = segment.get('text', '').strip()
        if text and text.strip(_PUNCTUATION) not in HALLUCINATION_PHRASES and segment.get('end') is not None:
            usable.append((segment['end'], text))
    picked = set()
    for start, stop in laugh_cores(laughs):
        near = [i for i, (end, _) in enumerate(usable) if start - lead <= end <= stop]
        picked.update(near[-per_laugh:])
    kept = []
    for i in sorted(picked):
        if usable[i][1] not in kept:
            kept.append(usable[i][1])
    return kept
