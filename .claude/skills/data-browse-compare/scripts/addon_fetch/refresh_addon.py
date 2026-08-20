#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""add-on 补丁自动刷新编排器。

每次 build 时依次执行 4 个 provider 的抓取脚本（deepseek / zai / kimi / minimax），
用官网最新内容刷新 add-on-data.json 中对应 provider 的条目。

安全设计：
- **按 provider 增量替换**：某家脚本成功才替换该家在 add-on 里的条目；失败则保留旧条目（兜底）；
- **非该 4 家的条目（如人工补的其他 provider）原样保留**；
- **数值合理性校验**：刷新后某模型价格与上一版快照偏差 >50% 时打醒目警告，提示人工核对；
- **失败不阻塞**：全部失败也返回原 add-on 不变，构建继续。

供 build.py 调用：refresh(addon_path) -> 是否发生变更。
"""

import importlib.util
import json
import os
import sys

# 本目录下的抓取脚本：provider -> 脚本文件名
FETCHERS = {
    'deepseek': 'fetch_deepseek.py',
    'zai': 'fetch_zai.py',
    'moonshot': 'fetch_kimi.py',
    'minimax': 'fetch_minimax.py',
}

# 价格合理性阈值：与旧值偏差超过该比例即告警
PRICE_DRIFT_THRESHOLD = 0.5

# 参与漂移校验的价格字段
PRICE_FIELDS = [
    'input_cost_per_token', 'output_cost_per_token', 'cache_read_input_token_cost',
    'cache_creation_input_token_cost', 'input_cost_per_character',
    'output_cost_per_second', 'output_cost_per_image',
]


def _load_fetcher(script_dir, filename):
    path = os.path.join(script_dir, filename)
    spec = importlib.util.spec_from_file_location(filename[:-3], path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.fetch


def _drift_warnings(old_rec, new_rec, key):
    """对比同 key 的新旧价格字段，偏差超阈值返回警告行列表。"""
    if not isinstance(old_rec, dict):
        return []
    warns = []
    for f in PRICE_FIELDS:
        ov = old_rec.get(f)
        nv = new_rec.get(f)
        if not isinstance(ov, (int, float)) or not isinstance(nv, (int, float)):
            continue
        if ov == 0 and nv == 0:
            continue
        if ov == 0 or nv == 0:
            warns.append(f'{key}.{f}: {ov} -> {nv}（从零/空变为非零或反之）')
            continue
        ratio = abs(nv - ov) / abs(ov)
        if ratio > PRICE_DRIFT_THRESHOLD:
            warns.append(f'{key}.{f}: {ov:.4g} -> {nv:.4g}（偏差 {ratio * 100:.0f}%）')
    return warns


def refresh(addon_path, quiet=False):
    """刷新 add-on-data.json。返回是否有变更。失败兜底保留旧值，不抛异常。"""
    log = (lambda *a, **kw: None) if quiet else print
    script_dir = os.path.dirname(os.path.abspath(__file__))

    # 读现有 add-on
    old = {}
    if os.path.isfile(addon_path):
        try:
            with open(addon_path, encoding='utf-8') as f:
                old = json.load(f)
        except Exception as e:
            print(f'  ! add-on 读取失败，跳过自动刷新：{e}', file=sys.stderr)
            return False

    new = dict(old)  # 以旧为基础，成功刷新的 provider 再替换其条目
    any_change = False
    all_warns = []

    for provider, filename in FETCHERS.items():
        try:
            fetch = _load_fetcher(script_dir, filename)
            records = fetch()
            if not records:
                raise ValueError('抓取结果为空')
            # 价格漂移校验：新条目 vs 旧条目同 key
            for k, rec in records.items():
                all_warns.extend(_drift_warnings(old.get(k), rec, k))
            # 覆盖写入新抓取条目（同 key 替换；该 provider 旧有但这次没抓到的条目保守保留，
            # 可能是下架也可能脚本漏抓——保留比误删安全，由补丁 diff 日志呈现变化供人工判断）
            for k, rec in records.items():
                new[k] = rec
            log(f'  ✓ {provider}: 自动刷新 {len(records)} 条')
            any_change = True
        except Exception as e:
            log(f'  ! {provider}: 抓取失败（{e}），沿用旧补丁', file=sys.stderr)

    # 漂移告警
    if all_warns:
        print('  ⚠️  补丁价格漂移告警（与上一版偏差 >50%，请人工核对）:', file=sys.stderr)
        for w in all_warns:
            print(f'      {w}', file=sys.stderr)

    if any_change:
        with open(addon_path, 'w', encoding='utf-8') as f:
            json.dump(new, f, ensure_ascii=False, indent=4)
            f.write('\n')
    return any_change


if __name__ == '__main__':
    path = sys.argv[1] if len(sys.argv) > 1 else 'add-on-data.json'
    changed = refresh(path)
    print(f'刷新完成，{"有" if changed else "无"}变更')
