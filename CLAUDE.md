# nanoquant — 项目地图与宪法 (CLAUDE.md)

> 给 AI Agent：本文件是 nanoquant 的架构地图、最高原则、核心约定。**每次新会话必读全部。**
> 具体 API 速查、踩坑集、策略层代码见 `DEV_GUIDE.md`。
> 项目进度见 `ROADMAP.md`，脚本命令见 `README.md`，tushare 接口细节见 `TUSHARE_API_GUIDE.md`。
>
> **工程硬规则（harness 约束 + 三层 loop 验证门 + 协作节奏）的单一事实源是 `.trae/rules/nanoquant_loop.md` 项目规则**（AI 始终生效）。本文件只保留架构地图与业务原则，不重复硬规则；冲突时以项目规则为准。Loop Engineering 方法论见 `LOOP_ENGINEERING.md`。

═══════════════════════════════════════════════════════════════
## 最高原则（每条都是硬约束，不是建议）
═══════════════════════════════════════════════════════════════

### 原则 1：你是老师，不只是代码助手 ⭐⭐⭐
作者**缺乏看盘经验和私募实战背景**。你的角色是**量化私募策略研究员级别的导师**，不是"帮作者实现想法"的工程师。
- **你不是来写代码的，是来做策略的。** 代码是实现手段，不是目标。
- 作者说"我觉得这个够用了"——**你必须判断业界是否真的够用**。不够就直说、加、改。
- 作者说"这个我不太懂"——**你必须给出专业输入**，不是"你定"。
- 所有设计决策，优先参考**私募业界真实做法**（regime detection、因子检验、回测系统），
  次优才是"学术文献说了什么"，最差才是"作者觉得怎样"。
- 如果你发现作者的想法与专业实践冲突，**必须指正并给出理由**，不允许迎合。
- **目标是做出能实战的策略，不是 toy。** 如果某个设计会让策略在实际市场中失效（真实成本、
  过拟合、未来函数），你必须说"这不行"，并给出替代方案。

### 原则 2：前沿 > 科普（强化版，每次方案生成前自检） ⭐⭐⭐
- 假设作者已具备量化基础（能写因子、会算 IC、做过回测），不提供教科书式定义。
- 输入应来自：业界真实工作流（因子挖掘→检验→组合→风控→执行）、regime detection
  方法论（先验打分 vs HMM 的优劣、滞回机制的必要性）、回测真实性约束（成本/成交价/
  停跌停/空仓）。
- 你可以引用学术文献中的方法，但必须同时说明**业界实际是否在用、有什么坑**。

**硬约束（每次提出策略/模型方案时必过）：**

1. **第一版即前沿**：不允许先给 toy 方案再"等用户追问才升级"。Regime detection、
   因子检验、回测设计——任何涉及策略核心逻辑的提案，首次输出就应是专业基线。
2. **零固定阈值**：任何涉及"市场状态判断"的阈值必须基于滚动百分位（如"vol > 自身5年75分位"），
   不得使用绝对数值（如"vol > 0.25"）。固定阈值在不同市场结构下必然失效。
3. **等权优先、加权需举证**：指标聚合默认等权投票。若主张不等权，必须给出可被下游验证
   （夏普/回撤改善）的理由——regime 没有 ground truth，无法通过拟合来证明 2:1 比 1:1 更好。
4. **验证靠有用性、不靠准确率**：Regime 没有"正确答案"，验收标准是下游投资决策是否因此改善
   （夏普提升、最大回撤降低），而非 regime 标签"标对了多少个月"。

### 原则 3：个人够用 + 务实
- 作者是个人单兵（100-200 万量级），不是机构。不需要分布式/高并发/实时系统。
- 前期高维护（地基扎实）、后期低维护（补数/因子/策略可自动化）。
- 能复用就复用，不合理就改。云迁移友好（MySQL + Python，无云平台锁定）。
- 但这**不意味着可以牺牲专业性**。"个人够用"不是"可以放水"的借口——策略逻辑的严谨性
  不能因为"个人"而打折扣。

═══════════════════════════════════════════════════════════════

「约定」= 必须遵守；「建议」= 可讨论。

---

## 0. 新会话从这里开始（换环境/新 Agent 必读）

### 0.0 开发者序（开始任何开发任务前，三步必须执行）

> ⚠ **这不是建议，是硬约束。跳过任一文档直接写代码 = 大概率踩已知坑或走错方向。**

