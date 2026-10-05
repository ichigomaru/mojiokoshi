import sounddevice as sd
import numpy as np
import whisper
import librosa
import threading
import queue
import os
import tkinter as tk
from tkinter import messagebox
import datetime  
import soundfile as sf 


# ----- 設定項目 -----
RECORD_SEC = 5            # 5秒ごとの分割録音
BUFFER_SEC = 60           # 60秒分貯まったらキューに送る
SAMPLE_RATE = 48000       # 録音時サンプルレート
TARGET_SR = 16000         # Whisper用サンプルレート
NUM_CHANNEL = 3
VOLUME = 1.3
MODEL_SIZE = "large"      # whisperモデルサイズ
SD_DEVICE = "mojiokoshi"  # spot検索、オーディオデバイスの設定から変更可能
LANGUAGE = "ja"           # Whisperの言語設定（例: "ja"、"en"）

class MojiOkoshi:
    def __init__(self):
        print(f"Whisperモデル({MODEL_SIZE})を読み込み中...")
        self.model = whisper.load_model(MODEL_SIZE)
        print("モデル読み込み完了")

        self.audio_queue = queue.Queue()
        self.text_results = []
        self.stop_flag = threading.Event()
        self.thread = None
        self.current_scene = "default"
        self.scene_transcriptions = {}
        self.transcription_lock = threading.Lock() 
        self.scenes = {}
        
        # WAV保存用
        self.voice_log_dir = os.path.join("log", "voice")
        os.makedirs(self.voice_log_dir, exist_ok=True)
        self.wav_writer = None
        self.current_wav_path = None
        
        # 即時テキスト保存用
        self.other_log_dir = os.path.join("log", "scenario_log", "other")
        os.makedirs(self.other_log_dir, exist_ok=True)
        self.current_text_log_path = None
        
        self.partial_audio_buffer = []
        self.blocksize = int(RECORD_SEC * SAMPLE_RATE) # 48k
        self.buffer_target_size = int(BUFFER_SEC * SAMPLE_RATE) # 10秒分 (48k)
        
        self.processing_progress = {
            'total_items': 0,
            'processed_items': 0,
            'current_stage': 'idle'
        }


    def audio_callback(self, indata, frames, time_info, status):
        if self.stop_flag.is_set():
            return
        if status:
            print(f"audio_callback status: {status}")
            
        # --- 3ch -> 2ch へのミックスダウンを正しく実行 ---
        try:
            if self.wav_writer:
                indata_t = indata.T

                resampled_data_t = librosa.resample(indata_t, orig_sr=SAMPLE_RATE, target_sr=TARGET_SR)
                resampled_data = resampled_data_t.T

                mic_channel = resampled_data[:, 0]
                virtual_mono = np.mean(resampled_data[:, 1:3], axis=1)

                output_data = np.stack((mic_channel, virtual_mono), axis=1)
                self.wav_writer.write(output_data)
                
        except Exception as e:
            print(f"WAVファイルへの書き込みエラー: {e}")
            
        # 処理キューには 48kHz / 3ch のデータをそのまま渡す
        self.partial_audio_buffer.append(indata.copy())
        
        total_frames = sum(data.shape[0] for data in self.partial_audio_buffer)
        if total_frames >= self.buffer_target_size:
            combined_data = np.concatenate(self.partial_audio_buffer, axis=0)
            self.audio_queue.put(combined_data)
            print(f"{BUFFER_SEC}秒分のブロックをキューに追加 - 現在のキューサイズ: {self.audio_queue.qsize()}")
            self.partial_audio_buffer = []

    def transcribe_worker(self):
        processed_index = 0
        while not self.stop_flag.is_set() or not self.audio_queue.empty() or self.partial_audio_buffer:
            try:
                timeout = 0.5 if self.stop_flag.is_set() else 1.0
                data = self.audio_queue.get(timeout=timeout) # 48kHz / 3ch
                # 終了シグナル (None) を受け取ったらワーカー終了
                if data is None:
                    self.audio_queue.task_done()
                    print("[DEBUG] transcribe_worker: received None → exiting")
                    break
                try:
                    processed_index += 1
                    total_queue = processed_index + self.audio_queue.qsize()
                    print(f"処理開始 ({processed_index} / {total_queue})")

                    # モノラル化 (np.meanが3chすべてを平均化してくれる)
                    if data.ndim > 1:
                        mono = np.mean(data, axis=1)
                    else:
                        mono = data.flatten()

                    if mono.size == 0:
                        text = "[音声なし]"
                        self.text_results.append(text)
                        self.add_transcription(text) # [音声なし] もログには残す
                        print(f"処理完了 ({processed_index} / {total_queue})")
                        continue

                    # リサンプリング (48kHz -> 16kHz)
                    resampled = librosa.resample(mono, orig_sr=SAMPLE_RATE, target_sr=TARGET_SR)
                    resampled = np.clip(resampled * VOLUME, -1.0, 1.0)

                    # Whisperで文字起こし
                    try:
                        result = self.model.transcribe(resampled, language=LANGUAGE)
                        text = result["text"]
                        print(text)
                        self.text_results.append(text)
                        self.add_transcription(text) # 成功したテキストをログに書く
                    except Exception as e:
                        text = f"[文字起こしエラー: {str(e)[:50]}...]"
                        self.text_results.append(text)
                        self.add_transcription(text) # エラー内容をログに書く
                    print(f"処理完了 ({processed_index} / {total_queue})")
                finally:
                    self.audio_queue.task_done()
            except queue.Empty:
                if self.stop_flag.is_set() and self.audio_queue.empty() and len(self.partial_audio_buffer) == 0:
                    break
                continue

    def start(self):
        try:
            sd.default.device = SD_DEVICE
            sd.default.samplerate = SAMPLE_RATE # 48000
            sd.default.channels = NUM_CHANNEL # 3

            now = datetime.datetime.now()
            timestamp_str = now.strftime("%Y-%m-%d_%H-%M-%S")

            # --- WAVファイルの設定 (16kHz / 2ch) ---
            try:
                wav_filename = timestamp_str + ".wav"
                self.current_wav_path = os.path.join(self.voice_log_dir, wav_filename)
                
                # 保存するWAVファイルは 2 チャンネル、16kHz (TARGET_SR) で作成
                self.wav_writer = sf.SoundFile(
                    self.current_wav_path, 
                    mode='w', 
                    samplerate=TARGET_SR, # 16000
                    channels=2            # 2チャンネル (Mic, Virtual Mono)
                )
                print(f"録音データを {self.current_wav_path} に (2ch, 16kHzで) 保存開始...")
            except Exception as e:
                print(f"録音ファイルの作成に失敗しました: {e}")
                self.wav_writer = None
            
            # --- 即時テキストログファイルの設定 ---
            try:
                text_filename = timestamp_str + ".txt"
                self.current_text_log_path = os.path.join(self.other_log_dir, text_filename)
                with open(self.current_text_log_path, "w", encoding="utf-8") as f:
                    f.write(f"--- 録音開始: {timestamp_str} ---\n")
                print(f"即時ログを {self.current_text_log_path} に保存開始...")
            except Exception as e:
                print(f"即時ログファイルの作成に失敗しました: {e}")
                self.current_text_log_path = None

            blocksize = int(RECORD_SEC * SAMPLE_RATE) # 48000Hz基準

            self.stream = sd.InputStream(callback=self.audio_callback, blocksize=blocksize)
            self.stream.start()
            print(f"{RECORD_SEC}秒間隔で録音開始...")

            self.thread = threading.Thread(target=self.transcribe_worker, daemon=True)
            self.thread.start()
        except Exception as e:
            print(f"録音開始エラー: {e}")
            if self.wav_writer:
                self.wav_writer.close()
                self.wav_writer = None
            self.current_text_log_path = None
            raise

    def stop(self):
        print("\n録音停止中...")

        if hasattr(self, 'stream') and self.stream.active:
            self.stream.stop()
            self.stream.close()
            print("録音ストリームを停止しました。")
            
        if self.wav_writer:
            try:
                self.wav_writer.close()
                print(f"WAVデータを {self.current_wav_path} に保存完了しました。")
            except Exception as e:
                print(f"WAVファイルのクローズ中にエラーが発生しました: {e}")
            self.wav_writer = None
            self.current_wav_path = None

        if self.partial_audio_buffer:
            print(f"残りの音声データ ({sum(data.shape[0] for data in self.partial_audio_buffer)}フレーム) をキューに追加します。")
            combined_data = np.concatenate(self.partial_audio_buffer, axis=0)
            self.audio_queue.put(combined_data)
            self.partial_audio_buffer = []

        print("残りの文字起こし処理を待っています...")
        self.audio_queue.join()

        self.stop_flag.set()

        if self.thread is not None and self.thread.is_alive():
            print("文字起こしスレッドの終了を待機中...")
            self.thread.join()
            print("スレッドが正常に終了しました。")
            
        if self.current_text_log_path:
            try:
                with open(self.current_text_log_path, "a", encoding="utf-8") as f:
                    f.write(f"\n--- 録音停止 ---\n")
                print(f"即時ログを {self.current_text_log_path} に保存完了しました。")
            except Exception as e:
                print(f"即時ログファイルの後処理エラー: {e}")
            self.current_text_log_path = None

        self.update_progress('saving', self.processing_progress['total_items'], self.processing_progress['total_items'])
        print("録音停止処理が完了しました。")

    def save(self, filename):
        with open(filename, "w", encoding="utf-8") as f:
            f.write("\n".join(self.text_results)) 
        print(f"文字起こし結果を {filename} に保存しました。")

    def switch_scene(self, scene_title: str):
        """現在の録音シーンを切り替える"""
        if not scene_title:
            print("⚠️ シーン名が空です。")
            return False

        if scene_title in self.scene_transcriptions:
            print(f"⚠️ シーン名 '{scene_title}' は既に存在します。別の名前を入力してください。")
            return False

        prev_scene = self.current_scene
        old_buffer = self.partial_audio_buffer
        old_queue_items = []
        while not self.audio_queue.empty():
            try:
                old_queue_items.append(self.audio_queue.get_nowait())
            except queue.Empty:
                break
        old_queue = self.audio_queue

        self.partial_audio_buffer = []
        #self.audio_queue = queue.Queue()
        
        self.current_scene = scene_title
        self.scene_transcriptions[scene_title] = []

        if old_buffer or not old_queue.empty():
            queue_items = []
            while not old_queue.empty():
                try:
                    queue_items.append(old_queue.get_nowait())
                except queue.Empty:
                    break
            
            threading.Thread(
                target=self.process_scene_async,
                args=(prev_scene, old_buffer, queue_items),
                daemon=True
            ).start()
            print(f"シーン '{prev_scene}' の未処理データをバックグラウンドで処理開始。")

        print(f"\n🎬 シーン切り替え → {scene_title}")
        return True

    def add_transcription(self, text: str):
        """文字起こし結果を現在のシーンに追加 (および即時ログに追記)"""
        
        # 1. GUI用のシーンリストに追加
        with self.transcription_lock:
            if self.current_scene not in self.scene_transcriptions:
                self.scene_transcriptions[self.current_scene] = []
            self.scene_transcriptions[self.current_scene].append(text)
        
        print(f"シーン '{self.current_scene}' にテキストを追加: '{text[:50]}...'")
        
        # 2. 即時テキストログへの追記
        if self.current_text_log_path:
            try:
                # "a" (append) モードでファイルを開き、テキストを追記
                with open(self.current_text_log_path, "a", encoding="utf-8") as f:
                    f.write(text + "\n")
            except Exception as e:
                print(f"即時ログファイルへの書き込みエラー: {e}")
    
    def update_progress(self, stage: str, processed: int = None, total: int = None):
        """処理進行状況を更新"""
        self.processing_progress['current_stage'] = stage
        if processed is not None:
            self.processing_progress['processed_items'] = processed
        if total is not None:
            self.processing_progress['total_items'] = total
    
    def get_progress_percentage(self):
        """進行状況のパーセンテージを取得"""
        if self.processing_progress['total_items'] == 0:
            return 0
        return int((self.processing_progress['processed_items'] / self.processing_progress['total_items']) * 100)

    def save_all_scenes(self, output_dir=None):
        """
        全シーンの文字起こしを保存
        - 各シーンの.txtを log/output/ フォルダ内に保存
        """
        if output_dir is None:
            output_dir = os.path.join("log", "output")
        os.makedirs(output_dir, exist_ok=True)
        self.scenes = {}

        with self.transcription_lock:
            scene_data_copy = {scene: list(texts) for scene, texts in self.scene_transcriptions.items()}

        for scene, texts in scene_data_copy.items():
            clean_texts = [t for t in texts if t.strip()]
            if not clean_texts:
                print(f"シーン '{scene}' は空なのでスキップします。")
                continue
            safe_name = scene.replace("/", "_").replace("\\", "_")
            file_path = os.path.join(output_dir, f"{safe_name}.txt")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("\n".join(clean_texts))
            print(f"💾 保存完了: {file_path}")
            self.scenes[scene] = "\n".join(clean_texts)

    @property
    def transcription(self):
        return "\n".join(self.text_results)
    
    def get_initial_scene_name(self, parent_window=None):
        """最初のシーン名を入力するダイアログを表示"""
        dialog = tk.Toplevel(parent_window) if parent_window else tk.Tk()
        dialog.title("シーン名を入力")
        dialog.geometry("400x150")
        dialog.resizable(False, False)
        
        if parent_window:
            dialog.transient(parent_window)
            dialog.grab_set()
            dialog.geometry("+%d+%d" % (parent_window.winfo_rootx() + 50, parent_window.winfo_rooty() + 50))
        
        label = tk.Label(dialog, text="最初のシーン名を入力してください:", font=("Arial", 12))
        label.pack(pady=20)
        
        entry = tk.Entry(dialog, width=30, font=("Arial", 11))
        entry.pack(pady=10)
        entry.focus()
        
        button_frame = tk.Frame(dialog)
        button_frame.pack(pady=10)
        
        result = {"scene_name": None}
        
        def on_ok():
            scene_name = entry.get().strip()
            if scene_name:
                result["scene_name"] = scene_name
                self.switch_scene(scene_name)
                dialog.destroy()
            else:
                messagebox.showwarning("警告", "シーン名を入力してください。")
        
        def on_cancel():
            result["scene_name"] = "default"
            self.switch_scene("default")
            dialog.destroy()
        
        ok_button = tk.Button(button_frame, text="OK", command=on_ok, width=10)
        ok_button.pack(side=tk.LEFT, padx=5)
        
        cancel_button = tk.Button(button_frame, text="デフォルト", command=on_cancel, width=10)
        cancel_button.pack(side=tk.LEFT, padx=5)
        
        entry.bind('<Return>', lambda e: on_ok())
        dialog.bind('<Escape>', lambda e: on_cancel())
        
        if parent_window:
            dialog.wait_window()
        else:
            dialog.mainloop()
        
        return result["scene_name"]
    
    def save_combined_scenario(self, scenario_title, output_dir=None):
        """
        全シーンをまとめて1つのテキストファイルに保存。
        - 結合テキストは log/scenario_log/ フォルダ内に保存
        """
        if not self.scenes:
            print("DEBUG: scenesが空です。save_all_scenes()を先に呼んでください。")
            return None

        if output_dir is None:
            output_dir = os.path.join("log", "scenario_log")
        os.makedirs(output_dir, exist_ok=True)
        safe_title = scenario_title.replace("/", "_").replace("\\", "_")
        combined_file_path = os.path.join(output_dir, f"{safe_title}.txt")

        sentence_terminators = ("。", "！", ".", "!", "?")

        with open(combined_file_path, "w", encoding="utf-8") as f:
            for idx, (scene_name, text) in enumerate(self.scenes.items()):
                f.write(f"【{scene_name}】\n")
                lines = text.splitlines()
                for line in lines:
                    line = line.rstrip()
                    if not line:
                        continue
                    f.write(line)
                    if line.endswith(sentence_terminators):
                        f.write("\n\n")
                    else:
                        f.write("\n")
                f.write("\n\n\n")

        print(f"全シーン結合テキスト保存完了: {combined_file_path}")
        return combined_file_path
    
    def process_partial_buffer_for_scene(self):
        """現在のシーンに対して、未処理バッファとキューのデータを文字起こしして追加"""
        if self.partial_audio_buffer:
            combined_data = np.concatenate(self.partial_audio_buffer, axis=0)
            mono = np.mean(combined_data, axis=1) if combined_data.ndim > 1 else combined_data.flatten()
            if mono.size > 0:
                resampled = librosa.resample(mono, orig_sr=SAMPLE_RATE, target_sr=TARGET_SR)
                resampled = np.clip(resampled * 1.3, -1.0, 1.0)
                try:
                    result = self.model.transcribe(resampled, language=LANGUAGE)
                    text = result["text"]
                    self.add_transcription(text) # このテキストも即時ログに追記される
                except Exception as e:
                    text = f"[文字起こしエラー: {str(e)[:50]}...]"
                    self.add_transcription(text) # エラーも追記
            self.partial_audio_buffer = []

        while not self.audio_queue.empty():
            try:
                data = self.audio_queue.get_nowait()
                mono = np.mean(data, axis=1) if data.ndim > 1 else data.flatten()
                if mono.size > 0:
                    resampled = librosa.resample(mono, orig_sr=SAMPLE_RATE, target_sr=TARGET_SR)
                    resampled = np.clip(resampled * 1.3, -1.0, 1.0)
                    try:
                        result = self.model.transcribe(resampled, language=LANGUAGE)
                        text = result["text"]
                        self.add_transcription(text) # このテキストも即時ログに追記される
                    except Exception as e:
                        text = f"[文字起こしエラー: {str(e)[:50]}...]"
                        self.add_transcription(text) # エラーも追記
                self.audio_queue.task_done()
            except queue.Empty:
                break
    
    def process_scene_async(self, scene_name, buffer_data, queue_data):
        """
        バックグラウンドで(古い)シーンのバッファとキューを文字起こしして追加
        (メモ: これは古いシーンの残りデータ用なので、即時ログには追記しない)
        """
        texts_to_add = []
        
        if buffer_data:
            try:
                combined_data = np.concatenate(buffer_data, axis=0)
                mono = np.mean(combined_data, axis=1) if combined_data.ndim > 1 else combined_data.flatten()
                if mono.size == 0:
                    pass
                elif mono.size < TARGET_SR * 0.02:  # ★ 追加
                    pass
                else:
                    resampled = librosa.resample(mono, orig_sr=SAMPLE_RATE, target_sr=TARGET_SR)
                    resampled = np.clip(resampled * 1.3, -1.0, 1.0)
                    if len(resampled) < 320:
                        pass
                    else:
                        result = self.model.transcribe(resampled, language=LANGUAGE)
                        texts_to_add.append(result["text"])
            except Exception as e:
                texts_to_add.append(f"[文字起こしエラー: {str(e)[:50]}...]")
                print(f"[バックグラウンドバッファ処理エラー: {e}]")

        for data in queue_data:
            try:
                mono = np.mean(data, axis=1) if data.ndim > 1 else data.flatten()
                
                if mono.size == 0:
                    continue
                
                if mono.size < TARGET_SR * 0.02:
                    continue

                resampled = librosa.resample(mono, orig_sr=SAMPLE_RATE, target_sr=TARGET_SR)
                resampled = np.clip(resampled * 1.3, -1.0, 1.0)

                if len(resampled) < 320:
                    continue

                result = self.model.transcribe(resampled, language=LANGUAGE)
                texts_to_add.append(result["text"])

            except Exception as e:
                texts_to_add.append(f"[文字起こしエラー: {str(e)[:50]}...]")
                print(f"[バックグラウンドキュー処理エラー: {e}]")

        if texts_to_add:
            with self.transcription_lock:
                if scene_name not in self.scene_transcriptions:
                    self.scene_transcriptions[scene_name] = []
                self.scene_transcriptions[scene_name].extend(texts_to_add)
                
        print(f"シーン '{scene_name}' のバックグラウンド処理が完了しました。")