# nanoquant 开发指南（API 速查 + 踩坑集）

> 写代码时读。包含：模块 API 速查、脚本入口、write\_mode/增量策略、新 Calculator 模板、策略层 API，以及所有踩过的坑。
> 架构与约定见 [CLAUDE.md](CLAUDE.md)，进度见 [ROADMAP.md](ROADMAP.md)。

***

## 0. 日常 CLI & 协作速查（每次写代码前扫一眼）

> **协作节奏与工程硬规则（harness 约束 H1-H12、mock-first / 小步验证 / 一次一个模块 / 日志即眼睛 / 根因不跳过 / 沙箱跑 Python 按风险分档 等）的单一事实源是 `.trae/rules/nanoquant_loop.md`**。本节只保留 cmd/PS 环境**具体坑表**（reference），不重复规则。

### 0.1 PowerShell / cmd 高频坑（环境 reference）

本项目命令行环境是 **Windows cmd**，不是 PowerShell。反复踩过的坑（对应项目规则 H1 沙箱跑 Python 按风险分档）：

| 坑 | 症状 | 正确做法 |
|---|---|---|
| `&&` 在 PS 里报错 | `标记"&&"不是此版本中的有效语句分隔符` | 在 **cmd** 里跑；或用 `cmd /c "..."` 包裹 |
| SQL 里 `<` `>` 被 PS 吃掉 | `WHERE a <= b` → PS 把 `<` 当重定向 | **含 SQL 的 `-c` 一律写 .py 文件再跑** |
| 引号转义地狱 | `-c "print('x')"` → `'` 和 `"` 互食 | 避免 `-c`，写 .py → 跑 → 删 |
| GBK 控制台崩溃 | `print("中文")` → `UnicodeEncodeError` | 日志统一 UTF-8 写文件；代码用 ASCII 标记（对应 H2 禁 emoji） |
| 沙箱输出丢失 | 任何长输出可能截断为空白 | 重要输出 `> out.txt 2>&1`，跑完 `type out.txt` |

**惯例**：快/短/只读/ASCII 输出的脚本（schema 查询、行数/null 统计、mock DataFrame 试跑）可直接沙箱跑；长任务（补数/回补/run_compute/backfill/大数据 EDA/写库/拉 tushare）给 .py 脚本由作者本地 cmd 跑，贴回日志。拿不准当长任务处理（H1）。

### 0.2 协作效率 Tips → 已收口到项目规则

> 原 8 条 Tips（小步验证 / mock 先于真实数据 / 改前看 schema / 日志是眼睛 / 幂等 / 一次一个模块 / 读完再改 / 错误信息=路标）已按 Loop Engineering 五移动 + harness 约束收口到 `.trae/rules/nanoquant_loop.md`：
> - mock-first / 小步验证 / 一次一个模块 → Loop 1-3 的「移交」「验证」门
> - 改前看 schema / 读完再改 → H9 改前必读
> - 日志是眼睛 → H10
> - 错误信息=路标 → H11
> - 幂等 → Loop「持久」+ overwrite 幂等
>
> **最理想节奏**：1 个明确需求 → AI 改 1 个模块 → 作者跑 1 条命令验证 → 确认再下一步（项目规则 §6）。

***

## 1. 入口脚本速查

| 脚本                            | 用途                   | 常用调用                                 |
| ----------------------------- | -------------------- | ------------------------------------ |
| `scripts/00_init_database.py` | 建库建表                 | `python scripts/00_init_database.py` |
| `scripts/run_ingest.py`       | 接入层拉数                | 见下                                   |
| `scripts/run_compute.py`      | 加工层计算                | 见下                                   |
| `scripts/run_strategy.py`     | 策略层回测/信号             | 见下                                   |
| `scripts/daily_task.bat`      | 每日定时（Windows 任务计划调它） | 双击或任务计划触发                            |

### 1.1 run\_ingest.py（接入层）

```bash
# 增量（从水位次日续跑到今天）
python scripts/run_ingest.py

# 回补指定 biz_date 区间
python scripts/run_ingest.py --start 20240101 --end 20240131

# 只跑指定接口（逗号分隔，key 见 tushare_apis.json）
python scripts/run_ingest.py --only daily,daily_basic

# 排除某些接口
python scripts/run_ingest.py --exclude income,balancesheet

# 列出全部 29 个接口
python scripts/run_ingest.py --list
```

