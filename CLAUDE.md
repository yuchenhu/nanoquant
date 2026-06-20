# nanoquant — 项目上下文与开发指南 (CLAUDE.md)

> 给 AI Agent：本文件描述 nanoquant 的目标架构、开发原则，以及**如何在旧框架（`data/` 为主）基础上解决痛点、做增量开发**。
>
> **最高原则：个人够用 + 云迁移友好 + 前期高维护后期中低维护。** 一切设计决策服从这三条。能复用就复用，不合理就改，不为了"不动旧代码"而留坑。
>
> 本文件分两类内容：**「约定」= 必须遵守**；**「建议」= 可讨论**（作者保留最终决定权）。不要把「建议」当铁律。

---

## 1. 一句话目标

把 nanoquant 建成面向 **ETF 截面轮动**的完整闭环：数据接入 → 因子/风控诊断 → 回测 → 信号。本轮先做 MVP，跑通"ETF 数据 → 动量因子 → 截面轮动回测 → 调仓信号"。

---

## 2. 架构（动手前必读）

项目根目录：`nanoquant/`，作者 Hu Yuchen。**基于旧框架 `data/` 目录开发**，本轮重点解决三大痛点（见 2.0），不做大规模重构。

### 2.0 旧框架三大痛点 + 本轮解决方案（必读）

| 痛点 | 旧框架现状 | 本轮解决 |
|------|----------|---------|
| **tushare_api 配置人肉维护** | `data/config/tushare_api.json` 手写字段列表，tushare 加列/改字段要人肉同步，非常麻烦 | **以官方 tushare MCP 为主**：开发期用 MCP（`mcp_tushareMcp`，200+ 接口 schema）探查接口字段/参数/返回值，自动生成或校验 `tushare_apis.json` 的 `fields`；配 **schema-as-code**（见 2.6），建表/改表由代码自动管理。生产仍用 tushare Python 包。MCP 是"探查 + 校验"工具，不是生产数据通道。 |
| **api 层与加工层耦合 + 加工层粒度不齐** | 旧 `data/sql/` 里既有个股×日（stock_daily_wide）、市场×日（market_sentiment）、月频（mv_monthly），粒度混在一起；接入层和加工层也没清晰边界 | **接入层（`data/etl/`）与加工层（`data/panel+factor+label/`）解耦、并列**。加工层用**量化通用的 panel（面板数据）抽象**分目录：`panel/`（实体×时间对齐宽表）、`factor/`（因子）、`label/`（标签）。粒度差异（个股/市场/行业、日/月/快照）用**表名前缀**显式标出，不靠目录硬分明细/汇总。见 2.1-2.2。 |
| **回补/增量入口不清 + 两函数冗余** | `data/etl/loader.py` 有 `history_backfill` 但无统一 `incremental_update`；且二者功能其实相同（都是按日期范围拉+落库），冗余 | **合并成单个 `update(start_date, end_date, **params)`**：不传日期=从 biz_date 水位续跑（增量），传日期=按 biz_date 区间回补。新增 `etl_biz_date` 表记录水位，支持断点续跑。入口脚本**按接入/加工两层各一个**：`run_ingest.py` + `run_compute.py`。废弃 Airflow DAG。 |

### 2.1 目录与职责

