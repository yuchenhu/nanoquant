# nanoquant ROADMAP（缺口清单 + 补全计划）

> 现状：接入层 + 调度补数已闭环（约占整个策略闭环 30%）。
> 本文档记录从「有数据」到「能跑出可信结论 / 能实盘」还缺的部分，按优先级分阶段。
> 客观评估，逐项勾选。

### 2026-07-04 进度：ETF 数据底座扩展 + ETF 轮动策略设计

- [x] **接入层 +2 接口**：`fund_factor_pro`（60+ ETF 技术因子，5000 积分）+ `fund_nav`（ETF 净值，2000 积分），`tushare_apis.json` 配置 + `loader.py` Calculator + `TUSHARE_API_GUIDE.md` 更新
- [x] **ETF 数据全景梳理**：19 个 ETF/基金/港股通 MCP 接口探查、积分权限核对、更新策略确认
- [x] **ETF 轮动工作流定案**：股票 ETF 选指数→找 ETF；商品/债券 ETF 直接选 ETF。两阶段工作流（选指数 + 选 ETF + 相关性约束）
- [x] **market_sentiment_monthly review**：提 4 条建议——加 sector/style 维度、加 ETF 资金流指标、all 维度改 ETF 可投视角、月频对周频轮动的适配性
- [x] **ETF 资金流价值判断**：中低频宽基轮动场景下 ETF 折溢价=噪音、份额变化=冗余信息。用现有 fund_share × close 自算即可，不需充钱升 8000 分
- [ ] Next: `fund_factor_pro` + `fund_nav` 本地验证拉数 → `panel_etf_daily` 面板表 → `config/etf_universe.py` 指数→ETF 映射

---

### 2026-07-02 进度：market_sentiment_monthly 重构 + EDA 验证 + regime 方法论定案

- [x] **market_sentiment_monthly 重构**：删 ma120、加 dv_ttm_median、idx_ret 改日历月对齐、PE 5y 分位改月末抽样（IO ↓~20×）、amount_pct 改月vs月对比
- [x] **schedule_compute.json 注册**：depends_on 改为实际上游（stock_daily_panel, index_daily, moneyflow_hsgt, margin, limit_list_d）
- [x] **逐月回补脚本**：`scripts/backfill_market_sentiment.py`（--start/--end/--resume 断点续跑）
- [x] **2024 全年 12 个月 EDA 验证**：手算交叉验证通过；发现并修复 4 个 bug
- [x] **CLAUDE.md 原则 2 强化**：零固定阈值、等权优先加权需举证、验证靠下游有用性（4 条硬约束）
- [x] **regime 方法论定案**：滚动百分位 + 等权投票 + 最小滞回，待实现 `panel_market_regime`
- [ ] Next: 全量回补 2010-2026 → `panel_market_regime` 实现

---
### 2026-06-28 进度：DQC 审计 + 频率超限修复 + 缺失表补齐

- [x] **接入层全量 DQC 审计**（sync.log 18 万行解析）：7 类问题覆盖 14/17 个回补年份
- [x] **频率超限修复**：`by_trade_date.py` 改为每次请求后 sleep 0.3s（原每 10 次 sleep 0.5s），`BY_TRADE_DATE_SLEEP` 环境变量可调；日常增量不撞限
- [x] **`backfill_years.py` 加 `--only`**：可指定接口名补数（如 `--only dividend,suspend_d`）；加 `--skip-refresh` 跳过清单全量刷新
- [x] **`schedule_ingest.json` 加入三新表**：moneyflow_hsgt / margin / limit_list_d（已在 loader.py 注册，只缺调度入口）
- [x] 三新表 CREATE TABLE 已交付，待用户手动建表

---

### 2026-06-28 进度：中间 Panel 表 + 估值因子管线重构
- [x] **panel_stock_daily 重构**：`_join_index_weight` 改读 `panel_index_membership_monthly`；新增 `is_sz50`，删 `is_zzhl`；`_join_index_member` 加 `out_date` 过滤
- [x] **panel_stock_percentiles 重构**：窄列 SELECT + `rolling.rank()` + `above_ma` + 3y/5y 分位
- [x] **financial_statements_snapshot / indicators**：显式 output_schema（避免 NULL 首行推断错误）
- [x] **factor_valuation 重写**：从 indicators 取 18 个比值，36 月 PIT 窗口，全量叉乘 5-6 种衍生（mean/std/zscore/tsrank/momentum/neg_cnt）≈ 90 列
- [x] **schedule_compute.json**：fin_statement / indicator / valuation 迁至 monthly 节
- [ ] Next: 数据回补 → run_compute 验证 → IC 分析筛因子 → 补其他基本面因子（profitability/growth/safety）

