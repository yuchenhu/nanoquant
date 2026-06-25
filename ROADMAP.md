# nanoquant ROADMAP（缺口清单 + 补全计划）

> 现状：接入层 + 调度补数已闭环（约占整个策略闭环 30%）。
> 本文档记录从「有数据」到「能跑出可信结论 / 能实盘」还缺的部分，按优先级分阶段。
> 客观评估，逐项勾选。

---

## 阶段 0 · 当前已完成 ✅

- [x] 29 个 tushare 接口接入（含 ETF：fund_basic/daily/adj/share）
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

### 0.5a panel_index_membership_monthly（指数成分归属 · 清洗 index_weight）
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
- [ ] **下游消费**：market_sentiment_monthly 的成分分布列、stock_daily_panel 的 is_xxx 重构改读此表

### 0.5b panel_market_sentiment_monthly（市场情绪底表 · regime 输入）
从旧三维护(cap/index/industry)重建为 **index 5 指数 + all 全A** 两维，6 支柱 32 指标。
旧文件已覆盖重写（`data/panel/market_sentiment_monthly.py`）。

已钉死：
- [x] **schema**：32 列，按趋势/广度/量能/资金/估值/波动 6 支柱分组。关键原则：衍生必带原始分量（ma_bull_align 带 ma60/120/250；pe_pct_5y 带 pe_ttm_median；amount_pct_1y 带 idx_amount）
- [x] **维度**：`dimension_type='all'`(全A) + `'index'`（上证50/沪深300/中证500/中证1000/中证2000）
- [x] **全A 独有列**（仅 `all` 行有值，`index` 行为 NULL）：`north_money`(北向) / `margin_balance`(两融) / `limit_up_count`(涨停家数)
- [x] 已注册到 `data/panel/__init__.py` PANEL_CALCULATORS
- [x] **6 支柱完整列清单**（每列标 RAW/DER + 上游来源）：
  ① 趋势：idx_close / ma60 / ma120 / ma250 / ma_bull_align / idx_ret_3m / idx_ret_6m
  ② 广度：up_count / down_count / big_up_count(>10%) / big_down_count(<-10%) / profit_ratio / pct_above_ma250 / limit_up_count(仅all)
  ③ 量能：idx_amount / amount_pct_1y
  ④ 资金(仅all)：north_money(moneyflow_hsgt) / margin_balance(margin rzrqye) / main_net_inflow_ratio(moneyflow)
  ⑤ 估值：pe_ttm_median / pb_median / pe_pct_5y / pb_pct_5y / erp(1/PE-10Y国债·待手工表)
  ⑥ 波动/风险：idx_volatility_60 / avg_correlation / max_drawdown_1y
- [x] **ERP 国债数据**：`yc_cb`(中债国债收益率)无积分权限 → 需手工维护月度国债收益率小表，或后续发现可免费后接入
- [x] **limit_list_d 数据始于 2020**，2010-2019 回补该列为空——不是 bug
- [x] **日期格式对齐**：加工层输出 `trade_date` 统一用 `yyyy-mm-dd`（与接入层 DATE 类型对齐，不在加工层做 YYYYMMDD↔DATE 转换）
- [ ] **实现 TODO**：get_data/process_data 返回空，待面板数据就绪后回填
- [ ] **需注册到 schedule_compute.json**
- [ ] **上游依赖**：index_daily + daily/daily_basic + moneyflow_hsgt/margin/moneyflow/limit_list_d
- [ ] **下游**：factor_regime_features → factor_regime_score → panel_market_regime（regime 完整链路）

### 0.5c 市场状态(regime)方法论（已讨论定稿，待开工）
单开条目记录定案决策（见研究笔记 §8.4）：
- [x] 全局 + 风格二维 regime，全A + 50/300/500/1000/2000 各一行
- [x] 3 态(牛/震荡/熊) + 滞回 + 最小持续期
- [x] 先验权重透明打分，不上 HMM
- [x] 指标设计按私募常用精简原则

---

## 阶段 1 · 致命缺口（不补会出假结论）🔴

### 1.0 panel 指数成分表（→ 见阶段 0.5a，schema 已完成）
schema 已落为 `panel_index_membership_monthly`（每月、每个 canonical 指数的成分构成 + weight）。
- [x] **schema + 清洗逻辑设计完成**（见 0.5a），接入层回补就绪后实现即可
- [ ] 实现 get_data/process_data 回填数据

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
