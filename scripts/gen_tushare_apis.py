"""Step 4: 从 data/config/tushare_api.json 生成顶层 config/tushare_apis.json。

每个接口加：
- incremental_strategy: by_trade_date / by_ann_date / full_refresh
- biz_date_col: trade_date / ann_date / ""

策略分配依据（CLAUDE.md 2.5 / 硬约束 8）：
- by_trade_date: 行情类（按 trade_date 区间拉）
- by_ann_date:   财务/事件类（按 ann_date 区间拉，覆盖修订）
- full_refresh:  基础信息类（低频全量刷新）
"""
import json
from pathlib import Path

# ===== 策略分配（22 个接口） =====
STRATEGY_MAP = {
    # full_refresh: 基础信息类（无 biz_date，全量 truncate）
    "trade_cal":        {"incremental_strategy": "full_refresh",  "biz_date_col": ""},
    "stock_basic":      {"incremental_strategy": "full_refresh",  "biz_date_col": ""},
    "index_basic":      {"incremental_strategy": "full_refresh",  "biz_date_col": ""},
    "index_classify":   {"incremental_strategy": "full_refresh",  "biz_date_col": ""},
    "index_member_all": {"incremental_strategy": "full_refresh",  "biz_date_col": ""},

    # by_trade_date: 行情类（biz_date_col=trade_date）
    "daily":            {"incremental_strategy": "by_trade_date", "biz_date_col": "trade_date"},
    "weekly":           {"incremental_strategy": "by_trade_date", "biz_date_col": "trade_date"},
    "monthly":          {"incremental_strategy": "by_trade_date", "biz_date_col": "trade_date"},
    "adj_factor":       {"incremental_strategy": "by_trade_date", "biz_date_col": "trade_date"},
    "daily_basic":      {"incremental_strategy": "by_trade_date", "biz_date_col": "trade_date"},
    "moneyflow":        {"incremental_strategy": "by_trade_date", "biz_date_col": "trade_date"},
    "index_daily":      {"incremental_strategy": "by_trade_date", "biz_date_col": "trade_date"},
    "index_dailybasic": {"incremental_strategy": "by_trade_date", "biz_date_col": "trade_date"},
    "index_weight":     {"incremental_strategy": "by_trade_date", "biz_date_col": "trade_date"},
    "sw_daily":         {"incremental_strategy": "by_trade_date", "biz_date_col": "trade_date"},
    "stock_st":         {"incremental_strategy": "by_trade_date", "biz_date_col": "trade_date"},
    "suspend_d":        {"incremental_strategy": "by_trade_date", "biz_date_col": "trade_date"},

    # by_ann_date: 财务/事件类（biz_date_col=ann_date）
    "income_vip":       {"incremental_strategy": "by_ann_date",   "biz_date_col": "ann_date"},
    "balancesheet_vip": {"incremental_strategy": "by_ann_date",   "biz_date_col": "ann_date"},
    "cashflow_vip":     {"incremental_strategy": "by_ann_date",   "biz_date_col": "ann_date"},
    "dividend":         {"incremental_strategy": "by_ann_date",   "biz_date_col": "ann_date"},
    "disclosure_date":  {"incremental_strategy": "by_ann_date",   "biz_date_col": "ann_date"},
}

# ===== MCP 验证补全的 fields（stock_basic 默认字段更新） =====
# MCP stock_basic 默认字段加了 cnspell/act_name/act_ent_type，额外可选 enname/fullname/curr_type
STOCK_BASIC_FIELDS = (
    "ts_code,symbol,name,area,industry,cnspell,market,exchange,list_status,"
    "list_date,delist_date,is_hs,enname,fullname,curr_type,act_name,act_ent_type"
)

FIELDS_OVERRIDE = {
    "stock_basic": STOCK_BASIC_FIELDS,
}


def main():
    src = Path("data/config/tushare_api.json")
    dst = Path("config/tushare_apis.json")

    with open(src, "r", encoding="utf-8") as f:
        old = json.load(f)

    new = {}
    for key, cfg in old.items():
        strategy = STRATEGY_MAP.get(key)
        if strategy is None:
            print(f"  WARN: {key} 未在 STRATEGY_MAP 中分配策略，跳过")
            continue

        # 深拷贝 params，fields 提到顶层
        params = dict(cfg.get("params", {}))
        fields = FIELDS_OVERRIDE.get(key, params.pop("fields", ""))

        new[key] = {
            "api_name": cfg["api_name"],
            "description": cfg.get("description", ""),
            "table_name": cfg["table_name"],
            "incremental_strategy": strategy["incremental_strategy"],
            "biz_date_col": strategy["biz_date_col"],
            "write_mode": cfg.get("write_mode", "upsert"),
            "frequency": cfg.get("frequency", "daily"),
            "fields": fields,
            "params": params,
        }

    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(new, f, ensure_ascii=False, indent=2)

    # 统计
    by_strat = {}
    for v in new.values():
        s = v["incremental_strategy"]
        by_strat[s] = by_strat.get(s, 0) + 1
    print(f"生成 {dst}: {len(new)} 个接口")
    for s, c in sorted(by_strat.items()):
        print(f"  {s}: {c}")


if __name__ == "__main__":
    main()
