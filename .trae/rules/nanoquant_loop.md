---
description: nanoquant 项目 Loop Engineering 硬规则收口——五移动(发现/移交/验证/持久/调度)+ 生成器/评估器分离 + harness 约束。所有 AI 对话始终生效。CLAUDE.md/DEV_GUIDE.md 只保留架构图与 API 速查，工程硬规则以本文件为准。
alwaysApply: true
---

# nanoquant · Loop Engineering 硬规则

> 本文件是 nanoquant **工程硬规则的单一事实源**。架构地图/业务原则见 `CLAUDE.md`，API 速查/踩坑目录见 `DEV_GUIDE.md`，进度见 `ROADMAP.md`。两者与本文冲突时，**以本文件为准**。

## 0. 三条心法（Loop Engineering，每次动手前自检）

1. **每一层失败方式不同，检查要装在不同位置。** Prompt 层错当场可见；loop 层错会被状态文件/水位/落库带到下一轮，几天才发现。越靠近"自动循环"层，越要装一个**能说"不"的东西**。
2. **生成器 ≠ 评估器。** 写代码的 agent 给自己打分太软。任何"我跑通了/数据对了"的断言，必须有**独立可复查的检查**（行数/null/主键重复/穿越/值域）能推翻它。否则视为未验证。
3. **错误成本随它存活的轮数指数增长。** Loop 是最大化轮数的机器。每个验证门都是为了缩短"错误→发现"的距离，**不可省**。宁可慢一轮，不可带病进下一轮。

---

## 1. Harness 约束（跨切面硬规则，无例外）

| # | 约束 | 理由 |
|---|---|---|
| H1 | **沙箱跑 Python 按风险分档** | ✅ **可直接沙箱跑**：快/短/只读/ASCII 输出——schema 查询（`SELECT COUNT(*)`/列对齐）、行数/null 统计、5-10 行 mock DataFrame 语法试跑、pandas 逻辑验证。<br>❌ **必须给 .py 脚本由作者本地 cmd 跑**：补数/回补/`run_compute`/`backfill`/大数据 EDA/任何写库/任何拉 tushare（耗时长+撞限频+花钱不可逆）。<br>**拿不准 → 当长任务处理（给本地）**。沙箱有引号转义/中文截断/进程被杀/输出丢失四类硬伤，长任务必踩；快速只读脚本踩不到，沙箱跑省往返。 |
| H2 | **禁 emoji/非 ASCII 符号** | Windows GBK 控制台 print emoji 直接 `UnicodeEncodeError` 崩溃。标记一律 ASCII：`[OK]`/`[WARN]`/`[ERR]`/`[EMPTY]`。 |
| H3 | **数值列统一 DOUBLE** | 不用 DECIMAL（大额字段溢出硬报错中断 pipeline，省空间<1%不值）。 |
| H4 | **字符串两档** | 名称/长文本 `VARCHAR(255)`，`desc` TEXT，其余（code/flag/枚举）统一 `VARCHAR(32)`。 |
| H5 | **日期格式** | 接入层 `yyyymmdd` 字符串入库转 DATE；加工层统一 `yyyy-mm-dd` 字符串，内部 `pd.to_datetime` 处理，**不做格式互转**。 |
| H6 | **路径** | `Path(__file__)` 相对定位，不出现绝对路径。 |
| H7 | **依赖** | `requirements.txt` 用 `>=,<` 范围，不锁 `==`。 |
| H8 | **不分区、不做 Docker** | 数据量未到；云迁移靠 `os.getenv` 覆盖。 |
| H9 | **改前必读** | 每次改代码前用 Read 看当前代码确认上下文，不凭记忆改。 |
| H10 | **日志是眼睛** | 长流程每个 join/步骤加 `[n/N] xxx...` + 行数 + 耗时。没日志=盲跑。 |
| H11 | **错误信息=路标** | 报错坚持排查根因，不留坑；读 traceback 从底向上。作者想跳过报错→AI 必须坚持排查。 |
| H12 | **一次改一个模块** | 改完验证再动别的。改三处→三处都炸→不知道谁炸。 |

---

## 2. Loop 1 · Ingest（接入层）

