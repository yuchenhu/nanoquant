# Spec: 回测引擎 (backtest/engine.py)

> 创建：2026-07-23 | 状态：MVP 已实现
> 范式：向量化 pandas 回测（非事件驱动）

---

## Context

nanoquant 有 9 个因子表 + 5 张情绪表，但没有回测引擎。需要一个轻量、透明、pandas 原生的回测框架来验证 ETF 轮动策略。

## 已实现

### 核心引擎 (`backtest/engine.py`)

- `VectorizedBacktester` 类，支持周频/月频/日频调仓
- 真实成本模型（华泰涨乐财富通 ETF）：
  - 佣金: 万2.5 双向，最低5元/笔（10万/笔时25元，不触发最低）
  - 滑点: 0.1% 单边（保守估计）
  - 往返合计: ~0.25% per unit turnover
- 防未来函数：T日收盘信号 → T+1日起新权重生效
- 停牌过滤：调仓日跳过停牌标的
- 可配置参数：`capital`（初始资金）、`commission_rate`、`slippage`

### 绩效指标 (`backtest/metrics.py`)

- `compute_metrics()`: 总收益、年化收益、年化波动率、夏普、最大回撤

### 策略层 (`portfolio/strategy.py`)

- `CrossSectionalMomentumStrategy`: 截面动量排名策略
- 支持 lookback 窗口、波动率过滤、持仓上限、单票上限、回撤止损

### 入口脚本 (`scripts/run_strategy.py`)

```bash
# 默认参数回测
python scripts/run_strategy.py --mode backtest --start 20240101 --end 20241231

# 自定义成本
python scripts/run_strategy.py --mode backtest --commission 0.00015 --slippage 0.0005

# 指定策略参数
python scripts/run_strategy.py --mode backtest --lookback 20 --max-positions 5 --capital 500000
```

## 时序设计（防未来函数）

```
T日收盘:
  - 当日收益用旧权重（上一调仓日确定）
  - 计算信号（因子得分基于T日收盘数据）
  - 生成目标权重
  - 检查停牌，过滤不可交易标的
  - 扣除换手成本（佣金+滑点）

T+1日起:
  - 新权重生效
  - 持有至下一调仓日
```

## 成本模型

| 项目 | 费率 | 10万/笔实际 |
|---|---|---|
| 佣金 | 万2.5 双向 | 25元/笔 |
| 滑点 | 0.1% 单边 | 100元/笔 |
| 印花税 | ETF 免 | 0 |
| 过户费 | ETF 免 | 0 |
| **往返合计** | **~0.25%** | **250元/笔** |

## 待实现

- [ ] 涨跌停过滤（stk_limit 表，ETF 场景较少见）
- [ ] Benchmark 对比（沪深300/中证500）
- [ ] 分年度回测报告
- [ ] 可视化（净值曲线、回撤曲线）
- [ ] 因子信号对接（当前用纯动量，后续接 factor 表）

## 与因子库的对接（未来）

```
factor_momentum_r2.score_20d   ─┐
factor_momentum_term.tsmom_ratio ┤
factor_return_vol.ret_vol       ┼── 加权合成 → signal DataFrame
...                              ┤   (trade_date × index_code)
factor_residual_alpha.z_res     ─┘

panel_stock_daily.close         ─── price DataFrame
                                     (trade_date × index_code)
```