### 1.2 run\_compute.py（加工层）

```bash
# 增量（panel → factor → label，按依赖顺序）
python scripts/run_compute.py

# 回补指定 biz_date 区间
python scripts/run_compute.py --start 20210101 --end 20251231

# 只跑某一层
python scripts/run_compute.py --layer panel
python scripts/run_compute.py --layer factor
python scripts/run_compute.py --layer label

# 只跑指定 calculator（格式 layer:name）
python scripts/run_compute.py --only panel:stock_daily,factor:price_volume_20d

# 列出全部 calculator
python scripts/run_compute.py --list
```

### 1.3 run\_strategy.py（策略层）

```bash
# 回测（默认近 1 年，周频调仓）
python scripts/run_strategy.py --mode backtest --start 20240101 --end 20241231

# 调策略参数
python scripts/run_strategy.py --mode backtest --lookback 20 --max-positions 5 --category broad
```

> **2026-07-22 更新**：`--mode signal` 已废弃（`signal_rebalance` 接口 + `signals/generator.py` 删除，write_mode=upsert 违反项目规则）。后续如需调仓信号在 `portfolio/` 层重写。

### 1.4 scripts/sync.py — 日常补数主命令

统一的拉数入口（替代直接调 run\_ingest），尤其适合「本地、不定期开机」：

```bash
# 日常：开机一键增量补齐（不传日期 → 各表从水位/保守窗口补到今天）
python scripts/sync.py

# 手动区间回补（overwrite 幂等，可断点重跑）
python scripts/sync.py --start 20200101 --end 20201231

# 只刷 / 排除某些接口；列出所有接口
python scripts/sync.py --only daily,moneyflow
python scripts/sync.py --exclude weekly,monthly
python scripts/sync.py --list
```

- 内部按 4 类策略分 Phase 执行（trade\_cal 最先 + reload 缓存 → 行情 → 财务 → 分红）。
- 全新空库首次：先 `--start` 回补全历史（无参增量对空库只拉今天/最近窗口）。
- 逐年回补历史：一年一条 `--start YYYY0101 --end YYYY1231`，断了重跑无副作用。

***

## 2. 核心模块 API 速查

### 2.1 config/settings.py — 全局配置

```python
from config.settings import settings

settings.tushare_token       # tushare token（从 .env 读）
settings.db_url              # mysql+pymysql://user:pwd@host:port/stock?charset=utf8mb4
settings.db_host / db_port / db_user / db_password / db_database
settings.validate()          # 返回缺失配置的警告列表（空=配置齐全）
```

### 2.2 config/database.py — 数据库读写（最常用）

```python
from config.database import engine, execute_sql, save_to_database

# 执行 SQL，返回 DataFrame（SELECT）或空 DataFrame（DDL/DML）
df = execute_sql("SELECT * FROM panel_stock_daily WHERE trade_date = '20240101'")

# 落库
save_to_database(df, "my_table", write_mode="overwrite", partition_col="trade_date")
#   write_mode: "overwrite"（先按 partition_col 删本批分区再批量写，推荐）/ "truncate"（全量刷新）

# overwrite：先按 partition_col DELETE 本批分区，再批量 append（幂等 + 去重护栏）
from config.database import overwrite_by_partition
overwrite_by_partition(df, "stock_daily", partition_col="trade_date", primary_keys=["ts_code","trade_date"])
```

其他：`get_table_info(table)` / `get_table_schema(table)` / `clear_table_data(table)` / `optimize_tables()`。

### 2.3 core/dates.py — 日期函数（全部返回 yyyymmdd 字符串）

```python
from core.dates import (
    get_today_str,                    # 今天
    is_trading_day,                   # is_trading_day("20240101") → False
    get_trade_dates_between,          # get_trade_dates_between("20240101", "20240131") → 交易日列表
    find_nearest_trading_day,         # find_nearest_trading_day("20240101", backward=True)
    get_previous_n_trading_date,      # get_previous_n_trading_date("20240131", n_days=5)
    get_next_n_trading_date,          # get_next_n_trading_date("20240101", n_days=5)
    get_recent_weekday,               # 最近一个周的最后交易日（<= 输入日期）
    get_recent_month,                 # 最近一个月的最后交易日（<= 输入日期）
    get_recent_quarter_dates,         # 前 N 个季度末日期
    get_month_start_end,              # 所在月首尾自然日
    get_monthly_last_tradedate,       # [start_year, end_year] 每月最后交易日
    reload_trade_cal,                 # trade_cal 表更新后强制重载缓存
)
```

