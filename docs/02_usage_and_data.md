# 脚本与数据说明

## 1. 环境

- Python **3.8+**，只用标准库（`urllib` / `json` / `argparse` / `statistics` / `unicodedata`），**不需要 pip install 任何东西**。
- 能访问 `push2ex.eastmoney.com`、`push2his.eastmoney.com`、`np-anotice-stock.eastmoney.com`、`datacenter-web.eastmoney.com`，以及兜底用的 `web.ifzq.gtimg.cn`。
- Windows / macOS / Linux 均可；`python3` 在 Windows 上可能叫 `python`。

## 2. 脚本一览

| 脚本 | 用途 | 联网 |
|---|---|---|
| `scripts/scan_zt_pool.py` | 涨停池评分排序 + 次日候选 + 退出预警 + JSON | 是 |
| `scripts/yaogu_pick.py` | 对 ≥7 分候选做二次核查（公告/龙虎榜/财报日/出售回购/K线形态） | 是 |
| `scripts/scan_dt_pool.py` | 跌停池 X/D/C 分档 + 风险提示命中 + 可选基准率 | 是 |
| `scripts/base_rates.py` | 重算晋级率 / 炸板率 / 次日跌停率全表 | 可选（`--offline`） |
| `scripts/yaogu_fetch.py` | HTTP 取数库：K 线（东财→腾讯兜底）、公告列表、公告正文 | — |
| `scripts/yaogu_data.py` | 数据路径库：仓库种子只读 + 用户缓存可写 | — |
| `backtest/zt_backtest.py` | 涨停正向四路径回测 | 否（读冻结快照） |
| `backtest/dt_backtest.py` | 跌停反向四路径回测 | 否（读冻结快照） |

---

## 3. `scripts/scan_zt_pool.py` —— 每日扫描

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
2. 主表（按 score 降序）：`GRD SCORE BRD SEAL@ TURN% SEAL% SAMT% FLOAT BRK SECTOR N CODE NAME`
3. `>>> NEXT-DAY CANDIDATES`：首板且 score ≥ 阈值的票
4. `>>> EXIT WARNINGS` + 当日炸板股清单

**`SEAL%` vs `SAMT%`**

| 字段 | 算法 | 含义 |
|---|---|---|
| `SEAL%` | 封单 ÷ 流通市值 | 锁仓强度 → 次日还能不能涨 |
| `SAMT%` | 封单 ÷ 成交额 | 订单簿厚度 → 今天你能不能买到 |

两者相关系数 0.68，不是同一件事。`SAMT%` ≥50% 的档次日一字率 45%，意味着那个档位的溢价你基本吃不到。

**JSON 结构**

