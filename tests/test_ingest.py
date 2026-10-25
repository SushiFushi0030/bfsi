"""Offline production ingest tests: raw evidence, budget, closure and real frozen contract."""
import sys
sys.dont_write_bytecode = True
import hashlib
import gzip
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

from bfsi.collection import collector
from bfsi.transform import cleaner

LEGACY = Path(__file__).resolve().parents[1]

BVID = 'BV1234567890'
STAMP = '2026-10-23T01:02:03.123456Z'


def node(cid, root=0, parent=0, rcount=0, pinned=False):
    value = {'rpid':cid,'oid':900001,'root':root,'parent':parent,'ctime':1700000000,
        'like':1,'rcount':rcount,'content':{'message':f'  评论 {cid} 😀 &amp;\n','members':[],'jump_url':{}},'replies':None}
    if pinned:
        value['reply_control'] = {'is_up_top':True}
    return value


def encoded(data):
    return json.dumps({'code':0,'data':data},ensure_ascii=False).encode()


def video():
    raw = encoded({'bvid':BVID,'aid':900001,'title':'合成黄金观点视频','desc':'合成测试背景'})
    return {'bvid':BVID,'oid':'900001','title':'合成黄金观点视频','url':f'https://www.bilibili.com/video/{BVID}/',
        '_resolution_raw':raw.decode(),'resolution_evidence':{'endpoint':'/x/web-interface/view',
        'requested_bvid':BVID,'response_bvid':BVID,'response_oid':'900001','received_at':STAMP,
        'response_sha256':hashlib.sha256(raw).hexdigest()}}


def page_video(raw=None):
    if raw is None:
        raw=('<html><script>window.__INITIAL_STATE__='+json.dumps({'aid':900001,'bvid':BVID,
            'videoData':{'aid':900001,'bvid':BVID,'title':'合成黄金观点视频','desc':'背景','stat':{'reply':5}}},ensure_ascii=False)
            +';(function(){})();</script></html>').encode()
    result=video()
    result['_resolution_raw']=raw.decode()
    result['resolution_evidence'].update(source='public_video_page',endpoint=f'/zh-Hans/video/{BVID}/',
        url=f'https://www.bilibili.com/zh-Hans/video/{BVID}/',response_sha256=hashlib.sha256(raw).hexdigest())
    return result


class FakeLegacy:
    def get_mixin_key(self):
        return 'test-key'
    def sign(self, params, mixin):
        return dict(params, w_rid='test-signature', wts=1)


class ProductionIngestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.run = self.root / 'runs/synthetic-test'
        self.patches = [patch.object(collector,'PROJECT',self.root),patch.object(collector,'RUNS',self.root/'runs'),patch.object(cleaner,'PROJECT',self.root),patch.object(cleaner,'RUNS',self.root/'runs'),
            patch.object(collector,'_legacy',return_value=FakeLegacy()),
            patch.object(collector.time,'sleep')]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.calls=[]

    def request(self, url):
        self.calls.append(url)
        query = parse_qs(urlparse(url).query)
        root1,pin = node(900101,rcount=2),node(900201,rcount=1,pinned=True)
        if '/wbi/main?' in url:
            return encoded({'replies':[root1],'top':{'upper':pin},'top_replies':[pin],
                'cursor':{'mode':2,'is_end':True}}),STAMP
        rid,pn=int(query['root'][0]),int(query['pn'][0])
        if rid==900101:
            # Reply-to-reply arrives before its parent; selection must await page 2.
            comments = [node(900103,900101,900102)] if pn==1 else [node(900102,900101,900101)]
            return encoded({'root':root1,'replies':comments,'page':{'num':pn,'size':1,'count':2}}),STAMP
        return encoded({'root':pin,'replies':[node(900202,900201,900201)],
            'page':{'num':1,'size':20,'count':1}}),STAMP

    def test_bv_url_validation_and_no_arbitrary_host(self):
        self.assertEqual(collector.normalize_bvid(BVID),BVID)
        self.assertEqual(collector.normalize_bvid(f'https://www.bilibili.com/video/{BVID}/?p=1'),BVID)
        self.assertEqual(collector.normalize_bvid(f'https://www.bilibili.com/zh-Hans/video/{BVID}/'),BVID)
        for value in ('https://evil.example/video/'+BVID,'BVbad', 'https://bilibili.com.evil.example/video/'+BVID):
            with self.assertRaises(ValueError):
                collector.normalize_bvid(value)

    def test_resolve_uses_public_page_mapping_and_retains_html(self):
        raw=page_video()['_resolution_raw'].encode()
        with patch.object(collector,'_request',return_value=(raw,STAMP)) as request:
            result=collector.resolve_video(BVID)
        self.assertEqual(result['oid'],'900001')
        self.assertEqual(result['title'],'合成黄金观点视频')
        self.assertEqual(result['resolution_evidence']['source'],'public_video_page')
        self.assertEqual(result['resolution_evidence']['response_sha256'],hashlib.sha256(raw).hexdigest())
        self.assertEqual(result['_resolution_raw'].encode(),raw)
        self.assertIn('/zh-Hans/video/',request.call_args[0][0])

    def test_resolve_mismatched_page_mapping_rejected(self):
        raw=page_video()['_resolution_raw'].replace('"aid": 900001','"aid": 0',1).encode()
        with patch.object(collector,'_request',return_value=(raw,STAMP)):
            with self.assertRaises(collector.CollectionError):
                collector.resolve_video(BVID)

    def test_public_page_cleaner_revalidates_real_html_evidence(self):
        current=page_video()
        with patch.object(collector,'_request',side_effect=self.request):
            manifest,_=collector.collect(current,self.run,100)
        self.assertEqual((self.run/'raw/video_page.html').read_bytes(),current['_resolution_raw'].encode())
        self.assertFalse((self.run/'raw/video_view.json').exists())
        self.assertEqual(len(cleaner.clean(current,manifest,self.run)['comments']),5)

    def test_public_page_without_ssr_or_challenge_does_not_fall_back(self):
        for raw in (b'<html>public shell</html>', '<html>安全验证 captcha</html>'.encode()):
            with patch.object(collector,'_request',return_value=(raw,STAMP)) as request:
                with self.assertRaises(collector.CollectionError):
                    collector.resolve_video(BVID)
                self.assertEqual(request.call_count,1)

    def test_collect_reply_pagination_pinned_dedupe_and_closure(self):
        progress=[]
        with patch.object(collector,'_request',side_effect=self.request):
            manifest,summary=collector.collect(video(),self.run,100,progress.append)
        self.assertEqual(len(self.calls),4)
        self.assertEqual(summary['selected_comments'],5)
        self.assertEqual(summary['raw_unique_comments'],5)
        self.assertEqual(len(summary['reply_trees']),2)
        self.assertTrue(summary['complete'])
        self.assertTrue(all(x['received_at']==STAMP for x in manifest['files']))
        self.assertTrue(progress and all(isinstance(x,dict) for x in progress))
        standard=cleaner.clean(video(),manifest,self.run)
        self.assertEqual(len(standard['comments']),5)
        self.assertEqual(sum(len(x['observations']) for x in standard['comments']),9)
        self.assertEqual(standard['ingestion_mode'],'production')
        byid={x['core']['comment_id']:x for x in standard['comments']}
        self.assertEqual(byid['900103']['core']['depth'],2)
        self.assertEqual(byid['900101']['core']['raw_text'],node(900101)['content']['message'])
        self.assertTrue(all(x['core']['video_bvid']==BVID for x in standard['comments']))
        self.assertTrue(all(o['observed_at']==STAMP and o['observed_at_precision']=='request'
            for x in standard['comments'] for o in x['observations']))
        self.assertEqual(json.loads((self.run/'cleaning_summary.json').read_text())['validation']['official_draft_2020_12_metaschema'],'passed')

    def test_analysis_budget_and_raw_evidence_are_separate(self):
        with patch.object(collector,'_request',side_effect=self.request):
            manifest,summary=collector.collect(video(),self.run,3)
        self.assertEqual(summary['selected_comments'],3)
        self.assertEqual(summary['raw_unique_comments'],5)
        self.assertTrue(summary['truncated'])
        self.assertFalse(summary['complete'])
        self.assertEqual(len(summary['reply_trees']),2) # Every selected root still gets a reply attempt.
        standard=cleaner.clean(video(),manifest,self.run)
        self.assertEqual(len(standard['comments']),3)
        ids={x['core']['comment_id'] for x in standard['comments']}
        for comment in standard['comments']:
            for key in ('root_comment_id','parent_comment_id'):
                self.assertTrue(comment['core'][key] is None or comment['core'][key] in ids)

    def test_missing_parent_reply_is_not_fabricated(self):
        root=node(900101,rcount=1)
        def fake(url):
            if '/wbi/main?' in url:
                return encoded({'replies':[root],'cursor':{'mode':2,'is_end':True}}),STAMP
            return encoded({'root':root,'replies':[node(900105,900101,999999)],'page':{'num':1,'size':20,'count':1}}),STAMP
        with patch.object(collector,'_request',side_effect=fake):
            manifest,summary=collector.collect(video(),self.run,100)
        self.assertEqual(summary['selected_comment_ids'],['900101'])
        self.assertEqual(summary['unresolved_comment_ids'],['900105'])
        self.assertTrue(summary['partial'])
        self.assertEqual(len(cleaner.clean(video(),manifest,self.run)['comments']),1)

    def test_412_has_no_retry_and_retains_response(self):
        fake=FakeLegacy()
        fake.get_bytes=lambda url: (_ for _ in ()).throw(HTTPError(url,412,'restricted',{},io.BytesIO(b'blocked')))
        with patch.object(collector,'_legacy',return_value=fake),patch.object(fake,'get_bytes',wraps=fake.get_bytes) as get:
            with self.assertRaises(collector.CollectionError) as caught:
                collector._request('https://api.bilibili.com/test')
        self.assertEqual(get.call_count,1)
        self.assertTrue(caught.exception.restricted)
        self.assertEqual(caught.exception.raw,b'blocked')

    def test_timeout_has_bounded_retries(self):
        fake=FakeLegacy()
        fake.get_bytes=lambda url: None
        with patch.object(collector,'_legacy',return_value=fake),patch.object(fake,'get_bytes',side_effect=[URLError('timeout'),TimeoutError(),b'{}']) as get:
            raw,_=collector._request('https://api.bilibili.com/test')
        self.assertEqual(raw,b'{}')
        self.assertEqual(get.call_count,3)

    def test_standard_gzip_transport_is_decoded_without_reformatting(self):
        expected=b'{"code":0, "data": {"title":"gold"}}\n'
        fake=FakeLegacy()
        fake.get_bytes=lambda url: gzip.compress(expected)
        with patch.object(collector,'_legacy',return_value=fake):
            actual,_=collector._request('https://api.bilibili.com/test')
        self.assertEqual(actual,expected)
        self.assertEqual(collector._decode_transport(expected),expected)

    def test_platform_risk_control_stops_without_fallback(self):
        raw=json.dumps({'code':-352,'message':'risk control','data':{}}).encode()
        with self.assertRaises(collector.CollectionError) as caught:
            collector._payload(raw)
        self.assertTrue(caught.exception.restricted)

    def test_raw_hash_and_video_evidence_tampering_rejected(self):
        with patch.object(collector,'_request',side_effect=self.request):
            manifest,_=collector.collect(video(),self.run,100)
        source=self.root/manifest['files'][0]['raw_file']
        source.write_bytes(source.read_bytes()+b' ')
        with self.assertRaisesRegex(ValueError,'指纹'):
            cleaner.clean(video(),manifest,self.run)
        altered=video()
        altered['oid']='900002'
        with self.assertRaisesRegex(ValueError,'映射证据'):
            cleaner.clean(altered,manifest,self.run)

    def test_failures_preserve_raw_and_do_not_publish_standard(self):
        def failure(url):
            raise collector.CollectionError('访问受限',raw=b'challenge',status=412,restricted=True)
        with patch.object(collector,'_request',side_effect=failure):
            with self.assertRaises(collector.CollectionError):
                collector.collect(video(),self.run,100)
        self.assertEqual((self.run/'raw/failure_001.response').read_bytes(),b'challenge')
        self.assertFalse((self.run/'standard_comments.json').exists())
        summary=json.loads((self.run/'collection_summary.json').read_text())
        self.assertTrue(summary['partial'])
        self.assertEqual(summary['selected_comments'],0)

    def test_history_write_path_rejected(self):
        with self.assertRaises(ValueError):
            collector.collect(video(),self.root/'task 01',100)
        with self.assertRaises(ValueError):
            cleaner.clean(video(),{},self.root/'task 03')


if __name__=='__main__':
    unittest.main(verbosity=2)
