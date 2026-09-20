#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 z.ai 官方文档站（docs.z.ai）抓取 GLM 模型定价，生成 add-on 补丁条目。

数据源：
  - 定价总表 https://docs.z.ai/guides/overview/pricing.md（markdown 源，价格 USD/1M）
  - 各模型详情页 https://docs.z.ai/guides/llm/<model>.md / vlm/<model>.md（上下文/模态/能力）
价格为美元，与页面数据口径一致。key 跟随上游 zai/ 前缀惯例。

定价总表按 '### ' 小节组织，只有 Latest / Text / Vision 三节是按 token 计费的 chat 模型；
其余小节（Built-in Tools、Image/Video Generation、Audio、Agents）计费口径不同，不解析。

MODEL_META 是人工从详情页核对后固化的结构信息（价格实时刷、结构人工维护）。定价页有而
MODEL_META 没有的模型会按纯价格收录并打告警——不要退回「遍历 MODEL_META」的写法，
那样新模型会被静默丢弃、页面上无声无息地缺一条。

输出：dict（key -> 完整 litellm 格式记录）。抓取失败抛异常由调用方捕获。
"""

import re
import sys
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

# 定价总表中按 token 计费的 chat/vlm 小节（小节名小写比较）；其余小节计费口径不同，跳过
CHAT_SECTIONS = ('latest models', 'text models', 'vision models')
MODEL_NAME_RE = re.compile(r'^GLM-[A-Za-z0-9.\-]+$', re.I)

# 模型详情页确认的上下文/模态/能力（价格由脚本实时刷新，这些人工核对过的结构信息固化）
# ctx/maxout 为 token 数；vision 模型带 supports_vision/video_input
MODEL_META = {
    'glm-5.3':       {'ctx': 1000000, 'maxout': 128000, 'vis': False},
    'glm-5.3-flash': {'ctx': 200000,  'maxout': 128000, 'vis': False},
    # FlashX 与 Flash 共用同一个详情页（标题即 "# GLM-5.3-Flash/FlashX"），页内规格卡片
    # 对二者一致（1M 上下文 / 128K 输出 / 原生多模态输入），差异只在推理速度
    'glm-5.3-flashx': {'ctx': 1000000, 'maxout': 128000, 'vis': True, 'speed': True,
                       'note': ('Fast variant of GLM-5.3-Flash, delivering inference speeds of '
                                '~200 tokens/s. ')},
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


def _sections(md):
    """按 '### ' 小节标题把 markdown 切成 [(小节名, 小节正文)]。"""
    parts = re.split(r'^###\s+(.+?)\s*$', md, flags=re.M)
    return [(parts[i], parts[i + 1]) for i in range(1, len(parts) - 1, 2)]


def _parse_pricing(md):
    """解析定价总表的 chat/vlm 小节，返回 {model_lower: {name, input, cache, output}}（USD/1M）。

    列固定为 Model | Input | Cached Input | Cached Input Storage | Output，但小节之间列数不同
    （Image Generation 只有 2 列），因此单元格用 [^|\\n]* 逐行匹配并锚定行尾 '|'，
    避免 2 列表与相邻表格被跨行粘成一条 5 列假记录。"""
    result = {}
    for sec_name, body in _sections(md):
        if sec_name.strip().lower() not in CHAT_SECTIONS:
            continue
        for m in re.finditer(r'^\|\s*([^|\n]+?)\s*\|([^|\n]*)\|([^|\n]*)\|([^|\n]*)\|([^|\n]*)\|\s*$',
                             body, re.M):
            name = m.group(1).strip()
            if not MODEL_NAME_RE.match(name):
                continue  # 表头（Model）与对齐行（:---）
            inp, cache, outp = _num(m.group(2)), _num(m.group(3)), _num(m.group(5))
            if inp is None or outp is None:
                # 行形态不符合 chat 表（价格列不是数值）：显式告警后跳过，不产出半条记录
                print(f'  ! zai: 定价表行形态异常已跳过（{name}：{m.group(0).strip()[:60]}）',
                      file=sys.stderr)
                continue
            result[name.lower()] = {'name': name, 'input': inp, 'cache': cache, 'output': outp}
    return result


def _build_record(price, meta):
    """按人工核对过的 MODEL_META 组装一条记录。
    meta 为空表示「定价页有、MODEL_META 未收录」：只写价格字段，结构字段留空不猜。
    字段写入顺序沿用改造前的顺序（max_* → 价格 → 能力/模态 → notes），避免整份补丁被无谓重排。"""
    rec = {
        'litellm_provider': 'zai',
        'mode': 'chat',
        'source': SRC,
        'supported_regions': ['global'],
        'supported_endpoints': ENDPOINTS,
    }

    def _prices():
        if price.get('input') is not None:
            rec['input_cost_per_token'] = price['input'] / 1e6
        if price.get('output') is not None:
            rec['output_cost_per_token'] = price['output'] / 1e6
        if price.get('cache') is not None:
            rec['cache_read_input_token_cost'] = price['cache'] / 1e6
            rec['input_cost_per_token_cache_hit'] = price['cache'] / 1e6

    if not meta:
        # 新模型：上下文/模态/能力没有官方依据，留空等人工核对详情页后补进 MODEL_META
        _prices()
        rec['notes'] = ('Newly listed on the official pricing page; context length, modalities '
                        'and capabilities are not verified yet.')
        return rec

    if meta.get('ctx'):
        rec['max_input_tokens'] = meta['ctx']
    if meta.get('maxout'):
        rec['max_output_tokens'] = meta['maxout']
        rec['max_tokens'] = meta['maxout']
    _prices()

    if meta.get('ocr'):
        rec['notes'] = ('OCR model. Input: PDF (<=50MB, <=100 pages) or images (JPG/PNG <=10MB). '
                        'Output: Text / Image Links / MD Documents. ')
        rec['supports_vision'] = True
        rec['supported_modalities'] = ['text', 'image']
    elif meta.get('vis'):
        rec.update(CAPS_TEXT)
        rec['supports_vision'] = True
        rec['supports_video_input'] = True
        rec['supported_modalities'] = ['text', 'image', 'video']
        rec['notes'] = ''
    else:
        rec.update(CAPS_TEXT)
        rec['supported_modalities'] = ['text']
        rec['notes'] = ''
    if meta.get('speed'):
        rec['supports_speed'] = True
    rec['supported_output_modalities'] = ['text']
    rec['notes'] = (meta.get('note', '') + rec['notes'] +
                    'Cached Input Storage is free for a limited time. '
                    'Prices per the official pricing page (USD/1M tokens).').strip()
    return rec


def fetch():
    md = _fetch(PRICING_URL)
    table = _parse_pricing(md)
    if not table:
        raise ValueError('未解析到任何模型价格行')

    out = {}
    # 已收录模型：用 MODEL_META 里人工核对过的结构信息组装
    for model, meta in MODEL_META.items():
        price = table.get(model)
        if price is None:
            continue  # 定价页没有的模型跳过（可能已下架）
        out['zai/' + model] = _build_record(price, meta)

    # 定价页有、MODEL_META 未收录：按纯价格先收录并显式告警（旧写法遍历 MODEL_META，
    # 新模型会被静默丢弃——GLM-5.3-FlashX 就是这样漏掉的）
    unlisted = sorted(m for m in table if m not in MODEL_META)
    for model in unlisted:
        out['zai/' + model] = _build_record(table[model], {})
    if unlisted:
        print('  ! zai: 定价页有而 MODEL_META 未收录的模型（已按纯价格收录，'
              '结构信息待人工核对详情页后补进 MODEL_META）：', file=sys.stderr)
        for model in unlisted:
            p = table[model]
            print(f'      {p["name"]}  入${p["input"]} 缓存读${p["cache"]} 出${p["output"]}',
                  file=sys.stderr)
    return out


if __name__ == '__main__':
    import json
    result = fetch()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f'\n共 {len(result)} 条', file=sys.stderr)
