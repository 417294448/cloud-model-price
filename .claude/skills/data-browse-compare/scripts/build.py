#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从模板 + JSON 数据完整生成自包含的 index.html。

与早期「往已有 index.html 里塞数据」不同，本脚本持有完整页面模板
（同目录 template.html，数据块位置用 <!--MODEL_DATA--> 占位），
因此数据或模板任一变化都能从零重建出整个页面。

生成流程是原子的，避免中途失败留下损坏的 index.html：
    0. 前置：默认先从 litellm 上游拉取最新 JSON 到临时文件并校验
       （--no-fetch 可跳过、改用本地已有 JSON；显式 --data 也会跳过拉取）
    1. 模板 + 数据拼出 index-new.html
    2. 验证 index-new.html（数据块可被 JSON.parse 解析、条目数与源一致、
       页面 JS 通过 node --check 语法校验）
    3. 验证通过：把现有 index.html 改名为 index-old.html（备份），
       再把 index-new.html 改名为 index.html，
       最后才把拉取的新数据写回本地 source-data/ 下的 JSON
    4. 后置：对比拉取前后的新旧数据，把新增/减少/变更的模型简洁信息
       追加记录到 diff/<YYYY-MM-DD>.txt（同一天多次执行追加，不覆盖）
    任一步失败：保留原 index.html 和本地 JSON 不动，删除临时文件，非零退出。

用法（在项目根目录执行）：
    python .claude/skills/data-browse-compare/scripts/build.py

参数：
    --template   页面模板路径（默认取脚本同目录的 template.html）
    --data       数据 JSON 路径（指定后跳过远程拉取，直接用该文件）
    --out        最终输出文件名（默认 index.html）
    --data-id    内嵌 <script> 标签的 id（须与模板 JS 中的 getElementById 一致，默认 model-data）
    --keep-old   保留上一版备份为 index-old.html（默认开启；--no-keep-old 关闭）
    --no-fetch   跳过拉取远程最新数据，用本地 source-data/ 下已有的 JSON 重建（离线/调试用）

开发提示：template.html 本身数据块是占位注释，浏览器会走 loadData 的
fetch 回退，开发时用 python -m http.server 即可实时预览模板改动。
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime

# 数据源：litellm 上游的模型定价 JSON（raw 形式才是纯数据，blob 是网页页）
DATA_URL = ('https://raw.githubusercontent.com/BerriAI/litellm/'
            'litellm_internal_staging/model_prices_and_context_window.json')


def fail(msg, new_path=None):
    print(f'错误：{msg}', file=sys.stderr)
    if new_path and os.path.exists(new_path):
        os.remove(new_path)
    # 拉取模式下残留的远程数据临时文件也一并清理，避免污染 source-data
    tmp = os.path.join(os.getcwd(), 'source-data', '.fetch-tmp.json')
    if os.path.exists(tmp):
        os.remove(tmp)
    sys.exit(1)


def fetch_latest(tmp_path):
    """下载最新 JSON 到临时文件并做基本校验；返回解析后的 dict。失败则 fail()。
    不直接覆盖本地数据文件——只有后续 index 生成并验证全部通过后，调用方才替换。"""
    print(f'正在拉取最新数据：{DATA_URL}')
    try:
        req = urllib.request.Request(DATA_URL, headers={'User-Agent': 'build.py'})
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
    except Exception as e:
        fail(f'拉取远程数据失败：{e}。可用 --no-fetch 跳过拉取、改用本地已有 JSON 重建')
    try:
        data = json.loads(raw.decode('utf-8'))
    except Exception as e:
        fail(f'下载的内容不是合法 JSON：{e}')
    if not isinstance(data, dict) or len(data) < 100:
        fail(f'下载的数据异常（顶层非对象或条目过少：{len(data) if isinstance(data, dict) else "?"}），已中止')
    with open(tmp_path, 'w', encoding='utf-8') as f:
        f.write(raw.decode('utf-8'))
    print(f'已下载 {len(raw) // 1024} KB，{len(data)} 个顶层条目')
    return data


def _brief(m):
    """从一条模型记录里提取简洁标识：provider / mode / 输入价 / 输出价 / 图像价。"""
    if not isinstance(m, dict):
        return ''
    prov = m.get('litellm_provider', '')
    mode = m.get('mode', '')
    ip = m.get('input_cost_per_token')
    op = m.get('output_cost_per_token')
    img = m.get('output_cost_per_image')
    def per1m(v):
        return f'${v * 1e6:.4g}' if isinstance(v, (int, float)) else '-'
    base = f'[{prov}/{mode}] 入{per1m(ip)} 出{per1m(op)}'
    if isinstance(img, (int, float)):
        base += f' 图${img:.4g}'
    return base