**注意**：交易日函数依赖 `trade_cal` 表，首次调用懒加载并缓存。`trade_cal` 表更新后调 `reload_trade_cal()`。

### 2.4 core/preprocessing.py — 因子预处理

```python
from core.preprocessing import (
    mad_winsorize,          # MAD 缩尾（按日期+可选分组）
    standardize_factor,     # zscore / mad 标准化
    quantile_factor,        # 分组（分位数）
    rank_factor,            # 排名（pct）
    neutralize_factor,      # 市值+行业中性化（WLS）
    orthogonalize_factor,   # 对控制因子正交化
)
```

### 2.5 core/calculator.py — BaseCalculator（所有计算的基类）

子类声明类属性 + 实现 `get_data` / `process_data`，统一用 `update()` 跑：

```python
class BaseCalculator:
    table_name: str = ""           # 子类必填
    biz_date_col: str = "trade_date"  # trade_date / ann_date / snapshot_date
    primary_keys: list[str] = []   # 子类必填
    write_mode: str = "overwrite"  # 默认 overwrite（已废弃 upsert）；truncate 用于全量刷新小表
    partition_col: str | None      # write_mode=overwrite 时的分区键（trade_date/end_date/ex_date）
    output_schema: dict | None     # 加工层手写；接入层 None（自动推断）
    type_overrides: dict | None    # 接入层类型微调，如 {"desc": "TEXT"}

    def update(self, start_date=None, end_date=None, **params) -> DataFrame:
        """
        - start_date=None → 从 etl_biz_date 水位次日续跑（增量）
        - start_date 指定 → 从该 biz_date 回补
        - end_date=None → 到今天
        - **params 透传给 get_data / process_data
        """

    def get_data(self, start_date, end_date, **params) -> DataFrame:
        """子类实现：取数。"""
    def process_data(self, data, **params) -> DataFrame:
        """子类实现：计算。update 会透传 start_date/end_date。"""
    def save_to_database(self, data) -> None:
        """自动建表/演化 schema + 日期转换 + write_mode 写入。"""
```

**水位机制**：`update` 跑完会把 `biz_date_col` 的最大值写进 `etl_biz_date` 表。下次不传 `start_date` 时从水位次日续跑，避免重复拉取。

### 2.6 core/schema.py — Schema-as-code

```python
from core.schema import (
    infer_schema_from_df,    # 从 DataFrame 推断 {列: MySQL类型}
    ensure_table,            # 表不存在则建表（用 schema dict + 主键）
    evolve_schema,           # 表存在则比对列差异，加列自动 ALTER，删列/改类型只告警
    convert_date_columns,    # yyyymmdd 字符串 → date 对象（入库前）
    generate_create_table_sql,
)
```

**类型推断规则**（`infer_schema_from_df`，按优先级，命中即返回）：

1. `type_overrides` 指定 → 用指定值
2. 列名含 `_date`/`date_`/是 `trade_date`/`ann_date` → `DATE`
3. dtype 已明确（float64/int64/bool/datetime）→ 直接映射（float64→DOUBLE）
4. object 列按列名语义：`desc`→TEXT；`name`→VARCHAR(255)；其余字符串（code/flag/枚举）→**统一 VARCHAR(32)**
5. 其余 object 列做数值探测：非空全可转数字 → DOUBLE；全空列 → DOUBLE；含真实字符串 → VARCHAR(32)

> 字符串只两档：名称/长文本 `VARCHAR(255)`，其余（含 flag/code/枚举）统一 `VARCHAR(32)`；`desc` 超长 → TEXT。数值统一 DOUBLE。

### 2.7 data/etl/base.py — 接入层基类

