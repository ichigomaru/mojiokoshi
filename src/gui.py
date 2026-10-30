import datetime
import os
import sys
import threading
import time
import tkinter as tk

import customtkinter as ctk

from mojiokoshi import MojiOkoshi, LANGUAGE
from scene_clock import SceneClock
import fusetter
import session_log

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


_insert_newlines_ja = session_log.insert_newlines_ja

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
        # "idle"(待機) / "recording"(録音中) / "paused"(一時停止中) / "stopping"(停止処理中) / "posting"(投稿中)
        self.state = "idle"
        self.scene_clock = SceneClock()
        self.total_offset = 0.0  # 全体タイマーは録音ごとに0から (それまでのシーンの時間を引く)
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

        self._run_modal(dialog, entry)
        return result["action"], result["text"]

    def _run_modal(self, dialog, focus_widget):
        """
        ダイアログを前面に出して閉じるまで待つ。
        メイン画面は -topmost なので、開いている間だけ外してダイアログを -topmost にする
        (macOS では transient を付けると -topmost が効かないので付けない)。
        """
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
        focus_widget.focus_set()
        dialog.wait_window()
        try:
            self.root.attributes("-topmost", parent_was_topmost)
        except tk.TclError:
            pass

    def show_message(self, title, message, buttons=(("OK", "ok"),)):
        """
        メッセージとボタンのダイアログを出し、押されたボタンの値を返す (× で閉じたら None)。
        macOS 26 では tkinter.messagebox (標準ダイアログ) を閉じたあとにアプリが落ちるので、こちらを使う。
        buttons: ((表示名, 値), ...)。最後のボタンがオレンジの主ボタンになる。
        """
        dialog = ctk.CTkToplevel(self.root, fg_color=BODY)
        dialog.title(title)
        dialog.resizable(False, False)
        dialog.grid_columnconfigure(tuple(range(len(buttons))), weight=1, uniform="btn")
        result = {"value": None}

        ctk.CTkLabel(dialog, text=message, font=self.font_body, text_color=INK, justify="left",
                     anchor="w", wraplength=360).grid(
            row=0, column=0, columnspan=len(buttons), sticky="w", padx=18, pady=(18, 14))

        def choose(value):
            result["value"] = value
            dialog.destroy()

        main_button = None
        for i, (label, value) in enumerate(buttons):
            primary = i == len(buttons) - 1
            b = self._key_button(dialog, label, lambda v=value: choose(v), primary=primary, width=110)
            b.grid(row=1, column=i, sticky="ew", padx=(18 if i == 0 else 5, 18 if primary else 5), pady=(0, 16))
            if primary:
                main_button = b
        dialog.bind("<Return>", lambda e: choose(buttons[-1][1]))
        dialog.bind("<Escape>", lambda e: choose(buttons[0][1] if len(buttons) > 1 else buttons[-1][1]))

        self._run_modal(dialog, main_button)
        return result["value"]

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
        if not scene_title or self.state in ("paused", "stopping", "posting"):
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
        self.scene_clock.add(scene_name, time.time(), self.state == "recording")

        for no_label, name_label, time_label in self.history_rows:
            no_label.configure(text_color=MUTED)
            name_label.configure(text_color=MUTED)
            time_label.configure(text_color=MUTED)

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
        # そのシーンの録音時間 (今のシーンは update_scene_timers で毎回更新)
        time_label = ctk.CTkLabel(row_frame, text="00:00", font=self.font_mono, text_color=ORANGE, height=22)
        time_label.grid(row=0, column=2, padx=(8, 10))
        self.history_rows.append((no_label, name_label, time_label))

        # 最新の行が見えるようにスクロール
        self.root.after(50, lambda: self.history_frame._parent_canvas.yview_moveto(1.0))
        self.current_scene_label.configure(text=scene_name)
        self.update_scene_timers()

    def update_scene_timers(self):
        """表示パネルのシーン番号の横と、履歴の各行に、シーンの録音時間を出す"""
        if not self.history_rows:
            return
        now = time.time()
        for i, (_, _, time_label) in enumerate(self.history_rows):
            time_label.configure(text=_format_elapsed(self.scene_clock.elapsed(i, now)))
        current = _format_elapsed(self.scene_clock.elapsed(len(self.history_rows) - 1, now))
        self.scene_no_label.configure(text=f"SCENE {len(self.history_rows):02d} · {current}")

    def switch_scene(self):
        if self.state in ("paused", "stopping", "posting"):
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
            self.show_message("エラー", f"録音を開始できませんでした: {e}")
            return
        if not self.history_rows:  # 最初のシーン名を入れずに始めたときも、時間を数えられるようにする
            self.add_scene_to_history(self.mojiokoshi.current_scene)
        self.recording_date = datetime.date.today().isoformat()
        self.state = "recording"
        now = time.time()
        self.total_offset = self.scene_clock.total(now)
        self.scene_clock.start(now)
        self.record_button.configure(text="■  STOP", fg_color=INK, hover_color=INK_HOVER)
        self._set_status("")
        self.update_record_status()

    def stop_recording(self):
        """STOP: 録音だけ止めて (文字起こしは裏で続く)、タイトル・投稿の有無を聞く。再開もできる"""
        if self.state != "recording":
            return
        try:
            self.mojiokoshi.pause()
        except Exception as e:
            self.show_message("エラー", f"録音を止められませんでした: {e}")
            return
        self.scene_clock.pause(time.time())
        self.state = "paused"
        self.record_button.configure(state="disabled", text="❚❚", fg_color=TRACK)
        self.update_switch_scene_button_state()
        self._set_status("一時停止中 (文字起こしは続いています)", MUTED)
        self.update_record_status()

        while True:
            action, title, post = self.ask_finish()
            if action == "finish":
                self.finish_recording(title, post)
                return
            # 再開 (× や Esc で閉じたときも再開する)
            try:
                self.mojiokoshi.resume()
            except Exception as e:
                self.show_message("エラー", f"録音を再開できませんでした: {e}")
                continue
            self.state = "recording"
            self.scene_clock.start(time.time())
            self.record_button.configure(state="normal", text="■  STOP", fg_color=INK, hover_color=INK_HOVER)
            self._set_status("")
            self.update_switch_scene_button_state()
            self.update_record_status()
            return

    def ask_finish(self):
        """
        一時停止中の画面。タイトル入力・ふせったー投稿のチェック・[再開] [保存して終了]。
        ("finish", タイトル, 投稿するか) / ("resume", None, False) を返す。× や Esc は再開。
        """
        dialog = ctk.CTkToplevel(self.root, fg_color=BODY)
        dialog.title("一時停止中")
        dialog.resizable(False, False)
        dialog.grid_columnconfigure((0, 1), weight=1, uniform="btn")
        result = {"action": "resume", "title": None, "post": False}

        ctk.CTkLabel(dialog, text="録音を一時停止しました", font=self.font_scene, text_color=INK).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=18, pady=(16, 2))
        ctk.CTkLabel(dialog, text="文字起こしは裏で続いています。終了するならタイトルを入力してください。",
                     font=self.font_tag, text_color=MUTED).grid(
            row=1, column=0, columnspan=2, sticky="w", padx=18, pady=(0, 8))
        entry = ctk.CTkEntry(dialog, placeholder_text="例: 2026-10-05 打ち合わせ", width=320, height=38,
                             corner_radius=6, font=self.font_body, fg_color=KEY, border_color=KEY_BORDER,
                             border_width=1, text_color=INK, placeholder_text_color="#A09C93")
        entry.grid(row=2, column=0, columnspan=2, sticky="ew", padx=18)
        previous = session_log.load_state()
        entry.configure(placeholder_text="シナリオ名")
        entry.insert(0, previous.get("last_scenario", ""))
        day_entry = ctk.CTkEntry(dialog, placeholder_text="何日目 (数字のみ)", height=38,
                                 font=self.font_body, fg_color=KEY, border_color=KEY_BORDER,
                                 text_color=INK)
        entry.grid_configure(columnspan=1, padx=(18, 5))
        day_entry.grid(row=2, column=1, sticky="ew", padx=(5, 18))
        day_entry.configure(validate="key", validatecommand=(dialog.register(
            lambda value: not value or (value.isascii() and value.isdigit())), "%P"))
        day_entry.insert(0, previous.get("last_day", ""))
        post_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(dialog, text="ふせったーに投稿する", variable=post_var, font=self.font_body,
                        text_color=INK, fg_color=ORANGE, hover_color=ORANGE_HOVER, border_color=KEY_BORDER,
                        corner_radius=4).grid(row=3, column=0, columnspan=2, sticky="w", padx=18, pady=(10, 0))
        warning = ctk.CTkLabel(dialog, text="", font=self.font_tag, text_color=ORANGE, height=16)
        warning.grid(row=4, column=0, columnspan=2, sticky="w", padx=18)

        def on_finish():
            title = entry.get().strip()
            if not title:
                warning.configure(text="タイトルを入力してください")
                return
            self.scenario = title
            self.day = day_entry.get().strip()
            try:
                session_log.save_state(self.scenario, self.day)
            except OSError as e:
                self.show_message("設定", f"前回の入力を保存できませんでした: {e}")
            result.update(action="finish", title=session_log.base_title(title, self.day), post=post_var.get())
            dialog.destroy()

        self._key_button(dialog, "▶  再開", dialog.destroy).grid(
            row=5, column=0, sticky="ew", padx=(18, 5), pady=(2, 16))
        self._key_button(dialog, "保存して終了", on_finish, primary=True).grid(
            row=5, column=1, sticky="ew", padx=(5, 18), pady=(2, 16))
        entry.bind("<Return>", lambda e: on_finish())
        dialog.bind("<Escape>", lambda e: dialog.destroy())

        self._run_modal(dialog, entry)
        return result["action"], result["title"], result["post"]

    def finish_recording(self, title, post):
        """残りの文字起こしが終わるのを待ってから、保存 (と投稿) して終了する"""
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
                self.show_message("エラー", f"処理中にエラーが発生しました: {stop_result['error']}")
                self.reset_ui()
                return
            if self.mojiokoshi.last_stop_skipped:
                self._set_status("文字起こしはスキップしました", ORANGE)
                reason = ("モデルを読み込めなかったため" if self.mojiokoshi.model_error
                          else "モデルの読み込みが終わる前に停止したため")
                self.show_message(
                    "文字起こしなし",
                    f"{reason}、文字起こしはしませんでした。\n"
                    + ("録音は " + "、".join(self.mojiokoshi.wav_paths) + " に保存されています。"
                       if self.mojiokoshi.wav_paths else "録音(WAV)も保存できませんでした。")
                )
                self.reset_ui()
                return
            self._set_status("✓ 文字起こし完了", GREEN)
            self.save_scenario(title, post)

        self.root.after(200, check_done)

    def save_scenario(self, final_title, post):
        """全シーンをまとめてタイトルのファイルに保存し、投稿するならふせったーへ、しないなら終了する"""
        try:
            scenes = getattr(self.mojiokoshi, "scenes", {})
            formatter = (_insert_newlines_ja if LANGUAGE == "ja" else
                         _insert_newlines_en if LANGUAGE == "en" else lambda text: text)
            formatted_text = session_log.scene_text(scenes, formatter=formatter)

            directory = session_log.absolute_path("log/scenario_log")
            os.makedirs(directory, exist_ok=True)
            filename = os.path.join(directory, f"{final_title}.txt")
            with open(filename, "w", encoding="utf-8") as f:
                f.write(formatted_text)
            print(f"シナリオファイルを {filename} に保存しました。")
        except Exception as e:
            print(f"DEBUG: save_scenarioでエラー: {e}")
            self.show_message("エラー", f"保存中にエラーが発生しました: {e}")
            self.reset_ui()
            return

        if post:
            choices = self.ask_secret_scenes(scenes)
            if choices is None:
                self.quit_app()
                return
            try:
                segments = session_log.build_segments(final_title, session_log.group_segments(
                    scenes, choices, getattr(self.mojiokoshi, "scene_laugh_before", {})), formatter)
            except ValueError as e:
                self.show_message("投稿", str(e))
                self.reset_ui()
                return
            self.start_fusetter_post(final_title, formatted_text, segments)
        else:
            self.quit_app()

    def ask_secret_scenes(self, scenes):
        """文字起こしが終わってから、録音順に秘匿の指定を聞く。"""
        dialog = ctk.CTkToplevel(self.root, fg_color=BODY)
        dialog.title("投稿するシーン")
        dialog.grid_columnconfigure((0, 1), weight=1)
        frame = ctk.CTkScrollableFrame(dialog, width=540, height=300, fg_color=KEY)
        frame.grid(row=0, column=0, columnspan=2, padx=18, pady=16)
        rows = {}
        result = {"choices": None}
        for i, name in enumerate(scenes):
            secret, who = session_log.secret_prefill(name)
            ctk.CTkLabel(frame, text=name, font=self.font_body, text_color=INK).grid(
                row=i, column=0, sticky="w", padx=5, pady=5)
            variable = tk.BooleanVar(value=secret)
            ctk.CTkCheckBox(frame, text="秘匿", variable=variable, width=70,
                            font=self.font_body, fg_color=ORANGE).grid(row=i, column=1, padx=5)
            entry = ctk.CTkEntry(frame, placeholder_text="誰のシーン", font=self.font_body)
            entry.grid(row=i, column=2, padx=5)
            entry.insert(0, who)
            rows[name] = variable, entry

        def confirm():
            result["choices"] = {name: (v.get(), e.get()) for name, (v, e) in rows.items()}
            dialog.destroy()

        self._key_button(dialog, "キャンセル", dialog.destroy).grid(row=1, column=0, padx=18, pady=(0, 16))
        button = self._key_button(dialog, "投稿する", confirm, primary=True)
        button.grid(row=1, column=1, padx=18, pady=(0, 16))
        dialog.bind("<Escape>", lambda e: dialog.destroy())
        self._run_modal(dialog, button)
        return result["choices"]

    def quit_app(self):
        print("アプリを終了します。")
        self.root.quit()  # mainloopを終了
        self.root.destroy()  # ウィンドウを破棄
        sys.exit(0)  # プロセスを終了

    # ------------------------------------------------------------------
    # ふせったーへの投稿
    # ------------------------------------------------------------------
    def start_fusetter_post(self, title, log_text, segments):
        """
        ふせったーに投稿し、Discord に URL を送ってから終了する。
        失敗したらやり直せる (投稿済みの分は飛ばす)。
        """
        self.state = "posting"
        self.record_button.configure(state="disabled", text="…", fg_color=TRACK)
        self.update_switch_scene_button_state()
        self.update_record_status()
        logs = session_log.SegmentLogs(self.scenario, self.day, self.recording_date, segments)
        pairs = [pair for segment in segments for pair in segment['posts']]
        posted = []          # 投稿できた (タイトル, URL)。やり直しても消さない
        progress = {"status": "", "done": False, "error": None, "discord_done": False,
                    "discord_sent": 0, "no_webhook": False}

        def on_posted(index, title, url):
            posted.append((title, url))
            logs.on_posted(index, title, url)

        def report_log_errors():
            if logs.errors:
                self.show_message("発言まとめ", "\n".join(logs.errors))

        def run():
            progress.update(done=False, error=None)
            try:
                fusetter.post_segments(
                    pairs, start=len(posted),
                    on_status=lambda text: progress.update(status=text), on_posted=on_posted)
                if not progress["discord_done"]:
                    webhook = fusetter.load_webhook_url()
                    if webhook:
                        progress["status"] = "Discord に送信中…"
                        # やり直しのときは送信済みのメッセージを飛ばす
                        fusetter.send_discord(webhook, posted, skip=progress["discord_sent"],
                                              extra_lines=logs.discord_lines,
                                              on_sent=lambda n: progress.update(discord_sent=n))
                    else:
                        print("config.local.json に Discord の Webhook URL が無いので、Discord への送信はスキップします。")
                        progress["no_webhook"] = True
                    progress["discord_done"] = True
            except Exception as e:
                print(f"ふせったー投稿エラー: {e}")
                progress["error"] = str(e)
                progress["unconfirmed"] = getattr(e, "unconfirmed", False)
                progress["unconfirmed_title"] = getattr(e, "title", "")
            progress["done"] = True

        def start_thread():
            threading.Thread(target=run, daemon=True).start()
            self.root.after(300, poll)

        def poll():
            try:
                if progress["status"]:
                    self._set_status(progress["status"])
            except Exception as e:  # 表示の更新に失敗しても見張りは止めない
                print(f"DEBUG: 投稿状況の表示エラー: {e}")
            if not progress["done"]:
                self.root.after(300, poll)
                return
            if progress["error"]:
                done_text = f"\n\n投稿済み: {', '.join(t for t, _ in posted)}" if posted else ""
                if progress.get("unconfirmed"):
                    # 投稿ボタンは押したが完了を確認できなかった。二重投稿を防ぐため人に確かめてもらう
                    message = (f"「{progress['unconfirmed_title']}」を投稿できたか確認できませんでした。{done_text}\n\n"
                               "ふせったーのマイページを確認してください。\n"
                               "投稿されていなければ「再試行」、投稿されていれば「キャンセル」で終了します(ログは保存済みです)。")
                else:
                    message = (f"投稿できませんでした。\n{progress['error']}{done_text}\n\n"
                               "「再試行」で続きから投稿します。「キャンセル」で終了します(ログは保存済みです)。")
                retry = self.show_message("ふせったー", message,
                                          buttons=(("キャンセル", "cancel"), ("再試行", "retry"))) == "retry"
                if retry:
                    start_thread()
                else:
                    report_log_errors()
                    self.quit_app()
                return
            for t, url in posted:
                print(f"ふせったーに投稿しました: {t} {url}")
            if progress["no_webhook"]:
                self.show_message("Discord", "ふせったーには投稿しました。\n"
                                  "Discord の設定 (config.local.json の discord_webhook_url) が見つからないので、"
                                  "通知は送りませんでした。")
            report_log_errors()
            if logs.saved:
                action = self.show_message("発言まとめ", "発言まとめメーカーも使いますか?",
                                           buttons=(("あとで", "later"), ("使う", "use")))
                if action == "use":
                    try:
                        session_log.launch_yokoku()
                    except OSError as e:
                        print(f"予告を起動できませんでした: {e}")
                    try:
                        session_log.launch_maker(logs.preferred_path())
                    except OSError as e:
                        self.show_message("発言まとめ", f"起動できませんでした: {e}")
            self.quit_app()

        start_thread()

    def reset_ui(self):
        """UIをリセット"""
        try:
            if self.record_button.winfo_exists():
                self.record_button.configure(state="normal", text="●  REC",
                                             fg_color=ORANGE, hover_color=ORANGE_HOVER)
        except tk.TclError:
            return

        self.state = "idle"
        self.update_record_status()
        self.update_switch_scene_button_state()

    # ------------------------------------------------------------------
    # 定期更新
    # ------------------------------------------------------------------
    def update_record_status(self):
        if self.state == "recording":
            self._blink = not self._blink
            self.record_status_label.configure(text="● REC" if self._blink else "  REC", text_color=ORANGE)
            self.timer_label.configure(text=_format_elapsed(self.scene_clock.total(time.time()) - self.total_offset),
                                       text_color=ORANGE)
        elif self.state == "paused":
            self.record_status_label.configure(text="❚❚ PAUSED", text_color=DISPLAY_TEXT)
            self.timer_label.configure(text_color=DISPLAY_MUTED)
        elif self.state == "stopping":
            self.record_status_label.configure(text="PROCESSING", text_color=DISPLAY_TEXT)
        elif self.state == "posting":
            self.record_status_label.configure(text="POSTING", text_color=DISPLAY_TEXT)
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
                self.update_scene_timers()
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
                self.show_message("エラー", f"Whisperモデルを読み込めませんでした: {m.model_error}\n録音(WAV)は保存できますが、文字起こしはできません。")

    def run(self):
        self.root.mainloop()

# If this file is run directly, launch the GUI
if __name__ == "__main__":
    gui = MojiOkoshiGUI()
    gui.run()
