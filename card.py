# -*- coding: utf-8 -*-
"""
主动基金月度筛选卡片渲染器（Pillow，无需浏览器）。

产出：
  render_cover(funds, ctx, date, path)  1080x1440 月报封面 —— 信息流第一眼钩子
                                        （月份 + 漏斗统计 + 冠军/Top3 + 回测超额徽章）
  render_card(funds, ctx, date, path)   1080 宽 Top20 排行长图 —— 每只基金一行
                                        卡片：名次奖牌 / 名称 / 经理 / 评级 / 综合得分
                                        / 入选原因 / 关键指标药丸 / 环比变动标记
  render_scene_*(...)                    1080x1920 视频分幕帧，供 video.py 合成播报

设计系统沿用「额度哨兵」品牌（美元绿深色渐变 + 暖金大数字），
大数字优先 Barlow Condensed（assets/fonts/，OFL 开源），缺失自动回退中文粗体。

数据契约：funds = top_n.to_dict('records')，每条含 screener 输出列
（基金简称/基金代码/基金经理/综合得分/评级/入选原因/经理任职年限/基金规模/
 卡玛比率/近3年最大回撤/业绩排名分位/熊市数/近1年收益率 等），
main.py 另注入 'change'(new/up/down/None) 与 'rank_delta'(int)。
ctx = {pool_n, passed_n, top_n, alpha_pct?, hit_rate?} 月度上下文统计。
"""

import datetime
import math
import pathlib
import re

from PIL import Image, ImageDraw, ImageFont

# ---------- 配色 ----------
C_BG_TOP   = "#06231A"
C_BG_BOT   = "#0B3526"
C_PANEL    = "#0E3829"
C_PANEL_BD = "#245646"
C_LINE     = "#1B4A38"
C_TEXT     = "#F2EFE6"
C_MUTED    = "#8FAE9F"
C_DIM      = "#54705F"
C_GOLD     = "#E8B84B"
C_GOLD_HI  = "#F7DC94"
C_GOLD_LO  = "#C9912D"
C_GREEN    = "#4CC38A"
C_GREEN_HI = "#9FE8C4"
C_RED      = "#E5604C"
C_BAR_BG   = "#12382A"
C_SILVER   = "#D7E0DA"
C_BRONZE   = "#D49C6A"
C_DONUT_REST  = "#16482F"
C_DONUT_HOLE  = "#0A2C20"

W = 1080      # 长图/封面宽
PX = 36       # 面板外边距
P_PAD = 30    # 面板内边距
M = 64        # 内容边距

ROOT = pathlib.Path(__file__).parent
BRAND = "主动基金周度筛选"

# 评级 → (主色, 浅色)
_GRADE_COLOR = {
    "A 级": (C_GOLD, C_GOLD_HI),
    "B 级": (C_GREEN, C_GREEN_HI),
    "C 级": ("#7FB7D6", "#B9DCEE"),
    "D 级": (C_MUTED, "#B7CCC0"),
}


# ---------- 字体 ----------

def _find_font():
    """按平台找可用的中文字体 (bold_path, regular_path, ttc_index)。"""
    candidates = [
        (r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\msyh.ttc", 0),
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
         "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 2),
        ("/System/Library/Fonts/PingFang.ttc",
         "/System/Library/Fonts/PingFang.ttc", 0),
    ]
    for bold, reg, idx in candidates:
        if pathlib.Path(bold).exists() and pathlib.Path(reg).exists():
            return bold, reg, idx
    raise FileNotFoundError("未找到可用的中文字体，请在 card.py 的 _find_font 中补充字体路径")


FONT_PATH, FONT_PATH_REG, SC = _find_font()

_COND_CANDIDATES = [
    ROOT / "assets" / "fonts" / "BarlowCondensed-Bold.ttf",
    ROOT / "assets" / "fonts" / "BarlowCondensed-SemiBold.ttf",
]
_NUM_RE = re.compile(r"^[\d.]+$")


def _font(size, bold=True):
    return ImageFont.truetype(FONT_PATH if bold else FONT_PATH_REG, size, index=SC)


def _cond(size):
    """大数字字体：Barlow Condensed，缺失回退中文粗体（仅用于数字/拉丁字符）。"""
    for p in _COND_CANDIDATES:
        if p.exists():
            return ImageFont.truetype(str(p), size)
    return _font(size)


def _num_font(text, cond_size, cjk_size):
    return _cond(cond_size) if _NUM_RE.match(text) else _font(cjk_size)


# ---------- 基础绘制 ----------

_DUMMY = ImageDraw.Draw(Image.new("RGB", (8, 8)))


def _tw(text, font):
    return _DUMMY.textlength(text, font=font)


def _rgb(hexcolor):
    h = hexcolor.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _mix(c1, c2, t):
    a, b = _rgb(c1), _rgb(c2)
    return "#%02x%02x%02x" % tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _vgradient(img, top=C_BG_TOP, bot=C_BG_BOT):
    d = ImageDraw.Draw(img)
    a, b = _rgb(top), _rgb(bot)
    h, w = img.height, img.width
    for yy in range(h):
        k = yy / max(h - 1, 1)
        d.line([(0, yy), (w, yy)],
               fill=tuple(int(a[i] + (b[i] - a[i]) * k) for i in range(3)))


def _glow(img, cx, cy, r, color, alpha=24):
    if r <= 0:
        return
    g = Image.radial_gradient("L").resize((r * 2, r * 2))
    g = g.point(lambda v: int((255 - v) * alpha / 255))
    solid = Image.new("RGB", g.size, _rgb(color))
    img.paste(solid, (int(cx - r), int(cy - r)), g)


def _rings(img, cx, cy):
    d = ImageDraw.Draw(img)
    d.ellipse((cx - 220, cy - 220, cx + 220, cy + 220),
              outline=_mix(C_BG_BOT, C_GREEN, 0.13), width=3)
    d.ellipse((cx - 125, cy - 125, cx + 125, cy + 125),
              outline=_mix(C_BG_BOT, C_GOLD, 0.16), width=2)


def _grad_rect(img, box, c1, c2, radius=0, vertical=True):
    x0, y0, x1, y1 = (int(v) for v in box)
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return
    grad = Image.new("RGB", (w, h))
    gd = ImageDraw.Draw(grad)
    n = h if vertical else w
    for i in range(n):
        c = _mix(c1, c2, i / max(n - 1, 1))
        if vertical:
            gd.line([(0, i), (w, i)], fill=c)
        else:
            gd.line([(i, 0), (i, h)], fill=c)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius, fill=255)
    img.paste(grad, (x0, y0), mask)


