# nanoquant — 项目上下文与开发指南 (CLAUDE.md)

> 给 AI Agent 和作者本人：本文件描述 nanoquant 的**当前架构**、常用入口、核心 API 速查。
>
> **最高原则：个人够用 + 云迁移友好 + 前期高维护后期中低维护。** 能复用就复用，不合理就改。
>
> 「约定」= 必须遵守；「建议」= 可讨论。

---

## 0. 新会话从这里开始（换环境/新 Agent 必读）

**推荐阅读顺序**：本文件(架构+约定) → `ROADMAP.md`(下一步该做什么) → `TUSHARE_API_GUIDE.md`(数据层细节) → `README.md`(命令速查) → `scripts/README_sync.md`(补数用法)。

**当前进度（2026-06）**：
- ✅ **接入层完成**：26 个 tushare 接口（含 4 个 ETF），4 类增量策略（by_trade_date / by_period / by_ex_date / full_refresh），overwrite 幂等 + 去重护栏，schema 自动推断（数值 DOUBLE / 字符串两档），sync.py 补数入口。
- 🔄 **进行中**：用 `scripts/backfill_years.py` 逐年回补 2010 至今历史数据，`scripts/data_audit.py` 体检。
- ⏭️ **下一步**：compute 层开工，第一站 = `ROADMAP.md §1.0` panel 指数成分表（双版去重 + 时点成分），然后 panel → factor → label（因子按月采样）。

**最关键的几条约定（动手前务必知道）**：
1. 增删 tushare 接口 → 改 `config/tushare_apis.json` + `data/etl/loader.py`，**不要跑** `gen_tushare_apis`（已删）。
2. 指数池在 `config/universe.py`（接入层用 ALL 含双版、下游用 CANONICAL 去重），见 §5.5。
3. 写入统一 overwrite/truncate（废弃 upsert）；数值列统一 DOUBLE（见 §7.6/§8）。
4. 战略方向：ETF 轮动为主引擎、多因子降为风控+拥挤监测（见 ROADMAP 末尾），不卷因子。
5. `tests/test_step*` 是历史搭建验收测试，多数已与现状不符，**不是回归套件**，别依赖它判断对错。

---

## 1. 一句话目标

面向 **ETF 截面轮动**的完整闭环：数据接入 → 因子/风控诊断 → 回测 → 调仓信号。MVP 已跑通"ETF 数据 → 动量因子 → 截面轮动回测 → 调仓信号"。

---

## 2. 架构总览

### 2.1 目录与职责

```
nanoquant/
├── config/                       # 全局配置
│   ├── settings.py               # .env 加载 + settings 单例（tushare_token / db_url）
│   ├── database.py               # engine + execute_sql / save_to_database / upsert_data
│   └── tushare_apis.json         # 26 个 tushare 接口配置（fields + 增量策略 + write_mode）
│
├── core/                         # 跨层共享核心
│   ├── calculator.py             # BaseCalculator（统一 update + 水位 + schema-as-code）
│   ├── schema.py                 # schema 推断 + ensure_table + evolve_schema
│   ├── dates.py                  # 交易日工具（is_trading_day / get_trade_dates_between ...）
│   └── preprocessing.py          # mad_winsorize / neutralize_factor / rank_factor ...
│
├── data/
│   ├── etl/                      # 接入层（tushare 1:1 复刻，26 个 Calculator）
│   │   ├── base.py               # TushareCalculatorMixin + 五个中间基类（trade_date/period/ex_date/ann_date/full_refresh）
│   │   └── loader.py             # 26 个具体 Calculator + CALCULATORS 注册表
│   ├── panel/                    # 加工层 - 面板数据（实体×时间对齐宽表，7 个）
│   ├── factor/                   # 加工层 - 因子（实体×日，6 个）
│   └── label/                    # 加工层 - 标签（实体×日，1 个）
│
├── pipeline/
│   ├── incremental/              # 四类增量策略基类（by_trade_date / by_period / by_ex_date / full_refresh）
│   ├── runner.py                 # JSON 配置驱动的调度执行器
│   ├── schedule_ingest.json      # 接入层调度配置
│   └── schedule_compute.json     # 加工层调度配置
│
├── portfolio/                    # 策略层 - 组合构建（CrossSectionalMomentumStrategy + ETF 池）
├── backtest/                     # 策略层 - 回测引擎（VectorizedBacktester + compute_metrics）
├── signals/                      # 策略层 - 调仓信号（SignalGenerator，复用 portfolio 策略）
│
├── scripts/                      # 运行脚本（见 §4）
├── research/                     # 研究 notebook + 评估指标
└── tests/                        # 验收测试（test_step3 ~ test_step10）
```

