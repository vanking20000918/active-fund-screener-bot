# -*- coding: utf-8 -*-
"""
周榜播报视频（抖音 / B站，1080x1920 竖版）：
  分幕大字分镜 —— 封面 → Top1-5 → Top6-10 → 评分方法 → 回测验证 → 落版
                 → 完整 Top20 长图慢滚。
  每幕一张 PNG 帧（card.render_scene_*），edge-tts 分段配音，
  幕时长 = 该段配音时长 + 留白，ffmpeg 逐幕合成后 concat 拼接。

用法：python video.py [YYYY-MM-DD]  （默认今天；需先运行 main.py 生成 payload）
产出：output/video_YYYY-MM-DD.mp4
"""

import io
import re
import sys
import json
import shutil
import asyncio
import pathlib
import datetime
import subprocess

import card

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = pathlib.Path(__file__).parent
OUT = ROOT / "output"
VOICE = "zh-CN-XiaoxiaoNeural"
PAD = 0.6           # 每幕配音后的留白秒数
MIN_DUR = 3.0       # 每幕最短时长
SCROLL_SPEED = 220          # 名单幕滚动速度（px/s）
SCROLL_PAD_BG = "0x06231A"  # 与 card.C_BG_TOP 一致


def find_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _v(r, key, default=None):
    v = r.get(key, default)
    return default if v is None else v


def _money_grade(r):
    score = _v(r, "综合得分")
    return f"{score:.1f}分" if score is not None else ""


def build_scenes(funds, ctx, date):
    """返回 [{kind, narration, subtitle, default_dur, ...}] 与帧渲染一一对应。"""
    n_top = len(funds)
    scenes = []

    # 封面
    pool = ctx.get("pool_n", "若干")
    passed = ctx.get("passed_n", "若干")
    scenes.append({
        "kind": "cover",
        "narration": (f"{date.month}月{date.day}日，本周主动基金榜来了。"
                      f"从全市场{pool}只候选里，{passed}只通过硬性筛选，"
                      f"为你选出综合评分最高的前{n_top}只。"),
        "subtitle": f"{date.month}月{date.day}日 主动基金 Top{n_top}",
        "default_dur": 5.5,
    })

    # Top 1-5
    if funds:
        top1 = funds[0]
        scenes.append({
            "kind": "ranklist", "start": 0, "count": 5,
            "title": "本月 Top 1–5", "watermark": "TOP 1-5",
            "narration": (f"先看前五名。本月冠军是{_v(top1, '基金简称', '')}，"
                          f"综合评分{_money_grade(top1)}，"
                          f"{_short_reason(top1)}。"),
            "subtitle": f"Top1–5 · 冠军 {_v(top1, '基金简称', '')}",
            "default_dur": 8.0,
        })

    # Top 6-10
    if n_top > 5:
        scenes.append({
            "kind": "ranklist", "start": 5, "count": 5,
            "title": "本月 Top 6–10", "watermark": "TOP 6-10",
            "narration": "接着是第六到第十名，同样是评分靠前的实力选手。",
            "subtitle": "Top6–10",
            "default_dur": 6.5,
        })

    # 评分方法
    scenes.append({
        "kind": "method",
        "narration": ("这份榜单怎么选出来的？我们用六个维度给全市场主动基金打分："
                      "业绩稳定性、熊市表现、经理任职年限、投资框架、风格一致性和规模，"
                      "加权排序得出。"),
        "subtitle": "6 维量化评分",
        "default_dur": 8.5,
    })

    # 回测验证
    if ctx.get("alpha_pct") is not None:
        a = ctx["alpha_pct"]
        scenes.append({
            "kind": "backtest",
            "narration": (f"这套评分靠谱吗？我们做了滚动五起点回测，"
                          f"评分组合相对全市场的年化超额平均{a:+.1f}个百分点，"
                          f"用历史数据验证它确实能选出更优的基金。"),
            "subtitle": f"回测年化超额 {a:+.1f}%",
            "default_dur": 8.0,
        })

    # 落版
    scenes.append({
        "kind": "outro",
        "narration": ("完整榜单见下一幕。数据来自天天基金网公开数据，"
                      "属于量化初筛，不构成投资建议。每周六更新，记得关注。"),
        "subtitle": "完整 Top20 见下一幕 · 每周更新",
        "default_dur": 6.5,
    })

    # 完整名单慢滚
    scenes.append({
        "kind": "list",
        "narration": "完整 Top20 榜单如下，可随时暂停查看每只基金的入选原因。",
        "subtitle": "完整 Top20 · 可暂停查看",
        "default_dur": 14.0,
    })
    return scenes


def _short_reason(r):
    reason = _v(r, "入选原因", "")
    return reason.split(";")[0].strip() if reason else "综合表现优异"