def write_diff(old, new, diff_dir, now):
    """对比新旧两份数据，把新增/减少/变更的模型简洁信息写入 diff/<日期>.txt。
    同一天多次执行追加写入，不覆盖历史。返回 (新增数, 减少数, 变更数)。"""
    old_keys = {k for k in old if k != 'sample_spec'}
    new_keys = {k for k in new if k != 'sample_spec'}
    added = sorted(new_keys - old_keys)
    removed = sorted(old_keys - new_keys)
    # 变更：两边都有、但内容不同的（用排序后的 JSON 串比较，键序无关）
    changed = sorted(
        k for k in (old_keys & new_keys)
        if json.dumps(old[k], sort_keys=True, ensure_ascii=False)
           != json.dumps(new[k], sort_keys=True, ensure_ascii=False)
    )

    os.makedirs(diff_dir, exist_ok=True)
    path = os.path.join(diff_dir, now.strftime('%Y-%m-%d') + '.txt')
    lines = []
    lines.append(f'===== {now.strftime("%Y-%m-%d %H:%M:%S")} 更新 =====')
    lines.append(f'新增 {len(added)} / 减少 {len(removed)} / 变更 {len(changed)}')
    lines.append('')
    if added:
        lines.append(f'--- 新增模型（{len(added)}）---')
        for k in added:
            lines.append(f'+ {k}  {_brief(new[k])}')
        lines.append('')
    if removed:
        lines.append(f'--- 减少模型（{len(removed)}）---')
        for k in removed:
            lines.append(f'- {k}  {_brief(old[k])}')
        lines.append('')
    if changed:
        lines.append(f'--- 发生变更（{len(changed)}）---')
        for k in changed:
            lines.append(f'~ {k}  {_brief(new[k])}')
        lines.append('')
    if not (added or removed or changed):
        lines.append('（本次无变化）')

    with open(path, 'a', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    return len(added), len(removed), len(changed), path


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--template', default=os.path.join(script_dir, '..', 'template.html'),
                        help='页面模板路径（默认脚本同目录的 template.html）')
    parser.add_argument('--data', default=None, help='数据 JSON 路径（默认自动探测 source-data/ 下唯一的 .json）')
    parser.add_argument('--out', default='index.html', help='最终输出文件名（默认 index.html）')
    parser.add_argument('--data-id', default='model-data', help='内嵌 <script> 标签的 id（默认 model-data）')
    parser.add_argument('--keep-old', dest='keep_old', action='store_true', default=True)
    parser.add_argument('--no-keep-old', dest='keep_old', action='store_false')
    parser.add_argument('--no-fetch', dest='fetch', action='store_false', default=True,
                        help='跳过拉取远程最新数据，直接用本地已有 JSON 重建（离线/调试用）')
    args = parser.parse_args()

    root = os.getcwd()
    template_path = os.path.abspath(args.template)
    out_path = os.path.join(root, args.out)
    new_path = os.path.join(root, 'index-new.html')   # 中间产物，验证通过后才转正
    old_path = os.path.join(root, 'index-old.html')
    fetched_tmp = os.path.join(root, 'source-data', '.fetch-tmp.json')  # 远程数据临时文件

    # ---- 读模板与数据 ----
    if not os.path.isfile(template_path):
        fail(f'找不到模板文件：{template_path}')
    with open(template_path, encoding='utf-8') as f:
        template = f.read()
    if '<!--MODEL_DATA-->' not in template:
        fail('模板中未找到 <!--MODEL_DATA--> 占位标记')

    # ---- 数据来源：默认先从远程拉取最新 JSON 到临时文件；--no-fetch 或显式 --data 时用本地 ----
    # local_data_path：最终验证通过后要把远程数据写回的本地文件（仅拉取模式下有意义）
    # old_data：拉取前本地已有的旧数据，用于最后生成 diff（仅拉取模式且本地已有数据时才有）
    local_data_path = None
    old_data = None
    if args.fetch and args.data is None:
        source_dir = os.path.join(root, 'source-data')
        os.makedirs(source_dir, exist_ok=True)
        json_files = [f for f in os.listdir(source_dir)
                      if f.endswith('.json') and not f.startswith('.')]
        # 本地数据文件名以现有为准（默认 model_prices_and_context_window.json）
        local_data_path = os.path.join(source_dir, json_files[0]) if json_files else \
            os.path.join(source_dir, 'model_prices_and_context_window.json')
        # 拉取前先快照本地旧数据，供末尾 diff 对比
        if os.path.isfile(local_data_path):
            try:
                with open(local_data_path, encoding='utf-8') as f:
                    old_data = json.load(f)
            except Exception:
                old_data = None  # 旧数据损坏则不生成 diff，不影响主流程
        data = fetch_latest(fetched_tmp)
    else:
        data_path = args.data
        if data_path is None:
            source_dir = os.path.join(root, 'source-data')
            if not os.path.isdir(source_dir):
                fail('未指定 --data，且找不到 source-data/ 目录')
            json_files = [f for f in os.listdir(source_dir)
                          if f.endswith('.json') and not f.startswith('.')]
            if len(json_files) != 1:
                fail(f'source-data/ 下有 {len(json_files)} 个 json 文件，请用 --data 明确指定一个')
            data_path = os.path.join(source_dir, json_files[0])
        else:
            data_path = os.path.join(root, data_path)
        try:
            with open(data_path, encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            fail(f'数据 JSON 解析失败：{e}')

    # 数据条目数（排除 sample_spec 等非记录项），用于后面验证内嵌结果
    expected_count = sum(1 for k, v in data.items() if isinstance(v, dict) and k != 'sample_spec')

    # ---- 生成 index-new.html ----
    json_text = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    json_text = json_text.replace('</', '<\\/')   # 防止数据里的 "</" 意外闭合 <script>
    embed_block = f'<script type="application/json" id="{args.data_id}">\n{json_text}\n</script>'
    new_html = template.replace('<!--MODEL_DATA-->', embed_block, 1)
    with open(new_path, 'w', encoding='utf-8') as f:
        f.write(new_html)

    # ---- 验证 index-new.html ----
    with open(new_path, encoding='utf-8') as f:
        content = f.read()

    # 1. 数据块可被 JSON.parse 解析
    m = re.search(
        r'<script type="application/json" id="' + re.escape(args.data_id) + r'">(.*?)</script>',
        content, re.DOTALL)
    if not m:
        fail('生成的页面里找不到内嵌数据块', new_path)
    try:
        embedded = json.loads(m.group(1))
    except Exception as e:
        fail(f'内嵌数据块无法被 JSON.parse 解析：{e}', new_path)

    # 2. 条目数与源数据一致
    actual_count = sum(1 for k, v in embedded.items() if isinstance(v, dict) and k != 'sample_spec')
    if actual_count != expected_count:
        fail(f'条目数不一致：源 {expected_count} vs 内嵌 {actual_count}', new_path)

    # 3. 页面主 JS 通过 node --check（无 node 时跳过并提示）
    js_blocks = re.findall(r'<script>(.*?)</script>', content, re.DOTALL)
    if js_blocks:
        main_js = js_blocks[-1]
        js_tmp = new_path + '.js'
        with open(js_tmp, 'w', encoding='utf-8') as f:
            f.write(main_js)
        try:
            subprocess.run(['node', '--check', js_tmp], check=True,
                           capture_output=True, text=True)
        except FileNotFoundError:
            print('提示：未检测到 node，跳过 JS 语法校验')
        except subprocess.CalledProcessError as e:
            os.remove(js_tmp)
            fail(f'页面 JS 语法校验失败：{e.stderr.strip()}', new_path)
        finally:
            if os.path.exists(js_tmp):
                os.remove(js_tmp)

    # ---- 原子替换：index.html → index-old.html，index-new.html → index.html ----
    if args.keep_old and os.path.exists(out_path):
        if os.path.exists(old_path):
            os.remove(old_path)
        os.replace(out_path, old_path)
    if os.path.exists(new_path):
        os.replace(new_path, out_path)

    # ---- index 生成并验证全部通过后，才把远程数据写回本地 JSON ----
    # 此前任何一步失败，本地 source-data 都保持旧版不被污染
    data_updated = False
    if local_data_path and os.path.exists(fetched_tmp):
        os.replace(fetched_tmp, local_data_path)
        data_updated = True

    # ---- 后置：记录本次更新的 diff（仅拉取模式且有旧数据可对比时）----
    diff_info = ''
    if old_data is not None:
        try:
            na, nr, nc, diff_path = write_diff(old_data, data, os.path.join(root, 'diff'), datetime.now())
            diff_info = f'，diff 已记录到 {os.path.relpath(diff_path, root)}（+{na} -{nr} ~{nc}）'
        except Exception as e:
            print(f'提示：生成 diff 失败（不影响页面生成）：{e}', file=sys.stderr)

    size_kb = os.path.getsize(out_path) // 1024
    backup = f'，上一版已备份为 {os.path.basename(old_path)}' if args.keep_old and os.path.exists(old_path) else ''
    synced = '，本地数据已同步为最新' if data_updated else ''
    print(f'已生成 {out_path}（{size_kb} KB，{actual_count} 条记录）{backup}{synced}{diff_info}，双击即可在浏览器中打开使用。')


if __name__ == '__main__':
    main()
