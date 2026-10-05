"""
ふせったー (https://fusetter.com) への投稿と、Discord Webhook への URL 送信。

ふせったーには公開 API が無いので、普段使っている (ログイン済みの) Google Chrome を
AppleScript で操作し、普通の投稿画面に入力して投稿する。
事前に Chrome のメニュー「表示 → 開発 / 管理 → Apple Events からの JavaScript を許可」をオンにしておく。
"""
import base64
import json
import os
import re
import subprocess
import time
import urllib.request

POST_URL = "https://fusetter.com/home"
POSTSCRIPT_LIMIT = 100_000   # 追記 (たくさん書けます) の上限文字数
BODY_LIMIT = 150             # 本文 (伏せ字が使えます) の上限文字数
CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.local.json")
# PyInstaller で作ったアプリではプロジェクト直下が見えないので、ホームの下も探す
HOME_CONFIG_PATH = os.path.expanduser("~/.mojiokoshi/config.local.json")

# 投稿画面の部品 (data-cy はテスト用の属性なので、見た目が変わっても変わりにくい)
BODY_SELECTOR = '[data-cy="post-form-body-input"]'
POSTSCRIPT_SELECTOR = '[data-cy="post-form-postscript-input"]'
SUBMIT_SELECTOR = '[data-cy="post-form-submit-btn"]'
VISIBILITY_LABELS = ("広場", "非公開", "だれでも", "合言葉", "ふせったーのフォロワー", "ふせったーの相互フォロー")


class FusetterError(Exception):
    """unconfirmed=True: 投稿ボタンは押したが、投稿できたか確認できなかった"""

    def __init__(self, message, unconfirmed=False):
        super().__init__(message)
        self.unconfirmed = unconfirmed


# ----------------------------------------------------------------------
# 分割・タイトル
# ----------------------------------------------------------------------
def split_log(text, limit=POSTSCRIPT_LIMIT):
    """limit 文字以内の塊に分ける。なるべく改行の位置で区切る。"""
    chunks = []
    rest = text
    while len(rest) > limit:
        cut = rest.rfind("\n", 0, limit)  # 改行を前の塊に含めても limit 以内になる位置
        if cut <= 0:
            cut = limit  # 1行が長すぎるときは文字数で切る
        else:
            cut += 1     # 改行は前の塊に含める
        chunks.append(rest[:cut])
        rest = rest[cut:]
    if rest or not chunks:
        chunks.append(rest)
    return chunks


def post_titles(title, count):
    """タイトル_ログ, タイトル_ログ2, タイトル_ログ3 ..."""
    return [f"{title}_ログ" if i == 0 else f"{title}_ログ{i + 1}" for i in range(count)]


# ----------------------------------------------------------------------
# Discord
# ----------------------------------------------------------------------
def load_webhook_url(path=None):
    """Webhook URL を返す。path を省略すると、プロジェクト直下 → ~/.mojiokoshi/ の順に探す。"""
    for p in ([path] if path else [CONFIG_PATH, HOME_CONFIG_PATH]):
        try:
            with open(p, encoding="utf-8") as f:
                url = (json.load(f).get("discord_webhook_url") or "").strip()
        except FileNotFoundError:
            continue
        if url:
            return url
    return None


def discord_messages(posts):
    """posts: [(タイトル, URL), ...] を Discord の 1メッセージ2000文字以内に分ける"""
    lines = [f"{t}\n{u or '(URL を取得できませんでした。ふせったーのマイページを確認してください)'}" for t, u in posts]
    messages, current = [], ""
    for line in lines:
        if current and len(current) + len(line) + 2 > 2000:
            messages.append(current)
            current = ""
        current = f"{current}\n\n{line}" if current else line
    if current:
        messages.append(current)
    return messages


