"""予告連携。GUI・外部サーバーを起動せず検証する。"""
import ast
import os
import sys
import subprocess
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
import session_log as log
import fusetter


class YokokuTest(unittest.TestCase):
    def test_url(self):
        self.assertEqual(log.yokoku_url('/tmp/物語 +#&?.json'),
                         'http://localhost:4479/?log=%E7%89%A9%E8%AA%9E%20%2B%23%26%3F.json')

    def test_notification_order_secret_failure_and_resume(self):
        segments = log.build_segments('会議', log.group_segments(
            {'A': '共有', 'S': '秘密', 'B': '続き'}, {'S': (True, 'PC1')}))
        tracker = log.SegmentLogs('会議', '', '2026-10-30', segments)
        with patch.object(log, 'save_log', side_effect=[
                '/tmp/shared.json', '/tmp/secret.json', OSError('保存失敗')]):
            for i in range(3):
                tracker.on_posted(i, '', 'url')
            tracker.on_posted(0, '', 'url')
        self.assertEqual(tracker.discord_lines, [
            log.edit_line('/tmp/shared.json'),
            '予告(編集): http://localhost:4479/?log=shared.json',
            log.edit_line('/tmp/secret.json', True, 'PC1')])

    def test_discord_split(self):
        segments = log.build_segments('会議', log.group_segments({'A': '共有'}, {}))
        tracker = log.SegmentLogs('会議', '', '', segments)
        path = '/tmp/' + '長' * 75 + '.json'
        with patch.object(log, 'save_log', return_value=path):
            tracker.on_posted(0, '', 'url')
        messages = fusetter.discord_messages([('題' * 140, 'u' * 1500)],
                                             extra_lines=tracker.discord_lines)
        self.assertGreater(len(messages), 1)
        self.assertTrue(all(len(m) <= 2000 for m in messages))
        for line in tracker.discord_lines:
            self.assertIn(line, '\n\n'.join(messages))

    def test_launch_directories(self):
        for state, directory in [({}, '~/Developer/ccfolia-opening'),
                                 ({'ccfolia_opening_dir': '~/custom'}, '~/custom'),
                                 ({'ccfolia_opening_dir': ''}, '~/Developer/ccfolia-opening')]:
            with self.subTest(state=state), \
                 patch.object(log.socket, 'create_connection', side_effect=OSError), \
                 patch.object(log, 'load_state', return_value=state), \
                 patch.object(log.shutil, 'which', return_value='/usr/bin/node'), \
                 patch.object(log.os.path, 'isfile', return_value=True), \
                 patch.object(log.subprocess, 'Popen') as popen:
                popen.return_value.wait.side_effect = subprocess.TimeoutExpired('node', 0.2)
                log.launch_yokoku()
                absolute = log.absolute_path(directory)
                self.assertEqual(popen.call_args.args[0], [
                    '/usr/bin/node', os.path.join(absolute, 'src/yokoku/server.mjs'), '--no-open'])
                self.assertEqual(popen.call_args.kwargs['cwd'], absolute)
                self.assertTrue(popen.call_args.kwargs['start_new_session'])

    def test_already_running(self):
        with patch.object(log.socket, 'create_connection') as connect, \
             patch.object(log.subprocess, 'Popen') as popen:
            log.launch_yokoku()
        connect.assert_called_once_with(('localhost', 4479), timeout=0.2)
        popen.assert_not_called()

    def test_launch_failures(self):
        for missing, error, exited in [(True, None, False), (False, OSError('失敗'), False),
                                       (False, None, True)]:
            with self.subTest(missing=missing, error=error, exited=exited), \
                 patch.object(log.socket, 'create_connection', side_effect=OSError), \
                 patch.object(log, 'load_state', return_value={}), \
                 patch.object(log.shutil, 'which', return_value='/usr/bin/node'), \
                 patch.object(log.os.path, 'isfile', return_value=not missing), \
                 patch.object(log.subprocess, 'Popen', side_effect=error) as popen:
                with self.assertRaises(OSError):
                    log.launch_yokoku()
                if missing:
                    popen.assert_not_called()


class GuiLaunchTest(unittest.TestCase):
    def test_yokoku_failure_does_not_block_maker(self):
        # GUIは起動せず、起動ボタンの分岐だけをそのまま実行する。
        source = os.path.join(os.path.dirname(__file__), '..', 'src', 'gui.py')
        with open(source, encoding='utf-8') as f:
            tree = ast.parse(f.read())
        branch = next(node for node in ast.walk(tree) if isinstance(node, ast.If)
                      and ast.unparse(node.test) == "action == 'use'")
        code = compile(ast.Module(body=[branch], type_ignores=[]), source, 'exec')
        logs = MagicMock()
        logs.preferred_path.return_value = '/tmp/shared.json'
        with patch.object(log, 'launch_yokoku', side_effect=OSError('起動失敗')), \
             patch.object(log, 'launch_maker') as maker, patch('builtins.print'):
            exec(code, dict(action='use', session_log=log, logs=logs, self=MagicMock()))
        maker.assert_called_once_with('/tmp/shared.json')