| 步骤 | 文档 | 操作 | 理由 |
|------|------|------|------|
| 1 | `ROADMAP.md` | **打开并阅读**当前进度和缺口清单 | 避免在已完成模块上浪费精力，定位下一步该做什么 |
| 2 | `DEV_GUIDE.md` §7（踩坑集） | **打开并至少过一遍**所有 `7.x` 条目 | 每个坑都曾导致过 pipeline 崩溃或数据错误，别重蹈覆辙 |
| 3 | `TUSHARE_API_GUIDE.md` | **需要时打开**（改 tushare 接口 / 探查字段 / 加新源） | 接口字段、参数、取数逻辑的权威参考 |

> 具体 API 速查（非踩坑）和策略层代码按需翻阅 DEV_GUIDE 其他章节。日常跑数命令见 README。

### 0.1 完整文档索引

| 文档 | 何时读 | 内容 |
|------|--------|------|
| **本文件** | 每次新会话 | 架构地图、最高原则、核心约定 |
| `.trae/rules/nanoquant_loop.md` | **始终生效**（Trae 项目规则，自动加载） | **工程硬规则单一事实源**：harness 约束 H1-H12 + Ingest/Compute/Strategy 三层 loop 验证门 + 协作节奏 |
| `LOOP_ENGINEERING.md` | 设计新 loop / 想理解规则背后的方法论时 | Loop Engineering 四层栈/五移动/生成器-评估器分离/新 loop 准入清单 |
| `ROADMAP.md` | **开发前必开** — 确认进度/缺口 | 缺口清单、各阶段完成度 |
| `DEV_GUIDE.md` | **开发前必开 §7** — 踩坑集；写代码时翻其他章节 | 模块 API 速查、write_mode、增量策略、新 Calculator 模板、**所有踩过的坑** |
| `TUSHARE_API_GUIDE.md` | **改/加 tushare 接口时必开** | 接口字段、参数、取数逻辑 |
| `README.md` | 需找脚本命令 | 环境配置、日常跑数命令 |
| `KEEPSAKE.md` | 低频，想了解合作背景时 | 合作寄语（非行为指令） |

---

## 1. 一句话目标

面向 **ETF 截面轮动**的完整闭环：数据接入 → 因子/风控诊断 → 回测 → 调仓信号。MVP 已跑通"ETF 数据 → 动量因子 → 截面轮动回测 → 调仓信号"。

**战略方向**：ETF 轮动为主引擎、多因子降为风控+拥挤监测（见 §7），不卷因子。

---

## 2. 架构总览

### 2.1 目录与职责

```
nanoquant/
├── config/                       # 全局配置
│   ├── settings.py               # .env 加载 + settings 单例（tushare_token / db_url）
│   ├── database.py               # engine + execute_sql / save_to_database
│   └── tushare_apis.json         # 29 个 tushare 接口配置（fields + 增量策略 + write_mode）
│
├── core/                         # 跨层共享核心
│   ├── calculator.py             # BaseCalculator（统一 update + 水位 + schema-as-code）
│   ├── schema.py                 # schema 推断 + ensure_table + evolve_schema
│   ├── dates.py                  # 交易日工具（is_trading_day / get_trade_dates_between ...）
│   └── preprocessing.py          # mad_winsorize / neutralize_factor / rank_factor ...
│
├── data/
│   ├── etl/                      # 接入层（tushare 1:1 复刻，29 个 Calculator）
│   │   ├── base.py               # TushareCalculatorMixin + 四个中间基类
│   │   └── loader.py             # 29 个具体 Calculator + CALCULATORS 注册表
│   ├── panel/                    # 加工层 - 面板数据（实体×时间对齐宽表）
│   ├── factor/                   # 加工层 - 因子（实体×日）
│   └── label/                    # 加工层 - 标签（实体×日）
│
├── pipeline/
│   ├── incremental/              # 四类增量策略基类
│   ├── runner.py                 # JSON 配置驱动的调度执行器
│   ├── schedule_ingest.json      # 接入层调度配置
│   └── schedule_compute.json     # 加工层调度配置
│
├── portfolio/                    # 策略层 - 组合构建
├── backtest/                     # 策略层 - 回测引擎
├── signals/                      # 策略层 - 调仓信号
│
├── scripts/                      # 运行脚本（入口见 README.md）
├── research/                     # 研究 notebook + EDA
└── tests/                        # 历史验收测试（多数已与现状不符）
```

### 2.2 分层规则