```json
{
  "date": "20261113",
  "source": "eastmoney push2ex",
  "base_rates": {"first_board": 17.0, "boards_2": 36.1, "boards_3": 40.6,
                 "boards_4": 42.9, "boards_5p": 50.0},
  "market": {"limit_up": 37, "broken": 21, "broke_rate": 36.2, "limit_down": 33},
  "rows": [{"code": "002905", "name": "金逸影视", "boards": 1,
            "seal_time": "09:32", "turnover": 2.36, "seal_pct": 2.41,
            "seal_amt_pct": 103.9, "float_yi": 34.6, "broke": 0,
            "sector": "影视院线", "sector_n": 5, "score": 14, "grade": "A",
            "flags": "-"}],
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

## 4. `scripts/yaogu_pick.py` —— 二次核查

在评分之上补数字说不出来的部分。默认对 `score >= 7` 的票逐只查：

- **K 线形态**：5/20/60 日涨幅、60 日箱体宽度、MA20、本波累计涨幅
- **公告**：风险提示 / 异常波动 / 停牌 / 减持，以及激励、重组、定增等事件
- **出售回购股份**：命中会单独打 `[BUYBACK SALE]`（见 `docs/06_case_600661.md`）
- **龙虎榜**：近 5 次记录 + 净买卖金额（看序列，不看单日）
- **财报预约日**：报告期 / 预约披露日 / 实际披露日

```bash
python3 scripts/yaogu_pick.py                 # 今天
python3 scripts/yaogu_pick.py 20261113
python3 scripts/yaogu_pick.py 20261113 --min-score 9 --json out/pick.json
```

结果默认写到 `out/pick_<date>.json`。

**公告关键词的两个坑**

- `RISK_KW` 里含「核查」「警示」，会命中「保荐机构核查意见」「持续督导」这类文件 → 用 `NOISE_KW`（保荐／持续督导／核查意见／验资／法律意见／审计报告）先剔除，否则一半命中都是噪音。
- 标题看不出卖压。「关于首次集中竞价出售回购股份的进展公告」不含「减持」二字，必须单独匹配 `BUYBACK_SALE_KW`。

---

## 5. `scripts/scan_dt_pool.py` —— 跌停池

```bash
python3 scripts/scan_dt_pool.py 20261113
python3 scripts/scan_dt_pool.py 20261113 --backtest   # 附带跌停基准率重算
```

分档逻辑：跌停股没有「买入档」，只有「这个反抽值不值得等」。

| 档 | 条件 |
|---|---|
| `X` | 已发风险提示/异动公告，或封死（封单/成交额 ≥15%）—— 买不到或不该买 |
| `D` | 10 日内有涨停血统 |
| `C` | 无涨停记录 |

每只票同时打印：换手、封单/成交额、流通市值、前 20 日涨幅、`%b`、跌停时间、板块。

> 注意：`D` 档（有涨停血统）在这段样本里比 `C` 档更差（次日中位 −3.86% vs −2.35%）。跌停是情绪终点，"它刚炒过"是负资产，不是加分项。

---

## 6. `scripts/base_rates.py` —— 基准率重跑

逐日对撞涨停池与次日池，重算 `docs/01` 的全部晋级率表，外加 TABLE 1.5 次日跌停率。

```bash
python3 scripts/base_rates.py                            # 默认 20260901 起
python3 scripts/base_rates.py 20260901 20261113          # 指定窗口
python3 scripts/base_rates.py --offline                  # 只用缓存，不联网
python3 scripts/base_rates.py 20260901 20261113 --refetch # 忽略缓存重抓
python3 scripts/base_rates.py 20260901 20261113 --exclude 20261021
```

| 参数 | 说明 |
|---|---|
| `beg` / `end` | 窗口，`YYYYMMDD`；只统计落在窗口内的日期 |
| `--offline` | 不联网，只用已有缓存（复现文档表格用这个） |
| `--refetch` | 已有缓存的日子也重抓 |
| `--cache PATH` | 指定缓存文件 |
| `--exclude YYYYMMDD` | 额外剔除一天 |

**自动行为**
- 当天（`time.strftime('%Y%m%d')`）永远从统计里剔除——盘中抓的池不是收盘终值。
- 已知的盘中快照日（脚本常量 `INTRADAY_SNAPSHOT_DAYS`，当前只有 `20261021`）自动剔除，会打印一行 `dropping known intraday snapshot day(s)`。
- 只抓缓存里没有的交易日，已有的不重复请求（约 3 个请求/交易日，1 秒间隔）。
- **跌停池用 `sort=fund:asc`**，不是涨停池那个 `fbt:asc`。用错会静默返回空 pool。
- 读缓存时会额外把 `data/dt_pools.json` 合并成 `dt` 层（只填没有跌停池的日子，不覆盖种子和用户缓存）。这一步不做的话，`zt_study.json` 里的 `dt` 是空的，**TABLE 1.5 次日跌停率会静默失效**。

所以在全新克隆上直接跑 `python3 scripts/base_rates.py --offline`，得到的窗口就是 **20260923 → 20261020 共 14 天**，与 `docs/01_base_rate_study_2026-10.md` 逐格一致（含 TABLE 1.5：涨停股次日跌停 5.5%/n=649、炸板股 6.6%/n=258）。

---

## 7. `scripts/yaogu_fetch.py` / `yaogu_data.py` —— 两个库

**`yaogu_fetch.py`**（被 `scan_dt_pool` / `yaogu_pick` 引用）

| 函数 | 说明 |
|---|---|
| `kline(code, lmt)` | 日 K → `{YYYYMMDD: [date, open, close, high, low, vol]}`。东财优先，失败自动回落腾讯 |
| `notices(code)` | 公告标题列表 `[(date, title)]` |
| `notice_bodies(code, keyword=...)` | **公告正文**（去 HTML），用于读出售回购数量/均价/余额 |
| `try_get(url)` | 取 JSON，失败返回 `None` 而不是抛异常 |

东财 `push2his` 连续抓几百只后会持续返回 `Remote end closed connection without response`。腾讯兜底接口：
`https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sz000001,day,,,60,qfq`（国证2000 = `sz399303`，中证全指 = `sh000985`）。

**`yaogu_data.py`**

| 函数 | 说明 |
|---|---|
| `read_json(name)` | 用户缓存优先，回落到仓库 `data/` |
| `read_fixture(name)` | **只读仓库 `data/`**，回测脚本用它保证可复现 |
| `write_json(name, obj)` | 只写用户缓存 |
| `path_of(name)` | 找到实际读取的文件路径 |

---

## 8. `backtest/` —— 双向回测

```bash
python3 backtest/zt_backtest.py            # 涨停正向
python3 backtest/dt_backtest.py            # 跌停反向
python3 backtest/zt_backtest.py --cost 0.004
python3 backtest/dt_backtest.py --cost 0.001 --slip 0.001 --maxfb 0.05
```

两个脚本都**固定读 `data/` 里的冻结快照**（`read_fixture`），不读用户缓存——否则你积累的新数据会悄悄改变样本，文档里的数字就复现不出来了。

`zt_backtest.py` 会把 693 条明细写到 `~/.cache/yaogu/zt_obs.json`。

结论与限制见 `docs/03` / `docs/04`，一句话版见 `backtest/README.md`。

---

## 9. 缓存机制（重要）

读取顺序是**合并**，不是覆盖：

```
仓库自带种子  data/          ← 只读，永不写入
用户缓存      ~/.cache/yaogu/  ← 所有写入都到这里
```

同一文件两边都有时，以用户缓存为准。

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

**清空重来**：删掉 `~/.cache/yaogu/` 即可，仓库种子不受影响。

**改位置**：`YAOGU_DATA`（种子目录）、`YAOGU_CACHE_DIR`（用户缓存目录）、`YAOGU_CACHE`（`base_rates.py` 的缓存文件路径）。

---

## 10. `data/` 目录

| 文件 | 大小 | 内容 |
|---|---|---|
| `zt_study.json` | 320 KB | `{"YYYYMMDD": {"zt": [...], "zb": [...], "dt": [...]}}`，96 个交易日 key（2026-06-01 → 2026-10-21），其中 **15 天有数据**。其余 81 天是全空占位，正是「API 只保留约 1 个月」的直接证据。`dt` 数组在这份快照里是空的（抓的时候还没发现排序字段的坑）。 |
| `dt_pools.json` | 66 KB | 跌停池快照，15 个跌停日，用正确的 `sort=fund:asc` 抓的。`base_rates.py` 读缓存时自动把它合并成 `dt` 层，TABLE 1.5 就靠它 |
| `zt_kl.json` | 2.0 MB | 涨停池涉及个股的日 K（含开盘价） |
| `kl_store.json` | 748 KB | 跌停池涉及个股的日 K |
| `bench.json` | 2.5 KB | 国证2000 / 中证全指 日收盘 |
| `bench_oc.json` | 3.8 KB | 同上，含开盘价（用于算日内超额） |
| `zt_obs.json` | 447 KB | 693 条「涨停股-日」明细 |
| `dt_obs.json` | 100 KB | 219 条「跌停股-日」明细 |
| `daily_limitup_summary.csv` | 0.3 KB | 逐日涨停/炸板/跌停家数，15 行 + 表头 |

`zt_study.json` 里单条记录的关键字段（东财原始口径）：

```json
{"c": "002905", "n": "金逸影视", "p": 12340, "zdp": 10.01, "amount": 41239093,
 "ltsz": 1358008845.12, "hs": 2.36, "lbc": 1, "fbt": 93200, "lbt": 93200,
 "fund": 49555424, "zbc": 0, "hybk": "影视院线"}
```

- `p` 是价格 ×1000（`12340` = 12.34 元）
- `fbt` / `lbt` 是时间，`HHMMSS` 去掉冒号（`93200` = 09:32:00）
- `ltsz` / `fund` / `amount` 单位是元
- `fund / amount` 就是 `SAMT%`，`fund / ltsz` 就是 `SEAL%`