### 2.2 分层规则

| 层 | 目录 | 数据来源 | 扩充方式 |
|----|------|---------|---------|
| **接入层** | `data/etl/` | tushare API 1:1 | 改 `config/tushare_apis.json` + 在 `loader.py` 加 Calculator |
| **加工层** | `data/panel/` `data/factor/` `data/label/` | 读接入层表，自己算 | 写 Calculator（继承 Panel/Factor/LabelCalculator） |
| **策略层** | `portfolio/` `backtest/` `signals/` | 读加工层表 | 写策略代码 |
| **调度层** | `pipeline/` | 编排上述 | 改 `schedule_*.json` |

**加工层用 panel 抽象分三个目录**（不按 alpha/risk 分，因子降级为风控诊断工具）：

| 子目录 | 角色 | 例子 |
|--------|------|------|
| `panel/` | 面板数据（实体×时间对齐宽表，因子/标签的输入底座） | `panel_stock_daily`、`panel_market_sentiment_daily`、`panel_financial_statements_snapshot` |
| `factor/` | 因子（实体×日，从 panel 计算） | `factor_price_volume_20d`、`factor_valuation` |
| `label/` | 标签（实体×日，从 panel 计算） | `label_forward_returns` |

### 2.3 表名约定

| 层 | 前缀 | 例子 |
|----|------|------|
| 接入层 | 无前缀（tushare 原表名） | `stock_basic`、`daily`、`income`、`trade_cal` |
| panel | `panel_` | `panel_stock_daily`、`panel_mv_monthly` |
| factor | `factor_` | `factor_price_volume_20d` |
| label | `label_` | `label_forward_returns` |
| 策略层 | `signal_` | `signal_rebalance` |
| 元数据 | `etl_` | `etl_biz_date`（水位表）、`etl_schema_log`（schema 留痕） |

实体维度 + 频率在表名里体现：`stock_`（个股）/ `market_`（市场）/ `fin_`（财务）+ `_daily` / `_monthly` / `_snapshot`。**不分区**（数据量未到）。

---

## 3. 快速上手

### 3.1 环境配置

```bash
# 1. 装 Python 3.11+（推荐 3.14）+ 依赖
pip install -r requirements.txt

# 2. 复制 .env 模板，填入 tushare token + MySQL 密码
cp .env.example .env
#   TUSHARE_TOKEN=（在 https://tushare.pro 注册获取）
#   DB_HOST=localhost / DB_USER=root / DB_PASSWORD=xxx / DB_DATABASE=stock

# 3. MySQL 里建库
#   CREATE DATABASE stock CHARACTER SET utf8mb4;
```

### 3.2 首次初始化（建表 + 水位表）

```bash
python scripts/00_init_database.py
# 做什么：测试连接 → 建 etl_biz_date / etl_schema_log → 遍历所有 Calculator 用 output_schema 建表
# --dry-run 只打印不执行
```

### 3.3 日常跑数（两条命令）

```bash
# 接入层：从 tushare 拉数入库（增量 = 从水位次日续跑到今天）
python scripts/run_ingest.py

# 加工层：跑 panel → factor → label（增量）
python scripts/run_compute.py
```

详见 §4 入口脚本速查。

---

## 4. 入口脚本速查

| 脚本 | 用途 | 常用调用 |
|------|------|---------|
| `scripts/00_init_database.py` | 建库建表 | `python scripts/00_init_database.py` |
| `scripts/run_ingest.py` | 接入层拉数 | 见下 |
| `scripts/run_compute.py` | 加工层计算 | 见下 |
| `scripts/run_strategy.py` | 策略层回测/信号 | 见下 |
| `scripts/daily_task.bat` | 每日定时（Windows 任务计划调它） | 双击或任务计划触发 |
| `scripts/gen_tushare_apis.py` | 用 MCP 探查结果生成 tushare_apis.json | 开发期用 |

### 4.1 run_ingest.py（接入层）

```bash
# 增量（从水位次日续跑到今天）
python scripts/run_ingest.py

# 回补指定 biz_date 区间
python scripts/run_ingest.py --start 20240101 --end 20240131

# 只跑指定接口（逗号分隔，key 见 tushare_apis.json）
python scripts/run_ingest.py --only daily,daily_basic

# 排除某些接口
python scripts/run_ingest.py --exclude income,balancesheet

# 列出全部 26 个接口
python scripts/run_ingest.py --list
```

