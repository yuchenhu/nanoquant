# Spec: 指数因子库（一期 9 因子）

> 创建：2026-07-23 | 状态：spec review | 窗口：统一 20d(1M) + 60d(3M)
> 池子：宽基 + 风格 + 申万行业（`index_daily` + `sw_daily` UNION ALL）

---

## 通用约定

- **数据源**：`index_daily`（宽基/风格）UNION ALL `sw_daily`（申万行业），取 `close` 列（部分因子需要 `open`/`vol`/`amount`）
- **主键**：`index_code + trade_date`（行业轮动速度为池子级因子，单主键 `trade_date`）
- **write_mode**：`overwrite`，`partition_col`：`trade_date`
- **组件拆分原则**：每个中间产物都暴露为独立列，方便探查组合方式
- **Null 处理**：计算不出 → None（MySQL NULL），不做 fillna
- **更新策略**：增量更新时 `start_date` 前推 `MAX_WINDOW + 10` 个交易日（`get_data()` 内部扩展）

---

## 因子 1：趋势一致性动量 (Momentum × R²)

**物理意义**：区分"稳步攀升"和"陡拉"，R² 高 → 趋势持续性更强。纯动量只度量"涨了多少"，不关心"怎么涨的"。一条陡拉涨停的路径和一条稳步攀升的路径，在纯动量下得分相同，但前者后续反转概率高，后者趋势持续性高。R² 区分了这两种路径。

**表**：`factor_momentum_r2`

**公式**：
```
ret_Nd = ln(P_t / P_{t-N})                           // N日对数收益率
P_i = α + β × i + ε_i,  i = 1..N                    // 对过去N日收盘价做OLS线性回归
R² = 1 - SS_res / SS_tot                             // 回归拟合优度
beta = (N×Σ(i×P_i) - Σi×ΣP_i) / (N×Σ(i²) - (Σi)²)  // OLS斜率（趋势强度）
score = sign(ret_Nd) × |ret_Nd| × R²                 // 综合得分
```

**N 取值**：20 和 60 两个窗口。每个窗口独立计算 ret、R²、beta、score 四列。

**输出列**：

| 列名 | 类型 | 物理意义 |
|---|---|---|
| `index_code` | string | 指数代码 |
| `trade_date` | date | 观察日 |
| `ret_20d` | float | 20日对数收益率 ln(P_t / P_{t-20}) |
| `r2_20d` | float | 20日 OLS 回归 R² [0, 1] |
| `beta_20d` | float | 20日 OLS 斜率（日均涨跌幅，非年化） |
| `score_20d` | float | sign(ret_20d) × |ret_20d| × r2_20d |
| `ret_60d` | float | 60日对数收益率 |
| `r2_60d` | float | 60日 OLS 回归 R² |
| `beta_60d` | float | 60日 OLS 斜率 |
| `score_60d` | float | sign(ret_60d) × |ret_60d| × r2_60d |

**边界**：不足 N 天 → 全部 NULL；R²=0 → score=0（被 R² 乘掉）；R²≈1 → |score|≈|ret|

---

## 因子 2：动量期限结构 (Term Structure)

**物理意义**：短期 vs 长期动量的加速/减速状态。正 = 加速（近期强于远期），负 = 减速。`tsmom_ratio` 回答了"短期动量是长期的几倍"，截面可比。

**表**：`factor_momentum_term`

**公式**：
```
ret_20d = ln(P_t / P_{t-20})                         // 20日对数收益率
ret_60d = ln(P_t / P_{t-60})                         // 60日对数收益率
tsmom_diff = ret_20d - ret_60d                        // 绝对加速度
tsmom_ratio = ret_20d / ret_60d                       // 相对加速度（截面可比）
trend_ok = ret_20d>0 AND ret_60d>0                    // 方向过滤
score = tsmom_ratio × trend_ok                        // 全部正收益才给分，否则归零
```

**输出列**：

| 列名 | 类型 | 物理意义 |
|---|---|---|
| `ret_20d` | float | 20日对数收益率 |
| `ret_60d` | float | 60日对数收益率 |
| `tsmom_diff` | float | `ret_20d - ret_60d`，绝对加速度 |
| `tsmom_ratio` | float | `ret_20d / ret_60d`，相对加速度 |
| `trend_ok` | int | 两个窗口全部正收益=1，否则=0 |
| `score` | float | `tsmom_ratio × trend_ok` |

**数据源**：`index_daily.close` + `sw_daily.close`

