# Tushare API 使用指南（经验证）

> **定位**：本项目实际接入的 **29 个 tushare 接口**的权威使用说明。
> 每个接口的更新策略都有三重背书：① **官方文档依据**（参数语义 + 更新频率）；② **MCP 实测**（用历史数据验证能取到数、不穿越）；③ **主键无重复的数据论证**（实测行数统计）。
> 最后验证：2026-06-23 ｜ 配套代码：`config/tushare_apis.json` + `data/etl/loader.py` + `pipeline/incremental/*` + `config/database.py`


## 1. 总览：29 个接口 × 4 类更新策略

| # | config_key | api_name | 表名 | 策略 | write_mode | 分区键 | 主键(PK) | 数据频率 |
|---|---|---|---|---|---|---|---|---|
| 1 | trade_cal | trade_cal | trade_cal | full_refresh | truncate | — | exchange+cal_date | 不定期 |
| 2 | stock_basic | stock_basic | stock_basic | full_refresh | truncate | — | ts_code | 每日 |
| 3 | index_basic | index_basic | index_basic | full_refresh | truncate | — | ts_code | 每日 |
| 4 | index_classify | index_classify | index_classify | full_refresh | truncate | — | index_code | 不定期 |
| 5 | index_member_all | index_member_all | index_member_all | full_refresh | truncate | — | ts_code+l1_code+in_date | 不定期 |
| 6 | fund_basic | fund_basic | fund_basic | full_refresh | truncate | — | ts_code | 每日 |
| 7 | daily | daily | stock_daily | by_trade_date | overwrite | trade_date | ts_code+trade_date | 每日 15-17h |
| 8 | weekly | weekly | stock_weekly | by_trade_date | overwrite | trade_date | ts_code+trade_date | 每周五 |
| 9 | monthly | monthly | stock_monthly | by_trade_date | overwrite | trade_date | ts_code+trade_date | 每月 |
| 10 | adj_factor | adj_factor | adj_factor | by_trade_date | overwrite | trade_date | ts_code+trade_date | 每日 |
| 11 | daily_basic | daily_basic | stock_daily_basic | by_trade_date | overwrite | trade_date | ts_code+trade_date | 每日 15-17h |
| 12 | moneyflow | moneyflow | moneyflow | by_trade_date | overwrite | trade_date | ts_code+trade_date | 每日 19h |
| 13 | stock_st | stock_st | stock_st | by_trade_date | overwrite | trade_date | ts_code+trade_date | 每日 |
| 14 | suspend_d | suspend_d | suspend | by_trade_date | overwrite | trade_date | ts_code+trade_date | 每日 |
| 15 | sw_daily | sw_daily | sw_daily | by_trade_date | overwrite | trade_date | ts_code+trade_date | 每日 |
| 16 | index_daily | index_daily | index_daily | by_trade_date | overwrite | trade_date | ts_code+trade_date | 每日 |
| 17 | index_dailybasic | index_dailybasic | index_daily_basic | by_trade_date | overwrite | trade_date | ts_code+trade_date | 每日 |
| 18 | index_weight | index_weight | index_weight | by_trade_date | overwrite | trade_date | index_code+con_code+trade_date | **月度** |
| 19 | fund_daily | fund_daily | fund_daily | by_trade_date | overwrite | trade_date | ts_code+trade_date | 每日 |
| 20 | fund_adj | fund_adj | fund_adj | by_trade_date | overwrite | trade_date | ts_code+trade_date | 每日 |
| 21 | fund_share | fund_share | fund_share | by_trade_date | overwrite | trade_date | ts_code+trade_date | 不定期 |
| 22 | income_vip | income_vip | income | by_period | overwrite | end_date | ts_code+end_date+ann_date+f_ann_date+update_flag | 实时(随财报) |
| 23 | balancesheet_vip | balancesheet_vip | balancesheet | by_period | overwrite | end_date | 同 income（5 列） | 实时 |
| 24 | cashflow_vip | cashflow_vip | cashflow | by_period | overwrite | end_date | 同 income（5 列） | 实时 |
| 25 | disclosure_date | disclosure_date | disclosure_date | by_period | overwrite | end_date | ts_code+end_date | 不定期 |
| 26 | dividend | dividend | dividend | by_ex_date | overwrite | ex_date | ts_code+end_date+ex_date+div_proc+update_flag | 实时 |
| 27 | moneyflow_hsgt | moneyflow_hsgt | moneyflow_hsgt | by_trade_date | overwrite | trade_date | trade_date | 每日盘后 |
| 28 | margin | margin | margin | by_trade_date | overwrite | trade_date | trade_date+exchange_id | 次日 9h |
| 29 | limit_list_d | limit_list_d | limit_list_d | by_trade_date | overwrite | trade_date | trade_date+ts_code | 每日盘后 |