### 4.2 run_compute.py（加工层）

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

### 4.3 run_strategy.py（策略层）

```bash
# 回测（默认近 1 年，周频调仓）
python scripts/run_strategy.py --mode backtest --start 20240101 --end 20241231

# 生成最新调仓信号并落库 signal_rebalance
python scripts/run_strategy.py --mode signal

# 只生成不落库
python scripts/run_strategy.py --mode signal --dry-run

# 调策略参数
python scripts/run_strategy.py --mode signal --lookback 20 --max-positions 5 --category broad
```

---

## 5. 测试 tushare API 取数（开发期探查）

### 5.1 用 tushare 官方 MCP 探查字段/参数（推荐）

项目环境已接入 `mcp_tushareMcp`（200+ 接口 schema）。**开发期探查接口字段、参数、返回值用 MCP，不用翻文档。**

调用方式（Agent 用 `run_mcp` 工具，作者可在 IDE 的 MCP 面板里直接调）：

```
server_name: mcp_tushareMcp
tool_name: daily / income / stock_basic / ...（200+ 个，对应 tushare 接口名）
args: { "ts_code": "000001.SZ", "trade_date": "20240101" }
```

典型场景：
- **查接口有哪些字段** → 调一次 MCP，看返回 DataFrame 的列
- **查接口参数** → 看 MCP 工具的 schema（参数描述）
- **验证字段含义** → 调一次拿真实数据对照

### 5.2 用 Python 直接拉数（生产/调试）

```python
from data.etl.base import fetch_tushare

# 直接调 tushare（自动分页 + 重试）
df = fetch_tushare(
    api_name="daily",
    params={"trade_date": "20240101"},
    fields="ts_code,trade_date,open,high,low,close,vol,amount",
)
```

### 5.3 跑单个 Calculator 验证

```python
from data.etl.loader import StockDailyCalculator

calc = StockDailyCalculator()
# 增量（从水位续跑）
result = calc.update()
# 回补指定区间
result = calc.update(start_date="20240101", end_date="20240131")
print(f"拉到 {len(result)} 行")
```

### 5.4 配置新接口流程

1. 用 MCP 探查接口字段/参数
2. 在 `config/tushare_apis.json` 加一项（标 `incremental_strategy` + `biz_date_col` + `fields`）
3. 在 `data/etl/loader.py` 加 Calculator（声明 `config_key`，继承对应中间基类，通常 5 行代码）
4. `python scripts/run_ingest.py --only <新接口key>` 验证

### 5.5 标的池配置（遍历型接口拉哪些指数）

指数池**定义在 `config/universe.py`**（接入层 + 下游策略层共用的单一事实源），不再硬编码在 loader.py。结构化字典 `INDEX_POOL` 自动派生三个产物：

| 产物 | 给谁用 | 含义 |
|---|---|---|
| `ALL_INDEX_CODES`（18 个，含双版） | **接入层** loader.py（`index_daily`/`index_dailybasic`/`index_weight`） | 含沪深双版冗余，保证任何年份成分不漏 |
| `CANONICAL_INDEX_CODES`（15 个） | **下游** panel/策略层 | 每个指数唯一规范代码，去重后用 |
| `CODE_TO_CANONICAL` / `canonical()` | 下游去重 | alt 代码→canonical（如 399300.SZ→000300.SH） |

- **改指数池 = 只动 `config/universe.py` 的 `INDEX_POOL` 字典**，接入层和下游同步生效。
- 现有 15 个 canonical：宽基(上证50/沪深300/中证500/800/1000/2000/科创50/创业板/中证全指) + 风格(中证红利/红利低波/红利低波100/上证红利/300价值/基本面50)。
- ⚠️ **沪深300/500/1000 保留沪+深两个代码（互补，非冗余）**：index_weight 成分权重的归属代码随年份变化且每个指数规律不同——实测沪深300 早年(2010)成分只在 `399300.SZ`、近年在 `000300.SH`；中证500 则 `000905.SH` 一直有。保留双版保证任何年份成分穿透不缺数据。接入层全拉(ALL)，**下游用 panel 成分表按 canonical 去重对齐时点成分**（见 ROADMAP 阶段1）。新增双版指数前务必两版都验早年+近年。
- **加新指数前先验能否取 index_weight**：
  ```
  run_mcp(mcp_tushareMcp, index_weight, {index_code:"000016.SH", start_date:"20240101", end_date:"20240131"})
  ```
  返回非空 = 该指数有成分权重数据，可加入；返回 `[]` = tushare 未收录该指数成分（如部分中华/标普系列），不能用于穿透。