def render_frames(scenes, funds, ctx, date, tmp):
    n = len(scenes)
    for i, s in enumerate(scenes):
        png = tmp / f"scene_{i}.png"
        k = s["kind"]
        if k == "cover":
            card.render_scene_cover(funds, ctx, date, str(png), i, n, s["subtitle"])
        elif k == "ranklist":
            card.render_scene_ranklist(funds, s["title"], s["watermark"],
                                       str(png), i, n, s["subtitle"],
                                       start=s["start"], count=s["count"])
        elif k == "method":
            card.render_scene_method(str(png), i, n, s["subtitle"])
        elif k == "backtest":
            card.render_scene_backtest(ctx, str(png), i, n, s["subtitle"])
        elif k == "outro":
            card.render_scene_outro(str(png), i, n, s["subtitle"])
        elif k == "list":
            png = OUT / f"card_{date.isoformat()}.png"
            if not png.exists():
                card.render_card(funds, ctx, date, str(png))
        s["png"] = png


def tts_all(scenes, tmp):
    """逐幕合成配音；任一失败则全部静音（保证视频仍能产出）。"""
    try:
        import edge_tts

        async def run():
            for i, s in enumerate(scenes):
                mp3 = tmp / f"scene_{i}.mp3"
                await edge_tts.Communicate(
                    s["narration"], VOICE, rate="+8%").save(str(mp3))
                s["mp3"] = mp3

        asyncio.run(run())
        return True
    except Exception as exc:
        print(f"[warn] TTS 失败（{exc}），输出无声视频")
        for s in scenes:
            s["mp3"] = None
        return False


def media_duration(ffmpeg, path):
    p = subprocess.run([ffmpeg, "-i", str(path)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", p.stderr)
    if not m:
        raise RuntimeError(f"无法读取时长: {path}")
    h, mi, s = float(m.group(1)), float(m.group(2)), float(m.group(3))
    return h * 3600 + mi * 60 + s


def _run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg 失败:\n{p.stderr[-2000:]}")


def render_scene_mp4(ffmpeg, s, dur, out_mp4):
    cmd = [ffmpeg, "-y", "-loop", "1", "-i", str(s["png"])]
    if s.get("mp3"):
        cmd += ["-i", str(s["mp3"])]
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
    if s.get("scroll", 0) > 0:
        roll = max(dur - 2.0, 0.1)
        vf = (f"crop=1080:1920:0:'(in_h-1920)*min(max((t-1)/{roll:.2f},0),1)',"
              "format=yuv420p")
    elif s["kind"] == "list":
        vf = f"pad=1080:1920:0:(oh-ih)/2:color={SCROLL_PAD_BG},format=yuv420p"
    else:
        vf = "format=yuv420p"
    cmd += [
        "-filter_complex", f"[0:v]{vf}[v];[1:a]apad[a]",
        "-map", "[v]", "-map", "[a]",
        "-t", f"{dur:.2f}", "-r", "30",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-ar", "44100", "-b:a", "128k",
        str(out_mp4),
    ]
    _run(cmd)


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    if arg:
        p = [int(x) for x in arg.split("-")]
        date = datetime.date(p[0], p[1], p[2] if len(p) > 2 else 1)
    else:
        date = datetime.date.today()
    key = date.isoformat()

    payload_path = OUT / f"payload_{key}.json"
    if not payload_path.exists():
        sys.exit(f"缺少 {payload_path.name}，请先运行 python main.py")
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    funds = payload["funds"]
    ctx = payload.get("ctx", {})
    # 用 payload 里的真实日期
    pdate = datetime.date.fromisoformat(payload.get("date", date.isoformat()))

    out = OUT / f"video_{key}.mp4"
    tmp = OUT / f".scenes_{key}"
    tmp.mkdir(parents=True, exist_ok=True)

    scenes = build_scenes(funds, ctx, pdate)
    print("播报文稿:")
    for s in scenes:
        print("  -", s["narration"])

    render_frames(scenes, funds, ctx, pdate, tmp)
    ffmpeg = find_ffmpeg()
    has_audio = tts_all(scenes, tmp)

    from PIL import Image
    parts = []
    for i, s in enumerate(scenes):
        if s.get("mp3"):
            dur = max(media_duration(ffmpeg, s["mp3"]) + PAD, MIN_DUR)
        else:
            dur = s["default_dur"]
        if s["kind"] == "list":
            s["scroll"] = max(Image.open(s["png"]).height - 1920, 0)
            dur = max(dur, s["scroll"] / SCROLL_SPEED + 2.0)
        mp4 = tmp / f"scene_{i}.mp4"
        render_scene_mp4(ffmpeg, s, dur, mp4)
        parts.append(mp4)

    lst = tmp / "concat.txt"
    lst.write_text("\n".join(f"file '{p.resolve().as_posix()}'" for p in parts),
                   encoding="utf-8")
    _run([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
          "-c", "copy", "-movflags", "+faststart", str(out)])

    print(f"完成：{out}（{media_duration(ffmpeg, out):.1f} 秒，"
          f"{'有' if has_audio else '无'}配音，{len(scenes)} 幕）")
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