```
nanoquant/
├── requirements.txt              # 依赖，>=,< 范围，Python 3.14
├── .env.example                  # 环境变量模板（进 git）
├── .env                          # 本地密钥（进 .gitignore）
│
├── config/                       # 全局配置（从 data/config 提升到顶层）
│   ├── settings.py               # 平台配置 + dotenv 加载 + TUSHARE_TOKEN
│   ├── database.py               # DB_CONFIG + engine + 读写函数
│   └── tushare_apis.json         # tushare 接口配置（含 incremental_strategy 标注）
│
├── data/                         # 旧框架主体，保留
│   ├── etl/                      # ★ 接入层（tushare 1:1 复刻）
│   │   ├── extractor.py          # tushare 拉数
│   │   └── loader.py             # 接入层 Calculator
│   │
│   ├── panel/                    # ★ 加工层 - 面板数据（实体×时间对齐宽表）
│   │   ├── stock_daily_panel.py        # 个股×日 行情宽表（行情+复权+市值+ST+行业+指数成分）
│   │   ├── market_sentiment_daily.py   # 市场×日 情绪
│   │   ├── market_sentiment_monthly.py # 市场×月 情绪
│   │   ├── stock_mv_monthly.py         # 个股×月 市值
│   │   ├── stock_percentiles.py        # 个股×日 历史百分位（窗口统计量）
│   │   ├── fin_statement_panel.py      # 个股×报告期 财务三表合并
│   │   └── fin_indicator_snapshot.py   # 个股×快照日 财务指标（biz_date=snapshot_date）
│   │
│   ├── factor/                   # ★ 加工层 - 因子（实体×日，从 panel 计算）
│   │   ├── price_volume_20d.py
│   │   ├── high_low_spread.py
│   │   ├── industry_resonance.py
│   │   ├── moneyflow_imbalance.py
│   │   ├── trader_structure.py
│   │   └── valuation.py
│   │
│   ├── label/                    # ★ 加工层 - 标签（实体×日，从 panel 计算）
│   │   └── forward_returns.py    # 未来 N 日收益率
│   │
│   ├── config/                   # 旧配置（迁移到顶层 config/ 后删）
│   ├── utils/                    # 旧工具（迁移到 core/ 后删）
│   └── workflows/                # 旧 Airflow DAG（废弃，迁移后删）
│
├── core/                         # 共享核心（从 data/utils 抽出，跨层复用）
│   ├── calculator.py             # BaseCalculator（统一 update + biz_date 抽象）
│   ├── schema.py                 # schema-as-code 工具（推断 + 生成 DDL + 演化）
│   ├── dates.py                  # 交易日工具
│   └── preprocessing.py          # winsorize / neutralize / rank
│
├── pipeline/                     # 调度与增量策略
│   ├── runner.py                 # JSON 配置驱动的调度执行器
│   ├── schedule_ingest.json      # 接入层任务调度（含调度频率）
│   ├── schedule_compute.json     # 加工层任务调度（含调度频率）
│   └── incremental/              # 三类增量策略基类
│       ├── base.py               # BaseIncremental + etl_biz_date 表
│       ├── by_trade_date.py      # 行情类
│       ├── by_ann_date.py        # 财务/事件类
│       └── full_refresh.py       # 基础信息类
│
├── portfolio/                    # 组合构建（ETF 截面轮动、仓位约束）
├── backtest/                     # 回测引擎（backtesting.py 封装 + 指标计算）
├── signals/                      # 调仓信号生成（与回测共用策略逻辑）
├── research/                     # 研究 notebook + 评估指标
├── scripts/                      # 运行脚本
└── tests/
```

**说明**：`data/etl/`（接入层）与 `data/panel+factor+label/`（加工层）**解耦、并列**。加工层用 panel 抽象分三个目录，不按 alpha/risk 分（因子在新体系里降级为风控诊断工具，不强行分收益/风险）。`config/` `core/` `pipeline/` 提升到顶层，跨层复用。`portfolio/ backtest/ signals/` 是本轮新增的策略层。

### 2.2 分层规则 + 加工层用 panel 抽象（一眼区分数据来源）

| 层 | 目录 | 数据来源 | 扩充方式 |
|----|------|---------|---------|
| **接入层** | `data/etl/` | tushare API 1:1 | 改 `config/tushare_apis.json` + 写 Calculator |
| **加工层** | `data/panel/` `data/factor/` `data/label/` | 读接入层表，自己算 | 写 Calculator |
| **策略层** | `portfolio/` `backtest/` `signals/` | 读加工层表 | 写策略代码 |
| **调度层** | `pipeline/` | 编排上述 | 加 task |

**加工层用量化通用的 panel 抽象分三个目录**（解决旧 `data/sql/` 粒度混乱痛点）：

| 子目录 | 角色 | 内容 | 例子 |
|--------|------|------|------|
| `panel/` | **面板数据**（实体×时间对齐的二维宽表） | 接入层多张窄表 JOIN/聚合成对齐宽表。实体=个股/市场/行业/基金，时间=日/月/报告期/快照日。这是因子和标签的输入底座 | `stock_daily_panel`、`market_sentiment_daily`、`stock_mv_monthly`、`stock_percentiles`、`fin_statement_panel`、`fin_indicator_snapshot` |
| `factor/` | **因子**（实体×日的预测/诊断特征） | 从 panel 计算的因子值，用于选股/风控诊断 | `price_volume_20d`、`valuation` |
| `label/` | **标签**（实体×日的预测目标） | 从 panel 计算的预测目标 | `forward_returns`（未来 N 日收益率） |

**为什么用 panel 而不用 dwd/dws**：dwd/dws 是 DataWorks 数仓黑话，量化领域更通用的概念是 **panel（面板数据）= 实体×时间对齐表**。旧框架"粒度不齐"的病根不是明细/汇总分不清，而是**实体维度混了**（个股×日、市场×日、月频堆在一个 `sql/` 里）。所以第一分类轴用"角色"（panel/factor/label），**粒度差异用表名前缀显式标出**，不靠目录层级硬分明细/汇总。