- 改完用 `python scripts/sync.py --start YYYY0101 --end YYYY1231 --only index_weight,index_daily,index_dailybasic` 回补新指数历史。

---

## 6. 中间层跑数指南（panel / factor / label）

### 6.1 跑现有 Calculator

见 §4.2，用 `run_compute.py`。也可直接调：

```python
from data.panel import StockDailyPanelCalculator
from data.factor import PriceVolume20DCalculator
from data.label import ForwardReturnsCalculator

# panel：个股×日 行情宽表（行情+复权+市值+ST+行业+指数成分）
StockDailyPanelCalculator().update(start_date="20240101", end_date="20240131")

# factor：20 日量价因子（依赖 panel_stock_daily）
PriceVolume20DCalculator().update(start_date="20240101", end_date="20240131")

# label：未来 N 日收益率
ForwardReturnsCalculator().update(start_date="20240101", end_date="20240131")
```

### 6.2 依赖顺序

`run_compute.py` 按 `panel → factor → label` 顺序跑。加工层依赖关系：
- `panel/*` 读接入层表（`daily` / `adj_factor` / `daily_basic` / `stock_st` / `index_member_all` / 财务三表）
- `factor/*` 读 `panel_stock_daily`（多数）或 `panel_financial_*`
- `label/*` 读 `panel_stock_daily`

**先跑接入层再跑加工层**。`schedule_compute.json` 里声明了 `depends_on`，runner 会拓扑排序。

### 6.3 写新的加工层 Calculator

```python
# data/factor/my_factor.py
from data.factor.base import FactorCalculator

class MyFactorCalculator(FactorCalculator):
    table_name = "my_factor"          # → factor_my_factor（基类自动加前缀）
    primary_keys = ["ts_code", "trade_date"]
    biz_date_col = "trade_date"
    write_mode = "upsert"

    output_schema = {
        "ts_code": "string",
        "trade_date": "string",
        "my_value": "float",
    }

    def get_data(self, start_date, end_date, **params):
        # 从 panel_stock_daily 查区间数据
        from config.database import execute_sql
        return execute_sql(f"""
            SELECT ts_code, trade_date, close, vol
            FROM panel_stock_daily
            WHERE trade_date BETWEEN '{start_date}' AND '{end_date}'
        """)

    def process_data(self, data, **params):
        # 算因子
        data["my_value"] = data["close"].rolling(20).mean() / data["close"]
        return data[["ts_code", "trade_date", "my_value"]]
```

然后在 `data/factor/__init__.py` 的 `CALCULATORS` 注册表加一行，`run_compute.py --list` 就能看到。

---

## 7. 核心模块 API 速查

### 7.1 config/settings.py — 全局配置

```python
from config.settings import settings

settings.tushare_token       # tushare token（从 .env 读）
settings.db_url              # mysql+pymysql://user:pwd@host:port/stock?charset=utf8mb4
settings.db_host / db_port / db_user / db_password / db_database
settings.validate()          # 返回缺失配置的警告列表（空=配置齐全）
```

### 7.2 config/database.py — 数据库读写（最常用）

```python
from config.database import engine, execute_sql, save_to_database, upsert_data

# 执行 SQL，返回 DataFrame（SELECT）或空 DataFrame（DDL/DML）
df = execute_sql("SELECT * FROM panel_stock_daily WHERE trade_date = '20240101'")

# 落库
save_to_database(df, "my_table", write_mode="upsert")
#   write_mode: "overwrite"（先删分区再批量写，推荐）/ "truncate"（全量刷新）/ "upsert"（已废弃，逐行慢）

# overwrite：先按 partition_col DELETE 本批分区，再批量 append（幂等 + 去重护栏）
from config.database import overwrite_by_partition
overwrite_by_partition(df, "stock_daily", partition_col="trade_date", primary_keys=["ts_code","trade_date"])

# 直接 upsert（save_to_database 内部调它）
upsert_data("my_table", df)
```

其他：`get_table_info(table)` / `get_table_schema(table)` / `clear_table_data(table)` / `optimize_tables()`。

### 7.3 core/dates.py — 日期函数（全部返回 yyyymmdd 字符串）

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

### 7.4 core/preprocessing.py — 因子预处理

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

### 7.5 core/calculator.py — BaseCalculator（所有计算的基类）

子类声明类属性 + 实现 `get_data` / `process_data`，统一用 `update()` 跑：

