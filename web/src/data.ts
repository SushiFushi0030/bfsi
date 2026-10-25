export type Counts = Record<string, number>;
export type Rate = { count: number; denominator: number; display_percent: number };
export type Target = { raw_name: string; normalized_name: string | null; target_type: string };
export type ContextComment = { comment_id: string; raw_text: string; depth: number };
export type Comment = {
  comment_id: string; raw_text: string; summary: string; market_stance: string;
  targets: Target[]; action_intent: string; confidence: string; depth: number;
  context_used?: { direct_parent: ContextComment | null; root: ContextComment | null; ancestor_chain: ContextComment[] };
};
export type Topic = { target_name: string; total_mentions: number; share_of_all_comments_display_pct: number };
export type Asset = {
  target_name: string; total_mentions: number; directional_comments: number;
  target_BFSI_display: number; target_signal_coverage_display_pct: number;
  no_directional_signal: boolean; bullish_comments: number; bearish_comments: number; mixed_comments: number;
};
export type Payload = {
  payload_version: string;
  analysis?: { synthetic?: boolean; run_id?: string; bvid?: string; model?: string; completed_at?: string; scope?: string; mode?: 'full'|'quick'; warnings?: string[]; collection?: { warnings?: string[]; [key:string]: unknown }; requested_limit?: number|null };
  video: { video_oid: string; video_bvid?: string | null; title: string | null; comment_count: number };
  headline: {
    bfsi: number; signal_coverage: number; disagreement: number; directional_comments: number;
    no_directional_signal: boolean; no_bullish_bearish_signal: boolean;
  };
  distributions: Record<string, Counts>;
  distribution_percentages: Record<string, Counts>;
  auxiliary_metrics: Record<string, Rate>;
  top_financial_topics: Topic[];
  asset_market_target_indices: Asset[];
  representative_comments: Record<string, Comment[]>;
  commentary: string;
  methodology: { commentary_only_bands: { near_neutral_bfsi_inclusive: number[]; low_coverage_below_percent: number; high_disagreement_at_least: number } };
};

export const STANCE: Record<string, string> = { bullish: '看多', bearish: '看空', mixed: '多向 / 混合', neutral: '方向中性', unclear: '方向不明', not_applicable: '未表达方向' };
export const EMOTION: Record<string, string> = { neutral: '中性', humorous: '幽默', confused: '困惑', pessimistic: '悲观', frustrated: '懊恼', anxious: '焦虑', excited: '兴奋', optimistic: '乐观', angry: '愤怒', fearful: '恐惧' };
export const ACTION: Record<string, string> = { ask_advice: '寻求建议', buy: '买入', add: '加仓', hold: '持有', sell: '卖出', reduce: '减仓', wait: '观望', unclear: '意图不明', no_action: '未表达操作', not_applicable: '不涉及投资操作' };
export const RELEVANCE: Record<string, string> = { financial: '财经讨论', social: '社交互动', other: '其他内容', unclear: '语义不明' };
export const num = (value: number, digits = 0) => Number.isFinite(value) ? value.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits }) : '—';
export const pct = (value: number) => `${num(value, 1)}%`;
export const clamp = (value: number) => Number.isFinite(value) ? Math.min(100, Math.max(0, value)) : 0;
export const count = (data: Payload, group: string, key: string) => data.distributions[group]?.[key] ?? 0;
export const percent = (data: Payload, group: string, key: string) => data.distribution_percentages[group]?.[key] ?? 0;

export function directionLabel(data: Payload) {
  const { bfsi, no_directional_signal } = data.headline;
  if (no_directional_signal) return '暂无方向信号';
  if (bfsi === 50) return '多空平衡';
  const [low, high] = data.methodology.commentary_only_bands.near_neutral_bfsi_inclusive;
  if (bfsi >= low && bfsi <= high) return `接近中性 · 略偏${bfsi < 50 ? '空' : '多'}`;
  return bfsi < 50 ? '方向偏空' : '方向偏多';
}

export function validatePayload(value: unknown): Payload {
  if (!value || typeof value !== 'object') throw new Error('数据格式不正确');
  const d = value as Payload;
  if (!d.headline || !d.video || !d.distributions || !d.distribution_percentages || !d.auxiliary_metrics || !d.methodology?.commentary_only_bands) throw new Error('数据缺少必要字段');
  if (![d.headline.bfsi, d.headline.signal_coverage, d.headline.disagreement, d.headline.directional_comments, d.video.comment_count].every(x => typeof x === 'number' && Number.isFinite(x))) throw new Error('指标数据格式不正确');
  if (typeof d.commentary !== 'string' || typeof d.video.video_oid !== 'string') throw new Error('数据格式不正确');
  return { ...d,
    top_financial_topics: Array.isArray(d.top_financial_topics) ? d.top_financial_topics : [],
    asset_market_target_indices: Array.isArray(d.asset_market_target_indices) ? d.asset_market_target_indices : [],
    representative_comments: d.representative_comments || {},
  };
}
