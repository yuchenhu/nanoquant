# nanoquant ROADMAP（现状快照 + 缺口清单）

> 维护约定：本文只保留「当前快照 + 按优先级的缺口 + 下一步」。不记过程流水账——完成项合并进现状，历史细节看 git log。
> 最后更新：2026-10-05（指数池扩容 + 数据基建 overhaul + fin_snapshot 全量闭环）

---

## 1. 现状快照（已闭环/已验证）

### 接入层（闭环）

- 31 个 tushare 接口：四类增量策略（full_refresh / by_trade_date / by_period / by_ex_date）+ overwrite 幂等 + 主键去重护栏（重复显式 WARNING）。
- 水位表增量 + 兜底窗口 `min(水位, today-窗口)`：覆盖数据修订 + 久未开机不漏。
- 指数族全史：`INDEX_POOL` 28 码（size 11 + style 17）+ 3 alt，index_daily/index_weight/index_dailybasic 2004/2005 起，全部到 2026-09-30；含 931052 错码修正（国信价值冒名红利低波100）。
- ETF/基金数据：fund_basic / daily / adj / share / factor_pro 已全量；**fund_nav 有意推迟**（等执行层折溢价需求，届时只对 ETF 拉）。
- 已知数据边界：dividend 2018-06 前记录 ex_date 为 NULL（源端特性）→ 股息率类因子 2018-06 起可用；PE/PB 不受影响。
- 补数工具链：三入口（`ingest.py` 接入 / `compute.py` 加工 / `sync.py` 一键）+ `check_registry_and_gaps.py` 全库体检（覆盖/起点/断档/水位漂移/补数命令自动生成）+ 年分块回补 + `py.bat` 免激活启动器。
- 写入语义规则成文（DEV_GUIDE §4.4）：truncate（全量小表）/ 分区覆盖（批=完整分区）/ upsert（子集批）；upsert 已批量化（executemany，20 倍提速）。

### 加工层（核心底座已验证）

- `panel_index_membership_monthly`：28 指数成分底座（双版归一 / 月末网格 / 前向填充），2010-2026 全历史，931052 残留已清除。**2026-10-09 审计修复"月内去重"双 bug 并全量重建验收通过**（162.4 万行：旧逻辑逐股 tail(1) 残留调样换出股 -> 沪深300 330/官方300；修复中又引入跨月去重塌缩事故，均已修）。已知源侧限制：399303 国证2000 2014-2017 成分回填不全（1,410->1,884/2,000）、个别指数偶发缺 1 行（面板=源，忠实复刻）。审计器：research/audit_foundation_tables.py。
- `panel_stock_mv_monthly`：71 万行月度市值（2010-2026），月末固化 + 按年分块读取。
- `panel_financial_statements_snapshot`：**562 万行 PIT 财务快照全量闭环**（196 个月无缺月，8 份/股，PK 零重复），fast 批量回补路径（预载+逐月 PIT+分区覆盖写入）；PE/PB 自算分布 EDA 对锚点全部通过。口径与 CAVEAT 见 design notes §4.1。
- `panel_market_sentiment_monthly`：36 列五维情绪底表，2010-2026；`north_money` 单位跳变已归一。（估值列口径问题见缺口 5）
- `panel_stock_daily` / `panel_fund_daily`：个股/ETF 日频宽表（14M / 2.9M 行）。**is_xxx 设计债已清（2026-10-09，P1.5）**：8 个指数布尔列已从生产代码 + 物理表删除（MySQL 9 INSTANT DROP），sentiment 维度抽取改 membership as-of join，'all' 维度改"当月全部个股"口径。
- compute 层已瘦身：11 任务（下架 7 个旧因子骨架入 `_delisted`，factor_valuation 暂缓入 `_deferred`）。
- `label_index_forward_returns`：**已建成并全量验收通过（2026-10-09）**——22.4 万行 / 59 码（宽基+风格 28 + 申万 L1 31），PK 零重复，抽样对拍精确一致，五项锚点全过。口径见 design notes §8.7（T+1 收盘入 / N 日收盘出 / 自身序列 shift / 增量回刷 125 交易日）。行业池样本起点 2014-02-21，宽基池 2005 起。
- `run_compute.py` 拓扑调度（depends_on BFS + 严格模式）。

### 策略层（MVP，结论仅作粗筛）

- ETF 截面动量轮动 MVP：portfolio / backtest / signals 三层共用代码。
- 分析单元已从 ETF 迁移到**指数**（池 + membership + 自算估值）；ETF 退居执行层。
- 指标生成器 SPEC v3 定稿（design notes §4.1+§4.2）：因子字典 / 宽表结构 / 两层决策原则 / PE/PB 聚合口径。
- **2026-10-09 merge 7月工作线资产落地（均未对 10 月基建验收，用前先核）**：
  - `backtest/engine.py` 真实成本版（佣金/冲击/防未来函数/停牌过滤）——P0.1 骨架已在，缺口变为"验收 + 接入 10 月基建"；
  - `data/factor/{momentum_r2, momentum_term, overnight_intraday}.py` 3 个指数因子 calculator——**未注册调度**，与 `factor_index_daily` 计划重叠，建表时吸收/取代；
  - `data/label/forward_returns.py` 全资产重写版（ETF+指数+停牌处理+asset_type）——与 `label_index_forward_returns` 形成**指数 label 双源**，待去重决策（倾向：个股/ETF 归 forward_returns，指数归专表）；
  - `docs/spec_{backtest_engine,index_factors}.md` 两份 spec、`wiki/wiki/` 研究笔记、`scripts/_lint_consistency.py`。