**边界**：不足 60 天 → 全部 NULL；ret_60d=0 时 tsmom_ratio=NULL

---

## 因子 3：隔夜 vs 日内收益分解 (Overnight / Intraday)

**物理意义**：隔夜收益反映机构定价（开盘前集合竞价），日内收益反映散户博弈。据此判断资金结构。

**表**：`factor_overnight_intraday`

**公式**：
```
overnight_ret = ln(open_t / close_{t-1})            // 隔夜对数收益
intraday_ret = ln(close_t / open_t)                 // 日内对数收益
on_mom = rolling_20d_sum(overnight_ret)             // 20日累计隔夜动量
id_mom = rolling_20d_sum(intraday_ret)               // 20日累计日内动量

四象限打分：
  Q1: on_mom>0 AND id_mom>0  → score = +1  (机构+散户同向买)
  Q2: on_mom>0 AND id_mom<0  → score = +2  (机构在买，散户在卖，最bullish)
  Q3: on_mom<0 AND id_mom>0  → score = -2  (机构在卖，散户在买，最bearish)
  Q4: on_mom<0 AND id_mom<0  → score = -1  (机构+散户同向卖)
```

**输出列**：

| 列名 | 类型 | 物理意义 |
|---|---|---|
| `overnight_ret` | float | 当日隔夜对数收益 |
| `intraday_ret` | float | 当日日内对数收益 |
| `on_mom` | float | 20日累计隔夜收益（机构累计资金流向） |
| `id_mom` | float | 20日累计日内收益（散户累计资金流向） |
| `score` | int | 四象限打分（-2, -1, +1, +2） |

**数据源**：`index_daily.open + close` + `sw_daily.open + close`

**边界**：缺 `open` 或 `prev_close` → overnight_ret/intraday_ret 为 NULL → on_mom/id_mom 为 NULL

---

## 因子 4：收益/波动比 (Return / Volatility)

**物理意义**：涨幅调整波动率，波动大的指数即使涨了也容易被震出去。简化版 Sharpe（不减去无风险利率，指数之间 r_f 相同，抵消）。

**表**：`factor_return_vol`

**公式**：
```
ret_20d = ln(P_t / P_{t-20})
std_20d = rolling_std(ret_1d, 20) × sqrt(252)       // 年化波动率，√252 常数量纲不影响截面排名
ret_vol_ratio = ret_20d / std_20d                    // 收益/波动比
```

**输出列**：

| 列名 | 类型 | 物理意义 |
|---|---|---|
| `ret_20d` | float | 20日对数收益率 |
| `ret_1d` | float | 当日对数收益率（用于算 std） |
| `std_20d` | float | 20日年化波动率 |
| `ret_vol_ratio` | float | `ret_20d / std_20d` |

**数据源**：`index_daily.close` + `sw_daily.close`

**边界**：std_20d=0（价格不变）→ ret_vol_ratio=NULL；不足 20 天 → 全部 NULL

---

## 因子 5：非对称 Beta (Asymmetric Beta，精简版)

**物理意义**：市场对不同指数在上涨日和下跌日的弹性不同。β_down_change 上升 = 市场正在重新定价这个板块的下行风险。

**表**：`factor_asym_beta`

**公式**：
```
60日滚动回归：ret_index = α + β_mkt × ret_mkt + ε
  - β_up：仅用 ret_mkt>0 的交易日
  - β_down：仅用 ret_mkt<0 的交易日
  - β_down_change = β_down(t) - β_down(t-20)：20日下行beta变化
  - capture_ratio = β_up / β_down：涨跌弹性比
```

**大盘基准**：沪深300（000300.SH）

**输出列**：

| 列名 | 类型 | 物理意义 |
|---|---|---|
| `beta_up` | float | 上涨日beta（大盘涨时指数弹性） |
| `beta_down` | float | 下跌日beta（大盘跌时指数弹性） |
| `beta_down_change` | float | `β_down(t) - β_down(t-20)`，下行敏感度变化 |
| `capture_ratio` | float | `β_up / β_down`，>1 = 涨时弹性更大 |
| `r2` | float | 全样本回归 R² |

**数据源**：`index_daily.close` + `sw_daily.close` + 沪深300

**边界**：β_down=0 时 capture_ratio=NULL；上涨/下跌日不足 10 个 → 对应 beta=NULL

---

## 因子 6：残差动量 α (Residual Momentum)

**物理意义**：剥离大盘 beta 后的纯 idiosyncratic 动量。大盘涨水涨船高的指数，残差动量低。

**表**：`factor_residual_alpha`