```python
from data.etl.base import (
    get_pro_client,                       # tushare pro 客户端单例
    fetch_tushare,                        # 通用拉数（分页+重试）
    load_api_config,                      # 加载 tushare_apis.json
    TushareByTradeDateCalculator,         # 行情类基类（逐交易日拉，overwrite/trade_date）
    TushareByPeriodCalculator,            # 财务类基类（按报告期 period 拉，overwrite/end_date）
    TushareByExDateCalculator,            # 分红类基类（按除权日 ex_date 拉，overwrite/ex_date）
    TushareByAnnDateCalculator,           # 旧财务区间基类（保留兼容，已不用于生产）
    TushareFullRefreshCalculator,         # 基础信息类基类（全量 truncate）
)

# 29 个具体 Calculator 在 data/etl/loader.py，注册表：
from data.etl.loader import CALCULATORS  # {"daily": StockDailyCalculator, ...}
```

### 2.8 加工层基类

```python
from data.panel.base import PanelCalculator    # 自动加 panel_ 前缀
from data.factor.base import FactorCalculator  # 自动加 factor_ 前缀
from data.label.base import LabelCalculator    # 自动加 label_ 前缀

# 注册表（run_compute.py 用）
from data.panel import PANEL_CALCULATORS
from data.factor import CALCULATORS as FACTOR_CALCULATORS
from data.label import CALCULATORS as LABEL_CALCULATORS
```

三个基类都继承 `BaseCalculator`，区别只是表名前缀。子类必须声明 `output_schema`（手写，不用自动推断）。

***

## 3. 更新模式（write\_mode）+ 增量策略

> **2026-07-22 收口**：upsert 已彻底废弃（基类默认值 + 所有 Calculator + 文档全清零）。统一为 2 种写入模式。

### 3.1 两种写入模式（write\_mode）

| write\_mode | 落库逻辑                                | partition\_col | 适用       |
| ----------- | ----------------------------------- | -------------- | -------- |
| `truncate`  | TRUNCATE 整表后 append（全量刷新）           | 无              | 基础信息小表   |
| `overwrite` | **先按分区键 DELETE 本批分区，再批量 append**    | 必填             | 行情/财务/分红 |

**overwrite 三大保证**：

- **幂等**：删除维度(partition\_col) == 取数维度。重跑 = 删该批分区全部 + 写该批分区全部。
- **不脏**：同事务 DELETE + INSERT，失败回滚。
- **去重护栏**：落库前按主键去重；**发现重复主键时 WARNING 显式列出被删主键**（不静默吞）。

### 3.2 四类增量策略（incremental\_strategy）

| 策略              | 基类                    | 适用接口                                                  | partition\_col | 取数逻辑                                   |
| --------------- | --------------------- | ----------------------------------------------------- | -------------- | -------------------------------------- |
| `full_refresh`  | FullRefreshCalculator | trade\_cal, stock\_basic, index\_basic, fund\_basic 等 | 无(truncate)    | 每次全量 truncate                          |
| `by_trade_date` | ByTradeDateCalculator | daily, adj\_factor, moneyflow, index\_daily 等         | `trade_date`   | 逐交易日拉，按 trade\_date overwrite          |
| `by_period`     | ByPeriodCalculator    | income, balancesheet, cashflow, disclosure\_date      | `end_date`     | 按报告期(period)拉全市场，按 end\_date overwrite |
| `by_ex_date`    | ByExDateCalculator    | dividend                                              | `ex_date`      | 按除权除息日逐交易日拉，按 ex\_date overwrite       |

**增量 vs 回补**：所有策略「传 start\_date=回补、不传=日常增量」。

- **by\_trade\_date / full\_refresh**：传参=区间回补；不传=从水位次日续跑。
- **by\_period（财务+disclosure）**：传参=区间内所有季度末报告期；不传=增量起点取 `min(水位, today往前4期)`（覆盖财报修订 + 久未开机不漏）。
- **by\_ex\_date（dividend）**：传参=区间 ex\_date；不传=增量起点取 `min(水位, today-365天)`（覆盖分红修订/补录）。
- **为什么不用纯水位**：纯水位增量不回头覆盖修订（财报/分红会改）；纯固定窗口在久未开机时漏中间断档。`min(水位, today-窗口)` 两全。

**index\_weight 月频优化**：index\_weight 是月度数据，get\_data 只对「区间内每月最后交易日」取一次（旧实现逐交易日重复拉同月）。

### 3.3 特殊 PK 说明

