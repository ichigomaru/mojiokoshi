"""発言まとめ連携のテスト。通信・GUI・実際のメーカー起動は行わない。"""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
import session_log as log
import fusetter


class SessionLogTest(unittest.TestCase):
    def test_edit_url_and_secret_label(self):
        path = '/tmp/logs/物語 +#&?.json'
        url = ('http://localhost:4477/?log='
               '%E7%89%A9%E8%AA%9E%20%2B%23%26%3F.json')
        self.assertEqual(log.edit_url(path), url)
        self.assertEqual(log.edit_line(path), f'発言まとめ(編集): {url}')
        self.assertEqual(log.edit_line(path, True, 'PC1'),
                         f'発言まとめ(編集・秘匿 PC1): {url}')

    def test_edit_lines_only_for_saved_logs(self):
        segments = log.build_segments('会議', log.group_segments(
            {'A': '共有', 'S': '秘密', 'B': '続き'}, {'S': (True, 'PC1')}))
        tracker = log.SegmentLogs('会議', '', '2026-10-30', segments)
        self.assertEqual(tracker.edit_lines, [])
        with patch.object(log, 'save_log', side_effect=[
                '/tmp/shared.json', '/tmp/secret.json', OSError('保存失敗')]):
            for i in range(3):
                tracker.on_posted(i, '', 'fake-url')
            tracker.on_posted(1, '', 'fake-url')
        self.assertEqual(tracker.edit_lines, [
            log.edit_line('/tmp/shared.json'), log.edit_line('/tmp/secret.json', True, 'PC1')])
        self.assertEqual(len(tracker.errors), 1)

    def test_state(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'settings', 'state.json')
            self.assertEqual(log.load_state(path), {})
            log.save_state('物語', '6', path)
            self.assertEqual(log.load_state(path)['last_day'], '6')
            with open(path, 'w') as f:
                json.dump({'hatsugen_maker_dir': '/tmp/maker'}, f)
            log.save_state('会議', '', path)
            self.assertEqual(log.load_state(path)['hatsugen_maker_dir'], '/tmp/maker')
            for invalid in ('{', '[]', 'null', '123'):
                with open(path, 'w') as f:
                    f.write(invalid)
                self.assertEqual(log.load_state(path), {})

    def test_prefill(self):
        for name in ('秘匿 - PC1', 'PC1_秘匿', '秘匿：PC1・', ' 秘匿 : PC1 '):
            self.assertEqual(log.secret_prefill(name), (True, 'PC1'))
        self.assertEqual(log.secret_prefill('共有'), (False, ''))
        self.assertEqual(log.secret_prefill('秘匿'), (True, ''))

    def test_group_and_format(self):
        scenes = {'A': '一。', 'B': '二！', '秘匿': '三？', 'C': '四。', 'D': '五。'}
        groups = log.group_segments(scenes, {'秘匿': (True, '')})
        self.assertEqual([list(s['scenes']) for s in groups], [['A', 'B'], ['秘匿'], ['C', 'D']])
        self.assertEqual(groups[1]['who'], '秘匿')
        all_shared = log.build_segments('物語', log.group_segments(scenes, {}))
        old = ''.join(f'【{n}】\n{t}\n\n' for n, t in scenes.items())
        self.assertEqual(all_shared[0]['text'], log.insert_newlines_ja(old))
        self.assertNotIn('【', all_shared[0]['plain_text'])

    def test_titles_and_split(self):
        scenes = {'A': ('x' * 50_000 + '\n') * 2, 'S': '秘密', 'B': '続き', 'T': '別の秘密'}
        groups = log.build_segments(log.base_title('物語', '6'), log.group_segments(
            scenes, {'S': (True, 'PC'), 'T': (True, 'PC')}))
        titles = [t for s in groups for t, _ in s['posts']]
        self.assertEqual(titles, ['物語_6日目_ログ', '物語_6日目_ログ2',
                                  '物語_6日目_秘匿「PC」_ログ', '物語_6日目_ログ3',
                                  '物語_6日目_秘匿「PC」_ログ2'])
        self.assertEqual(log.base_title('会議', ''), '会議')
        self.assertTrue(all(len(c) <= 100_000 for s in groups for _, c in s['posts']))
        long = log.build_segments('物語', log.group_segments({'S': ('x' * 50_000 + '\n') * 2}, {'S': (True, '長' * 300)}))
        self.assertTrue(all(len(t) <= 150 for t, _ in long[0]['posts']))
        many = {f'S{i}': '秘密' for i in range(12)}
        long = log.build_segments('物語', log.group_segments(many, {n: (True, '長' * 300) for n in many}))
        self.assertTrue(all(len(t) <= 150 for s in long for t, _ in s['posts']))
        self.assertEqual(len({t for s in long for t, _ in s['posts']}), 12)
        with self.assertRaises(ValueError):
            log.build_segments('長' * 150, log.group_segments({'A': 'a'}, {}))

    def test_json_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, HATSUGEN_HOME=d):
            segments = log.build_segments('物語', log.group_segments({'A': '話。', 'S': '秘密！'}, {'S': (True, 'P/C')}))
            path = log.save_log('物/語', '6', '2026-10-30', segments[0], ['url1', 'url2'])
            self.assertEqual(os.path.basename(path), '物_語_6日目_2026-10-30.json')
            path2 = log.save_log('物/語', '6', '2026-10-30', segments[0], ['url1'])
            self.assertTrue(path2.endswith('_2.json'))
            with open(path, encoding='utf-8') as f:
                self.assertEqual(json.load(f), dict(scenario='物/語', day='6日目', date='2026-10-30',
                    fusetterUrl='url1', fusetterUrls=['url1', 'url2'], secret=False, secretTitle='',
                    scenes=['A'], text='話。\n\n\n'))
            secret = log.save_log('会議', '', '2026-10-30', segments[1], ['secret'])
            self.assertTrue(secret.endswith('会議_秘匿_P_C_2026-10-30.json'))
            unsafe = '/\\:*?"<>|\n\r'
            safe = log.save_log(unsafe, '', '2026-10-30', segments[0], [None])
            self.assertNotIn(':', os.path.basename(safe))
            self.assertNotIn('\n', os.path.basename(safe))

    def test_long_secret_name_is_shortened_in_file_name(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, HATSUGEN_HOME=d):
            who = '長' * 100
            segments = log.build_segments('物語', log.group_segments({'S': '秘密！'}, {'S': (True, who)}))
            path = log.save_log('物語', '6', '2026-10-30', segments[0], ['url'])
            self.assertLessEqual(len(os.path.basename(path).encode('utf-8')), 255)
            self.assertTrue(os.path.basename(path).startswith('物語_6日目_秘匿_長'))
            self.assertTrue(path.endswith('_2026-10-30.json'))
            with open(path, encoding='utf-8') as f:
                self.assertEqual(json.load(f)['secretTitle'], who)

    def test_partial_resume_saves_once(self):
        segments = log.build_segments('物語', log.group_segments({'A': ('x' * 50_000 + '\n') * 2, 'S': '秘'}, {'S': (True, 'PC')}))
        tracker = log.SegmentLogs('物語', '6', '2026-10-30', segments)
        with patch.object(log, 'save_log', side_effect=['/tmp/shared.json', '/tmp/secret.json']) as save:
            tracker.on_posted(0, '', 'a')
            save.assert_not_called()
            tracker.on_posted(1, '', 'b')
            self.assertEqual(save.call_count, 1)
            tracker.on_posted(2, '', 'c')
            tracker.on_posted(2, '', 'c')
            self.assertEqual(save.call_count, 2)
            self.assertEqual(tracker.preferred_path(), '/tmp/shared.json')
            self.assertEqual(save.call_args_list[0].args[-1], ['a', 'b'])
        tracker = log.SegmentLogs('物語', '', '2026-10-30', segments)
        with patch.object(log, 'save_log', side_effect=OSError('保存不可')):
            for i in range(3):
                tracker.on_posted(i, '', None)
        self.assertEqual(len(tracker.errors), 2)

    def test_launch(self):
        with patch.object(log, 'load_state', return_value={'hatsugen_maker_dir': '~/maker'}), \
             patch.object(log.shutil, 'which', return_value='/usr/bin/node'), \
             patch.object(log.os.path, 'isfile', return_value=True), \
             patch.object(log.subprocess, 'Popen') as popen:
            log.launch_maker('/tmp/session.json')
            args, kwargs = popen.call_args
            self.assertEqual(args[0][-2:], ['--log', '/tmp/session.json'])
            self.assertTrue(kwargs['start_new_session'])
            self.assertTrue(os.path.isabs(kwargs['cwd']))


