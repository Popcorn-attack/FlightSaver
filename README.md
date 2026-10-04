# FlightSaver

搜索英国 ⇄ 中国国际航班：抓取实时报价，给出"买 / 观望 / 跳过"建议，并附上各大中外旅行平台和航空公司官网的购票链接。

> 当前范围：只做搜索和购票链接，不代下单。只支持英国 ⇄ 中国的国际航线，国内航线后续再加。

## 快速开始

```bash
uv sync --extra browser && uv run playwright install chromium   # 安装依赖和浏览器
uv run flightsaver search LON SHANGHAI 2026-12-20 --return 2027-01-05
uv run flightsaver search MAN PEK 2026-12-20 --budget 550 --max-stops 1
uv run flightsaver links LHR CAN 2026-12-20      # 只生成购票链接，不抓取
uv run flightsaver airports                       # 支持的机场和城市代码
```

`LON`、`BJS`、`LONDON`、`BEIJING`、`SHANGHAI`、`CHENGDU` 会展开成该城市的所有机场，然后逐个机场组合搜索。

也可以在 GitHub 上运行：**Actions → Flight search → Run workflow**。

## 对话网页

用自然语言搜索，比如"12月20号伦敦飞上海，1月5号回来"或"春节前从爱丁堡回成都，预算600镑以内"。网页有两种模式：

- **快速模式**（默认，不消耗 API 额度）：在本地用规则解析你的话（`src/flightsaver/nlp.py`），能识别中英文城市名、各种日期写法（12月20日、20 Dec、下周五、圣诞节、春节前、1月中旬等）、往返、舱位、人数、预算和直飞要求。解析后直接搜索，结果摘要由模板生成。如果缺信息，可以接着补一句，比如"12月20日"，会沿用上一句的城市；也可以点"让 AI 理解"。
- **AI 对话**：由 Claude 理解需求并总结结果。默认用最便宜的 `claude-haiku-4-5`，并做了这些节省：给模型看的搜索结果精简到前 5 条且不带链接，回复限制在 1500 token 以内，对话超过 12 条消息会清空上下文。另外有每日次数上限，由 `FLIGHTSAVER_AI_DAILY_LIMIT` 控制，默认 20 次，设为 0 即完全关闭 AI。按 token 估算，每条 AI 消息约 0.5 美分。

```bash
uv sync --extra web --extra jev --extra browser
uv run playwright install chromium   # KAYAK / Trip.com / 携程需要
export ANTHROPIC_API_KEY=sk-ant-...
uv run flightsaver web            # 打开 http://127.0.0.1:8000
```

### 部署（推荐 Google Cloud Run，免费额度内）

KAYAK、Trip.com、携程的结果需要用无头浏览器 Chromium 抓取，至少需要约 1–2 GB 内存。我们在 GitHub Actions 上按各平台的内存上限做了模拟，结果如下：

| 平台 | 内存 | 可用数据源 | 费用 |
| --- | --- | --- | --- |
| **Google Cloud Run**（推荐） | 2 GiB | 全部 4 个 | 每月免费额度约够 6000 次搜索；需绑定信用卡 |
| Render 免费套餐 | 512 MB | Google Flights + Trip.com（精简浏览器模式；携程、KAYAK 内存不够） | 免费；闲置 15 分钟后休眠 |
| 自己的电脑或服务器 | 不限 | 全部 4 个 | 免费 |

Hugging Face Spaces 从 2026 年 7 月起不再允许免费账户运行 Docker，所以没有列入。

**Cloud Run 部署步骤：**

