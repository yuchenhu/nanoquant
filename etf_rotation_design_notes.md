# ETF 轮动策略设计笔记

> 2026-07-04 定稿。记录 ETF 轮动涉及的核心设计决策，供后续开发时回溯。

---

## 1. 数据底座 · ETF 接口权限速查

| 接口 | 积分 | 用途 | 状态 |
|---|---|---|---|
| fund_basic | 2000 | ETF 清单 | 已接入 |
| fund_daily | 5000 | ETF 日线 OHLCV | 已接入 |
| fund_adj | 600 | ETF 复权因子 | 已接入 |
| fund_share | 2000 | ETF 每日份额 | 已接入 |
| fund_factor_pro | 5000 | 60+ 技术因子 | **新增，待本地拉数验证** |
| fund_nav | 2000 | ETF 净值 | **新增，待本地拉数验证** |
| etf_basic | 8000 | ETF 专属元信息 | 权限不够，用 fund_basic + 手写映射替代 |
| etf_share_size | 8000 | ETF 规模+NAV | 权限不够，用 fund_share×close 自算替代 |
| fund_portfolio | 2000 | ETF 持仓穿透 | 暂不需要（季度频率，用 index_weight 穿透已够） |
| ggt_daily | 2000 | 南向资金 | Phase 2 港股 ETF 时再接入 |

---

## 2. ETF vs 指数 · 分析单元选择

| 资产类别 | 信号源 | 原因 |
|---|---|---|
| **股票 ETF**（宽基+行业） | **指数**（index_daily/index_dailybasic） | 指数数据更纯净，ETF 多了跟踪误差噪音 |
| **商品 ETF**（黄金/豆粕等） | **ETF 自身**（fund_daily） | 底层指数 tushare 未覆盖 |
| **债券 ETF**（国债/信用债） | **ETF 自身**（fund_daily） | 债券指数 tushare 未覆盖 |
| **跨境 ETF**（港股/美股） | **ETF 自身**（fund_daily） | Phase 2，海外指数需额外付费 |

---

## 3. 策略工作流（两阶段）

```
第一阶段 · 选指数（仅股票类）
  统一因子：动量/波动率/RSI/MA 偏离（指数层面算）
  专属过滤：PE 分位 > 0.8 → score = -999（太贵排斥）
  → 产出：截面排序 Top-N 股票指数

第二阶段 · 选 ETF + 相关性约束
  股票 ETF：Top-N 指数 → INDEX_TO_ETF 映射 → N 只 ETF
  商品/债券 ETF：直接用 fund_daily 算统一因子 → Top-1 每类
  相关性约束：同类内去重 (<0.75)，跨类低相关全保留
  → 产出：最终调仓清单（3-5 只 ETF）
```

---

## 4. 双层因子设计

| 层级 | 因子 | 适用范围 | 用途 |
|---|---|---|---|
| **统一因子** | 20/60/120 日动量、波动率、RSI、MA 偏离、回撤 | 全部 ETF | 截面排序（跨资产可比） |
| **股票专属过滤** | PE 分位、PB 分位、股息率 | 仅股票 ETF | 风控过滤，**不参与排序**（太贵→直接排斥） |
| **商品/债券辅助** | ETF 份额变化 | 商品/债券 ETF | 极端行情确认信号，不参与日常排序 |

**关键原则**：
- 不能用 PE 去给黄金 ETF 排序
- 动量 (%return) 和波动率 (%std) 天然跨资产可比
- 估值因子（PE/PB）是"什么时候不买"，不是"买哪个"

---

## 5. 截面相关性处理

**不是 IC 计算问题，是组合构建问题。**

- IC 层面：正常算，不管相关性。15 个指数之间的结构性相关是事实，不是需要"修掉"的噪音
- 组合构建层面：相关性约束确保不集中押注（同类指数 < 0.75 只保留 1 个）
- 如需更纯粹的单因子对比检验：用全指（000985.CSI）做 beta 正交化后取残差再算 IC

---

## 6. ETF 资金流 · 中低频轮动的信号价值

| 指标 | 价值判断 | 理由 |
|---|---|---|
| ETF 折溢价率 | 噪音 | 做市商套利半衰期 1-3 天，周频以上调仓时已消失 |
| 宽基 ETF 份额变化 | 冗余 | 申赎主要受套保/流动性需求驱动，不是方向性押注，信息已包含在价格动量里 |
| 行业 ETF 份额变化 | 边际价值 | 机构行业配置有持续性，对行业 ETF 轮动可能有用（Phase 2 验证） |
| ETF 总规模 | 过滤器 | 用于排除迷你 ETF（日均成交额 < 500 万），不做信号 |

---

## 7. 待办 · 下一步

- [ ] 本地 `python -c "from data.etl.base import fetch_tushare; ..."` 验证 fund_factor_pro + fund_nav 能走通
- [ ] `config/etf_universe.py`：手写 INDEX_TO_ETF 映射表 + ETF 流动性筛选规则
- [ ] `data/panel/etf_daily.py`：panel_etf_daily 面板表（基金×交易日，JOIN fund_daily + fund_factor_pro）
- [ ] `data/panel/etf_daily_metrics.py`：panel_etf_daily_metrics（衍生指标：折溢价率、资金流、动量分位）
- [ ] market_sentiment_monthly 扩展：加 `dimension_type='sector'`（6 个行业板块）
- [ ] `data/panel/market_regime.py`：读 market_sentiment_monthly → 百分位化 → 投票 → 滞回 → 月频 regime 标签
- [ ] `portfolio/etf_rotation.py`：ETF 轮动策略（两阶段工作流）
- [ ] `backtest/`：真实成本回测（ETF 佣金 + 冲击 + 次日开盘成交）
