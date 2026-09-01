#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 z.ai 官方文档站（docs.z.ai）抓取 GLM 模型定价，生成 add-on 补丁条目。

数据源：
  - 定价总表 https://docs.z.ai/guides/overview/pricing.md（markdown 源，价格 USD/1M）
  - 各模型详情页 https://docs.z.ai/guides/llm/<model>.md / vlm/<model>.md（上下文/模态/能力）
价格为美元，与页面数据口径一致。key 跟随上游 zai/ 前缀惯例。

输出：dict（key -> 完整 litellm 格式记录）。抓取失败抛异常由调用方捕获。
"""

import re
import urllib.request

PRICING_URL = 'https://docs.z.ai/guides/overview/pricing.md'
SRC = 'https://docs.z.ai/guides/overview/pricing'

ENDPOINTS = ['/v1/chat/completions', '/v1/responses', '/anthropic']
CAPS_TEXT = {
    'supports_function_calling': True,
    'supports_response_schema': True,
    'supports_system_messages': True,
    'supports_prompt_caching': True,
    'supports_reasoning': True,
}

# 模型详情页确认的上下文/模态/能力（价格由脚本实时刷新，这些人工核对过的结构信息固化）
# ctx/maxout 为 token 数；vision 模型带 supports_vision/video_input
MODEL_META = {
    'glm-5.3':       {'ctx': 1000000, 'maxout': 128000, 'vis': False},
    'glm-5.3-flash': {'ctx': 200000,  'maxout': 128000, 'vis': False},
    'glm-4.5-flash': {'ctx': 128000,  'maxout': 96000,  'vis': False},
    'glm-5.2':       {'ctx': 1000000, 'maxout': 128000, 'vis': False},
    'glm-5.1':       {'ctx': 200000,  'maxout': 128000, 'vis': False},
    'glm-5':         {'ctx': 200000,  'maxout': 128000, 'vis': False},
    'glm-5-turbo':   {'ctx': 200000,  'maxout': 128000, 'vis': False},
    'glm-4.7':       {'ctx': 200000,  'maxout': 128000, 'vis': False},
    'glm-4.7-flashx': {'ctx': 200000, 'maxout': 128000, 'vis': False},
    'glm-4.7-flash': {'ctx': 200000,  'maxout': 128000, 'vis': False},
    'glm-4.6':       {'ctx': 200000,  'maxout': 128000, 'vis': False},
    'glm-4.5':       {'ctx': 128000,  'maxout': 96000,  'vis': False},
    'glm-4.5-x':     {'ctx': 128000,  'maxout': 96000,  'vis': False},
    'glm-4.5-air':   {'ctx': 128000,  'maxout': 96000,  'vis': False},
    'glm-4.5-airx':  {'ctx': 128000,  'maxout': 96000,  'vis': False},
    'glm-4-32b-0414-128k': {'ctx': 128000, 'maxout': 16000, 'vis': False},
    # 视觉模型
    'glm-5v-turbo':  {'ctx': 200000,  'maxout': 128000, 'vis': True},
    'glm-4.6v':      {'ctx': 128000,  'maxout': None,   'vis': True},
    'glm-4.6v-flashx': {'ctx': 128000, 'maxout': None,  'vis': True},
    'glm-4.6v-flash': {'ctx': 128000, 'maxout': None,   'vis': True},
    'glm-4.5v':      {'ctx': 128000,  'maxout': 16000,  'vis': True},
    'glm-ocr':       {'ctx': None,    'maxout': None,   'vis': True, 'ocr': True},
}


def _fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'build.py'})
    with urllib.request.urlopen(req, timeout=25) as resp:
        return resp.read().decode('utf-8')


def _num(s):
    s = s.strip()
    if s.lower() in ('free', '-', '\\', ''):
        return 0.0 if s.lower() == 'free' else None
    # 划线价（调价时新价格以 "~~旧价~~ 新价" 呈现）：优先取划线后紧跟的实付价。
    # 官网 markdown 里美元符带反斜杠转义（~~\$0.15~~ \$0.075），`\$?` 兼容两种情况。
    # 例：~~\$0.15~~ \$0.075 -> 0.075（旧价 0.15 已不生效，取新价）
    m = re.search(r'~~\\?\$?\s*[0-9]+(?:\.[0-9]+)?\s*~~\s*\\?\$?\s*([0-9]+(?:\.[0-9]+)?)', s)
    if m:
        return float(m.group(1))
    m = re.search(r'\\?\$?\s*([0-9]+(?:\.[0-9]+)?)', s)
    return float(m.group(1)) if m else None


def _parse_pricing(md):
    """解析定价总表 markdown。文本/视觉表列：Model|Input|Cached Input|Cached Storage|Output。
    返回 {model_name: {input, cache, output}}（USD/1M）。"""
    result = {}
    # 表格行：| GLM-5.3 | $1.4 | $0.26 | Limited-time Free | $4.4 |
    for m in re.finditer(r'^\|\s*(GLM-[A-Za-z0-9.\-]+)\s*\|([^|]+)\|([^|]+)\|([^|]+)\|([^|]+)\|', md, re.M):
        name = m.group(1).strip()
        inp = _num(m.group(2))
        cache = _num(m.group(3))
        outp = _num(m.group(5))
        if inp is None and outp is None:
            continue
        result[name.lower()] = {'input': inp, 'cache': cache, 'output': outp}
    return result


def fetch():
    md = _fetch(PRICING_URL)
    table = _parse_pricing(md)
    if not table:
        raise ValueError('未解析到任何模型价格行')

    out = {}
    for model, meta in MODEL_META.items():
        price = table.get(model)
        if price is None:
            continue  # 定价页没有的模型跳过（可能已下架）
        rec = {
            'litellm_provider': 'zai',
            'mode': 'chat',
            'source': SRC,
            'supported_regions': ['global'],
            'supported_endpoints': ENDPOINTS,
        }
        if meta.get('ctx'):
            rec['max_input_tokens'] = meta['ctx']
        if meta.get('maxout'):
            rec['max_output_tokens'] = meta['maxout']
            rec['max_tokens'] = meta['maxout']
        if price['input'] is not None:
            rec['input_cost_per_token'] = price['input'] / 1e6
        if price['output'] is not None:
            rec['output_cost_per_token'] = price['output'] / 1e6
        if price['cache'] is not None:
            rec['cache_read_input_token_cost'] = price['cache'] / 1e6
            rec['input_cost_per_token_cache_hit'] = price['cache'] / 1e6

        if meta.get('ocr'):
            rec['notes'] = ('OCR model. Input: PDF (<=50MB, <=100 pages) or images (JPG/PNG <=10MB). '
                            'Output: Text / Image Links / MD Documents. ')
            rec['supports_vision'] = True
            rec['supported_modalities'] = ['text', 'image']
        elif meta['vis']:
            rec.update(CAPS_TEXT)
            rec['supports_vision'] = True
            rec['supports_video_input'] = True
            rec['supported_modalities'] = ['text', 'image', 'video']
            rec['notes'] = ''
        else:
            rec.update(CAPS_TEXT)
            rec['supported_modalities'] = ['text']
            rec['notes'] = ''
        rec['supported_output_modalities'] = ['text']
        rec['notes'] = (rec['notes'] +
                        'Cached Input Storage is free for a limited time. '
                        'Prices per the official pricing page (USD/1M tokens).').strip()
        out['zai/' + model] = rec
    return out


if __name__ == '__main__':
    import json
    import sys
    result = fetch()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f'\n共 {len(result)} 条', file=sys.stderr)
