# 高精度队列模拟撮合系统

> 基于 Level-2 逐笔成交和盘口行情的高精度队列模拟撮合系统

## 系统特性

- **逐笔驱动撮合**：基于真实 Level-2 逐笔成交数据驱动撮合逻辑
- **价格优先 + 时间优先**：严格遵循交易所撮合规则
- **队列模拟**：非最优价委托进入队列排队，记录队列长度和位置
- **行情触发消耗**：当逐笔成交价格优于或等于队列价格时，按 FIFO 消耗队列
- **实时 API**：支持 REST API 和 WebSocket 实时推送
- **高精度**：委托处理延迟 < 1ms，逐笔响应延迟 < 1ms

## 架构概览

```
Level-2 行情源 → 行情解析 → 撮合引擎 → 订单簿 → 队列管理 → 成交记录
                     ↓
              外部委托 API (REST/WebSocket)
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 启动服务

```bash
python main.py
```

服务将在 `http://localhost:8000` 启动。

### 3. API 文档

启动后访问：
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## 核心接口

### 提交委托

```bash
curl -X POST "http://localhost:8000/api/v1/orders" \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "000001.SZ",
    "side": "buy",
    "price": "10.50",
    "quantity": 1000,
    "order_type": "limit"
  }'
```

响应示例：
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "order_id": "ord-abc123",
    "symbol": "000001.SZ",
    "side": "buy",
    "price": "10.50",
    "quantity": 1000,
    "filled_qty": 0,
    "status": "queued",
    "queue_info": {
      "queue_length_at_enter": 15,
      "queue_position_at_enter": 15,
      "current_queue_length": 15,
      "current_queue_position": 15,
      "enter_queue_time": "2024-06-16T10:30:00.123456",
      "queue_wait_ms": 0
    }
  }
}
```

### 查询订单簿

```bash
curl "http://localhost:8000/api/v1/orderbook/000001.SZ"
```

### WebSocket 订阅行情

```javascript
const ws = new WebSocket("ws://localhost:8000/ws/v1");
ws.onopen = () => {
  ws.send(JSON.stringify({
    action: "subscribe",
    channel: "market",
    symbols: ["000001.SZ"]
  }));
};
ws.onmessage = (event) => {
  console.log(JSON.parse(event.data));
};
```

## 撮合逻辑

### 买入委托

1. 如果买入价格 >= 最优卖价 → **立即撮合**
2. 如果买入价格 < 最优卖价 → **进入买方队列**
   - 记录当前队列长度 `queue_length`
   - 记录进入位置 `queue_position`

### 卖出委托

1. 如果卖出价格 <= 最优买价 → **立即撮合**
2. 如果卖出价格 > 最优买价 → **进入卖方队列**
   - 记录当前队列长度 `queue_length`
   - 记录进入位置 `queue_position`

### 逐笔成交驱动队列消耗

当收到新的逐笔成交时：
- **买方主动成交（外盘）**：消耗卖方队列中价格 <= 成交价的订单
- **卖方主动成交（内盘）**：消耗买方队列中价格 >= 成交价的订单

## 项目结构

```
├── docs/
│   ├── architecture.md      # 系统架构设计
│   ├── api_spec.md          # API 接口规范
│   └── matching_logic.md    # 撮合逻辑详细说明
├── src/
│   ├── core/
│   │   ├── order.py         # 订单模型
│   │   ├── order_book.py    # 订单簿
│   │   └── matching_engine.py  # 撮合引擎
│   ├── data/
│   │   ├── market_data.py   # 行情数据模型
│   │   └── level2_feed.py   # Level-2 行情接入
│   ├── api/
│   │   └── server.py        # FastAPI 服务
│   └── utils/
│       └── config.py        # 配置管理
├── tests/                    # 测试用例
├── main.py                   # 启动入口
├── requirements.txt          # 依赖
└── README.md                 # 本文件
```

## 文档

- [系统架构设计](docs/architecture.md)
- [API 接口规范](docs/api_spec.md)
- [撮合逻辑详细说明](docs/matching_logic.md)

## 技术栈

- **Python 3.9+**
- **FastAPI** - 高性能异步 Web 框架
- **uvicorn** - ASGI 服务器
- **sortedcontainers** - 高效有序字典（用于订单簿价格索引）
- **asyncio** - 异步事件驱动

## 性能指标

| 指标 | 目标 |
|---|---|
| 委托处理延迟 | < 1ms |
| 逐笔响应延迟 | < 1ms |
| 单标吞吐量 | > 10,000 笔/秒 |
| 并发标的数 | > 1,000 |

## 扩展计划

- [ ] Redis 持久化支持
- [ ] 真实 Level-2 行情源接入（腾讯、新浪、券商接口）
- [ ] 文件回放回测模式
- [ ] 多节点集群部署
- [ ] 监控和告警（Prometheus/Grafana）
- [ ] 用户管理和权限控制

## License

MIT
