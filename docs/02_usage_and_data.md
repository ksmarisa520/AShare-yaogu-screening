# 脚本与数据说明

## 1. 环境

- Python **3.8+**，只用标准库（`urllib` / `json` / `argparse` / `statistics`），**不需要 pip install 任何东西**。
- 能访问 `push2ex.eastmoney.com` 与 `push2his.eastmoney.com`。
- Windows / macOS / Linux 均可；`python3` 在 Windows 上可能叫 `python`。

---

## 2. `scripts/scan_zt_pool.py` —— 每日扫描

拉当日涨停池 / 炸板池 / 跌停池，对每只票按评分卡打分排序，并单独列出「次日候选」与「持仓退出预警」。

```bash
python3 scripts/scan_zt_pool.py                 # 今天
python3 scripts/scan_zt_pool.py 20261113        # 指定交易日
python3 scripts/scan_zt_pool.py 20261113 --top 60
python3 scripts/scan_zt_pool.py 20261113 --min-score 9
python3 scripts/scan_zt_pool.py 20261113 --json out/pick.json
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `date` | 今天 | 交易日 `YYYYMMDD` |
| `--top N` | 45 | 主表打印行数 |
| `--min-score N` | 7 | 进「次日候选」的最低分 |
| `--json PATH` | 无 | 额外输出结构化 JSON |

**输出分四段**
1. 市场温度行：涨停 / 炸板 / 炸板率 / 跌停家数 + 基准率提示
2. 主表（按 score 降序）：`GRD SCORE BRD SEAL@ TURN% SEAL% FLOAT BRK SECTOR N CODE NAME`
3. `>>> NEXT-DAY CANDIDATES`：首板且 score ≥ 阈值的票
4. `>>> EXIT WARNINGS` + 当日炸板股清单

**JSON 结构**

```json
{
  "date": "20261113",
  "source": "eastmoney push2ex",
  "base_rates": {"first_board": 17.0, "boards_2": 36.1, "boards_3": 40.6,
                 "boards_4": 42.9, "boards_5p": 50.0},
  "market": {"limit_up": 37, "broken": 21, "broke_rate": 36.2, "limit_down": 0},
  "rows": [{"code": "002905", "name": "金逸影视", "boards": 1,
            "seal_time": "09:32", "turnover": 2.36, "seal_pct": 2.41,
            "float_yi": 34.6, "broke": 0, "sector": "影视院线",
            "sector_n": 5, "score": 14, "grade": "A", "flags": "-"}],
  "next_day_candidates": ["002905", "603928", "603103", "002853"],
  "exit_warnings": ["002148", "605255"],
  "broken_today": ["600000"]
}
```

**评分实现**（满分 15，与 SKILL.md §2 评分卡逐条对应）

| 项 | 条件 | 分 |
|---|---|---:|
| 封板时间 | ≤09:35 / 09:35–10:00 / 其他 | +3 / +1 / −1 |
| 换手 | <5% / 5–10% / 10–20% / >20% | +3 / +1 / −2 / −3 |
| 封单比 | >4% / 2–4% / 1–2% / <1% | +4 / +3 / +1 / −1 |
| 流通市值 | 20–50 亿 / 50–100 亿 / 其他 | +2 / 0 / −1 |
| 炸板 | 0 / 1 / 2+ | +1 / 0 / −2 |
| 板块 | ≥5 家 / 3–4 家 / 孤立 | +2 / +1 / 0 |

等级：`A ≥9`、`B ≥7`、`C ≥4`、`D <4`。

`flags` 是独立的告警标记（不影响评分）：`VOL>10%`、`SEAL<1%`、`BROKE{n}`。

---

## 3. `scripts/base_rates.py` —— 基准率重跑

逐日对撞涨停池与次日池，重算 §1 的全部晋级率表。

```bash
python3 scripts/base_rates.py                            # 默认 20260901 起
python3 scripts/base_rates.py 20260901 20261113          # 指定窗口
python3 scripts/base_rates.py 20260923 20261020 --offline # 只用缓存，不联网
python3 scripts/base_rates.py 20260901 20261113 --refetch # 忽略缓存重抓
python3 scripts/base_rates.py 20260901 20261113 --exclude 20261021
```

| 参数 | 说明 |
|---|---|
| `beg` / `end` | 窗口，`YYYYMMDD`；只统计落在窗口内的日期 |
| `--offline` | 不联网，只用已有缓存（复现文档表格用这个） |
| `--refetch` | 已有缓存的日子也重抓 |
| `--cache PATH` | 指定缓存文件 |
| `--exclude YYYYMMDD` | 额外剔除一天（例如某天的盘中快照） |

**自动行为**
- 当天（`time.strftime('%Y%m%d')`）永远从统计里剔除——盘中抓的池不是收盘终值。
- 已知的盘中快照日（脚本常量 `INTRADAY_SNAPSHOT_DAYS`，当前只有 `20261021`）自动剔除，会打印一行 `dropping known intraday snapshot day(s)`。
- 只抓缓存里没有的交易日，已有的不重复请求（约 3 个请求/交易日，1 秒间隔）。

所以在全新克隆上直接跑 `python3 scripts/base_rates.py --offline`，得到的窗口就是 **20260923 → 20261020 共 14 天**，与 `docs/01_base_rate_study_2026-10.md` 逐格一致。

---

## 4. 缓存机制（重要）

读取顺序是**合并**，不是覆盖：

```
仓库自带种子  data/zt_study.json          ← 只读，永不写入
用户缓存      ~/.cache/yaogu/zt_study.json  ← 所有写入都到这里
```

同一天两边都有时，以用户缓存为准。

**为什么种子只读**：`data/zt_study.json` 里的 2026-09-23 → 10-21 这段历史，已经超出东财涨停池 API 约 1 个月的保留期，**现在重新抓只会拿到空数组**。它是这段样本的唯一副本，覆盖了就永久丢失。

**怎么把样本养长**：每个交易日盘后跑一次

```bash
python3 scripts/base_rates.py $(date -d '-3 days' +%Y%m%d) $(date +%Y%m%d)
```

放到 crontab 里（Linux）就是：

```cron
30 16 * * 1-5 cd /path/to/yaogu-screening && python3 scripts/base_rates.py $(date -d '-3 days' +\%Y\%m\%d) $(date +\%Y\%m\%d) >> ~/.cache/yaogu/run.log 2>&1
```

累积够 60+ 个交易日后，`base_rates.py` 出的表才勉强够当「跨市场环境」的赔率参考。

**清空重来**：删掉 `~/.cache/yaogu/zt_study.json` 即可，仓库种子不受影响。

---

## 5. `data/` 目录

| 文件 | 内容 |
|---|---|
| `zt_study.json` | 320 KB。`{"YYYYMMDD": {"zt": [...], "zb": [...], "dt": [...]}}`，96 个交易日 key（2026-06-01 → 2026-10-21），其中 **15 天有数据**（2026-09-23 起）。其余 81 天是 `zt/zb/dt` 全空的占位，正是「API 只保留约 1 个月」的直接证据。 |
| `daily_limitup_summary.csv` | 逐日涨停/炸板/跌停家数，15 行 + 表头，用来快速看情绪温度。最后一行 2026-10-21 是盘中快照，做统计要剔除。 |

`zt_study.json` 里单条记录的关键字段（东财原始口径）：

```json
{"c": "002905", "n": "金逸影视", "p": 12340, "zdp": 10.01, "amount": 41239093,
 "ltsz": 1358008845.12, "hs": 2.36, "lbc": 1, "fbt": 93200, "lbt": 93200,
 "fund": 49555424, "zbc": 0, "hybk": "影视院线"}
```

- `p` 是价格 ×1000（`12340` = 12.34 元）
- `fbt` / `lbt` 是时间，`HHMMSS` 去掉冒号（`93200` = 09:32:00）
- `ltsz` / `fund` 单位是元
