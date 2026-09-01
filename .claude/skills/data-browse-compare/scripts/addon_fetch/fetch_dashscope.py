#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从千问AI平台（qianwenai.com，阿里云百炼新平台）抓取 DashScope 模型定价，生成 add-on 补丁条目。

数据源（均为公开免登录的 POST 接口，纯 urllib 直连）：
  - 模型卡片 https://platform-home.qianwenai.com/data/api.json?product=AliyunDeliveryService&action=ListModelSeries
    分页返回模型卡片：上下文/最大输出/模态（InferenceMetadata）/能力（Features）/多档价格（MultiPrices）
  - 分类定价 https://cs-data.qianwenai.com/data/api.json（api=...listModelPrices）
    兜底价格源，含峰谷价（timeBand=peak/offpeak）与缓存命中价

价格政策：
  - 官网价格为人民币（CNY/1M tokens），按 1 USD = 6 CNY 换算（与国际站美元价核对一致）
  - 多档位（按输入长度分档）取最低档为代表价，完整档位表写入 notes
  - 峰谷定价取 peak 价，notes 标注 off-peak 价
  - 缓存命中价优先 input_token_cache（隐式），缺失取 input_token_cache_read（显式，5min TTL）

结构信息（上下文/模态/能力）由 ListModelSeries 实时返回，无需人工维护 MODEL_META。
key 跟随上游 dashscope/ 前缀惯例。