### 1.1 各表用途

| 表名 | 用途 |
|---|---|
| trade_cal | 交易日历（所有交易日工具的底座，core/dates.py 依赖） |
| stock_basic | 股票基本信息（代码/名称/上市退市日/行业，全市场清单） |
| index_basic | 指数基本信息（指数代码/名称/类别） |
| index_classify | 申万行业分类（SW2021 体系，行业代码树） |
| index_member_all | 申万行业成分股（个股→行业归属 + 纳入/剔除日期） |
| fund_basic | 场内基金/ETF 清单（market=E，ETF 轮动标的池来源） |
| stock_daily | 个股日线行情（OHLC/成交量额，量价因子底座） |
| stock_weekly | 个股周线行情 |
| stock_monthly | 个股月线行情 |
| adj_factor | 个股复权因子（算真实累计收益） |
| stock_daily_basic | 个股每日指标（PE/PB/PS/换手率/市值，估值因子底座） |
| moneyflow | 个股资金流向（大中小单买卖，资金面因子） |
| stock_st | ST 股票标记（风险过滤） |
| suspend | 每日停复牌（停牌过滤） |
| sw_daily | 申万行业日线行情（行业动量/共振因子） |
| index_daily | 指数日线行情（宽基指数动量，ETF 轮动信号源） |
| index_daily_basic | 指数每日指标（指数估值/市值） |
| index_weight | 指数成分股权重（月度，ETF 持仓穿透 + 成分股因子诊断） |
| fund_daily | 场内基金日线行情（ETF 下单价/流动性） |
| fund_adj | 基金复权因子（ETF 真实收益） |
| fund_share | 基金份额/规模（ETF 流动性过滤，避开迷你 ETF） |
| income | 利润表（营收/利润/EPS，成长盈利因子） |
| balancesheet | 资产负债表（资产/负债/权益，质量杠杆因子） |
| cashflow | 现金流量表（经营/投资/筹资现金流，盈利质量因子） |
| disclosure_date | 财报披露计划（报告期实际/预约披露日，财务可见性判断） |
| dividend | 分红送股（除权除息，股息率因子 + 复权校验） |
| moneyflow_hsgt | 沪深港通资金流向（北向/南向，每日 1 行，私募择时核心） |
| margin | 融资融券交易汇总（每日 3 行 SSE/SZSE/BSE，杠杆情绪指标） |
| limit_list_d | 每日涨跌停/炸板（每只一行，数据始于 2020，不含 ST） |

---

## 2. 四类更新策略详解

> 核心原则：**取数维度 == 落库分区键(partition_col) == 删除维度** → 重跑幂等、不脏。

### 2.1 full_refresh（truncate）— 基础信息（6 个）

- **适用**：慢变维 / 全量小表（清单、日历、分类）。
- **取数**：一次拉全量（部分接口遍历参数：stock_basic 遍历 list_status=L/D；index_member_all 遍历 is_new=Y/N；index_classify src=SW2021）。
- **落库**：`TRUNCATE` 整表后 append。
- **幂等**：天然（每次全量重写）。无穿越（快照维度）。

