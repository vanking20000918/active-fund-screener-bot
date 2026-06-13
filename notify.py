# -*- coding: utf-8 -*-
"""
半自动发布的"最后一公里"：把本周素材按三类推送到手机——
①纯文字（榜单文案）②纯图片（封面 + Top20 长图）③纯视频（播报视频），
人工花两分钟转发到各平台。

支持两个通道，配了哪个环境变量就推哪个（GitHub Secrets 注入）：
  WECOM_WEBHOOK               企业微信群机器人 webhook 完整 URL
  TG_BOT_TOKEN + TG_CHAT_ID   Telegram 机器人

用法：python notify.py [YYYY-MM-DD]   （默认今天）
"""

import io
import os
import re
import sys
import json
import base64
import hashlib
import pathlib
import datetime

import requests

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = pathlib.Path(__file__).parent
OUT = ROOT / "output"


# ---------------- 企业微信群机器人 ----------------

def _wecom_key(webhook):
    return re.search(r"key=([\w-]+)", webhook).group(1)


def wecom_text(webhook, text):
    requests.post(webhook, json={"msgtype": "text",
                                 "text": {"content": text[:2000]}}, timeout=15)


def wecom_image(webhook, path):
    data = path.read_bytes()
    if len(data) <= 2 * 1024 * 1024:  # 图片消息限 2MB
        requests.post(webhook, json={
            "msgtype": "image",
            "image": {"base64": base64.b64encode(data).decode(),
                      "md5": hashlib.md5(data).hexdigest()},
        }, timeout=30)
    else:
        wecom_file(webhook, path)


def wecom_file(webhook, path):
    """通过 upload_media 发文件消息（限 20MB）。"""
    if path.stat().st_size > 20 * 1024 * 1024:
        print(f"[warn] {path.name} 超过企业微信 20MB 限制，跳过")
        return
    key = _wecom_key(webhook)
    up = ("https://qyapi.weixin.qq.com/cgi-bin/webhook/"
          f"upload_media?key={key}&type=file")
    with path.open("rb") as f:
        r = requests.post(up, files={"media": (path.name, f)}, timeout=120)
    media_id = r.json().get("media_id")
    if not media_id:
        print(f"[warn] 企业微信上传失败: {r.text[:200]}")
        return
    requests.post(webhook, json={"msgtype": "file",
                                 "file": {"media_id": media_id}}, timeout=15)


# ---------------- Telegram ----------------

def tg_api(token, method, **kwargs):
    return requests.post(f"https://api.telegram.org/bot{token}/{method}",
                         timeout=120, **kwargs)


def tg_send(token, chat, text, photos, video):
    tg_api(token, "sendMessage", data={"chat_id": chat, "text": text[:4000]})
    for photo in photos:
        if not photo.exists():
            continue
        with photo.open("rb") as f:
            r = tg_api(token, "sendPhoto",
                       data={"chat_id": chat}, files={"photo": f})
        if not r.json().get("ok"):   # 长图可能被拒，退化为文件
            with photo.open("rb") as f:
                tg_api(token, "sendDocument",
                       data={"chat_id": chat}, files={"document": f})
    if video and video.exists():
        with video.open("rb") as f:
            tg_api(token, "sendVideo",
                   data={"chat_id": chat}, files={"video": f})


# ---------------- 主流程 ----------------

def main():
    if len(sys.argv) > 1:
        p = [int(x) for x in sys.argv[1].split("-")]
        date = datetime.date(p[0], p[1], p[2] if len(p) > 2 else 1)
    else:
        date = datetime.date.today()
    key = date.isoformat()

    cover = OUT / f"cover_{key}.png"
    card_img = OUT / f"card_{key}.png"
    video = OUT / f"video_{key}.mp4"
    text = OUT / f"text_{key}.txt"
    payload = OUT / f"payload_{key}.json"

    ctx, funds = {}, []
    if payload.exists():
        p = json.loads(payload.read_text(encoding="utf-8"))
        ctx, funds = p.get("ctx", {}), p.get("funds", [])

    top1 = funds[0]["基金简称"] if funds else ""
    alpha = ctx.get("alpha_pct")
    alpha_txt = (f"，评分体系回测年化超额 {alpha:+.1f}%" if alpha is not None else "")
    wd = "一二三四五六日"[date.weekday()]
    summary = (
        f"📊 {date.month}月{date.day}日(周{wd}) 主动基金周报已生成\n"
        f"候选 {ctx.get('pool_n', '—')} → 硬筛通过 {ctx.get('passed_n', '—')} "
        f"→ 推荐 Top{ctx.get('top_n', len(funds))}"
        + (f"，本月冠军 {top1}" if top1 else "") + alpha_txt + "\n"
        f"——以下三类素材：①纯文字（文案）②纯图片（封面 + Top20 长图）③纯视频（播报）"
    )

    webhook = os.environ.get("WECOM_WEBHOOK", "").strip()
    tg_token = os.environ.get("TG_BOT_TOKEN", "").strip()
    tg_chat = os.environ.get("TG_CHAT_ID", "").strip()
    if not webhook and not (tg_token and tg_chat):
        print("未配置 WECOM_WEBHOOK 或 TG_BOT_TOKEN/TG_CHAT_ID，跳过推送")
        return

    text_txt = text.read_text(encoding="utf-8") if text.exists() else ""

    if webhook:
        wecom_text(webhook, summary)
        if text_txt:
            wecom_text(webhook, "【纯文字】\n" + text_txt)
        if cover.exists():
            wecom_image(webhook, cover)
        if card_img.exists():
            wecom_image(webhook, card_img)
        if video.exists():
            wecom_file(webhook, video)
        print("已推送到企业微信")

    if tg_token and tg_chat:
        tg_send(tg_token, tg_chat,
                summary + "\n\n【纯文字】\n" + text_txt,
                [cover, card_img],
                video if video.exists() else None)
        print("已推送到 Telegram")


if __name__ == "__main__":
    main()
