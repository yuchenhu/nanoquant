"""
panel_market_sentiment_monthly 增强看板
横轴=时间 / 纵轴=指标 / 拆维度=分面子图 / 选指标=多选叠加
"""
import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.font_manager as fm
import seaborn as sns
from config.database import engine

# ─── 中文字体 ───
_CHINESE_FONTS = [f.name for f in fm.fontManager.ttflist if any(k in f.name for k in ["YaHei", "SimHei", "Hei", "Song", "Ming"])]
if _CHINESE_FONTS:
    plt.rcParams["font.sans-serif"] = [_CHINESE_FONTS[0]] + plt.rcParams["font.sans-serif"]
plt.rcParams["axes.unicode_minus"] = False

st.set_page_config(page_title="市场情绪月频看板", layout="wide")
st.title("市场情绪月频指标 — 量级/分布/趋势")
st.caption("数据: `panel_market_sentiment_monthly` | 维度: 全A + 五大指数")

# ═══════════════════════════════════════════
# 数据加载 + 缓存
# ═══════════════════════════════════════════
@st.cache_data(ttl=300)
def load_data():
    df = pd.read_sql(
        "SELECT * FROM panel_market_sentiment_monthly ORDER BY trade_date, dimension_type, dimension_value",
        engine
    )
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["dim_label"] = df["dimension_type"] + "/" + df["dimension_value"]
    return df

df = load_data()

# ═══════════════════════════════════════════
# 指标分组 & 标签
# ═══════════════════════════════════════════
INDICATOR_GROUPS = {
    "价格趋势": ["idx_close", "ma60", "ma250", "idx_ret_1m", "idx_ret_3m", "idx_ret_12m", "max_drawdown_1y"],
    "情绪广度": ["profit_ratio", "up_down_ratio", "pct_above_ma60", "pct_above_ma250", "limit_up_count"],
    "流动性/成交": ["idx_amount", "turnover_rate_median", "amount_pct_3m", "amount_pct_1y", "amount_gini"],
    "波动/风险": ["idx_volatility_20", "idx_volatility_60", "avg_correlation", "cross_sectional_vol", "downside_vol_ratio"],
    "估值": ["pe_ttm_median", "pb_median", "pe_pct_5y", "pb_pct_5y", "pe_dispersion", "pb_pe_divergence", "dv_ttm_median"],
    "资金流向": ["north_money", "margin_balance", "net_inflow_ratio", "inflow_direction_pct", "inflow_stability", "inflow_breadth", "institutional_pct"],
}

INDICATOR_LABELS = {
    "idx_close": "指数收盘价", "ma60": "MA60", "ma250": "MA250",
    "idx_ret_1m": "指数1月收益率", "idx_ret_3m": "指数3月收益率", "idx_ret_12m": "指数12月收益率",
    "max_drawdown_1y": "1年最大回撤", "profit_ratio": "上涨占比", "up_down_ratio": "涨跌比",
    "pct_above_ma60": "站上MA60占比", "pct_above_ma250": "站上MA250占比",
    "limit_up_count": "涨停家数", "idx_amount": "指数成交额",
    "turnover_rate_median": "换手率中位数", "amount_pct_3m": "成交额3月分位",
    "amount_pct_1y": "成交额1年分位", "amount_gini": "成交额基尼系数",
    "idx_volatility_20": "20日波动率", "idx_volatility_60": "60日波动率",
    "avg_correlation": "平均相关性", "cross_sectional_vol": "截面波动率",
    "downside_vol_ratio": "下行波动比", "pe_ttm_median": "PE_TTM中位数",
    "pb_median": "PB中位数", "pe_pct_5y": "PE 5年分位", "pb_pct_5y": "PB 5年分位",
    "pe_dispersion": "PE离散度", "pb_pe_divergence": "PB-PE背离", "dv_ttm_median": "股息率TTM中位数",
    "north_money": "北向资金", "margin_balance": "融资余额",
    "net_inflow_ratio": "资金净流入比", "inflow_direction_pct": "流入方向占比",
    "inflow_stability": "流入稳定性", "inflow_breadth": "流入广度",
    "institutional_pct": "机构成交占比",
}