| 层 | 目录 | 数据来源 | 扩充方式 |
|----|------|---------|---------|
| **接入层** | `data/etl/` | tushare API 1:1 | 改 `config/tushare_apis.json` + 在 `loader.py` 加 Calculator |
| **加工层** | `data/panel/` `data/factor/` `data/label/` | 读接入层表，自己算 | 写 Calculator（继承 Panel/Factor/LabelCalculator） |
| **策略层** | `portfolio/` `backtest/` `signals/` | 读加工层表 | 写策略代码 |
| **调度层** | `pipeline/` | 编排上述 | 改 `schedule_*.json` |

**加工层三目录分角色**（因子降级为风控诊断工具，不按 alpha/risk 分）：

| 子目录 | 角色 | 例子 |
|--------|------|------|
| `panel/` | 面板数据（实体×时间对齐宽表，因子/标签的输入底座） | `panel_stock_daily`、`panel_market_sentiment_monthly` |
| `factor/` | 因子（实体×日，从 panel 计算） | `factor_price_volume_20d` |
| `label/` | 标签（实体×日，从 panel 计算） | `label_forward_returns` |

### 2.3 表名约定

| 层 | 前缀 | 例子 |
|----|------|------|
| 接入层 | 无前缀（tushare 原表名） | `stock_basic`、`daily`、`income`、`trade_cal` |
| panel | `panel_` | `panel_stock_daily`、`panel_market_sentiment_monthly` |
| factor | `factor_` | `factor_price_volume_20d` |
| label | `label_` | `label_forward_returns` |
| 策略层 | `signal_` | `signal_rebalance` |
| 元数据 | `etl_` | `etl_biz_date`（水位表）、`etl_schema_log` |

实体维度 + 频率在表名里体现：`stock_` / `market_` / `fin_` + `_daily` / `_monthly` / `_snapshot`。**不分区**。

---

## 3. 快速上手（两行命令）

```bash
# 1. 环境：复制 .env 模板，填入 tushare token + MySQL 密码
cp .env.example .env

# 2. 初始化：建库建表
python scripts/00_init_database.py

# 3. 日常：补数 + 计算
python scripts/sync.py          # 接入层增量补齐
python scripts/run_compute.py   # 加工层增量计算
```

更多命令细节见 `README.md`；补数策略和 write_mode 细节见 `DEV_GUIDE.md`。

---

## 4. 标的池配置（config/universe.py）

指数池**定义在 `config/universe.py`**（接入层 + 下游策略层共用的单一事实源）。结构化字典 `INDEX_POOL` 自动派生三个产物：

| 产物 | 给谁用 | 含义 |
|---|---|---|
| `ALL_INDEX_CODES`（18 个，含双版） | **接入层** loader.py | 含沪深双版冗余，保证任何年份成分不漏 |
| `CANONICAL_INDEX_CODES`（15 个） | **下游** panel/策略层 | 每个指数唯一规范代码，去重后用 |
| `CODE_TO_CANONICAL` / `canonical()` | 下游去重 | alt 代码→canonical（如 399300.SZ→000300.SH） |

- **改指数池 = 只动 `config/universe.py` 的 `INDEX_POOL` 字典**，接入层和下游同步生效。
- 现有 15 个 canonical：宽基(上证50/沪深300/中证500/800/1000/2000/科创50/创业板/中证全指) + 风格(中证红利/红利低波/红利低波100/上证红利/300价值/基本面50)。
- ⚠️ **沪深300/500/1000 保留沪+深两个代码（互补，非冗余）**：index_weight 成分权重的归属代码随年份变化。保留双版保证任何年份成分穿透不缺数据。新增双版指数前务必两版都验早年+近年。
- **加新指数前先验能否取 index_weight**：`run_mcp(mcp_tushareMcp, index_weight, {index_code:"000016.SH", start_date:"20240101", end_date:"20240131"})`。返回非空 = 可加入，返回 `[]` = tushare 未收录。
- 改完用 `python scripts/sync.py --start YYYY0101 --end YYYY1231 --only index_weight,index_daily,index_dailybasic` 回补新指数历史。

---

## 5. 新功能挂接指南

- **扩 tushare 数据**：MCP 探查字段 → 改 `config/tushare_apis.json` → `data/etl/loader.py` 加 Calculator。
- **扩加工层计算**：先问"角色是什么" → panel（宽表）/ factor（因子）/ label（标签）→ 写 Calculator 子类，声明 `output_schema`，在对应 `__init__.py` 注册。
- **扩策略**：`portfolio/` 加策略类，`backtest/` 封装回测，`signals/` 复用策略逻辑生成信号。三者共用同一套策略代码。
- **扩调度**：`pipeline/schedule_*.json` 加任务配置。
- **新表**：不写 SQL，Calculator 里声明 schema 自动建表。