**表名前缀约定**（粒度一眼看清）：
- 实体维度前缀：`stock_`（个股）、`market_`（市场聚合）、`industry_`（行业）、`fund_`（基金/ETF）、`fin_`（财务）
- 频率后缀：`_daily` / `_monthly`，或快照类用 `_snapshot`
- 例：`stock_daily_panel`（个股×日）、`market_sentiment_daily`（市场×日）、`stock_mv_monthly`（个股×月）、`fin_indicator_snapshot`（个股×快照日）

**判断规则**：
- 要扩 tushare 数据 → 改 `data/etl/` + `config/tushare_apis.json`
- 要扩自定义计算 → 先问"角色是什么"：
  - 实体×时间的对齐宽表（因子/标签的输入底座） → `data/panel/`，表名带实体+频率前缀
  - 实体×日的因子 → `data/factor/`
  - 实体×日的标签 → `data/label/`

**不按 alpha/risk 分**：因子在新体系里降级为风控诊断工具（见第4节），不强行分收益/风险，避免过度设计。需要区分时用文件名前缀（如 `factor/momentum_*.py`、`factor/risk_volatility.py`）即可。

### 2.3 表名约定

- 接入层：`ingest_<domain>_<api>`，如 `ingest_equities_daily`、`ingest_financial_income`
  - **旧框架表名**（如 `stock_daily`、`income`、`balancesheet`）保留兼容，新增表用 `ingest_` 前缀
- 加工层（角色前缀 + 实体/频率在名字里体现）：
  - panel：`panel_<entity>_<name>_<freq>`，如 `panel_stock_daily`、`panel_market_sentiment_daily`、`panel_fin_indicator_snapshot`
  - factor：`factor_<name>`，如 `factor_price_volume_20d`、`factor_valuation`
  - label：`label_<name>`，如 `label_forward_returns`
  - **旧框架表名**（如 `stock_daily_wide`、`mv_monthly`）保留兼容，新增表用前缀
- 策略层：`portfolio_<name>` / `backtest_<name>` / `signal_<name>`

查 `SHOW TABLES` 一眼分类。**不分区**（数据量级未到，索引够用）。

### 2.4 配置约定（不上 yaml）

- 配置写在 **Python 模块** + **JSON**：`config/settings.py`、`config/database.py`、`config/tushare_apis.json`、`pipeline/schedule_*.json`。
- 敏感信息一律 `os.getenv('KEY', '')`，**默认值留空**，本地用 `.env` + `python-dotenv` 加载。`.env` 进 `.gitignore`，`.env.example` 进 git。
- 数据库：**MySQL，库名 `stock`**，SQLAlchemy 2.x + pymysql，全局 `engine` 在 `config/database.py`。

### 2.5 核心计算模式：BaseCalculator + 统一 update（所有计算都按这个写）

`core/calculator.py` 定义 `BaseCalculator`，所有接入层/加工层的计算都是它的子类。

**子类声明**：`table_name`、`primary_keys`、`write_mode`、`biz_date_col`（业务日期列名）。

**子类实现**：`get_data(start_date, end_date, **params) -> DataFrame`（取数）+ `process_data(data, **params) -> DataFrame`（计算）。

**统一入口 `update`（合并旧的 `history_backfill` + `incremental_update`，二者功能相同，去冗余）**：

```python
class BaseCalculator:
    biz_date_col = "trade_date"   # 子类覆盖：trade_date / ann_date / snapshot_date

    def update(self, start_date=None, end_date=None, **params):
        """把数据更新到覆盖 [start_date, end_date] 的 biz_date 区间。

        一个函数同时干增量和回补：
        - start_date=None → 从 etl_biz_date 当前水位的次日续跑（增量，自动定时任务用）
        - start_date 指定 → 从该 biz_date 回补（手动补数用）
        - end_date=None  → 到今天
        - **params：非时间参数（lookback_window / holding_horizons 等），透传给 get_data/process_data

        start_date/end_date 永远是 biz_date 区间；biz_date 是哪一列由子类 biz_date_col 决定。
        """
        start_date = start_date or self._next_after_biz_date()   # 增量起点 = 水位次日
        end_date = end_date or get_today_str()

        raw = self.get_data(start_date, end_date, **params)
        if raw is None or raw.empty:
            return pd.DataFrame()
        result = self.process_data(raw, start_date=start_date, end_date=end_date, **params)
        if result is None or result.empty:
            return pd.DataFrame()
        self.save_to_database(result)
        self._set_biz_date(result[self.biz_date_col].max())      # 水位 = 本批 biz_date 最大值
        return result
```

