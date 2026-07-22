"""市场x月 指数成分归属 Panel（清洗 index_weight 的干净底座）。

表名：panel_index_membership_monthly
主键：trade_date + ts_code + index_code
biz_date_col：trade_date（月末交易日）
write_mode：upsert
依赖（追溯接入层）：index_weight

================================ 为什么要这张表 ================================
接入层 index_weight 有三处"脏"（已用 MCP + 本地库交叉验证）：
1. 双版冗余：同一指数有两个代码（如沪深300 = 000300.SH 和 399300.SZ），
   成分+权重逐行镜像完全一致 -> 下游必须二选一，否则重复计数。
2. "月度"名不副实：部分指数一个月有多个 trade_date（调样日 + 月末都给），
   一年 22~26 个日期（其他 12 个）-> 不按月去重会重复。
3. 成分在两次调样之间是延续的 -> 需要对齐到标准月末交易日并前向填充，
   下游"某月末某票是否在300里"才有确定答案（无未来函数）。

================================ 清洗四步 ================================
1. 双版归一：用 config.universe.CODE_TO_CANONICAL 把 alt 代码归到 canonical。
2. 月内去重：每个 (canonical指数, con_code, 年月) 取该月最后一个 trade_date 的那条。
3. 月末网格 + 前向填充：构造标准月末交易日网格，每个月取 <= 该月的最近一次
   调样月成分（merge-asof 思路，因果、无未来函数）。
4. 保留 universe 全部指数（CANONICAL_INDEX_CODES），不裁剪。
"""
from __future__ import annotations

import bisect
import logging
from datetime import datetime, timedelta
from typing import Any, Optional

import numpy as np
import pandas as pd
from sqlalchemy import text

from config.universe import ALL_INDEX_CODES, CODE_TO_CANONICAL, INDEX_NAME
from core.dates import get_monthly_last_tradedate
from data.panel.base import PanelCalculator

logger = logging.getLogger(__name__)


