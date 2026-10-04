# FlightSaver

自动帮你搜机票、盯价格、在合适的时候提醒你下单的 agent。

> 状态：项目初始化阶段，架构方案讨论中。

## 目录

- `vendor/typesafe-sdk-python/`：TypeSafe AI 官方 Python SDK 的项目内 fork（MIT），
  用于接入 [Jev](https://docs.typesafe.ai/)（System One 类型化决策模型），作为
  "买 / 等 / 提醒" 等决策环节的一个可选引擎。上游信息见 `UPSTREAM.md`。

## 参考项目

| 项目 | 说明 |
| --- | --- |
| [LetsFG/LetsFG](https://github.com/LetsFG/LetsFG) | Agent 原生的机票/酒店搜索预订：MCP server、CLI、Python/JS SDK |
| [tourmind-com/Tourmind-Booking-Skills](https://github.com/tourmind-com/Tourmind-Booking-Skills) | 酒店 + 机票预订的 agent skills，比价 OTA |
| [AWeirdDev/flights](https://github.com/AWeirdDev/flights) | 快速的 Google Flights 抓取库（protobuf 构造请求） |
| [flightclaw/agents](https://github.com/flightclaw/agents) | 机票搜索 + 价格追踪的托管 MCP server |
| [harsh-vardhhan/ai-agent-flight-scanner](https://github.com/harsh-vardhhan/ai-agent-flight-scanner) | 基于 Google Flights 数据的 AI agent |
