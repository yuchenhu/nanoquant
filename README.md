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

> 旧版本曾在 `data/config/api.py`、`data/config/database.py`、`data/utils/date_utils.py`
> 中硬编码 token / 密码，已移除。**请尽快在 tushare.pro 轮换 token、在 MySQL 轮换密码。**

### 3. 初始化数据库

```bash
# 建库建表 + 水位表（首次运行一次）
python scripts/00_init_database.py
# --dry-run 只打印不执行
```

### 4. 跑数据

```bash
# 一键：API 接入层 -> 加工层（拓扑序） -> 缺口体检（日常开机就这一条）
scripts\py.bat scripts\sync.py

# 或分开跑（两层）：
scripts\py.bat scripts\ingest.py         # 1) API 层：增量 catch-up（水位 -> 今天）
scripts\py.bat scripts\compute.py        # 2) 加工层：panel -> factor -> label（拓扑依赖）

# 区间回补（幂等可断点；行情类自动按年分块，逐年落库推水位）
scripts\py.bat scripts\ingest.py --start 20100101 --end 20261004

# 全库体检：覆盖/起点/逐年断档 + 补数命令建议（纯查库，不拉数）
scripts\py.bat scripts\check_registry_and_gaps.py
```

### 5. 加工层常用操作

```bash
# 全量跑（自动从水位续跑，日常增量首选）
scripts\py.bat scripts\compute.py

# 回补指定区间
scripts\py.bat scripts\compute.py --start 20200101 --end 20201231

# 跑指定任务 + 其上游依赖（改变更后全链路补）
scripts\py.bat scripts\compute.py --start 20260601 --only panel:market_sentiment_monthly

# 只跑指定任务，不展开依赖（上游已就绪的最快路径）
scripts\py.bat scripts\compute.py --start 20260601 --solo panel:market_sentiment_monthly

# 列出所有任务 + 拓扑依赖链
scripts\py.bat scripts\compute.py --list

# 财务快照月频回补（panel_financial_statements_snapshot，逐月落库断点重跑）
scripts\py.bat scripts\backfill_fin_snapshot.py --start 20100630 --end 20260930
```

## 数据更新机制（初级分析师版，三分钟读懂）

1. **三层流水线**：`ingest`（把 tushare 原始数据拉进 MySQL）→ `compute`（加工成 panel/factor/label 宽表）→ 策略层。每层每张表都记得自己的"水位"——已更新到哪个业务日期（存 `etl_biz_date` 表）。
2. **日常只要一条命令** `sync.py`：它按顺序跑 ingest → compute → 体检。每张表自动从"水位次日"补到今天，不会重复拉。
3. **重跑永远安全**：所有表按分区先删后写（或按主键覆盖）。跑一半断了、重复跑、跑错了区间，都不会产生重复或脏数据。
4. **月频表月中跑是正常的零更新**：市值月表、财务快照这类月频表，只在"每月最后交易日"生成快照。月中运行时本月还没结束，所以什么都不写——不是坏了。
5. **体检报告怎么读**：`check_registry_and_gaps.py` 输出四段——接口盘点（策略/水位/覆盖）、加工层盘点、逐年断档明细、补数命令建议。`[OK]` 不用管；`[STALE]` 说明水位落后（跑一次增量）；`[GAP]` 是整年断档（按建议命令回补）；`[EMPTY]` 是从未拉过（给全量回补命令）。
6. **长回补自动分块**：回补 >1 年的行情区间会按年切块、逐年落库推水位，中断后重跑从断点继续。
7. 深入细节（水位列为什么是 end_date 不是 f_ann_date 等）见 DEV_GUIDE.md §4。

## 入口脚本总览

| 脚本 | 用途 | 常用调用 | 频率 |
|------|------|---------|------|
| `scripts/sync.py` | **一键同步**（ingest + compute + 体检） | `scripts\py.bat scripts\sync.py` | 日常/开机 |
| `scripts/ingest.py` | **API 层唯一入口**（增量+回补，长区间按年分块） | `scripts\py.bat scripts\ingest.py` | 日常 |
| `scripts/compute.py` | **加工层唯一入口**（panel→factor→label 拓扑序） | `scripts\py.bat scripts\compute.py` | 数据更新后 |
| `scripts/check_registry_and_gaps.py` | 全库体检：覆盖/起点/断档 + 补数建议 | `scripts\py.bat scripts\check_registry_and_gaps.py` | 更新后核对 |
| `scripts/backfill_fin_snapshot.py` | 财务快照月频回补（前置体检+断点重跑） | 见 §5 | 一次性/回补 |
| `scripts/00_init_database.py` | 建库建表 + 水位表 | `python scripts/00_init_database.py` | 一次性 |

> 2026-10-04 更名：`sync_all.py`→`sync.py`（一键）、原 `sync.py`→`ingest.py`（API 层）、
> `run_compute.py`→`compute.py`（加工层）；`run_ingest.py`/`README_sync.md` 已删除。
> 更新机制细则（水位语义/幂等/月频固化/分块）见 DEV_GUIDE.md §4。

### 典型工作流

```bash
# 【日常】开机一键更新到今天（含体检）
scripts\py.bat scripts\sync.py

# 【单表重算】改了一个 compute 表，连带上游一起补
scripts\py.bat scripts\compute.py --start 20260601 --only panel:xxx

# 【单表快速补】上游刚补完，只跑自己
scripts\py.bat scripts\compute.py --start 20260601 --solo panel:xxx

# 【新增指数/接口】先验可得性，再入池，再分块回补
scripts\py.bat scripts\ingest.py --start 20041231 --end 20261004 --only index_daily,index_weight
```

> **定时任务（可选）**：本地不定期开机用 `sync.py` 手动跑即可。
> 若要自动化，Windows 任务计划调 `scripts/daily_task.bat`（交易日 19:30 后，一键三段式）。

## 目录结构

```
nanoquant/
├── config/          # 配置（待提升到顶层）
├── core/            # 基础设施（待提升到顶层）
├── pipeline/        # 调度（待新建）
├── data/
│   ├── etl/         # 接入层：Tushare → MySQL
│   ├── panel/       # 加工层：宽表（待从 data/sql/ 迁移）
│   ├── factor/      # 加工层：因子
│   ├── label/       # 加工层：标签
│   └── ...
├── research/        # 研究笔记 / notebook
├── scripts/         # 入口脚本
└── tests/           # 测试
```

详见 [AGENTS.md](AGENTS.md)。

## 开发原则

- 接入层与加工层解耦
- Schema-as-code（每个 Calculator 声明 `output_schema`）
- 统一 `update(start_date, end_date, **params)` 签名
- 四类增量策略：`by_trade_date` / `by_period` / `by_ex_date` / `full_refresh`（统一 overwrite/truncate，已废弃 upsert）
- `etl_biz_date` 水位表做断点恢复
