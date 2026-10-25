"""Isolated resume regression tests; all HTTP/model entry points are mocked.

Run with python -B -m unittest discover -s tests -v.
All run artifacts go to TemporaryDirectory, never to runs.
"""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from bfsi.server import jobs
from bfsi.semantic import runner as semantic


VIDEO = {'bvid': 'BV1234567890', 'oid': '98765', 'title': '续跑离线测试', 'url': None}
RUN_ID = '20261023T120000Z_abcdef12'


def standard(count=12):
    return {'schema_version': '0.1', 'comments': [
        {'core': {'comment_id': str(100 + n), 'video_bvid': VIDEO['bvid'], 'video_oid': VIDEO['oid'],
                  'raw_text': f'仅用于离线测试的第 {n} 条评论', 'depth': 0,
                  'parent_comment_id': None, 'root_comment_id': None, 'published_at': '2026-10-23T00:00:00Z'},
         'observations': [{'has_picture': False}]} for n in range(count)]}


def fake_model(fail_second_batch=False, seen=None):
    def run(command, *, input, stdout, stderr, env, timeout):
        inputs = json.loads(input.decode().split('\nINPUT_ITEMS\n', 1)[1])
        ids = [item['current_comment']['comment_id'] for item in inputs]
        if seen is not None:
            seen.append(ids)
        values = [{'comment_id': cid, 'relevance': 'unclear', 'targets': [], 'market_stance': 'unclear',
                   'emotion': ['neutral'], 'action_intent': 'unclear', 'creator_attitude': 'unclear',
                   'sarcasm': 'unclear', 'confidence': 'low', 'summary': '受控离线模型响应'} for cid in ids]
        raw = 'invalid response' if fail_second_batch and ids[0] == '110' else json.dumps(values)
        Path(command[command.index('-o') + 1]).write_text(raw)
        for event in [{'type': 'item.completed', 'item': {'type': 'agent_message', 'text': raw}},
                      {'type': 'turn.completed', 'usage': {'input_tokens': 1, 'output_tokens': 1}}]:
            stdout.write((json.dumps(event) + '\n').encode())
        return subprocess.CompletedProcess(command, 0)
    return run


class ResumeEdges(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.app = Path(self.temporary.name)
        self.runs = self.app / 'runs'
        self.folder = self.runs / RUN_ID
        self.folder.mkdir(parents=True)
        self.patches = [patch.object(jobs, 'APP', self.app), patch.object(jobs, 'RUNS', self.runs)]
        for item in self.patches:
            item.start()
        self.worker = object.__new__(jobs.Jobs)
        self.worker.lock = threading.RLock()
        self.worker.active = None
        self.worker.closing = False
        self.data = standard()
        self.write('standard_comments.json', self.data)
        self.write('source_file_manifest.json', {'schema_version': '0.1', 'files': []})
        self.write('collection_summary.json', {'mode': 'full', 'complete': True, 'partial': False,
                   'warnings': [], 'selected_comment_ids': [str(100 + n) for n in range(12)]})
        self.write('cleaning_summary.json', {'full_data_integrity': True, 'partial': False, 'warnings': []})

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temporary.cleanup()

    def write(self, name, value):
        path = self.folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')

    def status(self, **changes):
        values = {'run_id': RUN_ID, 'state': 'failed', 'stage': 'failed', 'bvid': VIDEO['bvid'],
                  'mode': 'full', 'limit': None, 'pause_requested': False, 'resume_stage': 'collect',
                  'failed_stage': 'semantic', 'collected': 12, 'analyzed': 10}
        values.update(changes)
        self.write('status.json', values)

    def test_failed_stage_wins_and_only_unsuccessful_batch_is_reinferred(self):
        # Simulate collect-pause -> resume -> one successful semantic batch and one failed batch.
        with patch.object(semantic, '_cli', return_value='offline-mock'), patch.object(semantic.subprocess, 'run', side_effect=fake_model(fail_second_batch=True)):
            with self.assertRaises(semantic.SemanticError):
                semantic.analyze(self.data, VIDEO, self.folder)
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns)
                  for p in (self.folder / 'semantic').rglob('B001.attempt1*') if p.is_file()}
        self.status()
        with patch.object(self.worker, '_enqueue') as enqueue:
            queued = self.worker.resume(RUN_ID)
        self.assertEqual(queued['resume_stage'], 'semantic')
        enqueue.assert_called_once_with(RUN_ID)

        requested = []
        def payload(*args):
            return {'video': {'video_oid': VIDEO['oid'], 'comment_count': 12},
                    'headline': {'no_directional_signal': True}}
        with patch.object(self.worker, '_video', return_value=copy.deepcopy(VIDEO)), \
             patch('bfsi.collection.collector.collect', side_effect=AssertionError('Frozen input must not be recollected')) as collect, \
             patch('bfsi.transform.cleaner.clean', return_value=copy.deepcopy(self.data)), \
             patch('bfsi.index.calculator.build_payload', side_effect=payload), \
             patch.object(semantic, '_cli', return_value='offline-mock'), \
             patch.object(semantic.subprocess, 'run', side_effect=fake_model(seen=requested)):
            self.worker._run(RUN_ID)
        collect.assert_not_called()
        self.assertEqual(requested, [['110', '111']])
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before})
        self.assertEqual(jobs.read_status(RUN_ID)['state'], 'completed')
        result = json.loads((self.folder / 'semantic/full_semantic_dataset.json').read_text())
        self.assertEqual(result['record_count'], 12)
        self.assertEqual(len({r['comment_id'] for r in result['records']}), 12)

    def test_missing_frozen_sources_fail_without_collect_or_model(self):
        for missing in ['standard_comments.json', 'source_file_manifest.json', 'collection_summary.json']:
            for prior in ['semantic', 'collect']:
                with self.subTest(missing=missing, prior=prior):
                    backups = {name: (self.folder / name).read_bytes()
                               for name in ['standard_comments.json', 'source_file_manifest.json', 'collection_summary.json']}
                    (self.folder / missing).unlink()
                    self.write('semantic/contract.json', {'marker': 'frozen-input-exists'})
                    self.status(state='queued', resume_stage=prior, failed_stage=None)
                    with patch.object(self.worker, '_video', return_value=copy.deepcopy(VIDEO)), \
                         patch('bfsi.collection.collector.collect', side_effect=AssertionError('Do not recollect missing frozen input')) as collect, \
                         patch('bfsi.transform.cleaner.clean', side_effect=AssertionError('Do not rebuild unverified frozen input')) as clean, \
                         patch('bfsi.semantic.runner.analyze', side_effect=AssertionError('No model for incomplete frozen input')) as analyze, \
                         patch.object(semantic.subprocess, 'run', side_effect=AssertionError('No actual CLI')) as runner:
                        self.worker._run(RUN_ID)
                    collect.assert_not_called()
                    clean.assert_not_called()
                    analyze.assert_not_called()
                    runner.assert_not_called()
                    self.assertEqual(jobs.read_status(RUN_ID)['state'], 'failed')
                    self.assertFalse((self.folder / 'payload.json').exists())
                    for name, contents in backups.items():
                        (self.folder / name).write_bytes(contents)


if __name__ == '__main__':
    unittest.main()
