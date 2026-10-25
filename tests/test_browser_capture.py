"""Finite offline tests only. The UI driver is a synthetic file producer, never Chrome."""
import sys
sys.dont_write_bytecode=True
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from urllib.parse import urlparse

from bfsi.collection import browser_capture as adapter
REAL_PROJECT=Path(__file__).resolve().parents[1]
collector,cleaner,Pause=adapter.load_modules(REAL_PROJECT)
BVID='BV1234567890'


def comment(cid,root=0,parent=0,rcount=0):
    return {'rpid':cid,'oid':900001,'root':root,'parent':parent,'ctime':1700000000,
        'like':1,'rcount':rcount,'content':{'message':f'评论{cid}','members':[],'jump_url':{}},'replies':None}


class BrowserCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.bridge=self.root/'mirror-work/bridge'
        self.run_id='20261023T010203Z_1234abcd';self.run=self.root/'runs'/self.run_id
        for item in (patch.object(collector,'PROJECT',self.root),patch.object(collector,'RUNS',self.root/'runs'),patch.object(cleaner,'PROJECT',self.root),patch.object(cleaner,'RUNS',self.root/'runs'),

                patch.object(adapter,'load_modules',return_value=(collector,cleaner,Pause)),
                patch.object(collector,'REQUEST_INTERVAL',0)):
            item.start();self.addCleanup(item.stop)
        self.video_file=self.root/'video_resolution.json'
        html=('<html><script>window.__INITIAL_STATE__='+json.dumps({'aid':900001,'bvid':BVID,
            'videoData':{'aid':900001,'bvid':BVID,'title':'明确合成测试视频'}})+';</script></html>').encode()
        self.video={'bvid':BVID,'oid':'900001','title':'明确合成测试视频','url':f'https://www.bilibili.com/video/{BVID}/',
            '_resolution_raw':html.decode(),'resolution_evidence':{'source':'public_video_page',
                'endpoint':f'/zh-Hans/video/{BVID}/','url':f'https://www.bilibili.com/zh-Hans/video/{BVID}/',
                'requested_bvid':BVID,'response_bvid':BVID,'response_oid':'900001',
                'received_at':adapter.now(),'response_sha256':hashlib.sha256(html).hexdigest()}}
        self.video_file.write_text(json.dumps(self.video,ensure_ascii=False,indent=1)+'\n')
        self.config=self.root/'public_wbi.json'
        self.config.write_text(json.dumps({'data':{'wbi_img':{
            'img_url':'https://i0.hdslb.com/bfs/wbi/'+'a'*32+'.png',
            'sub_url':'https://i0.hdslb.com/bfs/wbi/'+'b'*32+'.png'}}}))
        self.served=[];self.stop=threading.Event();self.driver_errors=[]
        self.addCleanup(self.stop.set)

    def start_driver(self,only_main=False,non_json=False):
        def driver():
            try:
                while not self.stop.wait(0.005):
                    for path in sorted((self.bridge/'requests').glob('*.json')):
                        request=json.loads(path.read_text())
                        if request.get('state')!='pending' or request['id'] in self.served:continue
                        is_main=urlparse(request['url']).path.endswith('/main')
                        if only_main and not is_main:continue
                        self.assertFalse((self.run/'status.json').exists())
                        data=({'replies':[comment(123,rcount=1)],'cursor':{'mode':2,'is_end':True}}
                            if is_main else {'root':comment(123,rcount=1),'replies':[comment(124,123,123)],
                                'page':{'num':1,'size':20,'count':1}})
                        text='<html>访问验证</html>' if non_json else json.dumps({'code':0,'data':data},ensure_ascii=False)
                        adapter.atomic_json(self.bridge/'responses'/path.name,{'url':request['url'],
                            'transport':adapter.TRANSPORT,'received_at':adapter.now(),'raw_text':text})
                        self.served.append(request['id'])
            except Exception as exc:self.driver_errors.append(exc)
        thread=threading.Thread(target=driver,daemon=True);thread.start()
        self.addCleanup(lambda:thread.join(timeout=1))
        return thread

    def execute(self,timeout=2):
        with redirect_stdout(io.StringIO()):
            result=adapter.run_capture(self.root,self.video_file,self.run_id,self.config,self.bridge,timeout=timeout)
        if self.driver_errors:raise self.driver_errors[0]
        return result

    def test_bridge_full_collect_clean_and_registers_only_frozen_semantic_input(self):
        self.start_driver()
        self.assertEqual(self.execute(),0)
        self.stop.set()
        status=json.loads((self.run/'status.json').read_text())
        self.assertEqual(status['state'],'paused');self.assertEqual(status['resume_stage'],'semantic')
        self.assertEqual(status['collected'],2)
        self.assertEqual(status['raw_unique_comments'],2)
        self.assertEqual(status['raw_response_pages'],2)
        self.assertEqual(status['main_pages'],1)
        standard=json.loads((self.run/'standard_comments.json').read_text())
        self.assertEqual(len(standard['comments']),2)
        collection=json.loads((self.run/'collection_summary.json').read_text())
        self.assertIn('不是原始 HTTP wire bytes',collection['raw_format_note'])
        self.assertFalse(collection['wire_bytes_preserved'])
        receipts=list((self.run/'browser_receipts').glob('*.json'))
        self.assertEqual(len(receipts),2)
        for path in receipts:
            receipt=json.loads(path.read_text());raw=(path.parent/receipt['body_file']).read_bytes()
            self.assertEqual(receipt['body_sha256'],hashlib.sha256(raw).hexdigest())
        self.assertEqual((self.run/'browser_video_resolution_original.json').read_bytes(),self.video_file.read_bytes())
        self.assertFalse((self.run/'semantic').exists())
        with self.assertRaisesRegex(ValueError,'标准输入已冻结'):self.execute()

    def test_bridge_timeout_pauses_without_job_registration(self):
        self.assertEqual(self.execute(timeout=0.03),75)
        self.assertFalse((self.run/'status.json').exists())
        self.assertFalse((self.run/'standard_comments.json').exists())
        self.assertEqual(json.loads((self.run/'capture_status.json').read_text())['state'],'paused')
        self.assertEqual(json.loads((self.bridge/'requests/000001.json').read_text())['state'],'paused')

    def test_timeout_after_main_then_same_run_resume_does_not_refetch_main(self):
        first=self.start_driver(only_main=True)
        self.assertEqual(self.execute(timeout=0.15),75)
        self.stop.set();first.join(timeout=1)
        before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (self.run/'raw/pages').rglob('response.json')}
        self.assertEqual(len(before),1)
        self.stop.clear();self.start_driver()
        self.assertEqual(self.execute(),0)
        self.stop.set()
        self.assertEqual(self.served,['000001','000003'])
        for name,digest in before.items():self.assertEqual(hashlib.sha256(Path(name).read_bytes()).hexdigest(),digest)

    def test_non_json_body_is_preserved_but_not_success(self):
        self.start_driver(non_json=True)
        self.assertEqual(self.execute(),1)
        self.stop.set()
        self.assertFalse((self.run/'status.json').exists())
        self.assertFalse((self.run/'standard_comments.json').exists())
        self.assertIn('访问验证',(self.run/'browser_receipts/000001.body.txt').read_text())
        self.assertEqual(json.loads((self.run/'capture_status.json').read_text())['state'],'failed')

    def test_bridge_rejects_wrong_url_without_importing_response(self):
        bridge=adapter.BrowserBridge(self.bridge,self.root/'receipts',Pause,timeout=1)
        actual='https://api.bilibili.com/x/v2/reply/reply?oid=1&root=2&pn=1'
        adapter.atomic_json(self.bridge/'responses/000001.json',{'url':actual+'&wrong=1',
            'transport':adapter.TRANSPORT,'received_at':adapter.now(),'raw_text':'{}'})
        with redirect_stdout(io.StringIO()),self.assertRaisesRegex(ValueError,'URL'):
            bridge.request(actual)
        self.assertFalse(list((self.root/'receipts').glob('*.body.txt')))

    def test_fixed_host_paths_times_and_cancel_are_enforced(self):
        for url in ('http://api.bilibili.com/x/v2/reply/reply','https://example.com/x/v2/reply/reply',
                'https://api.bilibili.com/x/web-interface/nav','https://api.bilibili.com/x/v2/reply/reply?cookie=bad'):
            with self.assertRaises(ValueError):adapter.validate_url(url)
        for value in ('2026-10-23','yesterday','2026-99-23T00:00:00Z',None):
            with self.assertRaises((ValueError,TypeError)):adapter.validate_received_at(value)
        bridge=adapter.BrowserBridge(self.bridge,self.root/'receipts',Pause,timeout=1)
        (self.bridge/'cancel').write_text('pause')
        with self.assertRaises(Pause):bridge.control()


if __name__=='__main__':unittest.main(verbosity=2)