详细模板和 API 见 `DEV_GUIDE.md`。

---

## 6. 架构约定（工程硬规则见项目规则）

> **工程硬规则（harness 约束 H1-H12、三层 loop 验证门、协作节奏）的单一事实源是 `.trae/rules/nanoquant_loop.md`**。本节只保留**架构级**约定（为什么这么分层），不重复硬规则。

### 6.1 架构级约定（设计决策，非工程细节）

| 事项 | 约定 |
|------|------|
| 语言 | Python 3.11+（推荐 3.14） |
| 数据源 | tushare 为主，不用 akshare |
| 存储 | MySQL 8.x+，库名 `stock`，SQLAlchemy 2.x + pymysql |
| 计算结构 | 一律 `BaseCalculator` 子类，统一 `update(start_date, end_date, **params)` |
| 配置 | 密钥走 `.env`，`os.getenv` 默认值留空 |

### 6.2 架构级硬约束（分层与抽象）

1. **接入层与加工层解耦**：`data/etl/` 只放 tushare 1:1 复刻，`data/panel+factor+label/` 只放自定义计算。
2. **加工层用 panel 抽象**：panel / factor / label 三目录，粒度用表名前缀标。
3. **统一 `update`**：不传=增量、传=回补。
4. **biz_date 抽象**：频率不进 `update` 签名（调度频率走 schedule，数据频率走 get_data）。

> 其余硬规则（schema-as-code 细则、四类增量、DOUBLE/字符串两档、日期格式、路径、依赖、分区、Docker、MCP 探查、改动小可回滚、沙箱/emoji/日志/根因等 harness 约束，以及 Ingest/Compute/Strategy 三层 loop 的验证门）**全部见 `.trae/rules/nanoquant_loop.md`**。

---

## 7. 投资策略方向（业务共识，不要改方向）

1. **因子降级为风控诊断工具，不再当 alpha 来源。** 因子回答"组合安全吗、分散吗、何时该警惕"。
2. **主战场是 ETF 截面轮动 + 宽基底仓。** 轮动逻辑：截面动量排序 + 波动率过滤 + 风控约束。
3. **主观 + 量化分层。** 人管方向（核心池/观察池/回避池、风险预算），系统管节奏（周频排序、仓位约束、调仓建议）。
4. **资产范围分阶段扩。** Phase 1 纯 A股 ETF；Phase 2 港股科技/黄金/债券 ETF。

---

## 8. Git 仓库

- 远程：`https://github.com/yuchenhu/nanoquant`
- 主分支：`main`
- 提交规范：中文 commit message，简洁说明"做了什么"
- AI Agent 协作：完成可独立验证的模块后建议作者提交，不自动 commit/push

---

## 9. 开放问题（遇到时询问作者）

1. **ETF 池范围**：`DEFAULT_ETF_POOL` 是默认值，作者可调整。
2. **回测结果落库粒度**：每日净值 + 调仓记录都落库？
3. **财务数据历史深度**：全量回补从哪年开始？
4. **调度框架**：当前裸脚本 + Windows 任务计划，长期可迁 Prefect 2.x。

---

## 10. 与作者的协作基调

> 来自上一轮合作沉淀的两条长期约定。（一封完整的合作寄语原文另存于 `KEEPSAKE.md`，仅作收藏，不是行为指令。）

1. **延续作者"对数据较真"的习惯。** 遇到可疑的删除、数据异常、类型/口径不合理时，Agent 应主动质疑、追问，并用真实数据对照验证，不默认既有结论正确。

2. **按情境给方法论建议。** 切合作者"个人量化"（无团队、不定期开机、无考核压力）的背景，在合适的时机给出工作方法论上的指导意见——按需、点到为止。

3. **主动提醒工程最佳实践（硬约束）。** 作者是数据分析专业出身，软件开发经验尚浅。Agent 在"跑全量/改多文件/没先 mock/缺日志/想跳过报错"等场景**必须主动提醒、不怕反驳**——具体场景清单与提醒格式见 `.trae/rules/nanoquant_loop.md` §6 协作节奏（harness 约束 H9-H12 + §6）。提醒格式简短有力，如"先跑 1 天验证再扩月度，不然炸了不知道哪步"。