```python
class BaseCalculator:
    table_name: str = ""           # 子类必填
    biz_date_col: str = "trade_date"  # trade_date / ann_date / snapshot_date
    primary_keys: list[str] = []   # 子类必填
    write_mode: str = "upsert"     # overwrite（推荐）/ truncate / upsert（废弃）
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

### 7.6 core/schema.py — Schema-as-code

```python
from core.schema import (
    infer_schema_from_df,    # 从 DataFrame 推断 {列: MySQL类型}
    ensure_table,            # 表不存在则建表（用 schema dict + 主键）
    evolve_schema,           # 表存在则比对列差异，加列自动 ALTER，删列/改类型只告警
    convert_date_columns,    # yyyymmdd 字符串 → date 对象（入库前）
    generate_create_table_sql,
)
```

**类型推断规则**（`infer_schema_from_df` → `_infer_col_type`，按优先级，命中即返回）：
1. `type_overrides` 指定 → 用指定值（如 index_basic 的 `desc`→TEXT）
2. 列名含 `_date`/`date_`/是 `trade_date`/`ann_date` → `DATE`
3. dtype 已明确（float64/int64/bool/datetime，非 object）→ 直接映射（float64→DOUBLE）
4. object 列按列名语义：`desc`→TEXT；`name`/`*_name`/长文本→VARCHAR(255)；
   其余字符串（`ts_code`/`*_code`/`update_flag`/`*_flag`/枚举状态码 report_type/comp_type/div_proc/market...）→**统一 VARCHAR(32)**
5. 其余 object 列做**数值探测**：非空值全可转数字 → DOUBLE；**全空列 → DOUBLE**（财务罕见科目默认数值）；含真实字符串 → VARCHAR(32)

> 字符串只两档：名称/长文本 `VARCHAR(255)`，其余（含 flag/code/枚举）统一 `VARCHAR(32)`；`desc` 超长 → TEXT。数值统一 DOUBLE。

> **关键坑（2026-06-21 修复）**：接入层 `process_data` **不得把 NaN→None**（`base.py` 旧代码 `replace(NaN, None)` 会让 float64 列上溯成 object → schema 误判 VARCHAR(255) → 财务宽表行大小爆 InnoDB 65535 限制 1118 错误）。正确做法：只 `replace([inf,-inf], NaN)`，NaN 由 `to_sql` 自动写 NULL，保住数值 dtype。

> **数值列统一 DOUBLE，不用 DECIMAL**：金融大额字段（市值/成交额/股本，万元单位可达亿级）DECIMAL 反而比 DOUBLE 大 1-2 字节，且超 M 位会 `Out of range` 硬报错中断 pipeline；DOUBLE 超大值降级科学计数法不报错。省的空间 <1%，不值得换溢出风险。空间大头是因子层（按月采样缩 20 倍 + EDA 后删因子解决）。

**tushare schema 变更**：加列自动 `ALTER`；删列保留旧列；改类型只告警。变更写进 `etl_schema_log`。

### 7.7 data/etl/base.py — 接入层基类

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

# 26 个具体 Calculator 在 data/etl/loader.py，注册表：
from data.etl.loader import CALCULATORS  # {"daily": StockDailyCalculator, ...}
```

### 7.8 加工层基类

```python
from data.panel.base import PanelCalculator    # 自动加 panel_ 前缀
from data.factor.base import FactorCalculator  # 自动加 factor_ 前缀
from data.label.base import LabelCalculator    # 自动加 label_ 前缀

# 注册表（run_compute.py 用）
from data.panel import PANEL_CALCULATORS  # 7 个
from data.factor import CALCULATORS as FACTOR_CALCULATORS  # 6 个
from data.label import CALCULATORS as LABEL_CALCULATORS    # 1 个
```

三个基类都继承 `BaseCalculator`，区别只是表名前缀。子类必须声明 `output_schema`（手写，不用自动推断）。

---

## 8. 更新模式（write_mode）+ 增量策略

> **2026-06-21 重构**：废弃 upsert（逐行慢），统一为 **3 种写入模式**。
> 由 `tushare_apis.json` 的 `incremental_strategy`（取数怎么拆）+ `write_mode`（怎么落库）+ `biz_date_col` / `partition_col`（分区键）共同驱动。

### 8.1 三种写入模式（write_mode）

