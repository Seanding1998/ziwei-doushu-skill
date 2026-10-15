# 紫微斗数排盘与解盘 Skill（ziwei-doushu）

**一个把「可计算的部分」和「需要判断的部分」分开的紫微斗数工具。**

排盘、四化、自化、三方四正、夹宫、大限流年岁数表 —— 全部由脚本算出，逐字段可核对；
AI 只负责真正无法代码化的那部分：定盘、取舍、层次权衡、取象收敛、语气与边界。
输出是一份带**四道自动闸门**的 HTML 分析报告。

```
脚本算确定的  ·  AI 做不确定的  ·  闸门确保两者一致
```

![示例报告](docs/sample-report.png)

> 上图为示例输出。示例盘是**教学自拟盘（1990-06-07 辰时 男）**，虚构人物，不对应任何真实个案。

---

## 目录

- [这是什么](#这是什么)
- [设计原则](#设计原则)
- [快速开始](#快速开始)
- [排盘：四条命令](#排盘四条命令)
- [出报告：AI 分析 → 脚本渲染 → 四道闸门](#出报告ai-分析--脚本渲染--四道闸门)
- [四道闸门](#四道闸门)
- [项目结构](#项目结构)
- [排盘口径声明（重要）](#排盘口径声明重要)
- [为什么把事实层交给脚本](#为什么把事实层交给脚本)
- [常见问题](#常见问题)
- [免责声明](#免责声明)
- [许可证](#许可证)
- [English](#english)

---

## 这是什么

一个 **Agent Skill**：当用户给出出生日期时间（可能还有地点、性别）要求排盘、解盘、批命、看大限流年时，
AI 按 `SKILL.md` 的八步工作流调用本仓库的脚本，最后产出一份 HTML 报告。

它同时也可以**脱离 AI 单独使用** —— `scripts/ziwei.py` 是一个完整的命令行排盘器。

**能做什么**

- 公历/农历输入、真太阳时校正（给经度）、早晚子时、闰月两派口径
- 十二宫、十四主星、辅佐煞杂曜、庙旺、生年四化、离心/向心自化、三方四正、夹宫、大限/小限/流年岁数表
- 十七项常见格局的**构成条件判定**（成不成格由 AI 裁断，脚本只回答条件是否满足）
- 机器自检：十二宫名唯一、十四主星齐备、禄存与羊陀相邻、行向与限序一致
- 结构化 JSON / 人读树状盘 / Markdown 表格 / 四柱双口径对照
- AI 填结构化分析 → 脚本渲染 HTML（内嵌 CSS、十二宫方阵、格局表、逐宫、大限流年、结论与建议、技术留痕折叠区）
- 三道脚本闸门 + 一道独立审查闸门

---

## 设计原则

| 层 | 谁负责 | 内容 |
|---|---|---|
| **事实层**（可推导） | **脚本** | 真太阳时与时辰判定、宫位/星曜落宫与庙旺、四化落点、自化、三方四正与夹宫关系、星曜配对、格局构成条件、大限十二宫映射、各类计数、免责声明与口径声明 |
| **判断层**（无法代码化） | **AI** | 定盘反推、多解取舍、格局裁断与层次、破格/救格的论证、取象轻重与人生主题收敛、语气与边界 |

**硬约束：AI 不得心算，不得凭记忆说「某星应在某宫」。** 凡进入报告的事实，都来自脚本字段
（`palaceRelations` / `decadalPalaceMaps` / `patternCandidates` / `pairingFacts` 等），
报告里的「宫位关系表」「六吉六煞配对表」「大限十二宫落本命表」「大限四化落点表」都由脚本直接渲染，正文只能引用。

---

## 快速开始

**环境要求**：Python 3.8+ 与 Node.js 18+（Windows / macOS / Linux 均可）。

```bash
git clone https://github.com/<你的用户名>/ziwei-doushu-skill.git
cd ziwei-doushu-skill

# 首次：安装排盘引擎（iztro 2.6.2，装到本目录的 node_modules，不写全局）
python scripts/ziwei.py deps
```

`deps` 只做一次，之后离线可用。受限网络下会依次尝试默认缓存 → 目录内本地缓存 → 环境代理；
也可用环境变量覆盖可执行文件：`ZIWEI_NODE` / `ZIWEI_NPM`。

---

## 排盘：四条命令

```bash
# ① 人读：树状盘面
python scripts/ziwei.py tree  --date 1990-06-07 --tx 4 --gender 男 --year 2026

# ② AI 读：完整 JSON（盘面 + 四化/自化 + 关系 + 大限 + 自检）
python scripts/ziwei.py chart --date 1990-06-07 --tx 4 --gender 男 --year 2026

# ③ 贴文档：Markdown 十二宫表
python scripts/ziwei.py md    --date 1990-06-07 --tx 4 --gender 男

# ④ 四柱双口径对照（农历年 vs 立春，年干不同会换掉全部生年四化）
python scripts/ziwei.py pillar --date 2000-04-22 --time 19:30 --lng 116.41 --gender 女
```

**参数**

| 参数 | 说明 |
|---|---|
| `--date` | 出生日期，支持 `1990-06-07` / `1990/6/7` / `1990年6月7日` |
| `--lunar` | 把 `--date` 当农历 |
| `--tx` | 只知道时辰时直接用序号：`0`=早子(00:00–00:59) `1`=丑 … `11`=亥 `12`=晚子(23:00–23:59) |
| `--time` + `--lng` | 给钟点与出生地经度，脚本做**真太阳时校正**后定时辰（强烈建议） |
| `--gender` | `男` / `女` |
| `--year` | 要看流年的公历年份，可多个：`--year 2026,2027` |
| `--pretty` | JSON 缩进输出 |

> **出生地不是可选项。** 真太阳时校正会实打实挪动时辰——同一钟点在不同经度可能差一到两个时辰，
> 整张盘的命宫、身宫与十四主星会全部错位。缺经度时脚本会给出 `meta.warnings`，报告必须转述。

---

## 出报告：AI 分析 → 脚本渲染 → 四道闸门

```bash
# ① 生成分析工作单（盘面事实已预填，AI 的注意力留给判断）
python scripts/prepare_template.py --chart chart.json --output analysis.json

# ② 【AI 分析】严格顺序：先填九步留痕与关键发现，再写成文，最后逐项裁断格局
#    脚本会检查 interpretation_notes（九步各 ≥40 字）与 findings——跳过分析直接拼字段会被拒

# ③ 渲染 HTML（内嵌 CSS，可直接打开或归档）
python scripts/generate_report.py --chart chart.json --analysis analysis.json --output report.html

# ④ 三道脚本闸门（都要退出码 0）
python scripts/generate_report.py --chart chart.json --analysis analysis.json --validate
python scripts/check_report_language.py report.html
python scripts/audit_report.py --chart chart.json --report report.html

# ⑤ 自测：验证闸门本身没坏（夹具：干净用例 + 近 30 项「应被检出」的错误）
python scripts/test_audit.py --chart examples/sample-chart.json --report examples/sample-report.html
```

AI 只产 `analysis.json`，**HTML 由脚本渲染**：版式稳定、省 token、可校验。

---

## 四道闸门

| 闸门 | 查什么 |
|---|---|
| `--validate` | 字段是否填齐、字数下限、**九步分析留痕是否真做**、可读区语言、格局标成立却没给破格/救格、`verdict`/`advice` 是否空着占位 |
| `check_report_language` | 对**已生成的 HTML** 复查可读区：内部流程词、文件路径、JSON 字段名、Python 字面量、未渲染的 Markdown 星号、写给自己看的自校元指令 |
| `audit_report` | **结论与盘面是否矛盾**：星曜错位、三方四正误判、夹宫误判（同宫不算夹）、宫位关系错（含否定式）、大限宫名错、亮度编造、生年四化标记残缺、格局结论冲突、按类别计数不符 |
| **独立子代理审查** | **只审判断层**：裁断规则是否用对、层次依据、破格/救格论证力、取象是否超出盘面、边界与伦理、建议是否越界 |

当前测试规模：审查器夹具 **33/33**、语言规则自测 **42/42**、报告板块完整性 **11/11**。

规则见 [`references/review-gate.md`](references/review-gate.md)：**事实交给脚本、判断交给审查，一轮为限**；
同类判断问题第二次出现，就说明它其实可代码化 —— 应补成检测器与夹具，而不是靠下一轮审查再抓。

---

## 项目结构

```
SKILL.md                      Agent 的八步工作流（触发后读这个）
README.md                     本文件
references/
  interpretation.md           解盘规则、格局层次、效力层级尺、10 类必须并列的流派分歧、定盘反推
  report-schema.md            analysis JSON 字段契约、字数下限、语言闸门禁用词
  report-template.md          报告语气规范、伦理模板、交稿自检清单
  review-gate.md              独立审查的规则与可直接复制的提示词
  glossary.md                 排盘公式、术语表、iztro API 备忘、依赖信任说明
scripts/
  ziwei.py                    统一 CLI：deps / chart / tree / md / pillar
  engine.js                   iztro 计算引擎（Python 经 stdin/stdout 调用）
  prepare_template.py         由 chart 生成分析工作单
  generate_report.py          AI 产 JSON → 渲染 HTML + --validate 闸门
  check_report_language.py    成品可读区语言验收（--self-test 自带夹具）
  audit_report.py             结构审查器：结论与盘面是否矛盾
  test_audit.py               审查器自测夹具（33 项）
  ziwei_doushu/
    astro.py                  真太阳时、干支纳音、四化、自化、关系、格局条件、岁数表
    chart.py                  编排层 + 跨平台依赖与进程管理
    render.py                 树状盘面与 Markdown 渲染
examples/
  sample-chart.json           示例盘面数据（教学自拟 1990-06-07 辰时 男）
  sample-analysis.json        平铺分析 JSON（配 --chart）
  sample-bundle.json          合并包（配 --input，含全文附录）
  sample-report.html          渲染成品
  workorder-template.json     分析工作单模板
docs/
  sample-report.png           示例报告截图
  ziwei-03-verification.md    排盘口径的核验记录（含公式推导与交叉验证）
```

---

## 排盘口径声明（重要）

紫微斗数各家口径不一，本工具**把口径做成显式声明**，而不是悄悄选一个：

| 口径 | 本工具取值 | 备注 |
|---|---|---|
| 五行局 | 由**命宫宫干纳音**定 | 另一派用生年干推月支纳音，会得到不同局 |
| 年柱分界 | **农历年**（正月初一） | 八字按立春分界，年干可能不同 → 生年四化整套改变，两套体系不可混用 |
| 四化表 | **通行本** | 戊、庚、壬三干的「化科」与中州派不同；本工具支持整表覆盖 |
| 大限行向 | **阳男阴女顺行 / 阴男阳女逆行** | 现代软件通行口径；古籍卷二另有「从命前一宫起」之说 |
| 大限十二宫名 | 与本命**同向排布**，与行向无关 | 曾有人把行向套到宫名表上，导致顺行盘整表错位 |
| 小限 | 自生年支所属三合局的起宫起算，男顺女逆 | 与「小限自命宫起」之说不同 |
| 真太阳时 | 需**经度**，不内置地点库 | 缺经度则按时辰序号直取，并在报告中声明 |
| 亮度（庙旺利陷） | 采用单一来源，报告内声明版本差异 | 不同底本的庙陷表存在出入 |
| 格局 | 脚本只判**构成条件**，成不成格由 AI 裁断并给层次与破格/救格 | — |

排盘口径的推导与交叉验证见 [`docs/ziwei-03-verification.md`](docs/ziwei-03-verification.md)。

---

## 为什么把事实层交给脚本

这不是洁癖，是踩出来的。本项目在一个示例报告上连做了多轮独立盲审，把发现的问题归类后发现：

> **约九成的错误，是「可推导的事实被手写进散文」** —— 星曜错位、大限宫位算术、计数不符、术语误用；
> 只有约一成是真正的判断问题（层次、循环论证、破格概念误用、建议越界）。

于是有了现在的分工：**凡是脚本能算的，AI 不许写**；凡是 AI 必须判断的，脚本只提供事实、不下结论。
连带产出的几处真实修复：

- 有人把「大限行向」套到「限内宫名排布」上，导致所有顺行盘的宫名表整体错位 → 已用引擎自身的
  `horoscope().decadal.palaceNames` 在 4 张盘 × 2 个限上逐格验证并回退；
- `verdict`（总评）与 `advice`（宜忌）曾长期「被校验、被扫描、却从未渲染」→ 已补成「结论与建议」章，
  并列入必填板块（缺则校验失败）；
- 三合满足但不在命宫三方四正的格局曾被标「成立」→ 现在是 `audit_report` 的硬性拦截项。

---

## 常见问题

**Q：一定要用 AI 吗？**
不用。`scripts/ziwei.py` 是完整的命令行排盘器，`prepare_template.py` 生成的分析工作单也可以人工填写，
只要过得了三道脚本闸门，就能渲染出报告。

**Q：`deps` 装了什么？**
只装一个 `iztro@2.6.2`（MIT）到本目录的 `node_modules/`，不写全局、不改你的其它项目。

**Q：为什么报告是 HTML 而不是直接输出文字？**
版式稳定、可校验（语言闸门与结构审查都作用在成品上）、可归档，也便于把「技术留痕」折叠起来给复核者看。

**Q：结果和其他排盘软件不一致？**
先看 [排盘口径声明](#排盘口径声明重要)——年柱分界、四化表、大限起法、闰月与早晚子时都可能造成差异。
`pillar` 命令专门用来对照农历年与立春两套年柱。

**Q：能直接用来算命吗？**
请看下面的免责声明。本项目的定位是**结构化工具与流程演示**，不是断言任何人的命运。

---

## 免责声明

本项目产出的是**命理与自我观察内容，仅供参考**，需与实际条件交叉判读；
**不构成投资、财务、医疗、诊断、法律或其他专业建议**，必要时请咨询合格专业人士。

`SKILL.md` 与 `references/` 对 AI 的产出设有硬边界：不得预测死亡时间、不得下绝症诊断、不得劝阻就医、
不得给出投资或法律结论；健康与财务内容只能作结构性提醒，徵验类断语必须附「徵验率因人而异」声明。

---

## 许可证

本仓库**目前未附加开源许可证**（默认保留所有权利）。如果你想把它用于二次开发或分发，
可以通过 Issue 告诉我，我可以按 MIT 或 Apache-2.0 补上许可证文件。

---

## English

**ziwei-doushu** is an Agent Skill (and a standalone CLI) for Zi Wei Dou Shu (Purple Star Astrology).

The design principle is a strict split of labour: **everything computable is done by scripts**
(chart casting, solar-time correction, four transformations, self-transformations, palace relations,
major/minor limit tables, pattern *conditions*), while **the LLM only does what cannot be coded**
(chart verification, adjudication, weighting, phrasing, ethics).

Output is an HTML report that must pass four gates: field/analysis validation, readable-area language check,
a structural auditor that cross-checks every claim against the chart data, and an independent review pass
that only judges the interpretive layer.

**Requirements**: Python 3.8+, Node.js 18+. Run `python scripts/ziwei.py deps` once, then see
[快速开始](#快速开始) for usage. The sample chart is a synthetic teaching example, not a real person.

Conventions differ between schools; every convention used here is declared explicitly in the report
(see [排盘口径声明](#排盘口径声明重要)). This project is for study and tooling — it does not diagnose,
advise medically or financially, or predict anyone's fate.