**关键约定**：
- **`start_date`/`end_date` = biz_date 区间**（抽象的业务日期窗口，不绑定具体列名）。"biz_date 是哪一列"由子类 `biz_date_col` 决定：行情类=`trade_date`，财务流水=`ann_date`，财务快照=`snapshot_date`。snapshot 不是特例，就是 biz_date 列名不同（见 2.9）。
- **`update` 把整个 biz_date 区间交给 `get_data`**，逐日/逐月/逐快照怎么取，是子类 `get_data` 内部实现（如 snapshot 类在自己 `get_data` 里按 snapshot_date 逐日回看最新财报）。不在通用签名里写死按日循环，既不污染签名又保留灵活性。
- **`process_data` 会拿到 `start_date`/`end_date`**（update 透传），加工层可用来按 biz_date 区间查辅助表（如宽表 JOIN 时按区间查 adj_factor）。接入层 `process_data` 忽略这两个参数（只做日期转换）。
- **`**params` 只装非时间参数**（lookback_window、holding_horizons、index_lookback_window 等），透传给 get_data/process_data。
- 幂等靠 `write_mode`（`upsert` / `overwrite` / `truncate` / `append`）。
- **删掉 `history_backfill` 和 `incremental_update`**，统一用 `update`。

**频率拆成两个独立概念，都不进 `update` 签名**：
1. **调度频率（schedule 控制，自动定时触发）**：写在 `schedule_*.json` 的 `daily/weekly/monthly`，控制定时任务多久触发一次 `update`。例：财务 ann_date 数据可配成周更或月更，由 schedule 决定。自动触发时不传日期 → 从水位次日续跑 = **增量**。
2. **数据频率（Calculator 固有属性）**：数据本身是日/月/快照粒度（`stock_daily_panel` 是日频、`stock_mv_monthly` 是月频、`fin_indicator_snapshot` 是快照），由子类 `get_data` 内部处理，不需要调用时传。

**两种用法对照**（贴 DataWorks 心智，但比它还少传一个频率参数）：
| 场景 | 调用 | 行为 |
|------|------|------|
| 自动定时任务 | `update()`（不传日期，schedule 按频率触发） | 从 biz_date 水位次日续跑到今天 = **增量** |
| 手动灵活补数 | `update("20210101", "20251231")` | 按指定 biz_date 区间回补，绕开水位 |

### 2.6 Schema-as-code（解决痛点 1 的延伸：不用手写建表 SQL）

**核心思想**：不写 `table_schemas.sql` 这种手写建表文件，建表/改表逻辑由代码自动管理。

**为什么**：tushare 经常加列（如利润表今年多了 `credit_impa_loss`），手写 SQL 要人肉同步，易漏易错。schema-as-code 让 schema 跟着数据走，数据有什么列，表就有什么列。

**怎么做**（`core/schema.py` 实现）：

**接入层（自动推断）**：
1. 拉数得 DataFrame
2. 表不存在 → `infer_schema_from_df(df)` 推断类型 + `generate_create_table_sql` → 执行建表
3. 表存在 → 比对 DataFrame 列与库表列，按下方策略演化
4. 写入

**类型推断约定**（`core/schema.py`）：
- `object` → `VARCHAR(50)`，但列名含 `_date` / `date_` → `DATE`（tushare 日期是 yyyymmdd 字符串，入库前转 DATE）
- `int64` → `BIGINT`
- `float64` → `DOUBLE`
- 个别需微调：Calculator 加 `type_overrides = {'desc': 'TEXT'}` 覆盖

**tushare schema 变更处理策略**：

| 场景 | 处理 | 理由 |
|------|------|------|
| tushare **加列** | 自动 `ALTER TABLE ADD COLUMN` | 新列默认 NULL，不影响下游 |
| tushare **删列** | **不删**，保留旧列 | 下游可能依赖，删了丢数据 |
| tushare **改列类型** | 日志告警，不自动改 | 可能丢数据，需人工迁移 |
| tushare **改列含义** | 无法自动检测 | 靠跑数后校验行数/值域 |

每次 schema 变更写进 `etl_schema_log` 表留痕（table_name, change_type, column_name, old_value, new_value, detected_at）。

**加工层（手写 output_schema）**：
- 加工层列少（5-20 列），手写 `output_schema` dict 声明列名+类型，首次写入自动建表。
- 加列改 dict，下次跑自动 `ALTER TABLE ADD COLUMN`。
- 例子：
  ```python
  class PriceVolume20DCalculator(BaseCalculator):
      table_name = "factor_price_volume_20d"
      biz_date_col = "trade_date"
      output_schema = {
          "ts_code": "VARCHAR(20)",
          "trade_date": "DATE",
          "volatility_20d": "DOUBLE",
          "turnover_mean_20d": "DOUBLE",
          # ...
      }
      primary_keys = ["ts_code", "trade_date"]
      write_mode = "upsert"
  ```

**与旧 `table_schemas.sql` 的关系**：旧框架手写的 `data/config/table_schemas.sql` 废弃，迁移后删。所有新表由 schema-as-code 自动管理。

### 2.7 tushare_api 配置：以官方 MCP 为主（解决痛点 1）