**公式**：
```
60日滚动回归：ret_index = α + β × ret_mkt + ε
cum_excess = Sum(ε, 60)                              // 60日累计残差
z_residual = (cum_excess - cross_sectional_mean) / cross_sectional_std  // 截面标准化
```

**大盘基准**：沪深300（000300.SH）

**输出列**：

| 列名 | 类型 | 物理意义 |
|---|---|---|
| `beta` | float | 60日滚动回归 β |
| `alpha` | float | 60日滚动回归 α（截距项） |
| `cum_excess` | float | 60日累计残差（超额收益） |
| `z_residual` | float | 截面标准化后的残差动量 |

**数据源**：`index_daily.close` + `sw_daily.close` + 沪深300

**边界**：不足 60 天 → 全部 NULL；截面 <= 2 个指数 → z_residual=NULL

---

## 因子 7：行业/风格轮动速度 (Rotation Speed)

**物理意义**：池子级因子，度量当前市场是否在快速轮动。轮动速度快 → 动量信号可靠性下降 → 动量得分打折。

**表**：`factor_rotation_speed`（池子级因子，主键仅 `trade_date`）

**公式**：
```
rotation_speed = mean(|rank_t(ret_20d) - rank_{t-1}(ret_20d)|)   // 20日排名变化的平均绝对差
momentum_discount = 1 - rotation_speed / max_rotation_60d       // 归一化到 [0,1]，轮动快→折扣大
```

**输出列**：

| 列名 | 类型 | 物理意义 |
|---|---|---|
| `rotation_speed` | float | 20日收益排名平均绝对变化 |
| `momentum_discount` | float | 动量可靠性折扣因子 [0,1]，0=完全不可靠 |

**数据源**：`index_daily.close` + `sw_daily.close`（全池子）

**边界**：不足 2 个指数 → NULL；max_rotation_60d=0 → momentum_discount=1

---

## 因子 8：WAP 偏离 × 量比（简化版）

**物理意义**：收盘价偏离成交均价且放量 = 资金在尾盘有方向性动作。指数层面区分度弱于个股，但作为量价确认仍有价值。

**表**：`factor_wap_volume`

**公式**：
```
WAP = amount / vol                                  // 日内成交均价（万元/手）
WAP_deviation = (close - WAP) / WAP                 // 收盘价偏离度
vol_ratio = vol / rolling_mean(vol, 20)             // 量比
score = WAP_deviation × min(vol_ratio, 2.0)         // 量比截断在 2.0 防离群值
```

**输出列**：

| 列名 | 类型 | 物理意义 |
|---|---|---|
| `WAP` | float | 日内成交均价 |
| `WAP_deviation` | float | 收盘价偏离 WAP 的比例 |
| `vol_ratio` | float | 当日量 / 20日均量 |
| `score` | float | `WAP_deviation × min(vol_ratio, 2.0)` |

**数据源**：`index_daily.close + vol + amount` + `sw_daily.close + vol + amount`

**边界**：vol=0 → WAP=NULL → WAP_deviation=NULL；vol_ma_20d=0 → vol_ratio=NULL

---

## 因子 9：最大回撤 (Max Drawdown)

**物理意义**：60 日内峰谷最大跌幅，纯风控诊断指标。不用于排序选指数，用于过滤/减仓。

**表**：`factor_max_drawdown`

**公式**：
```
max_dd_60d = max( (P_peak - P_t) / P_peak )  for t in [t-59, t]
其中 P_peak = rolling_max(close, 60)
```

**输出列**：

| 列名 | 类型 | 物理意义 |
|---|---|---|
| `max_dd_60d` | float | 60 日内最大回撤（正值，如 0.15 = 15%） |

**数据源**：`index_daily.close` + `sw_daily.close`

**边界**：不足 60 天 → NULL

---

## 因子全貌总览

| # | 表名 | 列数 | 数据源 | 窗口 | 复杂度 |
|---|---|---|---|---|---|
| 1 | `factor_momentum_r2` | 8 | close | 20/60 | 中（OLS手算） |
| 2 | `factor_momentum_term` | 6 | close | 20/60 | 低 |
| 3 | `factor_overnight_intraday` | 5 | open+close | 20 | 低 |
| 4 | `factor_return_vol` | 4 | close | 20 | 低 |
| 5 | `factor_asym_beta` | 5 | close + 沪深300 | 60 | 中（滚动回归） |
| 6 | `factor_residual_alpha` | 4 | close + 沪深300 | 60 | 中（滚动回归） |
| 7 | `factor_rotation_speed` | 2 | close（全池子） | 20/60 | 低 |
| 8 | `factor_wap_volume` | 4 | close+vol+amount | 20 | 低 |
| 9 | `factor_max_drawdown` | 1 | close | 60 | 低 |