- **注意**：当前向量化路径的回测收益仍虚高（见缺口 1），7月线引擎未验收前结论只作粗筛。

---

## 2. 缺口（按优先级）

### P0 · 不补会出假结论

1. **真实回测引擎**：~~从零建~~ **2026-10-09 更新：7月线已带回真实成本引擎骨架（backtest/engine.py），本项变为"验收该引擎 + 对接 10 月基建（label_index_forward_returns / factor_index_daily）+ 补资金约束"**。交易成本（佣金 + 冲击成本）、涨跌停/停牌不可成交、次日开盘/VWAP 成交、整数手与现金缓冲，逐项核实现有实现。
2. **index_signal 指标生成器 + 因子有效性检验体系**：SPEC v3 定稿（design notes §4.1+§4.2）——表改为 `factor_index_daily`（标的×日宽表，列名内嵌窗口，Tier A+B 一次建成）。原料全部就绪并已验收：label（§8.7）、宽基/风格 membership、SW membership（PIT as-of）。检验 = IC/ICIR、分层单调性、IC 衰减、因子相关矩阵（去冗余）、过滤器触发日重叠矩阵（Jaccard>0.7 不启用）；条件 IC 按 §8.5。

### P1 · 影响可信度与推进

3. **panel_fund_daily 尾部增量 + fund_nav（ETF-only）**：执行层前置。
4. **panel_market_regime 实现**：方法论已定稿——滚动百分位（零固定阈值）+ 等权投票 + 最小滞回；验证标准 = 下游夏普/回撤改善。消费 sentiment + index_signal。
5. **[设计债] sentiment 估值 7 列换自算口径**：现从 stock_daily_basic 的个股 pe_ttm/pb 聚合（tushare 口径不透明、亏损股处理未知、与自算管线并存两套口径）。整改：复用 index_signal 的 PE/PB 聚合函数（fin_statement_panel + panel_mv_monthly 月末 mv），sentiment 全量重刷一次。（原 P1.5 的成分抽取部分已于 2026-10-09 随 is_xxx 删列一并完成：sentiment 维度改 membership as-of join。）
6. **数据质量校验层**：复权断裂、价格跳变、财务异常值自动检测；ETF 清盘退市处理规则；ST/新股统一过滤口径。
7. **etf_index_mapping**：指数信号 → 具体 ETF 的执行映射（规模/流动性/费率选代表）。

### P2 · 效率（迭代变慢再上）

8. **DuckDB + Parquet 读引擎**：MySQL 仍为写入真相源；研究/回测改读按年分区 Parquet。方案见附录。
9. 量价类降频读取优化（IO -60%+）。

### P3 · 实盘断层（远期，当前 non-goal）

10. 信号→下单执行层；持仓跟踪 + 实盘 vs 回测偏差监控；实盘风控（止损/仓位上限/熔断）。

---

## 3. 下一步（推荐顺序）

1. `index_signal.py`：`factor_index_daily` Tier A+B 一次建成（spec v3；原料+底座已全部验收）+ 验收锚点 → P0.2 前半。
2. 因子有效性检验 research 脚本（IC/ICIR/衰减/相关矩阵 + §8.5 条件 IC）→ P0.2 后半。
3. 真实回测引擎（P0.1）——建好前任何轮动结论只作粗筛。
4. 缺口 5：sentiment 估值换自算口径（index_signal 的 PE/PB 聚合函数落地后联动）。

---

## 4. 数据能力边界（客观结论）

| 场景 | tushare + MySQL + Python 够不够 |
|---|---|
| 日频股票多因子研究 | 够用，主流个人配置 |
| 宽基/行业 ETF 指数动量轮动 | 够用（index_daily + fund_daily） |
| ETF 折溢价 / 套利 / 精细择时 | 不够（tushare ETF 数据深度限制） |
| 分钟级 / 实时 / Level2 | 不够（非实时源） |

---

## 5. 开放问题（待作者拍板）

1. **ETF 执行池范围**：etf_index_mapping 的流动性/规模门槛（P1.8 时定）。
2. **回测结果落库粒度**：每日净值 + 调仓记录都落库？还是只落最终结果？
3. **调度框架演进**：当前裸脚本 + Windows 任务计划，长期可迁 Prefect 2.x。

---

## 附：列存方案（DuckDB + Parquet）——P2.8 展开

**定位**：研究/回测的「读引擎」，不替换 MySQL（MySQL 继续当写入真相源）。

**为什么快**：多因子 EDA 是「读某几列、全历史、groupby」——列存只读需要的列（I/O 少几十倍）+ 高压缩（55GB → 10-15GB）+ 向量化执行。

**怎么用**（体验 ≈ sqlalchemy + navicat）：
- 查询：DuckDB（标准 SQL，`con.execute(sql).df()` 直接出 pandas）。
- 存储：Parquet 按年分区。
- 图形界面：DBeaver（免费）或 DuckDB Web UI。

**切换成本**：低、可回退。`pip install duckdb` + 一个导出脚本（MySQL → Parquet）+ 研究脚本改读 Parquet。不动现有写入链路。

**何时上**：因子 EDA 反复读数据明显卡顿时。现在 55GB 单机 MySQL 仍够用，不急。