**财务三表 PK**（实测 25 万行 0 重复）：`ts_code + end_date + ann_date + f_ann_date + update_flag`。

- 同报告期多次修订靠 `f_ann_date` 区分（point-in-time 必需）
- vip 默认只返回 report\_type=1（合并报表）；若拉其他 report\_type 须把 report\_type 加进 PK

**dividend 用 ex\_date 而非 ann\_date**：dividend 无 period 参数；只关心真实分红 → ex\_date 非空的"实施"记录才被命中，自动过滤预案/股东大会通过阶段。

**disclosure\_date PK** = `ts_code + end_date`；**dividend PK** = `ts_code + end_date + ann_date + div_proc + update_flag`。

***

## 4. 加工层指南（panel / factor / label）

### 4.1 跑现有 Calculator

见 §1.2，用 `run_compute.py`。也可直接调：

```python
from data.panel import StockDailyPanelCalculator
from data.factor import PriceVolume20DCalculator
from data.label import ForwardReturnsCalculator

StockDailyPanelCalculator().update(start_date="20240101", end_date="20240131")
PriceVolume20DCalculator().update(start_date="20240101", end_date="20240131")
ForwardReturnsCalculator().update(start_date="20240101", end_date="20240131")
```

### 4.2 依赖顺序

`run_compute.py` 按 `panel → factor → label` 顺序跑。加工层依赖关系：

- `panel/*` 读接入层表
- `factor/*` 读 `panel_stock_daily`（多数）或 `panel_financial_*`
- `label/*` 读 `panel_stock_daily`

**先跑接入层再跑加工层**。`schedule_compute.json` 里声明了 `depends_on`，runner 会拓扑排序。

### 4.3 写新的加工层 Calculator

```python
# data/factor/my_factor.py
from data.factor.base import FactorCalculator

class MyFactorCalculator(FactorCalculator):
    table_name = "my_factor"          # → factor_my_factor（基类自动加前缀）
    primary_keys = ["ts_code", "trade_date"]
    biz_date_col = "trade_date"
    write_mode = "overwrite"
    partition_col = "trade_date"      # overwrite 必须声明分区键

    output_schema = {
        "ts_code": "string",
        "trade_date": "string",
        "my_value": "float",
    }

    def get_data(self, start_date, end_date, **params):
        from config.database import execute_sql
        return execute_sql(f"""
            SELECT ts_code, trade_date, close, vol
            FROM panel_stock_daily
            WHERE trade_date BETWEEN '{start_date}' AND '{end_date}'
        """)

    def process_data(self, data, **params):
        data["my_value"] = data["close"].rolling(20).mean() / data["close"]
        return data[["ts_code", "trade_date", "my_value"]]
```

然后在 `data/factor/__init__.py` 的 `CALCULATORS` 注册表加一行，`run_compute.py --list` 就能看到。

***

## 5. 策略层（portfolio / backtest / signals）

三者**共用同一套策略代码**，避免回测/实盘两套。

### 5.1 portfolio/ — 组合构建

```python
from portfolio.strategy import CrossSectionalMomentumStrategy
from portfolio.universe import DEFAULT_ETF_POOL, get_etf_universe

# DEFAULT_ETF_POOL: 16 个 A股 ETF（宽基6 + 行业7 + 风格3）
strategy = CrossSectionalMomentumStrategy(
    lookback=20,          # 动量回看窗口
    vol_window=20,        # 波动率窗口
    vol_threshold=0.4,    # 年化波动率过滤阈值
    max_positions=5,      # 持仓数量上限
    max_weight=0.3,       # 单标的仓位上限
    max_drawdown=0.10,    # 回撤止损线
    universe_category="all",
)

weights = strategy.compute_target_weights(end_date="20240131", current_drawdown=0.0)
weights = strategy.apply_constraints(weights)  # 单标的上限 + 归一化
```

### 5.2 backtest/ — 回测引擎

```python
from backtest.engine import VectorizedBacktester
from backtest.metrics import compute_metrics

bt = VectorizedBacktester(strategy=strategy, rebalance_freq="W", commission=0.0005)
result = bt.run(start_date="20240101", end_date="20241231")
# result = {
#   "equity_curve": DataFrame, "trades": DataFrame,
#   "metrics": {total_return, annual_return, sharpe, max_drawdown, calmar, win_rate},
# }
```