---

---

## 阶段 0 · 当前已完成 ✅

- [x] 31 个 tushare 接口接入（含 ETF：fund_basic/daily/adj/share/factor_pro/nav）
- [x] 4 类更新策略 + overwrite 幂等 + 去重护栏
- [x] schema-as-code（数值 DOUBLE / 字符串两档自动推断）
- [x] 增量起点 `min(水位, today-窗口)`（覆盖修订 + 久未开机不漏）
- [x] sync.py 一键补数 + 逐年回补 + `scripts/py.bat` 启动器（免激活）
- [x] 防穿越基础（财务多版本保留 + f_ann_date）
- [x] **+3 新接入源（2026-06-23）**：`moneyflow_hsgt`(北向) / `margin`(两融) / `limit_list_d`(涨跌停)，by_trade_date 自动纳入回补，已实拉验证
- [x] **DQC 监控表（2026-06-25）**：`panel_data_quality` — 22 张接入源 × 每应有日期的实际行数监控表（status=OK/MISSING/PARTIAL），全量重算+truncate。接入层全量 DQC 通过：仅剩数据源特性异常（stock_st 早年、sw_daily/moneyflow_hsgt 休市日）和今日未拉。
- [x] **scripts/py.bat 编码修复**：`chcp 65001 + PYTHONUTF8=1`，消除控制台中文 log 乱码。旧 `scripts/data_dqc.py` 已删（功能由 panel_data_quality 表替代）。
- [x] **suspend_d / dividend 修复**：主键 `ann_date→ex_date`（dividend）+ 列类型 `suspend_timing→TEXT`，全量重补无报错。
- [x] **panel_index_membership_monthly 实现（2026-06-25）**：长表 `(trade_date,ts_code,index_code,index_name,weight)`，四步清洗（双版归一/月内去重/月末网格/前向填充），全量 universe 15 指数，全历史回补完成。月级唯一保证：`overwrite+partition_col` + 落库前 DELETE 同月旧行。trade_date 存 MySQL DATE 类型。

---

## 阶段 0.5 · 部分落地的地基（schema 已钉死，实现挂 TODO）🔄

> 已设计好 schema 并建成空表/占位 Calculator，待上游数据就绪后回填逻辑。
> 对现有 pipeline 无破坏（update 返回空，优雅跳过）。

**⚠️ 加工层日期格式统一规则**：接入层 `trade_date` 存的是 MySQL DATE 类型（`yyyy-mm-dd`），加工层所有新表的 `trade_date` / `biz_date_col` 输出统一用 **`yyyy-mm-dd` 字符串**，不用 `YYYYMMDD`。`BaseCalculator.update()` 传进来的 `start_date/end_date` 是 `YYYYMMDD` 格式，加工层内部 `pd.to_datetime` 统一处理，落库 `convert_date_columns` 自动转 DATE。**不在加工层做格式互转**。

### 0.5a panel_index_membership_monthly（指数成分归属 · 清洗 index_weight）✅ 已完成
接入层 `index_weight` 有三处"脏"（已用 MCP + 本地库交叉验证）：
1. 双版镜像（沪深300有 000300.SH/399300.SZ，成分逐行完全一致）→ 必须归一
2. "月度"名不副实（沪深300一年 22~26 个 trade_date，其他 12 个）→ 必须月内去重
3. 成分在两次调样间延续 → 必须月末网格 + 前向填充（无未来函数）
加工这张干净底座。

