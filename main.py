# -*- coding: utf-8 -*-
"""
主动基金周度筛选 —— 周报主流程（每周六更新）。

  python main.py               # 真实抓取 + 评分 + 回测 + 生成素材
  python main.py --no-backtest # 跳过滚动回测（快速出榜）
  python main.py --mock        # 用合成数据跑通媒体流水线（离线自测，不联网）
  python main.py --date 2026-06-13 # 指定日期（默认今天）

产出（output/ 目录，文件名带日期，如 _2026-06-13）：
  cover_YYYY-MM-DD.png   周报封面卡（视频封面/信息流钩子）
  card_YYYY-MM-DD.png    Top20 排行长图（完整榜单）
  text_YYYY-MM-DD.txt    通用文案（不分平台）
  report_YYYY-MM-DD.xlsx Excel 明细（Top20 + 全部 + 回测）
  score_detail_YYYY-MM-DD.xlsx 评分明细（硬筛通过基金的 6 维子分/贡献/原始指标，便于对比）
同时把本期榜单快照写入 data/YYYY-MM-DD.json，供下周对比「新进 / 名次变动」。

推送由 notify.py 负责（读 WECOM_WEBHOOK / TG_*），视频由 video.py 合成。
"""

import io
import sys
import json
import math
import random
import logging
import pathlib
import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import card

ROOT = pathlib.Path(__file__).parent
DATA = ROOT / "data"
OUT = ROOT / "output"

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("main")

# Top20 排行长图里展示的关键列（写入 Excel 时也用）
SNAPSHOT_COLS = ["基金代码", "基金简称", "基金经理", "综合得分", "评级"]

# 评分明细各维度: (展示名, 子分列名, config.SCORE_WEIGHTS 键, 背后原始指标列)
SCORE_DIMENSIONS = [
    ("稳定性", "得分_稳定性", "stability", "业绩排名分位"),
    ("熊市",   "得分_熊市",   "bear_perf", "熊市平均回撤"),
    ("任职",   "得分_任职",   "tenure",    "经理任职年限"),
    ("框架",   "得分_框架",   "framework", "卡玛比率"),
    ("风格",   "得分_风格",   "style",     "行业稳定性"),
    ("规模",   "得分_规模",   "scale",     "基金规模"),
]


# ---------------- 月份 / 快照 ----------------

def _run_key(date):
    # 周报: 用当期(周六)日期做键, 文件名/快照均按此命名, 环比自动对上一份快照
    return date.isoformat()


def _load_prev_snapshot(cur_key):
    """取早于当前月、最近的一份月度快照。返回 {code: row}。"""
    if not DATA.exists():
        return {}
    snaps = sorted(p.stem for p in DATA.glob("*.json"))
    prev = [s for s in snaps if s < cur_key]
    if not prev:
        return {}
    rows = json.loads((DATA / f"{prev[-1]}.json").read_text(encoding="utf-8"))
    logger.info(f"环比基准月: {prev[-1]}（{len(rows)} 只）")
    return {r["基金代码"]: r for r in rows}


def _annotate_changes(funds, prev):
    """对每只基金注入 change(new/up/down/None) 与 rank_delta。"""
    prev_rank = {c: prev[c].get("rank") for c in prev}
    for i, r in enumerate(funds):
        code = r.get("基金代码")
        cur_rank = i + 1
        pr = prev_rank.get(code)
        if pr is None:
            r["change"], r["rank_delta"] = "new", None
        elif pr > cur_rank:
            r["change"], r["rank_delta"] = "up", pr - cur_rank
        elif pr < cur_rank:
            r["change"], r["rank_delta"] = "down", cur_rank - pr
        else:
            r["change"], r["rank_delta"] = None, 0
    return funds