ALL_INDICATORS = [col for grp in INDICATOR_GROUPS.values() for col in grp]
NUMERIC_COLS = [c for c in ALL_INDICATORS if c in df.columns]

# ═══════════════════════════════════════════
# 侧边栏
# ═══════════════════════════════════════════
st.sidebar.header("1. 指标选择（多选）")
group = st.sidebar.selectbox("指标分组", list(INDICATOR_GROUPS.keys()))
indicators_in_group = [c for c in INDICATOR_GROUPS[group] if c in df.columns]
default_idx = [indicators_in_group[0]] if indicators_in_group else []
selected_indicators = st.sidebar.multiselect(
    "选择指标（趋势图叠加、分布图逐个展示）",
    indicators_in_group,
    default=default_idx,
    format_func=lambda x: f"{INDICATOR_LABELS.get(x, x)} ({x})"
)

st.sidebar.header("2. 维度过滤")
dim_options = sorted(df["dim_label"].unique())
selected_dims = st.sidebar.multiselect(
    "选择维度", dim_options,
    default=dim_options
)

st.sidebar.header("3. 日期切片")
date_min = df["trade_date"].min().date()
date_max = df["trade_date"].max().date()
date_range = st.sidebar.date_input(
    "日期范围",
    value=(date_min, date_max),
    min_value=date_min,
    max_value=date_max,
)
if isinstance(date_range, tuple) and len(date_range) == 2:
    start_date, end_date = pd.Timestamp(date_range[0]), pd.Timestamp(date_range[1])
else:
    start_date, end_date = pd.Timestamp(date_min), pd.Timestamp(date_max)

st.sidebar.header("4. 异常值剔除")
trim_pct = st.sidebar.slider(
    "剔除尾部百分比（0=不剔除）",
    min_value=0.0, max_value=20.0, value=0.0, step=0.5,
    help="对选中指标按分位数剔除两端极端值。仅影响分布Tab的描述统计和直方图。"
)

st.sidebar.divider()
st.sidebar.caption(f"原始: {len(df)}行 | {df['trade_date'].min().date()} ~ {df['trade_date'].max().date()}")

# ═══════════════════════════════════════════
# 数据准备
# ═══════════════════════════════════════════
df_sel = df[
    (df["dim_label"].isin(selected_dims))
    & (df["trade_date"] >= start_date)
    & (df["trade_date"] <= end_date)
].copy()

def trim_outliers(series: pd.Series, pct: float) -> pd.Series:
    if pct <= 0 or series.dropna().empty:
        return series
    lo, hi = series.quantile(pct / 100), series.quantile(1 - pct / 100)
    return series.where((series >= lo) & (series <= hi))

# ═══════════════════════════════════════════
# Tabs
# ═══════════════════════════════════════════
tab1, tab2, tab3, tab4 = st.tabs(["📊 分布", "📈 趋势", "📋 概览表", "🔗 相关性"])

