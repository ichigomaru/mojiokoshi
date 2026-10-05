import datetime
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk

from mojiokoshi import MojiOkoshi, LANGUAGE

# --- 見た目の設定 (色は (ライト, ダーク) の組) ---
ctk.set_appearance_mode("system")  # Mac のライト/ダーク設定に合わせる
ctk.set_default_color_theme("blue")

RED = ("#E5484D", "#E5484D")
RED_HOVER = ("#C93C41", "#C93C41")
NEUTRAL = ("gray80", "gray30")
NEUTRAL_HOVER = ("gray72", "gray36")
NEUTRAL_TEXT = ("gray10", "gray92")
MUTED = ("gray40", "gray62")
GREEN = ("#2E7D32", "#5BD16B")
ORANGE = ("#B26A00", "#F0A040")
SEPARATOR = ("gray82", "gray28")
HISTORY_BG = ("gray97", "gray17")
HIGHLIGHT = ("#E8F0FE", "#1E3A5F")


def _insert_newlines_ja(text):
    """(from kaigyou.py) 文末記号の後に改行を入れる"""
    sentence_terminators = ("。", "！", "？", ".", "!", "?")
    new_text = ""
    for char in text:
        new_text += char
        if char in sentence_terminators:
            new_text += "\n"
    # 特定の記号を削除
    new_text = new_text.replace("[", "").replace("]", "").replace(",", "").replace(".", "").replace("'", "")
    return new_text

def _insert_newlines_en(text):
    """(from kaigyou_en.py) 文末記号の後に改行を入れる"""
    sentence_terminators = ("。", "！", "？", ".", "!", "?")
    new_text = ""
    for char in text:
        new_text += char
        if char in sentence_terminators:
            new_text += "\n"
    # 特定の記号を削除
    new_text = new_text.replace("[", "").replace("]", "")
    return new_text


def _format_elapsed(seconds):
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