### 5.3 signals/ — 调仓信号（已废弃）

> **2026-07-22**：`signals/generator.py` 已删除（`signal_rebalance` 接口 write_mode=upsert 违反项目规则）。
> 后续如需调仓信号应在 `portfolio/` 层重写（共用同一套策略代码，落库表名待定）。

***

## 6. Tushare API 开发期探查

### 6.1 用 tushare 官方 MCP 探查（推荐）

项目环境已接入 `mcp_tushareMcp`（200+ 接口 schema）。**开发期探查用 MCP，不用翻文档。**

Agent 调用方式：

```
server_name: mcp_tushareMcp
tool_name: daily / income / stock_basic / ...（200+ 个）
args: { "ts_code": "000001.SZ", "trade_date": "20240101" }
```

### 6.2 用 Python 直接拉数（生产/调试）

```python
from data.etl.base import fetch_tushare

df = fetch_tushare(
    api_name="daily",
    params={"trade_date": "20240101"},
    fields="ts_code,trade_date,open,high,low,close,vol,amount",
)
```

### 6.3 配置新接口流程

1. 用 MCP 探查接口字段/参数
2. 在 `config/tushare_apis.json` 加一项（标 `incremental_strategy` + `biz_date_col` + `fields`）
3. 在 `data/etl/loader.py` 加 Calculator（声明 `config_key`，继承对应中间基类，通常 5 行代码）
4. `python scripts/run_ingest.py --only <新接口key>` 验证

***

## 7. 踩过的坑（Pitfalls）

> 每个坑都曾导致过 pipeline 崩溃或数据错误。动手前务必过一遍。
>
> **本节是具体 bug 目录（catalog）**：记录每个坑的现象/根因/修复。坑背后的**工程实践**（mock-first、沙箱跑 Python 按风险分档、禁 emoji、DOUBLE 不 DECIMAL、去重不静默等）已收口为硬规则在 `.trae/rules/nanoquant_loop.md`（H1-H12 + Loop 验证门）；本节保留具体 bug 细节供回溯。

### 7.1 NaN→None 破坏数值 dtype

接入层 `process_data` **不得把 NaN→None**。旧代码 `replace(NaN, None)` 会让 float64 列上溯成 object → schema 误判 VARCHAR(255) → 财务宽表行大小爆 InnoDB 65535 限制 1118 错误。

正确做法：只 `replace([inf,-inf], NaN)`，NaN 由 `to_sql` 自动写 NULL，保住数值 dtype。

### 7.2 Emoji / 非 ASCII 符号导致 Windows 控制台崩溃

**任何脚本/代码/print 一律禁用 emoji 及非 ASCII 符号**（如 ⚠️/✓）。Windows GBK 控制台 print emoji 会 `UnicodeEncodeError` 直接崩溃（曾导致 `data_audit.py` 崩在打印阶段、结果文件都写不出）。

需要标记一律用 ASCII：`[WARN]` / `[OK]` / `[EMPTY]` 等。

### 7.3 数值列统一 DOUBLE，不用 DECIMAL

金融大额字段（市值/成交额/股本，万元单位可达亿级）DECIMAL 比 DOUBLE 大 1-2 字节，且超 M 位会 `Out of range` 硬报错中断 pipeline；DOUBLE 超大值降级科学计数法不报错。省的空间 <1%，不值得换溢出风险。

### 7.4 Schema 演化规则

- 加列：自动 `ALTER`（无破坏）
- 删列：保留旧列，不自动 DROP
- 改类型：只告警，不自动修改
- 变更写进 `etl_schema_log`

### 7.5 财务三表 report\_type 约束

vip 默认只返回 `report_type=1`（合并报表）。若拉其他 report\_type（如母公司报表），须把 `report_type` 加进主键，否则 overwrite 会互删。

### 7.6 加工层日期格式统一规则

接入层 `trade_date` 存 MySQL DATE 类型（`yyyy-mm-dd`）。加工层所有新表的 `trade_date` / `biz_date_col` 输出统一用 **`yyyy-mm-dd`** **字符串**，不用 `YYYYMMDD`。

