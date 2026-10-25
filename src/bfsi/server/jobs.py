"""Durable, resumable local analysis jobs; immutable published results."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import traceback
import uuid
from ..control import CooperativePause
from ..config import ROOT, DATA_DIR, RUNS

APP = DATA_DIR
PROJECT = ROOT
SUCCESS = ('completed', 'partial_completed')
ACTIVE = ('queued', 'running', 'pausing')
STAGES = {'queued':'等待分析','resolve':'解析视频','collect':'获取评论','clean':'标准化评论',
          'semantic':'LLM 语义分析','index':'计算 BFSI','completed':'分析完成',
          'partial_completed':'部分采集 · 分析完成','paused':'已暂停，可继续','failed':'分析失败'}

def now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')

def save_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    os.replace(temp,path)

def get_run(run_id):
    if not re.fullmatch(r'\d{8}T\d{6}Z_[a-f0-9]{8}',run_id):raise ValueError('任务编号不正确')
    return RUNS/run_id

def read_status(run_id):
    p=get_run(run_id)/'status.json'
    if not p.is_file():raise FileNotFoundError('任务不存在')
    return json.loads(p.read_text())

def read_json(path):return json.loads(Path(path).read_text())

class Jobs:
    def __init__(self):
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='bfsi')
        self.lock=threading.RLock();self.active=None;self.closing=False
        RUNS.mkdir(parents=True,exist_ok=True)
        for file in RUNS.glob('*/status.json'):
            try:
                status=read_json(file)
                if status['state'] in ACTIVE:
                    status.update(state='paused',resume_stage=status.get('stage','resolve'),stage='paused',
                        stage_label=STAGES['paused'],pause_requested=False,pause_reason='restart',
                        message='服务已重启；已保留检查点，请点击继续分析。',error=None,updated_at=now())
                    save_json(file,status)
            except (ValueError,KeyError,OSError):continue

    def _enqueue(self,run_id):
        self.active=run_id
        try:self.pool.submit(self._run,run_id)
        except RuntimeError:
            self.active=None
            self.update(run_id,'failed',state='failed',error='服务正在退出',message='任务没有启动')
            raise

    def submit(self,value,limit=100,mode='quick'):
        from ..collection.collector import normalize_bvid
        bvid=normalize_bvid(value)
        if mode not in ('quick','full'):raise ValueError('分析模式必须是 quick 或 full')
        if mode=='quick' and (type(limit) is not int or not 10<=limit<=200):raise ValueError('快速分析样本上限必须为10–200')
        limit=None if mode=='full' else limit
        with self.lock:
            if self.closing:raise RuntimeError('服务正在退出，不能提交新任务。')
            if self.active is not None:raise RuntimeError('另一个视频正在分析，请等待或暂停该任务。')
            run_id=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ_')+uuid.uuid4().hex[:8]
            folder=get_run(run_id);folder.mkdir(exist_ok=False)
            status=dict(run_id=run_id,state='queued',stage='queued',stage_label=STAGES['queued'],input=value,
                bvid=bvid,mode=mode,limit=limit,created_at=now(),updated_at=now(),collected=0,analyzed=0,
                message='全量任务已创建' if mode=='full' else '快速任务已创建',video=None,error=None,pause_requested=False)
            save_json(folder/'status.json',status);self._enqueue(run_id)
            return status

    def update(self,run_id,stage=None,**values):
        with self.lock:
            status=read_status(run_id)
            if stage:values.update(stage=stage,stage_label=STAGES[stage])
            status.update(values,updated_at=now());save_json(get_run(run_id)/'status.json',status)
            return status

    def control(self,run_id):
        status=read_status(run_id)
        if self.closing or status.get('pause_requested'):
            raise CooperativePause('已停止派发新请求，保存进度后可继续。',reason='shutdown' if self.closing else 'user')

    def pause(self,run_id):
        with self.lock:
            status=read_status(run_id)
            if status['state'] not in ACTIVE:raise ValueError('当前任务不在运行中')
            return self.update(run_id,state='pausing',pause_requested=True,message='正在保存进度；已发出的批次完成后暂停，不再派发新批次。')

    def resume(self,run_id):
        with self.lock:
            status=read_status(run_id)
            if status['state'] not in ('paused','failed'):raise ValueError('只有暂停或失败的任务可以继续；已发布结果不会被覆盖。')
            if self.active is not None or self.closing:raise RuntimeError('请先等待当前任务暂停或完成。')
            stage=(status.get('failed_stage') if status['state']=='failed' else status.get('resume_stage')) or 'resolve'
            self.update(run_id,'queued',state='queued',resume_stage=stage,pause_requested=False,error=None,
                        message='继续已有任务；成功页和模型批次将校验后复用。')
            self._enqueue(run_id)
            return read_status(run_id)

    def _video(self,folder,bvid):
        from ..collection.collector import resolve_video
        p=folder/'video.json'
        if p.exists():
            video=read_json(p)
            if video['bvid']!=bvid:raise ValueError('保存视频与任务BV不一致')
            if '_resolution_raw' not in video:
                raw=folder/'raw/video_page.html'
                if raw.is_file():video['_resolution_raw']=raw.read_text()
            if '_resolution_raw' in video:return video
        video=resolve_video(bvid);save_json(p,video);return video

    def _run(self,run_id):
        folder=get_run(run_id);status=read_status(run_id);bvid=status['bvid'];mode=status.get('mode','quick');limit=status.get('limit',100)
        prior=status.get('resume_stage','resolve');stage='resolve'
        try:
            self.control(run_id)
            self.update(run_id,stage,state='running',message='核验 BV 与视频编号')
            video=self._video(folder,bvid)
            public_video={k:v for k,v in video.items() if not k.startswith('_')}
            from ..collection.collector import collect
            self.control(run_id)
            # Once semantic input has been frozen, resume that input, not a new comment snapshot.
            frozen=prior in ('semantic','index') or (folder/'semantic/contract.json').exists()
            if frozen:
                required=('standard_comments.json','source_file_manifest.json','collection_summary.json')
                if any(not (folder/name).is_file() for name in required):
                    raise ValueError('已冻结语义输入的来源文件缺失，拒绝重新采集或覆盖。请恢复该任务原文件。')
                manifest=read_json(folder/'source_file_manifest.json');collection=read_json(folder/'collection_summary.json')
            else:
                stage='collect';self.update(run_id,stage,video=public_video,message='按正常接口分页获取评论')
                manifest,collection=collect(video,folder,limit=limit,mode=mode,
                    progress=lambda d:self.update(run_id,**d),control=lambda:self.control(run_id))
            self.control(run_id)
            stage='clean';self.update(run_id,stage,video=public_video,message='验证原文、关系和 production Schema')
            from ..transform.cleaner import clean
            standard=clean(video,manifest,folder)
            count=len(standard['comments']);self.update(run_id,stage,collected=count,total=count)
            if not count:raise ValueError('没有有效评论；不生成指数。')
            cleaning=read_json(folder/'cleaning_summary.json') if (folder/'cleaning_summary.json').exists() else {}
            cleaning_warnings=cleaning.get('warnings',[])
            self.control(run_id)
            stage='semantic';self.update(run_id,stage,message='分批调用真实模型；成功批次可从检查点复用')
            from ..semantic.runner import analyze
            dataset=analyze(standard,video,folder,progress=lambda d:self.update(run_id,**d),control=lambda:self.control(run_id))
            self.control(run_id)
            stage='index';self.update(run_id,stage,analyzed=len(dataset['records']),message='核对全部评论覆盖并计算原有公式')
            ids={c['core']['comment_id'] for c in standard['comments']}
            result_ids=[r['comment_id'] for r in dataset['records']]
            if len(result_ids)!=len(ids) or set(result_ids)!=ids:raise ValueError('仍有未处理或重复的评论；不发布指数。')
            from ..index.calculator import build_payload
            payload=build_payload(dataset,video,collection,run_id)
            partial=bool(collection.get('partial') or collection.get('truncated') or collection.get('unresolved_comment_ids') or cleaning_warnings or cleaning.get('partial') or cleaning.get('quarantine') or cleaning.get('position_issues'))
            if mode=='full' and (not collection.get('complete') or not cleaning.get('full_data_integrity')):partial=True
            scope='partial' if partial else ('full_accessible' if mode=='full' else 'sample')
            warnings=list(collection.get('warnings',[]))+list(cleaning_warnings)
            payload.setdefault('analysis',{}).update(run_id=run_id,bvid=bvid,mode=mode,scope=scope,
                model=dataset.get('model_requested',dataset.get('model','unknown')),completed_at=now(),collection=collection,
                unique_comments=count,analyzed_comments=len(result_ids),observations=sum(len(c['observations']) for c in standard['comments']),
                requested_limit=limit,warnings=warnings,semantic_complete=True,
                directional_signal_available=not payload['headline']['no_directional_signal'])
            assert payload['video']['video_oid']==str(video['oid']) and payload['video']['comment_count']==count
            self.control(run_id)
            save_json(folder/'payload.json',payload)
            state='partial_completed' if partial else 'completed'
            message='已分析所有成功采集的有效评论；部分采集范围和原因见说明。' if partial else ('全部正常可访问评论已完成分析' if mode=='full' else '真实样本分析完成')
            if payload['headline']['no_directional_signal']:message+='；暂无有效方向信号，50仅为默认占位。'
            self.update(run_id,state,state=state,result_url=f'/api/runs/{run_id}/result',message=message,analyzed=count,
                        scope=scope,warnings=warnings,pause_requested=False)
            save_json(APP/'latest.json',{'run_id':run_id,'completed_at':now()})
        except CooperativePause as error:
            self.update(run_id,'paused',state='paused',resume_stage=stage,pause_reason=error.reason,
                pause_requested=False,error=None,message=str(error))
        except Exception as error:
            raw=getattr(error,'raw',None)
            if isinstance(raw,bytes):
                stem='failed_http_response'
                if (folder/(stem+'.bin')).exists() or (folder/(stem+'.json')).exists():
                    stem+='_'+uuid.uuid4().hex[:8]
                with (folder/(stem+'.bin')).open('xb') as handle:handle.write(raw)
                save_json(folder/(stem+'.json'),{'sha256':hashlib.sha256(raw).hexdigest(),'status':getattr(error,'status',None),'stage':stage,'received_at':now(),'restricted':getattr(error,'restricted',False)})
            # Keep every failure on resume; a diagnostic file is never silently discarded.
            failure={'stage':stage,'error':str(error),'type':type(error).__name__,'traceback':traceback.format_exc(),'failed_at':now()}
            save_json(folder/f'failure_{uuid.uuid4().hex[:8]}.json',failure);save_json(folder/'failure.json',failure)
            self.update(run_id,'failed',state='failed',failed_stage=stage,resume_stage=stage,error=str(error),message=f'{STAGES.get(stage,stage)}失败：{error}')
        finally:
            with self.lock:self.active=None

    def shutdown(self):
        with self.lock:
            self.closing=True
            if self.active and read_status(self.active)['state'] in ACTIVE:
                self.update(self.active,state='pausing',pause_requested=True,message='服务停止中，正在保存检查点。')
        self.pool.shutdown(wait=True,cancel_futures=False)