def _save_snapshot(funds, cur_key):
    DATA.mkdir(exist_ok=True)
    rows = []
    for i, r in enumerate(funds):
        row = {k: _jsonable(r.get(k)) for k in SNAPSHOT_COLS}
        row["rank"] = i + 1
        rows.append(row)
    (DATA / f"{cur_key}.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"已写快照: data/{cur_key}.json")


def _jsonable(v):
    # NaN / NaT 归一为 None（二者自身不等于自身）
    try:
        if v is None or v != v:
            return None
    except Exception:
        pass
    # 时间类型（pd.Timestamp 继承 datetime）→ ISO 字符串
    if isinstance(v, (datetime.datetime, datetime.date)):
        return v.isoformat()
    if hasattr(v, "item"):       # numpy 标量 → python 标量
        try:
            iv = v.item()
            if isinstance(iv, (datetime.datetime, datetime.date)):
                return iv.isoformat()
            return iv
        except Exception:
            pass
    return v


# ---------------- 判重 + 交易日守卫 ----------------
# 周更改为多兜底槽位（Mon-Fri 多次）后，需保证「一周只出一期」且不在非交易日空跑。
# 决策放在 main(--check) 里输出给 CI，由 workflow 用 step output 闸住后续步骤。

def _reported_this_week(cur_date):
    """本周（周一至今）是否已有成功快照。返回命中的日期串(YYYY-MM-DD)或 None。
    快照仅在一次完整成功的 run 末尾写入，故它就是「本期已出」的可靠标记；
    半途失败（无快照）→ 后续兜底槽位会重试，正是我们要的。"""
    if not DATA.exists():
        return None
    week_start = cur_date - datetime.timedelta(days=cur_date.weekday())
    hit = None
    for p in DATA.glob("*.json"):
        try:
            d = datetime.date.fromisoformat(p.stem)
        except ValueError:
            continue
        if week_start <= d <= cur_date and (hit is None or d > datetime.date.fromisoformat(hit)):
            hit = p.stem
    return hit


def _is_trading_day(d):
    """用 akshare 上交所交易日历判断 d 是否 A 股交易日（含节假日/调休补班）。
    日历获取失败时 fail-open（返回 True）：宁可多出一次也不漏播，判重已防同周重复。"""
    try:
        import akshare as ak
        import pandas as pd
        cal = ak.tool_trade_date_hist_sina()
        days = set(pd.to_datetime(cal["trade_date"]).dt.date)
        return d in days
    except Exception as e:
        logger.warning(f"交易日历获取失败，按交易日处理：{e}")
        return True


def _should_run(date, argv):
    """判定本次调度是否应执行：先判重（免费、本地）后查交易日（联网）。
    返回 (run: bool, reason: str)。手动触发 / --force / --mock 一律放行。"""
    import os
    forced = ("--force" in argv or "--mock" in argv
              or os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch")
    if forced:
        return True, "forced（手动触发 / --force / --mock）"
    hit = _reported_this_week(date)
    if hit:
        return False, f"本周已生成报告 data/{hit}.json"
    if not _is_trading_day(date):
        return False, f"{date} 非 A 股交易日"
    return True, "本周未出且为交易日"


# ---------------- 回测 → 上下文 ----------------

def _annualize(r_pct, days):
    if r_pct is None or days is None or days <= 0:
        return None
    return ((1 + r_pct / 100) ** (365.0 / days) - 1) * 100


def _backtest_context(rolling):
    """把回测结果折算成封面/视频用的 ctx 片段。年化各窗口后取均值。"""
    if not rolling or not rolling.get("per_window"):
        return {}
    rows, alphas = [], []
    for s in rolling["per_window"]:
        days = s.get("hold_days")
        a = None
        ann_top = _annualize(s.get("top_n_avg_return_pct"), days)
        ann_uni = _annualize(s.get("universe_avg_return_pct"), days)
        if ann_top is not None and ann_uni is not None:
            a = round(ann_top - ann_uni, 1)
            alphas.append(a)
        as_of = s.get("as_of", "")
        label = f"起点 {as_of[:7]}" if as_of else "起点"
        rows.append({"label": label, "alpha": a})
    alpha_pct = round(sum(alphas) / len(alphas), 1) if alphas else None
    return {"alpha_pct": alpha_pct, "backtest_rows": rows,
            "hit_rate": rolling["aggregate"].get("positive_alpha_windows_vs_universe")}


# ---------------- 文案 ----------------

def write_copy(funds, ctx, date):
    wd = "一二三四五六日"[date.weekday()]
    lines = [f"📊 {date.month}月{date.day}日(周{wd}) 主动基金周榜"
             f"（量化初筛 Top{len(funds)}）", ""]
    if ctx.get("alpha_pct") is not None:
        lines.append(f"本榜评分体系滚动回测年化超额 {ctx['alpha_pct']:+.1f}%"
                     f"（胜率 {ctx.get('hit_rate', '—')}）")
        lines.append("")
    for i, r in enumerate(funds, 1):
        name = r.get("基金简称", "")
        code = r.get("基金代码", "")
        score = r.get("综合得分")
        grade = r.get("评级", "")
        mark = {"new": "🆕", "up": "⬆️", "down": "⬇️"}.get(r.get("change"), "")
        head = f"{i}. {name}（{code}） {score:.1f}分 {grade} {mark}".rstrip()
        lines.append(head)
        reason = r.get("入选原因")
        if reason:
            lines.append(f"   {reason}")
    lines += [
        "",
        f"筛选范围：全市场股票型/混合型/QDII，候选池 {ctx.get('pool_n', '—')} 只，"
        f"通过 6 项硬筛 {ctx.get('passed_n', '—')} 只。",
        "评分维度：业绩稳定性 / 熊市表现 / 经理任职 / 投资框架(卡玛) / 风格一致性 / 规模。",
        "🆕=本周新进榜  ⬆️/⬇️=较上周名次变动",
        "数据来自天天基金网公开数据，以基金公司公告为准。仅为信息整理，不构成投资建议。",
        "",
        "#基金 #主动基金 #基金定投 #量化筛选 #基金推荐",
    ]
    text = "\n".join(lines)
    (OUT / f"text_{_run_key(date)}.txt").write_text(text, encoding="utf-8")
    return text


# ---------------- Excel ----------------

def write_excel(top_df, all_df, rolling, path):
    import pandas as pd
    drop = ["_nav_history", "_nav_holdout", "_composite", "_code", "_founded",
            "_行业稳定性分位"]
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        top_df.drop(columns=drop, errors="ignore").to_excel(
            w, sheet_name="Top20", index=False)
        all_df.drop(columns=drop, errors="ignore").to_excel(
            w, sheet_name="全部候选", index=False)
        if rolling and rolling.get("aggregate"):
            pd.DataFrame([rolling["aggregate"]]).T.rename(
                columns={0: "value"}).to_excel(w, sheet_name="回测聚合")
            if rolling.get("per_window"):
                pd.DataFrame(rolling["per_window"]).to_excel(
                    w, sheet_name="回测各起点", index=False)
    logger.info(f"已写出: {path}")


def write_score_detail_excel(all_df, path):
    """评分明细：每只硬筛通过基金的 6 维子分 + 加权贡献 + 背后原始指标，
    单列对齐便于横向对比子分来源（report.xlsx 那份列太杂，不利于盯单维度）。"""
    import pandas as pd
    from src.config import SCORE_WEIGHTS

    if "硬筛通过" in all_df.columns:
        df = all_df[all_df["硬筛通过"] == True].copy()
    else:
        df = all_df.copy()
    if "综合得分" not in df.columns:
        logger.warning("评分明细：无『综合得分』列，跳过")
        return
    df = df.sort_values("综合得分", ascending=False).reset_index(drop=True)

    out = pd.DataFrame()
    out["排名"] = range(1, len(df) + 1)
    for col in ["基金代码", "基金简称", "基金经理", "基金类型"]:
        if col in df.columns:
            out[col] = df[col].values
    out["综合得分"] = pd.to_numeric(df["综合得分"], errors="coerce").round(2).values
    if "评级" in df.columns:
        out["评级"] = df["评级"].values

    # 6 维子分（列名带权重）+ 该维加权贡献（子分×权重，加总≈综合得分）
    for label, score_col, wkey, _raw in SCORE_DIMENSIONS:
        if score_col not in df.columns:
            continue
        w = SCORE_WEIGHTS.get(wkey, 0)
        s = pd.to_numeric(df[score_col], errors="coerce")
        out[f"{label}分(×{w})"] = s.round(1).values
        out[f"{label}贡献"] = (s * w).round(2).values

    # 背后原始指标，便于核对子分由来
    raw_cols = ["业绩排名分位", "卡玛比率", "经理任职年限", "基金规模",
                "熊市平均回撤", "熊市回撤分位", "熊市数", "行业稳定性",
                "年化波动率", "近3年最大回撤", "年化收益率", "夏普比率"]
    for col in raw_cols:
        if col in df.columns:
            out[col] = pd.to_numeric(df[col], errors="coerce").round(2).values

    with pd.ExcelWriter(path, engine="openpyxl") as w:
        out.to_excel(w, sheet_name="评分明细", index=False)
        wrows = [{"维度": label, "权重": SCORE_WEIGHTS.get(wkey, 0), "背后原始指标": raw}
                 for label, _sc, wkey, raw in SCORE_DIMENSIONS]
        pd.DataFrame(wrows).to_excel(w, sheet_name="权重说明", index=False)
    logger.info(f"已写出评分明细（{len(out)} 只）: {path}")


# ---------------- 合成数据（--mock） ----------------

def _mock_run():
    random.seed(20260613)
    names = ["易方达蓝筹精选", "富国天惠成长", "中欧医疗健康C", "广发高端制造A",
             "交银瑞和精选", "汇添富消费行业", "兴全合润", "景顺长城新兴成长",
             "工银前沿医疗", "银华富裕主题", "华夏回报A", "嘉实增长",
             "南方优选成长", "博时主题行业", "鹏华环保产业", "国富中小盘",
             "大成高新技术", "招商先锋", "安信优势增长", "信达澳银新能源产业"]
    mgrs = ["张坤", "朱少醒", "葛兰", "刘格菘", "王崇", "胡昕炜", "谢治宇",
            "刘彦春", "赵蓓", "焦巍"]
    funds = []
    for i, n in enumerate(names):
        score = round(92 - i * 1.4 + random.uniform(-1, 1), 1)
        funds.append({
            "基金简称": n, "基金代码": f"{random.randint(1, 999999):06d}",
            "基金经理": random.choice(mgrs), "综合得分": score,
            "评级": "A 级" if i < 4 else ("B 级" if i < 12 else "C 级"),
            "入选原因": (f"近3年业绩排候选池前 {random.randint(3, 30)}%; "
                       f"历{random.randint(1, 3)}轮熊市平均回撤"
                       f"{random.uniform(15, 30):.1f}%优于同类; "
                       f"卡玛比 {random.uniform(0.5, 1.5):.2f} 风险收益均衡"),
            "经理任职年限": round(random.uniform(3, 16), 1),
            "基金规模": round(random.uniform(2, 90), 1),
            "卡玛比率": round(random.uniform(0.4, 1.6), 2),
            "近3年最大回撤": round(random.uniform(20, 44), 1),
            "业绩排名分位": round(random.uniform(2, 40), 1),
            "熊市数": random.randint(0, 3),
            "近1年收益率": round(random.uniform(-15, 40), 1),
        })
    ctx = {"pool_n": 300, "passed_n": 87, "top_n": len(funds),
           "alpha_pct": 4.2, "hit_rate": "4/5",
           "backtest_rows": [{"label": "起点 2021-06", "alpha": 5.1},
                             {"label": "起点 2022-06", "alpha": 3.2},
                             {"label": "起点 2023-06", "alpha": 6.0},
                             {"label": "起点 2024-06", "alpha": -1.1},
                             {"label": "起点 2024-12", "alpha": 4.8}]}
    return funds, None, None, ctx


# ---------------- 主流程 ----------------

def main():
    argv = sys.argv[1:]
    mock = "--mock" in argv
    no_backtest = "--no-backtest" in argv
    date = datetime.date.today()
    if "--date" in argv:
        raw = argv[argv.index("--date") + 1]
        parts = [int(x) for x in raw.split("-")]
        date = datetime.date(parts[0], parts[1], parts[2] if len(parts) > 2 else 1)

    # 守卫模式：只输出「是否应执行」给 CI（写入 $GITHUB_OUTPUT），不做任何抓取。
    # run= 行走 stdout，日志走 stderr，二者不串。
    if "--check" in argv:
        run, reason = _should_run(date, argv)
        logger.info(f"[guard] run={run} :: {reason}")
        print(f"run={'true' if run else 'false'}")
        return

    OUT.mkdir(exist_ok=True)
    cur_key = _run_key(date)

    if mock:
        logger.info("=== MOCK 模式：使用合成数据 ===")
        funds, top_df, all_df, ctx = _mock_run()
        rolling = None
    else:
        from src.screener import run_screening
        top_df, all_df = run_screening()
        if len(top_df) == 0:
            logger.error("本月无基金通过筛选，终止")
            sys.exit(1)
        funds = top_df.to_dict("records")
        passed_n = int(all_df["硬筛通过"].sum()) if "硬筛通过" in all_df else len(funds)
        ctx = {"pool_n": int(len(all_df)), "passed_n": passed_n,
               "top_n": len(funds)}

        rolling = None
        if not no_backtest:
            try:
                from src.backtest import run_rolling_backtest
                rolling = run_rolling_backtest()
                ctx.update(_backtest_context(rolling))
            except Exception as e:
                logger.warning(f"回测失败，跳过：{e}")

    # 环比变动
    prev = _load_prev_snapshot(cur_key)
    funds = _annotate_changes(funds, prev)

    # 渲染素材
    cover = OUT / f"cover_{cur_key}.png"
    card_img = OUT / f"card_{cur_key}.png"
    card.render_cover(funds, ctx, date, str(cover))
    card.render_card(funds, ctx, date, str(card_img))
    write_copy(funds, ctx, date)

    # 媒体载荷（供 video.py 读取完整榜单 + 上下文，避免重跑筛选）
    payload = {
        "date": date.isoformat(),
        "ctx": {k: _jsonable(v) for k, v in ctx.items()},
        "funds": [{k: _jsonable(v) for k, v in r.items()} for r in funds],
    }
    (OUT / f"payload_{cur_key}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # 快照 + Excel（mock 无 DataFrame，跳过 Excel）
    _save_snapshot(funds, cur_key)
    if not mock and top_df is not None:
        try:
            write_excel(top_df, all_df, rolling, OUT / f"report_{cur_key}.xlsx")
        except Exception as e:
            logger.warning(f"Excel 生成失败，跳过：{e}")
        try:
            write_score_detail_excel(all_df, OUT / f"score_detail_{cur_key}.xlsx")
        except Exception as e:
            logger.warning(f"评分明细 Excel 生成失败，跳过：{e}")

    logger.info(f"完成：{cover.name} / {card_img.name} / text_{cur_key}.txt")
    print(f"完成：output/{cover.name}")
    print(f"      output/{card_img.name}")
    print(f"      output/text_{cur_key}.txt")


if __name__ == "__main__":
    main()