`BaseCalculator.update()` 传进来的 `start_date/end_date` 是 `YYYYMMDD` 格式，加工层内部 `pd.to_datetime` 统一处理，落库 `convert_date_columns` 自动转 DATE。**不在加工层做格式互转。**

### 7.7 新增双版指数必验两年份

沪深300/500/1000 保留沪+深两个代码（互补，非冗余）：index\_weight 成分权重的归属代码随年份变化。例如沪深300 早年(2010)成分只在 `399300.SZ`、近年在 `000300.SH`。保留双版保证任何年份成分穿透不缺数据。新增双版指数前务必两版都验早年+近年。

### 7.8 加新指数前先验能否取 index\_weight

```python
run_mcp(mcp_tushareMcp, index_weight, {index_code:"000016.SH", start_date:"20240101", end_date:"20240131"})
```

返回非空 = 该指数有成分权重数据，可加入；返回 `[]` = tushare 未收录该指数成分。

### 7.9 接入层 overwrite 去重不静默

`overwrite_by_partition` 落库前按主键去重，有 `update_flag` 留最大版本；发现重复主键时 **WARNING 显式列出被删主键**（不静默吞），便于核查数据源异常。

### 7.10 Tests 不可信

`tests/test_step*` 是历史搭建验收测试，多数已与现状不符（表结构、Calculator 名、write\_mode 均已变），**不是回归套件**。别依赖它判断对错，以 `run_compute.py --list` 实际输出为准。

### 7.11 指标列必须注释物理意义

每个指标回答一个具体问题（如"大家同涨同跌吗""跌比涨更剧烈吗"），不写"XX 指标"式废话注释。公式来源如有业界标准（CBOE、学术论文）必须注明。

### 7.12 pandas merge_asof + by 排序陷阱

**现象**：`pd.merge_asof(left, right, on='trade_date_dt', by='ts_code', direction='backward')` 报 `ValueError: left keys must be sorted`，即使已经调了 `sort_values(['ts_code', 'trade_date_dt']).reset_index(drop=True)`。

**根因**：pandas 2.x 的 `merge_asof` 加 `by` 时，对 `on` 列做**全局** `is_monotonic_increasing` 检查。按 `['ts_code', 'trade_date_dt']` 排序后，`trade_date_dt` 跨股票会"重置"（A股最后一天 → B股第一天），不满足全局单调。

**正确做法**：只按 `on` 列排序（不按 `by` 列），让 `on` 列全局单调递增。`by` 分组内自然保序，语义不变。

```python
# ❌ 错误：按 by+on 排序，on 列不全局单调
left = df.sort_values(['ts_code', 'trade_date_dt'])

# ✅ 正确：只按 on 排序，全局单调，by 分组内也保序
left = df.sort_values('trade_date_dt').reset_index(drop=True)
right = right_df.sort_values('trade_date_dt').reset_index(drop=True)
pd.merge_asof(left, right, on='trade_date_dt', by='ts_code', direction='backward')
```

### 7.13 merge_asof / pandas 语法调试不要连数据库

**原则**：调试 `merge_asof`、`pivot_table`、`groupby` 等 pandas 语法时，用 5-10 行 mock DataFrame 本地验证，不要跑真实 pipeline（拉库→join→落库 耗时数分钟）。

```python
# 本地快速验证 merge_asof 语法
import pandas as pd
left = pd.DataFrame({
    'ts_code': ['A', 'A', 'B', 'B'],
    'trade_date_dt': pd.to_datetime(['2020-01-02', '2020-01-03', '2020-01-02', '2020-01-03']),
})
right = pd.DataFrame({
    'ts_code': ['A', 'A', 'B', 'B'],
    'trade_date_dt': pd.to_datetime(['2020-01-01', '2020-01-02', '2020-01-01', '2020-01-02']),
    'val': [1, 2, 3, 4],
})
left_sorted = left.sort_values('trade_date_dt').reset_index(drop=True)
right_sorted = right.sort_values('trade_date_dt').reset_index(drop=True)
pd.merge_asof(left_sorted, right_sorted, on='trade_date_dt', by='ts_code', direction='backward')
```

跑通后再上真实数据。**切忌每改一行就重跑完整 pipeline。**

### 7.14 Trae 沙箱边界 — 按风险分档（对应项目规则 H1）