**废弃人肉维护 `tushare_api.json` 字段列表的做法**。

**新流程**：
1. **开发期探查**：用 tushare 官方 MCP（`mcp_tushareMcp`）探查接口的字段、参数、返回值。MCP 提供 200+ 接口的 schema，直接查就行，不用翻文档。
2. **自动生成/校验**：基于 MCP 探查结果，生成或校验 `config/tushare_apis.json` 的 `fields` 列表。tushare 加列时，用 MCP 重新探查，diff 出新列，更新配置。
3. **生产拉数**：仍用 tushare Python 包（`tushare.pro_api`），不用 MCP。MCP 是开发期工具，不是生产数据通道。

**`config/tushare_apis.json` 结构**（每个接口一项）：
```json
{
  "daily": {
    "api_name": "daily",
    "domain": "equities",
    "table_name": "ingest_equities_daily",
    "incremental_strategy": "by_trade_date",
    "biz_date_col": "trade_date",
    "write_mode": "upsert",
    "primary_keys": ["ts_code", "trade_date"],
    "params": {
      "trade_date": "",
      "start_date": "",
      "end_date": "",
      "fields": "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount",
      "limit": 6000
    },
    "description": "A股日线行情"
  }
}
```

**字段说明**：
- `incremental_strategy`：`by_trade_date` / `by_ann_date` / `full_refresh`，决定增量逻辑（见 2.8）
- `biz_date_col`：业务日期列名，`trade_date` / `ann_date` / null（注入到 Calculator 的 `biz_date_col`）
- `vip_api_name`（可选）：财务 API 的 `_vip` 变体，按 period 批量拉，仅用于大范围回补
- `params.fields`：从 MCP 探查得来，不再人肉维护

### 2.8 三类增量策略（解决痛点 2 + 财务数据增量问题）

`pipeline/incremental/` 三个策略，由 `tushare_apis.json` 的 `incremental_strategy` 字段驱动，决定 `get_data` 怎么按 biz_date 区间拉：

| 策略 | 适用接口 | biz_date_col | 逻辑 |
|------|---------|--------------|------|
| `by_trade_date` | daily, daily_basic, adj_factor, moneyflow, index_daily, sw_daily, fund_daily, stock_st, index_weight | `trade_date` | 区间内逐交易日拉，水位 = max(trade_date) |
| `by_ann_date` | income, balancesheet, cashflow, dividend, fina_indicator, forecast, namechange | `ann_date` | 按 ann_date 区间拉，回看 7 天覆盖修订；水位 = max(ann_date) |
| `full_refresh` | stock_basic, trade_cal, index_basic, fund_basic, etf_basic | 无 | 每次 truncate 全量（数据量小，无 biz_date） |

**财务数据增量关键点**（呼应作者痛点）：
- 财报会被修订，同一 end_date 有多条记录，用 `update_flag` 区分。主键必须含 `(ts_code, ann_date, end_date, report_type, update_flag)`，upsert 保留修订历史。
- **不用 `period` 遍历季度**（旧代码的错误做法），改用 `start_date`/`end_date`（公告日范围，即 ann_date 区间）。
- 大范围回补时用 `_vip` 接口按 period 一次拉一个季度全部股票（需 5000 积分），远快于按股票遍历。

### 2.9 biz_date 抽象 + etl_biz_date 水位表（解决痛点 3，对齐 max_compute 术语）

**术语对齐 max_compute**：用 **biz_date（业务日期）** 这个口径。biz_date 指数据所属的业务日期。每张表的 biz_date 落在哪一列由 `biz_date_col` 声明：

| 数据类型 | biz_date_col | 含义 |
|---------|--------------|------|
| 行情类 | `trade_date` | 交易日 |
| 财务流水（三表/指标/预告） | `ann_date` | 公告日 |
| 财务快照（自定义指标） | `snapshot_date` | 快照日（作者自定义的"在某天能看到的最新财报"口径） |

**核心**：`update(start_date, end_date)` 的区间永远是 biz_date 区间，但 biz_date 是哪一列由子类决定。这样 `snapshot_date` 不是特例——它就是财务快照表的 biz_date 列名，自动纳入统一的增量/回补/水位框架。

**etl_biz_date 水位表**（旧框架没有，本轮新增）。每个表记录"已拉到的最大 biz_date"，下次从次日续跑，支持断点续跑：

```sql
CREATE TABLE etl_biz_date (
  table_name VARCHAR(100) PRIMARY KEY,
  biz_date_col VARCHAR(30),         -- 'trade_date' / 'ann_date' / 'snapshot_date' / null
  biz_date VARCHAR(30),             -- 已拉到的最大业务日期 yyyymmdd
  last_updated DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_rows BIGINT DEFAULT 0,
  status VARCHAR(20) DEFAULT 'ok',
  INDEX idx_status (status)
);
```