def _gold_bar(img, x, y, w, h):
    _grad_rect(img, (x, y, x + w, y + h), C_GOLD_HI, C_GOLD_LO, radius=h // 2)


def _grad_text(img, pos, text, font, c_top=C_GOLD_HI, c_bot=C_GOLD_LO):
    bbox = _DUMMY.textbbox((0, 0), text, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    if w <= 0 or h <= 0:
        return 0
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).text((-bbox[0], -bbox[1]), text, font=font, fill=255)
    grad = Image.new("RGB", (w, h))
    gd = ImageDraw.Draw(grad)
    for yy in range(h):
        gd.line([(0, yy), (w, yy)], fill=_mix(c_top, c_bot, yy / max(h - 1, 1)))
    img.paste(grad, (int(pos[0]) + bbox[0], int(pos[1]) + bbox[1]), mask)
    return _tw(text, font)


def _spaced_text(d, pos, text, font, fill, spacing):
    x, y = pos
    for ch in text:
        d.text((x, y), ch, font=font, fill=fill)
        x += _tw(ch, font) + spacing
    return x - pos[0] - spacing


def _spaced_w(text, font, spacing):
    return sum(_tw(ch, font) for ch in text) + spacing * (len(text) - 1)


def _donut(img, cx, cy, r, frac, ring_w, color=C_GREEN):
    ss = 4
    size = r * 2 * ss
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.ellipse((0, 0, size - 1, size - 1), fill=C_DONUT_REST)
    if frac > 0:
        d.pieslice((0, 0, size - 1, size - 1), -90, -90 + 360 * min(frac, 1.0),
                   fill=color)
    hr = (r - ring_w) * ss
    d.ellipse((size / 2 - hr, size / 2 - hr, size / 2 + hr, size / 2 + hr),
              fill=C_DONUT_HOLE)
    layer = layer.resize((r * 2, r * 2), Image.LANCZOS)
    img.paste(layer, (int(cx - r), int(cy - r)), layer)


def _grad_border(img, w_px=4):
    w, h = img.size
    grad = Image.new("RGB", (w, h))
    gd = ImageDraw.Draw(grad)
    stops = ("#D8B055", "#2E6B4F", "#A8843B")
    for yy in range(h):
        t = yy / max(h - 1, 1)
        c = _mix(stops[0], stops[1], t * 2) if t < 0.5 \
            else _mix(stops[1], stops[2], (t - 0.5) * 2)
        gd.line([(0, yy), (w, yy)], fill=c)
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    md.rectangle((0, 0, w - 1, h - 1), fill=255)
    md.rectangle((w_px, w_px, w - 1 - w_px, h - 1 - w_px), fill=0)
    img.paste(grad, (0, 0), mask)


def _wrap(text, font, max_w):
    """按像素宽折行（中文逐字，拉丁词组尽量整体）。"""
    if not text:
        return []
    lines, cur = [], ""
    for ch in str(text):
        if _tw(cur + ch, font) <= max_w or not cur:
            cur += ch
        else:
            lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    return lines


def _ellipsize(text, font, max_w):
    text = str(text)
    if _tw(text, font) <= max_w:
        return text
    while text and _tw(text + "…", font) > max_w:
        text = text[:-1]
    return text + "…"


# ---------- 业务模型 ----------

def _val(r, key, default=None):
    """安全取值：None / NaN 归一为 default。"""
    v = r.get(key, default)
    try:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return default
    except TypeError:
        pass
    return v


def _grade_colors(grade):
    return _GRADE_COLOR.get((grade or "").strip(), (C_MUTED, "#B7CCC0"))


def _fmt_pct(v, digits=1):
    return "—" if v is None else f"{v:.{digits}f}%"


def _fmt_num(v, digits=2):
    return "—" if v is None else f"{v:.{digits}f}"


def _medal_colors(rank):
    """名次奖牌配色 (浅, 深, 文字色)。"""
    if rank == 1:
        return C_GOLD_HI, C_GOLD_LO, "#06231A"
    if rank == 2:
        return "#E2EAE5", "#9FB2A8", "#15302A"
    if rank == 3:
        return "#DCAE80", "#B17B43", "#2A1A0C"
    return _mix(C_PANEL, C_GREEN, 0.10), C_PANEL_BD, C_MUTED


def _change_mark(r):
    """环比变动 → (文字, 颜色) 或 None。"""
    ch = r.get("change")
    delta = r.get("rank_delta")
    if ch == "new":
        return "🆕 新进榜", C_GOLD
    if ch == "up" and delta:
        return f"▲ {abs(int(delta))}", C_GREEN
    if ch == "down" and delta:
        return f"▼ {abs(int(delta))}", C_RED
    if ch in ("up", "down"):
        return ("▲ 上升" if ch == "up" else "▼ 下降"), \
            (C_GREEN if ch == "up" else C_RED)
    return None


def _metric_pills(r):
    """每只基金的关键指标药丸 [(label, value)]。"""
    pills = []
    dd = _val(r, "近3年最大回撤")
    if dd is not None:
        pills.append(("近3年回撤", f"-{abs(dd):.0f}%"))
    calmar = _val(r, "卡玛比率")
    if calmar is not None:
        pills.append(("卡玛比", f"{calmar:.2f}"))
    pct = _val(r, "业绩排名分位")
    if pct is not None:
        pills.append(("业绩分位", f"前{pct:.0f}%"))
    bears = _val(r, "熊市数")
    if bears is not None and bears > 0:
        pills.append(("穿越熊市", f"{int(bears)}轮"))
    ret1y = _val(r, "近1年收益率")
    if ret1y is not None:
        pills.append(("近1年", f"{ret1y:+.0f}%"))
    return pills


# ---------- 通用页头元素 ----------

def _brand_row(img, d, y, en_text, badge_text):
    """品牌行 + 英文 microcopy + 右侧徽章。返回下一个 y。"""
    _gold_bar(img, M, y + 2, 10, 36)
    d.text((M + 28, y), BRAND, font=_font(30), fill=C_GOLD)
    enf = _cond(20)
    _spaced_text(d, (M + 28, y + 48), en_text, enf,
                 _mix(C_BG_BOT, C_MUTED, 0.65), 5)
    bf = _font(26)
    bw = _tw(badge_text, bf)
    bx1, bx0 = W - M, W - M - bw - 44
    d.rounded_rectangle((bx0, y - 6, bx1, y + 44), 25,
                        fill=_mix(C_BG_TOP, C_GREEN, 0.10),
                        outline=C_GREEN, width=2)
    d.text((bx0 + 22, y + 3), badge_text, font=bf, fill=C_GREEN)
    return y + 86


def _stat_strip(img, d, y, ctx):
    """漏斗统计条：候选池 → 通过硬筛 → Top20 (+ 回测超额)。返回下一个 y。"""
    cells = [
        (str(ctx.get("pool_n", "—")), "候选池", C_TEXT),
        (str(ctx.get("passed_n", "—")), "通过硬筛", C_GREEN),
        (str(ctx.get("top_n", "—")), "本月推荐", C_GOLD),
    ]
    alpha = ctx.get("alpha_pct")
    if alpha is not None:
        cells.append((f"{alpha:+.1f}%", "回测年化超额",
                      C_GREEN if alpha >= 0 else C_RED))
    n = len(cells)
    gap = 16
    cw = (W - 2 * PX - gap * (n - 1)) / n
    for i, (num, label, col) in enumerate(cells):
        x0 = PX + i * (cw + gap)
        d.rounded_rectangle((x0, y, x0 + cw, y + 130), 20,
                            fill=C_PANEL, outline=C_PANEL_BD, width=1)
        nf = _num_font(num.lstrip("+-"), 60, 40) if any(c.isdigit() for c in num) \
            else _font(40)
        if _NUM_RE.match(num):
            tw = _grad_text(img, (x0 + (cw - _tw(num, nf)) / 2, y + 18), num, nf,
                            _mix(col, "#FFFFFF", 0.4), col)
        else:
            d.text((x0 + (cw - _tw(num, nf)) / 2, y + 26), num, font=nf, fill=col)
        lf = _font(23, bold=False)
        d.text((x0 + (cw - _tw(label, lf)) / 2, y + 92), label,
               font=lf, fill=C_MUTED)
    return y + 130 + 30


# ============================================================
# 封面卡 1080 x 1440
# ============================================================

def render_cover(funds, ctx, date, out_path):
    H = 1440
    img = Image.new("RGB", (W, H), C_BG_TOP)
    _vgradient(img)
    _glow(img, 180, 320, 320, C_GOLD, alpha=18)
    _rings(img, W - 40, -20)
    d = ImageDraw.Draw(img)

    wd = "一二三四五六日"[date.weekday()]
    badge = f"{date.month}月{date.day}日 · 周{wd}"

    y = 64
    _brand_row(img, d, y, "ACTIVE FUND · WEEKLY TOP 20", badge)

    # 主标题
    y = 182
    d.text((M, y), "主动基金", font=_font(96), fill=C_TEXT)
    _grad_text(img, (M + _tw("主动基金", _font(96)) + 24, y + 6), "TOP20", _cond(104))
    _gold_bar(img, M + 4, y + 132, 120, 8)
    d.text((M, y + 162), "股票型 / 混合型 / QDII · 6维评分 + 滚动回测验证",
           font=_font(27, bold=False), fill=C_MUTED)

    # 漏斗统计条
    y = 470
    y = _stat_strip(img, d, y, ctx)

    # Top3 面板
    ty = y + 6
    panel_h = 470
    d.rounded_rectangle((PX, ty, W - PX, ty + panel_h), 28,
                        fill=C_PANEL, outline=C_PANEL_BD, width=1)
    _gold_bar(img, M, ty + 34, 8, 32)
    d.text((M + 24, ty + 30), "本周冠军 · Top3", font=_font(32), fill=C_TEXT)
    note = "综合得分 / 评级"
    nf2 = _font(22, bold=False)
    d.text((W - M - _tw(note, nf2), ty + 40), note, font=nf2, fill=C_DIM)
    d.line((M, ty + 90, W - M, ty + 90), fill=_mix(C_LINE, C_GOLD, 0.25), width=1)

    best3 = funds[:3]
    if best3:
        row_h = 118
        for i, r in enumerate(best3):
            ry = ty + 104 + i * row_h
            m1, m2, mt = _medal_colors(i + 1)
            _grad_rect(img, (M, ry + 22, M + 54, ry + 76), m1, m2, radius=27)
            mf = _cond(34)
            d.text((M + 27 - _tw(str(i + 1), mf) / 2, ry + 30), str(i + 1),
                   font=mf, fill=mt)
            name = _ellipsize(_val(r, "基金简称", ""), _font(38), 560)
            d.text((M + 80, ry + 8), name, font=_font(38), fill=C_TEXT)
            mgr = _val(r, "基金经理", "")
            tenure = _val(r, "经理任职年限")
            sub = f"{_val(r, '基金代码', '')} · {mgr}"
            if tenure is not None:
                sub += f" · 任职{tenure:.0f}年"
            d.text((M + 80, ry + 62), _ellipsize(sub, _font(23, bold=False), 560),
                   font=_font(23, bold=False), fill=C_MUTED)
            # 右侧：得分 + 评级
            score = _val(r, "综合得分")
            grade = _val(r, "评级", "")
            gc, _ = _grade_colors(grade)
            stxt = f"{score:.1f}" if score is not None else "—"
            snf = _cond(72)
            sw = _tw(stxt, snf)
            _grad_text(img, (W - M - sw, ry + 14), stxt, snf)
            gf = _font(24)
            gw = _tw(grade, gf) + 28
            d.rounded_rectangle((W - M - max(sw, gw), ry + 84,
                                 W - M - max(sw, gw) + gw, ry + 84 + 38), 12,
                                fill=_mix(C_PANEL, gc, 0.16), outline=gc, width=1)
            d.text((W - M - max(sw, gw) + 14, ry + 90), grade, font=gf, fill=gc)
            if i < len(best3) - 1:
                d.line((M, ry + row_h - 6, W - M, ry + row_h - 6),
                       fill=C_LINE, width=1)
    else:
        d.text((M, ty + 140), "本月无基金通过硬性筛选", font=_font(34), fill=C_RED)

    # 页脚：免责声明 + CTA
    fy = H - 64 - 64
    d.text((M, fy), "数据来源：天天基金网公开数据 · 评分/回测为量化初筛",
           font=_font(21, bold=False), fill=C_DIM)
    d.text((M, fy + 34), "仅为公开信息整理，不构成投资建议",
           font=_font(21, bold=False), fill=C_DIM)
    cta = "完整 Top20 榜单 · 第 2 张图 →"
    cf = _font(26)
    cw = _tw(cta, cf) + 68
    _grad_rect(img, (W - M - cw, fy - 2, W - M, fy + 62),
               "#F2CD74", "#DCA93C", radius=32)
    d.text((W - M - cw + 34, fy + 13), cta, font=cf, fill="#0A2C20")

    _grad_border(img, 4)
    pathlib.Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    return out_path


# ============================================================
# Top20 排行长图
# ============================================================

ROW_PAD = 24          # 行内边距
ROW_GAP = 18          # 行间距


def _fund_row(img, y0, rank, r):
    """绘制(或测量)一只基金的排行行卡片，返回行高。img=None 时只测量。"""
    d = ImageDraw.Draw(img) if img is not None else None
    x0, x1 = PX, W - PX
    ix0 = x0 + ROW_PAD            # 内容左
    ix1 = x1 - ROW_PAD           # 内容右

    name_f = _font(38)
    sub_f = _font(23, bold=False)
    reason_f = _font(24, bold=False)
    pill_lf = _font(19, bold=False)
    pill_vf = _font(25)

    # 右侧得分块宽度预留
    score_block_w = 150
    name_left = ix0 + 78          # 奖牌宽 + 间距
    name_max_w = ix1 - name_left - score_block_w - 20

    reason = _val(r, "入选原因", "")
    reason_lines = _wrap(reason, reason_f, ix1 - ix0 - 16)[:2]

    pills = _metric_pills(r)

    # 行高计算
    h = ROW_PAD + 44 + 30          # 名称行 + 副标题行
    if reason_lines:
        h += 6 + len(reason_lines) * 34
    if pills:
        h += 14 + 56               # 药丸行
    h += ROW_PAD

    if d is None:
        return h

    # 行背景
    rank_accent = rank <= 3
    bg = _mix(C_PANEL, C_GOLD, 0.05) if rank_accent else C_PANEL
    bd = _mix(C_PANEL_BD, C_GOLD, 0.4) if rank_accent else C_PANEL_BD
    d.rounded_rectangle((x0, y0, x1, y0 + h), 24, fill=bg,
                        outline=bd, width=2 if rank_accent else 1)

    # 奖牌
    m1, m2, mt = _medal_colors(rank)
    my = y0 + ROW_PAD
    _grad_rect(img, (ix0, my, ix0 + 58, my + 58), m1, m2, radius=16)
    mf = _cond(34)
    d.text((ix0 + 29 - _tw(str(rank), mf) / 2, my + 8), str(rank),
           font=mf, fill=mt)

    # 名称 + 变动标记
    name = _val(r, "基金简称", "")
    nx = name_left
    name = _ellipsize(name, name_f, name_max_w)
    d.text((nx, y0 + ROW_PAD - 2), name, font=name_f, fill=C_TEXT)
    mark = _change_mark(r)
    if mark:
        mtxt, mcol = mark
        mff = _font(20)
        mw = _tw(mtxt, mff) + 22
        mx = nx + _tw(name, name_f) + 16
        if mx + mw < ix1 - score_block_w:
            d.rounded_rectangle((mx, y0 + ROW_PAD + 2, mx + mw,
                                 y0 + ROW_PAD + 2 + 32), 10,
                                fill=_mix(C_PANEL, mcol, 0.16), outline=mcol, width=1)
            d.text((mx + 11, y0 + ROW_PAD + 6), mtxt, font=mff, fill=mcol)

    # 副标题：代码 · 经理 · 任职 · 规模
    bits = [_val(r, "基金代码", "")]
    mgr = _val(r, "基金经理")
    if mgr:
        bits.append(str(mgr))
    tenure = _val(r, "经理任职年限")
    if tenure is not None:
        bits.append(f"任职{tenure:.0f}年")
    scale = _val(r, "基金规模")
    if scale is not None:
        bits.append(f"{scale:.0f}亿")
    sub = " · ".join(b for b in bits if b)
    d.text((name_left, y0 + ROW_PAD + 46),
           _ellipsize(sub, sub_f, name_max_w + score_block_w),
           font=sub_f, fill=C_MUTED)

    # 右侧：综合得分 + 评级
    score = _val(r, "综合得分")
    grade = _val(r, "评级", "")
    gc, _ = _grade_colors(grade)
    stxt = f"{score:.1f}" if score is not None else "—"
    snf = _cond(58)
    sw = _tw(stxt, snf)
    _grad_text(img, (ix1 - sw, y0 + ROW_PAD - 4), stxt, snf)
    d.text((ix1 - sw - _tw("分", sub_f) - 2, y0 + ROW_PAD + 18), "",
           font=sub_f, fill=C_MUTED)
    gf = _font(21)
    gw = _tw(grade, gf) + 24
    d.rounded_rectangle((ix1 - gw, y0 + ROW_PAD + 50, ix1,
                         y0 + ROW_PAD + 50 + 32), 10,
                        fill=_mix(C_PANEL, gc, 0.16), outline=gc, width=1)
    d.text((ix1 - gw + 12, y0 + ROW_PAD + 54), grade, font=gf, fill=gc)

    # 入选原因
    ry = y0 + ROW_PAD + 46 + 30 + 6
    for ln in reason_lines:
        d.text((ix0, ry), ln, font=reason_f, fill=_mix(C_TEXT, C_MUTED, 0.4))
        ry += 34

    # 指标药丸
    if pills:
        ry += 14
        px = ix0
        for label, value in pills:
            pw = max(_tw(label, pill_lf), _tw(value, pill_vf)) + 32
            if px + pw > ix1:
                break
            d.rounded_rectangle((px, ry, px + pw, ry + 56), 12,
                                fill=C_BAR_BG, outline=C_LINE, width=1)
            d.text((px + 16, ry + 6), label, font=pill_lf, fill=C_DIM)
            vf_col = C_GOLD if label in ("卡玛比", "业绩分位") else C_TEXT
            d.text((px + 16, ry + 26), value, font=pill_vf, fill=vf_col)
            px += pw + 12

    return h


def render_card(funds, ctx, date, out_path):
    wd = "一二三四五六日"[date.weekday()]
    badge = f"{date.month}月{date.day}日 · 周{wd}"

    # 预测量
    row_hs = [_fund_row(None, 0, i + 1, r) for i, r in enumerate(funds)]
    HEADER_H = 470
    FOOTER_H = 210
    H = HEADER_H + sum(h + ROW_GAP for h in row_hs) + FOOTER_H

    img = Image.new("RGB", (W, H), C_BG_TOP)
    _vgradient(img)
    _glow(img, 160, 240, 260, C_GOLD, alpha=14)
    _rings(img, W - 40, -20)
    d = ImageDraw.Draw(img)

    # 头部
    y = 64
    y = _brand_row(img, d, y, f"WEEKLY TOP {ctx.get('top_n', len(funds))} · ACTIVE FUNDS", badge)
    d.text((M, y), "主动基金 Top榜", font=_font(76), fill=C_TEXT)
    d.text((M + _tw("主动基金 Top榜", _font(76)) + 20, y + 40),
           "股票/混合/QDII", font=_font(30), fill=C_MUTED)
    y += 116

    # 统计条
    y = _stat_strip(img, d, y, ctx)

    # 排行
    for i, (r, rh) in enumerate(zip(funds, row_hs)):
        _fund_row(img, y, i + 1, r)
        y += rh + ROW_GAP

    # 页脚
    fy = H - FOOTER_H + 24
    d.line((M, fy, W - M, fy), fill=_mix(C_LINE, C_GOLD, 0.25), width=1)
    fy += 22
    d.text((M, fy), "评分维度：业绩稳定性 · 熊市表现 · 投资框架(卡玛) · 经理任职 · 风格一致性 · 规模",
           font=_font(22, bold=False), fill=C_MUTED)
    fy += 36
    d.text((M, fy), "🆕=本周新进榜  ▲/▼=较上周名次变动",
           font=_font(22, bold=False), fill=C_MUTED)
    fy += 36
    d.text((M, fy), "数据来源：天天基金网公开数据（以基金公司公告为准）",
           font=_font(22, bold=False), fill=C_MUTED)
    fy += 36
    d.text((M, fy),
           f"仅为公开信息整理，不构成投资建议 · 生成于 {date:%Y-%m-%d}",
           font=_font(22, bold=False), fill=C_MUTED)

    pathlib.Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    return out_path


# ============================================================
# 视频分幕帧 1080 x 1920
# ============================================================

VW, VH = 1080, 1920


def _scene_base(watermark=""):
    img = Image.new("RGB", (VW, VH), C_BG_TOP)
    _vgradient(img)
    _rings(img, VW - 30, -30)
    if watermark:
        d = ImageDraw.Draw(img)
        wf = _cond(52)
        wx = VW - 60 - _spaced_w(watermark, wf, 14)
        _spaced_text(d, (wx, VH - 300), watermark, wf,
                     _mix(C_BG_BOT, C_TEXT, 0.06), 14)
    return img


def _scene_chrome(img, idx, n, subtitle, accent_gold=True):
    """底部：分段进度条 + 字幕条。"""
    d = ImageDraw.Draw(img)
    seg_w, gap = max(20, int((VW - 240) / max(n, 1)) - 12), 12
    total_w = n * seg_w + (n - 1) * gap
    x = (VW - total_w) / 2
    by = VH - 230
    for i in range(n):
        if i == idx:
            _grad_rect(img, (x, by, x + seg_w, by + 10), C_GOLD_HI, C_GOLD_LO,
                       radius=5, vertical=False)
        else:
            d.rounded_rectangle((x, by, x + seg_w, by + 10), 5, fill="#1E5040")
        x += seg_w + gap
    sy = VH - 184
    d.rounded_rectangle((80, sy, VW - 80, sy + 104), 22,
                        fill="#061C15", outline="#1E5040", width=1)
    bar_c1, bar_c2 = (C_GOLD_HI, C_GOLD_LO) if accent_gold else (C_GREEN_HI, C_GREEN)
    _grad_rect(img, (114, sy + 34, 122, sy + 70), bar_c1, bar_c2, radius=4)
    sub = _ellipsize(subtitle, _font(40), VW - 80 - 148 - 24)
    d.text((148, sy + 28), sub, font=_font(40), fill=C_TEXT)


def render_scene_cover(funds, ctx, date, out_path, idx, n, subtitle):
    img = _scene_base("ACTIVE FUND")
    _glow(img, 300, 980, 360, C_GOLD, alpha=20)
    d = ImageDraw.Draw(img)

    y = 380
    _gold_bar(img, 80, y + 4, 12, 44)
    wd = "一二三四五六日"[date.weekday()]
    d.text((80 + 30, y), f"{BRAND} · 周报 · 周{wd}", font=_font(40), fill=C_GOLD)
    y += 96
    date_str = f"{date.month}/{date.day}"
    d.text((80, y), date_str, font=_cond(150), fill=C_TEXT)
    mw = _tw(date_str, _cond(150))
    _grad_text(img, (80 + mw + 24, y + 30), "TOP20", _cond(110))
    y += 210
    d.text((80, y), "股票/混合/QDII 主动基金", font=_font(50), fill=C_MUTED)
    y += 130

    # 漏斗大数字
    cells = [
        (str(ctx.get("pool_n", "—")), "候选池", C_TEXT),
        (str(ctx.get("passed_n", "—")), "通过硬筛", C_GREEN),
        (str(ctx.get("top_n", len(funds))), "本月推荐", C_GOLD),
    ]
    cw = (VW - 160 - 2 * 24) / 3
    for i, (num, label, col) in enumerate(cells):
        x0 = 80 + i * (cw + 24)
        d.rounded_rectangle((x0, y, x0 + cw, y + 200), 24,
                            fill=C_PANEL, outline=C_PANEL_BD, width=2)
        nf = _cond(96)
        _grad_text(img, (x0 + (cw - _tw(num, nf)) / 2, y + 28), num, nf,
                   _mix(col, "#FFFFFF", 0.4), col)
        lf = _font(34, bold=False)
        d.text((x0 + (cw - _tw(label, lf)) / 2, y + 138), label,
               font=lf, fill=C_MUTED)
    y += 200 + 60

    alpha = ctx.get("alpha_pct")
    if alpha is not None:
        tag = f"滚动回测年化超额  {alpha:+.1f}%"
        col = C_GREEN if alpha >= 0 else C_RED
        tf = _font(46)
        tw_ = _tw(tag, tf) + 80
        d.rounded_rectangle(((VW - tw_) / 2, y, (VW + tw_) / 2, y + 96), 28,
                            fill=_mix(C_PANEL, col, 0.10),
                            outline=_mix(C_PANEL_BD, col, 0.6), width=2)
        d.text(((VW - _tw(tag, tf)) / 2, y + 22), tag, font=tf, fill=col)

    _scene_chrome(img, idx, n, subtitle)
    img.save(out_path)
    return out_path


def render_scene_ranklist(funds, title, watermark, out_path, idx, n, subtitle,
                          start=0, count=5):
    """名次榜分幕：展示 funds[start:start+count]。"""
    img = _scene_base(watermark)
    d = ImageDraw.Draw(img)

    y = 130
    _gold_bar(img, 80, y + 4, 12, 56)
    d.text((80 + 32, y), title, font=_font(64), fill=C_TEXT)

    subset = funds[start:start + count]
    card_h, gap = 230, 26
    y0 = 320
    for i, r in enumerate(subset):
        rank = start + i + 1
        cy0 = y0 + i * (card_h + gap)
        accent = rank <= 3
        if accent:
            _glow(img, VW / 2, cy0 + card_h / 2, 300, C_GOLD, alpha=10)
            d.rounded_rectangle((80, cy0, VW - 80, cy0 + card_h), 28,
                                fill=_mix(C_PANEL, C_GOLD, 0.05),
                                outline=_mix(C_PANEL_BD, C_GOLD, 0.6), width=3)
        else:
            d.rounded_rectangle((80, cy0, VW - 80, cy0 + card_h), 28,
                                fill=C_PANEL, outline=C_PANEL_BD, width=2)
        # 奖牌
        m1, m2, mt = _medal_colors(rank)
        _grad_rect(img, (120, cy0 + 38, 120 + 78, cy0 + 38 + 78), m1, m2, radius=20)
        mf = _cond(48)
        d.text((120 + 39 - _tw(str(rank), mf) / 2, cy0 + 50), str(rank),
               font=mf, fill=mt)
        # 名称 + 经理
        name = _ellipsize(_val(r, "基金简称", ""), _font(54), 560)
        d.text((230, cy0 + 34), name, font=_font(54), fill=C_TEXT)
        mgr = _val(r, "基金经理", "")
        tenure = _val(r, "经理任职年限")
        sub = f"{mgr} · 任职{tenure:.0f}年" if tenure is not None else str(mgr)
        d.text((230, cy0 + 102), _ellipsize(sub, _font(34, bold=False), 540),
               font=_font(34, bold=False), fill=C_MUTED)
        # 入选原因（截一行，避开右侧得分列）
        reason = _ellipsize(_val(r, "入选原因", ""), _font(30, bold=False), 560)
        d.text((230, cy0 + 152), reason, font=_font(30, bold=False),
               fill=_mix(C_TEXT, C_MUTED, 0.5))
        # 右侧得分
        score = _val(r, "综合得分")
        stxt = f"{score:.1f}" if score is not None else "—"
        snf = _cond(86)
        _grad_text(img, (VW - 128 - _tw(stxt, snf), cy0 + 40), stxt, snf)
        grade = _val(r, "评级", "")
        gc, _ = _grade_colors(grade)
        gf = _font(30)
        d.text((VW - 128 - _tw(grade, gf), cy0 + 150), grade, font=gf, fill=gc)

    _scene_chrome(img, idx, n, subtitle)
    img.save(out_path)
    return out_path


def render_scene_method(out_path, idx, n, subtitle):
    """评分方法论分幕。"""
    img = _scene_base()
    d = ImageDraw.Draw(img)

    y = 150
    _gold_bar(img, 80, y + 4, 12, 56)
    d.text((80 + 32, y), "怎么选出来的", font=_font(64), fill=C_TEXT)
    y += 130
    d.text((80, y), "全市场主动基金 → 6 维量化评分", font=_font(40), fill=C_MUTED)
    y += 110

    dims = [
        ("业绩稳定性", "近3年收益候选池分位", "28%"),
        ("熊市表现", "历轮熊市相对回撤", "20%"),
        ("经理任职", "任职年限 · 穿越周期", "15%"),
        ("投资框架", "卡玛比 收益/回撤匹配", "15%"),
        ("风格一致性", "行业配置稳定度", "12%"),
        ("规模适中", "钟形 5–30 亿最优", "10%"),
    ]
    card_h, gap = 150, 22
    for i, (name, desc, w) in enumerate(dims):
        cy0 = y + i * (card_h + gap)
        d.rounded_rectangle((80, cy0, VW - 80, cy0 + card_h), 24,
                            fill=C_PANEL, outline=C_PANEL_BD, width=2)
        d.text((124, cy0 + 26), name, font=_font(46), fill=C_TEXT)
        d.text((124, cy0 + 86), desc, font=_font(30, bold=False), fill=C_MUTED)
        wf = _cond(72)
        _grad_text(img, (VW - 124 - _tw(w, wf), cy0 + 36), w, wf)

    _scene_chrome(img, idx, n, subtitle, accent_gold=True)
    img.save(out_path)
    return out_path


def render_scene_backtest(ctx, out_path, idx, n, subtitle):
    """滚动回测验证分幕。"""
    img = _scene_base()
    bt = ctx.get("backtest_rows") or []
    alpha = ctx.get("alpha_pct")
    _glow(img, VW / 2, 820, 420, C_GREEN if (alpha or 0) >= 0 else C_RED, alpha=14)
    d = ImageDraw.Draw(img)

    y = 150
    _gold_bar(img, 80, y + 4, 12, 56)
    d.text((80 + 32, y), "回测验证", font=_font(64), fill=C_TEXT)
    y += 130
    d.text((80, y), "滚动 5 起点 PIT 回测 · 评分组合 vs 基准", font=_font(38),
           fill=C_MUTED)
    y += 120

    if alpha is not None:
        big = f"{alpha:+.1f}%"
        col = C_GREEN if alpha >= 0 else C_RED
        nf = _cond(200)
        _grad_text(img, ((VW - _tw(big, nf)) / 2, y), big, nf,
                   _mix(col, "#FFFFFF", 0.4), col)
        y += 230
        cap = "5 个回测起点平均年化超额收益"
        cf = _font(40)
        d.text(((VW - _tw(cap, cf)) / 2, y), cap, font=cf, fill=C_MUTED)
        y += 100

    # 各起点条
    for row in bt[:5]:
        label = row.get("label", "")
        a = row.get("alpha")
        cy0 = y
        d.rounded_rectangle((120, cy0, VW - 120, cy0 + 96), 20,
                            fill=C_PANEL, outline=C_PANEL_BD, width=2)
        d.text((150, cy0 + 24), label, font=_font(40), fill=C_TEXT)
        if a is not None:
            col = C_GREEN if a >= 0 else C_RED
            atxt = f"{a:+.1f}%"
            af = _cond(56)
            _grad_text(img, (VW - 150 - _tw(atxt, af), cy0 + 16), atxt, af,
                       _mix(col, "#FFFFFF", 0.4), col)
        y += 96 + 20

    if alpha is None and not bt:
        d.text(((VW - _tw("本月未运行回测", _font(60))) / 2, 820),
               "本月未运行回测", font=_font(60), fill=C_MUTED)

    _scene_chrome(img, idx, n, subtitle, accent_gold=(alpha or 0) >= 0)
    img.save(out_path)
    return out_path


def render_scene_outro(out_path, idx, n, subtitle):
    img = _scene_base()
    _glow(img, VW / 2, 760, 380, C_GOLD, alpha=14)
    d = ImageDraw.Draw(img)

    y = 540
    bf = _font(48)
    bx = (VW - _tw(BRAND, bf) - 30) / 2
    _gold_bar(img, bx, y + 6, 12, 48)
    d.text((bx + 30, y), BRAND, font=bf, fill=C_GOLD)
    y += 130
    for line in ("每周六更新", "主动基金 Top20"):
        lf = _font(76)
        d.text(((VW - _tw(line, lf)) / 2, y), line, font=lf, fill=C_TEXT)
        y += 110
    y += 40
    cta = "关注，看下周谁上榜"
    cf = _font(44)
    cw = _tw(cta, cf) + 96
    _grad_rect(img, ((VW - cw) / 2, y, (VW + cw) / 2, y + 88),
               "#F2CD74", "#DCA93C", radius=44)
    d.text(((VW - cw) / 2 + 48, y + 20), cta, font=cf, fill="#0A2C20")
    y += 150
    d.line(((VW - 200) / 2, y, (VW + 200) / 2, y),
           fill=_mix(C_LINE, C_GOLD, 0.3), width=2)
    y += 40
    for line in ("数据来自天天基金网公开数据", "量化初筛，不构成投资建议"):
        lf = _font(32, bold=False)
        d.text(((VW - _tw(line, lf)) / 2, y), line, font=lf, fill=C_MUTED)
        y += 52

    _scene_chrome(img, idx, n, subtitle)
    img.save(out_path)
    return out_path