**共享计算**：因子 5 和因子 6 共用 60 日滚动回归，实现时合并为一个数据查询，两个 process 各取所需。

## 验收标准（通用）

- 每个因子 mock 验证：3-5 个指数 × 窗口+10 天模拟数据，手算验证公式
- 单日回补：跑 1 个 trade_date，看列对齐和行数
- 区间回补：跑 1 个月，检查值域（R²∈[0,1]、max_dd∈[0,1]、score 符号合理等）
- 无 NaN 落入 int 列（仅因子 2 的 `trend_ok` 和因子 3 的 `score` 是 int，需确保无 NaN）
- 增量更新：前推窗口后，区间边界日期值与非增量回补一致

---

# Part 2: 市场情绪 & Regime 体系

> 5 张表：月频底表 → 日频信号 → 告警 → regime 标签 → 周频拥挤度
> 数据流：`daily_signals → alarm → regime_label`，`monthly_sentiment` 为历史回测提供长周期验证

---

## 私募批判（写在前面）

### 1. 月频底表 vs 周频调仓的不匹配

`market_sentiment_monthly` 月频出数，但 ETF 轮动是周频。这意味着 regime 判断的响应延迟至少 2-4 周。一笔"月频牛市"的信号，在日频可能已经跌了 15 天了。

**建议**：月频表保留（回测长周期），但 regime 的实时判断走 `market_sentiment_daily` + `crash_alarm_daily`。不要在月频表上做 regime 标签。

### 2. 36 列太多了

现有月频表 5 维度 × 36 列。roadmap 自身分析发现 `cross_sectional_vol` 和 `downside_vol_ratio` 对 regime 判断无增量价值。长格式（dimension_type + dimension_value）让 SQL 窗口函数更难写。

**建议**：二期瘦身，每个维度保留 3-5 个最高信息量的指标。当前一期先不改（已落库的数据不能丢）。

### 3. Alarm 和 Regime 的分工

Alarm 和 Regime 容易混淆。正确的分工：
- **Alarm**：硬护栏。跌停=退出，涨跌停比>5=警惕。是"无论什么 regime 都该警惕"的规则。
- **Regime**：指针。告诉你在牛市/熊市/震荡/混沌中，轮动策略应该怎么做。
- **不交叉**：Alarm 不应该影响 regime 分类，regime 也不应该否定 alarm。

### 4. 叙事拥挤度是 alpha 来源

`narrative_crowding_weekly` 是这套设计里最创新的部分。HHI + PE分位数 + Gini + 收益偏离度 → 检测"所有人都在讨论同一个板块"的拥挤状态。A 股叙事驱动特征明显，这比纯技术指标更有信息量。

---

## 表 1：market_sentiment_monthly（月频底表）

**状态**：已实现，已回补。只在此记录 spec。

**表**：`panel_market_sentiment_monthly`
**主键**：`trade_date + dimension_type`（长格式，每行一个维度）
**write_mode**：`overwrite`，`partition_col`：`end_date`
**依赖**：`stock_daily_panel`、`index_daily`、`moneyflow_hsgt`、`margin`、`limit_list_d`

**五个维度**：

| 维度 | 指标数 | 代表性指标 |
|---|---|---|
| 价 | 8 | index_close, pct_chg_20d, pct_chg_60d, above_ma_20d_pct, new_high_20d_pct, ret_skew_20d, bullish_engulf_ratio, rally_concentration |
| 量 | 8 | total_turnover, turnover_ma_ratio, vol_ratio_20d, amount_ratio_20d, up_vol_ratio, margin_balance, margin_balance_ma_ratio, margin_buy_ratio |
| 波 | 5 | index_vol_20d, average_volatility, vol_of_vol, cross_sectional_vol, downside_vol_ratio |
| 估值 | 6 | pe_ttm, pe_percentile_3y, pb, pb_percentile_3y, earnings_yield, erp_5y |
| 资金 | 9 | north_money, north_money_ma_ratio, north_buy_amount, south_money, south_money_ma_ratio, limit_up_count, limit_down_count, limit_up_down_ratio, money_flow_ratio |

**输出格式**：长格式（dimension_type, dimension_value），共 36 行/月。