def send_discord(webhook_url, posts, skip=0, on_sent=None):
    """
    posts をまとめて Discord に送る。
    skip: 送信済みのメッセージ数 (やり直しで重複させないため)。on_sent(送った数) が1通ごとに呼ばれる。
    """
    messages = discord_messages(posts)
    for n, content in enumerate(messages[skip:], start=skip + 1):
        req = urllib.request.Request(
            webhook_url, data=json.dumps({"content": content}).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "mojiokoshi"}, method="POST")
        with urllib.request.urlopen(req, timeout=15) as res:
            if res.status >= 300:
                raise FusetterError(f"Discord への送信に失敗しました (HTTP {res.status})")
        if on_sent:
            on_sent(n)


# ----------------------------------------------------------------------
# 普段の Chrome を AppleScript で操作する
# ----------------------------------------------------------------------
def _osascript(script, timeout=30):
    try:
        r = subprocess.run(["osascript", "-"], input=script, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        # 初回は macOS の「○○が Google Chrome を制御しようとしています」の確認待ちで止まることがある
        raise FusetterError("Chrome の操作が時間切れになりました。「○○が“Google Chrome”を制御しようとしています」"
                            "という確認が出ていたら「OK」を押してから、もう一度試してください。")
    if r.returncode != 0:
        err = r.stderr.strip()
        if "JavaScript" in err and ("Apple Events" in err or "Apple イベント" in err or "オフ" in err or "turned off" in err):
            raise FusetterError("Chrome のメニュー「表示 → 開発 / 管理 → Apple Events からの JavaScript を許可」をオンにしてください。")
        if "-1743" in err or "not allowed" in err.lower() or "許可" in err:
            raise FusetterError("Chrome を操作する許可がありません。システム設定 → プライバシーとセキュリティ → オートメーション で許可してください。")
        raise FusetterError(f"Chrome の操作に失敗しました: {err}")
    return r.stdout.rstrip("\n")


_good_window_id = None  # 前回うまくいったウィンドウ (次からはこれを先に試す)


def _window_ids():
    out = _osascript('''
tell application "Google Chrome"
    set ids to {}
    repeat with w in windows
        set end of ids to (id of w as text)
    end repeat
    set AppleScript's text item delimiters to ","
    return ids as text
end tell''')
    return [x for x in out.split(",") if x]


def _open_background_tab(window_id, url):
    """ウィンドウ window_id に裏でタブを開く (Chrome を前に出さず、今見ているタブもそのまま)"""
    return _osascript(f'''
tell application "Google Chrome"
    set w to (first window whose id is {window_id})
    set prev to active tab index of w
    set t to make new tab at end of tabs of w with properties {{URL:"{url}"}}
    set active tab index of w to prev
    return id of t
end tell''')


def _close_tab(tab_id):
    try:
        _osascript(f'''
tell application "Google Chrome"
    repeat with w in windows
        try
            close (first tab of w whose id is {tab_id})
        end try
    end repeat
end tell''')
    except FusetterError:
        pass


def _open_post_tab():
    """
    投稿画面のタブを裏で開く。Chrome のプロフィールごとにウィンドウが分かれ、
    「Apple Events からの JavaScript を許可」もログイン状態もプロフィールごとなので、
    使えるウィンドウが見つかるまで順に試す。使えなかったウィンドウのタブは閉じる。
    """
    global _good_window_id
    windows = _window_ids()
    if not windows:
        _osascript('tell application "Google Chrome" to make new window')
        windows = _window_ids()
    if _good_window_id in windows:
        windows.remove(_good_window_id)
        windows.insert(0, _good_window_id)
    problems = []
    for wid in windows:
        tab_id = _open_background_tab(wid, POST_URL)
        try:
            _check_login(tab_id)
        except FusetterError as e:
            problems.append(str(e))
            _close_tab(tab_id)
            continue
        _good_window_id = wid
        return tab_id
    if any("ログイン" in p for p in problems):
        raise FusetterError("ふせったーにログインしている Chrome のウィンドウが見つかりませんでした。ログインしてからやり直してください。")
    raise FusetterError(problems[0] if problems else "Chrome のウィンドウが見つかりませんでした。")


def _js(tab_id, code, timeout=30):
    """タブで JavaScript を実行して結果 (文字列) を返す。コードは base64 で渡して、引用符の問題を避ける"""
    b64 = base64.b64encode(code.encode("utf-8")).decode("ascii")
    wrapper = ("(() => { try { const r = eval(new TextDecoder().decode(Uint8Array.from(atob('" + b64 +
               "'), c => c.charCodeAt(0)))); return r === undefined ? '' : String(r); }"
               " catch (e) { return 'JS_ERROR: ' + e.message; } })()")
    out = _osascript(f'''
tell application "Google Chrome"
    set found to missing value
    repeat with w in windows
        try
            set found to (first tab of w whose id is {tab_id})
            exit repeat
        end try
    end repeat
    if found is missing value then return "TAB_CLOSED"
    return execute found javascript "{wrapper}"
end tell''', timeout=timeout)
    if out == "TAB_CLOSED":
        raise FusetterError("ふせったーのタブが閉じられました。")
    if out.startswith("JS_ERROR: "):
        raise FusetterError(f"ふせったーの画面操作に失敗しました: {out[10:]}")
    return out


def _wait_js(tab_id, code, timeout_sec, message):
    """code が 'ok' を返すまで待つ"""
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if _js(tab_id, code) == "ok":
            return
        time.sleep(0.5)
    raise FusetterError(message)


def _q(s):
    return json.dumps(s, ensure_ascii=False)


def _check_login(tab_id):
    deadline = time.time() + 30
    while time.time() < deadline:
        state = _js(tab_id, f"location.href.includes('login') ? 'login' : "
                            f"(document.readyState === 'complete' && document.querySelector({_q(BODY_SELECTOR)}) ? 'ok' : '')")
        if state == "login":
            raise FusetterError("普段の Chrome でふせったーにログインしていません。ログインしてからやり直してください。")
        if state == "ok":
            return
        time.sleep(0.5)
    raise FusetterError("ふせったーの投稿画面が開けませんでした。")


# React の入力欄は value を直接書き換えても反映されないので、標準の setter を使って input イベントを送る
_FILL_JS = """
(() => {{
  const el = document.querySelector({selector});
  if (!el) throw new Error('入力欄が見つかりません: ' + {selector});
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
  setter.call(el, {value});
  el.dispatchEvent(new Event('input', {{ bubbles: true }}));
  return el.value === {value} ? 'ok' : 'mismatch';
}})()
"""

# 裏のタブでは描画されないので innerText は空になる。textContent と data-dialog-open で判定する
_VISIBILITY_TRIGGER_JS = """
[...document.querySelectorAll('form button[type=button]')].find(b =>
  b.offsetParent !== null && {labels}.some(l => b.textContent.trim().startsWith(l)))
"""


def _open_dialog_js(keyword):
    return ("[...document.querySelectorAll('[role=dialog][data-dialog-open=true]')]"
            f".find(d => d.textContent.includes({_q(keyword)}))")


def _fill(tab_id, selector, value):
    if _js(tab_id, _FILL_JS.format(selector=_q(selector), value=_q(value)), timeout=60) != "ok":
        raise FusetterError("入力欄に文字を入れられませんでした。")


def _select_anyone(tab_id):
    """公開範囲を「以下は広場では公開されません」の中の「だれでも」にする"""
    trigger = _VISIBILITY_TRIGGER_JS.format(labels=_q(list(VISIBILITY_LABELS)))
    current = _js(tab_id, f"(() => {{ const b = {trigger}; return b ? b.textContent.trim() : 'none'; }})()")
    if current == "none":
        raise FusetterError("公開範囲のボタンが見つかりませんでした。")
    if current == "だれでも":
        return  # 既に「だれでも」(初期値)。裏のタブでは選択画面のアニメーションが止まるので開かない
    if _js(tab_id, f"(() => {{ const b = {trigger}; b.click(); return 'ok'; }})()") != "ok":
        raise FusetterError("公開範囲のボタンが見つかりませんでした。")
    dialog = _open_dialog_js("広場では公開されません")
    _wait_js(tab_id, f"{dialog} ? 'ok' : ''", 10, "公開範囲の選択画面が開きませんでした。")
    clicked = _js(tab_id, f"(() => {{ const d = {dialog}; const b = [...d.querySelectorAll('button')]"
                          f".find(b => b.textContent.trim() === 'だれでも'); if (!b) return 'none'; b.click(); return 'ok'; }})()")
    if clicked != "ok":
        raise FusetterError("公開範囲に「だれでも」が見つかりませんでした。")
    _wait_js(tab_id, f"(() => {{ const b = {trigger}; return b && b.textContent.trim() === 'だれでも' && !{dialog} ? 'ok' : ''; }})()",
             10, "公開範囲を「だれでも」にできませんでした。")


def _focus_tab(tab_id):
    """タブを最前面にする (クリップボードへの書き込みは、手前にあるタブでしか許されないため)"""
    _osascript(f'''
tell application "Google Chrome"
    activate
    set wi to 0
    set ti to 0
    repeat with w from 1 to (count of windows)
        repeat with t from 1 to (count of tabs of window w)
            if (id of tab t of window w as text) is "{tab_id}" then
                set wi to w
                set ti to t
            end if
        end repeat
    end repeat
    if wi is 0 then error "tab not found"
    set active tab index of window wi to ti
    set index of window wi to 1
end tell''')


def _clipboard_get():
    return subprocess.run(["pbpaste"], capture_output=True).stdout


def _clipboard_set(data):
    subprocess.run(["pbcopy"], input=data)


def _wait_post_complete(tab_id, timeout_sec=30):
    """投稿ボタンのあと: 「伏せてないけどいいの?」が出たら OK を押し、投稿完了ページになるのを待つ"""
    confirm = (f"(() => {{ const d = {_open_dialog_js('伏せてないけどいいの')}; if (!d) return '';"
               " const b = [...d.querySelectorAll('button')].find(b => b.textContent.trim() === 'OK');"
               " if (b) b.click(); return 'confirmed'; })()")
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if "/post-complete" in _js(tab_id, "location.pathname"):
            return
        _js(tab_id, confirm)
        time.sleep(0.5)
    raise FusetterError("投稿できたか確認できませんでした。", unconfirmed=True)


def _copy_post_url(tab_id):
    """
    投稿完了ページの「この投稿の本文とURLをコピー」を押して、クリップボードから https://fse.tw/... を取る。
    使う前のクリップボードの中身は必ず元に戻す。
    """
    saved = _clipboard_get()
    try:
        _clipboard_set(b"")
        _focus_tab(tab_id)
        for _ in range(10):  # タブが手前に来るのを待つ
            if _js(tab_id, "String(document.hasFocus())") == "true":
                break
            time.sleep(0.3)
        deadline = time.time() + 10
        while time.time() < deadline:
            _js(tab_id, "(() => { const b = [...document.querySelectorAll('button')]"
                        ".find(e => e.textContent.includes('本文とURLをコピー')); if (b) b.click(); return ''; })()")
            time.sleep(0.7)
            m = re.search(r"https://fse\.tw/\w+", _clipboard_get().decode("utf-8", "replace"))
            if m:
                return m.group(0)
        raise FusetterError("投稿の URL をコピーできませんでした。ふせったーのマイページで確認してください。")
    finally:
        _clipboard_set(saved)


# 投稿時の ふせったー の返事から identificationCode (fse.tw/<code> の部分) を拾うフック。
# AppleScript の JavaScript はページとは別の世界で動くので、<script> を足してページ側に仕込み、
# 結果は DOM の属性で受け渡す。
_HOOK_CODE = r"""(() => {
  if (window.__mojiHooked) return; window.__mojiHooked = true;
  const find = d => { if (!d || typeof d !== 'object') return null;
    if (typeof d.identificationCode === 'string') return d.identificationCode;
    for (const k of ['data', 'post', 'result']) { const r = find(d[k]); if (r) return r; } return null; };
  const oj = Response.prototype.json;
  Response.prototype.json = async function () {
    const d = await oj.call(this);
    try { if (/\/api\/posts(\?|$)/.test(this.url)) { const c = find(d); if (c) document.documentElement.setAttribute('data-moji-post-code', c); } } catch (e) {}
    return d; };
})()"""


def _install_post_hook(tab_id):
    _js(tab_id, "(() => { document.documentElement.removeAttribute('data-moji-post-code');"
                " const s = document.createElement('script'); s.textContent = " + _q(_HOOK_CODE) + ";"
                " document.documentElement.appendChild(s); s.remove(); return ''; })()")


def _hooked_post_url(tab_id, timeout_sec=5):
    """フックで拾えた投稿の URL (取れなければ None)"""
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        code = _js(tab_id, "document.documentElement.getAttribute('data-moji-post-code') || ''")
        if re.fullmatch(r"\w+", code or ""):
            return f"https://fse.tw/{code}"
        time.sleep(0.5)
    return None


def post_to_fusetter(title, log_text, start=0, dry_run=False, on_status=print, on_posted=None):
    """
    ログを ふせったー に投稿する。10万字を超えたら タイトル_ログ2 … に分ける。
    start: 何件目から投稿するか (失敗後のやり直しで、投稿済みを飛ばすため)
    dry_run: True なら入力と公開範囲の選択までして、投稿ボタンは押さない
    on_posted(index, タイトル, URL): 1件投稿するたびに呼ばれる
    戻り値: [(タイトル, URL), ...] (今回投稿した分)
    """
    chunks = split_log(log_text)
    titles = post_titles(title, len(chunks))
    if len(titles[-1]) > BODY_LIMIT:
        raise FusetterError(f"タイトルが長すぎます (本文は{BODY_LIMIT}文字まで)。")

    results = []
    for i in range(start, len(chunks)):
        on_status(f"ふせったーに投稿中… ({i + 1}/{len(chunks)})")
        tab_id = _open_post_tab()
        _fill(tab_id, BODY_SELECTOR, titles[i])
        _fill(tab_id, POSTSCRIPT_SELECTOR, chunks[i])
        _select_anyone(tab_id)
        if dry_run:
            on_status(f"確認モード: {titles[i]} を入力しました (投稿はしていません)")
            results.append((titles[i], None))
            continue
        _install_post_hook(tab_id)
        # 投稿ボタンを押してから完了を確認するまでのエラーは、投稿済みかどうか分からない
        # (やり直すと二重投稿になるかもしれないので、人に確認してもらう)
        try:
            _js(tab_id, f"document.querySelector({_q(SUBMIT_SELECTOR)}).click()")
            _wait_post_complete(tab_id)
        except Exception as e:
            err = FusetterError(str(e) if isinstance(e, FusetterError) else f"投稿の確認中にエラーが起きました: {e}",
                                unconfirmed=True)
            err.title = titles[i]
            raise err from e
        # 完了後に URL だけ取れなかったときは、投稿済みとして続ける (やり直すと二重投稿になる)
        url = _hooked_post_url(tab_id)
        if url is None:
            try:
                url = _copy_post_url(tab_id)  # 取れなかったときだけ、タブを前に出してコピーする
            except Exception as e:
                print(f"{titles[i]}: {e}")
                url = None
        _close_tab(tab_id)
        results.append((titles[i], url))
        if on_posted:
            on_posted(i, titles[i], url)
    return results

if __name__ == "__main__":
    # 手動確認用
    #   .venv/bin/python src/fusetter.py                Discord に通知のテストを送る
    #   .venv/bin/python src/fusetter.py --dry-run      ふせったーに入力と公開範囲の選択までして、投稿はしない
    import sys
    if "--dry-run" in sys.argv:
        sample = "【テスト】\nこれは投稿しない確認用のテキストです。\n"
        for title, _ in post_to_fusetter("動作確認", sample, dry_run=True):
            print("入力済み:", title)
    else:
        webhook = load_webhook_url()
        if not webhook:
            sys.exit(f"{CONFIG_PATH} の discord_webhook_url に Webhook URL を書いてください。")
        send_discord(webhook, [("【テスト】mojiokoshi からの通知テスト", "https://fse.tw/GV4Zg9n8")])
        print("Discord にテストの通知を送りました。チャンネルを確認してください。")