### 2.2 by_trade_date（overwrite / trade_date）— 行情类（18 个）

- **适用**：按交易日切片的行情/快照数据。
- **取数**：枚举区间内每个交易日，逐日 `fetch_one_period(trade_date=...)`（每 10 日 sleep 0.5s 防限频）。
- **落库**：`DELETE WHERE trade_date IN (本批交易日)` + 批量 append。
- **幂等**：取数=删除维度=trade_date，重跑某日 = 删该日全部 + 重写该日。
- **代码**：所有子类继承 `TushareByTradeDateCalculator`，基类统一设 `write_mode=overwrite, partition_col=trade_date`。

### 2.3 by_period（overwrite / end_date）— 财务三表 + disclosure_date（4 个）

- **适用**：按报告期组织的财务数据（一个 end_date 一批，会被多次修订）。
- **取数**：财务三表 `fetch(period=报告期)`；disclosure_date `fetch(end_date=报告期)`。一次拉该报告期全市场所有版本。
- **增量**：起点取 `min(水位, today往前4期)`——常开机刷最近 4 期覆盖修订，久未开机从水位补全不漏断档；**回补**（传 start/end）：拉区间内所有季度末。
- **落库**：`DELETE WHERE end_date IN (本批报告期)` + 批量 append。
- **幂等**：取数=删除维度=end_date。重拉某报告期 = 删该期全部版本 + 重写该期全部版本。
- **防穿越**：保留所有 f_ann_date 版本，加工层 `WHERE f_ann_date <= snapshot_date` 选版（point-in-time）。
- **代码**：继承 `TushareByPeriodCalculator`（`pipeline/incremental/by_period.py`）。

### 2.4 by_ex_date（overwrite / ex_date）— 分红（1 个）

- **适用**：dividend（无 period 参数；只关心真实分红）。
- **取数**：按除权除息日逐交易日 `fetch(ex_date=...)`。ex_date 非空的"实施"记录才被命中，**自动过滤预案/股东大会通过阶段**。
- **增量**：起点取 `min(水位, today-365天)`——常开机回刷近 1 年覆盖分红修订/补录/推迟，久未开机从水位补全不漏；**回补**（传 start/end）：精确取该 ex_date 区间。
- **落库**：`DELETE WHERE ex_date IN (本批交易日)` + 批量 append。
- **幂等**：取数=删除维度=ex_date。
- **代码**：继承 `TushareByExDateCalculator`（`pipeline/incremental/by_ex_date.py`）。

---

## 3. overwrite 写入机制 + 去重护栏

`config/database.py:overwrite_by_partition(df, table, partition_col, primary_keys)`：

```sql
-- 同一事务内（engine.begin），失败回滚，不出现"删了没写"空窗
DELETE FROM {table} WHERE {partition_col} IN (:p0, :p1, ...);   -- 参数化 IN，防注入
-- 然后 df.to_sql(append)  批量 executemany，比逐行 upsert 快几十倍
```

**三大保证**：
1. **幂等**：删除维度 == 取数维度，重跑逐行一致。
2. **不脏**：删除粒度 ⊇ 写入粒度，旧数据全清后再写，不残留不交叉；同事务回滚防空窗。
3. **去重护栏**（落库前）：按主键去重，有 `update_flag` 留最大版本，否则留最后一条。
   - **关键**：发现重复主键时打 **WARNING 并显式列出被删的主键值**（最多 20 组）+ 行数变化，**不静默吞掉**。正常数据永不触发；触发 = 数据源异常，日志直接定位。

**为什么废弃 upsert**：旧 upsert 逐行 `ON DUPLICATE KEY UPDATE`，一天 5000 股 = 5000 次 DB 往返，回补一年 240 天 = 120 万次往返。overwrite 批量写入提速几十倍，且能清掉 tushare 撤回的脏记录（upsert 只增不删会残留）。

