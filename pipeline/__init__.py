"""nanoquant 调度与增量策略层。

子模块：
- incremental: 四类增量策略基类（by_trade_date / by_period / by_ex_date / full_refresh；by_ann_date 保留兼容）
- runner:      JSON 配置驱动的调度执行器
- schedule_ingest.json:  接入层任务调度
- schedule_compute.json: 加工层任务调度
"""