**专业评估**：五维度设计合理，覆盖全面。但月频太慢 + 长格式难用。二期考虑：① 日频化关键指标 ② 宽格式（每个指标一列）而非长格式。

---

## 表 2：market_sentiment_daily（日频信号）

**状态**：占位（`data/panel/market_sentiment_daily.py` 仅 stub），需实现。
**表**：`panel_market_sentiment_daily`
**主键**：`trade_date`
**write_mode**：`overwrite`，`partition_col`：`trade_date`
**依赖**：`stock_daily_panel`、`index_daily`、`moneyflow_hsgt`、`margin`、`limit_list_d`

**17 个日频信号**（从月频 36 个中筛选有日频价值的）：

| 维度 | 列名 | 物理意义 |
|---|---|---|
| 价 | `index_close` | 沪深300收盘价 |
| 价 | `index_pct_chg` | 当日涨跌幅 |
| 价 | `above_ma_20d_pct` | 收盘价在20日均线之上的股票占比 |
| 价 | `new_high_20d_pct` | 创20日新高股票占比 |
| 量 | `total_turnover` | 全市场成交额（亿） |
| 量 | `turnover_ma_ratio` | 成交额/20日均值 |
| 量 | `up_vol_ratio` | 上涨股票成交量占比 |
| 量 | `margin_balance` | 融资余额（亿） |
| 量 | `margin_buy_ratio` | 融资买入额/总成交额 |
| 波 | `index_vol_20d` | 20日波动率 |
| 波 | `average_volatility` | 全市场平均波动率 |
| 估值 | `pe_ttm` | 沪深300 PE(TTM) |
| 估值 | `pe_percentile_3y` | PE 3年分位数 |
| 估值 | `erp_5y` | 股权风险溢价 |
| 资金 | `north_money` | 北向资金净流入（亿） |
| 资金 | `limit_up_count` | 涨停家数 |
| 资金 | `limit_down_count` | 跌停家数 |

**更新频率**：每个交易日 15:30 后跑。

---

## 表 3：crash_alarm_daily（崩溃告警）

**状态**：未实现，需新建。
**表**：`crash_alarm_daily`
**主键**：`trade_date`
**write_mode**：`overwrite`，`partition_col`：`trade_date`
**依赖**：`market_sentiment_daily`、`limit_list_d`、`stock_daily`

**7 个基础告警（来自 roadmap）**：

| 列名 | 类型 | 告警逻辑 | 物理意义 |
|---|---|---|---|
| `limit_down_ratio_hi` | int | 跌停数/涨停数 > 5 | 极端恐慌 |
| `limit_down_count_hi` | int | 跌停家数 > 100 | 流动性危机 |
| `turnover_spike` | int | 成交额/20日均值 > 2.5 | 天量换手（顶/底信号） |
| `vol_spike` | int | 20日波动率 > 3年95分位 | 波动率飙升 |
| `breadth_crash` | int | 上涨占比 < 10% | 普跌 |
| `margin_call` | int | 融资余额周降幅 > 5% | 去杠杆 |
| `north_flight` | int | 北向单日净流出 > 100亿 | 外资撤离 |

**3 个新增告警（来自 择时指标探查）**：

| 列名 | 类型 | 告警逻辑 | 物理意义 |
|---|---|---|---|
| `drawdown_20pct` | int | 沪深300距60日高点回撤 > 20% | 技术性熊市 |
| `consecutive_down` | int | 连续下跌 > 5 个交易日 | 持续踩踏 |
| `pe_below_10pct` | int | PE 3年分位数 < 10% | 极度低估（不是卖点，是加仓信号） |

**输出**：`alarm_count` = 10 个告警中触发个数；`panic_level` = 0(无) / 1(警惕) / 2(危险) / 3(崩溃)，基于 alarm_count 阈值。

**与 regime 的关系**：告警是硬护栏，不参与 regime 分类。regime 怎么说，告警都是独立的"无论什么 regime 都该知道"的规则。

---

## 表 4：market_regime_monthly（regime 标签）

**状态**：未实现，需新建。
**表**：`market_regime_monthly`
**主键**：`trade_date`
**write_mode**：`overwrite`，`partition_col`：`end_date`
**依赖**：`market_sentiment_monthly`、`crash_alarm_daily`（取月频聚合）

**regime 分类逻辑**（来自 roadmap）：

