import sounddevice as sd
import numpy as np
import whisper
import librosa
import threading
import queue
import time
import os
import tkinter as tk
from tkinter import messagebox

# ----- 設定項目 -----
RECORD_SEC = 5            # 5秒ごとの分割録音
BUFFER_SEC = 60           # 60秒分貯まったらキューに送る
SAMPLE_RATE = 48000       # 録音時サンプルレート
TARGET_SR = 16000         # Whisper用サンプルレート
NUM_CHANNEL = 3 
VOLUME = 1.3
MODEL_SIZE = "medium"     # whisperモデルサイズ
SD_DEVICE = "mojiokoshi"  # spot検索、オーディオデバイスの設定から変更可能

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
        self.scenes = {}
        
        
        # 未完成の録音ブロックを保持するバッファ
        self.partial_audio_buffer = []
        self.blocksize = int(RECORD_SEC * SAMPLE_RATE)  # 1秒分のフレーム数
        self.buffer_target_size = int(BUFFER_SEC * SAMPLE_RATE)  # 60秒分のフレーム数
        
        # 処理進行状況の追跡
        self.processing_progress = {
            'total_items': 0,
            'processed_items': 0,
            'current_stage': 'idle'  # idle, transcribing, saving, completed
        }


    def audio_callback(self, indata, frames, time_info, status):
        if self.stop_flag.is_set():
            return
        if status:
            print(f"audio_callback status: {status}")
        #print(f" データサイズ: {indata.shape}, フレーム数: {frames}")
        
        # データをバッファに追加
        self.partial_audio_buffer.append(indata.copy())
        
        # バッファが60秒分（buffer_target_size）に達したらキューに追加
        total_frames = sum(data.shape[0] for data in self.partial_audio_buffer)
        if total_frames >= self.buffer_target_size:
            # バッファのデータを結合してキューに追加
            combined_data = np.concatenate(self.partial_audio_buffer, axis=0)
            self.audio_queue.put(combined_data)
            print(f"60秒分のブロックをキューに追加 - 現在のキューサイズ: {self.audio_queue.qsize()}")
            # バッファをクリア
            self.partial_audio_buffer = []
        # else:
        #     print(f"DEBUG: データをバッファに蓄積中 - 現在のフレーム数: {total_frames}/{self.buffer_target_size} ({total_frames/self.buffer_target_size*100:.1f}%)")

    def transcribe_worker(self):
        processed_index = 0
        #print("DEBUG: transcribe_worker開始")
        while not self.stop_flag.is_set() or not self.audio_queue.empty() or self.partial_audio_buffer:
            #print(f"DEBUG: ループ開始 - stop_flag: {self.stop_flag.is_set()}, queue_empty: {self.audio_queue.empty()}, buffer_empty: {len(self.partial_audio_buffer) == 0}")
            try:
                # stop_flagが設定されている場合は短いタイムアウトで待機
                timeout = 0.5 if self.stop_flag.is_set() else 1.0
                #print("DEBUG: キューからデータを取得中...")
                data = self.audio_queue.get(timeout=timeout)
                #print("DEBUG: データ取得成功")
                try:
                    processed_index += 1
                    total_queue = processed_index + self.audio_queue.qsize()
                    print(f"処理開始 ({processed_index} / {total_queue})")

                    # モノラル化
                    if data.ndim > 1:
                        mono = np.mean(data, axis=1)
                    else:
                        mono = data.flatten()

                    # 空データチェック
                    if mono.size == 0:
                        text = "[音声なし]"
                        #print("DEBUG: 音声データが空です。プレースホルダーを追加します。")
                        self.text_results.append(text)
                        self.add_transcription(text)
                        print(f"処理完了 ({processed_index} / {total_queue})")
                        continue

                    # リサンプリング
                    resampled = librosa.resample(mono, orig_sr=SAMPLE_RATE, target_sr=TARGET_SR)
                    resampled = np.clip(resampled * VOLUME, -1.0, 1.0)

                    # Whisperで文字起こし
                    #print(f"Whisper処理開始 ({processed_index} / {total_queue})")
                    try:
                        result = self.model.transcribe(resampled, language="ja")
                        text = result["text"]
                        print(text)
                        self.text_results.append(text)
                        self.add_transcription(text)
                    except Exception as e:
                        #print(f"DEBUG: Whisper処理中にエラー: {e}")
                        # エラーが発生しても処理を継続
                        text = f"[文字起こしエラー: {str(e)[:50]}...]"
                        self.text_results.append(text)
                        self.add_transcription(text)
                    print(f"処理完了 ({processed_index} / {total_queue})")
                finally:
                    self.audio_queue.task_done()
            except queue.Empty:
                #print("DEBUG: キューが空（タイムアウト）")
                # stop_flagが設定されていて、キューが空でバッファも空の場合は終了
                if self.stop_flag.is_set() and self.audio_queue.empty() and len(self.partial_audio_buffer) == 0:
                    #print("DEBUG: stop_flagが設定されていてキューとバッファが空なので終了")
                    break
                continue
        #print("DEBUG: transcribe_worker終了")

    def start(self):
        #print("DEBUG: start()メソッド開始")
        
        # 利用可能なオーディオデバイスを表示
        #print("DEBUG: 利用可能なオーディオデバイス:")
        # devices = sd.query_devices()
        # for i, device in enumerate(devices):
        #     print(f"  {i}: {device['name']} (入力: {device['max_input_channels']}, 出力: {device['max_output_channels']})")
        
        try:
            sd.default.device = SD_DEVICE  # BlackHole + マイクの複合デバイス名
            sd.default.samplerate = SAMPLE_RATE
            sd.default.channels = NUM_CHANNEL  # モノラル録音
            #print(f"DEBUG: 録音設定 - デバイス: {SD_DEVICE}, サンプルレート: {SAMPLE_RATE}, チャンネル: {NUM_CHANNEL}")

            # バッファサイズは1秒分のフレーム数
            blocksize = int(RECORD_SEC * SAMPLE_RATE)
            #print(f"DEBUG: ブロックサイズ: {blocksize} (1秒分)")

            self.stream = sd.InputStream(callback=self.audio_callback, blocksize=blocksize)
            self.stream.start()
            print(f"{RECORD_SEC}秒間隔で録音開始...")
            #print("DEBUG: 録音ストリーム開始完了")

            self.thread = threading.Thread(target=self.transcribe_worker, daemon=True)
            self.thread.start()
            #print("DEBUG: transcribe_workerスレッド開始")
        except Exception as e:
            print(f"録音開始エラー: {e}")
            raise

    def stop(self):
        print("\n録音停止中...")
        #print(f"DEBUG: stop_flag設定前の状態: {self.stop_flag.is_set()}")
        self.stop_flag.set()
        #print(f"DEBUG: stop_flag設定後の状態: {self.stop_flag.is_set()}")
        
        # スレッドの状態を確認
        #print(f"DEBUG: スレッドの状態 - is_alive: {self.thread.is_alive() if self.thread else 'None'}")
        #print(f"DEBUG: キューのサイズ: {self.audio_queue.qsize()}")
        
        # 処理項目数を計算
        queue_size = self.audio_queue.qsize()
        buffer_has_data = len(self.partial_audio_buffer) > 0
        total_items = queue_size + (1 if buffer_has_data else 0)
        self.update_progress('transcribing', 0, total_items)
        
        # まず、transcribe_workerスレッドの終了を待つ（最大30秒）
        if self.thread is not None and self.thread.is_alive():
            print("文字起こし処理の完了を待機中...")
            #print("DEBUG: thread.join()を呼び出します")
            self.thread.join(timeout=60)
            #print("DEBUG: thread.join()が完了しました")
            
            # タイムアウトした場合は強制終了
            if self.thread.is_alive():
                print("⚠️ 文字起こし処理がタイムアウトしました。強制終了します。")
            else:
                print("スレッドが正常に終了しました")
        else:
            print("スレッドが存在しないか、既に終了しています")
        
        # バッファに残っている未完成のデータを処理
        if self.partial_audio_buffer:
            #print(f"未完成のバッファデータを処理 - フレーム数: {sum(data.shape[0] for data in self.partial_audio_buffer)}")
            self.update_progress('transcribing', self.processing_progress['processed_items'], self.processing_progress['total_items'])
            try:
                # バッファのデータを結合
                combined_data = np.concatenate(self.partial_audio_buffer, axis=0)
                #print(f"未完成データの文字起こし開始")
                
                # モノラル化
                if combined_data.ndim > 1:
                    mono = np.mean(combined_data, axis=1)
                else:
                    mono = combined_data.flatten()

                # 空データチェック
                if mono.size == 0:
                    text = "[音声なし]"
                    #print("DEBUG: 未完成バッファの音声データが空です。プレースホルダーを追加します。")
                    self.text_results.append(text)
                    self.add_transcription(text)
                    self.update_progress('transcribing', self.processing_progress['processed_items'] + 1, self.processing_progress['total_items'])
                else:
                    # リサンプリング
                    resampled = librosa.resample(mono, orig_sr=SAMPLE_RATE, target_sr=TARGET_SR)
                    resampled = np.clip(resampled * 1.3, -1.0, 1.0)

                    # Whisperで文字起こし
                    try:
                        result = self.model.transcribe(resampled, language="ja")
                        text = result["text"]
                        #print(f"未完成データの文字起こし結果: {text}")
                        self.text_results.append(text)
                        self.add_transcription(text)
                        #print("DEBUG: 未完成データの処理完了")
                    except Exception as e:
                        print(f"DEBUG: 未完成データのWhisper処理中にエラー: {e}")
                        text = f"[未完成データ文字起こしエラー: {str(e)[:50]}...]"
                        self.text_results.append(text)
                        self.add_transcription(text)
                    finally:
                        # 進行状況を更新
                        self.update_progress('transcribing', self.processing_progress['processed_items'] + 1, self.processing_progress['total_items'])
            except Exception as e:
                print(f"DEBUG: 未完成データ処理中にエラー: {e}")
            finally:
                # バッファをクリア
                self.partial_audio_buffer = []
        
        # 残りのキューを処理（念のため）
        #print("DEBUG: 残りキュー処理開始")
        remaining_count = 0
        queue_size = self.audio_queue.qsize()
        #print(f"処理前のキューサイズ: {queue_size}")
        
        while not self.audio_queue.empty():
            try:
                #print(f"キューからデータを取得中... (残り: {self.audio_queue.qsize()})")
                data = self.audio_queue.get_nowait()
                remaining_count += 1
                print(f"残りデータ処理中... ({remaining_count})")
                
                # モノラル化
                if data.ndim > 1:
                    mono = np.mean(data, axis=1)
                else:
                    mono = data.flatten()

                # 空データチェック
                if mono.size == 0:
                    text = "[音声なし]"
                    #print("DEBUG: 残りキューの音声データが空です。プレースホルダーを追加します。")
                    self.text_results.append(text)
                    self.add_transcription(text)
                    self.audio_queue.task_done()
                    # 進行状況を更新
                    self.update_progress('transcribing', self.processing_progress['processed_items'] + 1, self.processing_progress['total_items'])
                    #print(f"Whisper処理完了 ({remaining_count})")
                    continue

                # リサンプリング
                resampled = librosa.resample(mono, orig_sr=SAMPLE_RATE, target_sr=TARGET_SR)
                resampled = np.clip(resampled * 1.3, -1.0, 1.0)

                # Whisperで文字起こし
                #print(f"Whisper処理開始 ({remaining_count})")
                try:
                    result = self.model.transcribe(resampled, language="ja")
                    text = result["text"]
                    print(text)
                    self.text_results.append(text)
                    self.add_transcription(text)
                except Exception as e:
                    #print(f"DEBUG: 残りキューWhisper処理中にエラー: {e}")
                    text = f"[残りキュー文字起こしエラー: {str(e)[:50]}...]"
                    self.text_results.append(text)
                    self.add_transcription(text)
                finally:
                    self.audio_queue.task_done()
                    # 進行状況を更新
                    self.update_progress('transcribing', self.processing_progress['processed_items'] + 1, self.processing_progress['total_items'])
                    #print(f"DEBUG: Whisper処理完了 ({remaining_count})")
                
            except queue.Empty:
                #print("DEBUG: キューが空です")
                break
            except Exception as e:
                #print(f"DEBUG: キュー処理中にエラー: {e}")
                break
        
        if remaining_count > 0:
            print(f"残り{remaining_count}件のデータ処理が完了しました。")
        else:
            print("処理する残りデータはありませんでした")
        
        # 最後に録音ストリームを停止（データ処理完了後）
        #print("DEBUG: ストリーム停止処理開始")
        if hasattr(self, 'stream'):
            #print("DEBUG: ストリームが存在します")
            self.stream.stop()
            #print("DEBUG: ストリーム停止完了")
            self.stream.close()
            #print("DEBUG: ストリームクローズ完了")
        else:
            print("ストリームが存在しません")
        
        # 文字起こし完了
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
            return False  # 切り替え不可

        # 重複チェック
        if scene_title in self.scene_transcriptions:
            print(f"⚠️ シーン名 '{scene_title}' は既に存在します。別の名前を入力してください。")
            return False

        # ★前シーンの未処理バッファとキューをバックグラウンドで文字起こしして反映
        prev_scene = self.current_scene
        # Deep copy buffer and queue items for async processing
        buffer_copy = [x.copy() for x in self.partial_audio_buffer] if self.partial_audio_buffer else []
        queue_copy = []
        while not self.audio_queue.empty():
            try:
                item = self.audio_queue.get_nowait()
                queue_copy.append(item.copy() if hasattr(item, "copy") else item)
                self.audio_queue.task_done()
            except queue.Empty:
                break

        # Start background thread for previous scene's processing
        threading.Thread(
            target=self.process_scene_async,
            args=(prev_scene, buffer_copy, queue_copy),
            daemon=True
        ).start()

        # Clear current buffer and queue, switch scene
        self.partial_audio_buffer = []
        self.audio_queue = queue.Queue()
        self.current_scene = scene_title
        self.scene_transcriptions[scene_title] = []
        print(f"前シーン '{prev_scene}' の未処理データをバックグラウンドで処理開始。")
        print(f"\n🎬 シーン切り替え → {scene_title}")
        return True

    def add_transcription(self, text: str):
        """文字起こし結果を現在のシーンに追加"""
        if self.current_scene not in self.scene_transcriptions:
            self.scene_transcriptions[self.current_scene] = []
        self.scene_transcriptions[self.current_scene].append(text)
        print(f"シーン '{self.current_scene}' にテキストを追加: '{text[:50]}...'")
    
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
        # 保存先ディレクトリ
        if output_dir is None:
            output_dir = os.path.join("log", "output")
        os.makedirs(output_dir, exist_ok=True)
        self.scenes = {}

        for scene, texts in self.scene_transcriptions.items():
            # 空のシーン（テキストが空または空リスト）をスキップ
            clean_texts = [t for t in texts if t.strip()]  # 空文字を除外
            if not clean_texts:
                print(f"シーン '{scene}' は空なのでスキップします。")
                continue
            safe_name = scene.replace("/", "_").replace("\\", "_")
            file_path = os.path.join(output_dir, f"{safe_name}.txt")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("\n".join(clean_texts))
            print(f"💾 保存完了: {file_path}")
            # scenesに追加
            self.scenes[scene] = "\n".join(clean_texts)

    @property
    def transcription(self):
        return "\n".join(self.text_results)
    
    def get_initial_scene_name(self, parent_window=None):
        """最初のシーン名を入力するダイアログを表示"""
        # ダイアログウィンドウを作成
        dialog = tk.Toplevel(parent_window) if parent_window else tk.Tk()
        dialog.title("シーン名を入力")
        dialog.geometry("400x150")
        dialog.resizable(False, False)
        
        # メインウィンドウを一時的に無効化
        if parent_window:
            dialog.transient(parent_window)
            dialog.grab_set()
            # ダイアログを中央に配置
            dialog.geometry("+%d+%d" % (parent_window.winfo_rootx() + 50, parent_window.winfo_rooty() + 50))
        
        # ラベル
        label = tk.Label(dialog, text="最初のシーン名を入力してください:", font=("Arial", 12))
        label.pack(pady=20)
        
        # 入力フィールド
        entry = tk.Entry(dialog, width=30, font=("Arial", 11))
        entry.pack(pady=10)
        entry.focus()  # フォーカスを設定
        
        # ボタンフレーム
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
            # デフォルトシーンを使用
            result["scene_name"] = "default"
            self.switch_scene("default")
            dialog.destroy()
        
        # OKボタン
        ok_button = tk.Button(button_frame, text="OK", command=on_ok, width=10)
        ok_button.pack(side=tk.LEFT, padx=5)
        
        # キャンセルボタン
        cancel_button = tk.Button(button_frame, text="デフォルト", command=on_cancel, width=10)
        cancel_button.pack(side=tk.LEFT, padx=5)
        
        # EnterキーでOK
        entry.bind('<Return>', lambda e: on_ok())
        
        # Escapeキーでキャンセル
        dialog.bind('<Escape>', lambda e: on_cancel())
        
        # ダイアログが閉じられるまで待機
        if parent_window:
            dialog.wait_window()
        else:
            dialog.mainloop()
        
        return result["scene_name"]
    
    def save_combined_scenario(self, scenario_title, output_dir=None):
        """
        全シーンをまとめて1つのテキストファイルに保存。
        シーンごとにヘッダを付けて連結し、各文ごとに適切な改行を挿入。
        - 結合テキストは log/scenario_log/ フォルダ内に保存
        """
        if not self.scenes:
            print("DEBUG: scenesが空です。save_all_scenes()を先に呼んでください。")
            return None

        # 保存先ディレクトリ
        if output_dir is None:
            output_dir = os.path.join("log", "scenario_log")
        os.makedirs(output_dir, exist_ok=True)
        safe_title = scenario_title.replace("/", "_").replace("\\", "_")
        combined_file_path = os.path.join(output_dir, f"{safe_title}.txt")

        sentence_terminators = ("。", "！", ".", "!", "?")

        with open(combined_file_path, "w", encoding="utf-8") as f:
            for idx, (scene_name, text) in enumerate(self.scenes.items()):
                f.write(f"【{scene_name}】\n")
                # シーンテキストを行ごとに分割し、各文末で改行を挿入
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
                # シーン間は3つの改行で区切る
                f.write("\n\n\n")

        print(f"全シーン結合テキスト保存完了: {combined_file_path}")
        return combined_file_path
    
    def process_partial_buffer_for_scene(self):
        """現在のシーンに対して、未処理バッファとキューのデータを文字起こしして追加"""
        # バッファを処理
        if self.partial_audio_buffer:
            combined_data = np.concatenate(self.partial_audio_buffer, axis=0)
            mono = np.mean(combined_data, axis=1) if combined_data.ndim > 1 else combined_data.flatten()
            if mono.size > 0:
                resampled = librosa.resample(mono, orig_sr=SAMPLE_RATE, target_sr=TARGET_SR)
                resampled = np.clip(resampled * 1.3, -1.0, 1.0)
                try:
                    result = self.model.transcribe(resampled, language="ja")
                    text = result["text"]
                    self.add_transcription(text)
                except Exception as e:
                    text = f"[文字起こしエラー: {str(e)[:50]}...]"
                    self.add_transcription(text)
            self.partial_audio_buffer = []

        # キューを処理
        while not self.audio_queue.empty():
            try:
                data = self.audio_queue.get_nowait()
                mono = np.mean(data, axis=1) if data.ndim > 1 else data.flatten()
                if mono.size > 0:
                    resampled = librosa.resample(mono, orig_sr=SAMPLE_RATE, target_sr=TARGET_SR)
                    resampled = np.clip(resampled * 1.3, -1.0, 1.0)
                    try:
                        result = self.model.transcribe(resampled, language="ja")
                        text = result["text"]
                        self.add_transcription(text)
                    except Exception as e:
                        text = f"[文字起こしエラー: {str(e)[:50]}...]"
                        self.add_transcription(text)
                self.audio_queue.task_done()
            except queue.Empty:
                break
    def process_scene_async(self, scene_name, buffer_data, queue_data):
        """バックグラウンドでシーンのバッファとキューを文字起こしして追加"""
        # バッファデータ処理
        if buffer_data:
            try:
                combined_data = np.concatenate(buffer_data, axis=0)
                mono = np.mean(combined_data, axis=1) if combined_data.ndim > 1 else combined_data.flatten()
                if mono.size > 0:
                    resampled = librosa.resample(mono, orig_sr=SAMPLE_RATE, target_sr=TARGET_SR)
                    resampled = np.clip(resampled * 1.3, -1.0, 1.0)
                    try:
                        result = self.model.transcribe(resampled, language="ja")
                        text = result["text"]
                        if scene_name not in self.scene_transcriptions:
                            self.scene_transcriptions[scene_name] = []
                        self.scene_transcriptions[scene_name].append(text)
                    except Exception as e:
                        text = f"[文字起こしエラー: {str(e)[:50]}...]"
                        if scene_name not in self.scene_transcriptions:
                            self.scene_transcriptions[scene_name] = []
                        self.scene_transcriptions[scene_name].append(text)
            except Exception as e:
                print(f"[バックグラウンドバッファ処理エラー: {e}]")

        # キューデータ処理
        for data in queue_data:
            try:
                mono = np.mean(data, axis=1) if data.ndim > 1 else data.flatten()
                if mono.size > 0:
                    resampled = librosa.resample(mono, orig_sr=SAMPLE_RATE, target_sr=TARGET_SR)
                    resampled = np.clip(resampled * 1.3, -1.0, 1.0)
                    try:
                        result = self.model.transcribe(resampled, language="ja")
                        text = result["text"]
                        if scene_name not in self.scene_transcriptions:
                            self.scene_transcriptions[scene_name] = []
                        self.scene_transcriptions[scene_name].append(text)
                    except Exception as e:
                        text = f"[文字起こしエラー: {str(e)[:50]}...]"
                        if scene_name not in self.scene_transcriptions:
                            self.scene_transcriptions[scene_name] = []
                        self.scene_transcriptions[scene_name].append(text)
            except Exception as e:
                print(f"[バックグラウンドキュー処理エラー: {e}]")
        print(f"シーン '{scene_name}' のバックグラウンド処理が完了しました。")