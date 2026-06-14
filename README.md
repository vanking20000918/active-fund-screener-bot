# 主动基金周度筛选播报机器人

每周六自动从全市场筛选 A 股**主动股票型 + 混合型 + QDII** 基金，用 6 维量化评分
排出 Top20，并把结果做成**三类发帖素材**——纯文字文案、Top20 排行长图、TTS 配音
竖版播报视频——推送到手机（企业微信 / Telegram），人工花两分钟转发到各平台。

> 量化引擎移植自 `fund_screener`（筛选 / 评分 / 滚动回测），
> 媒体与推送流水线移植自 `sp500&nq100-quota-bot`（卡片 / 视频 / 群机器人推送）。

## 它做什么

1. 从天天基金网拉取全市场基金，按各期收益综合排名取候选池（默认 300 只）
2. 应用 **6 项硬性筛选**（经理任职年限、成立年限、规模、回撤、近 1 年兜底等）
3. 计算 **6 维度软性评分**（业绩稳定性 / 熊市表现 / 经理任职 / 投资框架(卡玛) /
   风格一致性 / 规模），加权排序输出 **Top20 + 人话版入选原因**
4. 跑**滚动 5 起点 PIT 回测**（T-60/48/36/24/18 月）验证评分体系的跨周期 alpha，
   各窗口年化后取平均超额收益
5. 与**上周快照对比**，标注「🆕 新进榜 / ▲▼ 名次变动」
6. 生成素材：`cover`（封面卡）、`card`（Top20 长图）、`text`（文案）、
   `report.xlsx`（明细）、`video`（播报视频）
7. 推送到企业微信群机器人 / Telegram；产物同时上传为 Actions artifact

## 项目结构

```
active-fund-screener-bot/
├── .github/workflows/weekly.yml    # 每周六定时任务
├── assets/fonts/                   # Barlow Condensed（大数字字体，OFL）
├── src/                            # 量化引擎（移植自 fund_screener）
│   ├── config.py                   # 所有可调参数（筛选/评分/回测/熊市区间）
│   ├── data_fetcher_eastmoney.py   # 天天基金网爬取（默认数据源，并发 8 线程）
│   ├── data_fetcher.py             # AKShare 备用数据源
│   ├── metrics.py                  # 指标计算（回撤/卡玛/熊市穿越等）
│   ├── screener.py                 # 筛选与评分主逻辑（run_screening）
│   └── backtest.py                 # 滚动 5 起点回测（run_rolling_backtest）
├── card.py                         # Top20 卡片 / 封面 / 视频分幕帧渲染（Pillow）
├── video.py                        # TTS 配音 + ffmpeg 合成播报视频
├── notify.py                       # 企业微信 / Telegram 推送
├── main.py                         # 周报主入口（编排 + 环比 + 文案 + Excel）
├── data/                           # 每周榜单快照（入库，供下周对比）
├── output/                         # 当月产物（不入库）
└── requirements.txt
```

## 本地运行

```bash
pip install -r requirements.txt

python main.py              # 真实抓取 + 评分 + 回测 + 生成卡片/文案/Excel
python video.py             # 合成播报视频（edge-tts 配音，需联网）
python notify.py            # 推送（需先设好 WECOM_WEBHOOK 等环境变量）
```

常用参数：

```bash
python main.py --mock         # 用合成数据跑通整条媒体流水线（离线自测，不联网）
python main.py --no-backtest  # 跳过滚动回测，快速出榜
python main.py --date 2026-06-13 # 指定日期
python video.py 2026-06-13       # 为指定日期合成视频（读 output/payload_2026-06-13.json）
```

产物在 `output/`（文件名带年月）：
- `text_YYYY-MM-DD.txt` — 通用文案
- `cover_YYYY-MM-DD.png` — 周报封面卡（1080×1440）
- `card_YYYY-MM-DD.png` — Top20 排行长图（1080 宽）
- `report_YYYY-MM-DD.xlsx` — Excel 明细（Top20 / 全部候选 / 回测）
- `score_detail_YYYY-MM-DD.xlsx` — 评分明细（硬筛通过基金的 6 维子分 + 加权贡献 + 背后原始指标，便于横向对比）
- `payload_YYYY-MM-DD.json` — 媒体载荷（video.py 读取，免重跑筛选）
- `video_YYYY-MM-DD.mp4` — 1080×1920 竖版播报视频

大数字字体用 `assets/fonts/` 下的 Barlow Condensed，缺失时自动回退中文粗体。

## 部署到 GitHub Actions

1. 推送本仓库到 GitHub（建议**私有仓库**，避免输出泄露），Settings → Actions 启用。
2. `weekly.yml` 已配置每周六 UTC 01:13（北京时间约 09:13）触发。
   注意 GitHub schedule 不保证准点，高峰期可能延迟数小时。
3. Settings → Secrets and variables → Actions 配置推送通道（二选一或都配）：
   - `WECOM_WEBHOOK`：企业微信群机器人 webhook 完整 URL
   - `TG_BOT_TOKEN` + `TG_CHAT_ID`：Telegram 机器人 token 与会话 ID
4. 手动测试：Actions 页 → weekly-fund-report → Run workflow。

## 配置调整

打开 `src/config.py` 可改：数据源 `DATA_SOURCE`、筛选范围 `FUND_TYPES`、
硬筛阈值 `HARD_FILTER`、评分权重 `SCORE_WEIGHTS`、`TOP_N`、候选池大小、
熊市区间 `BEAR_MARKETS`、滚动回测起点 `BACKTEST_ROLLING_OFFSETS_MONTHS` 等。

## 已知限制

- **初筛工具**：风格一致性 / 投资框架等维度用了代理指标（行业相似度、卡玛比），
  NaN 数据硬筛放行、软评分回中位，出来的 Top20 应结合人工核查再决策。
- **回测年化超额**是各窗口持有期收益年化后相对全市场均值的差，属历史验证，
  不代表未来收益。
- **请求克制**：每周只跑一次，已设请求间隔与并发上限，不要改成高频轮询。
- **合规**：内容只陈述公开数据与量化评分，不构成投资建议，免责声明请勿删除。

## License

MIT License — 自由使用，但不对任何投资损失负责。
