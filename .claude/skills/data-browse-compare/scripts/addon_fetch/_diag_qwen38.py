#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断 qwen3.8-flash 为何未被抓取。用完即删。"""
import json
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, '.')
from fetch_dashscope import SERIES_URL, _post, _is_chat, _fetch_prices, _pick_tiers

TARGET = 'qwen3.8-flash'

# 1. ListModelSeries：逐页查找目标模型，并统计总卡片数
all_cards = {}
found_pages = []
for page_no in range(1, 11):
    params = {'PageNo': page_no, 'PageSize': 50, 'OrderType': 'Featured', 'Language': 'zh-CN'}
    body = ('product=AliyunDeliveryService&action=ListModelSeries&sec_token=&params='
            + urllib.parse.quote(json.dumps(params)))
    d = _post(SERIES_URL, body)
    groups = (d.get('data') or {}).get('Data') or []
    total = (d.get('data') or {}).get('Total') or (d.get('data') or {}).get('TotalCount')
    if page_no == 1:
        print(f'[series] 接口报告总数: {total}')
    if not groups:
        print(f'[series] 第 {page_no} 页起无数据')
        break
    n_items = 0
    for g in groups:
        for it in g.get('Items') or []:
            m = it.get('Model')
            if not m:
                continue
            n_items += 1
            all_cards.setdefault(m, []).append((page_no, it))
            if TARGET in m.lower():
                found_pages.append((page_no, m))
    print(f'[series] 第 {page_no} 页: {n_items} 个卡片')

print(f'\n[series] 累计去重模型数: {len(all_cards)}')
print(f'[series] 含 "{TARGET}" 的模型: {found_pages or "未找到"}')

# 列出所有 qwen3.8* 看命名
q38 = sorted(m for m in all_cards if m.lower().startswith('qwen3.8'))
print(f'[series] 全部 qwen3.8* 模型: {q38}')

# 2. 若找到卡片，检查过滤与价格环节
for m, occ in all_cards.items():
    if m.lower() != TARGET:
        continue
    for page_no, it in occ:
        im = it.get('InferenceMetadata') or {}
        mp = it.get('MultiPrices') or []
        print(f'\n[card] {m} (第{page_no}页)')
        print(f'  RequestModality: {im.get("RequestModality")}')
        print(f'  _is_chat 判定: {_is_chat(m, it)}')
        print(f'  MultiPrices 档数: {len(mp)}')
        for t in mp:
            pr = {p.get("Type"): p.get("Price") for p in t.get("Prices", [])}
            print(f'    range={t.get("RangeName")} timeBand={t.get("TimeBand")} prices={pr}')

# 3. 兜底价格源查找
prices_map = _fetch_prices()
print(f'\n[prices] 兜底源 itemCode 总数: {len(prices_map)}')
hit = [c for c in prices_map if TARGET in c.lower()]
print(f'[prices] 含 "{TARGET}" 的 itemCode: {hit or "未找到"}')
for c in hit:
    for t in prices_map[c]:
        pr = {p.get("type"): p.get("price") for p in t.get("prices", [])}
        print(f'    {c}: range={t.get("rangeName")} timeBand={t.get("timeBand")} prices={pr}')
