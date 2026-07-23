# nanoquant

A 股量化研究框架：Tushare 接入 → MySQL 落库 → 因子/标签加工 → 回测。

## 快速开始

### 1. 环境准备

- Python >= 3.11（推荐 3.14）
- MySQL 8.x

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -r requirements.txt
```

### 2. 配置密钥

复制 `.env.example` 为 `.env`，填入 Tushare token 和 MySQL 密码：

```bash
cp .env.example .env
# 编辑 .env 填入 TUSHARE_TOKEN / DB_PASSWORD
```

### 3. 初始化数据库

```bash
# 建库建表 + 水位表（首次运行一次）
python scripts/00_init_database.py
# --dry-run 只打印不执行
```

### 4. 日常跑数据

```bash
# 接入层：日常增量补齐（推荐用 sync.py，从水位补到今天）
python scripts/sync.py

# 加工层：panel -> factor -> label（按拓扑依赖顺序，严格模式）
scripts\py.bat scripts\run_compute.py
```

### 5. 历史数据回补

```bash
# ingest 层逐年回补（断点续跑，支持 EDA 检查）
python scripts/backfill_years.py --from-year 2010 --to-year 2026

# 指定年份
python scripts/backfill_years.py --from-year 2010 --to-year 2010

# 逐年后做 EDA 检查
python scripts/eda_year.py 2010
```

### 6. 加工层常用操作

```bash
# 全量跑（自动从水位续跑，日常增量首选）
scripts\py.bat scripts\run_compute.py

# 回补指定区间
scripts\py.bat scripts\run_compute.py --start 20200101 --end 20201231

# 跑指定任务 + 其上游依赖（改变更后全链路补）
scripts\py.bat scripts\run_compute.py --start 20260601 --only panel:market_sentiment_monthly

# 只跑指定任务，不展开依赖（上游已就绪的最快路径）
scripts\py.bat scripts\run_compute.py --start 20260601 --solo panel:market_sentiment_monthly

# 列出所有任务 + 拓扑依赖链
scripts\py.bat scripts\run_compute.py --list
```

### 7. 回测

```bash
# ETF 截面动量轮动回测
python scripts/run_strategy.py --mode backtest --start 20240101 --end 20241231

# 调策略参数
python scripts/run_strategy.py --mode backtest --lookback 20 --max-positions 5 --category broad
```

## 入口脚本总览

| 脚本 | 用途 | 频率 |
|---|---|---|
| `scripts/00_init_database.py` | 建库建表 + 水位表 | 一次性 |
| `scripts/sync.py` | 接入层日常补数（统一入口，自动补齐离线缺口） | 日常 |
| `scripts/run_ingest.py` | 接入层拉数（底层，sync.py 内部调用） | sync.py 内部 |
| `scripts/run_compute.py` | 加工层 panel->factor->label（拓扑排序） | 接入层更新后 |
| `scripts/backfill_years.py` | ingest 层历史数据回补（断点续跑） | 需要时 |
| `scripts/eda_year.py` | 逐年 EDA 检查（行数 + 日期范围） | 回补后 |
| `scripts/run_strategy.py` | 回测（--mode backtest）；signal 模式已废弃 | 需要时 |

### 典型工作流

```bash
# 【日常】开机更新到今天
python scripts/sync.py                              # 1. 接入层补数
scripts\py.bat scripts\run_compute.py               # 2. 加工层全量

# 【单表重算】改了一个 compute 表，连带上游一起补
scripts\py.bat scripts\run_compute.py --start 20260601 --only panel:xxx

# 【单表快速补】上游刚补完，只跑自己
scripts\py.bat scripts\run_compute.py --start 20260601 --solo panel:xxx

# 【回测】跑 ETF 动量轮动回测
python scripts/run_strategy.py --mode backtest --start 20240101 --end 20241231
```

## 目录结构

```
nanoquant/
├── config/          # 配置（tushare_apis.json: 31 个接口, database.py, universe.py）
├── core/            # 基础设施（calculator 基类, dates.py, schema.py, preprocessing.py）
├── pipeline/        # 调度（schedule_compute.json, schedule_ingest.json, incremental 基类）
├── data/
│   ├── etl/         # 接入层：Tushare -> MySQL（31 个 Calculator）
│   ├── panel/       # 加工层：宽表（market_sentiment, index_membership 等）
│   ├── factor/      # 加工层：因子（price_volume_20d 等）
│   ├── label/       # 加工层：标签（forward_returns）
├── backtest/        # 回测引擎（已实现 MVP：向量化回测 + 指标计算）
├── portfolio/       # 策略层（因子合成 + 指数池 + 截面动量策略）
├── docs/            # Spec 文档（因子库、情绪表、回测引擎）
├── research/        # 研究笔记 / notebook
├── scripts/         # 入口脚本
└── tests/           # 历史验收测试（多数已与现状不符）
```

## 相关文档

| 文档 | 用途 |
|---|---|
| [CLAUDE.md](CLAUDE.md) | 架构地图、最高原则、核心约定（AI Agent 必读） |
| [DEV_GUIDE.md](DEV_GUIDE.md) | API 速查、踩坑集、新 Calculator 模板（写代码时读） |
| [ROADMAP.md](ROADMAP.md) | 缺口清单、各阶段完成度（开发前必读） |
| [TUSHARE_API_GUIDE.md](TUSHARE_API_GUIDE.md) | tushare 接口字段、参数、取数逻辑 |
| [docs/spec_index_factors.md](docs/spec_index_factors.md) | 9 个指数因子 + 5 张市场情绪表 spec |
| [docs/spec_backtest_engine.md](docs/spec_backtest_engine.md) | 回测引擎设计 spec |

## 开发原则

- 接入层与加工层解耦：`data/etl/` 只放 tushare 1:1 复刻，`data/panel+factor+label/` 只放自定义计算
- Schema-as-code：每个 Calculator 声明 `output_schema`，自动建表
- 统一 `update(start_date, end_date, **params)` 签名
- write_mode：`overwrite`（默认）+ `partition_col` 分区覆盖；`truncate` 用于全量刷新表
- 主键去重：`BaseCalculator.save_to_database()` 统一在落库前按主键去重
- `etl_biz_date` 水位表做断点恢复
- 路径用 `Path(__file__)` 相对定位，不出现绝对路径