**Trae 内置沙箱有四类硬伤**：PowerShell 引号转义异常、中文输出截断、进程被随机 `Ctrl+C` 杀死（exit code `-1073741510`）、`run_compute` 等长时间任务的输出完全丢失。长任务必踩，快速只读脚本踩不到。

**分档**（项目规则 H1 为准）：
- ✅ **可直接沙箱跑**：快/短/只读/ASCII 输出——`SELECT COUNT(*)`、列对齐、行数/null 统计、5-10 行 mock DataFrame 语法试跑、pandas 逻辑验证。
- ❌ **必须给 .py 脚本由作者本地 cmd 跑**：补数/回补/`run_compute`/`backfill`/大数据 EDA/任何写库/任何拉 tushare（耗时长+撞限频+花钱不可逆）。
- **Python 语法/逻辑调试一律用 mock DataFrame**（见 §7.13），不连数据库。
- 沙箱另可用于：`pip install`、`git`、`Get-ChildItem` 等简单 shell。
- **拿不准 → 当长任务处理（给本地）**。

**正确做法**：长任务 → Agent 给 .py/命令 → 作者本地 cmd 跑 → 作者贴回日志/结果 → Agent 分析。

### 7.15 PowerShell / 命令行 常见坑

→ 已前置到 [§0.1](#01-powershell--cmd-高频坑)。不再赘述。

### 7.16 moneyflow_hsgt.north_money 单位跳变（2024-08-19）

**症状**：`north_money` 在 2024年8月起数值跳变 ~10,000 倍，月度加总后跨年不可比。

**根因**：tushare `moneyflow_hsgt` 接口在 **2024-08-19** 将 `north_money` 字段单位从「万元」改为「元」。
- 2014-01 ~ 2024-08-18：单位为 **万元**（日值范围 -17,000 ~ +21,000）
- 2024-08-19 至今：单位为 **元**（日值范围 -13,000 ~ +510,000）
- 跳变精确发生在 2024-08-19：前一天 `-6,774`（万元），当天 `88,110`（元）

**修复**：`market_sentiment_monthly.py` 的 `_normalize_north_money()` 自动将 post-2024-08-19 数据除以 10,000，统一归一到「万元」。**所有消费 north_money 的下游代码无需额外处理。**

**验证**：`scripts/find_north_money_break.py` 可重新扫描确认跳变点未漂移。

***

### 7.17 `astype(int)` 爆炸：NaN 不能直接转整数

**症状**：
```
pandas.errors.IntCastingNaNError: Cannot convert non-finite values (NA or inf) to integer
```
发生在 `df.astype(int)` 或 `.dt.days.astype(int)` 等位置。

**根因**：pandas 的 `Int64` 可存 NaN，但 numpy `int64` 不行。`.astype(int)` 走 numpy 路径，遇到 NaN 就炸。

**修复模板**：
```python
# ❌ 不能：NaN + astype(int)
df["col"] = some_calc.astype(int)

# ✅ 修复：先 fillna 再转
df["col"] = some_calc.fillna(-1).astype(int)  # 用 -1 标记缺失

# ❌ 不能：bool 序列含 NaN 直接 astype(int)
df["is_x"] = (condition).astype(int)

# ✅ 修复：.fillna(False) 先
df["is_x"] = (condition).fillna(False).astype(int)
```

**常见触发场景**：
- `.dt.days` 从 NaT 算出差值 → NaN
- `np.where(cond, 1, 0)` 当 cond 含 NaN → NaN
- groupby 操作后某些组无数据 → NaN
- 两 DataFrame merge 后右表缺行 → NaN

**验证门**：任何写新 Calculator 或新增整数列时，**必须**确保 `output_schema` 声明的 `"int"` 列在最终 DataFrame 中不含 NaN。最稳妥的做法是全程 `.fillna(-1)` 或 `.fillna(0)`。

***

## 8. 开放问题（遇到时询问作者）

1. **ETF 池范围**：`DEFAULT_ETF_POOL` 是默认值，作者可调整。
2. **回测结果落库粒度**：每日净值 + 调仓记录都落库？还是只落最终结果？
3. **财务数据历史深度**：全量回补从哪年开始（2010? 2015?）？
4. **调度框架**：当前裸脚本 + Windows 任务计划，长期可迁 Prefect 2.x。