已钉死：
- [x] schema：`(trade_date, ts_code, index_code, index_name, weight)`，长表，保留全量 universe 15 个 canonical 指数
- [x] **清洗四步（带具体策略）**：
  1. 双版归一 → `config.universe.CODE_TO_CANONICAL`（399300.SZ→000300.SH）
  2. 月内去重 → 按 `(canonical, con_code, 年月)` GROUP，取该月最后 trade_date
  3. 月末网格 → `core.dates.get_monthly_last_tradedate()` 构造标准月末交易日列表
  4. 前向填充 → `bisect` + 分组 merge-asof（取 ≤ 该月的最近调样月成分）
- [x] 已注册到 `data/panel/__init__.py` PANEL_CALCULATORS
- [x] **READ_BUFFER_DAYS=400**：往前多读 400 天，保证区间首月能取到上一次调样
- [x] **日期格式注意**：index_weight.trade_date 库里是 `yyyy-mm-dd`，用 `pd.to_datetime` 处理（不用字符串比较）
- [x] **月级唯一保证（2026-06-25）**：`write_mode=overwrite + partition_col=trade_date`，`save_to_database` 落库前 `DELETE WHERE DATE_FORMAT(trade_date, '%Y-%m') IN (输出月份)`。7 月重算时旧月末快照被清掉，每月每个指数就一个 trade_date。
- [x] **已注册 schedule_compute.json + 全历史回补完成**（15 指数，2010-01 ~ 今）
- [x] **下游消费**：market_sentiment_monthly 已读此表取月末成分（见 0.5b），stock_daily_panel 的 is_xxx 重构待后续

### 0.5b panel_market_sentiment_monthly（市场情绪底表 · regime 输入）✅ 已实现 + 已验证

> **2026-07-02 更新**：旧版 36 列已废弃。全量重构为 36 列（价11/量6/波6/估值7/资金7），12 个月 EDA 验证通过，待全量回补。

已钉死 + 已实现：
- [x] **schema**：五维度 36 列（全部已注释物理意义 + 公式来源）。
  **价(11)**：idx_close, ma60/250（删 ma120）, idx_ret_1m/3m/12m（**日历月对齐**，非固定交易日数）, profit_ratio, up_down_ratio, pct_above_ma60, pct_above_ma250, limit_up_count。
  **量(6)**：idx_amount, turnover_rate_median, amount_pct_3m, amount_pct_1y（**月总额 vs 月总额**，非月vs日）, amount_gini。
  **波(6)**：idx_volatility_20/60, max_drawdown_1y, avg_correlation（CBOE KCJ同源公式）, cross_sectional_vol, downside_vol_ratio。
  **估值(7)**：pe_ttm_median, pb_median, **dv_ttm_median（股息率，价值风格估值锚）**, pe_pct_5y, pb_pct_5y（**月末抽样 60 点替代逐日 1250 点，IO ↓~20×**）, pe_dispersion, pb_pe_divergence。
  **资金(7)**：全A独有 north_money/margin_balance；各维度 net_inflow_ratio, inflow_direction_pct, inflow_stability, inflow_breadth, institutional_pct。
- [x] **维度**：`dimension_type='all'` + `'index'`（50/300/500/1000/2000）
- [x] **get_data / process_data 已实现**：2 路取数（日线前溯1年 + 月末抽样前溯5年）、MA60/250 预计算、日历月末收盘 lookup
- [x] **已注册 schedule_compute.json**（monthly 节，depends_on: stock_daily_panel, index_daily, moneyflow_hsgt, margin, limit_list_d）
- [x] **2024 全年 12 个月 EDA 验证通过**：手算交叉验证 idx_close/ma/ret/pe/pb/dv/north_money 全部对齐；idx_ret 与 close 环比相关性 1.0；全A 独有列 null 隔离正确
- [x] **3 个单月回补 bug 已修复**：① ret 回看用 all_month_ends（非仅计算期）② amount_pct 月vs月对比（非月vs日）③ hsgt/margin/limit 取整月数据（非仅月末一天）；pct_above_ma60>1.0 边界 bug 已修复
- [x] **所有抽象指标已注释物理含义**（up_down_ratio / amount_gini / pe_dispersion / pb_pe_divergence / avg_correlation / cross_sectional_vol / downside_vol_ratio / 资金流四指标）
- [x] **逐月回补脚本**：`scripts/backfill_market_sentiment.py`（--start/--end/--resume，断点续跑）
- [ ] **待全量回补**：2010-2026 全历史分年分批跑（单月~4-7min，约 192 个月）
- [ ] **null 容忍/不可覆盖字段**：待后续决策（limit_up_count 早年、margin 早年可能缺失等）

