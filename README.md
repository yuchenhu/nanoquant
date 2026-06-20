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
# TODO: scripts/00_init_database.py 将在后续步骤提供
```

### 4. 跑数据

```bash
# TODO: scripts/run_ingest.py / run_compute.py 将在后续步骤提供
```

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
- 三类增量策略：`by_trade_date` / `by_ann_date` / `full_refresh`
- `etl_biz_date` 水位表做断点恢复