```
方向轴（direction）：价格动量 > 价格分位 > 资金方向
  → UP / DOWN / FLAT

广度轴（breadth）：上涨占比 > 新高占比 > 成交集中度
  → BROAD / NARROW

交叉 → 4 种 regime：
  UP + BROAD   = BULL       (牛市，轮动正常做)
  UP + NARROW  = CHAOS      (混沌，权重股独拉，轮动风险大)
  DOWN + BROAD = BEAR       (熊市，普跌，轮动暂停)
  DOWN + NARROW = SIDEWAYS  (震荡，局部机会)
```

**三重 override**（对边界情况的修正）：

1. **估值 override**：PE < 10%分位 + ERp > 5年90分位 → 强制升一级（BEAR→SIDEWAYS）
2. **波动 override**：vol > 95分位 → 强制 CHAOS（无论其他指标说什么）
3. **情绪 override**：alarm_count ≥ 3 → 强制降一级（BULL→CHAOS）

**输出列**：

| 列名 | 类型 | 物理意义 |
|---|---|---|
| `direction_score` | float | 方向轴连续得分 |
| `direction_label` | string | 方向轴离散标签（UP/DOWN/FLAT） |
| `breadth_score` | float | 广度轴连续得分 |
| `breadth_label` | string | 广度轴离散标签（BROAD/NARROW） |
| `regime` | string | 最终 regime 标签（BULL/BEAR/SIDEWAYS/CHAOS） |
| `override_reason` | string | 触发 override 的原因（NULL=无） |

**专业评估**：方向×广度的 2×2 框架简洁有力。但三重 override 可能导致边界震荡——比如 PE 刚好在 10.1% 分位，前一天 BEAR 后一天 SIDEWAYS。建议加 3-5% 滞回带（hysteresis band）。

---

## 表 5：narrative_crowding_weekly（叙事拥挤度）

**状态**：未实现，需新建。Phase 5.4 规划。
**表**：`narrative_crowding_weekly`
**主键**：`trade_date`
**write_mode**：`overwrite`，`partition_col`：`end_date`（周频）
**依赖**：`sw_daily`、`index_daily_basic`、`stock_daily_panel`

**公式**（来自 roadmap + wiki）：
```
HHI_weekly = mean(HHI(sector_daily_amount), 5d)           // 31个申万行业周均成交额HHI
pe_percentile_cap = max(pe_percentile_3y across sectors)   // 最贵行业的PE分位数
gini_weekly = gini(sector_weekly_ret)                      // 行业周收益基尼系数
ret_dispersion = std(sector_ret_20d)                       // 行业20日收益离散度

crowding_score = 0.3×HHI_norm + 0.25×pe_norm + 0.25×gini_norm + 0.2×disp_norm
narrative_multiplier = step(crowding_score):
  0.0-0.3 → 1.0  (正常)
  0.3-0.5 → 0.8  (轻度拥挤，动量打8折)
  0.5-0.7 → 0.5  (中度拥挤)
  0.7-1.0 → 0.0  (极度拥挤，动量清零)
```

**输出列**：

| 列名 | 类型 | 物理意义 |
|---|---|---|
| `hhi_weekly` | float | 行业成交额周均 HHI |
| `pe_percentile_cap` | float | 最贵行业 3年PE分位数 |
| `gini_weekly` | float | 行业周收益基尼系数 |
| `ret_dispersion` | float | 行业 20日收益离散度 |
| `crowding_score` | float | 综合拥挤度 [0,1] |
| `narrative_multiplier` | float | 动量折扣因子 [0,1] |

**用法**：`narrative_multiplier` 乘到因子 7（轮动速度）的 `momentum_discount` 上，形成双层折扣：轮动快 → 动量打折，拥挤 → 再打折。

**专业评估**：这是整套设计里最创新的部分。A 股叙事驱动特征明显（"XX板块概念"），HHI 和 Gini 能捕捉到"所有人都在讨论同一个板块"的状态。但 0.3/0.25/0.25/0.2 的权重需要回测验证，不能拍脑袋。

---

## 市场情绪表总览

| 表 | 频率 | 状态 | 依赖 | 用途 |
|---|---|---|---|---|
| `market_sentiment_monthly` | 月 | 已实现 | 5 张表 | 回测长周期验证 |
| `market_sentiment_daily` | 日 | 占位 | 同上 | regime 实时判断 |
| `crash_alarm_daily` | 日 | 未实现 | daily + limit_list | 硬护栏，独立告警 |
| `market_regime_monthly` | 月 | 未实现 | monthly + alarm | 轮动策略开关 |
| `narrative_crowding_weekly` | 周 | 未实现 | sw_daily + index_basic | 动量折扣因子 |