### 0.5c 市场状态(regime)方法论（已讨论定稿，待实现）

> **2026-07-02 更新**：设计定案——滚动百分位 + 等权投票 + 最小滞回。CLAUDE.md 原则2 强化为硬约束（零固定阈值、等权优先、验证靠下游有用性）。

- [x] 全局 + 风格二维 regime，全A + 50/300/500/1000/2000 各一行
- [x] 3 态(牛/震荡/熊) + 滞回 + 最短持续期
- [x] **百分位化（非固定阈值）**：幅度指标取 5 年滚动分位（vol > 75pct → 高波），方向指标不做百分位（close > ma → 偏多）
- [x] **等权投票聚合**：维度内简单计数（≥+2 = trend_up），不等权不加。regime 无 ground truth，不等权无法被下游证明更好
- [x] **滞回最小化**：只过滤"牛→震荡→牛"单月抖动，不强行修正持续方向变化。raw 信号质量是根本，滞回是安全网
- [x] **验证标准**：下游夏普/回撤改善 → regime 有用，不追求"label 准确率"
- [ ] **待实现**：`data/panel/market_regime.py`（读 market_sentiment_monthly → 百分位化 → 维度投票 → 综合判定 → 滞回），挂 schedule_compute.json monthly 节，depends_on: market_sentiment_monthly

### 0.5d 中间 Panel 表（panel_stock_daily / percentiles 重构）🔄 2026-06-28
为市场状态表及其他下游提供干净底座，已完成重构：
- [x] **panel_stock_daily 重构**：`_join_index_weight` 改读 `panel_index_membership_monthly`（不再直接读 `index_weight`）；新增 `is_sz50`（上证50），删 `is_zzhl`（中证红利）；`_join_index_member` 增加 `out_date` 过滤修复历史行业误配。
- [x] **panel_stock_percentiles 重构**：窄列 SELECT（8 列代替 60+ 列，IO -85%）；`rolling.rank()` 替代 `percentileofscore`（C 原生 vs Python lambda）；新增 `above_ma20/60/250`（0/1 标记）+ `price/pe/pe_ttm/pb_tsrank_3y/5y`（3y/5y 时序分位）。
- [ ] **TODO: 量价类降频优化**：量价指标（close/turnover/volatility）只需 1y 日频，PE/PB 类需 5y 但可降月频（读 `panel_financial_indicators_snapshot`）。此举可将 extended 读取窗口从 1450 日砍到 ~60 月 + 350 日，增量 IO 再降 60%+。等 `financial_indicators_snapshot` 全量回补后实施。
- [ ] **TODO: 调度层控制财务快照按月末频率**：`financial_statements_snapshot` / `indicators` / 下游 valuation 因子按 snapshot_date = monthend 批量回补。不做日历天盲跑。由 run_compute.py 外部循环或 schedule_compute.json 的频率控制实现。

---

## 阶段 1 · 致命缺口（不补会出假结论）🔴

### 1.0 panel 指数成分表（→ 见阶段 0.5a，已完成）
- [x] **schema + 清洗逻辑 + get_data/process_data 全部实现**，全历史回补完成（2010-01 ~ 今）

### 1.1 真实回测引擎
向量化回测对 ETF 轮动有硬伤，必须建模真实约束，否则收益虚高 2-5 点/年。
- [ ] **交易成本建模**：ETF 佣金 + 冲击成本（轮动换手高，这是最大收益杀手）
- [ ] **涨跌停 / 停牌不可成交**：调仓日标的停牌或一字板时跳过（接入层已有 suspend / stk_limit 数据，核对回测是否真用上）
- [ ] **成交价假设修正**：禁止用「算信号当日的收盘价」成交（未来函数），改用次日开盘 / VWAP
- [ ] **调仓节奏 / 资金约束**：整数手、最小下单、现金缓冲

