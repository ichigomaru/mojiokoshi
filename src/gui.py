import threading
import time
import tkinter as tk
from tkinter import ttk
from mojiokoshi import MojiOkoshi
from tkinter import messagebox
from tkinter import simpledialog

class MojiOkoshiGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("MojiOkoshi")

        self.mojiokoshi = MojiOkoshi()
        self.recording_thread = None
        self.is_recording = False
        self.root.attributes("-topmost", True) 

        # Scene title input
        self.scene_title_label = tk.Label(self.root, text="シーン名:")
        self.scene_title_label.grid(row=0, column=0, padx=5, pady=5, sticky="e")
        self.scene_title_entry = tk.Entry(self.root, width=30)
        self.scene_title_entry.grid(row=0, column=1, padx=5, pady=5)
        self.scene_title_entry.bind("<KeyRelease>", self.on_scene_title_change)

        # Switch scene button
        self.switch_scene_button = tk.Button(self.root, text="シーン切り替え", command=self.switch_scene)
        self.switch_scene_button.grid(row=0, column=2, padx=5, pady=5)

        # Start recording button
        self.start_button = tk.Button(self.root, text="録音開始", command=self.start_recording,
                                        bg="#4CAF50", fg="black",
                                        activebackground="#45a049", activeforeground="black")
        self.start_button.grid(row=1, column=0, padx=5, pady=5)

        # Stop recording and save all scenes button
        self.stop_button = tk.Button(self.root, text="録音停止", command=self.stop_recording,
                                    bg="#f44336", fg="black",
                                    activebackground="#da190b", activeforeground="black",
                                    state="disabled")
        self.stop_button.grid(row=1, column=1, padx=5, pady=5)

        # Progress display
        self.progress_label = tk.Label(self.root, text="Progress: 0/0")
        self.progress_label.grid(row=2, column=0, columnspan=3, padx=5, pady=10)

        # Transcription status display
        self.transcription_status_label = tk.Label(self.root, text="", fg="blue")
        self.transcription_status_label.grid(row=3, column=0, columnspan=3, padx=5, pady=5)

        # Current scene display
        self.current_scene_label = tk.Label(self.root, text="現在のシーン: 未設定", 
                                            font=("Arial", 14), fg="blue")
        self.current_scene_label.grid(row=4, column=0, columnspan=3, padx=5, pady=(10, 5), sticky="w")
        
        # Scene history display
        self.scene_history_label = tk.Label(self.root, text="シーン履歴:", font=("Arial", 14))
        self.scene_history_label.grid(row=5, column=0, columnspan=3, padx=5, pady=(5, 5), sticky="w")
        
        # Scene history listbox with scrollbar
        self.scene_history_frame = tk.Frame(self.root)
        self.scene_history_frame.grid(row=6, column=0, columnspan=3, padx=5, pady=5, sticky="ew")
        
        self.scene_history_listbox = tk.Listbox(self.scene_history_frame, height=6, width=50, 
                                                font=("Arial", 12), selectbackground="#4CAF50", 
                                                selectforeground="white")
        self.scene_history_scrollbar = tk.Scrollbar(self.scene_history_frame, orient="vertical")
        self.scene_history_listbox.config(yscrollcommand=self.scene_history_scrollbar.set)
        self.scene_history_scrollbar.config(command=self.scene_history_listbox.yview)
        
        self.scene_history_listbox.pack(side="left", fill="both", expand=True)
        self.scene_history_scrollbar.pack(side="right", fill="y")
        
        # Initialize scene history
        self.scene_history = []

        # Polling for progress updates
        self.update_progress()
        
        # 最初のシーン名を入力
        # 最初のシーン名を入力（最前面に固定）
        self.root.attributes("-topmost", True)  # 一時的に最前面に
        initial_scene = self.mojiokoshi.get_initial_scene_name(self.root)
        if initial_scene:
            self.scene_title_entry.delete(0, tk.END)
            self.scene_title_entry.insert(0, initial_scene)
            # 最初のシーンを履歴に追加
            self.add_scene_to_history(initial_scene)
            # Update switch scene button state initially
            self.update_switch_scene_button_state()

    def on_scene_title_change(self, event=None):
        """シーン名入力が変更されたときの処理。ボタン状態を更新し、警告を消す。"""
        self.update_switch_scene_button_state()

    def update_switch_scene_button_state(self):
        """
        シーン切り替えボタンの状態を更新。重複シーン名があればボタンを無効化し警告を表示。
        有効なシーン名が入力されたらボタンを有効化し警告を消す。
        """
        scene_title = self.scene_title_entry.get().strip()
        current_scene = None
        current_scene_text = self.current_scene_label.cget("text")
        if current_scene_text.startswith("現在のシーン: "):
            current_scene = current_scene_text.replace("現在のシーン: ", "").strip()
        # Remove any previous warning label if present
        if hasattr(self, "_scene_warning_label") and self._scene_warning_label.winfo_exists():
            self._scene_warning_label.destroy()
        # If empty, disable button and clear warning
        if not scene_title:
            self.switch_scene_button.config(state="disabled")
            return
        # Use MojiOkoshi's check for duplicate
        if hasattr(self.mojiokoshi, "scene_name_exists"):
            is_dup = self.mojiokoshi.scene_name_exists(scene_title, exclude=current_scene)
        else:
            # fallback (legacy) check
            is_dup = hasattr(self.mojiokoshi, "scene_transcriptions") and scene_title in self.mojiokoshi.scene_transcriptions and scene_title != current_scene
        if is_dup:
            self.switch_scene_button.config(state="disabled")
            # Show warning label below the entry
            self._scene_warning_label = tk.Label(self.root, text=f"シーン名「{scene_title}」は既に存在します。", fg="red", font=("Arial", 10, "bold"))
            self._scene_warning_label.grid(row=0, column=1, padx=5, pady=(28,0), sticky="sw")
            return
        # No duplication: enable button and clear warning
        self.switch_scene_button.config(state="normal")

    def add_scene_to_history(self, scene_name):
        """シーン履歴に新しいシーンを追加（既存なら無視）"""
        # Check if scene_name already exists in the history listbox (ignore timestamp)
        for idx in range(self.scene_history_listbox.size()):
            entry = self.scene_history_listbox.get(idx)
            # Extract scene name from entry: format "[HH:MM:SS] scene_name"
            if "] " in entry:
                entry_scene = entry.split("] ", 1)[1]
            else:
                entry_scene = entry
            if entry_scene == scene_name:
                # Already exists, do not add
                print(f"DEBUG: シーン履歴に既に存在: {scene_name}")
                # 現在のシーン表示のみ更新
                self.current_scene_label.config(text=f"現在のシーン: {scene_name}", fg="green")
                return
        import datetime
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        history_entry = f"[{timestamp}] {scene_name}"
        self.scene_history.append(history_entry)
        self.scene_history_listbox.insert(tk.END, history_entry)
        # 最新の項目を選択して表示
        self.scene_history_listbox.selection_clear(0, tk.END)
        self.scene_history_listbox.selection_set(tk.END)
        self.scene_history_listbox.see(tk.END)
        # 現在のシーン表示を更新
        self.current_scene_label.config(text=f"現在のシーン: {scene_name}", fg="green")
        #print(f"DEBUG: シーン履歴に追加: {history_entry}")

    def switch_scene(self):
        scene_title = self.scene_title_entry.get().strip()
        if not scene_title:
            messagebox.showwarning("警告", "シーン名が空です。シーン名を入力してください。")
            return

        # MojiOkoshiのswitch_sceneメソッドを呼び出す
        # 戻り値がFalseなら、重複などの理由で失敗したと判断
        if not self.mojiokoshi.switch_scene(scene_title):
            # ボタンの状態を更新すると、GUI上の重複警告が表示されます
            self.update_switch_scene_button_state()
            messagebox.showwarning("警告", f"シーン名「{scene_title}」は既に存在します。別の名前を入力してください。")
            return

        # シーン切り替えが成功した場合のみ、UIの更新を行う
        # 1. シーン履歴に新しいシーンを追加
        self.add_scene_to_history(scene_title)
        # 2. ボタンの状態を更新
        self.update_switch_scene_button_state()

    def start_recording(self):
        if not self.is_recording:
            self.is_recording = True
            # ボタンの見た目を変更（録音中状態）
            self.start_button.config(text="● REC", bg="#E91E63", fg="red", 
                                    activebackground="#cc0000", activeforeground="black")
            self.stop_button.config(state="normal")
            
            # Start the MojiOkoshi recording in a separate thread
            self.recording_thread = threading.Thread(target=self.mojiokoshi.start)
            self.recording_thread.daemon = True
            self.recording_thread.start()

    def stop_recording(self):
        if self.is_recording:
            self.is_recording = False
            self.stop_button.config(state="disabled", text="処理中...")
            
            # 録音停止処理を別スレッドで実行（UIをブロックしないため）
            def stop_and_save():
                try:
                    print("DEBUG: GUI停止処理開始")
                    
                    # 録音を停止
                    self.mojiokoshi.stop()
                    print("DEBUG: mojiokoshi.stop()完了")
                    
                    if self.recording_thread is not None:
                        #print("DEBUG: recording_thread.join()開始")
                        self.recording_thread.join(timeout=5)
                        #print("DEBUG: recording_thread.join()完了")
                    
                    # キューにデータが残っているかチェック
                    queue_size = self.mojiokoshi.audio_queue.qsize()
                    buffer_size = len(self.mojiokoshi.partial_audio_buffer)
                    if queue_size > 0 or buffer_size > 0:
                        # 文字起こし処理の完了を待機
                        self.wait_for_transcription_completion()
                    
                    # 保存処理
                    #print("DEBUG: save_all_scenes()開始")
                    self.mojiokoshi.save_all_scenes()
                    # After saving, ensure self.mojiokoshi.scenes is populated from scene_transcriptions
                    if hasattr(self.mojiokoshi, "scene_transcriptions"):
                        self.mojiokoshi.scenes = dict(self.mojiokoshi.scene_transcriptions)
                    #print("DEBUG: save_all_scenes()完了")
                    
                    # 完了メッセージを表示
                    #print("DEBUG: 完了メッセージ表示開始")
                    self.root.after(0, self.show_completion_message)
                    #print("DEBUG: 完了メッセージ表示完了")
                    
                except Exception as e:
                    print(f"エラーが発生しました: {e}")
                    self.root.after(0, lambda: messagebox.showerror("エラー", f"処理中にエラーが発生しました: {e}"))
                    self.root.after(0, self.reset_ui)
            
            # 停止処理を別スレッドで実行
            stop_thread = threading.Thread(target=stop_and_save, daemon=True)
            stop_thread.start()
    
    def wait_for_transcription_completion(self):
        """文字起こし処理の完了を待機"""
        #print("DEBUG: 文字起こし完了待機開始")
        while (self.mojiokoshi.audio_queue.qsize() > 0 or 
                len(self.mojiokoshi.partial_audio_buffer) > 0 or
                self.mojiokoshi.processing_progress['current_stage'] == 'transcribing'):
            
            # 進行状況を取得
            progress = self.mojiokoshi.get_progress_percentage()
            stage = self.mojiokoshi.processing_progress['current_stage']
            processed = self.mojiokoshi.processing_progress['processed_items']
            total = self.mojiokoshi.processing_progress['total_items']
            
            print(f"DEBUG: 進行状況 - {stage}: {processed}/{total} ({progress}%)")
            
            # UIを更新
            if stage == 'transcribing':
                self.root.after(0, lambda p=progress, s=stage: self.transcription_status_label.config(
                    text=f"文字起こし中... {p}%", fg="orange"))
            elif stage == 'saving':
                self.root.after(0, lambda: self.transcription_status_label.config(
                    text="保存中...", fg="blue"))
            
            time.sleep(0.5)  # 0.5秒待機
        
        # 完了メッセージを表示
        self.root.after(0, lambda: self.transcription_status_label.config(
            text="文字起こし完了！", fg="green"))
        #print("DEBUG: 文字起こし完了")

    def show_completion_message(self):
        """完了メッセージを表示してシナリオまとめファイル作成"""
        #print("DEBUG: show_completion_message開始")
        try:
            # Prompt user for final scenario title
            final_title = tk.simpledialog.askstring(
                "シナリオタイトル",
                "シナリオのタイトルを入力してください:",
                parent=self.root
            )
            if final_title:
                # Collect all scenes and their text
                combined_text = ""
                scenes = getattr(self.mojiokoshi, "scenes", {})
                if not isinstance(scenes, dict):
                    scenes = {}
                for scene_name, text in scenes.items():
                    combined_text += f"【{scene_name}】\n{text}\n\n"
                
                # Save to file named by final scenario title
                import os
                os.makedirs("log/scenario_log", exist_ok=True)
                filename = os.path.join("log", "scenario_log", f"{final_title}.txt")
                with open(filename, "w", encoding="utf-8") as f:
                    f.write(combined_text)
                #print(f"全シーン結合テキスト保存完了: {filename}")
                
                result = messagebox.showinfo(
                    "完了",
                    f"文字起こしが完了しました！\nシナリオファイルを保存しました\n\nアプリを終了しますか？",
                    parent=self.root
                )

                if result:
                    #print("DEBUG: ")
                    self.root.quit()  # mainloopを終了
                    self.root.destroy()  # ウィンドウを破棄
                    import sys
                    sys.exit(0)  # プロセスを終了
                else:
                    print("DEBUG: UIリセット")
                    self.reset_ui()
            else:
                messagebox.showwarning("未入力", "シナリオタイトルが入力されませんでした。UIをリセットします。")
                self.reset_ui()
        except Exception as e:
            print(f"DEBUG: show_completion_messageでエラー: {e}")
            self.reset_ui()
    
    def reset_ui(self):
        """UIをリセット"""
        # 録音開始ボタンを元の状態に戻す
        self.start_button.config(text="録音開始", bg="#4CAF50", fg="black",
                                activebackground="#45a049", activeforeground="black")
        # 録音停止ボタンを元の状態に戻す
        self.stop_button.config(state="normal", text="録音停止", bg="#f44336", fg="black",
                                activebackground="#da190b", activeforeground="black")
        self.is_recording = False

    def update_progress(self):
        """
        Update the Progress label to show (processed / total) items.
        """
        try:
            processed = getattr(self.mojiokoshi, "processing_progress", {}).get("processed_items", 0)
            total = getattr(self.mojiokoshi, "processing_progress", {}).get("total_items", 0)
            self.progress_label.config(text=f"Progress: {processed}/{total}")
        except Exception as e:
            print(f"DEBUG: update_progressでエラー: {e}")
        finally:
            self.root.after(500, self.update_progress)

    def run(self):
        self.root.mainloop()

# If this file is run directly, launch the GUI
if __name__ == "__main__":
    gui = MojiOkoshiGUI()
    gui.run()