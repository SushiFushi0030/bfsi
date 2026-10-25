"""Offline contract/example validation, not a Transformer/Cleaner.
Uses only Python stdlib. Implements the finite keywords used by this contract,
NOT the complete Draft 2020-12 vocabulary or official meta-schema validation.
"""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,re
from ..config import DATA_DIR, SCHEMAS
P=SCHEMAS;PROJECT=DATA_DIR

def load(p):return json.loads(Path(p).read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def require(v,msg):
 if not v:raise ValueError(msg)
def equal(a,b):
 if isinstance(a,bool)!=isinstance(b,bool):return False
 return a==b
def matches(v,t):
 return {'object':lambda:isinstance(v,dict),'array':lambda:isinstance(v,list),'string':lambda:isinstance(v,str),'null':lambda:v is None,'boolean':lambda:isinstance(v,bool),'integer':lambda:not isinstance(v,bool) and isinstance(v,(int,float)) and int(v)==v,'number':lambda:isinstance(v,(int,float)) and not isinstance(v,bool)}[t]()
def resolve(doc,ptr):
 require(ptr.startswith('/'),'pointer must start /')
 for k in ptr[1:].split('/'):
  k=k.replace('~1','/').replace('~0','~');doc=doc[int(k)] if isinstance(doc,list) else doc[k]
 return doc

def check_schema(s,root=None):
 if root is None:root=s
 require(isinstance(s,dict),'schema must be object')
 known={'$schema','$id','$defs','$ref','title','description','type','properties','required','additionalProperties','items','minItems','minLength','minimum','maximum','pattern','enum','const','anyOf','allOf','if','then','else','format','readOnly','uniqueItems'}
 require(not s.keys()-known,'unsupported schema keyword '+str(s.keys()-known))
 if '$ref' in s:
  require(s['$ref'].startswith('#/$defs/'),'only local defs supported');resolve(root,s['$ref'][1:])
 if 'type' in s:
  ts=s['type'] if isinstance(s['type'],list) else [s['type']];require(all(t in ['object','array','string','integer','number','boolean','null'] for t in ts),'bad type')
 if 'required' in s:require(isinstance(s['required'],list) and len(set(s['required']))==len(s['required']) and all(isinstance(x,str) for x in s['required']),'bad required')
 if 'pattern' in s:re.compile(s['pattern'])
 if 'format' in s:require(s['format']=='date-time','unsupported format')
 if 'additionalProperties' in s:require(s['additionalProperties'] is False,'only closed objects used')
 for key in ['minItems','minLength','minimum','maximum']:
  if key in s:require(isinstance(s[key],int) and not isinstance(s[key],bool) and s[key]>=0,'bad numeric keyword')
 for key in ['uniqueItems','readOnly']:
  if key in s:require(isinstance(s[key],bool),'bad boolean keyword')
 for key in ['anyOf','allOf']:
  if key in s:
   require(isinstance(s[key],list) and s[key],'empty combinator')
   for x in s[key]:check_schema(x,root)
 for key in ['if','then','else','items']:
  if key in s:check_schema(s[key],root)
 for key in ['properties','$defs']:
  if key in s:
   require(isinstance(s[key],dict),'bad schema map')
   for x in s[key].values():check_schema(x,root)

def validate(v,s,root=None,path='$'):
 if root is None:root=s
 if '$ref' in s:validate(v,resolve(root,s['$ref'][1:]),root,path)
 if 'type' in s:require(any(matches(v,t) for t in (s['type'] if isinstance(s['type'],list) else [s['type']])),path+' type')
 if 'const' in s:require(equal(v,s['const']),path+' const')
 if 'enum' in s:require(any(equal(v,x) for x in s['enum']),path+' enum')
 if 'anyOf' in s:
  good=False
  for x in s['anyOf']:
   try:validate(v,x,root,path);good=True
   except ValueError:pass
  require(good,path+' anyOf')
 for x in s.get('allOf',[]):validate(v,x,root,path)
 if 'if' in s:
  try:validate(v,s['if'],root,path);branch='then'
  except ValueError:branch='else'
  if branch in s:validate(v,s[branch],root,path)
 if isinstance(v,dict):
  require(not set(s.get('required',[]))-v.keys(),path+' missing required')
  if s.get('additionalProperties') is False:require(not v.keys()-s.get('properties',{}).keys(),path+' additional fields')
  for k,x in v.items():
   if k in s.get('properties',{}):validate(x,s['properties'][k],root,path+'.'+k)
 if isinstance(v,list):
  require(len(v)>=s.get('minItems',0),path+' minItems')
  if s.get('uniqueItems'):require(len({json.dumps(x,sort_keys=True) for x in v})==len(v),path+' uniqueItems')
  if 'items' in s:
   for i,x in enumerate(v):validate(x,s['items'],root,path+f'[{i}]')
 if isinstance(v,str):
  require(len(v)>=s.get('minLength',0),path+' minLength')
  if 'pattern' in s:require(re.search(s['pattern'],v) is not None,path+' pattern')
  if s.get('format')=='date-time':
   try:d=datetime.fromisoformat(v.replace('Z','+00:00'));require(d.tzinfo is not None,path+' timezone')
   except ValueError:raise ValueError(path+' invalid date-time')
 if isinstance(v,(float,int)) and not isinstance(v,bool):
  if 'minimum' in s:require(v>=s['minimum'],path+' minimum')
  if 'maximum' in s:require(v<=s['maximum'],path+' maximum')

FORBIDDEN={'count','mid','user_id','uname','username','handle','avatar','sex','sign','level','vip','VIP','fans_badge','certification','member','anonymous_author_id','image_url','img_src','ocr','sentiment','is_hot','is_selected','up_liked','up_replied'}
def privacy(v):
 if isinstance(v,dict):
  require(not v.keys()&FORBIDDEN,'forbidden standard field')
  for x in v.values():privacy(x)
 elif isinstance(v,list):
  for x in v:privacy(x)
def project_file(rel, project=None):
 root=Path(PROJECT if project is None else project).resolve()
 f=(root/rel).resolve();require(f.is_relative_to(root),'path escapes data directory');require(f.relative_to(root).as_posix()==rel,'non-canonical path alias');return f

def mention_evidence(content):
 records={}
 a=content.get('at_name_to_mid') or {};b=content.get('at_name_to_mid_str') or {}
 for name in a.keys() & b.keys():require(str(a[name])==str(b[name]),'conflicting mention identity maps')
 for field in ['at_name_to_mid_str','at_name_to_mid','members']:
  v=content.get(field)
  if not v:continue
  seq=sorted(v.items()) if isinstance(v,dict) else list(enumerate(v))
  for i,(k,x) in enumerate(seq):
   name=x.get('uname') if field=='members' else k
   mid=x.get('mid') if field=='members' else x
   require(isinstance(mid,(int,str)) and not isinstance(mid,bool) and re.fullmatch('[1-9][0-9]*',str(mid)),'invalid mention account evidence; quarantine')
   records[(field,i)]=(str(mid),name)
 return records

def check_source(c,o,raw):
 core=c['core'];ct=raw['content']
 for k,rk in [('comment_id','rpid'),('video_oid','oid')]:require(core[k]==str(raw[rk]),'raw identity mismatch')
 for k,rk in [('root_comment_id','root'),('parent_comment_id','parent')]:require(core[k]==(str(raw[rk]) if raw[rk] else None),'raw relationship mismatch')
 require(core['raw_text']==ct['message'] and core['publish_time_raw']==raw['ctime'],'raw core mismatch / immutable text')
 require(o['like_count']==raw['like'] and o['reply_count']==raw['rcount'],'snapshot mismatch')
 for k,state in o['content_source_states'].items():
  actual='missing' if k not in ct else 'null' if ct[k] is None else 'object' if isinstance(ct[k],dict) else 'array' if isinstance(ct[k],list) else 'unsupported'
  require(actual==state,'raw source state mismatch')
 pics=ct.get('pictures',[])
 require((o['has_picture'],o['picture_count'])==((None,None) if pics is None else (bool(pics),len(pics))),'picture presence mismatch')
 emotes=sorted((ct.get('emote') or {}).items());links=sorted((ct.get('jump_url') or {}).items())
 require(len(o['emotes'])==len(emotes),'emote completeness')
 for i,(item,(token,value)) in enumerate(zip(o['emotes'],emotes)):
  require(item=={'source_index':i,'token':token,'label':value.get('text')},'emote evidence mismatch')
 require(len(o['links'])==len(links),'link completeness')
 for i,(item,(anchor,value)) in enumerate(zip(o['links'],links)):
  require(item=={'source_index':i,'anchor_text':anchor,'title':value.get('title')},'link evidence mismatch')
 evidence=mention_evidence(ct);used=set();target_to_group={}
 for i,m in enumerate(o['mentions']):
  require(m['local_ref']==f'mention_{i+1}','local mention sequence')
  ids=set();names=[]
  for src in m['source_refs']:
   key=(src['field'],src['index']);require(key in evidence and key not in used,'mention source invalid/duplicate');used.add(key);mid,name=evidence[key];ids.add(mid);names.append(name)
  require([(x['field'],x['index']) for x in m['source_refs']]==[x for x in evidence if x in {(y['field'],y['index']) for y in m['source_refs']}],'mention source order')
  require(len(ids)==1,'merge of distinct mentioned targets')
  target=next(iter(ids));require(target not in target_to_group,'split duplicate mention target');target_to_group[target]=i
  spans=set()
  for name in names:
   if not isinstance(name,str) or not name:continue
   for mat in re.finditer(re.escape(name),core['raw_text']):
    a,b=mat.span();spans.add((a-1 if a and core['raw_text'][a-1]=='@' else a,b))
  require(m['text_spans']==[{'start':a,'end':b} for a,b in sorted(spans)],'mention spans mismatch')
 require(used==set(evidence),'mention source completeness')
 # Deterministic target order: str map, numeric map, members, then source index.
 first_order={mid:n for n,mid in enumerate(dict.fromkeys(mid for mid,name in evidence.values()))}
 require([target_to_group[mid] for mid in first_order]==list(range(len(first_order))),'mention group order')
 flag=raw.get('reply_control',{}).get('is_up_top')
 require(flag is None or isinstance(flag,bool),'unsupported pinned flag type')
 require(not(o['source_role']=='pinned_root' and flag is False),'contradictory pinned evidence')
 expected=True if o['source_role']=='pinned_root' or flag is True else False if flag is False or o['source_role'] in ['ordinary_root','preview_reply','full_reply','pinned_preview_reply'] else None
 require(o['is_pinned']==expected,'pinned evidence mismatch')

ROLE_PATHS={
 'ordinary_root':r'/data/replies/[0-9]+','preview_reply':r'/data/replies/[0-9]+/replies/[0-9]+',
 'full_reply':r'/data/replies/[0-9]+','reply_api_root_copy':r'/data/root',
 'pinned_root':r'/data/(top/upper|top_replies/[0-9]+)',
 'pinned_preview_reply':r'/data/(top/upper|top_replies/[0-9]+)/replies/[0-9]+'}

def validate_dataset(data,manifest,verify_sources=True,previous=None,project=None):
 validate(data,load(P/'standard_comment_schema_v0.1.json'));validate(manifest,load(P/'source_file_manifest_schema_v0.1.json'));privacy(data)
 files={x['raw_file']:x for x in manifest['files']};require(len(files)==len(manifest['files']),'duplicate manifest file')
 cache={};seen_obs=set();cores={c['core']['comment_id']:c['core'] for c in data['comments']}
 require(len(cores)==len(data['comments']),'duplicate comment identity')
 for file,item in files.items():
  if item['run_time_basis']=='unknown':require(item['run_time'] is None,'unknown run time')
  if item['run_time_basis'] in ['collector','run_directory']:require(item['run_time'] is not None,'missing run time')
  if item['run_time_basis']=='run_directory':
   stamp=re.search(r'([0-9]{8}T[0-9]{6}Z)',item['source_run']);require(stamp is not None,'missing run timestamp in source_run')
   require(datetime.strptime(stamp[1],'%Y%m%dT%H%M%SZ').strftime('%Y-%m-%dT%H:%M:%SZ')==item['run_time'],'run time mismatch')
  if verify_sources:
   f=project_file(file,project);require(sha(f)==item['sha256'],'raw file hash mismatch');cache[file]=load(f)
 for c in data['comments']:
  core=c['core'];rid=core['root_comment_id'];par=core['parent_comment_id'];cid=core['comment_id']
  require(core['published_at']==datetime.fromtimestamp(core['publish_time_raw'],timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),'published time derivation')
  if par is not None:
   require(par in cores and rid in cores,'unresolved relationship; quarantine, do not fabricate depth')
   require(cid!=par and cid!=rid,'self relationship');parent=cores[par];root=cores[rid]
   require(root['depth']==0 and parent['depth']+1==core['depth'],'root/depth mismatch')
   require(parent['comment_id']==rid or parent['root_comment_id']==rid,'cross-tree parent')
   require(parent['video_oid']==root['video_oid']==core['video_oid'],'cross-video relation')
  for other in cores.values():
   if other['video_oid']==core['video_oid']:require(other['video_bvid']==core['video_bvid'],'inconsistent video identity')
  for o in c['observations']:
   key=(o['source_run'],o['raw_file'],o['raw_object_path']);require(key not in seen_obs,'duplicate observation');seen_obs.add(key)
   require(o['raw_file'] in files,'missing source file manifest');f=files[o['raw_file']]
   require(o['source_endpoint']==f['source_endpoint'] and o['source_run']==f['source_run'],'provenance disagreement')
   require(o['observed_at']==f['received_at'],'exact request time disagreement')
   role=o['source_role'];require(re.fullmatch(ROLE_PATHS[role],o['raw_object_path']) is not None,'role / pointer mismatch')
   require(o['source_endpoint']==('/x/v2/reply/reply' if role in ['full_reply','reply_api_root_copy'] else '/x/v2/reply/wbi/main'),'role / endpoint mismatch')
   require((core['depth']==0)==(role in ['ordinary_root','pinned_root','reply_api_root_copy']),'role / relationship mismatch')
   require(not(role=='pinned_root' and o['is_pinned'] is not True),'pinned root context')
   require(not(role=='pinned_preview_reply' and o['is_pinned'] is True),'pin not inherited by reply')
   states=o['content_source_states']
   for source,array in [('emote','emotes'),('jump_url','links')]:
    if states[source] in ['missing','null']:require(o[array]==[],'unavailable source cannot contain items')
    require([x['source_index'] for x in o[array]]==list(range(len(o[array]))),'content item index order')
   if states['pictures']=='null':require(o['has_picture'] is None and o['picture_count'] is None,'null picture state')
   elif states['pictures']=='missing':require(o['has_picture'] is False and o['picture_count']==0,'missing picture state')
   else:require(o['has_picture'] is not None and o['picture_count'] is not None,'array picture state')
   require([m['local_ref'] for m in o['mentions']]==[f'mention_{i+1}' for i in range(len(o['mentions']))],'mention local refs sequence')
   allrefs=[]
   for m in o['mentions']:
    for source in m['source_refs']:
     require(states[source['field']] in ['object','array'],'mention refers to unavailable source');allrefs.append((source['field'],source['index']))
   require(len(allrefs)==len(set(allrefs)),'reused mention evidence')
   for m in o['mentions']:
    for span in m['text_spans']:require(0<=span['start']<span['end']<=len(core['raw_text']),'mention span bounds')
   if verify_sources:check_source(c,o,resolve(cache[o['raw_file']],o['raw_object_path']))
 if previous:
  now={c['core']['comment_id']:c for c in data['comments']}
  for old in previous['comments']:
   require(old['core']['comment_id'] in now,'removed existing comment');new=now[old['core']['comment_id']]
   require(old['core']==new['core'],'stable core mutation; quarantine conflict')
   for obs in old['observations']:require(obs in new['observations'],'prior observation overwritten/removed')
 return True