class PostSegmentsTest(unittest.TestCase):
    def test_failure_then_resume(self):
        segments = log.build_segments('会議', log.group_segments({'A': '共有', 'S': '秘密'}, {'S': (True, 'PC')}))
        pairs = [p for segment in segments for p in segment['posts']]
        tracker = log.SegmentLogs('会議', '', '2026-10-30', segments)
        posted = []

        def callback(i, title, url):
            posted.append((title, url))
            tracker.on_posted(i, title, url)

        with patch.object(fusetter, '_open_post_tab', side_effect=[1, RuntimeError('一時的な失敗'), 2]), \
             patch.object(fusetter, '_fill'), patch.object(fusetter, '_select_anyone'), \
             patch.object(fusetter, '_install_post_hook'), patch.object(fusetter, '_js'), \
             patch.object(fusetter, '_wait_post_complete'), patch.object(fusetter, '_close_tab'), \
             patch.object(fusetter, '_hooked_post_url', return_value='fake-url'), \
             patch.object(log, 'save_log', side_effect=['/tmp/shared.json', OSError('保存失敗')]) as save:
            with self.assertRaises(RuntimeError):
                fusetter.post_segments(pairs, on_posted=callback)
            self.assertEqual(len(tracker.saved), 1)
            fusetter.post_segments(pairs, start=len(posted), on_posted=callback)
            self.assertEqual(len(posted), 2)
            self.assertEqual(save.call_count, 2)
            self.assertEqual(len(tracker.errors), 1)

    def test_flatten_and_resume(self):
        pairs = [('会議_ログ', ('x' * 50_000 + '\n') * 2), ('会議_秘匿「PC」_ログ', 'secret'), ('会議_ログ3', 'end')]
        posted = []
        with patch.object(fusetter, '_open_post_tab', return_value=1), \
             patch.object(fusetter, '_fill') as fill, \
             patch.object(fusetter, '_select_anyone'), \
             patch.object(fusetter, '_install_post_hook'), \
             patch.object(fusetter, '_js'), \
             patch.object(fusetter, '_wait_post_complete'), \
             patch.object(fusetter, '_hooked_post_url', return_value='fake-url'), \
             patch.object(fusetter, '_close_tab'):
            result = fusetter.post_segments(pairs, start=1, on_posted=lambda *args: posted.append(args))
        self.assertEqual([t for t, u in result], ['会議_ログ2', '会議_秘匿「PC」_ログ', '会議_ログ3'])
        self.assertEqual([i for i, t, u in posted], [1, 2, 3])
        self.assertEqual(fill.call_count, 6)
        self.assertEqual(fusetter.post_segments(pairs, start=4), [])


if __name__ == '__main__':
    unittest.main()
