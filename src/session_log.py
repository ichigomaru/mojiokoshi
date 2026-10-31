"""シナリオ設定・投稿区間・発言まとめ用ログ (画面や通信に依存しない)。"""
import json
import os
import re
import shutil
import socket
import subprocess
from urllib.parse import quote

from fusetter import BODY_LIMIT, split_log

HATSUGEN_PORT = 4477
YOKOKU_PORT = 4479


def edit_url(path):
    """保存済みログのファイル名から編集画面の URL を作る。"""
    return f'http://localhost:{HATSUGEN_PORT}/?log={quote(os.path.basename(path), safe="")}'


def yokoku_url(path):
    """保存済みログのファイル名から予告編集画面の URL を作る。"""
    return f'http://localhost:{YOKOKU_PORT}/?log={quote(os.path.basename(path), safe="")}'


def edit_line(path, secret=False, who=''):
    label = f'発言まとめ(編集・秘匿 {who})' if secret else '発言まとめ(編集)'
    return f'{label}: {edit_url(path)}'


def absolute_path(path):
    return os.path.abspath(os.path.expanduser(path))


def load_state(path=None):
    try:
        with open(absolute_path(path or '~/.mojiokoshi/state.json'), encoding='utf-8') as f:
            state = json.load(f)
        return {k: v for k, v in state.items() if isinstance(v, str)}
    except (OSError, ValueError, AttributeError):
        return {}


def save_state(scenario, day, path=None):
    path = absolute_path(path or '~/.mojiokoshi/state.json')
    state = load_state(path)
    state.update(last_scenario=scenario, last_day=day)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def base_title(scenario, day):
    return f'{scenario}_{day}日目' if day else scenario


def secret_prefill(scene):
    secret = '秘匿' in scene
    return secret, scene.replace('秘匿', '').strip(' \t\r\n　-_・：:') if secret else ''


def insert_newlines_ja(text):
    text = ''.join(c + ('\n' if c in '。！？.!?' else '') for c in text)
    for c in "[],.'":
        text = text.replace(c, '')
    return text


def scene_text(scenes, headers=True, formatter=insert_newlines_ja):
    return formatter(''.join((f'【{name}】\n' if headers else '') + text + '\n\n'
                             for name, text in scenes.items()))


def group_segments(scenes, choices, scene_laugh_before=None):
    """choices はシーン名 → (秘匿か, 誰のシーン)。共有は連続する範囲だけまとめる。"""
    segments = []
    for name, text in scenes.items():
        secret, who = choices.get(name, (False, ''))
        if secret or not segments or segments[-1]['secret']:
            segments.append(dict(secret=secret, who=(who.strip() or name) if secret else '', scenes={}, laughBefore=[]))
        segments[-1]['scenes'][name] = text
        before = segments[-1]['laughBefore']
        before.extend(line for line in (scene_laugh_before or {}).get(name, []) if line not in before)
    return segments


def flatten_segments(segments):
    """入力タイトルの末尾番号を引き継ぎ、既存の10万字分割を適用する。"""
    flat = []
    for title, text in segments:
        match = re.search(r'(_ログ)(\d*)$', title)
        chunks = split_log(text)
        for i, chunk in enumerate(chunks):
            number = (int(match[2]) if match and match[2] else 1) + i
            stem = title[:match.start()] if match else title
            suffix = '_ログ' + (str(number) if number > 1 else '')
            t = stem + suffix if match else title + (str(i + 1) if i else '')
            if len(t) > BODY_LIMIT:
                raise ValueError(f'タイトルが長すぎます (本文は{BODY_LIMIT}文字まで)。')
            flat.append((t, chunk))
    return flat


def build_segments(base, segments, formatter=insert_newlines_ja):
    shared_count = 0
    secret_counts = {}
    texts = [scene_text(s['scenes'], formatter=formatter) for s in segments]
    # 同じ相手の秘匿が続いても、通し番号の桁数まで確保する。
    number_width = len(str(sum(len(split_log(text)) for text in texts)))
    for segment, text in zip(segments, texts):
        count = len(split_log(text))
        who = segment['who']
        if segment['secret']:
            # 分割後の番号も含めて150文字に収める。JSONには元の名前を残す。
            reserve = len(base + '_秘匿「」_ログ') + number_width
            room = BODY_LIMIT - reserve
            if room < 0:
                raise ValueError('シナリオ名が長すぎます。')
            stem = f'{base}_秘匿「{who[:room]}」'
            offset = secret_counts.get(stem, 0)
            secret_counts[stem] = offset + count
        else:
            stem, offset = base, shared_count
            shared_count += count
        title = stem + '_ログ' + (str(offset + 1) if offset else '')
        pairs = flatten_segments([(title, text)])
        segment.update(text=text, plain_text=scene_text(segment['scenes'], False, formatter), posts=pairs)
    return segments


