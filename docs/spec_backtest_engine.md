# Spec: 回测引擎 (backtest/engine.py)

> 创建：2026-07-23 | 状态：spec review
> 范式：向量化 pandas 回测（非事件驱动）

---

## Context

nanoquant 有 9 个因子表 + 5 张情绪表，但没有回测引擎。需要一个轻量、透明、pandas 原生的回测框架来验证 ETF 轮动策略。

策略特征：定期调仓（每 N 个交易日） + 截面排名（因子得分 top K） + 等权/风险平价仓位。不需要事件驱动（逐 bar 判断），向量化回测天然适合。

## 改什么

新建 `backtest/engine.py`（~200 行），提供：
- 截面排名调仓回测（向量化，非事件驱动）
- 真实成本（佣金 + 滑点）
- 绩效指标（年化收益、夏普、最大回撤、换手率、胜率）
- 净值曲线可视化

## 不改什么

- 不涉及事件驱动（逐 bar 信号触发）
- 不涉及多资产再平衡（固定比例）
- 不涉及杠杆/做空
- 不涉及 benchmark 对比（二期加）

## 架构

```
backtest/
  engine.py       # 核心引擎：run_backtest()
  metrics.py      # 绩效指标：calc_metrics()
  plot.py         # 可视化：plot_equity_curve()
```

## 输入

```python
@dataclass
class BacktestInput:
    # 价格数据：MultiIndex (trade_date, asset_code)，值=close
    price: pd.DataFrame
    # 信号数据：MultiIndex (trade_date, asset_code)，值=因子得分
    signal: pd.DataFrame
    # 调仓频率（交易日）
    rebalance_freq: int = 10
    # 持仓数量
    top_k: int = 4
    # 初始资金
    initial_capital: float = 1_000_000
    # 佣金率（双向）
    commission_rate: float = 0.0005  # 万5
    # 滑点（bp）
    slippage_bp: float = 1.0  # 1bp
    # 调仓执行价偏移（0=当日收盘，1=次日开盘，-1=当日VWAP）
    execution_delay: int = 0
```

## 核心逻辑

### Step 1: 生成调仓信号

```python
def _generate_positions(
    signal: pd.DataFrame,
    price: pd.DataFrame,
    rebalance_freq: int,
    top_k: int,
) -> pd.DataFrame:
    """
    返回 DataFrame: index=trade_date, columns=asset_code, values=权重
    - 调仓日：按 signal 排名，top_k 权重=1/k，其余=0
    - 非调仓日：继承上一调仓日的权重
    - 停牌/无价格：权重=0（卖不出去，不参与当日）
    """
```

**关键细节**：
1. 调仓日 = `dates[::rebalance_freq]`（从第一个有信号的交易日开始）
2. 信号日 = 调仓日（T 日收盘后算因子，T 日收盘价调仓）
3. 如果 `execution_delay=1`，则 T 日信号 → T+1 日执行（用 T+1 日收盘价），这需要 price 对齐到 T+1
4. 停牌检查：如果某资产在调仓日无价格，该资产权重=0，剩余权重均分给其他 top_k

### Step 2: 向量化计算组合收益

```python
def _compute_returns(
    positions: pd.DataFrame,
    price: pd.DataFrame,
    commission_rate: float,
    slippage_bp: float,
) -> tuple[pd.Series, pd.Series]:
    """
    - 资产日收益：ret[t] = price[t] / price[t-1] - 1
    - 组合日收益 = sum(权重[t-1] * ret[t])  # 注意：前一天的权重决定今天的收益
    - 换手 = sum(|权重[t] - 权重[t-1]|) / 2
    - 成本 = 换手 * (2 * commission_rate + 2 * slippage_bp / 10000)
    - 组合日收益（扣成本）= 组合日收益 - 当日成本 / 当日净值
    """
```

**关键细节**：
1. 权重的时序对齐：`position[t-1]` 决定 `ret[t]` 的分配
2. 换手率：`turnover[t] = sum(|w[t] - w[t-1]|) / 2`，仅调仓日 > 0
3. 成本从净值中扣除，不是从收益中扣（避免复利偏差）
4. 佣金双向收（买+卖），滑点也是双向

### Step 3: 绩效指标

```python
@dataclass
class BacktestMetrics:
    total_return: float       # 总收益率
    annual_return: float      # 年化收益率
    annual_volatility: float  # 年化波动率
    sharpe_ratio: float       # 夏普比率（rf=0）
    max_drawdown: float       # 最大回撤
    calmar_ratio: float       # 年化收益/最大回撤
    turnover_avg: float       # 平均换手率（调仓日）
    win_rate: float           # 调仓周期胜率
    n_rebalances: int         # 调仓次数
    n_trading_days: int       # 回测交易日数
```

## 输出

```python
@dataclass
class BacktestResult:
    nav: pd.Series            # 每日净值 (index=trade_date)
    positions: pd.DataFrame   # 每日权重矩阵
    turnover: pd.Series       # 每日换手率
    metrics: BacktestMetrics  # 绩效指标
    trades: pd.DataFrame      # 调仓记录（日期、买入、卖出、成本）
```

## 使用示例

```python
from backtest.engine import run_backtest, BacktestInput

# 1. 读数据
price = load_price_data()    # pivot: trade_date × asset_code
signal = load_factor_data()  # pivot: trade_date × asset_code

# 2. 跑回测
result = run_backtest(BacktestInput(
    price=price,
    signal=signal,
    rebalance_freq=10,
    top_k=4,
    initial_capital=1_000_000,
))

# 3. 看结果
print(result.metrics)
result.plot_equity_curve()
```

## 与因子库的对接

```
factor_momentum_term.score   ─┐
factor_momentum_r2.score_20d ─┤
factor_return_vol.ret_vol    ─┼── 加权合成 → signal DataFrame
...                           ─┤   (trade_date × index_code)
factor_residual_alpha.z_res  ─┘

index_daily.close            ─── price DataFrame
                                 (trade_date × index_code)
```

## 边界条件

1. **调仓日无信号**：跳过该日，保持上一期权重
2. **调仓日某资产停牌**：该资产权重=0，top_k 递补
3. **调仓日所有资产停牌**：保持上一期权重（不调仓）
4. **回测起始日不足 lookback**：向前推 `max(rebalance_freq * 2, 60)` 日作为预热期
5. **净值归零**：如果净值 < 初始资金的 1%，中断回测并报错

## 验收标准

- mock 验证：3 个资产 × 30 天模拟数据，手算验证净值、换手、成本
- 边界测试：停牌资产 → 权重重新分配；全停牌 → 不调仓
- 与 toy 回测对齐：用同一份数据跑，结果差异 < 0.01%（成本计算方式不同可能导致微小差异）
- 空仓回测：无信号 → 净值=1.0（不变）
- 换手率验证：调仓日换手 > 0，非调仓日换手 = 0