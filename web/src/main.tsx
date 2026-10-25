import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { ACTION, EMOTION, RELEVANCE, STANCE, clamp, count, directionLabel, num, pct, percent, validatePayload } from './data';
import type { Asset, Comment, Payload, Rate } from './data';
import './styles.css';

function Mark() { return <span className="brand-mark" aria-hidden="true"><i/><i/><i/><i/></span>; }
function Arrow() { return <span aria-hidden="true">↗</span>; }
function Info({ children }: { children: React.ReactNode }) { return <span className="info" tabIndex={0} aria-label={typeof children === 'string' ? children : '指标说明'}>i<span role="tooltip">{children}</span></span>; }
function Empty({ text = '暂无可展示的数据' }: { text?: string }) { return <p className="empty">{text}</p>; }
function SectionHead({ label, title, note, id }: { label: string; title: string; note?: string; id?: string }) {
  return <div className="section-head" id={id}><div><span className="eyebrow">{label}</span><h2>{title}</h2></div>{note && <p>{note}</p>}</div>;
}
function Bar({ value, tone = '' }: { value: number; tone?: string }) { return <div className={`bar ${tone}`} aria-hidden="true"><span style={{ width: `${clamp(value)}%` }}/></div>; }
function Scale({ value, muted = false }: { value: number; muted?: boolean }) {
  return <div className={`scale ${muted ? 'muted' : ''}`}>
    <div className="scale-track" role="img" aria-label={muted?'暂无方向信号':`方向刻度 ${num(value, 1)} / 100`}>
      <span className="scale-center"/>{!muted && <span className="scale-pointer" style={{ left: `${clamp(value)}%` }}/>}</div>
    <div className="scale-labels"><span>0 <em>看空</em></span><span>50 <em>多空平衡</em></span><span>100 <em>看多</em></span></div>
  </div>;
}
function Hero({ data }: { data: Payload }) {
  const h = data.headline; const low = h.signal_coverage < data.methodology.commentary_only_bands.low_coverage_below_percent;
  return <>
    <section className="hero-grid" aria-label="核心指标">
      <article className="panel hero-main">
        <div className="card-top"><span className="eyebrow">MARKET DIRECTION</span><span className="status-pill">{directionLabel(data)}</span></div>
        <h2>BFSI 市场方向指数 <Info>仅使用看多、看空与 mixed 评论，反映方向表达。请同时阅读信号覆盖率。</Info></h2>
        <div className="hero-number"><strong data-testid="bfsi">{h.no_directional_signal?'—':num(h.bfsi, 1)}</strong><span>/ 100</span></div>
        <Scale value={h.bfsi} muted={h.no_directional_signal}/>
        <p className="card-foot">{h.no_directional_signal ? '当前数值是约定的默认值，不代表已观察到多空平衡。' : '一条评论，一份方向表达。结合覆盖率共同解读。'}</p>
      </article>
      <article className="panel kpi coverage-card">
        <div className="card-top"><span className="eyebrow">SIGNAL COVERAGE</span><span className="corner-icon" aria-hidden="true">◌</span></div>
        <h2>方向信号覆盖率</h2>
        <div className="kpi-number" data-testid="coverage">{num(h.signal_coverage, 1)}<span>%</span></div>
        <p className="signal-status">{low ? '方向信号较低' : '方向信号覆盖情况'}</p>
        <Bar value={h.signal_coverage}/>
        <p className="card-foot"><b>{num(h.directional_comments)}</b> / {num(data.video.comment_count)} 条评论<br/>表达了明确市场方向</p>
      </article>
      <article className="panel kpi disagreement-card">
        <div className="card-top"><span className="eyebrow">DISAGREEMENT</span><span className="corner-icon" aria-hidden="true">⇄</span></div>
        <h2>多空分歧程度</h2>
        <div className="kpi-number"><b data-testid="disagreement">{h.no_bullish_bearish_signal?'—':num(h.disagreement)}</b><span>/ 100</span></div>
        <p className="signal-status">{h.no_bullish_bearish_signal ? '暂无多空比较样本' : h.disagreement >= data.methodology.commentary_only_bands.high_disagreement_at_least ? '多空高度分歧' : '多空数量存在差异'}</p>
        <div className="mini-counts">{['bullish','bearish','mixed'].map(k => <div key={k}><span className={`dot ${k}`}/><b>{num(count(data,'market_stance',k))}</b><small>{k === 'mixed' ? 'Mixed' : k === 'bullish' ? 'Bullish' : 'Bearish'}</small></div>)}</div>
        <p className="card-foot">分歧仅比较看多与看空的数量</p>
      </article>
    </section>
    <section className="snapshot" aria-label="评论区快照"><div><span className="snapshot-icon" aria-hidden="true">“</span><h2>评论区快照</h2><span className="eyebrow">BFSI SNAPSHOT</span></div><p data-testid="commentary">{data.commentary}</p></section>
  </>;
}
function DirectionDistribution({ data }: { data: Payload }) {
  const keys = ['bullish','bearish','mixed','neutral','unclear','not_applicable'];
  return <article className="panel distribution-card">
    <div className="card-top"><h3>市场方向分布</h3><span className="small-label">全部 {num(data.video.comment_count)} 条</span></div>
    <p className="distribution-callout"><strong>{num(count(data,'market_stance','not_applicable'))}</strong> 条评论未表达作者自己的市场方向</p>
    <div className="stacked" role="img" aria-label={keys.map(k=>`${STANCE[k]} ${count(data,'market_stance',k)} 条`).join('，')}>
      {keys.map(k=><span key={k} className={k} style={{flex:count(data,'market_stance',k)}} title={`${STANCE[k]} ${count(data,'market_stance',k)} 条`}/>)}</div>
    <div className="stance-legend">{keys.map(k=><div key={k}><span className={`dot ${k}`}/><span>{STANCE[k]}</span><b>{num(count(data,'market_stance',k))}</b><small>{pct(percent(data,'market_stance',k))}</small></div>)}</div>
    <div className="directional-subset"><span>其中，方向样本 <b>{num(data.headline.directional_comments)}</b> 条</span><div>{['bullish','bearish','mixed'].map(k=><span key={k}><i className={`dot ${k}`}/>{STANCE[k]} <b>{num(count(data,'market_stance',k))}</b></span>)}</div></div>
  </article>;
}
function Relevance({ data }: { data: Payload }) {
  const keys=Object.keys(RELEVANCE); let offset=0;
  const paths=keys.map(k=>{const value=percent(data,'relevance',k);const start=offset;offset+=value;return {key:k,value,start};});
  return <article className="panel relevance-card"><div className="card-top"><h3>财经相关性</h3><span className="small-label">FINANCIAL RELEVANCE</span></div>
    <div className="relevance-body"><div className="donut" role="img" aria-label={`财经相关性 ${pct(data.auxiliary_metrics.financial_relevance_rate?.display_percent)}`}>
      <svg viewBox="0 0 120 120" aria-hidden="true"><circle className="donut-base" cx="60" cy="60" r="48"/>{paths.map(p=><circle key={p.key} className={`ring ${p.key}`} cx="60" cy="60" r="48" pathLength="100" strokeDasharray={`${p.value} ${100-p.value}`} strokeDashoffset={-p.start} transform="rotate(-90 60 60)"/>)}</svg>
      <div><strong>{pct(data.auxiliary_metrics.financial_relevance_rate?.display_percent)}</strong><span>财经语义相关</span></div></div>
      <div className="relevance-legend">{keys.map(k=><div key={k}><span className={`dot rel-${k}`}/><span>{RELEVANCE[k]}</span><b>{num(count(data,'relevance',k))}</b></div>)}</div></div>
    <p className="card-foot">讨论财经，不一定表达看多或看空。</p>
  </article>;
}
function Topics({ data }: { data: Payload }) {
  const topics=data.top_financial_topics;const max=Math.max(1,...topics.map(t=>t.total_mentions));
  return <section className="panel topic-panel"><div className="card-top"><div><span className="eyebrow">IN THE CONVERSATION</span><h3>正在谈论的财经话题</h3></div><span className="small-label">按涉及评论数</span></div>
    {topics.length ? <div className="topic-list" data-testid="financial-topics">{topics.map((t,i)=><div className="topic" key={t.target_name}><span className="rank">{String(i+1).padStart(2,'0')}</span><div><span>{t.target_name}</span><Bar value={t.total_mentions/max*100}/></div><b>{num(t.total_mentions)}<small>条</small></b></div>)}</div> : <Empty text="暂无财经话题"/>}
  </section>;
}
function AssetCard({ asset }: { asset: Asset }) {
  return <article className="panel asset-card" data-testid="asset-card"><div className="card-top"><h3>{asset.target_name}</h3><Arrow/></div><span className="approx-label">共现近似</span>
    <div className="asset-number"><strong>{asset.no_directional_signal ? '—' : num(asset.target_BFSI_display,1)}</strong><span>BFSI / 100</span></div>
    {asset.no_directional_signal ? <p className="no-signal">暂无方向信号</p> : <Bar value={asset.target_BFSI_display} tone="asset-bar"/>}
    <div className="asset-sample"><b>{num(asset.directional_comments)}</b> 条方向评论 <span>/ {num(asset.total_mentions)} 条涉及评论</span></div>
    <div className="asset-coverage"><span>方向覆盖率</span><b>{pct(asset.target_signal_coverage_display_pct)}</b></div>
    <p className="asset-caution">评论级方向，不等于对象专属立场</p>
  </article>;
}
function Emotions({ data }: { data: Payload }) {
  return <article className="panel emotion-card"><div className="card-top"><h3>情绪表达</h3><span className="small-label">EMOTIONS</span></div><div className="bar-list">{Object.entries(EMOTION).map(([k,label])=><div className="bar-row" key={k}><span>{label}</span><Bar value={percent(data,'emotion',k)} tone={k==='neutral'?'neutral-bar':''}/><b>{num(count(data,'emotion',k))}</b><small>{pct(percent(data,'emotion',k))}</small></div>)}</div><p className="card-foot">一条评论可包含多个情绪标签，因此比例总和可能超过 100%。</p></article>;
}
function Actions({ data }: { data: Payload }) {
  const advice=data.auxiliary_metrics.advice_seeking_rate;const active=data.auxiliary_metrics.active_trading_intent_rate;
  return <article className="panel action-card"><div className="card-top"><h3>投资行为表达</h3><span className="small-label">ACTION INTENT</span></div>
    <div className="action-kpis"><div><strong>{pct(advice?.display_percent)}</strong><span>寻求建议</span></div><div><strong>{pct(active?.display_percent)}</strong><span>明确投资行为 <Info>包含已发生操作、条件计划等，仅作为探索性表达指标，不代表未来订单。</Info></span></div></div>
    <div className="action-list">{['ask_advice','buy','add','hold','sell','reduce','wait'].map(k=><div key={k}><span>{ACTION[k]}</span><Bar value={percent(data,'action_intent',k)}/><b>{num(count(data,'action_intent',k))}</b></div>)}</div>
    <div className="muted-actions">{['no_action','not_applicable','unclear'].map(k=><span key={k}>{ACTION[k]} <b>{num(count(data,'action_intent',k))}</b></span>)}</div>
    <p className="card-foot">行为表达包含已发生操作及条件计划，不代表未来订单。</p>
  </article>;
}
function Community({ data }: { data: Payload }) {
  const items=[['humor_rate','幽默表达','幽默让讨论更有温度'],['confusion_rate','困惑表达','尚待理解与澄清的问题'],['creator_positive_rate','创作者好感','对 UP 主的正向态度'],['anxiety_rate','焦虑表达','评论中可见的担忧']] as const;
  return <section className="community"><div className="community-heading"><span className="eyebrow">COMMUNITY SIGNALS</span><h2>评论区的另一面</h2></div><div className="community-grid">{items.map(([key,label,note])=>{const r:Rate|undefined=data.auxiliary_metrics[key];return <article key={key}><span>{label}</span><strong>{pct(r?.display_percent)}</strong><p>{num(r?.count)} / {num(r?.denominator)} 条</p><small>{note}</small></article>;})}</div></section>;
}
const tabs=[['bullish','看多','Bullish'],['bearish','看空','Bearish'],['mixed','多向观点','Mixed'],['ask_advice','寻求建议','Ask Advice'],['humorous','幽默 / 反讽','Humorous']] as const;
function CommentCard({ comment }: { comment: Comment }) {
  const [expanded,setExpanded]=useState(false);
  const ctx=comment.context_used;const chain=ctx?.ancestor_chain ?? [];
  return <article className="panel comment-card"><div className="card-top"><span className={`comment-stance ${comment.market_stance}`}>{STANCE[comment.market_stance] ?? comment.market_stance}</span><span className="confidence">置信度 · {({high:'高',medium:'中',low:'低'} as Record<string,string>)[comment.confidence] ?? comment.confidence}</span></div>
    <blockquote>{comment.raw_text}</blockquote><div className="comment-summary"><span>语义摘要</span><p>{comment.summary}</p></div>
    <div className="comment-targets">{comment.targets?.length ? comment.targets.map((t,i)=><span key={`${t.raw_name}-${i}`}>{t.normalized_name || t.raw_name}</span>) : <span>未明确对象</span>}</div>
    <div className="comment-meta"><span>操作 · {ACTION[comment.action_intent] ?? comment.action_intent}</span><small>ID {comment.comment_id}</small></div>
    <button className="context-toggle" aria-expanded={expanded} aria-controls={`context-${comment.comment_id}`} onClick={()=>setExpanded(!expanded)}>查看上下文 <span aria-hidden="true">{expanded?'−':'+'}</span></button>
    {expanded && <div id={`context-${comment.comment_id}`} className="context-chain">{chain.length ? chain.map((c,i)=><div key={c.comment_id}><span>{i===0 ? '根评论' : '祖先评论'}{i===chain.length-1?' · 直接父评论':''}</span><p>{c.raw_text}</p><small>ID {c.comment_id}</small></div>) : <p>本条为一级评论，没有父评论上下文。</p>}</div>}
  </article>;
}
function Comments({ data }: { data: Payload }) {
  const [active,setActive]=useState<string>('bullish');const comments=data.representative_comments[active] ?? [];
  return <section className="comments-section" id="comments"><SectionHead label="VOICES BEHIND THE NUMBERS" title="回到评论本身" note="已有语义结果中的代表表达，保留原文与摘要。"/>
    <div className="comment-tabs" role="tablist" aria-label="代表评论分类">{tabs.map(([key,label,en],i)=><button key={key} role="tab" id={`tab-${key}`} aria-selected={active===key} aria-controls="comment-panel" tabIndex={active===key?0:-1} onClick={()=>setActive(key)} onKeyDown={e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();const next=e.key==='Home'?0:e.key==='End'?tabs.length-1:(i+(e.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;setActive(tabs[next][0]);document.getElementById(`tab-${tabs[next][0]}`)?.focus();}}}><span>{label}</span><small>{en}</small><b>{num((data.representative_comments[key] ?? []).length)}</b></button>)}</div>
    <div className="comment-grid" role="tabpanel" id="comment-panel" aria-labelledby={`tab-${active}`} tabIndex={0}>{comments.length ? comments.map(c=><CommentCard key={`${active}-${c.comment_id}`} comment={c}/>) : <Empty text="这一分类暂无代表评论"/>}</div>
  </section>;
}
function Methodology() {
  return <section className="methodology" id="methodology"><details><summary><span><span className="eyebrow">METHODOLOGY</span><strong>如何读懂 BFSI</strong></span><span className="method-expand" aria-hidden="true">＋</span></summary><div className="method-content"><div><h3>一个简单、透明的方向指数</h3><p>Bullish = +1 · Bearish = −1 · Mixed = 0</p><div className="formula">BFSI = 50 + 50 ×<br/><span>(Bullish − Bearish) / (Bullish + Bearish + Mixed)</span></div><p>50 为多空平衡中点，&gt;50 偏多，&lt;50 偏空。没有方向样本时，50 是默认值，应显示“暂无方向信号”。</p></div><div><h3>先看覆盖率，再读方向</h3><p>Coverage 说明有多少评论表达了方向。Disagreement 仅比较 Bullish 与 Bearish 的数量，不包含 Mixed。</p><p>当前没有点赞、时间、置信度或回复层级权重。资产卡片是评论级方向的共现近似，不是精确的对象级立场。</p><p>数据来自一个视频样本，属于探索性观察，不用于市场预测。</p></div></div></details></section>;
}
function Dashboard({ data, controls }: { data: Payload; controls: React.ReactNode }) {
  return <><a className="skip-link" href="#main">跳到主要内容</a><header className="site-header"><a href="#" className="brand" aria-label="BFSI 首页"><Mark/><div><b>BFSI<span>V0.1</span></b><small>Bilibili Financial Sentiment Index</small></div></a><nav aria-label="页面导航"><a href="#overview">概览</a><a href="#assets">资产观察</a><a href="#comments">代表评论</a></nav><span className="header-status"><i/>单视频观察</span></header>
    <main id="main"><div className="page-intro" id="overview"><div><span className="eyebrow">LISTEN TO THE MARKET CONVERSATION</span><h1>评论区情绪观察</h1><p>B站财经评论区语义与市场情绪观察器</p></div><div className="video-info"><span>当前分析视频 <b data-testid="comment-count">{num(data.video.comment_count)} Comments</b></span><strong>{data.video.title || '未获取视频标题'}</strong><small>{data.video.video_bvid ? `${data.video.video_bvid} · ` : ''}OID {data.video.video_oid}</small></div></div>
      {data.analysis?.synthetic&&<aside className="synthetic-notice" role="note">合成演示数据：视频、评论与语义标签为人工构造，仅用于复现界面和计算流程。</aside>}{controls}<Hero data={data}/><section className="section-block"><SectionHead label="THE FULL PICTURE" title="看见整个评论区" note="财经讨论、情绪表达与市场方向，是不同的信号。"/><div className="two-columns direction-row"><DirectionDistribution data={data}/><Relevance data={data}/></div></section>
      <Topics data={data}/><section className="section-block" id="assets"><SectionHead label="ASSET & MARKET SENTIMENT" title="资产与市场观察" note="V0.1 共现近似 · 不等于精确的对象级立场"/><div className="asset-grid" data-testid="assets">{data.asset_market_target_indices.length ? data.asset_market_target_indices.map(a=><AssetCard key={a.target_name} asset={a}/>) : <Empty text="暂无达到展示条件的资产或市场对象"/>}</div></section>
      <section className="section-block"><SectionHead label="BEYOND BULLS & BEARS" title="情绪与行动之间" note="有情绪，不一定有操作；有操作，也不一定是未来计划。"/><div className="two-columns"><Emotions data={data}/><Actions data={data}/></div></section>
      <Community data={data}/><Comments data={data}/><Methodology/>
    </main><footer><div className="footer-brand"><Mark/><b>BFSI</b><span>让评论区的声音，有迹可循。</span></div><p>BFSI V0.1 为实验性评论语义观察工具，结果来自 AI 对 B站评论的自动结构化分析，仅用于探索和展示，不构成投资建议，也不用于预测市场涨跌。</p><span className="footer-note">一个视频，一次观察。<a href="#overview">返回顶部 ↑</a></span></footer></>;
}
type Job = { run_id: string; state: string; stage: string; stage_label: string; message: string; error?: string; bvid: string; collected: number; raw_unique_comments?: number; analyzed: number; mode?: 'full'|'quick'; limit: number|null; total?: number; batches_done?: number; batches_total?: number; video?: { title: string; bvid: string } };
const succeeded=(state:string)=>state==='completed'||state==='partial_completed';
const active=(state?:string)=>state==='queued'||state==='running'||state==='pausing';
async function api<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || '本地服务请求失败');
  return body as T;
}
function ShareExport({runId}:{runId:string}) {
  const [state,setState]=useState<{preview_url:string;download_url:string;path:string}|null>(null);
  const [busy,setBusy]=useState(false);const [error,setError]=useState('');
  // A keyed component keeps exported links scoped to the displayed run.
  const controller=React.useRef<AbortController|null>(null);
  useEffect(()=>()=>controller.current?.abort(),[]);
  async function generate(){
    if(busy)return;setBusy(true);setError('');controller.current=new AbortController();
    try{const result=await api<{preview_url:string;download_url:string;path:string}>(`/api/runs/${encodeURIComponent(runId)}/export`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}',signal:controller.current.signal});setState(result);}
    catch(e){if(!controller.current.signal.aborted)setError(e instanceof Error?e.message:'图片生成失败');}
    finally{if(!controller.current.signal.aborted)setBusy(false);}
  }
  return <div className="share-export"><div className="export-actions"><button type="button" className="export-button" disabled={busy} onClick={()=>void generate()}>{busy?'正在生成图片…':'导出结果图'}</button><span>为当前视频生成单张高清 PNG，包含指标、分布、代表评论和覆盖说明。</span></div>
    {error&&<p className="analysis-error" role="alert">{error}</p>}
    {state&&<div className="export-ready" role="status"><b>结果图已保存</b><a href={state.preview_url} target="_blank" rel="noreferrer">预览图片 ↗</a><a href={state.download_url} download>下载 PNG ↓</a><a href={state.preview_url} target="_blank" rel="noreferrer" aria-label="查看完整结果图片"><img src={state.preview_url} alt="当前视频的 BFSI 分享结果图"/></a></div>}
  </div>;
}
function App() {
  const [data,setData]=useState<Payload|null>(null);const [job,setJob]=useState<Job|null>(null);
  const [input,setInput]=useState('');const [limit,setLimit]=useState(100);const [mode,setMode]=useState<'full'|'quick'>('full');
  const [error,setError]=useState('');const [busy,setBusy]=useState(false);const [loading,setLoading]=useState(true);
  const [pollAttempt,setPollAttempt]=useState(0);const [saved,setSaved]=useState<Job[]>([]);
  const runId=job?.run_id;const pending=active(job?.state);
  async function refreshRuns(signal?:AbortSignal){try{setSaved(await api<Job[]>('/api/runs',{signal}));}catch{/* The selected task remains usable if history cannot be listed. */}}
  useEffect(()=>{
    const controller=new AbortController();
    async function initial() {
      try {
        const requested=new URLSearchParams(window.location.search).get('run') || localStorage.getItem('bfsi-active-run');
        const latest=requested ? {run_id:requested} : await api<{run_id:string|null}>('/api/latest',{signal:controller.signal});
        if (latest.run_id) {
          const state=await api<Job>(`/api/runs/${encodeURIComponent(latest.run_id)}`,{signal:controller.signal});
          setJob(state);setInput(state.bvid);setLimit(state.limit||100);
          if(succeeded(state.state))setData(validatePayload(await api(`/api/runs/${encodeURIComponent(state.run_id)}/result`,{signal:controller.signal})));
          if(active(state.state)||state.state==='paused')setMode(state.mode||'quick');
        } else setData(validatePayload(await api('/api/sample',{signal:controller.signal})));
        await refreshRuns(controller.signal);
      } catch(e) {if(!controller.signal.aborted)setError(e instanceof Error?e.message:'无法连接本地服务');}
      finally {if(!controller.signal.aborted)setLoading(false);}
    }
    void initial();return ()=>controller.abort();
  },[]);
  useEffect(()=>{
    if(!runId||!pending)return;
    const controller=new AbortController();let timer:ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const state=await api<Job>(`/api/runs/${encodeURIComponent(runId!)}`,{signal:controller.signal});setError('');
        if(succeeded(state.state)){
          const result=validatePayload(await api(`/api/runs/${encodeURIComponent(state.run_id)}/result`,{signal:controller.signal}));
          if(result.video.video_bvid!==state.bvid)throw new Error('结果的视频标识与任务不一致');
          setData(result);setJob(state);setBusy(false);void refreshRuns();
        }else if(state.state==='failed'||state.state==='paused'){setData(null);setJob(state);setBusy(false);void refreshRuns();}
        else {setJob(state);timer=setTimeout(()=>void poll(),1500);}
      } catch(e) {if(!controller.signal.aborted){setError(e instanceof Error?e.message:'状态读取失败');setBusy(false);}}
    }
    void poll();return ()=>{controller.abort();clearTimeout(timer);};
  },[runId,pending,pollAttempt]);
  function remember(state:Job){localStorage.setItem('bfsi-active-run',state.run_id);history.replaceState(null,'',`?run=${state.run_id}#overview`);setJob(state);}
  async function submit(e:React.FormEvent) {
    e.preventDefault();if(loading||busy||pending)return;setData(null);setJob(null);setError('');setBusy(true);setLoading(false);
    localStorage.removeItem('bfsi-active-run');history.replaceState(null,'',location.pathname+'#overview');
    try {remember(await api<Job>('/api/analyze',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({input,mode,limit:mode==='full'?null:limit})}));}
    catch(e){setError(e instanceof Error?e.message:'分析启动失败');setBusy(false);}
  }
  async function taskAction(action:'pause'|'resume'){
    if(!job)return;setError('');setBusy(true);
    try{remember(await api<Job>(`/api/runs/${encodeURIComponent(job.run_id)}/${action}`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}));setPollAttempt(n=>n+1);}
    catch(e){setError(e instanceof Error?e.message:'任务操作失败');setBusy(false);}
  }
  async function chooseRun(id:string){
    if(loading||busy||pending)return;setData(null);setError('');setLoading(true);
    try{const state=await api<Job>(`/api/runs/${encodeURIComponent(id)}`);remember(state);setInput(state.bvid);
      if(succeeded(state.state))setData(validatePayload(await api(`/api/runs/${encodeURIComponent(id)}/result`)));
      else setMode(state.mode||'quick');
    }catch(e){setError(e instanceof Error?e.message:'结果读取失败');}finally{setLoading(false);}
  }
  async function showSample(){
    if(loading||busy||pending)return;setError('');setJob(null);setData(null);setLoading(true);localStorage.removeItem('bfsi-active-run');history.replaceState(null,'',location.pathname+'#overview');
    try{setData(validatePayload(await api('/api/sample')));}catch(e){setError(e instanceof Error?e.message:'演示数据读取失败');}finally{setLoading(false);}
  }
  const scope=data?.analysis?.scope;
  const scopeLabel=data?.analysis?.synthetic?'合成演示 · 不含真实用户数据':scope==='full_accessible'?'全量分析 · 正常接口可获取集合':scope==='partial'?'部分采集 · 已获取范围':data?.analysis?.mode==='full'?'全量任务 · 采集范围见说明':'快速分析 · 采集样本';
  const controls=<section className="analysis-entry panel" aria-label="新视频分析">
    <div className="analysis-heading"><div><span className="eyebrow">ANALYZE A NEW VIDEO</span><h2>分析一个新视频</h2></div><span className="sample-tag">{mode==='full'?'全量分析 · 分页到实际结束':'Quick Analysis · 样本观察'}</span></div>
    <form onSubmit={e=>void submit(e)} className="analysis-form">
      <div className="bv-field"><label htmlFor="video-input">BV 号或 B站视频链接</label><input id="video-input" name="video" placeholder="BV… 或 https://www.bilibili.com/video/BV…" value={input} onChange={e=>setInput(e.target.value)} maxLength={2048} required disabled={loading||busy||pending}/></div>
      <div className="mode-field"><label htmlFor="analysis-mode">分析模式</label><select id="analysis-mode" value={mode} onChange={e=>setMode(e.target.value as 'full'|'quick')} disabled={loading||busy||pending}><option value="full">全量分析</option><option value="quick">快速分析</option></select></div>
      {mode==='quick'&&<div className="limit-field"><label htmlFor="sample-limit">最多分析</label><select id="sample-limit" value={limit} onChange={e=>setLimit(Number(e.target.value))} disabled={loading||busy||pending}>{[20,50,100,200].map(n=><option key={n} value={n}>{n} 条评论</option>)}</select></div>}
      <button className="analyze-button" type="submit" disabled={loading||busy||pending}>{busy||pending?'分析进行中…':'开始分析'} <Arrow/></button>
    </form>
    <p className="analysis-hint">{mode==='full'?'逐页获取正常接口可访问的一级评论、置顶与二级回复，所有不同评论均交给真实模型分析。没有固定评论数量上限；任务较大时可暂停并继续，接口限制会如实标记。':'自动获取真实评论、调用模型并计算 BFSI；结果仅代表本次采集样本，不宣称覆盖完整评论区。'}</p>
    {job&&<div className={`job-status ${job.state}`} role={job.state==='failed'?'alert':'status'} aria-live="polite">
      <div className="job-title"><b>{job.stage_label}</b><span>{job.video?.title||job.bvid}</span></div>
      <p>{job.message}</p><small>{job.mode==='full'?'全量分析':'快速分析'} · 已采集 {job.raw_unique_comments??job.collected??0} 条不同评论{job.total?` · 有效 ${job.total} 条`:''} · 已分析 {job.analyzed||0}{job.total?` / ${job.total}`:''} 条{job.batches_total?` · 批次 ${job.batches_done||0}/${job.batches_total}`:''} · {job.bvid}</small>
      {pending&&(job.stage==='semantic'||job.stage==='index'?<progress aria-label="语义分析进度" value={job.analyzed||0} max={job.total||1}/>:<progress aria-label="评论采集进行中"/>)}
      <div className="job-actions">{pending&&<button type="button" onClick={()=>void taskAction('pause')} disabled={job.state==='pausing'}>{job.state==='pausing'?'正在保存进度…':'暂停分析'}</button>}{(job.state==='paused'||job.state==='failed')&&<button type="button" onClick={()=>void taskAction('resume')} disabled={busy}>{busy?'正在继续…':'继续分析'}</button>}</div>
      {job.state==='failed'&&<p>此任务没有发布分析结果。已保存成功页和模型批次，访问恢复后可继续。</p>}
    </div>}
    {error&&<div className="analysis-error" role="alert">{error}{pending&&<button onClick={()=>{setError('');setPollAttempt(n=>n+1);}}>重新连接</button>}</div>}
    {data?.analysis&&<div className="result-provenance"><b>{scopeLabel} · {num(data.video.comment_count)} 条不同评论</b><span>原始采集 {num(typeof data.analysis.collection?.raw_unique_comments==='number'?data.analysis.collection.raw_unique_comments:data.video.comment_count)} 条 · 通过标准化并分析 {num(data.video.comment_count)} 条 · {data.analysis.model} · {data.analysis.completed_at?new Date(data.analysis.completed_at).toLocaleString('zh-CN', { timeZoneName: 'short' }):''}</span>{(data.analysis.warnings||data.analysis.collection?.warnings||[]).map((w,i)=><small key={i}>{w}</small>)}</div>}
    {data?.analysis?.run_id&&job&&succeeded(job.state)&&<ShareExport key={data.analysis.run_id} runId={data.analysis.run_id}/>}
    <div className="history-actions"><button type="button" className="history-button" onClick={()=>void showSample()} disabled={loading||busy||pending}>{data?.analysis?.synthetic?'正在查看合成演示':'查看合成演示'}</button>
    <label>已保存的任务 <select aria-label="已保存的任务" value={job?.run_id||''} onChange={e=>{if(e.target.value)void chooseRun(e.target.value);}} disabled={loading||busy||pending}><option value="">选择结果或继续任务</option>{saved.map(s=><option key={s.run_id} value={s.run_id}>{s.video?.title||s.bvid} · {s.state==='paused'?'已暂停':s.state==='partial_completed'?'部分结果':succeeded(s.state)?'已完成':s.state==='failed'?'失败，可继续':'进行中'} · {s.run_id}</option>)}</select></label></div>
  </section>;
  if(data)return <Dashboard data={data} controls={controls}/>;
  return <><header className="site-header"><a className="brand" href="#overview"><Mark/><div><b>BFSI<span>V0.1</span></b><small>Bilibili Financial Sentiment Index</small></div></a></header><main id="overview"><div className="page-intro"><div><span className="eyebrow">LISTEN TO THE MARKET CONVERSATION</span><h1>评论区情绪观察</h1><p>输入新视频，观察真实评论。</p></div></div>{controls}{loading&&<p className="empty" role="status">正在读取本地数据…</p>}</main></>;
}
createRoot(document.getElementById('root')!).render(<App/>);
