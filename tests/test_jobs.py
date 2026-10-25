"""Offline job/API integration boundaries, using isolated run storage only."""
from contextlib import ExitStack
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from bfsi.server import jobs, http as server
from bfsi.control import CooperativePause


class JobTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.app = Path(self.temporary.name).resolve()
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(jobs, 'APP', self.app))
        self.stack.enter_context(patch.object(jobs, 'RUNS', self.app / 'runs'))
        self.manager = jobs.Jobs()

    def tearDown(self):
        self.manager.shutdown()
        self.stack.close()
        self.temporary.cleanup()

    def wait(self, run_id):
        for _ in range(400):
            value = jobs.read_status(run_id)
            if value['state'] not in jobs.ACTIVE and self.manager.active is None:
                return value
            time.sleep(.01)
        self.fail('isolated job did not finish')

    def fixture_pipeline(self, count=1, partial_result=False):
        """Stub network/model boundaries; retain jobs.py publication unchanged."""
        standard = {'comments': [{'core': {'comment_id': str(900101 + i)},
                                  'observations': [{}]} for i in range(count)]}
        dataset = {'records': [{'comment_id': c['core']['comment_id']} for c in standard['comments']],
                   'model_requested': 'TEST_ONLY'}
        collection = {'complete': not partial_result, 'partial': partial_result,
                      'warnings': ['one page unavailable'] if partial_result else []}

        def collect(video, folder, **kwargs):
            manifest = {'files': []}
            jobs.save_json(folder / 'source_file_manifest.json', manifest)
            jobs.save_json(folder / 'collection_summary.json', collection)
            return manifest, collection

        def clean(video, manifest, folder):
            jobs.save_json(folder / 'standard_comments.json', standard)
            jobs.save_json(folder / 'cleaning_summary.json', {'warnings': [], 'full_data_integrity': True})
            return standard

        def payload(ds, video, scope, run_id):
            return {'video': {'video_oid': video['oid'], 'video_bvid': video['bvid'],
                              'title': video['title'], 'comment_count': count},
                    'headline': {'bfsi': 50, 'no_directional_signal': False}, 'analysis': {}}

        resolve_mock = self.stack.enter_context(patch('bfsi.collection.collector.resolve_video',
            side_effect=lambda bv: dict(bvid=bv, oid='900001', title='isolated fixture',
                                       _resolution_raw='<html>isolated</html>')))
        collect_mock = self.stack.enter_context(patch('bfsi.collection.collector.collect', side_effect=collect))
        clean_mock = self.stack.enter_context(patch('bfsi.transform.cleaner.clean', side_effect=clean))
        analyze_mock = self.stack.enter_context(patch('bfsi.semantic.runner.analyze', return_value=dataset))
        self.stack.enter_context(patch('bfsi.index.calculator.build_payload', side_effect=payload))
        return {'standard': standard, 'dataset': dataset, 'resolve': resolve_mock,
                'collect': collect_mock, 'clean': clean_mock, 'analyze': analyze_mock}

    def test_two_jobs_are_isolated_and_only_success_is_published(self):
        self.fixture_pipeline()
        one = self.manager.submit('BV1234567890', 20)
        self.assertEqual(self.wait(one['run_id'])['state'], 'completed')
        two = self.manager.submit('BV0987654321', 20)
        self.assertEqual(self.wait(two['run_id'])['state'], 'completed')
        self.assertNotEqual(one['run_id'], two['run_id'])
        for item in (one, two):
            payload = jobs.read_json(jobs.get_run(item['run_id']) / 'payload.json')
            self.assertEqual(payload['analysis']['run_id'], item['run_id'])
            self.assertEqual(payload['video']['video_bvid'], item['bvid'])
        self.assertEqual(jobs.read_json(self.app / 'latest.json')['run_id'], two['run_id'])

    def test_full_accepts_none_and_more_than_200_without_sample_cap(self):
        fixture = self.fixture_pipeline(count=251)
        job = self.manager.submit('BV1234567890', None, mode='full')
        status = self.wait(job['run_id'])
        self.assertEqual(status['state'], 'completed')
        self.assertIsNone(status['limit'])
        self.assertEqual(status['mode'], 'full')
        self.assertEqual(status['scope'], 'full_accessible')
        self.assertIsNone(fixture['collect'].call_args.kwargs['limit'])
        self.assertEqual(fixture['collect'].call_args.kwargs['mode'], 'full')
        payload = jobs.read_json(jobs.get_run(job['run_id']) / 'payload.json')
        self.assertEqual(payload['analysis']['unique_comments'], 251)
        self.assertIsNone(payload['analysis']['requested_limit'])
        self.assertEqual(payload['analysis']['scope'], 'full_accessible')
        second = self.manager.submit('BV0987654321', 5000, mode='full')
        self.assertEqual(self.wait(second['run_id'])['state'], 'completed')
        self.assertIsNone(jobs.read_status(second['run_id'])['limit'])

    def test_partial_completed_publishes_all_collected_comment_analysis(self):
        self.fixture_pipeline(count=3, partial_result=True)
        job = self.manager.submit('BV1234567890', None, mode='full')
        status = self.wait(job['run_id'])
        self.assertEqual(status['state'], 'partial_completed')
        self.assertEqual(status['scope'], 'partial')
        self.assertEqual(status['warnings'], ['one page unavailable'])
        payload = jobs.read_json(jobs.get_run(job['run_id']) / 'payload.json')
        self.assertEqual(payload['analysis']['scope'], 'partial')
        self.assertEqual(payload['analysis']['analyzed_comments'], 3)
        self.assertTrue(payload['analysis']['semantic_complete'])
        self.assertEqual(jobs.read_json(self.app / 'latest.json')['run_id'], job['run_id'])

    def test_paused_job_has_no_payload_and_resumes_same_frozen_run(self):
        fixture = self.fixture_pipeline(count=2)
        fixture['analyze'].side_effect = [CooperativePause('isolated pause', reason='user'), fixture['dataset']]
        job = self.manager.submit('BV1234567890', None, mode='full')
        status = self.wait(job['run_id'])
        self.assertEqual(status['state'], 'paused')
        self.assertEqual(status['resume_stage'], 'semantic')
        folder = jobs.get_run(job['run_id'])
        standard_before = (folder / 'standard_comments.json').read_bytes()
        self.assertFalse((folder / 'payload.json').exists())
        self.assertFalse((self.app / 'latest.json').exists())
        self.assertEqual(self.manager.resume(job['run_id'])['run_id'], job['run_id'])
        self.assertEqual(self.wait(job['run_id'])['state'], 'completed')
        self.assertEqual((folder / 'standard_comments.json').read_bytes(), standard_before)
        self.assertEqual(fixture['resolve'].call_count, 1)
        self.assertEqual(fixture['collect'].call_count, 1)
        self.assertEqual(fixture['analyze'].call_count, 2)
        self.assertEqual(len(list((self.app / 'runs').iterdir())), 1)
        with self.assertRaises(ValueError):
            self.manager.resume(job['run_id'])

    def test_failure_retains_response_and_cannot_replace_latest(self):
        from bfsi.collection.collector import CollectionError
        jobs.save_json(self.app / 'latest.json', {'run_id': 'previous-success'})
        with patch('bfsi.collection.collector.resolve_video', side_effect=CollectionError('HTTP 412', raw=b'blocked', status=412, restricted=True)):
            job = self.manager.submit('BV1234567890', 20)
            status = self.wait(job['run_id'])
        self.assertEqual(status['state'], 'failed')
        self.assertEqual(status['failed_stage'], 'resolve')
        self.assertFalse((jobs.get_run(job['run_id']) / 'payload.json').exists())
        self.assertEqual((jobs.get_run(job['run_id']) / 'failed_http_response.bin').read_bytes(), b'blocked')
        self.assertEqual(jobs.read_json(self.app / 'latest.json')['run_id'], 'previous-success')

    def test_resolve_retry_keeps_each_failed_response(self):
        from bfsi.collection.collector import CollectionError
        with patch('bfsi.collection.collector.resolve_video', side_effect=[CollectionError('first', raw=b'first'), CollectionError('second', raw=b'second')]):
            one=self.manager.submit('BV1234567890',20)
            self.assertEqual(self.wait(one['run_id'])['state'],'failed')
            self.manager.resume(one['run_id'])
            self.assertEqual(self.wait(one['run_id'])['state'],'failed')
        folder=jobs.get_run(one['run_id'])
        self.assertEqual({p.read_bytes() for p in folder.glob('failed_http_response*.bin')},{b'first',b'second'})
        self.assertEqual(len(list(folder.glob('failure_*.json'))),2)

    def test_invalid_input_mode_and_quick_limit_have_no_job(self):
        for value, limit, mode in [('not-a-bv', 20, 'quick'), ('BV1234567890', 201, 'quick'),
                                    ('BV1234567890', None, 'quick'), ('BV1234567890', 20, 'other')]:
            with self.subTest(value=value, limit=limit, mode=mode), self.assertRaises(ValueError):
                self.manager.submit(value, limit, mode)
        self.assertEqual(list((self.app / 'runs').iterdir()), [])

    def test_unprocessed_duplicate_or_foreign_semantic_ids_rejected(self):
        self.fixture_pipeline(count=2)
        for records in ([], [{'comment_id': '900101'}],
                        [{'comment_id': '900101'}, {'comment_id': '900101'}],
                        [{'comment_id': '900101'}, {'comment_id': 'other-video'}]):
            with self.subTest(records=records), patch('bfsi.semantic.runner.analyze', return_value={'records': records}):
                job = self.manager.submit('BV1234567890', None, mode='full')
                status = self.wait(job['run_id'])
                self.assertEqual(status['state'], 'failed')
                self.assertIn('未处理或重复', status['error'])
                self.assertFalse((jobs.get_run(job['run_id']) / 'payload.json').exists())
                self.assertFalse((self.app / 'latest.json').exists())

    def test_no_directional_signal_is_explicitly_reported(self):
        self.fixture_pipeline()
        with patch('bfsi.index.calculator.build_payload', return_value={'video': {'video_oid': '900001', 'comment_count': 1},
                    'headline': {'bfsi': 50, 'no_directional_signal': True}, 'analysis': {}}):
            job = self.manager.submit('BV1234567890', 20)
            status = self.wait(job['run_id'])
        self.assertEqual(status['state'], 'completed')
        self.assertIn('暂无有效方向信号', status['message'])
        self.assertFalse(jobs.read_json(jobs.get_run(job['run_id']) / 'payload.json')['analysis']['directional_signal_available'])

    def test_active_worker_rejects_second_submit(self):
        entered, release = threading.Event(), threading.Event()
        def blocked(bv):
            entered.set()
            release.wait(3)
            raise ValueError('isolated stop')
        with patch('bfsi.collection.collector.resolve_video', side_effect=blocked):
            one = self.manager.submit('BV1234567890', 20)
            self.assertTrue(entered.wait(2))
            with self.assertRaises(RuntimeError):
                self.manager.submit('BV0987654321', 20)
            release.set()
            self.wait(one['run_id'])

    def test_restart_marks_unfinished_job_paused_without_result(self):
        run_id = '20260101T000000Z_12345678'
        jobs.save_json(jobs.get_run(run_id) / 'status.json', {'state': 'running', 'stage': 'semantic', 'run_id': run_id})
        other = jobs.Jobs()
        try:
            status = jobs.read_status(run_id)
            self.assertEqual(status['state'], 'paused')
            self.assertEqual(status['resume_stage'], 'semantic')
            self.assertEqual(status['pause_reason'], 'restart')
            self.assertFalse((jobs.get_run(run_id) / 'payload.json').exists())
        finally:
            other.shutdown()

    def test_shutdown_does_not_rewrite_already_published_success(self):
        # Reproduce the interval after terminal status is saved but before the
        # worker's finally block clears active. No background work is necessary.
        run_id = '20260101T000000Z_12345678'
        jobs.save_json(jobs.get_run(run_id) / 'status.json', {'state': 'completed', 'stage': 'completed', 'run_id': run_id})
        self.manager.active = run_id
        try:
            self.manager.shutdown()
            self.assertEqual(jobs.read_status(run_id)['state'], 'completed')
        finally:
            self.manager.active = None


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.stack = ExitStack()
        for module, key, value in ((jobs, 'APP', self.root), (jobs, 'RUNS', self.root / 'runs'),
                                    (server, 'APP', self.root), (server, 'RUNS', self.root / 'runs')):
            self.stack.enter_context(patch.object(module, key, value))
        class Quiet(server.Handler):
            def log_message(self, *args): pass
        class FakeJobs:
            active = None
            def __init__(self): self.calls = []
            def submit(self, value, limit=100, mode='quick'):
                self.calls.append(('submit', value, limit, mode))
                if value == 'bad': raise ValueError('invalid BV')
                return {'run_id': '20260101T000000Z_12345678', 'state': 'queued', 'mode': mode}
            def pause(self, run_id):
                self.calls.append(('pause', run_id))
                return {'run_id': run_id, 'state': 'pausing'}
            def resume(self, run_id):
                self.calls.append(('resume', run_id))
                return {'run_id': run_id, 'state': 'queued'}
        self.fake = FakeJobs()
        self.stack.enter_context(patch.object(server, 'JOBS', self.fake))
        self.handler = Quiet
        self.httpd = SimpleNamespace(server_port=8787, server_name='127.0.0.1')

    def tearDown(self):
        self.stack.close()
        self.temp.cleanup()

    def request(self, path, body=None, headers=None, method=None):
        actual_headers = dict(headers or {})
        if body is not None and not isinstance(body, bytes):
            body = json.dumps(body).encode()
            actual_headers.setdefault('Content-Type', 'application/json')
        # Feed a real HTTP wire request through BaseHTTPRequestHandler without
        # binding a socket or making any network request. API code is unchanged.
        actual_headers.setdefault('Host', '127.0.0.1:8787')
        if body is not None:
            actual_headers['Content-Length'] = str(len(body))
        verb = method or ('POST' if body is not None else 'GET')
        wire = (verb + ' ' + path + ' HTTP/1.0\r\n' +
                ''.join(key + ': ' + value + '\r\n' for key, value in actual_headers.items()) + '\r\n').encode() + (body or b'')
        class Socket:
            def __init__(self): self.output = bytearray()
            def makefile(self, *args, **kwargs): return io.BytesIO(wire)
            def sendall(self, value): self.output.extend(value)
        socket = Socket()
        self.handler(socket, ('127.0.0.1', 12345), self.httpd, directory=str(self.root))
        head, _, response_body = bytes(socket.output).partition(b'\r\n\r\n')
        lines = head.decode('iso-8859-1').split('\r\n')
        headers = {line.split(':', 1)[0]: line.split(':', 1)[1].strip() for line in lines[1:] if ':' in line}
        return int(lines[0].split()[1]), headers, response_body

    def persist(self, run_id, state):
        folder = jobs.get_run(run_id)
        payload = {'video': {'video_bvid': 'BV1234567890' if run_id.endswith('12345678') else 'BV0987654321'},
                   'analysis': {'run_id': run_id}}
        jobs.save_json(folder / 'status.json', {'run_id': run_id, 'state': state,
                                              'bvid': payload['video']['video_bvid']})
        jobs.save_json(folder / 'payload.json', payload)
        return folder, payload

    def test_local_api_input_origin_host_and_missing_result(self):
        self.assertEqual(self.request('/api/health')[0], 200)
        self.assertEqual(self.request('/api/health', headers={'Host': 'evil.example'})[0], 403)
        self.assertEqual(self.request('/api/health', headers={'Host': 'evil.example'}, method='HEAD')[0], 403)
        self.assertEqual(self.request('/api/analyze', {'input': 'BV1234567890'}, {'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.request('/api/analyze', {'input': 'BV1234567890'}, {'Sec-Fetch-Site': 'cross-site'})[0], 403)
        self.assertEqual(self.request('/api/analyze', {'input': 'bad'})[0], 400)
        self.assertEqual(self.request('/api/not-real')[0], 404)
        self.assertEqual(self.request('/api/runs/20260101T000000Z_12345678/result')[0], 404)
        self.assertEqual(self.request('/api/runs/invalid/result')[0], 400)

    def test_full_submission_and_pause_resume_mode_forwarding(self):
        status, _, body = self.request('/api/analyze', {'input': 'BV1234567890', 'mode': 'full', 'limit': None})
        self.assertEqual(status, 202)
        self.assertEqual(json.loads(body)['mode'], 'full')
        self.assertEqual(self.fake.calls[-1], ('submit', 'BV1234567890', None, 'full'))
        run_id = '20260101T000000Z_12345678'
        self.assertEqual(self.request(f'/api/runs/{run_id}/pause', {})[0], 202)
        self.assertEqual(self.request(f'/api/runs/{run_id}/resume', {})[0], 202)
        self.assertEqual(self.fake.calls[-2:], [('pause', run_id), ('resume', run_id)])

    def test_export_and_poster_reject_unfinished_and_failed_runs(self):
        run_id = '20260101T000000Z_12345678'
        with patch('bfsi.reporting.poster.export_poster') as export:
            for state in ('queued', 'running', 'pausing', 'paused', 'failed'):
                with self.subTest(state=state):
                    self.persist(run_id, state)
                    self.assertEqual(self.request(f'/api/runs/{run_id}/result')[0], 409)
                    self.assertEqual(self.request(f'/api/runs/{run_id}/export', {})[0], 409)
                    self.assertEqual(self.request(f'/api/runs/{run_id}/poster')[0], 409)
            export.assert_not_called()

    def test_wrong_payload_run_or_bvid_rejected_before_result_and_export(self):
        run_id = '20260101T000000Z_12345678'
        with patch('bfsi.reporting.poster.export_poster') as export:
            for mismatch in ('run_id', 'bvid'):
                folder, payload = self.persist(run_id, 'completed')
                if mismatch == 'run_id':
                    payload['analysis']['run_id'] = '20260101T000001Z_87654321'
                else:
                    payload['video']['video_bvid'] = 'BV0987654321'
                jobs.save_json(folder / 'payload.json', payload)
                with self.subTest(mismatch=mismatch):
                    self.assertEqual(self.request(f'/api/runs/{run_id}/result')[0], 400)
                    self.assertEqual(self.request(f'/api/runs/{run_id}/export', {})[0], 400)
                    self.assertEqual(self.request(f'/api/runs/{run_id}/poster')[0], 400)
            export.assert_not_called()

    def test_two_successful_run_export_and_download_are_isolated(self):
        ids = ['20260101T000000Z_12345678', '20260101T000001Z_87654321']
        for run_id, state in zip(ids, ('completed', 'partial_completed')):
            self.persist(run_id, state)
        calls = []
        def export(payload, folder):
            run_id = payload['analysis']['run_id']
            self.assertEqual(folder, jobs.get_run(run_id))
            png = folder / 'exports/result_poster.png'
            png.parent.mkdir(exist_ok=True)
            png.write_bytes(b'PNG-' + run_id.encode())
            calls.append(run_id)
            return {'path': str(png), 'filename': run_id + '.png', 'run_id': run_id, 'width': 1080, 'height': 2000}
        with patch('bfsi.reporting.poster.export_poster', side_effect=export):
            for run_id in ids:
                code, _, body = self.request(f'/api/runs/{run_id}/result')
                self.assertEqual(code, 200)
                self.assertEqual(json.loads(body)['analysis']['run_id'], run_id)
                code, _, body = self.request(f'/api/runs/{run_id}/export', {})
                self.assertEqual(code, 200)
                self.assertEqual(json.loads(body)['run_id'], run_id)
                self.assertEqual(json.loads(body)['preview_url'], f'/api/runs/{run_id}/poster')
                code, headers, body = self.request(f'/api/runs/{run_id}/poster')
                self.assertEqual(code, 200)
                self.assertEqual(headers['Content-Type'], 'image/png')
                self.assertTrue(headers['Content-Disposition'].startswith('inline;'))
                self.assertEqual(body, b'PNG-' + run_id.encode())
                code, headers, body = self.request(f'/api/runs/{run_id}/poster?download=1')
                self.assertEqual(code, 200)
                self.assertTrue(headers['Content-Disposition'].startswith('attachment;'))
                self.assertEqual(body, b'PNG-' + run_id.encode())
        self.assertEqual(calls, [ids[0]] * 3 + [ids[1]] * 3)

    def test_csrf_is_rejected_for_export_pause_and_resume(self):
        run_id = '20260101T000000Z_12345678'
        self.persist(run_id, 'completed')
        with patch('bfsi.reporting.poster.export_poster') as export:
            for action in ('export', 'pause', 'resume'):
                for headers in ({'Origin': 'https://evil.example'}, {'Sec-Fetch-Site': 'cross-site'}, {'Host': 'evil.example'}):
                    with self.subTest(action=action, headers=headers):
                        self.assertEqual(self.request(f'/api/runs/{run_id}/{action}', {}, headers)[0], 403)
            export.assert_not_called()
        self.assertEqual(self.fake.calls, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