| write_mode | 落库逻辑 | partition_col | 适用 |
|------------|---------|---------------|------|
| `truncate` | TRUNCATE 整表后 append（全量刷新） | 无 | 基础信息小表 |
| `overwrite` | **先按分区键 DELETE 本批分区，再批量 append**（dataworks INSERT OVERWRITE 语义） | 必填 | 行情/财务/分红 |
| `upsert` | 逐行 ON DUPLICATE KEY UPDATE（**已废弃**，慢，仅兼容保留） | 无 | 不再使用 |

**overwrite 三大保证**（`config/database.py:overwrite_by_partition`）：
- **幂等**：删除维度(partition_col) == 取数维度。重跑 = 删该批分区全部 + 写该批分区全部，逐行一致。
- **不脏**：同事务 DELETE + INSERT，失败回滚，不出现"删了没写"空窗。
- **去重护栏**：落库前按主键去重，有 `update_flag` 留最大版本；**发现重复主键时 WARNING 显式列出被删主键**（不静默吞），便于核查数据源异常。

### 8.2 四类增量策略（incremental_strategy）

| 策略 | 基类 | 适用接口 | partition_col | 取数逻辑 |
|------|------|---------|---------------|---------|
| `full_refresh` | FullRefreshCalculator | trade_cal, stock_basic, index_basic, index_classify, index_member_all, **fund_basic** | 无(truncate) | 每次全量 truncate |
| `by_trade_date` | ByTradeDateCalculator | daily, weekly, monthly, adj_factor, daily_basic, moneyflow, stock_st, suspend_d, sw_daily, index_daily, index_dailybasic, index_weight, **fund_daily, fund_adj, fund_share** | `trade_date` | 逐交易日拉，按 trade_date overwrite |
| `by_period` | ByPeriodCalculator | income, balancesheet, cashflow, **disclosure_date** | `end_date` | 按报告期(period)拉全市场，按 end_date overwrite |
| `by_ex_date` | ByExDateCalculator | dividend | `ex_date` | 按除权除息日逐交易日拉，按 ex_date overwrite |

**增量 vs 回补（关键，2026-06-21 加固）**：所有策略「传 start_date=回补、不传=日常增量」。
- **by_trade_date / full_refresh**：传参=区间回补；不传=从水位次日续跑到今天（水位驱动，久未开机不漏）。
- **by_period（财务+disclosure）**：传参=区间内所有季度末报告期；不传=**增量起点取 `min(水位, today往前4期)`**：
  - 常开机(水位新)→ `today-4期` 更早 → 刷最近 4 期覆盖财报修订；
  - 久未开机(水位旧)→ 水位更早 → 从水位补全不漏中间断档。
- **by_ex_date（dividend）**：传参=区间 ex_date；不传=**增量起点取 `min(水位, today-365天)`**：
  - 常开机→ 回刷近 1 年覆盖分红修订/补录/推迟；久未开机→ 从水位补全。
- **为什么不用纯水位 / 纯固定窗口**：纯水位增量不回头覆盖修订（财报/分红会改）；纯固定窗口"重刷最近N"在久未开机时漏中间断档。`min(水位, today-窗口)` 两全。overwrite 幂等，重叠期重刷无副作用。

**index_weight 月频优化**：index_weight 是月度数据，覆盖 get_data 只对「区间内每月最后交易日」取一次（旧实现逐交易日重复拉同月 23 次 → 13200 重复行 + 浪费 95% API）。

**财务三表 PK**（实测 25 万行 0 重复）：`ts_code + end_date + ann_date + f_ann_date + update_flag`。
- 同报告期多次修订靠 `f_ann_date` 区分（point-in-time 必需，加工层按 snapshot_date 选版防穿越）
- 约束：vip 默认只返回 report_type=1（合并报表）；若拉其他 report_type 须把 report_type 加进 PK
- `disclosure_date` PK = `ts_code + end_date`；`dividend` PK = `ts_code + end_date + ann_date + div_proc + update_flag`

**dividend 用 ex_date 而非 ann_date**：dividend 无 period 参数；只关心真实分红 → ex_date 非空的"实施"记录才被命中，自动过滤预案/股东大会通过阶段，同时修掉旧实现 ann_date=null 漏数 bug。

### 8.3 数据同步入口（scripts/sync.py）—— 日常补数主命令

`scripts/sync.py` 是统一的拉数入口（替代直接调 run_ingest），尤其适合「本地、不定期开机」：

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

- 内部按 4 类策略分 Phase 执行（trade_cal 最先 + reload 缓存 → 行情 → 财务 → 分红）。
- 全新空库首次：先 `--start` 回补全历史（无参增量对空库只拉今天/最近窗口）。
- 逐年回补历史：一年一条 `--start YYYY0101 --end YYYY1231`，断了重跑无副作用。

