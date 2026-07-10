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
# 接入层：拉 tushare → MySQL（日常增量，从水位补到今天）
scripts\py.bat scripts\run_ingest.py

# 加工层：panel → factor → label（按拓扑依赖顺序，严格模式）
scripts\py.bat scripts\run_compute.py
```

### 5. 加工层常用操作

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

## 入口脚本总览

| 脚本 | 用途 | 常用调用 | 频率 |
|------|------|---------|------|
| `scripts/00_init_database.py` | 建库建表 + 水位表 | `python scripts/00_init_database.py` | 一次性 |
| `scripts/sync.py` | **接入层拉数主入口**（增量+回补，见 README_sync.md） | `python scripts/sync.py` | 日常/开机 |
| `scripts/run_ingest.py` | **接入层拉数主入口**（增量+回补） | `scripts\py.bat scripts\run_ingest.py` | 日常/开机 |
| `scripts/run_compute.py` | **加工层（panel→factor→label）拓扑依赖** | `scripts\py.bat scripts\run_compute.py` | 数据更新后 |
| `scripts/sync.py` | 接入层旧入口（内部调 run_ingest） | `python scripts/sync.py` | 兼容保留 |

### 典型工作流

```bash
# 【日常】开机更新到今天
scripts\py.bat scripts\run_ingest.py               # 1. 接入层补数
scripts\py.bat scripts\run_compute.py               # 2. 加工层全量（拓扑排序，安全）

# 【单表重算】改了一个 compute 表，连带上游一起补
scripts\py.bat scripts\run_compute.py --start 20260601 --only panel:xxx

# 【单表快速补】上游刚补完，只跑自己
scripts\py.bat scripts\run_compute.py --start 20260601 --solo panel:xxx

# 【market_sentiment 月中更新】上月全覆盖 + 当月 MTD
scripts\py.bat scripts\run_ingest.py
scripts\py.bat scripts\run_compute.py                                # 增量上游
scripts\py.bat scripts\run_compute.py --start 20260601 --end 20260709 --solo panel:market_sentiment_monthly
```

> **定时任务（可选）**：本地不定期开机用 `sync.py` 手动跑即可，无需配调度。
> 若要自动化，可用 Windows 任务计划调 `scripts/daily_task.bat`（交易日 19:30 后，过 moneyflow 19:00 更新时点）。

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

详见 [CLAUDE.md](CLAUDE.md)。

## 开发原则

- 接入层与加工层解耦
- Schema-as-code（每个 Calculator 声明 `output_schema`）
- 统一 `update(start_date, end_date, **params)` 签名
- 四类增量策略：`by_trade_date` / `by_period` / `by_ex_date` / `full_refresh`（统一 overwrite/truncate，已废弃 upsert）
- `etl_biz_date` 水位表做断点恢复
