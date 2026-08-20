#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 DeepSeek 官方文档（api-docs.deepseek.com）抓取模型定价，生成 add-on 补丁条目。

数据源：https://api-docs.deepseek.com/quick_start/pricing （HTML 表格，SSR 可解析）。
页面为峰谷双价：峰值时段 01:00-04:00 / 06:00-10:00 UTC，谷时半价。
口径：**字段存峰值价**，notes 注明谷时价与时段（与人工核对时的口径一致）。

输出：dict（key -> 完整 litellm 格式记录）。抓取失败抛异常由调用方捕获。
"""

import re
import urllib.request

URL = 'https://api-docs.deepseek.com/quick_start/pricing'
SRC = 'https://api-docs.deepseek.com/quick_start/pricing/'

CAPS = {
    'supports_function_calling': True,
    'supports_response_schema': True,
    'supports_system_messages': True,
    'supports_prompt_caching': True,
    'supports_reasoning': True,
    'supports_assistant_prefill': True,
}
ENDPOINTS = ['/v1/chat/completions', '/v1/responses', '/anthropic']

# 详情页人工核对过的结构信息（上下文/版本号/并发上限）
MODEL_META = {
    'deepseek-v4-flash': {
        'version': 'DeepSeek-V4-Flash-0731',
        'max_input_tokens': 1000000, 'max_output_tokens': 384000,
        'concurrency': 2500,
    },
    'deepseek-v4-pro': {
        'version': 'DeepSeek-V4-Pro-0813',
        'max_input_tokens': 1000000, 'max_output_tokens': 384000,
        'concurrency': 500,
    },
}


def _fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'build.py'})
    with urllib.request.urlopen(req, timeout=25) as resp:
        return resp.read().decode('utf-8')


def _strip(html):
    t = re.sub(r'<script[\s\S]*?</script>', ' ', html)
    t = re.sub(r'<style[\s\S]*?</style>', ' ', t)
    t = re.sub(r'<[^>]+>', '\n', t)
    t = re.sub(r'&amp;', '&', t)
    return [l.strip() for l in re.sub(r'\n{2,}', '\n', t).split('\n') if l.strip()]


def fetch():
    """解析定价页。表格列序（按模型两列并排）：
    MODEL | flash | pro / ... / 1M INPUT (CACHE HIT) OFF-PEAK $x $y PEAK $x $y / ...
    返回 {key: record}。"""
    lines = _strip(_fetch(URL))

    # 定位 MODEL 行找模型名
    models = []
    for i, l in enumerate(lines):
        if l.upper() == 'MODEL':
            # 后续两列是模型名
            models = [x for x in lines[i + 1:i + 4] if x.startswith('deepseek')]
            break
    if not models:
        raise ValueError('未在定价页找到模型名')

    # 按价格锚点提取：OFF-PEAK / PEAK 后的 $ 值
    # 页面结构：类别名 -> OFF-PEAK -> $a -> $b -> PEAK -> $a -> $b（a=flash, b=pro）
    def grab(label):
        """找 label 后的 OFF-PEAK/PEAK 各两个价格，返回 {peak: [f, p], off: [f, p]}"""
        for i, l in enumerate(lines):
            if l.upper().startswith(label.upper()):
                seg = lines[i:i + 12]
                off_i = next((j for j, x in enumerate(seg) if x.upper() == 'OFF-PEAK'), None)
                peak_i = next((j for j, x in enumerate(seg) if x.upper() == 'PEAK'), None)
                if off_i is None or peak_i is None:
                    return None
                off = [float(x[1:]) for x in seg[off_i + 1:off_i + 3] if x.startswith('$')]
                peak = [float(x[1:]) for x in seg[peak_i + 1:peak_i + 3] if x.startswith('$')]
                return {'off': off, 'peak': peak}
        return None

    cache_hit = grab('1M INPUT TOKENS (CACHE HIT)')
    cache_miss = grab('1M INPUT TOKENS (CACHE MISS)')
    output = grab('1M OUTPUT TOKENS')
    if not all([cache_hit, cache_miss, output]):
        raise ValueError('价格表解析不完整')

    out = {}
    for idx, model in enumerate(models):
        meta = MODEL_META.get(model, {})
        peak_in = cache_miss['peak'][idx]
        peak_out = output['peak'][idx]
        peak_cr = cache_hit['peak'][idx]
        off_in = cache_miss['off'][idx]
        off_out = output['off'][idx]
        off_cr = cache_hit['off'][idx]
        notes = (
            f"Model version: {meta.get('version', model)}\n"
            'Pricing is dual-rate: PEAK hours are 01:00-04:00 and 06:00-10:00 UTC; all other hours '
            'are OFF-PEAK (half of peak). Values in this entry use PEAK rates.\n'
            f'Off-peak rates: input_cache_hit=${off_cr}/1M, input_cache_miss=${off_in}/1M, output=${off_out}/1M\n'
            f"Concurrency limit: {meta.get('concurrency', 'N/A')}\n"
            'Supports both non-thinking and thinking (default) modes; thinking output billed as '
            'regular output tokens\nFIM Completion (Beta) available in non-thinking mode only\n'
            'Base URL (OpenAI format): https://api.deepseek.com\n'
            'Base URL (Anthropic format): https://api.deepseek.com/anthropic\n'
            'Supports Chat Prefix Completion (Beta)\nSupports Responses API and Anthropic API'
        )
        rec = {
            'litellm_provider': 'deepseek',
            'mode': 'chat',
            'source': SRC,
            'notes': notes,
            'max_input_tokens': meta.get('max_input_tokens'),
            'max_output_tokens': meta.get('max_output_tokens'),
            'max_tokens': meta.get('max_output_tokens'),
            'input_cost_per_token': peak_in / 1e6,
            'output_cost_per_token': peak_out / 1e6,
            'output_cost_per_reasoning_token': peak_out / 1e6,
            'cache_read_input_token_cost': peak_cr / 1e6,
            'input_cost_per_token_cache_hit': peak_cr / 1e6,
            'supported_modalities': ['text'],
            'supported_output_modalities': ['text'],
            'supported_endpoints': ENDPOINTS,
            'supported_regions': ['global'],
        }
        rec.update(CAPS)
        # 去掉 None 值
        rec = {k: v for k, v in rec.items() if v is not None}
        out[model] = rec
    return out


if __name__ == '__main__':
    import json
    import sys
    result = fetch()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f'\n共 {len(result)} 条', file=sys.stderr)