---

## 9. 策略层（portfolio / backtest / signals）

三者**共用同一套策略代码**，避免回测/实盘两套。

### 9.1 portfolio/ — 组合构建

```python
from portfolio.strategy import CrossSectionalMomentumStrategy
from portfolio.universe import DEFAULT_ETF_POOL, get_etf_universe

# DEFAULT_ETF_POOL: 16 个 A股 ETF（宽基6 + 行业7 + 风格3）
# get_etf_universe("broad" / "industry" / "style" / "all") → 代码列表

strategy = CrossSectionalMomentumStrategy(
    lookback=20,          # 动量回看窗口
    vol_window=20,        # 波动率窗口
    vol_threshold=0.4,    # 年化波动率过滤阈值
    max_positions=5,      # 持仓数量上限
    max_weight=0.3,       # 单标的仓位上限
    max_drawdown=0.10,    # 回撤止损线
    universe_category="all",
)

# 计算某日目标权重 {ts_code: weight}
weights = strategy.compute_target_weights(end_date="20240131", current_drawdown=0.0)
weights = strategy.apply_constraints(weights)  # 单标的上限 + 归一化
```

逻辑：截面动量排序 → 波动率过滤 → 风控约束（仓位上限 + 持仓数上限 + 回撤止损）→ 等权配置。

### 9.2 backtest/ — 回测引擎

```python
from backtest.engine import VectorizedBacktester
from backtest.metrics import compute_metrics

bt = VectorizedBacktester(strategy=strategy, rebalance_freq="W", commission=0.0005)
result = bt.run(start_date="20240101", end_date="20241231")
# result = {
#   "equity_curve": DataFrame(trade_date, nav, daily_return),
#   "trades": DataFrame(trade_date, ts_code, weight, nav),
#   "metrics": {total_return, annual_return, annual_volatility, sharpe, max_drawdown, calmar, win_rate},
# }

# compute_metrics 纯 Python 实现，接受 list[float] 或 pd.Series
m = compute_metrics([0.01, -0.02, 0.03])
```

### 9.3 signals/ — 调仓信号

```python
from signals.generator import SignalGenerator

gen = SignalGenerator(strategy=strategy, strategy_name="etf_momentum")
signals = gen.generate()              # 取最新交易日，生成 buy/sell/hold 信号
gen.save(signals)                     # 落 signal_rebalance 表
```

`SignalGenerator` 内部调 `strategy.compute_target_weights`，与回测共用逻辑。

---

## 10. 约定与硬约束

### 10.1 约定（必须遵守）

| 事项 | 约定 |
|------|------|
| 语言 | Python 3.11+（推荐 3.14） |
| 数据源 | tushare 为主，不用 akshare。开发期用 MCP 探查字段，生产用 tushare Python 包 |
| 存储 | MySQL 8.x+，库名 `stock`，SQLAlchemy 2.x + pymysql |
| 计算结构 | 一律 `BaseCalculator` 子类，统一 `update(start_date, end_date, **params)` |
| Schema | schema-as-code：接入层自动推断，加工层手写 `output_schema`。不用 `table_schemas.sql` |
| 增量 | 三类策略（trade_date / ann_date / full_refresh），biz_date 抽象 + etl_biz_date 水位表 |
| 配置 | Python + `os.getenv` + JSON + `.env`，不引 yaml |
| 日期 | 统一 `yyyymmdd` 字符串，入库转 DATE；用 `core/dates.py` 判断交易日 |
| 路径 | `Path(__file__)` 相对定位，不出现绝对路径 |
| 依赖 | `requirements.txt` 用 `>=,<` 范围，不锁 `==` |
| 分区 | 不做（数据量未到） |
| Docker | 不做（云迁移由 `os.getenv` 覆盖） |

### 10.2 硬约束