### 1.2 因子有效性检验体系（多因子核心）
有因子表但无评估 = 盲调。这是从「有数据」到「有策略」的必经环节。
- [ ] **IC / ICIR**：因子值与未来收益的相关性 + 稳定性
- [ ] **分层回测（quantile backtest）**：因子分 5-10 组，看单调性 + 多空收益
- [ ] **因子衰减**：IC 随预测周期(1/5/10/20日)的衰减曲线
- [ ] **因子换手率 / 拥挤度**：换手太高 → 成本吃掉 alpha
- [ ] **因子相关性矩阵**：剔除冗余因子，避免多因子合成时重复下注

### 1.3 防过拟合机制
- [ ] **样本切分约定**：train / valid / test 时间分段，禁止全样本调参
- [ ] **参数框架**：lookback / 持仓数 / 阈值的搜索 + 样本外验证
- [ ] **多因子合成方法**：等权 / IC 加权 / 回归，定方法论而非拍脑袋

---

## 阶段 2 · 重要缺口（影响可信度 / 效率）🟡

### 2.1 数据质量校验层
- [ ] 复权因子断裂、价格跳变、财务异常值自动检测
- [ ] ETF 折溢价、规模骤变、清盘退市处理规则
- [ ] ST / 退市 / 新股上市初期的统一过滤口径
- [ ] **DQC 全集体检**：全部 29 个数据源历史数据补完后，跑一次完整 data quality check（覆盖度/异常值/NULL/复权/跨表一致性），产出一份 DQC 报告

### 2.2 ETF 数据完整性（受 tushare 天花板限制）
- [ ] 评估是否需要 ETF 申赎清单(PCF) / IOPV / 跟踪误差（tushare 弱或没有）
- [ ] 结论：宽基/行业 ETF「指数动量型」轮动 tushare 够用；折溢价/套利型做不了，需换数据源

### 2.3 存储性能（研究迭代变慢时再上）
> 详见本文档末「附：列存方案(DuckDB + Parquet)」。**不是前置必需，EDA 卡顿时再上。**
- [ ] 加 DuckDB + Parquet 作「研究读引擎」（MySQL 仍当写入真相源）
- [ ] 导出脚本：MySQL 表 → 按年分区 Parquet
- [ ] 研究/回测/因子检验改从 Parquet 读

---

## 阶段 3 · 实盘断层（研究 → 交易）⚪

> 当前是「研究框架」，不是「交易系统」。实盘前补。
- [ ] 信号 → 下单的执行层（券商接口）
- [ ] 持仓跟踪 + 实盘 vs 回测的误差监控
- [ ] 实盘风控（止损 / 仓位上限 / 异常熔断）

---

## 技术栈适配性结论（客观）

| 场景 | tushare + MySQL + Python 是否够用 |
|---|---|
| 日频股票多因子研究 | ✅ 完全够用，主流个人量化配置 |
| 宽基/行业 ETF 指数动量轮动 | ✅ 够用（index_daily + fund_daily） |
| ETF 折溢价 / 套利 / 精细择时 | ❌ 受 tushare ETF 数据深度限制，做不了 |
| 分钟级 / 实时 / Level2 | ❌ tushare 非实时，数据深度不足 |

**优先级提醒**：最该先补的不是数据（地基够了），而是 **1.1 真实回测 + 1.2 因子检验**——否则补再多数据，也是在没有刻度尺的情况下量东西。

---

## 附：列存方案（DuckDB + Parquet）—— 阶段 2.3 展开

**定位**：研究/回测时的「读引擎」，不替换 MySQL（MySQL 继续当写入真相源）。

**为什么快**：多因子 EDA 是「读某几列、全历史、groupby」，列存只读需要的列(I/O 少几十倍) + 高压缩(55GB→10-15GB) + 向量化执行。

**怎么用**（体验≈ sqlalchemy + navicat）：
- 查询：DuckDB（SQL 兼容标准 SQL，Python 里 `con.execute(sql).df()` 直接出 pandas）
- 存储：Parquet 文件（按年分区）
- 图形界面：DBeaver（免费，连 DuckDB）或 DuckDB 自带 Web UI

**切换成本**：低、可回退。`pip install duckdb` + 一个导出脚本（MySQL→Parquet）+ 研究脚本改读 Parquet。不动现有写入链路。

**何时上**：因子 EDA 反复读数据明显卡顿时。现在 55GB 单机，MySQL 仍够用，**不急**。