**说明**：`biz_date` 是"数据已经覆盖到哪天"的标记，不是"今天日期"。`update` 不传 start_date 时从 `biz_date` 次日续跑，避免重复拉取。`full_refresh` 类无 biz_date，每次全量。

### 2.10 入口脚本：按接入/加工两层各一个（解决痛点 3）

**两个统一函数已合并为 `update`**（见 2.5），入口脚本**按层分两个**，对齐作者"api 与加工层清晰区分"的诉求：

| 脚本 | 层 | 用途 |
|------|-----|------|
| `scripts/run_ingest.py` | 接入层 | 跑 `data/etl/` 的 Calculator |
| `scripts/run_compute.py` | 加工层 | 跑 `data/panel+factor+label/` 的 Calculator，按依赖拓扑排序 |

**用法**（每个脚本：传日期=回补，不传=增量；贴 DataWorks 心智）：
```bash
# 增量：各任务从自己水位续跑到今天（自动定时任务调这个）
python scripts/run_ingest.py
python scripts/run_compute.py

# 回补：指定 biz_date 区间
python scripts/run_ingest.py 20250101 20251231
python scripts/run_compute.py 20210101 20251231

# 只补单个任务
python scripts/run_compute.py --only=panel.StockDailyPanel 20210101 20251231

# 只补某层某 domain（接入层）
python scripts/run_ingest.py --only=financial 20210101 20251231
```

### 2.11 调度（废弃 Airflow DAG）

- **本轮**：裸脚本 + Windows 任务计划。`scripts/run_ingest.py` + `run_compute.py` 是每日入口，`scripts/daily_task.bat` 调它们。
- **废弃** Airflow 风格 DAG（旧 `data/workflows/quant_pipeline_dag.py` 迁移后删）。
- **调度配置 = 调度频率**：`pipeline/schedule_ingest.json` + `pipeline/schedule_compute.json`，按 `frequency`（daily/weekly/monthly/irregular）组织任务，**控制定时任务多久触发一次 `update`**（见 2.5 频率两层概念）。`depends_on` 声明依赖，runner 拓扑排序 + 上游表行数检查。
- **runner 容错**：`--only=module.Class` 跨频率搜索时，若某频率下没有该任务，跳过该频率而非报错（便于单任务调试）。
- **长期**：Prefect 2.x（已在依赖里），等 MVP 跑通后再迁。Prefect Cloud 上云零成本，符合云迁移友好原则。

### 2.12 Git 仓库（已定）

- **远程仓库**：`https://github.com/yuchenhu/nanoquant`
- **主分支**：`main`
- **提交规范**：中文 commit message，简洁说明"做了什么"。如 `新增 ingest/equities/daily 接入`、`修复 BaseCalculator engine bug`。
- **敏感信息**：`.env` 进 `.gitignore`，绝不提交。`.env.example` 进 git 作为模板。
- **AI Agent 协作**：Agent 完成一个可独立验证的模块后，可建议作者提交；不要自动 commit/push，等作者确认。

### 2.13 已有能力清单（旧框架保留，不重复造）

| 能力 | 位置 | 说明 |
|------|------|------|
| tushare 拉数 | `data/etl/extractor.py` | 保留逻辑 |
| BaseCalculator | `data/utils/base_calculator.py` → `core/calculator.py` | 合并 `history_backfill`+`incremental_update` 为统一 `update`，加 `biz_date_col` 抽象 |
| 交易日工具 | `data/utils/date_utils.py` → `core/dates.py` | 保留 |
| 预处理 | `data/utils/preprocessing.py` → `core/preprocessing.py` | 保留 |
| 面板宽表（panel） | `data/sql/stock_daily_wide.py`、`mv_monthly.py`、`market_sentiment_*.py`、`stock_percentiles.py`、`financial_statements_snapshot.py`、`financial_indicators_snapshot.py` → `data/panel/` | 保留，按 panel 角色归类，表名带实体+频率前缀 |
| 因子 | `data/factor/*.py` | 保留 |
| 标签 | `data/label/forward_returns.py` | 保留 |
| DB 操作 | `data/config/database.py` → `config/database.py` | 保留 + 清空硬编码 |

---

## 3. 本轮新增模块（解决痛点 + MVP）

