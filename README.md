# FlightSaver

搜索英国 ⇄ 中国国际航班：抓取实时报价，给出"买 / 观望 / 跳过"建议，并附上各大中外旅行平台和航空公司官网的购票链接。

> 当前范围：只做搜索和购票链接，不代下单。只支持英国 ⇄ 中国的国际航线，国内航线后续再加。

## 快速开始

```bash
uv sync                      # 安装依赖（加 --extra jev 可启用 Jev 决策引擎）
uv run flightsaver search LON SHANGHAI 2026-12-20 --return 2027-01-05
uv run flightsaver search MAN PEK 2026-12-20 --budget 550 --max-stops 1
uv run flightsaver links LHR CAN 2026-12-20      # 只生成购票链接，不抓取
uv run flightsaver airports                       # 支持的机场和城市代码
```

`LON`、`BJS`、`LONDON`、`BEIJING`、`SHANGHAI`、`CHENGDU` 会展开成该城市的所有机场，然后逐个机场组合搜索。

也可以在 GitHub 上运行：**Actions → Flight search → Run workflow**。

## 对话网页

用自然语言对话搜索，比如"12月20号伦敦飞上海，1月5号回来"或"春节前从爱丁堡回成都，预算600镑以内"。由 Claude 理解你的需求，调用 FlightSaver 搜索，再把结果整理成航班卡片和购票链接。

```bash
uv sync --extra web --extra jev
export ANTHROPIC_API_KEY=sk-ant-...
uv run flightsaver web            # 打开 http://127.0.0.1:8000
```

### 免费部署到 Render

1. 打开 [Render Dashboard](https://dashboard.render.com/)，用 GitHub 账号登录。
2. **New → Blueprint**，选择 `Popcorn-attack/FlightSaver` 仓库。Render 会读取 `render.yaml`（免费套餐，Docker 部署）。
3. 按提示填入 `ANTHROPIC_API_KEY`（`TYPESAFE_API_KEY` 可以不填），然后点 **Apply**。
4. 部署完成后，在服务的 **Environment** 页面复制自动生成的 `FLIGHTSAVER_ACCESS_TOKEN`。打开 `https://flightsaver-xxxx.onrender.com`，第一次访问时输入这个口令。

免费套餐的限制：服务闲置 15 分钟后会休眠，下次打开需要等大约 1 分钟启动；没有持久磁盘，重新部署后价格历史会被清空。

### 其他平台

部署到 Fly.io、Railway 或云主机等任何能跑 Docker 的地方：

```bash
docker build -t flightsaver .
docker run -p 8000:8000 -e ANTHROPIC_API_KEY=... -e FLIGHTSAVER_ACCESS_TOKEN=自定义口令 \
  -v flightsaver-data:/data flightsaver
```

| 环境变量 | 说明 |
| --- | --- |
| `ANTHROPIC_API_KEY` | 必填，Claude API key |
| `FLIGHTSAVER_ACCESS_TOKEN` | 公开部署时建议设置；设置后打开网页需要输入这个口令，防止别人消耗你的 API 额度 |
| `TYPESAFE_API_KEY` | 可选，启用 Jev 决策引擎 |
| `FLIGHTSAVER_MODEL` | 可选，默认 `claude-opus-5-5` |

## 数据来源

| 类型 | 平台 | 实时报价 | 购票链接 |
| --- | --- | --- | --- |
| 元搜索 | Google Flights | ✅ 抓取（[fast-flights](https://github.com/AWeirdDev/flights)） | ✅ 已预填 |
| 元搜索 | Skyscanner、KAYAK | 规划中 | ✅ 已预填 |
| 国际 OTA | Trip.com、Expedia UK | 规划中 | ✅ 已预填 |
| 中国 OTA | 携程 | 规划中 | ✅ 已预填 |
| 中国 OTA | 去哪儿、飞猪、同程 | 规划中 | 平台首页 |
| 航司官网 | 英航、维珍、国航、东航、南航、海航、川航、国泰、芬航等 | 规划中 | 官网首页 |

Google Flights 汇总了大部分航司和 OTA 的报价，是覆盖面最广的免费数据源。其他平台的抓取器会逐个接入，统一实现 `providers/base.py` 里的 `Provider` 接口。

**说明：** 除了 Google Flights，其他平台和航司的链接都还没有经过实际访问验证，因为开发环境的网络屏蔽了这些网站。如果发现链接打不开或没有正确预填，请提 issue。

## 决策引擎

- `rules`（默认）：综合本次搜索的最低价、经停次数、飞行时长，以及历史价格的分位数打分（价格历史存在 `~/.flightsaver/history.sqlite3`）。
- `jev`（备选）：用 [TypeSafe AI](https://typesafe.ai) 的 Jev 模型，给每个报价判断"买 / 观望 / 跳过"，并按 0–4 分评估性价比。需要先运行 `uv sync --extra jev` 并设置 `TYPESAFE_API_KEY`；条件不满足时自动退回 `rules`。

```bash
TYPESAFE_API_KEY=... uv run flightsaver search LON SHANGHAI 2026-12-20 --engine jev
```

## 项目结构

```
src/flightsaver/
  models.py            SearchQuery / FlightOffer / BookingLink / Verdict
  airports.py          英国和中国机场、城市代码、航线范围检查
  providers/           数据源（google_flights.py）与购票链接（links.py）
  search.py            并发调用数据源、去重、排序
  history.py           SQLite 价格历史
  decision/            rules.py（默认）、jev.py（备选）
  cli.py               命令行入口
  web/                 对话网页：agent.py（Claude 工具调用循环）、app.py（FastAPI）、static/index.html
vendor/typesafe-sdk-python/   TypeSafe SDK 的项目内 fork（MIT），见 UPSTREAM.md
```

## 开发

```bash
uv sync --all-extras
uv run pytest
uv run ruff check src tests
```

## 参考项目

| 项目 | 说明 |
| --- | --- |
| [LetsFG/LetsFG](https://github.com/LetsFG/LetsFG) | 面向 agent 的机票和酒店搜索预订：MCP server、CLI、SDK |
| [tourmind-com/Tourmind-Booking-Skills](https://github.com/tourmind-com/Tourmind-Booking-Skills) | 酒店和机票预订的 agent skills，跨 OTA 比价 |
| [AWeirdDev/flights](https://github.com/AWeirdDev/flights) | Google Flights 抓取库 |
| [flightclaw/agents](https://github.com/flightclaw/agents) | 机票搜索和价格追踪的 MCP server |