# ファイル名の上限は 255 バイト。連番と拡張子 (_999.json) の分を空けておく
NAME_BYTES = 240


def log_file_stem(base, who, date):
    """保存するログのファイル名 (拡張子なし)。長すぎるときは秘匿の相手名、次にシナリオ名を切り詰める。"""
    def stem(b, w):
        name = b + ('_秘匿_' + w if w is not None else '') + '_' + date
        return re.sub(r'[/\\:*?"<>|\r\n]', '_', name)
    while who and len(stem(base, who).encode('utf-8')) > NAME_BYTES:
        who = who[:-1]
    while base and len(stem(base, who).encode('utf-8')) > NAME_BYTES:
        base = base[:-1]
    return stem(base, who)


def save_log(scenario, day, date, segment, urls):
    directory = absolute_path(os.path.join(os.environ.get('HATSUGEN_HOME') or '~/Documents/TRPG/発言まとめ', 'logs'))
    os.makedirs(directory, exist_ok=True)
    name = log_file_stem(base_title(scenario, day), segment['who'] if segment['secret'] else None, date)
    content = dict(scenario=scenario, day=f'{day}日目' if day else '', date=date,
                   fusetterUrl=urls[0], fusetterUrls=urls, secret=segment['secret'],
                   secretTitle=segment['who'], scenes=list(segment['scenes']), text=segment['plain_text'],
                   laughBefore=segment.get('laughBefore', []))
    number = 1
    while True:
        path = os.path.join(directory, name + (f'_{number}' if number > 1 else '') + '.json')
        try:
            with open(path, 'x', encoding='utf-8') as f:
                json.dump(content, f, ensure_ascii=False, indent=2)
            return path
        except FileExistsError:
            number += 1


class SegmentLogs:
    """投稿完了した区間を即時保存する。再試行でも同じ区間は保存しない。"""
    def __init__(self, scenario, day, date, segments):
        self.scenario, self.day, self.date = scenario, day, date
        self.segments = segments
        self.urls = {}
        self.finished = set()
        self.saved = []
        self.edit_lines = []
        self.errors = []

    def on_posted(self, index, title, url):
        self.urls[index] = url
        end = 0
        for number, segment in enumerate(self.segments):
            start = end
            end += len(segment['posts'])
            if number in self.finished or not all(i in self.urls for i in range(start, end)):
                continue
            self.finished.add(number)
            try:
                path = save_log(self.scenario, self.day, self.date, segment,
                                [self.urls[i] for i in range(start, end)])
                self.saved.append((segment['secret'], path))
                self.edit_lines.append(edit_line(path, segment['secret'], segment['who']))
            except Exception as e:
                self.errors.append(f'ログ保存失敗: {e}')

    @property
    def discord_lines(self):
        lines = []
        for line, (secret, path) in zip(self.edit_lines, self.saved):
            lines.append(line)
            if not secret:
                lines.append(f'予告(編集): {yokoku_url(path)}')
        return lines

    def preferred_path(self):
        return next((p for secret, p in self.saved if not secret), self.saved[0][1] if self.saved else None)


def launch_maker(path):
    directory = absolute_path(load_state().get('hatsugen_maker_dir') or '~/Developer/hatsugen-maker')
    node = shutil.which('node')
    if not node:
        node = next((p for p in ('/usr/local/bin/node', '/opt/homebrew/bin/node') if os.access(p, os.X_OK)), None)
    if not node:
        raise OSError('node が見つかりません。')
    server = absolute_path(os.path.join(directory, 'server.mjs'))
    if not os.path.isfile(server):
        raise OSError(f'server.mjs が見つかりません: {directory}')
    subprocess.Popen([absolute_path(node), server, '--log', absolute_path(path)], cwd=directory,
                     start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def launch_yokoku():
    """予告サーバーをブラウザを開かず起動する。起動済みなら何もしない。"""
    try:
        with socket.create_connection(('localhost', YOKOKU_PORT), timeout=0.2):
            return
    except OSError:
        pass
    directory = absolute_path(load_state().get('ccfolia_opening_dir') or '~/Developer/ccfolia-opening')
    node = shutil.which('node')
    if not node:
        node = next((p for p in ('/usr/local/bin/node', '/opt/homebrew/bin/node') if os.access(p, os.X_OK)), None)
    if not node:
        raise OSError('node が見つかりません。')
    server = os.path.join(directory, 'src', 'yokoku', 'server.mjs')
    if not os.path.isfile(server):
        raise OSError('予告の server.mjs が見つかりません。')
    process = subprocess.Popen([absolute_path(node), server, '--no-open'], cwd=directory,
                               start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        process.wait(timeout=0.2)
    except subprocess.TimeoutExpired:
        return
    raise OSError('予告サーバーが終了しました。')