| 模块 | 现状 | 本轮做到 |
|------|------|---------|
| tushare_api 维护 | 人肉维护字段 | 以官方 MCP 为主 + schema-as-code 自动建表/演化 |
| 加工层解耦 + 粒度 | 接入/加工耦合，旧 `data/sql/` 粒度混乱 | 接入层与加工层并列；加工层 panel/factor/label，粒度用表名前缀 |
| 回补/增量入口 | `history_backfill`/`incremental_update` 冗余，入口不清 | 合并为单个 `update` + biz_date 水位表；入口按层 `run_ingest.py`+`run_compute.py` |
| 调度 | Airflow DAG（废弃） | JSON 配置（调度频率）+ runner，长期 Prefect |
| ETF 数据 | 无 | `data/etl/` 加 fund_basic, fund_daily, etf_basic |
| ETF 因子 | 无 | `data/factor/etf_momentum.py` |
| 风控诊断 | 部分（市场热度/百分位） | 复用 `data/panel/` + `data/factor/`，不单建 risk/ |
| 策略/回测 | 完全缺失 | `portfolio/` + `backtest/`（backtesting.py） |
| 调仓信号 | 无 | `signals/`（与回测共用策略逻辑） |
| 配置安全 | 硬编码密码/token | `.env` + dotenv，默认值清空 |

---

## 4. 投资策略方向（业务共识，不要改方向）

1. **因子降级为风控诊断工具，不再当 alpha 来源。** AI 普及让信息差变小、经典因子拥挤、个人拼不过机构。因子在新体系里回答"组合安全吗、分散吗、何时该警惕"，用途：风险归因、尾部预警（波动率倒转/换手异常/相关性紧缩）、拥挤度（估值分位数做反向指标）、压力测试。
2. **主战场是 ETF 截面轮动 + 宽基底仓。** ETF 自带一层分散化，决策落在资产配置层面。轮动逻辑：截面动量排序 + 波动率过滤 + 风控约束（单标的仓位上限、组合回撤止损）。
3. **主观 + 量化分层（方向先记录，MVP 不强求实现）。** 人管方向（核心池/观察池/回避池、风险预算），系统管节奏（周频排序、仓位约束、调仓建议）。系统给建议、人来确认，记录否决以便复盘。
4. **资产范围分阶段扩。** Phase 1 纯 A股 ETF（行业/风格/宽基）；Phase 2 再考虑港股科技、黄金、债券 ETF。每加一个新资产，要能回答：它赚什么钱？什么环境会亏？历史最大回撤多少、为什么？

---

## 5. 技术选型：约定 vs 建议

### 5.1 「约定」必须遵守

| 事项 | 约定 |
|------|------|
| 语言 | **Python 3.14**（作者指定，主流跟进） |
| 数据源 | **tushare 为主**，不用 akshare。**开发期用 tushare 官方 MCP 探查字段/参数**，生产用 tushare Python 包 |
| 存储 | MySQL 8.x+，库名 `stock`，SQLAlchemy 2.x + pymysql |
| 计算结构 | 一律 `BaseCalculator` 子类，统一 `update(start_date, end_date, **params)` 跑、`save_to_database` 落库 |
| Schema | **schema-as-code**：接入层从 tushare 返回 df 自动推断 + 演化，加工层手写 `output_schema`。**不用 `table_schemas.sql`** |
| 增量 | 三类策略（trade_date / ann_date / full_refresh），biz_date 抽象 + etl_biz_date 水位表驱动 |
| 配置 | Python + `os.getenv` + JSON + `.env`，**不引 yaml** |
| 日期 | 统一 `yyyymmdd` 字符串，入库转 DATE；用 `core/dates.py` 判断交易日。`start_date`/`end_date` 一律指 biz_date 区间 |
| 路径 | `Path(__file__)` 相对定位，不出现绝对路径 |
| 依赖 | `requirements.txt` 用 `>=,<` 范围，不锁 `==` |
| 分区 | **不做**（数据量级未到） |
| Docker | **不做**（作者不用，云迁移由 `os.getenv` 覆盖） |

### 5.2 「建议」可讨论

| 缺口 | 方案 | 状态 |
|------|------|------|
| 回测引擎 | **backtesting.py** | 已定。兼容性问题由 Agent 修（代码量小） |
| 调度框架 | 本轮裸脚本，长期 Prefect 2.x | 已定。Airflow 废弃 |
| LLM 情绪 | 暂不做 | MVP 先把轮动跑通 |
| 看板 | 暂不做 | 先用 `research/` notebook |
| 资产池扩展 | Phase 2 | 本轮只 A股 ETF |

---

## 6. 新功能挂接指南

- **扩 tushare 数据**：用 MCP 探查接口字段 → 改 `config/tushare_apis.json`（标 `incremental_strategy` + `biz_date_col`）→ `data/etl/` 加 Calculator（不手写 schema，自动推断）。
- **扩加工层计算**：先问"角色是什么"：
  - 实体×时间的对齐宽表（因子/标签底座） → `data/panel/`，表名带实体+频率前缀，手写 `output_schema`，声明 `biz_date_col`
  - 实体×日的因子 → `data/factor/`，手写 `output_schema`
  - 实体×日的标签 → `data/label/`，手写 `output_schema`
  - 复用 `core/preprocessing.py`（winsorize/neutralize/rank）
