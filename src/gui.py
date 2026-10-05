import datetime
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk

from mojiokoshi import MojiOkoshi, LANGUAGE

# --- 見た目の設定 (ハードウェア風。配色が前提なのでライト固定) ---
ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

BODY = "#E7E5DF"          # 本体の色
DISPLAY = "#141414"       # 黒い表示パネル
DISPLAY_TEXT = "#F2F2F2"
DISPLAY_MUTED = "#7A7A7A"
ORANGE = "#FF5A1F"        # アクセント (録音・タイマー)
ORANGE_HOVER = "#E64C14"
INK = "#1A1A1A"           # 黒いボタン・文字
INK_HOVER = "#333333"
KEY = "#F7F6F2"           # 白いキー (入力欄・副ボタン)
KEY_HOVER = "#EDEBE5"
KEY_BORDER = "#CFCCC3"
MUTED = "#6B6862"
TRACK = "#C9C6BD"         # 進捗バーの残り
DISABLED = "#B9B6AE"
GREEN = "#2F8F46"


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
        self.root = ctk.CTk(fg_color=BODY)
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
        self._warning_shown = False
        self.scene_history = []

        self.font_timer = ctk.CTkFont(family="Menlo", size=30)
        self.font_scene = ctk.CTkFont(size=15, weight="bold")
        self.font_key = ctk.CTkFont(size=13, weight="bold")
        self.font_body = ctk.CTkFont(size=13)
        self.font_tag = ctk.CTkFont(family="Menlo", size=10)
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
    def _key_button(self, parent, text, command, primary=False, **kw):
        if primary:
            colors = dict(fg_color=ORANGE, hover_color=ORANGE_HOVER, text_color="white")
        else:
            colors = dict(fg_color=KEY, hover_color=KEY_HOVER, text_color=INK,
                          border_width=1, border_color=KEY_BORDER)
        return ctk.CTkButton(parent, text=text, command=command, corner_radius=6, height=40,
                             font=self.font_key, text_color_disabled=DISABLED, **colors, **kw)

    def _build_layout(self):
        """上から 表示パネル → 操作キー → 進み具合 → ログ の順に並べる"""
        self.body = ctk.CTkFrame(self.root, fg_color="transparent")
        self.body.grid(row=0, column=0, sticky="nsew", padx=14, pady=(12, 12))
        self.root.grid_rowconfigure(0, weight=1)
        body = self.body
        body.grid_columnconfigure(0, weight=1)

        # --- 黒い表示パネル: シーン番号・シーン名 (左) / 状態・タイマー (右) ---
        display = ctk.CTkFrame(body, fg_color=DISPLAY, corner_radius=8)
        display.grid(row=0, column=0, sticky="ew")
        display.grid_columnconfigure(0, weight=1)
        self.scene_no_label = ctk.CTkLabel(display, text="SCENE --", font=self.font_tag,
                                           text_color=DISPLAY_MUTED, height=14)
        self.scene_no_label.grid(row=0, column=0, sticky="w", padx=12, pady=(8, 0))
        self.record_status_label = ctk.CTkLabel(display, text="STANDBY", font=self.font_tag,
                                                text_color=DISPLAY_MUTED, height=14)
        self.record_status_label.grid(row=0, column=1, sticky="e", padx=12, pady=(8, 0))
        self.current_scene_label = ctk.CTkLabel(display, text="未設定", font=self.font_scene,
                                                text_color=DISPLAY_TEXT, anchor="w")
        self.current_scene_label.grid(row=1, column=0, sticky="sw", padx=12, pady=(0, 8))
        self.timer_label = ctk.CTkLabel(display, text="00:00", font=self.font_timer,
                                        text_color=DISPLAY_MUTED, height=34)
        self.timer_label.grid(row=1, column=1, sticky="e", padx=12, pady=(0, 6))

        # --- 操作キー: REC/STOP (1つのボタンで切り替え) / 次のシーン名 / +SCENE ---
        keys = ctk.CTkFrame(body, fg_color="transparent")
        keys.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        keys.grid_columnconfigure(1, weight=1)
        self.record_button = self._key_button(keys, "●  REC", self.toggle_recording, primary=True, width=130)
        self.record_button.grid(row=0, column=0, padx=(0, 8))
        self.scene_title_entry = ctk.CTkEntry(
            keys, placeholder_text="次のシーン名", height=40, corner_radius=6, font=self.font_body,
            fg_color=KEY, border_color=KEY_BORDER, border_width=1, text_color=INK,
            placeholder_text_color="#A09C93")
        self.scene_title_entry.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        self.scene_title_entry.bind("<KeyRelease>", self.on_scene_title_change)
        self.scene_title_entry.bind("<Return>", lambda e: self.switch_scene())
        self.switch_scene_button = self._key_button(keys, "＋ SCENE", self.switch_scene, width=100)
        self.switch_scene_button.grid(row=0, column=2)

        # --- 進み具合 (四角いバー) とモデル状態 ---
        self.progress_bar = ctk.CTkProgressBar(body, height=5, corner_radius=0,
                                               fg_color=TRACK, progress_color=INK)
        self.progress_bar.grid(row=2, column=0, sticky="ew", pady=(12, 2))
        self.progress_bar.set(0)
        info = ctk.CTkFrame(body, fg_color="transparent")
        info.grid(row=3, column=0, sticky="ew")
        info.grid_columnconfigure(1, weight=1)
        self.progress_label = ctk.CTkLabel(info, text="TRANSCRIBE 0/0", font=self.font_tag,
                                           text_color=MUTED, height=16)
        self.progress_label.grid(row=0, column=0, sticky="w")
        # 状況メッセージ・シーン名の警告 (普段は空)
        self.transcription_status_label = ctk.CTkLabel(info, text="", font=self.font_tag,
                                                       text_color=MUTED, height=16)
        self.transcription_status_label.grid(row=0, column=1, sticky="w", padx=10)
        self.scene_warning_label = self.transcription_status_label
        # Whisper モデルの読み込み状況 (読み込み中でも録音はできる)
        self.model_status_label = ctk.CTkLabel(info, text="MODEL ○ LOADING", font=self.font_tag,
                                               text_color=ORANGE, height=16)
        self.model_status_label.grid(row=0, column=2, sticky="e")

        # --- ログ (シーン履歴) ---
        body.grid_rowconfigure(4, weight=1)
        self.history_frame = ctk.CTkScrollableFrame(
            body, height=80, corner_radius=6, fg_color=KEY, border_width=1, border_color=KEY_BORDER,
            scrollbar_button_color=TRACK, scrollbar_button_hover_color=MUTED)
        self.history_frame.grid(row=4, column=0, sticky="nsew", pady=(10, 0))
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
        dialog = ctk.CTkToplevel(self.root, fg_color=BODY)
        dialog.title(title)
        dialog.resizable(False, False)
        dialog.grid_columnconfigure((0, 1), weight=1, uniform="btn")

        result = {"action": "close", "text": None}

        ctk.CTkLabel(dialog, text=message, font=self.font_scene, text_color=INK).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=18, pady=(16, 8))
        entry = ctk.CTkEntry(dialog, placeholder_text=placeholder, width=320, height=38, corner_radius=6,
                             font=self.font_body, fg_color=KEY, border_color=KEY_BORDER, border_width=1,
                             text_color=INK, placeholder_text_color="#A09C93")
        entry.grid(row=1, column=0, columnspan=2, sticky="ew", padx=18)
        warning = ctk.CTkLabel(dialog, text="", font=self.font_tag, text_color=ORANGE, height=16)
        warning.grid(row=2, column=0, columnspan=2, sticky="w", padx=18)

        def on_ok():
            text = entry.get().strip()
            if not text:
                warning.configure(text="入力してください")
                return
            result.update(action="ok", text=text)
            dialog.destroy()

        def on_secondary():
            result["action"] = "secondary"
            dialog.destroy()

        if secondary_text:
            self._key_button(dialog, secondary_text, on_secondary).grid(
                row=3, column=0, sticky="ew", padx=(18, 5), pady=(2, 16))
        self._key_button(dialog, ok_text, on_ok, primary=True).grid(
            row=3, column=1 if secondary_text else 0, columnspan=1 if secondary_text else 2,
            sticky="ew", padx=(5, 18) if secondary_text else 18, pady=(2, 16))

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

    def _set_status(self, text, color=MUTED):
        """状況メッセージを出す (シーン名の警告を上書きする)"""
        self.transcription_status_label.configure(text=text, text_color=color)
        self._warning_shown = False

    def _show_warning(self, text):
        """シーン名の警告を出す (状況メッセージと同じ欄を使うので、警告かどうかを覚えておく)"""
        self.scene_warning_label.configure(text=text, text_color=ORANGE)
        self._warning_shown = True

    def update_switch_scene_button_state(self):
        """
        シーン切り替えボタンの状態を更新。重複シーン名があればボタンを無効化し警告を表示。
        有効なシーン名が入力されたらボタンを有効化し警告を消す。
        """
        scene_title = self.scene_title_entry.get().strip()
        if self._warning_shown:
            self.scene_warning_label.configure(text="")
            self._warning_shown = False
        # If empty or stopping, disable button
        if not scene_title or self.state == "stopping":
            self.switch_scene_button.configure(state="disabled")
            return
        if scene_title in self.mojiokoshi.scene_transcriptions:
            self.switch_scene_button.configure(state="disabled")
            self._show_warning(f"「{scene_title}」は使用済み")
            return
        # No duplication: enable button and clear warning
        self.switch_scene_button.configure(state="normal")

    def add_scene_to_history(self, scene_name):
        """シーン履歴に新しいシーンを追加し、表示パネルを更新"""
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.scene_history.append(f"[{timestamp}] {scene_name}")

        for no_label, name_label in self.history_rows:
            no_label.configure(text_color=MUTED)
            name_label.configure(text_color=MUTED)

        row = len(self.history_rows)
        row_frame = ctk.CTkFrame(self.history_frame, fg_color="transparent")
        row_frame.grid(row=row, column=0, sticky="ew")
        row_frame.grid_columnconfigure(1, weight=1)
        no_label = ctk.CTkLabel(row_frame, text=f"{row + 1:02d}  {timestamp}", font=self.font_mono,
                                text_color=ORANGE, height=22)
        no_label.grid(row=0, column=0, padx=(6, 12))
        name_label = ctk.CTkLabel(row_frame, text=scene_name, font=self.font_body, text_color=INK,
                                  anchor="w", height=22)
        name_label.grid(row=0, column=1, sticky="w")
        self.history_rows.append((no_label, name_label))

        # 最新の行が見えるようにスクロール
        self.root.after(50, lambda: self.history_frame._parent_canvas.yview_moveto(1.0))
        self.scene_no_label.configure(text=f"SCENE {len(self.history_rows):02d}")
        self.current_scene_label.configure(text=scene_name)

    def switch_scene(self):
        if self.state == "stopping":
            self._show_warning("停止処理中は切り替えられません")
            return
        scene_title = self.scene_title_entry.get().strip()
        if not scene_title:
            self._show_warning("シーン名を入力してください")
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
    def toggle_recording(self):
        """REC/STOP ボタン: 待機中なら録音開始、録音中なら停止"""
        if self.state == "idle":
            self.start_recording()
        elif self.state == "recording":
            self.stop_recording()

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
        self.record_button.configure(text="■  STOP", fg_color=INK, hover_color=INK_HOVER)
        self._set_status("")
        self.update_record_status()

    def stop_recording(self):
        if self.state != "recording":
            return
        self.state = "stopping"
        self.record_button.configure(state="disabled", text="…", fg_color=TRACK)
        self.update_switch_scene_button_state()
        self._set_status("残りを文字起こし中…", MUTED)
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
                self._set_status("文字起こしはスキップしました", ORANGE)
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
            self._set_status("✓ 文字起こし完了", GREEN)
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
            if self.record_button.winfo_exists():
                self.record_button.configure(state="normal", text="●  REC",
                                             fg_color=ORANGE, hover_color=ORANGE_HOVER)
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
            self.record_status_label.configure(text="● REC" if self._blink else "  REC", text_color=ORANGE)
            self.timer_label.configure(text=_format_elapsed(time.time() - self.record_started_at),
                                       text_color=ORANGE)
        elif self.state == "stopping":
            self.record_status_label.configure(text="PROCESSING", text_color=DISPLAY_TEXT)
        else:
            self.record_status_label.configure(text="STANDBY", text_color=DISPLAY_MUTED)
            self.timer_label.configure(text_color=DISPLAY_MUTED)

    def update_progress(self):
        """進み具合・録音時間・モデル状態を定期的に更新する"""
        try:
            progress = getattr(self.mojiokoshi, "processing_progress", {})
            processed = progress.get("processed_items", 0)
            total = progress.get("total_items", 0)
            self.progress_label.configure(text=f"TRANSCRIBE {processed}/{total}")
            self.progress_bar.set(processed / total if total else 0)
            # 0 のときも左端に黒い点が出るので、色を残りと同じにして隠す
            self.progress_bar.configure(progress_color=INK if processed else TRACK)
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
            self.model_status_label.configure(text="MODEL ● READY", text_color=GREEN)
        else:
            self.model_status_label.configure(text="MODEL ✕ ERROR", text_color=ORANGE)
            if not self._model_error_shown:
                self._model_error_shown = True
                messagebox.showerror("エラー", f"Whisperモデルを読み込めませんでした: {m.model_error}\n録音(WAV)は保存できますが、文字起こしはできません。")

    def run(self):
        self.root.mainloop()

# If this file is run directly, launch the GUI
if __name__ == "__main__":
    gui = MojiOkoshiGUI()
    gui.run()