---

## 4. 关键接口的取数依据与实测论证

### 4.1 财务三表 income / balancesheet / cashflow

**官方文档**（[利润表 doc_id=33](https://tushare.pro/document/2?doc_id=33)，三表参数一致）：
- 输入参数：`ts_code`, `ann_date`, `f_ann_date`, `start_date`(公告日开始), `end_date`(公告日结束), **`period`(报告期，季度末)**, `report_type`, `comp_type`
- `income`(普通版)需 ts_code 必选；`income_vip`(5000 积分)参数一致但 ts_code 可空 → **可按 period 拉全市场**
- 关键日期语义：`ann_date`=预案公告日｜`f_ann_date`=实际公告日(真正可见日，防穿越用它)｜`end_date`=报告期(描述哪个季度，**不是日期区间**)

**为什么用 by_period 而非 by_ann_date**：财报会被多次修订，同报告期的修订版只能"重拉整个 period"才能完整覆盖。按 period 取 + 按 end_date overwrite，幂等且不漏修订版。

**MCP 实测**（`income`, ts_code=600000.SH, start_date=20180101, end_date=20180630）：
```
{ann_date:20180428, f_ann_date:20180428, end_date:20180331, report_type:1, n_income:1.4459e10}
{ann_date:20180428, f_ann_date:20180428, end_date:20171231, ...}
```
✅ 确认 start/end 过滤的是公告日；period 取数得该报告期全市场。

### 4.2 disclosure_date — 财报披露计划

**官方文档**（[财报披露计划 doc_id=162](https://tushare.pro/document/2?doc_id=162)）：
- 输入参数：`ts_code`, **`end_date`(报告期)**, `pre_date`, `ann_date`(最新披露公告日), `actual_date`
- **无 start_date/end_date 区间**；官方示例 `pro.disclosure_date(end_date='20181231')` = 按报告期拉全市场

**MCP 实测**（end_date=20231231）取到 5669 行，其中大量 `ann_date=null`：
```
{ts_code:600930.SH, ann_date:null, end_date:20231231, actual_date:20240429}  ← ann_date 为 null
{ts_code:603049.SH, ann_date:null, end_date:20231231, actual_date:20240425}  ← ann_date 为 null
```
**论证**：旧实现按 `ann_date` 逐日取数 → 这些 ann_date=null 记录**永远漏掉**（P0 数据正确性 bug）。改按 `end_date`(报告期)取，一次得全市场完整数据，**实测幂等验证**：period=20231231 跑两次，库内行数稳定 5669（不翻倍）。

### 4.3 dividend — 分红送股

**官方文档**（[分红送股 doc_id=103](https://tushare.pro/document/2?doc_id=103)）：
- 输入参数：`ts_code`, `ann_date`, `record_date`, `ex_date`, `imp_ann_date`（至少一个非空）；**无 period 参数**
- 关键字段：`div_proc`(实施进度：预案/股东大会通过/实施)，`ex_date`(除权除息日，仅"实施"阶段非空)

**MCP 实测**（ts_code=000001.SZ, end_date=20251231）同一报告期 **3 条**（div_proc 不同）：
```
{end_date:20251231, ann_date:20260321, div_proc:预案,        ex_date:null}
{end_date:20251231, ann_date:20260321, div_proc:股东大会通过, ex_date:null}
{end_date:20251231, ann_date:20260321, div_proc:实施,        ex_date:20260612, cash_div:0.36}
```
**论证**：① 只关心真实分红 → 用 `ex_date` 取数只命中"实施"记录（ex_date 非空），自动过滤预案/通过。② 旧实现按 ann_date 逐自然日（365 次/年）且漏 ann_date=null 早期记录。**MCP 实测**（ex_date=20240614）取到全市场当日除权股票，证明 ex_date 取数可行。

### 4.4 index_weight — 指数成分权重（月度）

**官方文档**（[指数成分和权重 doc_id=96](https://tushare.pro/document/2?doc_id=96)）：
- **月度数据**，"建议输入参数开始/结束日期分别输入当月第一天和最后一天"
- 直接传单个 trade_date 常查不到（数据落在月度调整日）

**MCP 实测**（index_code=399300.SZ, start_date=20180101, end_date=20180131）：返回沪深300全部成分 ~300 条，**trade_date 全部 = 20180131**（月末调整日）。红利低波系列（930955.CSI/H30269.CSI/000015.SH/399324.SZ）实测均收录成分权重。
**下游对齐**：`stock_daily_panel._join_index_weight` 用回看窗口 + `merge_asof(direction='backward')`，对每个交易日取 ≤ 当日的最近月末权重，日度对齐月度成分且不穿越。

### 4.5 fund_* — ETF/基金（4 个）

**MCP 实测**：
- `fund_basic`(market=E)：取到场内 ETF/LOF/REITs 清单
- `fund_daily`(trade_date=20240105)：全市场场内基金日线
- `fund_adj`(trade_date=20240105, 不传 ts_code)：全市场复权因子，**ETF 有真实折算**（如 159629=0.352）→ 证明复权必要
- `fund_share`(trade_date=20240105)：全市场份额，含 `.OF` 场外（接入层全量入库，过滤留策略层）

**ETF 持仓穿透风控**：用已有 `index_weight` 穿透（红利低波等指数实测均收录），**不需要 fund_portfolio**。

---

## 5. 主键设计的数据论证（核心）

> 用户最强调：**怎么用数据证明主键无重复合理**。下面是实测论证，非推断。

### 5.1 财务三表 PK = 5 列：`ts_code+end_date+ann_date+f_ann_date+update_flag`

**实测样本**：income_vip 拉 **2015-2024 连续 40 期**（每年 Q1/Q2/Q3/Q4），共 **254166 行 / 6327 只股票**。

**各 PK 候选的重复行数实测**：

| PK 候选 | 重复行数 | 结论 |
|---|---|---|
| **5 列** ts+end+ann+f_ann+update_flag | **0** | ✅ 唯一（采用） |
| 7 列 +report_type+comp_type | 0 | ✅ 唯一（超集，更保险但本数据无差异） |
| 6 列 去 update_flag | 61410 | ❌ |
| 去 ann_date（只留 f_ann） | **14** | ❌ |
| 去 f_ann_date（只留 ann） | **273** | ❌ |
| 最小 4 列 ts+end+rt+ct | 62698 | ❌ |

**论证结论**：
1. **5 列联合 0 重复** → 足以唯一标识每行。
2. **去掉 f_ann_date 产生 273 行重复** → 同报告期有多个不同 f_ann_date（修订版，不同时点发布），f_ann_date 不可省（point-in-time 必需）。
3. **去掉 ann_date 产生 14 行重复** → ann_date 也不可省（跨期/补充更正时 ann_date 与 f_ann_date 不一致）。
4. **去掉 update_flag 产生 61410 行重复** → 同 (ts,end,ann,f_ann) 存在 update_flag=0/1 两版，必须保留。

**真实多版本样本**（同 ts_code+end_date+report_type+comp_type 多个 f_ann_date）：
```
[000003.SZ 2020年报] (ann:20210501, f_ann:20210501, flag:0)  +  (ann:20210501, f_ann:20230429, flag:1)
                                          ↑ 原始版 20210501          ↑ 2 年后才发的更正版，f_ann 不同
```
若 PK 不含 f_ann_date，原始版与更正版会被折叠成一条最新版 → **穿越**（2022 年回测看到 2023 年才知道的数据）。保留多版本 + 加工层 snapshot 选版才能 point-in-time 正确。

**约束**：vip 默认只返回 `report_type=1`(合并报表)，本数据 report_type 恒为 1，故 5 列已足。**若未来主动拉其他 report_type（单季 2 / 调整 4 / 母公司 6 等），必须把 report_type 加进 PK**，否则跨口径报表会主键冲突丢数据。

### 5.2 disclosure_date PK = `ts_code+end_date`

**MCP 实测**（ts_code=000001.SZ 全历史 1990-2026）：每个 end_date **唯一一条**，含有 modify_date 的记录也只有一条。→ `ts_code+end_date` 唯一，`modify_date` 是字段不是维度。

### 5.3 dividend PK = `ts_code+end_date+ex_date+div_proc+update_flag`

**MCP 实测**：同 (ts_code,end_date) 有多条 div_proc（预案/股东大会通过/实施，见 §4.3），故 div_proc 必须进 PK；update_flag 区分修订版。

**为什么主键用 ex_date 不用 ann_date（2026-06-24 修正）**：原主键含 `ann_date`，回补时报 `(1048, "Column 'ann_date' cannot be null")`。MCP 拉 000001.SZ 全历史分红实证：`ann_date=null` 出现在远古实施记录（end_date=1990/1991/1992/1993/1994/1996 等）及全市场个别记录上，而 `ann_date` 作主键必须 NOT NULL → 冲突。本接入层 by_ex_date 只入库 ex_date 非空的"实施"记录，**ex_date 在实施记录里必非空、且是分红的核心维度**，故用 ex_date 替代 ann_date 进 PK；`ann_date` 降为普通列保留真实值（含 null）。同时 ex_date 已是 partition_col，主键含它逻辑自洽。

### 5.4 行情类 PK = `ts_code+trade_date`

按交易日 × 标的天然唯一，无修订版本问题。

---

## 6. 官方文档索引

| 接口 | 文档 |
|---|---|
| 利润表/资产负债表/现金流（参数一致） | [doc_id=33](https://tushare.pro/document/2?doc_id=33) |
| 分红送股 dividend | [doc_id=103](https://tushare.pro/document/2?doc_id=103) |
| 财报披露计划 disclosure_date | [doc_id=162](https://tushare.pro/document/2?doc_id=162) |
| 指数成分和权重 index_weight | [doc_id=96](https://tushare.pro/document/2?doc_id=96) |
| 接口权限与更新频率表 | [doc_id=108](https://tushare.pro/document/1?doc_id=108) |

> 其余接口（daily/fund_*/suspend_d 等）以 MCP 实测验证为准（开发期用 `mcp_tushareMcp` 探查字段/参数，不人肉翻文档）。

---

## 7. 验证方法说明

- **官方文档**：核对 tushare.pro 文档「输入参数」表（日期参数名 + 语义）+「更新频率」。
- **MCP 实测**：用历史日期实际调用 `mcp_tushareMcp`，确认①该参数能返回数据②返回日期列语义与文档一致③是否有 null 导致漏数。
- **主键论证**：从 tushare 拉真实数据（绕过 DB 避免现有 PK 污染），pandas `duplicated(subset=PK)` 统计各候选重复数。财务表用 2015-2024 连续 40 期 25 万行压力测试。
- **幂等验证**：disclosure_date period=20231231 真实写库跑两次，库内行数稳定 5669（不翻倍）= overwrite 幂等成立。

---

## 附：未做的优化项（不影响正确性，仅省 API）

| 项 | 状态 | 说明 |
|---|---|---|
| index_weight 改按月调用 | ✅ 已做 | 覆盖 get_data 只对区间内每月最后交易日取一次，去重 13200 行 + 省 95% API |
| index_daily/dailybasic 改区间取数 | ✅ 已做 | 覆盖 get_data 遍历指数×区间（每指数一次拿整段），替代逐交易日×逐指数单条取，省 ~99% API |
| weekly/monthly 只在周末/月末调 | ⏳ | 非周末/月末返回空跳过，结果正确仅浪费 API |
| stock_daily_panel 指数回看 40→70 天 | ⏳ | 加工层，防早期月度数据缺月，待评估 |