- **扩策略**：`portfolio/` 加策略类，`backtest/` 封装回测，`signals/` 复用策略逻辑生成信号。三者共用同一套策略代码，避免回测/实盘两套。
- **扩调度**：`pipeline/schedule_*.json` 加任务配置（设调度频率），`scripts/` 复用 `run_ingest.py`/`run_compute.py`。
- **新表**：不写 SQL，Calculator 里声明 schema 自动建表。

---

## 7. 硬约束

1. **接入层与加工层解耦**：接入层（`data/etl/`）只放 tushare 1:1 复刻，加工层（`data/panel+factor+label/`）只放自定义计算，不混。
2. **加工层用 panel 抽象**：panel（实体×时间对齐宽表）/ factor（实体×日因子）/ label（实体×日标签），粒度用表名前缀（stock_/market_/industry_/fin_ + 频率）标，不按 alpha/risk 分，不用 dwd/dws。
3. **统一 `update`**：删 `history_backfill`/`incremental_update`，用单个 `update(start_date, end_date, **params)`。`start_date`/`end_date` 是 biz_date 区间，不传=增量、传=回补。
4. **biz_date 抽象**：每个 Calculator 声明 `biz_date_col`（trade_date/ann_date/snapshot_date），snapshot 不是特例。频率不进 `update` 签名（调度频率走 schedule，数据频率走 get_data）。
5. **新计算 = Calculator 子类**，落库走 `save_to_database`，幂等靠 `write_mode`。
6. **schema-as-code**：接入层自动推断，加工层手写 `output_schema`。不用 `table_schemas.sql`。
7. **tushare_api 以 MCP 为主**：开发期用 MCP 探查字段，不人肉维护 `tushare_apis.json` 的 `fields` 列表。
8. **三类增量**：财务/事件类必须用 `by_ann_date`，不用 period 遍历。
9. **配置走 `.env`**：密钥 `os.getenv` 默认值留空，不硬编码。
10. **路径用 `Path(__file__)`**，不出现绝对路径。
11. **改动小而可回滚**：一次一个模块，便于 review 和 git。
12. **Python 3.14 兼容**：库版本用 `>=` 范围，遇到兼容问题优先修代码而非降版本（backtesting.py 等）。

---

## 8. 开发路线

**Phase 1 — MVP（本轮）**

1. 建 Python 3.14 env + 重写 `requirements.txt`
2. 把 `config/` `core/` `pipeline/` 从 `data/` 提升到顶层（旧 `data/config/` `data/utils/` `data/workflows/` 迁移后删）
3. 用 MCP 验证字段，重写 `config/tushare_apis.json`（22 个精选接口 + `incremental_strategy` + `biz_date_col`）
4. 写 `pipeline/incremental/` 三类基类 + `etl_biz_date` 水位表
5. 改 `data/utils/base_calculator.py` → `core/calculator.py`：合并 `update`，加 `biz_date_col` 抽象，`update` 透传 `start_date`/`end_date` 给 `process_data`
6. 改 `data/etl/loader.py`：接入层 Calculator 用统一 `update`，按三类增量策略分桶
7. 加工层用 panel 抽象重组：`data/sql/` 拆到 `data/panel/`（stock_daily_panel, market_sentiment_*, stock_mv_monthly, stock_percentiles, fin_statement_panel, fin_indicator_snapshot），表名带实体+频率前缀；`data/factor/` `data/label/` 保留
8. 迁加工层到统一 `update` 签名 + 手写 `output_schema` + 声明 `biz_date_col`
9. 写 `pipeline/schedule_*.json`（设调度频率）+ `pipeline/runner.py`
10. 新增 `portfolio/` `backtest/` `signals/`（ETF 截面轮动闭环）
11. 写 `scripts/00_init_database.py` + `run_ingest.py` + `run_compute.py`

**Phase 2 及以后**：参数稳健性、多维信号融合（含情绪）、因子归因与压力测试、主观+量化三层、资产池扩展（港股/黄金/债券）、Prefect 迁移、上云。

---

## 9. 开放问题（遇到时询问作者）

1. **ETF 池范围**：具体选哪些 ETF（代码清单），由作者给定或确认。
2. **回测结果落库粒度**：每日净值 + 调仓记录都落库？还是只落最终结果？
3. **财务数据历史深度**：全量回补从哪年开始（2010? 2015?）？影响首次拉数耗时。
4. **旧 `data/config/` `data/utils/` `data/workflows/` `data/sql/` 删除时机**：新模块跑通后立即删，还是保留一段时间对照？

> 以上未明确前，Agent 采用最小侵入做法，假设显式写在代码注释或交付说明里。