# ─── Tab 1: 分布 ───
with tab1:
    if not selected_indicators:
        st.warning("请先在侧边栏选择至少一个指标")
    else:
        # 分布指标选择器：默认第一个，可切换
        dist_col = st.selectbox(
            "查看分布的指标",
            selected_indicators,
            format_func=lambda x: f"{INDICATOR_LABELS.get(x, x)}",
            key="dist_indicator"
        )
        col1, col2 = st.columns(2)

        with col1:
            st.subheader(f"直方图 — {INDICATOR_LABELS.get(dist_col, dist_col)}")
            data_hist = df_sel[dist_col].dropna()

            if trim_pct > 0:
                data_trimmed = trim_outliers(df_sel[dist_col].copy(), trim_pct).dropna()
                n_dropped = len(data_hist) - len(data_trimmed)
                st.caption(f"已剔除 {n_dropped} 个极端值（{trim_pct:.1f}% 尾部）")
            else:
                data_trimmed = data_hist
                st.caption(f"有效样本: {len(data_hist)}")

            if len(data_hist) > 0:
                fig, ax = plt.subplots(figsize=(6, 4))
                bins = min(20, len(data_hist) // 3 + 1)
                if trim_pct > 0 and len(data_trimmed) > 0:
                    ax.hist(data_hist, bins=bins, edgecolor="white", color="gray", alpha=0.3, label=f"原始 (n={len(data_hist)})")
                    ax.hist(data_trimmed, bins=bins, edgecolor="white", color="#4C72B0", alpha=0.85, label=f"剔除后 (n={len(data_trimmed)})")
                else:
                    ax.hist(data_hist, bins=bins, edgecolor="white", color="#4C72B0", alpha=0.85)
                ax.axvline(data_trimmed.median(), color="red", linestyle="--", linewidth=1.5, label=f"中位数={data_trimmed.median():.4f}")
                ax.axvline(data_trimmed.mean(), color="orange", linestyle="--", linewidth=1.5, label=f"均值={data_trimmed.mean():.4f}")
                ax.legend(fontsize=7, loc="upper right")
                ax.set_xlabel(dist_col)
                st.pyplot(fig)
            else:
                st.warning("无数据")

        with col2:
            st.subheader("箱线图 × 维度")
            if dist_col in df_sel.columns and df_sel[dist_col].notna().sum() > 0:
                fig, ax = plt.subplots(figsize=(6, 4))
                order = sorted(df_sel["dim_label"].unique())
                data_box = [df_sel[df_sel["dim_label"] == d][dist_col].dropna().values for d in order]
                bp = ax.boxplot(data_box, patch_artist=True, showfliers=True)
                ax.set_xticklabels(order, rotation=45, fontsize=9)
                for patch in bp['boxes']:
                    patch.set_facecolor("#55A868")
                    patch.set_alpha(0.7)
                for flier in bp['fliers']:
                    flier.set_markerfacecolor('red')
                    flier.set_markeredgecolor('red')
                    flier.set_markersize(4)
                    flier.set_alpha(0.6)
                ax.set_ylabel(dist_col)
                st.pyplot(fig)
            else:
                st.warning("该指标在当前维度下无数据")

        # 描述统计
        st.divider()
        if trim_pct > 0:
            col_s1, col_s2 = st.columns(2)
            with col_s1:
                st.subheader("描述统计 — 原始")
                stats_raw = df_sel[dist_col].describe().to_frame().T.round(4)
                stats_raw.insert(0, "null_cnt", df_sel[dist_col].isnull().sum())
                st.dataframe(stats_raw, use_container_width=True)
            with col_s2:
                st.subheader(f"描述统计 — 剔除 {trim_pct}% 尾部后")
                stats_trim = trim_outliers(df_sel[dist_col].copy(), trim_pct).describe().to_frame().T.round(4)
                stats_trim.insert(0, "dropped", df_sel[dist_col].notna().sum() - trim_outliers(df_sel[dist_col].copy(), trim_pct).notna().sum())
                st.dataframe(stats_trim, use_container_width=True)
        else:
            st.subheader("描述统计")
            stats = df_sel[dist_col].describe().to_frame().T.round(4)
            stats.insert(0, "null_cnt", df_sel[dist_col].isnull().sum())
            stats.insert(1, "null_pct", f"{df_sel[dist_col].isnull().mean():.1%}")
            st.dataframe(stats, use_container_width=True)

        st.subheader("按维度分组统计")
        grp_stats = df_sel.groupby("dim_label")[dist_col].agg(
            ["count", "mean", "std", "min", "median", "max"]
        ).round(4)
        grp_stats["null"] = df_sel.groupby("dim_label")[dist_col].apply(lambda x: x.isnull().sum())
        st.dataframe(grp_stats, use_container_width=True)

# ─── Tab 2: 趋势（横轴=时间，纵轴=指标，按维度拆分子图） ───
with tab2:
    if not selected_indicators:
        st.warning("请先在侧边栏选择至少一个指标")
    else:
        # 控制面板
        col_ctrl, col_chart = st.columns([0.25, 0.75])

        with col_ctrl:
            st.subheader("图表控制")
            zscore = st.checkbox("标准化 (Z-score)", value=False,
                help="不同量纲的指标叠加时，勾选后统一转为 Z-score 便于对比趋势形态")
            zscore_eps = st.number_input("Z-score 分母下限（防除零）", 1e-6, 0.1, 0.01, 0.001,
                help="标准差低于此值时视为常数，Z-score 置零", disabled=not zscore)

            ma_windows = st.multiselect(
                "移动均线窗口（月）",
                options=[2, 3, 4, 6],
                default=[],
                help="对每个（维度×指标）时间序列叠加滚动均线"
            )
            ma_alpha = st.slider("MA 线透明度", 0.3, 1.0, 0.60, 0.1, disabled=not ma_windows)

            show_markers = st.checkbox("显示数据点标记", value=True)

            st.divider()
            st.caption("提示：不同量纲指标叠加时建议开启 Z-score。月频 MA3 至少需 3 个连续月。")

        with col_chart:
            st.subheader(f"趋势（{len(selected_dims)}维度 × {len(selected_indicators)}指标）")

            dims = sorted(df_sel["dim_label"].unique())
            n_dims = len(dims)
            n_indicators = len(selected_indicators)

            if n_dims == 0:
                st.warning("无维度数据")
            else:
                # 按维度拆分子图：每个维度一行
                n_cols = min(2, n_dims)
                n_rows = (n_dims + n_cols - 1) // n_cols
                fig, axes = plt.subplots(
                    n_rows, n_cols,
                    figsize=(14, 3.5 * n_rows),
                    squeeze=False,
                    sharex=True
                )
                axes_flat = axes.flatten()

                # 颜色 = 按指标
                indicator_colors = plt.cm.tab10(np.linspace(0, 1, max(n_indicators, 1)))
                color_map = dict(zip(selected_indicators, indicator_colors))

                for i, dim in enumerate(dims):
                    ax = axes_flat[i]
                    sub = df_sel[df_sel["dim_label"] == dim].sort_values("trade_date")

                    for ind in selected_indicators:
                        series = sub.set_index("trade_date")[ind].dropna()
                        if len(series) == 0:
                            continue

                        # Z-score
                        if zscore and series.std() > zscore_eps:
                            y_vals = (series - series.mean()) / series.std()
                            y_label = "Z-score"
                        elif zscore:
                            y_vals = pd.Series(0, index=series.index)
                            y_label = "Z-score"
                        else:
                            y_vals = series
                            y_label = ind

                        c = color_map[ind]
                        label = INDICATOR_LABELS.get(ind, ind)
                        ax.plot(y_vals.index, y_vals.values,
                                marker="o" if show_markers else None,
                                label=label, color=c, linewidth=1.5,
                                markersize=4, alpha=0.85)

                        # MA
                        for w in ma_windows:
                            ma_s = y_vals.rolling(window=w, min_periods=w).mean()
                            if ma_s.notna().sum() > 0:
                                ax.plot(ma_s.index, ma_s.values,
                                        linestyle="--", color=c, linewidth=0.9,
                                        alpha=ma_alpha)

                    ax.set_title(dim, fontsize=11, fontweight="bold")
                    ax.set_ylabel(y_label if zscore else "", fontsize=8)
                    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
                    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=max(1, len(sub)//8)))
                    ax.legend(fontsize=7, loc="upper left", framealpha=0.7)
                    ax.grid(True, alpha=0.3)

                # 隐藏多余子图
                for j in range(n_dims, len(axes_flat)):
                    axes_flat[j].set_visible(False)

                # 共享 Y 轴标签（不标准化时提示量纲不同）
                if not zscore and n_indicators > 1:
                    fig.text(0.02, 0.5, "原始值（量纲不同，看趋势形态）",
                             va="center", rotation="vertical", fontsize=9, color="gray")
                elif zscore:
                    fig.text(0.02, 0.5, "Z-score", va="center", rotation="vertical", fontsize=9, color="gray")

                fig.autofmt_xdate()
                fig.tight_layout(rect=[0.03, 0, 1, 1])
                st.pyplot(fig)

# ─── Tab 3: 概览表 ───
with tab3:
    st.subheader("全部指标概览")
    view_mode = st.radio("显示方式", ["按维度（纵向展开）", "按月份（横向对比）"], horizontal=True,
                         label_visibility="collapsed")

    if view_mode == "按维度（纵向展开）":
        for dim in sorted(df_sel["dim_label"].unique()):
            with st.expander(dim, expanded=(len(df_sel["dim_label"].unique()) <= 3)):
                sub = df_sel[df_sel["dim_label"] == dim].sort_values("trade_date")
                show_cols = ["trade_date"] + [c for c in NUMERIC_COLS if c in sub.columns and sub[c].notna().any()]
                sub_show = sub[show_cols].copy()
                sub_show["trade_date"] = sub_show["trade_date"].dt.strftime("%Y-%m")
                st.dataframe(sub_show.set_index("trade_date").T, use_container_width=True)
    else:
        pivot_data = df_sel.pivot_table(
            index="trade_date", columns="dim_label",
            values=[c for c in NUMERIC_COLS if c in df_sel.columns and df_sel[c].notna().any()],
            aggfunc="first"
        )
        st.dataframe(pivot_data.round(4), use_container_width=True)

# ─── Tab 4: 相关性 ───
with tab4:
    st.subheader("指标间相关性矩阵")
    col_c1, col_c2 = st.columns([0.3, 0.7])
    with col_c1:
        corr_method = st.selectbox("相关方法", ["pearson", "spearman"], index=0)
        corr_group = st.selectbox("限定指标分组", ["全部"] + list(INDICATOR_GROUPS.keys()), index=0)
        st.caption("提示：部分指标因缺失率过高不参与计算")

    with col_c2:
        if corr_group == "全部":
            corr_candidate_cols = [c for c in NUMERIC_COLS if df_sel[c].notna().sum() > 10]
        else:
            corr_candidate_cols = [c for c in INDICATOR_GROUPS[corr_group] if c in df_sel.columns and df_sel[c].notna().sum() > 10]

        if len(corr_candidate_cols) >= 2:
            corr = df_sel[corr_candidate_cols].corr(method=corr_method)
            short_labels = {c: INDICATOR_LABELS.get(c, c)[:8] for c in corr_candidate_cols}
            corr_renamed = corr.rename(index=short_labels, columns=short_labels)

            fig, ax = plt.subplots(figsize=(max(8, len(corr_candidate_cols) * 0.7),
                                           max(6, len(corr_candidate_cols) * 0.6)))
            mask = np.triu(np.ones_like(corr_renamed, dtype=bool), k=1)
            sns.heatmap(corr_renamed, mask=mask, annot=True, fmt=".2f", cmap="RdBu_r",
                        center=0, vmin=-1, vmax=1, ax=ax, linewidths=0.5,
                        annot_kws={"size": 8})
            ax.set_title(f"{corr_method.title()} Correlation ({corr_group})", fontsize=13)
            ax.tick_params(axis='both', labelsize=8)
            st.pyplot(fig)
        else:
            st.warning("有效指标不足，无法计算相关性")

    st.divider()
    st.subheader("缺失值概览")
    null_summary = df_sel[NUMERIC_COLS].isnull().sum()
    null_summary = null_summary[null_summary > 0].sort_values(ascending=False)
    if len(null_summary) > 0:
        null_df = pd.DataFrame({
            "指标": [INDICATOR_LABELS.get(c, c) for c in null_summary.index],
            "列名": null_summary.index,
            "缺失数": null_summary.values,
            "缺失率": (null_summary.values / len(df_sel) * 100).round(1).astype(str) + "%"
        })
        st.dataframe(null_df, use_container_width=True)
    else:
        st.success("所选维度下无缺失值")