输出：dict（key -> 完整 litellm 格式记录）。抓取失败抛异常由调用方捕获。
"""

import json
import urllib.parse
import urllib.request

SERIES_URL = 'https://platform-home.qianwenai.com/data/api.json?product=AliyunDeliveryService&action=ListModelSeries'
PRICE_URL = ('https://cs-data.qianwenai.com/data/api.json?action=BroadScopeAspnGateway&product=sfm_bailian'
             '&api=zeldaHttp.dashscopeModel./zelda/api/v1/modelCenter/listModelPrices')
SRC = 'https://platform.qianwenai.com/pricing/api ; https://www.qianwenai.com/models'

RATE = 6.0  # 1 USD = 6 CNY
MAX_PAGES = 10
UA = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0 (build.py)',
    'Content-Type': 'application/x-www-form-urlencoded',
}
MOD_MAP = {'Text': 'text', 'Image': 'image', 'Audio': 'audio', 'Video': 'video'}
# 非 chat 模型前缀（图像/语音/视频/向量/重排等另有 mode，不在本补丁范围）
SKIP_PREFIX = ('qwen-image', 'qwen3-asr', 'qwen-audio', 'qwen3-tts', 'qwen-omni',
               'qwen3.5-omni', 'qwen3.5-livetranslate', 'qwen3.5-ocr', 'qwen-vl-ocr',
               'qwen-mt-', 'qwen-deep-research', 'text-embedding', 'tongyi-embedding',
               'qwen3.7-text-embedding', 'qwen3-rerank', 'qwen2.5-vl-embedding',
               'qwen3-vl-embedding', 'wan', 'happyhorse', 'fun-', 'cosyvoice',
               'z-image', 'facechain', 'image-', 'shoemodel', 'virtualmodel',
               'voice-enrollment', 'wanx')


def _post(url, body):
    req = urllib.request.Request(url, data=body.encode('utf-8'), headers=UA)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode('utf-8'))


def _fetch_series():
    """分页抓取模型卡片。返回 {model_id: card}（同名保留带 MultiPrices 的）。"""
    models = {}
    for page_no in range(1, MAX_PAGES + 1):
        params = {'PageNo': page_no, 'PageSize': 50, 'OrderType': 'Featured', 'Language': 'zh-CN'}
        body = ('product=AliyunDeliveryService&action=ListModelSeries&sec_token=&params='
                + urllib.parse.quote(json.dumps(params)))
        d = _post(SERIES_URL, body)
        groups = (d.get('data') or {}).get('Data') or []
        if not groups:
            break
        for g in groups:
            for it in g.get('Items') or []:
                m = it.get('Model')
                if not m:
                    continue
                if m not in models or (it.get('MultiPrices') and not models[m].get('MultiPrices')):
                    models[m] = it
    if not models:
        raise ValueError('ListModelSeries 未返回任何模型卡片')
    return models


def _fetch_prices():
    """抓取文本生成分类定价（Qwen + Other 第三方）作兜底。返回 {itemCode: [tier,...]}。"""
    prices = {}
    for lv2 in ('Qwen', 'Other'):
        inner = {'region': 'cn-beijing', 'categoryLevel1': 'Text-Generation',
                 'categoryLevel2': lv2, 'itemCode': '', 'batch': False, 'pageNo': 1, 'pageSize': 100}
        params = {'Api': 'zeldaHttp.dashscopeModel./zelda/api/v1/modelCenter/listModelPrices',
                  'Data': {'cornerstoneParam': {'consoleSite': 'QIANWENAI', 'domain': 'platform.qianwenai.com',
                                                'productCode': 'p_efm', 'protocol': 'V2', 'xsp_lang': 'zh-CN'},
                           'input': inner}}
        body = ('product=sfm_bailian&action=BroadScopeAspnGateway&sec_token=&region=cn-beijing&params='
                + json.dumps(params, separators=(',', ':')))
        d = _post(PRICE_URL, body)
        lst = ((d.get('data') or {}).get('DataV2') or {}).get('data', {})
        lst = (lst.get('data') or {}).get('list') or []
        for it in lst:
            code = it.get('itemCode')
            if code:
                prices.setdefault(code, []).append(it)
    # 兜底源失败不致命（卡片价已覆盖大部分），只兜底空 dict
    return prices


def _cny(cny_per_1m):
    return float(cny_per_1m) / RATE / 1e6


def _is_chat(model_id, item):
    if model_id.lower().startswith(SKIP_PREFIX):
        return False
    req = (item.get('InferenceMetadata') or {}).get('RequestModality') or []
    return 'Text' in req


def _pick_tiers(model_id, item, prices_map):
    """返回 [{range, timeBand, prices:{type:price}}]，优先卡片 MultiPrices，缺失用卡片顶层 Prices 或定价页兜底。

    注意：部分卡片（如 qwen3.8-flash / qwen3.8-27b）的 MultiPrices 是空占位
    （[{"Prices":[{}]}]），真实价格放在顶层 Prices 字段，必须兜底读取。"""
    tiers = []
    for t in item.get('MultiPrices') or []:
        pr = {p['Type']: p['Price'] for p in t.get('Prices', []) if 'Type' in p}
        if pr.get('input_token') and pr.get('output_token'):
            tiers.append({'range': (t.get('RangeName') or '').replace('输入', 'input'),
                          'timeBand': t.get('TimeBand') or 'standard', 'prices': pr})
    if tiers:
        return tiers
    # 兜底 1：卡片顶层 Prices（MultiPrices 空占位时真实价格在此）
    top_pr = {p['Type']: p['Price'] for p in item.get('Prices') or [] if 'Type' in p}
    if top_pr.get('input_token') and top_pr.get('output_token'):
        return [{'range': 'all', 'timeBand': 'standard', 'prices': top_pr}]
    # 兜底 2：定价页 listModelPrices
    for t in prices_map.get(model_id) or []:
        pr = {p['type']: p['price'] for p in t.get('prices', []) if 'type' in p}
        if pr.get('input_token') and pr.get('output_token'):
            tiers.append({'range': (t.get('rangeName') or 'all').replace('输入', 'input'),
                          'timeBand': t.get('timeBand') or 'standard', 'prices': pr})
    return tiers


def _select_tier(tiers):
    """代表档：优先 standard 第一档；含峰谷时取 peak 档。"""
    std = [t for t in tiers if t['timeBand'] in ('standard', 'all', '?', None)]
    peak = [t for t in tiers if t['timeBand'] == 'peak']
    if peak:
        return peak[0]
    return std[0] if std else tiers[0]


def _build_notes(tiers, chosen, feats):
    lines = []
    for t in tiers:
        pr = t['prices']
        ch = pr.get('input_token_cache') or pr.get('input_token_cache_read') or '-'
        tb = f" [{t['timeBand']}]" if t['timeBand'] not in (None, 'standard', 'all', '?') else ''
        lines.append(f"  {t['range']}{tb}: input CNY {pr.get('input_token')}, "
                     f"cache-hit CNY {ch}, output CNY {pr.get('output_token')} per 1M tokens")
    parts = ["Tiered pricing by input length (CNY per 1M tokens):\n" + "\n".join(lines)]
    used = 'peak-hour rate' if chosen['timeBand'] == 'peak' else f"lowest tier ({chosen['range']})"
    parts.append(f"This entry uses the {used}; converted at 1 USD = {RATE} CNY.")
    pr = chosen['prices']
    if pr.get('input_token_cache_creation_5m') or pr.get('input_token_cache_read'):
        parts.append(f"Explicit prompt caching: creation CNY {pr.get('input_token_cache_creation_5m', '-')}/1M, "
                     f"read within 5 min CNY {pr.get('input_token_cache_read', '-')}/1M.")
    if feats:
        parts.append(f"Features: {', '.join(feats)}")
    return "\n".join(parts)


def _build_entry(model_id, item, prices_map):
    mi = item.get('ModelInfo') or {}
    im = item.get('InferenceMetadata') or {}
    feats = item.get('Features') or []
    tiers = _pick_tiers(model_id, item, prices_map)
    if not tiers:
        return None
    chosen = _select_tier(tiers)
    P = chosen['prices']
    cache_hit = P.get('input_token_cache') or P.get('input_token_cache_read')
    # MaxInputTokens 缺失时用 ContextWindow 兜底（官方卡片字段）；max_output 无依据则不写
    max_input = mi.get('MaxInputTokens') or mi.get('ContextWindow')

    rec = {
        'litellm_provider': 'dashscope',
        'mode': 'chat',
        'source': SRC,
        'notes': _build_notes(tiers, chosen, feats),
        'max_input_tokens': max_input,
        'max_output_tokens': mi.get('MaxOutputTokens'),
        'max_tokens': mi.get('MaxOutputTokens'),
        'input_cost_per_token': _cny(P['input_token']),
        'output_cost_per_token': _cny(P['output_token']),
        'supported_modalities': [MOD_MAP.get(m, m.lower()) for m in im.get('RequestModality', [])],
        'supported_output_modalities': [MOD_MAP.get(m, m.lower()) for m in im.get('ResponseModality', [])],
        'supported_endpoints': ['/v1/chat/completions', '/v1/responses'],
        'supported_regions': ['cn'],
        'supports_function_calling': 'function-calling' in feats,
        'supports_response_schema': 'structured-outputs' in feats,
        'supports_system_messages': True,
        'supports_prompt_caching': 'cache' in feats or bool(cache_hit),
        'supports_assistant_prefill': 'prefix-completion' in feats,
        'supports_reasoning': bool(mi.get('ReasoningMaxOutputTokens')),
    }
    if cache_hit:
        rec['cache_read_input_token_cost'] = _cny(cache_hit)
        rec['input_cost_per_token_cache_hit'] = _cny(cache_hit)
    return {k: v for k, v in rec.items() if v is not None}


def fetch():
    models = _fetch_series()
    prices_map = _fetch_prices()
    out = {}
    for mid, item in sorted(models.items()):
        if not _is_chat(mid, item):
            continue
        rec = _build_entry(mid, item, prices_map)
        if rec is None:
            continue  # 无价格（第三方渠道/未定价），跳过
        out['dashscope/' + mid] = rec
    if not out:
        raise ValueError('未生成任何 dashscope 补丁条目')
    return out


if __name__ == '__main__':
    import sys
    result = fetch()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f'\n共 {len(result)} 条', file=sys.stderr)
