"""signals/: 调仓信号生成层（待重构）。

原 SignalGenerator（signals/generator.py）已废弃并删除：
- signal_rebalance 表使用 write_mode=upsert，违反项目规则"已废弃 upsert"
- 后续如需调仓信号，应在 portfolio/ 层用 Calculator 模式重新实现
  （overwrite + partition_col=signal_date）

参见：ROADMAP.md / .trae/rules/nanoquant_loop.md §2 持久层
"""
