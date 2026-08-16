#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 JSON 数据内嵌进 HTML 页面，使其成为一个可直接双击打开使用的自包含文件。

浏览器出于安全考虑，禁止 file:// 协议下的页面用 fetch() 读取本地文件。
这个脚本把数据 JSON 序列化后塞进一个 <script type="application/json"> 标签，
页面运行时优先读取这个内嵌数据块，没有的话再退回 fetch（仅用于开发时跑
http-server 实时看数据变化，不作为交付方式）。

默认是"原地内嵌"：模板和输出是同一个文件（默认都是 index.html），也就是说
最终交付物就是这一个 index.html，不额外产出 dist/ 目录 —— 这是这个 skill
的默认建议：能用一个文件解决就不要引入两份不同步的产物。

用法（原地更新 index.html，最常用）：
    python build.py

    等价于：
    python build.py --template index.html --out index.html

如果数据源更新了，重新跑一次这个命令即可刷新内嵌数据；脚本会识别已有的
内嵌数据块并整体替换，不会重复插入或残留旧数据。

用法（如果确实需要 template 和输出分开，比如保留一份带 fetch 的开发版）：
    python build.py --template index.html --data source-data/data.json --out dist/index.html

参数：
    --template   页面模板路径（默认 index.html）
    --data       数据 JSON 路径（默认自动探测 source-data/ 下唯一的 .json 文件）
    --out        输出路径（默认与 --template 相同，即原地更新）
    --data-id    内嵌 <script> 标签的 id（须与模板 JS 中的 getElementById 一致，默认 model-data）

模板里的加载逻辑应该长这样（优先读内嵌数据，没有则退回 fetch）：

    async function loadData() {
      let data;
      const embedded = document.getElementById('model-data');
      if (embedded) {
        data = JSON.parse(embedded.textContent);
      } else {
        const resp = await fetch('source-data/data.json');
        data = await resp.json();
      }
      ...
    }
"""

import argparse
import json
import os
import re
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--template', default='index.html', help='页面模板路径（默认 index.html）')
    parser.add_argument('--data', default=None, help='数据 JSON 路径（默认自动探测 source-data/ 下唯一的 .json）')
    parser.add_argument('--out', default=None, help='输出路径（默认与 --template 相同，即原地更新，不产出额外文件）')
    parser.add_argument('--data-id', default='model-data', help='内嵌 <script> 标签的 id（须与模板 JS 中的 getElementById 一致）')
    args = parser.parse_args()

    root = os.getcwd()
    template_path = os.path.join(root, args.template)
    out_path = os.path.join(root, args.out) if args.out else template_path

    data_path = args.data
    if data_path is None:
        source_dir = os.path.join(root, 'source-data')
        if not os.path.isdir(source_dir):
            print('错误：未指定 --data，且找不到 source-data/ 目录', file=sys.stderr)
            sys.exit(1)
        json_files = [f for f in os.listdir(source_dir) if f.endswith('.json')]
        if len(json_files) != 1:
            print(f'错误：source-data/ 下有 {len(json_files)} 个 json 文件，请用 --data 明确指定一个', file=sys.stderr)
            sys.exit(1)
        data_path = os.path.join(source_dir, json_files[0])
    else:
        data_path = os.path.join(root, data_path)

    with open(template_path, encoding='utf-8') as f:
        html = f.read()
    with open(data_path, encoding='utf-8') as f:
        data = json.load(f)

    # 用紧凑分隔符减小体积；转义 "</" 防止数据里的字符串意外闭合 <script> 标签
    json_text = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    json_text = json_text.replace('</', '<\\/')

    embed_block = f'<script type="application/json" id="{args.data_id}">\n{json_text}\n</script>'

    # 幂等处理：如果文件里已经有同 id 的内嵌数据块（比如原地更新、重复运行），
    # 整体替换掉旧块，而不是在 </head> 前再插入一份，避免重复数据把文件越滚越大。
    existing_pattern = re.compile(
        r'<script type="application/json" id="' + re.escape(args.data_id) + r'">.*?</script>',
        re.DOTALL,
    )
    if existing_pattern.search(html):
        # 注意：repl 参数必须传函数，不能直接传字符串 —— re.sub 会把字符串形式的
        # repl 当成正则替换模板解析，其中的反斜杠会被当作转义/反向引用语法处理
        # （比如 "\\." 会被吃掉一个反斜杠变成 "\."），而 embed_block 里全是 JSON
        # 转义出来的反斜杠，一旦被这样"二次解释"就会产生损坏的、无法解析的 JSON。
        # 用 lambda 返回原文本可以让反斜杠保持字面值，不被正则引擎解释。
        html = existing_pattern.sub(lambda _: embed_block, html, count=1)
    else:
        marker = '</head>'
        if marker not in html:
            print('错误：模板中未找到 </head> 标记，且没有已存在的内嵌数据块可替换', file=sys.stderr)
            sys.exit(1)
        html = html.replace(marker, embed_block + '\n' + marker, 1)

    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)

    size_kb = os.path.getsize(out_path) // 1024
    same_file = os.path.abspath(out_path) == os.path.abspath(template_path)
    verb = '已更新' if same_file else '已生成'
    print(f'{verb} {out_path}（{size_kb} KB），双击即可在浏览器中打开使用。')


if __name__ == '__main__':
    main()