1. 打开 [Google Cloud Console](https://console.cloud.google.com/)，新建一个项目，并绑定结算账号（在免费额度内不会扣费）。
2. 打开 [Cloud Shell](https://shell.cloud.google.com)，依次运行：
   ```bash
   git clone -b claude/flightsaver-flight-search-fm9vk3 https://github.com/Popcorn-attack/FlightSaver
   cd FlightSaver
   gcloud config set project <你的项目ID>
   bash deploy/cloudrun.sh        # 按提示粘贴 ANTHROPIC_API_KEY
   ```
3. 脚本运行结束后会输出网址和访问口令。部署在伦敦区域，内存 2 GiB，最多 1 个实例，闲置时缩到 0 个实例、不计费。

**Render 免费精简版：** 在 [Render Dashboard](https://dashboard.render.com/) 选择 **New → Blueprint**，选这个仓库，填入 `ANTHROPIC_API_KEY`，然后点 **Apply**。部署完成后，到服务的 Environment 页面复制自动生成的 `FLIGHTSAVER_ACCESS_TOKEN`，作为访问口令。精简版有 Google Flights 和 Trip.com 的实时价格（实测一次约 120 个报价、13 秒），所有平台和航司官网的购票链接照常提供。

### 其他平台

部署到 Fly.io、Railway 或云主机等任何能跑 Docker 的地方（建议 ≥ 2 GB 内存）：

```bash
docker build -t flightsaver .
docker run -p 8000:8000 -e ANTHROPIC_API_KEY=... -e FLIGHTSAVER_ACCESS_TOKEN=自定义口令 \
  -v flightsaver-data:/data flightsaver
```

| 环境变量 | 说明 |
| --- | --- |
| `ANTHROPIC_API_KEY` | 只有 AI 模式需要；不设置时只能用快速模式 |
| `FLIGHTSAVER_ACCESS_TOKEN` | 公开部署时建议设置；设置后打开网页需要输入这个口令，防止别人消耗你的 API 额度 |
| `TYPESAFE_API_KEY` | 可选，启用 Jev 决策引擎 |
| `FLIGHTSAVER_MODEL` | 可选，AI 模式使用的模型，默认 `claude-haiku-4-5`（最便宜） |
| `FLIGHTSAVER_AI_DAILY_LIMIT` | 每天最多几条 AI 消息，默认 20；设为 `0` 则关闭 AI 模式，只保留免费的快速模式 |
| `FLIGHTSAVER_PROVIDERS` | 可选，指定要用的数据源，逗号分隔，默认 `google_flights,kayak,trip_com,ctrip` |
| `FLIGHTSAVER_BROWSER` | 设为 `0` 时禁用无头浏览器，适合小内存主机 |
| `FLIGHTSAVER_BROWSER_CONCURRENCY` | 同时打开的浏览器数量，默认 2，每个约占几百 MB 内存 |
| `FLIGHTSAVER_BROWSER_LEAN` | 设为 `1` 时启用省内存的浏览器模式（512 MB 主机用） |
| `FLIGHTSAVER_VALUE_OF_TIME` | 综合排序里每小时旅行时间折算多少英镑，默认 15；调高会更偏向快的航班 |

## 数据来源

| 平台 | 实时报价 | 抓取方式 | 购票链接 |
| --- | --- | --- | --- |
| Google Flights | ✅ | 先直接发 HTTP 请求（[fast-flights](https://github.com/AWeirdDev/flights)）；被拒绝时改用浏览器，截获 `GetShoppingResults` 接口 | ✅ 已预填 |
| KAYAK | ✅ | 浏览器截获结果轮询接口 `flights/poll` | ✅ 精确到具体航班 |
| Trip.com | ✅ | 浏览器截获 `FlightListSearchSSE` 流式接口 | ✅ 已预填 |
| 携程 | ✅（人民币价格自动换算） | 浏览器截获 `search/pull` 接口 | ✅ 已预填 |
| Skyscanner | ❌ 有验证码 | — | ✅ 已预填 |
| Expedia UK | 规划中 | — | ✅ 已预填 |
| 去哪儿、飞猪、同程 | 规划中 | — | 平台首页 |
| 航司官网（英航、国航、东航等） | 规划中 | — | 官网首页 |

KAYAK、Trip.com、携程的接口都需要由页面 JS 生成签名或令牌，没法直接发 HTTP 请求复现，所以用无头浏览器打开这些网站自己的搜索页，读取页面拿到的 JSON（需要 `uv sync --extra browser` 和 `playwright install chromium`）。

`tests/fixtures/live/` 里保存了 `.github/workflows/probe.yml` 抓下来的真实返回数据。解析器的测试都基于这些数据；网站改版后，重新运行这个 workflow 就能更新样本。另外 `.github/workflows/live.yml` 会在每次推送后做一次真实搜索，并测试容器在不同内存上限下能否正常运行。

在 GitHub 的服务器上实测，伦敦 → 上海单程四个来源一共返回约 260 个报价，耗时约 27 秒。

## 决策引擎

- `rules`（默认）：按"广义成本"排序和打分，计算方法是票价加上时间价值（默认每小时 £15）乘以总时长，总时长包含中转等待。另外这几种情况会加罚：中转超过 6 小时、中转不到 1 小时（有误机风险）、需要换机场、自行转机（分开出票）。中转太紧的行程不会被标为"推荐"。如果积累了价格历史，还会参考当前价格在历史中的分位数（价格历史存在 `~/.flightsaver/history.sqlite3`）。
- 结果页可以按"综合 / 最便宜 / 最快"排序。每条结果显示总时长、每次中转的机场和等待时间；KAYAK 的结果里如果有航司官网直销的价格，会单独标出"航司官网价"。
- `jev`（备选）：用 [TypeSafe AI](https://typesafe.ai) 的 Jev 模型，给每个报价判断"买 / 观望 / 跳过"，并按 0–4 分评估性价比。需要先运行 `uv sync --extra jev` 并设置 `TYPESAFE_API_KEY`；条件不满足时自动退回 `rules`。

```bash
TYPESAFE_API_KEY=... uv run flightsaver search LON SHANGHAI 2026-12-20 --engine jev
```

## 项目结构

```
src/flightsaver/
  models.py            SearchQuery / FlightOffer / BookingLink / Verdict
  airports.py          英国和中国机场、城市代码、航线范围检查
  providers/           数据源：google_flights.py、kayak.py、trip_com.py、ctrip.py；
                       browser.py（无头浏览器抓取）；links.py（购票链接）
  fx.py / airlines.py  汇率换算 / 航司代码与英文名
  search.py            并发调用数据源、去重、排序
  history.py           SQLite 价格历史
  decision/            rules.py（默认）、jev.py（备选）
  cli.py               命令行入口
  nlp.py               本地解析自然语言（快速模式，不调用 LLM）
  web/                 网页：quick.py（快速模式）、agent.py（AI 模式）、app.py（FastAPI）、static/index.html
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