> **触发**：补数 / 回补 / 加新 tushare 接口。
> **可验证目标**：库内行数合理 + 主键无重复（数据论证）+ 不穿越 + EDA 与 A 股常识一致。

| 移动 | 硬规则 |
|---|---|
| **发现** | 改/加接口前**先用 `mcp_tushareMcp` 探查**字段/参数/返回（不人肉翻文档）。加新指数前先验 `index_weight` 能否取到（返回 `[]`=未收录）。 |
| **移交** | `config/tushare_apis.json` 加项（标 `incremental_strategy`+`biz_date_col`+`fields`）+ `data/etl/loader.py` 加 Calculator。**一次一个接口**。 |
| **验证**（必过门） | ① 跑 **1 个交易日** → 看行数（全市场 ~5000 股）→ 扩月 → 扩年。**禁止直接跑全量/一年**。<br>② **EDA**：null 比例、值域、跨表一致性、与 A 股常识对照（如 `north_money` 2024-08-19 单位跳变万元→元）。<br>③ **主键无重复必须用数据论证**（`df.duplicated(subset=PK).sum()`），不是假设。财务三表 PK=5 列实测 25 万行 0 重复。<br>④ **防穿越**：财务保留所有 `f_ann_date` 版本，加工层 `WHERE f_ann_date <= snapshot_date` 选版（point-in-time）。 |
| **持久** | 水位表 `etl_biz_date`（`update` 跑完写 max biz_date）。`overwrite` 幂等：**取数维度==删除维度==partition_col**。落库前按主键去重，`update_flag` 留最大版本。 |
| **调度** | 日常 `sync.py` 增量；回补逐年一条 `--start YYYY0101 --end YYYY1231`，断了重跑无副作用。 |

**四类增量策略**（取数维度==落库分区键）：`full_refresh`(truncate) / `by_trade_date`(trade_date) / `by_period`(end_date) / `by_ex_date`(ex_date)。**已废弃 upsert**。

---

## 3. Loop 2 · Compute（加工层 panel/factor/label）

> **触发**：写/改 panel/factor/label Calculator。
> **可验证目标**：mock 跑通 + 单日回补对齐 + 全量 EDA 通过 + 防穿越。

| 移动 | 硬规则 |
|---|---|
| **发现** | 改前先读 panel schema + 现有 Calculator 代码（H9）。确认上游依赖（`depends_on`）。 |
| **移交** | pandas 语法（merge/pivot/`merge_asof`/groupby）**先用 5-10 行 mock DataFrame 本地验证，不连库**。**一次一个 Calculator**。 |
| **验证**（必过门，按序） | ① **mock 跑通**（语法对）<br>② **单日回补**（连库 1 天，看列对齐/null/行数）<br>③ **区间回补**（1 个月）<br>④ **全量回补**<br>⑤ **EDA**：趋势与 A 股历史印证；`inf/NaN` 处理（`_safe_div` 包装除零）；单位跳变扫描。<br>⑥ **`astype(int)` NaN 炸弹**：`output_schema` 声明 `"int"` 的列，落库前**必须**确保无 NaN——`.dt.days` 来自 NaT、merge 缺行、`np.where` 含 NaN 都会产生 NaN → `astype(int)` 炸 `IntCastingNaNError`。修复：`.fillna(-1).astype(int)`、`.fillna(False).astype(int)`。详见 DEV_GUIDE §7.17。<br>⑦ `merge_asof + by` 陷阱：**只按 `on` 列排序**（全局单调），不按 `by+on`（跨 ts_code 重置会报 `left keys must be sorted`）。<br>⑧ 指标列必须注释**物理意义**（回答什么具体问题 + 公式来源），不写"XX指标"废话。 |
| **持久** | `write_mode=overwrite`+`partition_col`；`output_schema` 手写（不自动推断）。新表不写 SQL，Calculator 声明 schema 自动建表。落库前按主键去重，**发现重复主键 WARNING 显式列出被删主键，不静默吞**。 |
| **调度** | `run_compute.py` 按 `schedule_compute.json` 的 `depends_on` BFS 拓扑排序；严格模式：上游失败→下游全部跳过。 |

**接入层 vs 加工层解耦**：`data/etl/` 只放 tushare 1:1 复刻；`data/panel+factor+label/` 只放自定义计算。**加工层用 panel 抽象**（panel/factor/label 三目录，表名前缀标粒度）。