1. **接入层与加工层解耦**：`data/etl/` 只放 tushare 1:1 复刻，`data/panel+factor+label/` 只放自定义计算，不混。
2. **加工层用 panel 抽象**：panel / factor / label 三目录，粒度用表名前缀标，不按 alpha/risk 分。
3. **统一 `update`**：用单个 `update(start_date, end_date, **params)`。不传=增量、传=回补。
4. **biz_date 抽象**：每个 Calculator 声明 `biz_date_col`（trade_date/ann_date/snapshot_date）。频率不进 `update` 签名（调度频率走 schedule，数据频率走 get_data）。
5. **新计算 = Calculator 子类**，落库走 `save_to_database`，幂等靠 `write_mode`。
6. **schema-as-code**：接入层自动推断（见 §7.6 类型推断规则），加工层手写 `output_schema`。数值列统一 DOUBLE（不用 DECIMAL）；接入层 `process_data` 不得把 NaN 转 None（破坏数值列 dtype）。
7. **tushare_api 以 MCP 为主**：开发期用 MCP 探查字段，不人肉维护 `fields`。
8. **四类增量**：行情→by_trade_date(overwrite/trade_date)，财务三表+disclosure→by_period(overwrite/end_date)，分红→by_ex_date(overwrite/ex_date)，基础信息→full_refresh(truncate)。废弃 upsert。
9. **配置走 `.env`**：密钥 `os.getenv` 默认值留空，不硬编码。
10. **路径用 `Path(__file__)`**，不出现绝对路径。
11. **改动小而可回滚**：一次一个模块，便于 review 和 git。
12. **Python 3.14 兼容**：库版本用 `>=` 范围，遇到兼容问题优先修代码而非降版本。

---

## 11. 投资策略方向（业务共识，不要改方向）

1. **因子降级为风控诊断工具，不再当 alpha 来源。** 因子回答"组合安全吗、分散吗、何时该警惕"，用途：风险归因、尾部预警、拥挤度、压力测试。
2. **主战场是 ETF 截面轮动 + 宽基底仓。** 轮动逻辑：截面动量排序 + 波动率过滤 + 风控约束（单标的仓位上限、组合回撤止损）。
3. **主观 + 量化分层。** 人管方向（核心池/观察池/回避池、风险预算），系统管节奏（周频排序、仓位约束、调仓建议）。
4. **资产范围分阶段扩。** Phase 1 纯 A股 ETF；Phase 2 港股科技/黄金/债券 ETF。

---

## 12. Git 仓库

- 远程：`https://github.com/yuchenhu/nanoquant`
- 主分支：`main`
- 提交规范：中文 commit message，简洁说明"做了什么"
- 敏感信息：`.env` 进 `.gitignore`，绝不提交；`.env.example` 进 git 作为模板
- AI Agent 协作：完成可独立验证的模块后建议作者提交，不自动 commit/push

---

## 13. 新功能挂接指南

- **扩 tushare 数据**：MCP 探查字段 → 改 `config/tushare_apis.json`（标 `incremental_strategy` + `biz_date_col`）→ `data/etl/loader.py` 加 Calculator（不手写 schema，自动推断）。
- **扩加工层计算**：先问"角色是什么"：
  - 实体×时间对齐宽表（因子/标签底座） → `data/panel/`，手写 `output_schema`，声明 `biz_date_col`
  - 实体×日因子 → `data/factor/`
  - 实体×日标签 → `data/label/`
  - 复用 `core/preprocessing.py`（winsorize/neutralize/rank）
- **扩策略**：`portfolio/` 加策略类，`backtest/` 封装回测，`signals/` 复用策略逻辑生成信号。三者共用同一套策略代码。
- **扩调度**：`pipeline/schedule_*.json` 加任务配置，`scripts/` 复用 `run_ingest.py`/`run_compute.py`。
- **新表**：不写 SQL，Calculator 里声明 schema 自动建表。

---

## 14. 开放问题（遇到时询问作者）

1. **ETF 池范围**：`DEFAULT_ETF_POOL` 是默认值，作者可调整。
2. **回测结果落库粒度**：每日净值 + 调仓记录都落库？还是只落最终结果？（当前 `run_strategy.py` 只打印不落库回测结果，信号落 `signal_rebalance`）
3. **财务数据历史深度**：全量回补从哪年开始（2010? 2015?）？影响首次拉数耗时。
4. **调度框架**：当前裸脚本 + Windows 任务计划，长期可迁 Prefect 2.x。

---

## 15. 验收测试

```bash
# 跑全部验收测试（Step 3-10）
$env:PYTHONIOENCODING="utf-8"; $env:PYTHONPATH="."
python tests/test_step3_pipeline.py
python tests/test_step4_tushare_apis.py
python tests/test_step5_etl.py
python tests/test_step6_panel.py
python tests/test_step7_factor_label.py
python tests/test_step8_scripts.py
python tests/test_step9_cleanup.py
python tests/test_step10_strategy.py
```

测试覆盖：循环依赖检测、tushare 配置完整性、ETL Calculator 结构、panel/factor/label 迁移、入口脚本、旧文件清理、策略层闭环。
