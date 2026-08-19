# Cloud Model Pricing & Compare

[English](#english) | [中文](#中文)

---

<a id="english"></a>

A zero-dependency, single-file static page for browsing and comparing **3,000+ cloud AI models** across all major providers — pricing (standard / Priority / Batch / Flex tiers), context windows, capabilities, modalities, and API endpoints, normalized to USD per 1M tokens.

**Open `index.html` in any browser. That's it** — no build step, no server, no installation. All data is embedded in the file itself.

## Highlights

- **Browse everything** — full-text search across model keys and providers; filter by vendor, provider, capability type, capabilities (vision, function calling, reasoning, web search, computer use), and input/output modalities; click any column header to sort; paginated at 100 rows.
- **Vendor quick-filters** — one-click chips for 18 major vendors (OpenAI, Anthropic, Google, Amazon, Microsoft, xAI, Alibaba Cloud, DeepSeek, Z.ai, Moonshot AI, Mistral AI, MiniMax, Meta, Oracle, Volcengine, Cohere, Fireworks AI, OpenRouter), each with provider logo and model count.
- **Side-by-side comparison** — select up to 4 models to compare pricing tiers, context limits, supported modalities, a 30+ item capability matrix, and API endpoints; the single best value per numeric row is highlighted automatically (ties are never highlighted).
- **Full pricing-tier coverage** — beyond standard input/output prices: cache read/write, Priority, Batch, and Flex tiers, image generation, audio tokens, and per-second/per-query billing are all surfaced in the detail drawer when present.
- **Bilingual UI** — English / 中文, one-click toggle, persisted across sessions.
- **Shareable comparisons** — the current selection is encoded into the URL hash (`#compare=key1,key2,…`); send the link and the recipient lands on the same comparison.
- **Session persistence** — tab, selections, and language restore from `localStorage` on refresh.

## Rebuilding

The page is generated atomically from a template plus the JSON data:

```bash
python .claude/skills/data-browse-compare/scripts/build.py
```

By default this fetches the latest upstream JSON, rebuilds `index.html`, validates the embedded data (record count + JS syntax via `node --check`), backs up the previous version to `index-old.html`, and records a diff. Use `--no-fetch` to rebuild from local data only. Provider logos are cached under `source-data/provider-logos/` so rebuilds work fully offline.

## Repository layout

```
index.html          # the deliverable — self-contained page with embedded data
provider.csv        # provider → company / logo mapping
source-data/        # upstream JSON snapshot + cached provider logos
diff/               # per-day model add/remove/change logs
.claude/skills/data-browse-compare/
  ├── template.html # page template (data + logos injected at build time)
  └── scripts/      # build.py (fetch → build → validate → swap → diff)
```

---

<a id="中文"></a>

# 云模型定价与能力对比

一个零依赖的单文件静态页面，用于浏览和对比**全部主流云厂商的 3,000+ AI 模型**——价格（标准 / Priority / Batch / Flex 多档）、上下文窗口、能力矩阵、输入输出模态与 API 端点，价格统一换算为美元 / 百万 tokens。

**双击 `index.html` 即可使用**——无需安装依赖、无需启动服务，全部数据已内嵌在文件中。

## 功能亮点

- **全模型浏览**：模型名 / provider 模糊搜索；按厂商、provider、类型、能力（视觉、函数调用、推理、联网搜索、计算机操作）、输入/输出模态筛选；点击列头排序；每页 100 条分页。
- **厂商快捷筛选**：18 家重点厂商一键圈选（OpenAI、Anthropic、Google、Amazon、Microsoft、xAI、阿里云、DeepSeek、智谱 Z.ai、月之暗面、Mistral、MiniMax、Meta、Oracle、火山引擎、Cohere、Fireworks、OpenRouter），带品牌 logo 与模型数。
- **多模型并排对比**：最多勾选 4 个模型，对比价格档位、上下文、支持模态、30+ 项能力矩阵与 API 端点；数值维度自动高亮唯一最优值（并列不高亮）。
- **完整价格档覆盖**：除标准输入/输出价外，缓存读/写、Priority、Batch、Flex 档位价、图像生成、音频 token、按秒/按次计费等在详情抽屉中按实展示。
- **中英双语**：一键切换，选择持久化。
- **可分享对比**：已选模型编码进 URL hash（`#compare=key1,key2,…`），对方打开链接即见同一对比。
- **现场恢复**：当前 tab、已选模型、语言刷新后从 `localStorage` 完整还原。

## 重新构建

页面由模板 + 数据原子化生成：

```bash
python .claude/skills/data-browse-compare/scripts/build.py
```

默认流程：拉取上游最新 JSON → 重建 `index.html` → 校验（条目数 + `node --check` JS 语法）→ 旧版备份为 `index-old.html` → 记录 diff。离线时用 `--no-fetch` 跳过拉取。provider logo 已缓存到 `source-data/provider-logos/`，重建可完全离线进行。

## 目录结构

```
index.html          # 交付物——自包含页面（数据已内嵌）
provider.csv        # provider → 公司 / logo 映射
source-data/        # 上游 JSON 快照 + provider logo 缓存
diff/               # 按天的模型新增/减少/变更日志
.claude/skills/data-browse-compare/
  ├── template.html # 页面模板（构建期注入数据与 logo）
  └── scripts/      # build.py（拉取 → 构建 → 校验 → 替换 → diff）
```
