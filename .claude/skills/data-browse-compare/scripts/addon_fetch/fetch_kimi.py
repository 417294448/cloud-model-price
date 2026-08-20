#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 Kimi 英文官方站（platform.kimi.ai）抓取 moonshot 模型定价，生成 add-on 补丁条目。

数据源：各模型定价页的 markdown 源（.md 后缀直出）。
  - https://platform.kimi.ai/docs/pricing/chat-k3.md
  - https://platform.kimi.ai/docs/pricing/chat-k27-code.md
  - https://platform.kimi.ai/docs/pricing/chat-k26.md
价格为美元 USD/1M tokens（英文站），与页面数据口径一致。

输出：dict（key -> 完整 litellm 格式记录），由 refresh_addon.py 合并进 add-on-data.json。
抓取失败时返回 None，由调用方决定沿用旧补丁。
"""

import re
import urllib.request

BASE = 'https://platform.kimi.ai/docs/pricing/{}.md'
SRC = 'https://platform.kimi.ai/docs/pricing/chat'

# 各定价页包含的模型（页内可能有多个 row，如 highspeed 变体）
PAGES = ['chat-k3', 'chat-k27-code', 'chat-k26']

COMMON_CAPS = {
    'supports_function_calling': True,
    'supports_response_schema': True,
    'supports_system_messages': True,
    'supports_prompt_caching': True,
    'supports_reasoning': True,
}
ENDPOINTS = ['/v1/chat/completions']


def _fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'build.py'})
    with urllib.request.urlopen(req, timeout=25) as resp:
        return resp.read().decode('utf-8')


def _to_int(s):
    return int(s.replace(',', '').strip())


def _parse_price_cell(cell):
    """<>{"$"}0.30</> 或 "$0.30" -> 0.30"""
    m = re.search(r'\$?\s*([0-9]+(?:\.[0-9]+)?)', cell)
    return float(m.group(1)) if m else None


def _parse_page(md):
    """解析定价页 markdown，返回 [(model, cache_hit, in_miss, out, ctx), ...]，价格 USD/1M。"""
    # 行形如 ["kimi-k3", "1M tokens", <>{"$"}0.30</>, <>{"$"}3.00</>, <>{"$"}15.00</>, "1,048,576 tokens"]
    rows = []
    for m in re.finditer(r'\[\s*"([^"]+)"\s*,\s*"1M tokens"\s*,\s*([^\]]+?)\]', md):
        model = m.group(1)
        cells = re.findall(r'<\>\{"\$"\}([0-9.]+)</>|"([0-9,]+ tokens)"', m.group(2))
        # cells 是 (价格, 上下文) 元组的混合，按位置提取
        flat = [a or b for a, b in cells]
        nums = [x for x in flat if 'tokens' not in x]
        ctxs = [x for x in flat if 'tokens' in x]
        if len(nums) < 3 or not ctxs:
            continue
        rows.append({
            'model': model,
            'cache_hit': float(nums[0]),
            'input': float(nums[1]),
            'output': float(nums[2]),
            'context': _to_int(ctxs[0].replace(' tokens', '')),
        })
    return rows


# 各模型的能力/模态/说明（详情页人工核对后的口径，价格由脚本实时刷新）
MODEL_META = {
    'kimi-k3': {
        'max_input_tokens': 1048576,
        'max_output_tokens': 128000,
        'modalities': ['text', 'image', 'video'],
        'caps': {'supports_vision': True, 'supports_video_input': True},
        'notes': ('Kimi flagship for long-horizon coding and end-to-end knowledge work. '
                  'Always runs with thinking enabled; reasoning effort configurable via top-level '
                  'reasoning_effort (low/high/max, default max). Supports tool calling, JSON Mode, '
                  'structured output, Partial Mode, context caching, tool_choice and dynamic tool loading. '),
    },
    'kimi-k2.7-code': {
        'max_input_tokens': 262144,
        'modalities': ['text', 'image', 'video'],
        'caps': {'supports_vision': True, 'supports_video_input': True},
        'notes': ('Coding-focused model. Thinking mode only. Supports text/image/video input. 256K context. '),
    },
    'kimi-k2.7-code-highspeed': {
        'max_input_tokens': 262144,
        'modalities': ['text', 'image', 'video'],
        'caps': {'supports_vision': True, 'supports_video_input': True, 'supports_speed': True},
        'notes': ('High-speed variant of kimi-k2.7-code (same model, ~180 output tokens/s, up to ~260 '
                  'in short-context scenarios). Thinking mode only. '),
    },
    'kimi-k2.6': {
        'max_input_tokens': 262144,
        'modalities': ['text', 'image', 'video'],
        'caps': {'supports_vision': True, 'supports_video_input': True},
        'notes': ('General-purpose model with stable long-horizon coding, instruction following and '
                  'self-correction. Supports text/image/video input, thinking and non-thinking modes. '),
    },
}


def fetch():
    """抓取所有定价页，返回 {key: record}。任一页面抓取/解析失败抛异常由调用方捕获。"""
    all_rows = []
    for page in PAGES:
        md = _fetch(BASE.format(page))
        all_rows.extend(_parse_page(md))
    if not all_rows:
        raise ValueError('未解析到任何模型价格行')

    out = {}
    for row in all_rows:
        model = row['model']
        meta = MODEL_META.get(model, {})
        rec = {
            'litellm_provider': 'moonshot',
            'mode': 'chat',
            'source': SRC,
            'input_cost_per_token': row['input'] / 1e6,
            'output_cost_per_token': row['output'] / 1e6,
            'cache_read_input_token_cost': row['cache_hit'] / 1e6,
            'input_cost_per_token_cache_hit': row['cache_hit'] / 1e6,
            'supported_regions': ['global'],
            'supported_endpoints': ENDPOINTS,
        }
        if meta.get('max_input_tokens'):
            rec['max_input_tokens'] = meta['max_input_tokens']
        if meta.get('max_output_tokens'):
            rec['max_output_tokens'] = meta['max_output_tokens']
            rec['max_tokens'] = meta['max_output_tokens']
        rec.update(COMMON_CAPS)
        rec.update(meta.get('caps', {}))
        rec['supported_modalities'] = meta.get('modalities', ['text'])
        rec['supported_output_modalities'] = ['text']
        rec['notes'] = (meta.get('notes', '') +
                        'Prices per the official pricing page (USD/1M tokens).')
        out['moonshot/' + model] = rec
    return out


if __name__ == '__main__':
    import json
    result = fetch()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f'\n共 {len(result)} 条', file=__import__('sys').stderr)
