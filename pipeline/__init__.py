"""nanoquant 调度与增量策略层。

子模块：
- incremental: 三类增量策略基类（by_trade_date / by_ann_date / full_refresh）
- runner:      JSON 配置驱动的调度执行器
- schedule_ingest.json:  接入层任务调度
- schedule_compute.json: 加工层任务调度
"""