class MojiOkoshiGUI:
    def __init__(self):
        self.root = ctk.CTk()
        self.root.title("MojiOkoshi")
        self.root.attributes("-topmost", True)
        self.root.geometry("520x360")
        self.root.minsize(460, 330)

        self.mojiokoshi = MojiOkoshi()
        # "idle"(待機) / "recording"(録音中) / "stopping"(停止処理中)
        self.state = "idle"
        self.record_started_at = None
        self._blink = False
        self._model_error_shown = False
        self.scene_history = []

        self.font_title = ctk.CTkFont(size=15, weight="bold")
        self.font_body = ctk.CTkFont(size=13)
        self.font_small = ctk.CTkFont(size=12)
        self.font_section = ctk.CTkFont(size=12, weight="bold")
        self.font_mono = ctk.CTkFont(family="Menlo", size=12)

        self.root.grid_columnconfigure(0, weight=1)
        self._build_layout()

        # Polling for progress updates
        self.update_progress()

        # 最初のシーン名を入力
        initial_scene = self.ask_initial_scene_name()
        if initial_scene:
            self.add_scene_to_history(initial_scene)
        self.update_switch_scene_button_state()

    # ------------------------------------------------------------------
    # 画面の組み立て
    # ------------------------------------------------------------------
    def _separator(self, row):
        ctk.CTkFrame(self.body, height=1, fg_color=SEPARATOR).grid(
            row=row, column=0, columnspan=2, sticky="ew", pady=8)

    def _build_layout(self):
        """カードを使わず、1枚の画面に上から 状態 → シーン → 履歴 の順に並べる"""
        self.body = ctk.CTkFrame(self.root, fg_color="transparent")
        self.body.grid(row=0, column=0, sticky="nsew", padx=16, pady=(12, 12))
        self.root.grid_rowconfigure(0, weight=1)
        body = self.body
        body.grid_columnconfigure((0, 1), weight=1, uniform="col")

        # --- 状態 ---
        self.record_status_label = ctk.CTkLabel(body, text="●  待機中", font=self.font_title, text_color=MUTED, height=24)
        self.record_status_label.grid(row=0, column=0, sticky="w")
        # Whisper モデルの読み込み状況 (読み込み中でも録音はできる)
        self.model_status_label = ctk.CTkLabel(body, text="◌ モデル読み込み中…", font=self.font_small, text_color=ORANGE, height=24)
        self.model_status_label.grid(row=0, column=1, sticky="e")

        self.start_button = ctk.CTkButton(
            body, text="●  録音開始", height=36, corner_radius=8, font=self.font_title,
            fg_color=RED, hover_color=RED_HOVER, command=self.start_recording)
        self.start_button.grid(row=1, column=0, sticky="ew", padx=(0, 5), pady=(6, 8))
        self.stop_button = ctk.CTkButton(
            body, text="■  停止", height=36, corner_radius=8, font=self.font_title,
            fg_color=NEUTRAL, hover_color=NEUTRAL_HOVER, text_color=NEUTRAL_TEXT,
            state="disabled", command=self.stop_recording)
        self.stop_button.grid(row=1, column=1, sticky="ew", padx=(5, 0), pady=(6, 8))

        # 文字起こしの進み具合 (左: 件数と状況メッセージ、右: バー)
        progress_row = ctk.CTkFrame(body, fg_color="transparent")
        progress_row.grid(row=2, column=0, columnspan=2, sticky="ew")
        progress_row.grid_columnconfigure(2, weight=1)
        self.progress_label = ctk.CTkLabel(progress_row, text="文字起こし  0 / 0", font=self.font_small, text_color=MUTED, height=20)
        self.progress_label.grid(row=0, column=0, sticky="w")
        # Transcription status display
        self.transcription_status_label = ctk.CTkLabel(progress_row, text="", font=self.font_small, text_color=MUTED, height=20)
        self.transcription_status_label.grid(row=0, column=1, sticky="w", padx=(10, 10))
        self.progress_bar = ctk.CTkProgressBar(progress_row, height=6, corner_radius=3)
        self.progress_bar.grid(row=0, column=2, sticky="ew")
        self.progress_bar.set(0)

        self._separator(3)

        # --- シーン ---
        self.current_scene_label = ctk.CTkLabel(body, text="シーン:  未設定", font=self.font_title, anchor="w", height=24)
        self.current_scene_label.grid(row=4, column=0, columnspan=2, sticky="w")
        scene_row = ctk.CTkFrame(body, fg_color="transparent")
        scene_row.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        scene_row.grid_columnconfigure(0, weight=1)
        self.scene_title_entry = ctk.CTkEntry(scene_row, placeholder_text="次のシーン名", height=32, font=self.font_body)
        self.scene_title_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.scene_title_entry.bind("<KeyRelease>", self.on_scene_title_change)
        self.scene_title_entry.bind("<Return>", lambda e: self.switch_scene())
        self.switch_scene_button = ctk.CTkButton(
            scene_row, text="切り替え", width=90, height=32, corner_radius=8, command=self.switch_scene)
        self.switch_scene_button.grid(row=0, column=1)
        # 重複などの警告 (普段は空)
        self.scene_warning_label = ctk.CTkLabel(body, text="", font=self.font_small, text_color=RED, height=16)
        self.scene_warning_label.grid(row=6, column=0, columnspan=2, sticky="w")

        # --- 履歴 ---
        ctk.CTkLabel(body, text="履歴", font=self.font_section, text_color=MUTED, height=18).grid(
            row=7, column=0, sticky="w")
        body.grid_rowconfigure(8, weight=1)
        self.history_frame = ctk.CTkScrollableFrame(body, height=84, corner_radius=8, fg_color=HISTORY_BG)
        self.history_frame.grid(row=8, column=0, columnspan=2, sticky="nsew", pady=(2, 0))
        self.history_frame.grid_columnconfigure(0, weight=1)
        self.history_rows = []

    # ------------------------------------------------------------------
    # 入力ダイアログ (最初のシーン名・シナリオタイトル共通)
    # ------------------------------------------------------------------
    def ask_text(self, title, message, placeholder="", ok_text="OK", secondary_text=None):
        """
        1行入力のダイアログを出し、("ok", テキスト) / ("secondary", None) / ("close", None) を返す。
        メイン画面は -topmost なので、開いている間だけ外してダイアログを -topmost にする
        (macOS では transient を付けると -topmost が効かないので付けない)。
        """
        dialog = ctk.CTkToplevel(self.root)
        dialog.title(title)
        dialog.resizable(False, False)
        dialog.grid_columnconfigure((0, 1), weight=1, uniform="btn")

        result = {"action": "close", "text": None}

        ctk.CTkLabel(dialog, text=message, font=self.font_title).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=20, pady=(18, 8))
        entry = ctk.CTkEntry(dialog, placeholder_text=placeholder, width=320, height=36, font=self.font_body)
        entry.grid(row=1, column=0, columnspan=2, sticky="ew", padx=20)
        warning = ctk.CTkLabel(dialog, text="", font=self.font_small, text_color=RED, height=18)
        warning.grid(row=2, column=0, columnspan=2, sticky="w", padx=20)

        def on_ok():
            text = entry.get().strip()
            if not text:
                warning.configure(text="入力してください。")
                return
            result.update(action="ok", text=text)
            dialog.destroy()

        def on_secondary():
            result["action"] = "secondary"
            dialog.destroy()

        if secondary_text:
            ctk.CTkButton(dialog, text=secondary_text, height=36, corner_radius=8,
                          fg_color=NEUTRAL, hover_color=NEUTRAL_HOVER, text_color=NEUTRAL_TEXT,
                          command=on_secondary).grid(row=3, column=0, sticky="ew", padx=(20, 6), pady=(4, 18))
        ctk.CTkButton(dialog, text=ok_text, height=36, corner_radius=8, command=on_ok).grid(
            row=3, column=1 if secondary_text else 0, columnspan=1 if secondary_text else 2,
            sticky="ew", padx=(6, 20) if secondary_text else 20, pady=(4, 18))

        entry.bind("<Return>", lambda e: on_ok())
        dialog.bind("<Escape>", lambda e: on_secondary() if secondary_text else dialog.destroy())

        self.root.update_idletasks()
        dialog.geometry("+%d+%d" % (self.root.winfo_rootx() + 40, self.root.winfo_rooty() + 60))
        parent_was_topmost = bool(self.root.attributes("-topmost"))
        self.root.attributes("-topmost", False)
        dialog.attributes("-topmost", True)
        dialog.lift()
        try:
            dialog.wait_visibility()
            dialog.grab_set()
        except tk.TclError:
            pass
        dialog.focus_force()
        entry.focus_set()
        dialog.wait_window()
        self.root.attributes("-topmost", parent_was_topmost)
        return result["action"], result["text"]

    def ask_initial_scene_name(self):
        """最初のシーン名を入力 (「デフォルト」なら default)"""
        action, text = self.ask_text("シーン名を入力", "最初のシーン名を入力してください",
                                     placeholder="例: はじめ", secondary_text="デフォルト")
        if action == "ok":
            name = text
        elif action == "secondary":
            name = "default"
        else:
            return None
        self.mojiokoshi.switch_scene(name)
        return name

    # ------------------------------------------------------------------
    # シーン
    # ------------------------------------------------------------------
    def on_scene_title_change(self, event=None):
        """シーン名入力が変更されたときの処理。ボタン状態を更新し、警告を消す。"""
        self.update_switch_scene_button_state()

    def update_switch_scene_button_state(self):
        """
        シーン切り替えボタンの状態を更新。重複シーン名があればボタンを無効化し警告を表示。
        有効なシーン名が入力されたらボタンを有効化し警告を消す。
        """
        scene_title = self.scene_title_entry.get().strip()
        self.scene_warning_label.configure(text="")
        # If empty or stopping, disable button
        if not scene_title or self.state == "stopping":
            self.switch_scene_button.configure(state="disabled")
            return
        if scene_title in self.mojiokoshi.scene_transcriptions:
            self.switch_scene_button.configure(state="disabled")
            self.scene_warning_label.configure(text=f"「{scene_title}」は既に使われています")
            return
        # No duplication: enable button and clear warning
        self.switch_scene_button.configure(state="normal")

    def add_scene_to_history(self, scene_name):
        """シーン履歴に新しいシーンを追加し、現在のシーン表示を更新"""
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.scene_history.append(f"[{timestamp}] {scene_name}")

        for time_label, name_label in self.history_rows:
            name_label.configure(font=self.font_body, text_color=NEUTRAL_TEXT)
            time_label.master.configure(fg_color="transparent")

        row = len(self.history_rows)
        row_frame = ctk.CTkFrame(self.history_frame, corner_radius=6, fg_color=HIGHLIGHT)
        row_frame.grid(row=row, column=0, columnspan=2, sticky="ew", pady=1)
        row_frame.grid_columnconfigure(1, weight=1)
        time_label = ctk.CTkLabel(row_frame, text=timestamp, font=self.font_mono, text_color=MUTED, height=24)
        time_label.grid(row=0, column=0, padx=(8, 10), pady=0)
        name_label = ctk.CTkLabel(row_frame, text=scene_name, font=self.font_title, anchor="w", height=24)
        name_label.grid(row=0, column=1, sticky="w", pady=0)
        self.history_rows.append((time_label, name_label))

        # 最新の行が見えるようにスクロール
        self.root.after(50, lambda: self.history_frame._parent_canvas.yview_moveto(1.0))
        self.current_scene_label.configure(text=f"シーン:  {scene_name}")

    def switch_scene(self):
        if self.state == "stopping":
            self.scene_warning_label.configure(text="停止処理中はシーンを切り替えられません")
            return
        scene_title = self.scene_title_entry.get().strip()
        if not scene_title:
            self.scene_warning_label.configure(text="シーン名を入力してください")
            return

        # MojiOkoshiのswitch_sceneメソッドを呼び出す
        # 戻り値がFalseなら、重複などの理由で失敗したと判断
        if not self.mojiokoshi.switch_scene(scene_title):
            self.update_switch_scene_button_state()
            return

        # シーン切り替えが成功した場合のみ、UIの更新を行う
        self.add_scene_to_history(scene_title)
        self.scene_title_entry.delete(0, tk.END)
        self.root.focus_set()  # プレースホルダーを表示に戻す
        self.update_switch_scene_button_state()

    # ------------------------------------------------------------------
    # 録音
    # ------------------------------------------------------------------
    def start_recording(self):
        if self.state != "idle":
            return
        try:
            # start() はすぐ戻るので、UIスレッドで直接呼んでエラーをその場で表示する
            self.mojiokoshi.start()
        except Exception as e:
            messagebox.showerror("エラー", f"録音を開始できませんでした: {e}")
            return
        self.state = "recording"
        self.record_started_at = time.time()
        self.start_button.configure(state="disabled", text="●  録音中", fg_color=NEUTRAL)
        self.stop_button.configure(state="normal", fg_color=RED, hover_color=RED_HOVER, text_color=("white", "white"))
        self.transcription_status_label.configure(text="")
        self.update_record_status()

    def stop_recording(self):
        if self.state != "recording":
            return
        self.state = "stopping"
        self.stop_button.configure(state="disabled", text="処理中…",
                                   fg_color=NEUTRAL, hover_color=NEUTRAL_HOVER, text_color=NEUTRAL_TEXT)
        self.update_switch_scene_button_state()
        self.transcription_status_label.configure(text="残りの音声を文字起こし中…", text_color=ORANGE)
        self.update_record_status()

        # 録音停止処理を別スレッドで実行（UIをブロックしないため）
        # Tk はメインスレッドからしか触らないので、スレッドは結果を残すだけにする
        stop_result = {}

        def stop_and_save():
            try:
                print("DEBUG: GUI停止処理開始")

                # 録音を停止 (残りの文字起こしが終わるまで戻らない)
                self.mojiokoshi.stop()
                print("DEBUG: mojiokoshi.stop()完了")

                # 保存処理
                self.mojiokoshi.save_all_scenes()
                # After saving, ensure self.mojiokoshi.scenes is populated from scene_transcriptions
                if hasattr(self.mojiokoshi, "scene_transcriptions"):
                    self.mojiokoshi.scenes = dict(self.mojiokoshi.scene_transcriptions)
            except Exception as e:
                print(f"エラーが発生しました: {e}")
                stop_result["error"] = str(e)

        stop_thread = threading.Thread(target=stop_and_save, daemon=True)
        stop_thread.start()

        def check_done():
            if stop_thread.is_alive():
                self.root.after(200, check_done)
                return
            if "error" in stop_result:
                messagebox.showerror("エラー", f"処理中にエラーが発生しました: {stop_result['error']}")
                self.reset_ui()
                return
            if self.mojiokoshi.last_stop_skipped:
                self.transcription_status_label.configure(text="文字起こしはスキップしました", text_color=RED)
                reason = ("モデルを読み込めなかったため" if self.mojiokoshi.model_error
                          else "モデルの読み込みが終わる前に停止したため")
                messagebox.showinfo(
                    "文字起こしなし",
                    f"{reason}、文字起こしはしませんでした。\n"
                    + (f"録音は {self.mojiokoshi.last_wav_path} に保存されています。"
                       if self.mojiokoshi.last_wav_path else "録音(WAV)も保存できませんでした。"),
                    parent=self.root
                )
                self.reset_ui()
                return
            self.transcription_status_label.configure(text="✓ 文字起こし完了", text_color=GREEN)
            # 完了メッセージを表示
            self.show_completion_message()

        self.root.after(200, check_done)

    def show_completion_message(self):
        """完了メッセージを表示してシナリオまとめファイル作成"""
        try:
            # Prompt user for final scenario title
            action, final_title = self.ask_text(
                "シナリオタイトル", "シナリオのタイトルを入力してください",
                placeholder="例: 2026-10-05 打ち合わせ", ok_text="保存", secondary_text="キャンセル")
            if action == "ok" and final_title:
                # Collect all scenes and their text
                combined_text = ""
                scenes = getattr(self.mojiokoshi, "scenes", {})
                if not isinstance(scenes, dict):
                    scenes = {}
                for scene_name, text in scenes.items():
                    combined_text += f"【{scene_name}】\n{text}\n\n"

                # LANGUAGE定数に基づいて、適切な改行処理を適用します
                if LANGUAGE == "ja":
                    print("DEBUG: 日本語の改行フォーマットを適用します。")
                    formatted_text = _insert_newlines_ja(combined_text)
                elif LANGUAGE == "en":
                    print("DEBUG: 英語の改行フォーマットを適用します。")
                    formatted_text = _insert_newlines_en(combined_text)
                else:
                    # 'ja' 'en' 以外の場合は、デフォルトの結合テキストを使用
                    print(f"DEBUG: '{LANGUAGE}' に対応する改行フォーマットがありません。")
                    formatted_text = combined_text

                # Save to file named by final scenario title
                os.makedirs("log/scenario_log", exist_ok=True)
                filename = os.path.join("log", "scenario_log", f"{final_title}.txt")
                with open(filename, "w", encoding="utf-8") as f:
                    f.write(formatted_text)

                result = messagebox.showinfo(
                    "完了",
                    f"文字起こしが完了しました！\nシナリオファイルを保存しました\n\nアプリを終了しますか？",
                    parent=self.root
                )

                if result:
                    self.root.quit()  # mainloopを終了
                    self.root.destroy()  # ウィンドウを破棄
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
        try:
            if self.start_button.winfo_exists():
                self.start_button.configure(state="normal", text="●  録音開始", fg_color=RED)
            if self.stop_button.winfo_exists():
                self.stop_button.configure(state="disabled", text="■  停止",
                                           fg_color=NEUTRAL, hover_color=NEUTRAL_HOVER, text_color=NEUTRAL_TEXT)
        except tk.TclError:
            return

        self.state = "idle"
        self.record_started_at = None
        self.update_record_status()
        self.update_switch_scene_button_state()

    # ------------------------------------------------------------------
    # 定期更新
    # ------------------------------------------------------------------
    def update_record_status(self):
        if self.state == "recording":
            self._blink = not self._blink
            elapsed = _format_elapsed(time.time() - self.record_started_at)
            self.record_status_label.configure(
                text=f"{'●' if self._blink else '○'}  録音中  {elapsed}", text_color=RED)
        elif self.state == "stopping":
            self.record_status_label.configure(text="◌  停止処理中…", text_color=ORANGE)
        else:
            self.record_status_label.configure(text="●  待機中", text_color=MUTED)

    def update_progress(self):
        """進み具合・録音時間・モデル状態を定期的に更新する"""
        try:
            progress = getattr(self.mojiokoshi, "processing_progress", {})
            processed = progress.get("processed_items", 0)
            total = progress.get("total_items", 0)
            self.progress_label.configure(text=f"文字起こし  {processed} / {total}")
            self.progress_bar.set(processed / total if total else 0)
            if self.state == "recording":
                self.update_record_status()
            self.update_model_status()
        except Exception as e:
            print(f"DEBUG: update_progressでエラー: {e}")
        finally:
            self.root.after(500, self.update_progress)

    def update_model_status(self):
        m = self.mojiokoshi
        if not m.model_ready.is_set():
            return
        if m.model is not None:
            self.model_status_label.configure(text="✓ モデル準備完了", text_color=GREEN)
        else:
            self.model_status_label.configure(text="✕ モデル読み込み失敗", text_color=RED)
            if not self._model_error_shown:
                self._model_error_shown = True
                messagebox.showerror("エラー", f"Whisperモデルを読み込めませんでした: {m.model_error}\n録音(WAV)は保存できますが、文字起こしはできません。")

    def run(self):
        self.root.mainloop()

# If this file is run directly, launch the GUI
if __name__ == "__main__":
    gui = MojiOkoshiGUI()
    gui.run()