---

## 4. Loop 3 · Strategy（策略层 portfolio/backtest/signals）

> **触发**：写/改 portfolio/backtest/signals。
> **可验证目标**：真实成本回测 + 防穿越 + 分年度 + 样本外。

| 移动 | 硬规则 |
|---|---|
| **发现** | 读 panel/factor/label 表确认输入对齐。 |
| **移交** | 回测/信号/组合**共用同一套策略代码**（避免回测/实盘两套）。 |
| **验证**（必过门） | ① **真实成本**：ETF 佣金 + 冲击成本（轮动换手高，最大收益杀手）；涨跌停/停牌不可成交（核对 suspend/stk_limit 是否真用上）；**成交价禁止用信号当日收盘**（未来函数），用次日开盘/VWAP。<br>② **分年度**：禁止全样本调参；train/valid/test 时间分段。<br>③ **防过拟合**：lookback/持仓数/阈值搜索 + 样本外验证。<br>④ **验证靠下游有用性**（夏普/回撤改善），不靠"label 准确率"（regime 无 ground truth）。 |
| **持久** | `signal_rebalance` 表落库。 |
| **调度** | 周频调仓。 |

**投资策略方向**（业务共识，详见 `CLAUDE.md` §7）：ETF 截面轮动为主引擎 + 宽基底仓；因子降级为风控诊断工具，不卷 alpha。Regime 硬约束：零固定阈值（用滚动百分位）、等权优先（加权需举证）、验证靠有用性。

---

## 5. 评估器原则（能说"不"的东西）

- 任何"我跑通了/数据对了"的断言，AI 必须给出**可独立复查的证据**（行数/null 统计/主键重复数/穿越检查/值域），否则视为未验证。作者可随时要求"给我证据"。
- `tests/test_step*` 是历史验收测试，多数已与现状不符，**不是回归套件**。以 `run_compute.py --list` 实际输出为准。
- 数据可疑（删除/异常/类型口径不合理）→ 主动质疑、追问、用真实数据对照验证，不默认既有结论正确。
- 作者说"我觉得够用了" → AI 必须判断业界是否真够用；与专业实践冲突 → 必须指正并给理由，不迎合。
- 第一版即前沿：regime detection / 因子检验 / 回测设计，首次输出就应是专业基线，不允许先 toy 再"等追问升级"。

---

## 6. 协作节奏（人机循环）

- **理想节奏**：1 个明确需求 → AI 改 1 个模块 → 作者跑 1 条命令验证 → 确认再下一步。
- AI 给命令 → 作者本地 cmd 跑 → 贴回日志 → AI 分析。长任务（补数/回补/run_compute/backfill/大数据 EDA/写库/拉 tushare）必须给本地跑；快/短/只读脚本可沙箱跑（H1）。
- 作者要求"跑全量/跑一年" → AI **必须先提醒**跑 1 天验证再扩月度/年度。
- 作者要求"改多个文件一起跑" → AI **必须提醒**一次改一个模块，验证再下一步。
- 改 pandas/SQL 但没先 mock → AI **必须提醒**用 5 行 DataFrame 验证。
- 脚本缺日志/进度标记 → AI **必须提醒**加 `[n/N]` 步骤日志。
- 遇报错作者想跳过 → AI **坚持排查根因**，不留坑。
- 提醒格式简短有力，如"先跑 1 天验证再扩月度，不然炸了不知道哪步"。

---

## 7. 新增 loop 的准入清单（设计任何自动循环前自检）

> 来自 Loop Engineering：一个 loop 必须同时有**触发器**和**可验证目标**，否则是"自信满满的 token 焚烧炉"。

1. **触发器**：什么启动它？（定时/事件/手动）没有触发器=你还在 loop 里。
2. **可验证目标**：怎么判断"完成"？（行数/测试绿/穿越检查/夏普）没有=不是 loop。
3. **能说"不"的检查**：loop 内部装了什么独立检查能停掉它？没有=agent 给自己点赞。
4. **状态持久**：结果写到对话外（表/文件/水位），下次能接着跑。
5. **blast radius**：错了能回滚吗？错误能存活几轮才被发现？

详见 `LOOP_ENGINEERING.md`。
