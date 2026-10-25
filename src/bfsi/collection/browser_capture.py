"""Offline file bridge for JSON documents navigated in the user's normal Chrome session.

This CLI never controls Chrome, reads credentials, or sends its own API requests.
The separate UI driver supplies the actual navigated document and reception time.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid
from urllib.parse import parse_qs, urlparse

sys.dont_write_bytecode = True
TRANSPORT = 'chrome_navigation_json_document'
REPRESENTATION = 'Chrome JSON 文档 DOM pre.textContent 的 UTF-8 表示；不是原始 HTTP wire bytes。'
MAX_BODY_BYTES = 20 * 1024 * 1024
RUN_PATTERN = r'\d{8}T\d{6}Z_[a-f0-9]{8}'


def now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00','Z')


def atomic_json(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    os.replace(temp,path)


def project_root():
    from ..config import ROOT
    return ROOT


def load_modules(project=None):
    from . import collector
    from ..transform import cleaner
    from ..control import CooperativePause
    return collector,cleaner,CooperativePause


def validate_url(url):
    parsed=urlparse(url)
    if (parsed.scheme!='https' or parsed.hostname!='api.bilibili.com' or parsed.port not in (None,443)
            or parsed.username or parsed.password or parsed.fragment
            or parsed.path not in ('/x/v2/reply/wbi/main','/x/v2/reply/reply')):
        raise ValueError('浏览器桥只接受 api.bilibili.com 的真实 main/reply HTTPS 地址。')
    query=parse_qs(parsed.query)
    if any(key.lower() in {'cookie','sessdata','access_token','access_key','token','csrf','authorization'} for key in query):
        raise ValueError('浏览器桥不得保存认证资料。')
    return parsed


def validate_received_at(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z',value):
        raise ValueError('received_at 必须是本轮真实接收时刻的 UTC ISO 时间。')
    parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    if parsed.utcoffset()!=timezone.utc.utcoffset(parsed):
        raise ValueError('received_at 时区不正确。')
    return value


class BrowserBridge:
    def __init__(self, bridge_dir, receipts_dir, pause_class, control=None, timeout=180):
        self.folder=Path(bridge_dir).resolve()
        self.requests=self.folder/'requests';self.responses=self.folder/'responses'
        self.receipts=Path(receipts_dir).resolve()
        for path in (self.requests,self.responses,self.receipts):path.mkdir(parents=True,exist_ok=True)
        self.pause_class=pause_class;self.external_control=control;self.timeout=min(float(timeout),180)
        if not 0 < self.timeout <= 180:raise ValueError('桥接等待时间必须为 0 到 180 秒。')
        existing=[int(path.stem) for path in self.requests.glob('*.json') if re.fullmatch(r'\d{6}',path.stem)]
        self.next_id=max(existing,default=0)+1

    def control(self):
        if self.external_control:self.external_control()
        if (self.folder/'cancel').exists() or (self.folder/'cancel.json').exists():
            raise self.pause_class('浏览器传输已暂停；移除 bridge/cancel 后可重新执行同一 CLI 续采。',reason='user')

    def request(self,url):
        validate_url(url)
        self.control()
        request_id=f'{self.next_id:06d}';self.next_id+=1
        request_path=self.requests/(request_id+'.json')
        response_path=self.responses/(request_id+'.json')
        request={'id':request_id,'url':url,'transport':TRANSPORT,'created_at':now(),'state':'pending'}
        atomic_json(request_path,request)
        print(json.dumps({'event':'browser_request','id':request_id,'url':url},ensure_ascii=False),flush=True)
        deadline=time.monotonic()+self.timeout
        try:
            while not response_path.is_file():
                self.control()
                if time.monotonic()>=deadline:
                    raise self.pause_class('等待 Chrome 响应超时，检查点已保留；可重新执行同一 CLI 续采。',reason='browser_transport_timeout')
                time.sleep(min(0.1,max(0,deadline-time.monotonic())))
            self.control()
            # The UI writer must atomically publish this envelope. Do not inspect cookies/headers.
            if response_path.stat().st_size>MAX_BODY_BYTES*6+4096:
                raise ValueError('浏览器响应信封超过安全大小限制。')
            response=json.loads(response_path.read_text(encoding='utf-8'))
            if response.get('url')!=url or response.get('transport')!=TRANSPORT:
                raise ValueError('Chrome 响应 URL 或传输类型与当前请求不一致。')
            validate_url(response['url'])
            text=response.get('raw_text')
            if not isinstance(text,str):raise ValueError('Chrome 响应必须保留实际 pre 文本。')
            raw=text.encode('utf-8')
            if len(raw)>MAX_BODY_BYTES:raise ValueError('Chrome 文档正文超过 20MB。')
            received=validate_received_at(response.get('received_at'))
            # Keep non-JSON/platform error representations too; collector._payload decides success.
            body_path=self.receipts/(request_id+'.body.txt')
            with body_path.open('xb') as handle:handle.write(raw)
            receipt={'id':request_id,'url':url,'transport':TRANSPORT,'received_at':received,
                'bridge_received_at':now(),'body_sha256':hashlib.sha256(raw).hexdigest(),
                'body_bytes':len(raw),'body_file':body_path.name,'representation':REPRESENTATION,
                'wire_bytes':False,'response_envelope_sha256':hashlib.sha256(response_path.read_bytes()).hexdigest()}
            atomic_json(self.receipts/(request_id+'.json'),receipt)
            request.update(state='consumed',consumed_at=now(),body_sha256=receipt['body_sha256'])
            atomic_json(request_path,request)
            return raw,received
        except self.pause_class:
            request.update(state='paused',paused_at=now())
            atomic_json(request_path,request)
            raise
        except Exception:
            request.update(state='rejected',rejected_at=now())
            atomic_json(request_path,request)
            raise


def public_mixin(config, collector):
    data=config.get('data',config)
    image=data.get('wbi_img',data)
    values=[]
    for name in ('img_url','sub_url'):
        url=image.get(name)
        if not isinstance(url,str):raise ValueError('公开 WBI 配置缺少 img_url/sub_url。')
        parsed=urlparse(url)
        if parsed.scheme!='https' or not (parsed.hostname or '').endswith('.hdslb.com') or parsed.username or parsed.password:
            raise ValueError('WBI 配置只能包含公开 hdslb.com 图片 URL。')
        stem=Path(parsed.path).stem
        if not re.fullmatch(r'[A-Fa-f0-9]{32}',stem):raise ValueError('公开 WBI 图片 key 格式不正确。')
        values.append(stem)
    raw=''.join(values)
    key=''.join(raw[index] for index in collector._legacy().MIXIN_KEY_ORDER)[:32]
    return key,{'img_url':image['img_url'],'sub_url':image['sub_url']}


@contextmanager
def private_transport(collector,bridge,mixin):
    original_request,original_key=collector._request,collector._mixin_key
    collector._request=bridge.request
    collector._mixin_key=lambda:mixin
    try:yield
    finally:collector._request,collector._mixin_key=original_request,original_key


def run_capture(project,video_path,run_id,wbi_config_path,bridge_dir,timeout=180):
    if not re.fullmatch(RUN_PATTERN,run_id):raise ValueError('run-id 必须符合 YYYYMMDDTHHMMSSZ_八位十六进制 格式。')
    collector,cleaner,Pause=load_modules(project)
    run_dir=collector.RUNS/run_id
    if (run_dir/'standard_comments.json').exists():
        raise ValueError('标准输入已冻结；请在 Dashboard 继续语义分析，禁止再次采集或覆盖。')
    if (run_dir/'status.json').exists():
        raise ValueError('该 run 已由任务服务注册，请使用独立浏览器采集 run。')
    video_path=Path(video_path).resolve()
    original_video=video_path.read_bytes()
    video=json.loads(original_video)
    config=json.loads(Path(wbi_config_path).read_text(encoding='utf-8'))
    mixin,public_keys=public_mixin(config,collector)
    run_dir.mkdir(parents=True,exist_ok=True)
    capture_path=run_dir/'capture_status.json'
    original_path=run_dir/'browser_video_resolution_original.json'
    if original_path.exists() and original_path.read_bytes()!=original_video:
        raise ValueError('续采视频原件与首次采集不同。')
    if not original_path.exists():
        with original_path.open('xb') as handle:handle.write(original_video)
    atomic_json(run_dir/'browser_wbi_public_config.json',{'wbi_img':public_keys})
    started=now()
    prior=json.loads(capture_path.read_text()) if capture_path.exists() else {}
    started=prior.get('created_at',started)
    capture={'run_id':run_id,'state':'running','stage':'collect','bvid':video.get('bvid'),
        'mode':'full','created_at':started,'updated_at':now(),'transport':TRANSPORT,
        'representation':REPRESENTATION,'message':'等待主代理通过正常 Chrome 导航提供真实响应。'}
    atomic_json(capture_path,capture)
    bridge=BrowserBridge(bridge_dir,run_dir/'browser_receipts',Pause,timeout=timeout)
    def progress(event):
        capture.update(event,updated_at=now())
        atomic_json(capture_path,capture)
        print(json.dumps({'event':'progress',**event},ensure_ascii=False),flush=True)
    try:
        with private_transport(collector,bridge,mixin):
            manifest,summary=collector.collect(video,run_dir,limit=None,mode='full',
                progress=progress,control=bridge.control)
        if not summary.get('selected_comments'):
            raise ValueError('浏览器没有取得有效评论，不能冻结标准输入。')
        # Before Standard's first publication, explain the exact representation honestly.
        summary.update(transport=TRANSPORT,raw_format_note=REPRESENTATION,
            browser_receipts='browser_receipts/',wire_bytes_preserved=False)
        atomic_json(run_dir/'collection_summary.json',summary)
        bridge.control()
        capture.update(stage='clean',updated_at=now(),message='校验来源、关系和既有 production Schema。')
        atomic_json(capture_path,capture)
        standard=cleaner.clean(video,manifest,run_dir)
        count=len(standard['comments'])
        if not count:raise ValueError('没有有效标准评论，不能注册语义任务。')
        cleaning=json.loads((run_dir/'cleaning_summary.json').read_text())
        partial=bool(summary.get('partial') or cleaning.get('partial') or not cleaning.get('full_data_integrity'))
        public_video={key:value for key,value in video.items() if not key.startswith('_')}
        status={'run_id':run_id,'state':'paused','stage':'paused','stage_label':'已暂停，可继续',
            'resume_stage':'semantic','pause_reason':'browser_capture_complete','pause_requested':False,
            'input':video.get('url',video['bvid']),'bvid':video['bvid'],'mode':'full','limit':None,
            'created_at':started,'updated_at':now(),'collected':count,'total':count,'analyzed':0,
            'raw_unique_comments':summary.get('raw_unique_comments'),
            'raw_response_pages':summary.get('raw_response_pages'),
            'video':public_video,'error':None,'transport':TRANSPORT,
            'message':('Chrome 部分采集范围已明确，标准输入已冻结；可继续分析所有所得有效评论。'
                if partial else 'Chrome 正常可访问评论已取得并冻结标准输入；可继续真实语义分析。')}
        checkpoint_path=run_dir/'collection_checkpoint.json'
        if checkpoint_path.is_file():
            checkpoint=json.loads(checkpoint_path.read_text(encoding='utf-8'))
            next_batch=(checkpoint.get('main') or {}).get('next_batch')
            if type(next_batch) is int:status['main_pages']=max(0,next_batch-1)
        atomic_json(run_dir/'status.json',status)
        capture.update(state='standard_frozen',stage='semantic',resume_stage='semantic',
            collected=count,partial=partial,updated_at=now(),message=status['message'])
        atomic_json(capture_path,capture)
        print(json.dumps({'event':'standard_frozen','run_id':run_id,'comments':count,'partial':partial,
            'next_step':'Dashboard 继续分析；本 CLI 没有调用 LLM。'},ensure_ascii=False),flush=True)
        return 0
    except Pause as exc:
        capture.update(state='paused',stage='collect',pause_reason=exc.reason,message=str(exc),updated_at=now())
        atomic_json(capture_path,capture)
        print(json.dumps({'event':'paused','run_id':run_id,'reason':exc.reason,'message':str(exc)},ensure_ascii=False),flush=True)
        return 75
    except Exception as exc:
        capture.update(state='failed',message=str(exc),error_type=type(exc).__name__,updated_at=now())
        atomic_json(capture_path,capture)
        print(json.dumps({'event':'failed','run_id':run_id,'message':str(exc)},ensure_ascii=False),flush=True)
        return 1


def main():
    parser=argparse.ArgumentParser(description='BFSI Chrome JSON-document file bridge; never reads credentials or invokes LLM.')
    parser.add_argument('--video',required=True,type=Path)
    parser.add_argument('--run-id',required=True)
    parser.add_argument('--wbi-config',required=True,type=Path)
    parser.add_argument('--bridge-dir',required=True,type=Path)
    args=parser.parse_args()
    try:return run_capture(project_root(),args.video,args.run_id,args.wbi_config,args.bridge_dir)
    except Exception as exc:
        print(json.dumps({'event':'failed','message':str(exc)},ensure_ascii=False),flush=True)
        return 1


if __name__=='__main__':
    raise SystemExit(main())
