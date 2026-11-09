# mojiokoshi の司令塔メモ

このリポジトリで司令塔(設計・分担・確認・取り込み)をするセッションが読むメモ。共通の動き方は `~/.claude/playbooks/command-center.md`、ここはこのリポジトリ固有のこと。
**司令塔が交代するとき・会話を引き継ぐときは、先に「いまの状態」と「記録」を更新する。**

## 何をするアプリか
TRPG のセッションなどを録音しながら Whisper で文字起こしし、シーンごとにログを保存する Mac の GUI アプリ(CustomTkinter)。終わったら次につながる。

1. ふせったー(fusetter.com)に「タイトル_ログ」を投稿(普段の Chrome を AppleScript で裏から操作)
2. Discord(Webhook)に投稿の URL と、下の2つの編集画面の URL を通知
3. **発言まとめメーカー** `~/Developer/hatsugen-maker`: ログから「#発言まとめメーカー」風の画像を作る(ポート 4477)
4. **次回予告** `~/Developer/ccfolia-opening` の予告(`docs/yokoku.md`): 選んだ発言から Angel Beats! 次回予告風の 15 秒動画(ポート 4479)

ログの受け渡し先: `~/Documents/TRPG/発言まとめ`(`$HATSUGEN_HOME`)の `logs/`。

## 主なファイル
- `main.py`: 起動。macOS の日本語入力のログ(TSM/IMK)を stderr から除く
- `src/mojiokoshi.py`: 録音・文字起こし(mlx-whisper)・シーン・停止処理
- `src/gui.py`: 画面(ハードウェア風の D デザイン、ライト固定)・一時停止画面・投稿の流れ
- `src/fusetter.py`: ふせったー投稿と Discord 通知
- `tests/`: `uv run --with pytest pytest -q`

## このリポジトリで守ること
- **tkinter.messagebox(macOS 標準のダイアログ)は使わない。** macOS 26 では閉じた後にアプリが落ちる。`self.show_message` / `ask_text` を使う
- ダイアログはメイン画面が `-topmost` なので、開いている間だけメイン画面の `-topmost` を外す(`_run_modal`)。`transient` は付けない(macOS では -topmost が効かなくなる)
- Tk はメインスレッドからだけ触る。別スレッドは結果を残し、`after` で拾う
- `config.local.json`(Discord の Webhook URL)は git に入れない(.gitignore 済み)。チャットにも貼らない
- 録音デバイス: REC・再開のたびに `src/audio_device.py` がアプリ専用(private)の集約デバイス [マイク, BlackHole] を作り、一時停止・停止で消す。マイクは「UGREEN USB MIC-CM769」があればそれ、なければ「MacBook Airのマイク」。作れないときは従来の集約デバイス `mojiokoshi` に戻る。**最後の 2ch が BlackHole(Mac の音)、残りがマイク**
- ふせったー: Chrome の「表示 → 開発 / 管理 → Apple Events からの JavaScript を許可」が必要。AppleScript の JavaScript はページと別の世界で動くので、ページ側に仕込むときは `<script>` を足して DOM の属性で受け渡す。裏のタブでは innerText が空になるので textContent と `data-dialog-open` で判定する
- 投稿ボタンを押した後のエラーは「投稿できたか不明」として扱い、二重投稿させない
- 画面に出す説明文は必要最低限

## コミット・push
- コミット・push はユーザー本人がその会話で了承したときだけ
- GitHub は `ichigomaru/mojiokoshi`。キーチェーンには別アカウント(ichigomaru115)が残っていて普通の `git push` は 403 になる。`git -c credential.helper= -c 'credential.helper=!gh auth git-credential' push origin main`(gh の active が ichigomaru であることを `gh auth status` で確認)
- このリポジトリの `user.email` は GitHub の非公開用アドレス(大学のアドレスが入ったコミットは push で弾かれる)

## いまの状態(2026-11-02 更新)
- 司令塔: local_37736c5f-e54a-4b10-8cac-53443c2b6934(「司令塔」)
- main: `3bc7cc1`(push 済み)
- 起動: デスクトップの `文字起こし.command`(ダブルクリック。ターミナルで動くので許可はそのまま。リポジトリの外)
- 動いている分担のセッション: なし
- 関連: 発言まとめメーカーは main `1441ba5`(push 済み。GitHub は ichigomaru115/hatsugen-maker、push は gh を ichigomaru115 に切り替えてから、終わったら ichigomaru に戻す)

## 記録
- 10-05: 停止処理・シーン切り替えの修正、UI を CustomTkinter で作り直し、Whisper の裏読み込み、幻の文の除去、ふせったー投稿・Discord 通知、macOS 標準ダイアログの置き換え、マイクのチャンネル自動判定
- 10-30 ごろ: mlx-whisper への切り替え(約5倍速)、繰り返しの暴走対策・一時停止と再開・シーンごとのタイマー
- 10-31 ごろ: hatsugen-link(発言まとめ用のログ保存、Discord に発言まとめ・予告の編集 URL、笑いの検出)を main に取り込み
- 11-02: 司令塔を開始。このメモを作成。一時停止画面のチェックを横並びに(`3bc7cc1`)。発言まとめメーカーの上書き防止・おまかせ50個を取り込み(`1441ba5`)
- 11-09: デスクトップに起動用の `文字起こし.command` を作成。英語の1回分は終わり `LANGUAGE` は `"ja"` に戻した。マイクの自動切り替え(Codex 実装、内蔵マイクで実機確認、USB マイクは未確認)
