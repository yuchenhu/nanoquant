# 数据补数指南（sync.py）

> 接入层拉数的统一入口。为「本地、不定期开机」设计：开机跑一次自动补齐缺口，也支持手动区间回补。
> 低频参考文档，详细机制见 `CLAUDE.md §8`。

---

## 0. 虚拟环境（每次开机 / 新终端第一步）

项目依赖装在 `.venv` 里，跑任何脚本前先激活，否则会报 `No module named pandas` 之类的错。

### 方案 A：启动器（推荐，无需激活）

```cmd
REM 用 scripts\py.bat，自动调用 .venv 里的 python
scripts\py.bat scripts\sync.py
scripts\py.bat scripts\backfill_years.py --from-year 2021 --to-year 2026
scripts\py.bat scripts\data_audit.py
```

### 方案 B：手动激活（cmd）

```cmd
.venv\Scripts\activate
python scripts\sync.py
```

激活后命令行前会出现 `(.venv)` 标记，说明切进去了。退出用 `deactivate`。

### 方案 C：手动激活（PowerShell）

```powershell
.\.venv\Scripts\Activate.ps1
python scripts\sync.py
```

> 下面所有示例用裸 `python`，记得先激活或用 `scripts\py.bat` 替换。

---

## 1. 日常用法（90% 的场景）

```bash
# 开机一键补齐：各表从「水位 / 保守窗口」自动补到今天
python scripts/sync.py
```

不传任何参数 = 增量模式。每张表自己知道上次跑到哪（水位线），自动补离线期间的缺口。
一两周没开机也不用算日期，跑这一条就行。

---

## 2. 手动区间回补

```bash
# 回补指定 biz_date 区间（overwrite 幂等，可重复跑、可断点重跑）
python scripts/sync.py --start 20200101 --end 20201231

# 只刷某些接口
python scripts/sync.py --only daily,moneyflow,adj_factor
python scripts/sync.py --start 20200101 --end 20201231 --only income

# 排除某些接口
python scripts/sync.py --exclude weekly,monthly

# 列出全部 26 个接口 + 各自策略
python scripts/sync.py --list
```

---

## 3. 首次建库 + 灌全历史（一次性）

新环境从零开始，按顺序执行：

```bash
# ① 建库建表 + 水位表
python scripts/00_init_database.py

# ② 交易日历（最先，其他都依赖它）
python scripts/sync.py --only trade_cal

# ③ 清单类全量刷一次（full_refresh，无视区间，跑一次即可）
python scripts/sync.py --only stock_basic,index_basic,index_classify,index_member_all,fund_basic

# ④ 逐年回补行情/财务/分红（一年一条，断了重跑无副作用）
python scripts/sync.py --start 20100101 --end 20101231 --exclude trade_cal,stock_basic,index_basic,index_classify,index_member_all,fund_basic
python scripts/sync.py --start 20110101 --end 20111231 --exclude trade_cal,stock_basic,index_basic,index_classify,index_member_all,fund_basic
# ... 逐年到 ...
python scripts/sync.py --start 20250101 --end 20251231 --exclude trade_cal,stock_basic,index_basic,index_classify,index_member_all,fund_basic
```

> 为什么逐年：单条全历史太久，中途断了要从头；逐年每条几分钟、进度可见、断点友好。
> 为什么 ④ 排除清单类：它们是 full_refresh，不分年份，跑一次拿当前全量快照即可，逐年重刷是浪费。

灌完历史后，以后日常就只用第 1 节的无参 `python scripts/sync.py`。

---

## 4. 区间参数（--start/--end）对 4 类接口的含义

不同策略对同一个区间的解释不一样：

| 策略 | 接口 | --start/--end 含义 |
|------|------|--------------------|
| by_trade_date | daily / adj_factor / moneyflow / fund_* / index_weight / weekly / monthly ... | trade_date 区间，逐交易日拉 |
| by_period | income / balancesheet / cashflow / disclosure_date | 当报告期区间，自动拆成区间内所有季度末(0331/0630/0930/1231) |
| by_ex_date | dividend | ex_date(除权日) 区间，逐交易日拉 |
| full_refresh | trade_cal / stock_basic / index_* / fund_basic | 忽略区间，每次全量 truncate 重刷 |

---

## 5. 不传参（日常增量）时各接口怎么补

| 接口类 | 增量起点 | 说明 |
|--------|---------|------|
| 行情类(by_trade_date) | 水位次日 → 今天 | 纯水位驱动，久未开机从断点续，不漏 |
| 财务三表 + disclosure | `min(水位, today往前4期)` → 今天 | 常开机刷最近 4 期覆盖财报修订；久未开机从水位补全 |
| 分红(dividend) | `min(水位, today-365天)` → 今天 | 常开机回刷近 1 年覆盖分红修订；久未开机从水位补全 |
| 清单类(full_refresh) | 忽略，全量重刷 | — |

`min(水位, today-窗口)` 的设计：既覆盖最近修订、又不漏久未开机的中间断档。overwrite 幂等，重叠期重刷无副作用。

---

## 6. 注意事项

1. **空库首次必须先 `--start` 回补**：无参增量对空库只会拉今天/最近窗口，不会自动灌全历史。
2. **trade_cal 永远最先跑并刷新缓存**：sync 内部已保证（Phase 1），手动单跑也要先它。
3. **执行顺序**：trade_cal → 清单类 → 行情 → 财务 → 分红（sync 内部按 4 个 Phase 自动排）。
4. **断点重跑安全**：全程 overwrite / 水位幂等，跑到一半断电/报错，直接重跑即可，不会重复堆数据。
5. **日志**：每次运行写 `logs/sync.log`（同时打印到控制台）。
6. **tushare 更新时间**：当天数据 moneyflow 19:00 才更新；若当天要拉当天数据，19:30 后再跑（日常补昨天及更早不受影响）。

---

## 7. 后续加工层

接入层数据补好后，跑加工层（panel → factor → label）：

```bash
python scripts/run_compute.py            # 增量
python scripts/run_compute.py --start 20100101 --end 20251231   # 区间回补
```
