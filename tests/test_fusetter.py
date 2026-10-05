"""fusetter.py のうち、ブラウザを使わない部分 (分割・タイトル・設定・Discord 送信) のテスト"""
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import fusetter  # noqa: E402


class SplitTest(unittest.TestCase):
    def test_short_text_is_one_chunk(self):
        self.assertEqual(fusetter.split_log("abc"), ["abc"])
        self.assertEqual(fusetter.split_log(""), [""])

    def test_split_at_newline_within_limit(self):
        text = "aaaa\nbbbb\ncccc\n"
        chunks = fusetter.split_log(text, limit=10)
        self.assertEqual(chunks, ["aaaa\nbbbb\n", "cccc\n"])
        self.assertEqual("".join(chunks), text)
        self.assertTrue(all(len(c) <= 10 for c in chunks))

    def test_newline_exactly_at_limit(self):
        # 改行がちょうど limit 文字目にあっても、limit を超えない
        chunks = fusetter.split_log("a" * 10 + "\nb", limit=10)
        self.assertTrue(all(len(c) <= 10 for c in chunks), [len(c) for c in chunks])
        self.assertEqual("".join(chunks), "a" * 10 + "\nb")

    def test_long_line_is_cut_by_length(self):
        text = "x" * 25
        self.assertEqual(fusetter.split_log(text, limit=10), ["x" * 10, "x" * 10, "x" * 5])

    def test_default_limit_100k(self):
        line = "あ" * 999 + "\n"  # 1000 文字の行
        text = line * 250          # 25 万文字
        chunks = fusetter.split_log(text)
        self.assertEqual(len(chunks), 3)
        self.assertEqual([len(c) for c in chunks], [100_000, 100_000, 50_000])
        self.assertEqual("".join(chunks), text)

    def test_titles(self):
        self.assertEqual(fusetter.post_titles("会議", 1), ["会議_ログ"])
        self.assertEqual(fusetter.post_titles("会議", 3), ["会議_ログ", "会議_ログ2", "会議_ログ3"])

    def test_nothing_left_returns_without_browser(self):
        # 全部投稿済みならブラウザを起動せずに戻る
        self.assertEqual(fusetter.post_to_fusetter("会議", "abc", start=1, on_status=lambda s: None), [])


class ConfigTest(unittest.TestCase):
    def test_load_webhook_url(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.local.json")
            self.assertIsNone(fusetter.load_webhook_url(path))
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"discord_webhook_url": " https://example.invalid/hook "}, f)
            self.assertEqual(fusetter.load_webhook_url(path), "https://example.invalid/hook")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"discord_webhook_url": ""}, f)
            self.assertIsNone(fusetter.load_webhook_url(path))

    def test_search_order(self):
        # プロジェクト直下が空なら ~/.mojiokoshi/ を見る
        with tempfile.TemporaryDirectory() as d:
            a, b = os.path.join(d, "a.json"), os.path.join(d, "b.json")
            with open(b, "w", encoding="utf-8") as f:
                json.dump({"discord_webhook_url": "https://example.invalid/home"}, f)
            orig = (fusetter.CONFIG_PATH, fusetter.HOME_CONFIG_PATH)
            fusetter.CONFIG_PATH, fusetter.HOME_CONFIG_PATH = a, b
            try:
                self.assertEqual(fusetter.load_webhook_url(), "https://example.invalid/home")
            finally:
                fusetter.CONFIG_PATH, fusetter.HOME_CONFIG_PATH = orig


class DiscordTest(unittest.TestCase):
    """手元に立てた HTTP サーバーを Webhook の代わりにして、送る中身を確かめる"""

    def setUp(self):
        self.received = []
        received = self.received

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                received.append(json.loads(body))
                self.send_response(204)
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/hook"

    def tearDown(self):
        self.server.shutdown()

    def test_send_title_and_url(self):
        fusetter.send_discord(self.url, [("会議_ログ", "https://fse.tw/a"),
                                         ("会議_ログ2", "https://fse.tw/b")])
        self.assertEqual(len(self.received), 1)
        self.assertEqual(self.received[0]["content"],
                         "会議_ログ\nhttps://fse.tw/a\n\n会議_ログ2\nhttps://fse.tw/b")

    def test_resume_skips_sent_messages(self):
        posts = [(f"会議_ログ{i}", "https://fse.tw/" + "x" * 300) for i in range(10)]
        total = len(fusetter.discord_messages(posts))
        sent = []
        fusetter.send_discord(self.url, posts, skip=1, on_sent=sent.append)
        self.assertEqual(len(self.received), total - 1)  # 1通目は送らない
        self.assertEqual(sent, list(range(2, total + 1)))

    def test_unknown_url_message(self):
        fusetter.send_discord(self.url, [("会議_ログ", None)])
        self.assertIn("URL を取得できませんでした", self.received[0]["content"])

    def test_long_message_is_split_under_2000(self):
        posts = [(f"会議_ログ{i}", "https://fusetter.com/tw/" + "x" * 300) for i in range(10)]
        fusetter.send_discord(self.url, posts)
        self.assertGreater(len(self.received), 1)
        self.assertTrue(all(len(m["content"]) <= 2000 for m in self.received))
        joined = "\n\n".join(m["content"] for m in self.received)
        self.assertEqual(joined.count("会議_ログ"), 10)


if __name__ == "__main__":
    unittest.main()
