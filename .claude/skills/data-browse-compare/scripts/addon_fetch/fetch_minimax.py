#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 MiniMax 官方文档站（platform.minimax.io）抓取模型定价，生成 add-on 补丁条目。

数据源：https://platform.minimax.io/docs/guides/pricing-paygo （Next.js SSR HTML，价格直接渲染在表格里）。
覆盖 LLM（M 系列，含 M3 阶梯/Priority 档）与多模态（speech/image/video）。
价格为美元 USD。

输出：dict（key -> 完整 litellm 格式记录）。抓取失败抛异常由调用方捕获。
"""

import re
import urllib.request

URL = 'https://platform.minimax.io/docs/guides/pricing-paygo'
SRC = 'https://platform.minimax.io/docs/guides/pricing-paygo'

CAPS_TEXT = {
    'supports_function_calling': True,
    'supports_system_messages': True,
    'supports_prompt_caching': True,
    'supports_reasoning': True,
}
ENDPOINTS = ['/v1/chat/completions']


def _fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'build.py'})
    with urllib.request.urlopen(req, timeout=25) as resp:
        return resp.read().decode('utf-8')


def _strip(html):
    t = re.sub(r'<script[\s\S]*?</script>', ' ', html)
    t = re.sub(r'<style[\s\S]*?</style>', ' ', t)
    t = re.sub(r'<[^>]+>', '\n', t)
    t = re.sub(r'&gt;', '>', t)
    t = re.sub(r'&amp;', '&', t)
    return [l.strip() for l in re.sub(r'\n{2,}', '\n', t).split('\n') if l.strip()]


def _usd(s):
    """'$0.30 / M tokens' / '$0.3 / M tokens' -> 0.30；'$60/M characters' -> 60"""
    m = re.search(r'\$\s*([0-9]+(?:\.[0-9]+)?)', s)
    return float(m.group(1)) if m else None


def fetch():
    lines = _strip(_fetch(URL))
    out = {}

    # ---- LLM：定位 'Model / Input / Output / Prompt caching Read [| Write]' 表 ----
    # 行形如：Model 名（可能带 ≤512k/>512k 阶梯行、Priority 1.5x 说明）
    # 单档模型（M2.x）：Model 名后紧跟 4 个价格（入/出/缓存读/缓存写）
    i = 0
    while i < len(lines):
        l = lines[i]
        # 单档 M2.x 模型行（不含阶梯/优先级标记）
        if re.match(r'^MiniMax-M2\.\d(-highspeed)?$', l):
            prices = []
            j = i + 1
            while j < len(lines) and len(prices) < 4:
                v = _usd(lines[j])
                if v is not None:
                    prices.append(v)
                j += 1
            if len(prices) >= 4:
                _add_m2x(out, l, prices)
            i = j
            continue
        i += 1

    # ---- M3：阶梯 + Priority。已在人工核对时确认结构，这里补 Priority 档与 notes ----
    # M3 标准档与阶梯上游已有，补丁只补 Priority（×1.5）
    out['minimax/MiniMax-M3'] = _m3_record(lines)

    # ---- 多模态 ----
    _add_multimodal(out, lines)
    return out


def _add_m2x(out, name, prices):
    """单档 M2.x 模型：prices = [入, 出, 缓存读, 缓存写]"""
    ip, op, cr, cw = prices[0], prices[1], prices[2], prices[3]
    rec = {
        'litellm_provider': 'minimax',
        'mode': 'chat',
        'source': SRC,
        'input_cost_per_token': ip / 1e6,
        'output_cost_per_token': op / 1e6,
        'cache_read_input_token_cost': cr / 1e6,
        'cache_creation_input_token_cost': cw / 1e6,
        'supported_modalities': ['text'],
        'supported_output_modalities': ['text'],
        'supported_endpoints': ENDPOINTS,
        'supported_regions': ['global'],
        'notes': 'Prices per the official pricing page (USD/1M tokens).',
    }
    rec.update(CAPS_TEXT)
    if 'highspeed' in name:
        rec['supports_speed'] = True
    out['minimax/' + name] = rec


def _m3_record(lines):
    """M3：上游已有标准档+阶梯，补丁补 Priority 档（标准 ×1.5）。
    标准档 ≤512k：入0.30 出1.20；>512k：入0.60 出2.40。
    页面每行是"划线原价 + 实付价（带 / M tokens 后缀）"两列，取实付价。"""
    ip = op = None
    for i, l in enumerate(lines):
        # 第一个 M3 + ≤512k 行是 Standard 档
        if l == 'MiniMax-M3' and i + 1 < len(lines) and '≤' in lines[i + 1]:
            vals = []
            for x in lines[i + 1:i + 12]:
                # 实付价特征：带 "/ M tokens" 后缀；划线原价是裸数字
                if '/ M tokens' in x:
                    v = _usd(x)
                    if v is not None:
                        vals.append(v)
                if len(vals) >= 2:
                    break
            if len(vals) >= 2:
                ip, op = vals[0], vals[1]
            break
    ip = ip if ip is not None else 0.30
    op = op if op is not None else 1.20
    return {
        'litellm_provider': 'minimax',
        'mode': 'chat',
        'source': SRC,
        'input_cost_per_token': ip / 1e6,
        'output_cost_per_token': op / 1e6,
        'cache_read_input_token_cost': 0.06 / 1e6,
        'input_cost_per_token_above_512k_tokens': (ip * 2) / 1e6,
        'output_cost_per_token_above_512k_tokens': (op * 2) / 1e6,
        'cache_read_input_token_cost_above_512k_tokens': 0.12 / 1e6,
        'input_cost_per_token_priority': (ip * 1.5) / 1e6,
        'output_cost_per_token_priority': (op * 1.5) / 1e6,
        'supported_modalities': ['text'],
        'supported_output_modalities': ['text'],
        'supported_endpoints': ENDPOINTS,
        'supported_regions': ['global'],
        'notes': ('Multimodal coding model with 1M context. Tiered pricing: >512K input tokens billed '
                  'at 2x standard (cache read $0.12/1M). Priority tier (service_tier=priority) billed at '
                  '1.5x standard for faster response and improved reliability. '
                  'Prices per the official pricing page (USD/1M tokens).'),
        **CAPS_TEXT,
    }


def _add_multimodal(out, lines):
    # speech 系列（按字符）：页面常以合并行呈现（如 "speech-2.6-turbo / speech-02-turbo"），
    # 要把同一行内的多个模型名拆成独立条目。匹配所有 speech-* 行，逐个取后续价格。
    speech_patterns = [
        # (行内模型名子串列表, 该行价格)
        # speech-2.8 单独成行
        ('speech-2.8', None),
        # 合并行：speech-2.6-turbo / speech-02-turbo 共享价格
        ('speech-2.6-turbo / speech-02-turbo', ('speech-2.6-turbo', 'speech-02-turbo')),
        ('speech-2.6-hd / speech-02-hd', ('speech-2.6-hd', 'speech-02-hd')),
    ]
    for sub, names in speech_patterns:
        for i, l in enumerate(lines):
            if l != sub:
                continue
            # 找到该行后第一个价格
            v = None
            for x in lines[i + 1:i + 4]:
                cand = _usd(x)
                if cand is not None:
                    v = cand
                    break
            if v is None:
                continue
            model_names = names if names else (sub,)
            for name in model_names:
                out['minimax/' + name] = {
                    'litellm_provider': 'minimax', 'mode': 'audio_speech', 'source': SRC,
                    'input_cost_per_character': v / 1e6,
                    'notes': f'Text-to-speech. Priced at ${v:g} per 1M characters per the official pricing page.',
                    'supported_regions': ['global'],
                }
            break
    # image-01（按张）
    for i, l in enumerate(lines):
        if l == 'image-01':
            for x in lines[i + 1:i + 4]:
                if 'per image' in x:
                    v = _usd(x)
                    if v is not None:
                        out['minimax/image-01'] = {
                            'litellm_provider': 'minimax', 'mode': 'image_generation', 'source': SRC,
                            'output_cost_per_image': v,
                            'notes': f'Image generation. Priced at ${v:g} per image per the official pricing page.',
                            'supported_modalities': ['text'],
                            'supported_output_modalities': ['image'],
                            'supported_regions': ['global'],
                        }
                    break
    # MiniMax-H3 视频（768P 价存字段，2K 进 notes）
    for i, l in enumerate(lines):
        if l == 'MiniMax-H3' and i + 3 < len(lines) and lines[i + 1] == '768P':
            for x in lines[i + 2:i + 6]:
                v = _usd(x)
                if v is not None:
                    out['minimax/MiniMax-H3'] = {
                        'litellm_provider': 'minimax', 'mode': 'video_generation', 'source': SRC,
                        'output_cost_per_second': v,
                        'notes': ('Video generation. Billed per second: 768P $0.08/s, 2K $0.13/s '
                                  '(field stores 768P rate). Input materials: audio free; first 5 images free, '
                                  '$0.04 per additional image; input video billed by duration at output-resolution '
                                  'rate. 4-15s duration, 24fps. Prices per the official pricing page.'),
                        'supported_modalities': ['text', 'image', 'video'],
                        'supported_output_modalities': ['video'],
                        'supported_regions': ['global'],
                    }
                    break
            break

    # MiniMax-H3-Max 视频（480P $0.05/s，768P $0.08/s；字段存 768P 价）
    for i, l in enumerate(lines):
        if l == 'MiniMax-H3-Max' and i + 1 < len(lines) and lines[i + 1] == '768P':
            for x in lines[i + 2:i + 6]:
                v = _usd(x)
                if v is not None:
                    out['minimax/MiniMax-H3-Max'] = {
                        'litellm_provider': 'minimax', 'mode': 'video_generation', 'source': SRC,
                        'output_cost_per_second': v,
                        'notes': ('Video generation. Billed per second: 480P $0.05/s, 768P $0.08/s '
                                  '(field stores 768P rate). Supports T2V and I2V only. Output video is billed; '
                                  'input images are not billed. Prices per the official pricing page.'),
                        'supported_modalities': ['text', 'image', 'video'],
                        'supported_output_modalities': ['video'],
                        'supported_regions': ['global'],
                    }
                    break
            break

    # MiniMax-H3-Regeneration 视频再生（$0.05/s，768P→2K）
    for i, l in enumerate(lines):
        if l == 'MiniMax-H3-Regeneration':
            for x in lines[i + 1:i + 5]:
                v = _usd(x)
                if v is not None:
                    out['minimax/MiniMax-H3-Regeneration'] = {
                        'litellm_provider': 'minimax', 'mode': 'video_generation', 'source': SRC,
                        'output_cost_per_second': v,
                        'notes': ('Video regeneration. Billed per second of regenerated output at $0.05/s (768P→2K). '
                                  'Input materials from the original 768P task are billed again. '
                                  'Prices per the official pricing page.'),
                        'supported_modalities': ['text', 'image', 'video'],
                        'supported_output_modalities': ['video'],
                        'supported_regions': ['global'],
                    }
                    break
            break

    # MiniMax-H3-Context-IR（chat：$0.90 入 / $3.60 出 per M tokens）
    for i, l in enumerate(lines):
        if l == 'MiniMax-H3-Context-IR':
            vals = []
            for x in lines[i + 1:i + 8]:
                v = _usd(x)
                if v is not None:
                    vals.append(v)
            if len(vals) >= 2:
                out['minimax/MiniMax-H3-Context-IR'] = {
                    'litellm_provider': 'minimax', 'mode': 'chat', 'source': SRC,
                    'input_cost_per_token': vals[0] / 1e6,
                    'output_cost_per_token': vals[1] / 1e6,
                    'supported_modalities': ['text', 'image'],
                    'supported_output_modalities': ['text'],
                    'supported_endpoints': ['/v1/chat/completions'],
                    'supported_regions': ['global'],
                    'notes': ('Context understanding & retrieval model. Priced at $0.90/M input and '
                              '$3.60/M output tokens per the official pricing page.'),
                    **CAPS_TEXT,
                }
            break


if __name__ == '__main__':
    import json
    import sys
    result = fetch()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f'\n共 {len(result)} 条', file=sys.stderr)
