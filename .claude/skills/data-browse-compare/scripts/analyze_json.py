#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
快速分析一份 JSON 数据的结构，为设计浏览+对比页面提供依据。

用法：
    python analyze_json.py <path-to-json>

输出：
    - 顶层条目数、条目的 key 命名规律
    - 所有出现过的字段及其覆盖率（按频次排序）
    - 每个字段的值类型分布（string / number / boolean / array / object / null）
    - 数值字段的 min/max/常见量级（用于判断展示时要不要做单位换算、格式化）
    - 布尔字段中 True 值的覆盖率（用于判断哪些适合做「能力」标签或筛选项）
    - 嵌套字段（array/object 类型）示例，提醒可能需要特殊处理

这不是最终结论，是给你（写页面的人）看的原始素材 —— 拿到这些数字后
再决定：哪些字段做核心对比列、哪些字段只在对比视图展开、哪些字段该在
浏览表隐藏（覆盖率太低、或是内部/元数据字段）。
"""

import json
import sys
from collections import Counter


def type_name(v):
    if v is None:
        return 'null'
    if isinstance(v, bool):
        return 'boolean'
    if isinstance(v, (int, float)):
        return 'number'
    if isinstance(v, str):
        return 'string'
    if isinstance(v, list):
        return 'array'
    if isinstance(v, dict):
        return 'object'
    return type(v).__name__


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)

    path = sys.argv[1]
    with open(path, encoding='utf-8') as f:
        data = json.load(f)

    if not isinstance(data, dict):
        print('提示：顶层不是 dict（key -> record 的映射）。如果是 list，请先确认每条记录的唯一标识字段是什么。')
        if isinstance(data, list) and data:
            print('\n第一条记录示例：')
            print(json.dumps(data[0], indent=2, ensure_ascii=False)[:1500])
        return

    entries = {k: v for k, v in data.items() if isinstance(v, dict)}
    print(f'顶层条目数: {len(data)}（其中 {len(entries)} 条是 dict 结构，可作为可对比的记录）')

    non_dict_keys = [k for k, v in data.items() if not isinstance(v, dict)]
    if non_dict_keys:
        print(f'注意：{len(non_dict_keys)} 个顶层 key 不是 dict（可能是 schema 说明/元数据），示例: {non_dict_keys[:5]}')

    if not entries:
        return

    # 字段覆盖率
    field_counter = Counter()
    field_types = {}
    bool_true_counter = Counter()
    numeric_samples = {}
    array_examples = {}
    object_examples = {}

    for record in entries.values():
        for field, value in record.items():
            field_counter[field] += 1
            field_types.setdefault(field, Counter())[type_name(value)] += 1
            if value is True:
                bool_true_counter[field] += 1
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                numeric_samples.setdefault(field, []).append(value)
            if isinstance(value, list) and field not in array_examples:
                array_examples[field] = value
            if isinstance(value, dict) and field not in object_examples:
                object_examples[field] = value

    total = len(entries)
    print(f'\n=== 字段覆盖率（前 40，按出现频次排序） ===')
    for field, count in field_counter.most_common(40):
        pct = count * 100 // total
        types = field_types[field]
        type_str = '/'.join(f'{t}:{c}' for t, c in types.most_common())
        print(f'  {field}: {count}/{total} ({pct}%)  类型分布={type_str}')

    if bool_true_counter:
        print(f'\n=== 布尔字段中 True 的覆盖率（候选：能力标签 / 筛选项） ===')
        for field, count in bool_true_counter.most_common(30):
            pct = count * 100 // total
            print(f'  {field}: {count} ({pct}%)')

    if numeric_samples:
        print(f'\n=== 数值字段范围（候选：价格/数量等排序列，注意是否要做单位换算） ===')
        for field, values in numeric_samples.items():
            print(f'  {field}: min={min(values):g} max={max(values):g} 样本数={len(values)}')

    if array_examples:
        print(f'\n=== 数组类型字段示例（候选：多选标签 / 需要 join 展示） ===')
        for field, example in list(array_examples.items())[:10]:
            print(f'  {field}: {json.dumps(example, ensure_ascii=False)[:150]}')

    if object_examples:
        print(f'\n=== 嵌套对象字段示例（候选：需要展开成子行，或做特殊格式化，如阶梯定价） ===')
        for field, example in list(object_examples.items())[:10]:
            print(f'  {field}: {json.dumps(example, ensure_ascii=False)[:200]}')

    # key 命名规律（辅助判断是否存在"同一实体多个变体 key"的情况）
    keys = list(entries.keys())
    slash_keys = [k for k in keys if '/' in k]
    if slash_keys:
        print(f'\n提示：{len(slash_keys)}/{len(keys)} 个 key 含 "/"（可能是 provider/变体前缀），示例: {slash_keys[:5]}')
        print('如果同一个基础名称对应多个 key（不同前缀/后缀变体），考虑在页面里是否需要合并展示或保持独立条目。')


if __name__ == '__main__':
    main()