class IndexMembershipMonthlyCalculator(PanelCalculator):
    """指数成分归属（月末，长表 + weight，清洗 index_weight）。"""

    table_name = "index_membership_monthly"  # -> panel_index_membership_monthly
    primary_keys = ["trade_date", "ts_code", "index_code"]
    biz_date_col = "trade_date"
    write_mode = "overwrite"
    partition_col = "trade_date"
    output_schema = {
        "trade_date": "date",      # 月末交易日，入库 DATE 类型
        "ts_code": "string",       # 成分股代码（index_weight.con_code）
        "index_code": "string",    # canonical 指数代码（双版已归一）
        "index_name": "string",    # 指数中文名
        "weight": "float",         # 成分权重（%）
    }

    # 前向填充缓冲：往前多读 400 天，保证区间首月能取到上一次调样成分
    READ_BUFFER_DAYS = 400

    def __init__(self, engine=None):
        super().__init__(engine=engine)
        self.logger.info("IndexMembershipMonthlyCalculator 初始化")

    def save_to_database(self, data: pd.DataFrame) -> None:
        """落库前先清掉同月份的旧行（月频唯一：月末变动时覆盖旧快照）。"""
        if not data.empty and "trade_date" in data.columns:
            months = set()
            for d in data["trade_date"]:
                s = str(d)[:7]  # 'yyyy-mm-dd' -> 'yyyy-mm'
                if len(s) == 7:
                    months.add(s)
            if months:
                month_list = "','".join(sorted(months))
                self.logger.info("清理旧月份: %s", month_list)
                with self.engine.begin() as conn:
                    conn.execute(
                        text(
                            "DELETE FROM panel_index_membership_monthly "
                            f"WHERE DATE_FORMAT(trade_date, '%Y-%m') IN ('{month_list}')"
                        )
                    )
        super().save_to_database(data)

    def get_data(
        self, start_date: Optional[str] = None, end_date: Optional[str] = None, **params: Any
    ) -> pd.DataFrame:
        # 读全部 universe 指数（含双版冗余）。不在 SQL 里按日期字符串过滤：
        # index_weight.trade_date 在库里是带横线格式（'2020-01-23'），
        # 字符串比较易错；统一在 process_data 用 pd.to_datetime 处理。
        codes_str = ",".join([f"'{c}'" for c in ALL_INDEX_CODES])
        query = (
            "SELECT index_code, con_code, trade_date, weight "
            f"FROM index_weight WHERE index_code IN ({codes_str})"
        )
        # buffer 下界：减少全表扫描；上界用 end_date
        if start_date:
            sd = datetime.strptime(start_date, "%Y%m%d")
            read_start = (sd - timedelta(days=self.READ_BUFFER_DAYS)).strftime("%Y-%m-%d")
            query += f" AND trade_date >= '{read_start}'"
        if end_date:
            ed = datetime.strptime(end_date, "%Y%m%d").strftime("%Y-%m-%d")
            query += f" AND trade_date <= '{ed}'"
        try:
            return pd.read_sql(query, self.engine)
        except Exception as e:
            self.logger.error(f"读取 index_weight 失败: {e}")
            return pd.DataFrame()

    def process_data(self, data: pd.DataFrame, **params: Any) -> pd.DataFrame:
        if data is None or data.empty:
            return pd.DataFrame()
        start_date = params.get("start_date")
        end_date = params.get("end_date")

        df = data.copy()
        df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce")
        df = df.dropna(subset=["trade_date", "con_code", "index_code"])

        # 1. 双版归一
        df["index_code"] = df["index_code"].map(lambda c: CODE_TO_CANONICAL.get(c, c))
        # 2. 月内去重：每个 (index_code, con_code, 年月) 取该月最新 trade_date
        df["ym"] = df["trade_date"].dt.strftime("%Y%m")
        df = df.sort_values("trade_date")
        snap = df.groupby(["index_code", "con_code", "ym"], as_index=False).tail(1)
        if snap.empty:
            return pd.DataFrame()

        # 3. 月末网格（标准月末交易日）。年份用真实输出区间（不含 buffer）
        start_year = int(start_date[:4]) if start_date else int(snap["ym"].min()[:4])
        end_year = int(end_date[:4]) if end_date else int(snap["ym"].max()[:4])
        last_tds = get_monthly_last_tradedate(self.engine, start_year, end_year)
        # get_monthly_last_tradedate 返回 yyyymmdd，统一转 yyyy-mm-dd（约定）
        ym_to_eom = {td[:6]: f"{td[:4]}-{td[4:6]}-{td[6:]}" for td in last_tds}
        # 输出目标月：落在 [start_date 月, end_date 月] 内
        start_ym = start_date[:6] if start_date else min(ym_to_eom)
        end_ym = end_date[:6] if end_date else max(ym_to_eom)
        target_yms = sorted(ym for ym in ym_to_eom if start_ym <= ym <= end_ym)
        if not target_yms:
            return pd.DataFrame()

        # 4. 前向填充：每个 index_code，每个目标月取 <= 它的最近调样月成分
        out_frames = []
        for code, g in snap.groupby("index_code"):
            avail_yms = sorted(g["ym"].unique())
            if not avail_yms:
                continue
            snaps = {ym: sub for ym, sub in g.groupby("ym")}
            for target_ym in target_yms:
                idx = bisect.bisect_right(avail_yms, target_ym) - 1
                if idx < 0:
                    continue  # 该月之前无任何调样数据，跳过（不凭空造）
                src = snaps[avail_yms[idx]]
                out = pd.DataFrame(
                    {
                        "trade_date": ym_to_eom[target_ym],
                        "ts_code": src["con_code"].values,
                        "index_code": code,
                        "index_name": INDEX_NAME.get(code, code),
                        "weight": src["weight"].values,
                    }
                )
                out_frames.append(out)

        if not out_frames:
            return pd.DataFrame()
        final = pd.concat(out_frames, ignore_index=True)
        final = final.replace([np.nan, np.inf, -np.inf, pd.NaT], None)
        self.logger.info(
            f"index_membership_monthly 完成: {len(final)} 行, "
            f"{final['index_code'].nunique()} 指数, {len(target_yms)} 个月"
        )
        return final
