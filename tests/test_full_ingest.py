"""Full collection, durable journals and pause/resume beyond the old quick boundaries."""
import sys
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import hashlib
import json
import tempfile
import unittest
from collections import Counter
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse
from test_ingest import collector, cleaner, FakeLegacy, LEGACY, STAMP, video, node, encoded


class FullCollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        self.run=self.root/'runs/full-test'
        for item in (patch.object(collector,'PROJECT',self.root),patch.object(collector,'RUNS',self.root/'runs'),patch.object(cleaner,'PROJECT',self.root),patch.object(cleaner,'RUNS',self.root/'runs'),
                patch.object(collector,'_legacy',return_value=FakeLegacy()),
                patch.object(collector.time,'sleep')):
            item.start();self.addCleanup(item.stop)
        self.calls=[]
        self.root_id=1000000
        self.pin_id=2000000
        self.ordinary=[node(self.root_id+i,rcount=3 if i==0 else 0) for i in range(205)]
        self.pin=node(self.pin_id,rcount=2,pinned=True)

    def request(self,url):
        query=parse_qs(urlparse(url).query)
        if '/wbi/main?' in url:
            offset=json.loads(query['pagination_str'][0])['offset']
            page=int(offset or 0)
            self.calls.append(('main',page))
            begin,end=page*20,(page+1)*20
            final=end>=len(self.ordinary)
            cursor={'mode':2,'is_end':final,'all_count':211}
            if not final:cursor['pagination_reply']={'next_offset':str(page+1)}
            return encoded({'replies':self.ordinary[begin:end],'top':{'upper':self.pin},
                'top_replies':[self.pin],'cursor':cursor}),STAMP
        rid,pn=int(query['root'][0]),int(query['pn'][0])
        self.calls.append(('reply',rid,pn))
        if rid==self.root_id:
            replies=([node(3000001,rid,rid),node(3000003,rid,3000002)] if pn==1 else [node(3000002,rid,rid)])
            return encoded({'root':self.ordinary[0],'replies':replies,
                'page':{'num':pn,'size':2,'count':3}}),STAMP
        replies=[node(4000000+pn,rid,rid if pn==1 else 4000001)]
        return encoded({'root':self.pin,'replies':replies,'page':{'num':pn,'size':1,'count':2}}),STAMP

    def stored_video(self):
        return json.loads((self.run/'video.json').read_text())

    def checkpoint(self):
        return json.loads((self.run/'collection_checkpoint.json').read_text())

    def raw_fingerprints(self):
        return {str(p.relative_to(self.run)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (self.run/'raw/pages').rglob('response.json')}

    def test_full_collects_211_without_quick_caps_and_passes_frozen_standard(self):
        with patch.object(collector,'_request',side_effect=self.request),patch.object(collector,'MAX_REQUESTS',1),patch.object(collector,'MAX_PAGES_PER_ROOT',1):
            manifest,summary=collector.collect(video(),self.run,limit=None,mode='full')
        self.assertEqual(summary['selected_comments'],211)
        self.assertEqual(summary['raw_unique_comments'],211)
        self.assertEqual(len(self.calls),15)
        self.assertEqual(len(set(self.calls)),15)
        self.assertTrue(summary['complete'])
        self.assertFalse(summary['sample'])
        self.assertIsNone(summary['analysis_comment_budget'])
        self.assertTrue(summary['main_pagination_complete'])
        self.assertTrue(all(x['complete'] for x in summary['reply_trees']))
        standard=cleaner.clean(video(),manifest,self.run)
        self.assertEqual(len(standard['comments']),211)
        self.assertTrue(json.loads((self.run/'cleaning_summary.json').read_text())['full_data_integrity'])

    def test_pause_after_commit_has_no_publication_and_resume_never_refetches(self):
        raised=False
        def control():
            nonlocal raised
            if not raised and self.checkpoint().get('committed_pages',0)>=1:
                raised=True
                raise collector.CooperativePause('user pause')
        with patch.object(collector,'_request',side_effect=self.request):
            with self.assertRaises(collector.CooperativePause):
                collector.collect(video(),self.run,mode='full',control=control)
        self.assertEqual(self.checkpoint()['status'],'paused')
        self.assertFalse((self.run/'collection_summary.json').exists())
        self.assertFalse((self.run/'source_file_manifest.json').exists())
        before=self.raw_fingerprints()
        with patch.object(collector,'_request',side_effect=self.request):
            _,summary=collector.collect(self.stored_video(),self.run,mode='full')
        self.assertTrue(summary['complete'])
        self.assertEqual(summary['selected_comments'],211)
        self.assertEqual(len(self.calls),len(set(self.calls)))
        for path,digest in before.items():self.assertEqual(self.raw_fingerprints()[path],digest)

    def test_pause_mid_reply_resumes_exact_unfinished_page(self):
        def control():
            tree=self.checkpoint().get('roots',{}).get(str(self.root_id),{})
            if tree.get('pages')==1:
                raise collector.CooperativePause('pause after first reply page')
        with patch.object(collector,'_request',side_effect=self.request):
            with self.assertRaises(collector.CooperativePause):
                collector.collect(video(),self.run,mode='full',control=control)
        checkpoint=self.checkpoint()
        self.assertEqual(checkpoint['roots'][str(self.root_id)]['next_page'],2)
        self.assertFalse(checkpoint['roots'][str(self.root_id)]['complete'])
        old=len(self.calls)
        with patch.object(collector,'_request',side_effect=self.request):
            _,summary=collector.collect(self.stored_video(),self.run,mode='full')
        self.assertEqual(self.calls[old],('reply',self.root_id,2))
        self.assertEqual(len(self.calls),15)
        self.assertTrue(summary['complete'])

    def test_multiple_bursts_cool_down_and_automatically_continue_to_actual_end(self):
        clock=[1000.0]
        sleeps=[]
        def sleep(seconds):
            sleeps.append(seconds)
            clock[0]+=seconds
        progress=[]
        with patch.object(collector,'_request',side_effect=self.request),patch.object(collector,'FULL_BURST_REQUESTS',2), \
                patch.object(collector,'FULL_COOLDOWN_SECONDS',1),patch.object(collector,'REQUEST_INTERVAL',0), \
                patch.object(collector.time,'time',side_effect=lambda:clock[0]),patch.object(collector.time,'sleep',side_effect=sleep):
            _,summary=collector.collect(video(),self.run,mode='full',progress=progress.append)
        self.assertTrue(summary['complete'])
        self.assertEqual(summary['selected_comments'],211)
        self.assertEqual(len(self.calls),15)
        self.assertEqual(len(set(self.calls)),15)
        self.assertEqual(summary['automatic_cooldowns'],7)
        self.assertTrue(any(event.get('waiting_for')=='rate_cooldown' for event in progress))
        self.assertLessEqual(max(sleeps),0.25)

    def test_user_can_pause_during_cooldown_and_resume_without_refetch(self):
        clock=[1000.0]
        def sleep(seconds):clock[0]+=seconds
        paused=False
        def control():
            nonlocal paused
            checkpoint=self.checkpoint()
            deadline=checkpoint.get('cooldown_until_epoch')
            if not paused and deadline is not None and clock[0]>=deadline-0.5:
                paused=True
                raise collector.CooperativePause('冷却中手动暂停',reason='user')
        with patch.object(collector,'_request',side_effect=self.request),patch.object(collector,'FULL_BURST_REQUESTS',2), \
                patch.object(collector,'FULL_COOLDOWN_SECONDS',1),patch.object(collector,'REQUEST_INTERVAL',0), \
                patch.object(collector.time,'time',side_effect=lambda:clock[0]),patch.object(collector.time,'sleep',side_effect=sleep):
            with self.assertRaises(collector.CooperativePause):
                collector.collect(video(),self.run,mode='full',control=control)
            self.assertEqual(self.checkpoint()['status'],'paused')
            self.assertEqual(self.checkpoint()['pause_reason'],'user')
            self.assertEqual(len(self.calls),2)
            self.assertFalse((self.run/'collection_summary.json').exists())
            before=self.raw_fingerprints()
            _,summary=collector.collect(self.stored_video(),self.run,mode='full')
        self.assertTrue(summary['complete'])
        self.assertEqual(len(self.calls),15)
        self.assertEqual(len(set(self.calls)),15)
        for path,digest in before.items():self.assertEqual(self.raw_fingerprints()[path],digest)

    def test_external_quota_pause_is_still_propagated(self):
        def control():
            if self.checkpoint().get('committed_pages',0)>=1:
                raise collector.CooperativePause('实际配额不足',reason='quota')
        with patch.object(collector,'_request',side_effect=self.request):
            with self.assertRaises(collector.CooperativePause) as caught:
                collector.collect(video(),self.run,mode='full',control=control)
        self.assertEqual(caught.exception.reason,'quota')
        self.assertEqual(self.checkpoint()['pause_reason'],'quota')
        self.assertFalse((self.run/'collection_summary.json').exists())

    def test_transport_partial_before_publication_can_resume_incomplete_tree(self):
        failed=False
        def request(url):
            nonlocal failed
            q=parse_qs(urlparse(url).query)
            if q.get('root')==[str(self.root_id)] and q.get('pn')==['2'] and not failed:
                failed=True
                raise collector.CollectionError('temporary outage')
            return self.request(url)
        with patch.object(collector,'_request',side_effect=request):
            _,partial=collector.collect(video(),self.run,mode='full')
        self.assertTrue(partial['partial'])
        self.assertIn('temporary outage',partial['partial_reasons'])
        self.assertTrue(partial['main_pagination_complete'])
        with patch.object(collector,'_request',side_effect=self.request):
            _,summary=collector.collect(self.stored_video(),self.run,mode='full')
        self.assertTrue(summary['complete'])
        self.assertEqual(len(self.calls),15)
        self.assertEqual(len(set(self.calls)),15)
        self.assertEqual(len(summary['failure_history']),1)

    def test_resume_rejects_changed_saved_raw_before_any_network(self):
        def control():
            if self.checkpoint().get('committed_pages',0)>=1:
                raise collector.CooperativePause()
        with patch.object(collector,'_request',side_effect=self.request):
            with self.assertRaises(collector.CooperativePause):
                collector.collect(video(),self.run,mode='full',control=control)
        raw=next((self.run/'raw/pages').rglob('response.json'))
        raw.write_bytes(raw.read_bytes()+b' ')
        with patch.object(collector,'_request') as network:
            with self.assertRaisesRegex(collector.CollectionError,'SHA'):
                collector.collect(self.stored_video(),self.run,mode='full')
            network.assert_not_called()

    def test_committed_page_recovers_even_if_checkpoint_cursor_is_stale(self):
        def control():
            if self.checkpoint().get('committed_pages',0)>=1:
                raise collector.CooperativePause()
        with patch.object(collector,'_request',side_effect=self.request):
            with self.assertRaises(collector.CooperativePause):
                collector.collect(video(),self.run,mode='full',control=control)
        stale=self.checkpoint()
        stale.update(main={'next_batch':1,'next_offset':'','complete':False,'offsets':[]},roots={},root_order=[],files=[],committed_pages=0)
        (self.run/'collection_checkpoint.json').write_text(json.dumps(stale))
        with patch.object(collector,'_request',side_effect=self.request):
            _,summary=collector.collect(self.stored_video(),self.run,mode='full')
        self.assertTrue(summary['complete'])
        self.assertEqual(Counter(self.calls)[('main',0)],1)

    def test_full_reply_tree_continues_beyond_twenty_pages(self):
        root=node(5555555,rcount=425)
        calls=[]
        def request(url):
            q=parse_qs(urlparse(url).query)
            if '/wbi/main?' in url:
                calls.append(('main',))
                return encoded({'replies':[root],'cursor':{'mode':2,'is_end':True}}),STAMP
            pn=int(q['pn'][0]);calls.append(('reply',pn))
            values=[node(6000000+i,5555555,5555555) for i in range((pn-1)*20,min(pn*20,425))]
            return encoded({'root':root,'replies':values,'page':{'num':pn,'size':20,'count':425}}),STAMP
        with patch.object(collector,'_request',side_effect=request),patch.object(collector,'MAX_PAGES_PER_ROOT',1):
            manifest,summary=collector.collect(video(),self.run,mode='full',limit=None)
        self.assertEqual(summary['selected_comments'],426)
        self.assertTrue(summary['complete'])
        self.assertEqual(calls[-1],('reply',22))
        self.assertEqual(len(cleaner.clean(video(),manifest,self.run)['comments']),426)

    def test_unmapped_raw_position_is_visible_and_never_full_integrity(self):
        root=node(7000000)
        def request(url):
            return encoded({'replies':[root],'hots':[node(7000001)],'cursor':{'mode':2,'is_end':True}}),STAMP
        with patch.object(collector,'_request',side_effect=request):
            manifest,summary=collector.collect(video(),self.run,mode='full')
        self.assertTrue(summary['complete']) # API pagination did finish.
        cleaner.clean(video(),manifest,self.run)
        report=json.loads((self.run/'cleaning_summary.json').read_text())
        self.assertFalse(report['full_data_integrity'])
        self.assertTrue(report['partial'])
        self.assertTrue(report['position_issues'])
        self.assertTrue(report['warnings'])

    def test_completed_run_is_read_only_and_never_refetched(self):
        with patch.object(collector,'_request',side_effect=self.request):
            manifest,summary=collector.collect(video(),self.run,mode='full')
        before={str(p.relative_to(self.run)):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.run.rglob('*') if p.is_file()}
        with patch.object(collector,'_request') as network:
            again,new_summary=collector.collect(self.stored_video(),self.run,mode='full')
            network.assert_not_called()
        after={str(p.relative_to(self.run)):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.run.rglob('*') if p.is_file()}
        self.assertEqual(manifest,again)
        self.assertEqual(summary,new_summary)
        self.assertEqual(before,after)

    def test_full_exhausts_main_endpoint_before_attempting_reply_trees(self):
        with patch.object(collector,'_request',side_effect=self.request):
            _,summary=collector.collect(video(),self.run,mode='full')
        self.assertTrue(summary['main_pagination_complete'])
        self.assertEqual(self.calls[:11],[('main',page) for page in range(11)])
        self.assertTrue(all(call[0]=='reply' for call in self.calls[11:]))

    def test_full_reply_restriction_keeps_all_successful_main_pages_and_stops(self):
        def request(url):
            if '/wbi/main?' not in url:
                self.calls.append(('restricted_reply',))
                raise collector.CollectionError('B站访问校验限制（code=-352）。',status=-352,restricted=True)
            return self.request(url)
        with patch.object(collector,'_request',side_effect=request):
            _,summary=collector.collect(video(),self.run,mode='full')
        self.assertTrue(summary['main_pagination_complete'])
        self.assertEqual(summary['selected_comments'],206)
        self.assertEqual(summary['raw_response_pages'],11)
        self.assertTrue(summary['partial'])
        self.assertFalse(summary['complete'])
        self.assertEqual(self.calls[:11],[('main',page) for page in range(11)])
        self.assertEqual(self.calls[11:],[('restricted_reply',)])
        self.assertTrue(summary['failure_history'][-1]['restricted'])

    def test_conflicting_raw_comment_is_reported_as_unmapped_not_full(self):
        def request(url):
            q=parse_qs(urlparse(url).query)
            offset=json.loads(q['pagination_str'][0])['offset']
            changed=node(8100000)
            if offset=='':
                return encoded({'replies':[changed,node(8100001)],'cursor':{'mode':2,'is_end':False,
                    'pagination_reply':{'next_offset':'next'}}}),STAMP
            changed['content']['message']='changed core text'
            return encoded({'replies':[changed],'cursor':{'mode':2,'is_end':True}}),STAMP
        with patch.object(collector,'_request',side_effect=request):
            manifest,_=collector.collect(video(),self.run,mode='full')
        standard=cleaner.clean(video(),manifest,self.run)
        report=json.loads((self.run/'cleaning_summary.json').read_text())
        self.assertEqual(len(standard['comments']),1)
        self.assertEqual(report['unmapped_selected_comment_ids'],['8100000'])
        self.assertTrue(report['quarantine'])
        self.assertTrue(report['partial'])
        self.assertFalse(report['full_data_integrity'])

    def test_repeated_reply_page_explicitly_partial_and_preserves_failure_raw(self):
        root=node(8200000,rcount=40)
        def request(url):
            if '/wbi/main?' in url:
                return encoded({'replies':[root],'cursor':{'mode':2,'is_end':True}}),STAMP
            pn=int(parse_qs(urlparse(url).query)['pn'][0])
            return encoded({'root':root,'replies':[node(8200001,8200000,8200000)],
                'page':{'num':pn,'size':20,'count':40}}),STAMP
        with patch.object(collector,'_request',side_effect=request):
            _,summary=collector.collect(video(),self.run,mode='full')
        self.assertTrue(summary['partial'])
        self.assertFalse(summary['complete'])
        self.assertIn('回复分页内容重复，已停止。',summary['partial_reasons'])
        self.assertTrue(list((self.run/'raw').glob('failure_*.response')))
        self.assertEqual(summary['raw_response_pages'],2)


if __name__=='__main__':
    unittest.main(verbosity=2)
