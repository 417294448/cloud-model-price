#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 Kimi 英文官方站（platform.kimi.ai）抓取 moonshot 模型定价，生成 add-on 补丁条目。

数据源：定价页 HTML（https://platform.kimi.ai/docs/pricing/chat）。
  官网已把 K3 / K2 各定价页合并成这一页，且价格表由 MDX 的 DocTable 组件渲染——
  markdown 源（.md）里的表格是空的（<table><tr /></table>），拿不到价格；
  价格只存在于服务端渲染的 Next.js flight 数据里（`_jsx(DocTable,{columns:[...],rows:[...]})`），
  因此改为抓 HTML 并从 flight 数据解析，不再请求 .md。
价格为美元 USD/1M tokens（英文站），与页面数据口径一致。

表格列数/列序会随官网改版变化（如 K3 表在缓存读之外新增了 Cache Write TTL
5min/1h 两列），因此解析按 DocTable 的 columns 列标题（COLUMN_FIELDS）取值，
不依赖固定位置——新增一列不会让价格整体错位；出现未收录列则显式报错。

输出：dict（key -> 完整 litellm 格式记录），由 refresh_addon.py 合并进 add-on-data.json。
抓取失败时抛异常，由调用方沿用旧补丁。
"""

import re
import urllib.request

SRC = 'https://platform.kimi.ai/docs/pricing/chat'
URL = SRC

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


# 定价表列标题（小写）-> 记录内语义字段。列标题是比位置更可靠的锚点。
COLUMN_FIELDS = {
    'input': 'input',
    'input price': 'input',
    'input price (cache miss)': 'input',
    'output': 'output',
    'output price': 'output',
    'cached input': 'cache_read',
    'cached input price': 'cache_read',
    'input (cache hit)': 'cache_read',
    'input price (cache hit)': 'cache_read',
    '5m writes': 'cache_write_5min',
    '1h writes': 'cache_write_1h',
    'cache write (5m)': 'cache_write_5min',
    'cache write (1h)': 'cache_write_1h',
    'cache write price (ttl 5min)': 'cache_write_5min',
    'cache write price (ttl 1h)': 'cache_write_1h',
}


def _balanced(seg, start):
    """seg[start] 是 '[' 或 '{'，返回与之配对的闭括号之后的下标（跳过反引号字符串）。"""
    pairs = {'[': ']', '{': '}'}
    stack = []
    i = start
    while i < len(seg):
        ch = seg[i]
        if ch == '`':
            i += 1
            while i < len(seg) and seg[i] != '`':
                i += 2 if seg[i] == '\\' else 1
        elif ch in pairs:
            stack.append(pairs[ch])
        elif ch in ']}':
            if not stack or stack[-1] != ch:
                raise ValueError(f'DocTable 参数括号不配对：{seg[start:start + 60]}')
            stack.pop()
            if not stack:
                return i + 1
        i += 1
    raise ValueError(f'DocTable 参数括号未闭合：{seg[start:start + 60]}')


def _split_items(block):
    """把 '[...]'（或 '[[..],[..]]' 的外层）按顶层逗号切分成元素文本列表。"""
    body = block[1:-1]
    items, depth, i, start = [], 0, 0, 0
    while i < len(body):
        ch = body[i]
        if ch == '`':
            i += 1
            while i < len(body) and body[i] != '`':
                i += 2 if body[i] == '\\' else 1
            i += 1
            continue
        if ch in '[{':
            depth += 1
        elif ch in ']}':
            depth -= 1
        elif ch == ',' and depth == 0:
            items.append(body[start:i])
            start = i + 1
        i += 1
    if body[start:].strip():
        items.append(body[start:])
    return [x.strip() for x in items]


def _column_title(item):
    """列定义 -> 列标题。兼容 {title:`Input`} 与 {title:_jsx(Tooltip,{...children:`Cached Input`})}。"""
    m = re.search(r'title:', item)
    if not m:
        raise ValueError(f'列定义缺少 title：{item[:80]}')
    rest = item[m.end():]
    if rest.startswith('`'):
        return rest[1:rest.index('`', 1)]
    m2 = re.search(r'children:`([^`]*)`', rest)
    if not m2:
        raise ValueError(f'列标题无法解析：{item[:120]}')
    return m2.group(1)


def _parse_row(row, titles):
    """一行 -> {model, input, output, cache_read, cache_write_5min?, cache_write_1h?}。"""
    prices = [float(x) for x in re.findall(r'\$([0-9]+(?:\.[0-9]+)?)\s*/\s*MTok', row)]
    model = None
    for s in re.findall(r'`([^`]*)`', row):
        if s.startswith('/') or s.startswith('$'):
            continue
        model = s
    if not model:
        raise ValueError(f'价格行缺少模型名：{row[:80]}')
    if len(prices) != len(titles) - 1:
        raise ValueError(
            f'{model} 价格数与表格列数不一致（{len(titles) - 1} 列 / {len(prices)} 值）：'
            f'{row[:120]}，官网表格结构可能已变化')
    out = {'model': model}
    for title, price in zip(titles[1:], prices):
        field = COLUMN_FIELDS.get(title.strip().lower())
        if not field:
            raise ValueError(f'{model} 出现未收录的价格列「{title}」，官网表格结构可能已变化')
        out[field] = price
    if out.get('input') is None or out.get('output') is None or out.get('cache_read') is None:
        raise ValueError(f'定价表解析不完整（缺输入价/输出价/缓存读价）：{row[:120]}')
    return out


def _parse_page(html):
    """解析定价页 HTML 里的 Next.js flight 数据，返回
    [{model, cache_read, input, output, cache_write_5min?, cache_write_1h?}, ...]，价格 USD/1M。"""
    rows = {}
    for m in re.finditer(r'_jsx\(DocTable,\{', html):
        seg = html[m.end():]
        ci, ri = seg.find('columns:['), seg.find('rows:[')
        if ci < 0 or ri < 0:
            continue  # 空表（只有表头样式定义，无数据）
        columns = seg[ci + len('columns:'):_balanced(seg, ci + len('columns:'))]
        rows_at = ri + len('rows:')
        rows_block = seg[rows_at:_balanced(seg, rows_at)]
        titles = [_column_title(item) for item in _split_items(columns)]
        for item in _split_items(rows_block):
            row = _parse_row(item, titles)
            rows[row['model']] = row
    return list(rows.values())


# 官网定价页公告的 K2 系列退役计划（2026-10 起，K2 系列由 K3.1 Pro 取代）
K2_RETIRE = ('K2 series is being retired: unavailable to newly registered users from October 31, 2026, '
             'and retired for all users by the end of December 2026. ')

# 各模型的能力/模态/说明（详情页人工核对后的口径，价格由脚本实时刷新）
MODEL_META = {
    'kimi-k3.1-pro': {
        'max_input_tokens': 1048576,
        'modalities': ['text', 'image', 'video'],
        'caps': {'supports_vision': True, 'supports_video_input': True},
        'notes': ('Kimi K3.1 Pro, post-trained successor to Kimi K3 (3T-class open-weight model). '
                  '1M-token context, flat pay-as-you-go pricing (no context-length tiering). '
                  'Thinking is always on; reasoning effort configurable via top-level reasoning_effort '
                  '(low/high/max, default max). Supports tool calling, strict JSON Schema, Partial Mode and '
                  'context caching (cache write billed separately for 5m / 1h TTL tiers). Vision input accepts '
                  'base64 or ms:// file ids only (public image URLs are not supported). '),
    },
    'kimi-k3.1-pro-highspeed': {
        'max_input_tokens': 1048576,
        'modalities': ['text', 'image', 'video'],
        'caps': {'supports_vision': True, 'supports_video_input': True, 'supports_speed': True},
        'notes': ('High-speed variant of kimi-k3.1-pro (2-3x faster, same model). 1M-token context. '
                  'Thinking is always on; reasoning effort configurable via reasoning_effort (low/high/max). '),
    },
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
        'notes': ('Coding-focused model. Thinking mode only. Supports text/image/video input. 256K context. '
                  + K2_RETIRE),
    },
    'kimi-k2.7-code-highspeed': {
        'max_input_tokens': 262144,
        'modalities': ['text', 'image', 'video'],
        'caps': {'supports_vision': True, 'supports_video_input': True, 'supports_speed': True},
        'notes': ('High-speed variant of kimi-k2.7-code (same model, ~180 output tokens/s, up to ~260 '
                  'in short-context scenarios). Thinking mode only. ' + K2_RETIRE),
    },
    'kimi-k2.6': {
        'max_input_tokens': 262144,
        'modalities': ['text', 'image', 'video'],
        'caps': {'supports_vision': True, 'supports_video_input': True},
        'notes': ('General-purpose model with stable long-horizon coding, instruction following and '
                  'self-correction. Supports text/image/video input, thinking and non-thinking modes. '
                  + K2_RETIRE),
    },
}


def fetch():
    """抓取定价页，返回 {key: record}。抓取/解析失败抛异常由调用方捕获。"""
    all_rows = _parse_page(_fetch(URL))
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
            'cache_read_input_token_cost': row['cache_read'] / 1e6,
            'input_cost_per_token_cache_hit': row['cache_read'] / 1e6,
            'supported_regions': ['global'],
            'supported_endpoints': ENDPOINTS,
        }
        # 缓存写入价：5min 档（未指定 TTL 时的默认档）与 1h 档，字段名跟随上游 litellm 惯例
        if row.get('cache_write_5min') is not None:
            rec['cache_creation_input_token_cost'] = row['cache_write_5min'] / 1e6
        if row.get('cache_write_1h') is not None:
            rec['cache_creation_input_token_cost_above_1hr'] = row['cache_write_1h'] / 1e6
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
