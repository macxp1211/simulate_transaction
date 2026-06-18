# API 接口规范

> 本文档描述高精度队列模拟撮合系统的完整 REST API 和 WebSocket 接口。

**基础路径**: `/api/v1`
**数据格式**: JSON
**时间格式**: ISO 8601 (`YYYY-MM-DDTHH:MM:SS.sssZ`)
**价格格式**: Decimal（字符串传输，避免浮点精度问题）
**认证**: Bearer Token（预留）

---

## 1. 通用响应格式

所有 API 响应采用统一格式：

```json
{
  "code": 0,
  "message": "success",
  "data": { ... }
}
```

错误响应：

```json
{
  "code": 1001,
  "message": "参数错误",
  "data": null
}
```

HTTP 状态码：
- `200`: 成功
- `400`: 请求参数错误（资金不足、价格笼子、涨跌停等）
- `404`: 资源不存在
- `500`: 系统内部错误

---

## 2. 委托接口

### 2.1 提交委托

```
POST /api/v1/orders
```

**请求体:**

```json
{
  "symbol": "000001.SZ",
  "side": "buy",
  "price": "10.50",
  "quantity": 1000,
  "order_type": "limit"
}
```

**字段说明:**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `symbol` | string | 是 | 标的代码，如 `000001.SZ` |
| `side` | string | 是 | `buy` 或 `sell` |
| `price` | string | 限价单必填 | 委托价格（Decimal 字符串） |
| `quantity` | int | 是 | 委托数量，必须是 100 的整数倍 |
| `order_type` | string | 否 | `limit`（默认）或 `market` |

**响应示例（立即撮合）:**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "order_id": "ord-20240616-001",
    "symbol": "000001.SZ",
    "side": "buy",
    "price": "10.50",
    "quantity": 1000,
    "filled_qty": 1000,
    "status": "filled",
    "queue_info": null,
    "create_time": "2024-06-16T10:30:00.123Z",
    "update_time": "2024-06-16T10:30:00.123Z"
  }
}
```

**响应示例（进入队列）:**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "order_id": "ord-20240616-002",
    "symbol": "000001.SZ",
    "side": "buy",
    "price": "10.48",
    "quantity": 1000,
    "filled_qty": 0,
    "status": "queued",
    "queue_info": {
      "queue_length": 15,
      "queue_position": 15,
      "enter_queue_time": "2024-06-16T10:30:00.123Z"
    },
    "create_time": "2024-06-16T10:30:00.123Z",
    "update_time": "2024-06-16T10:30:00.123Z"
  }
}
```

**错误响应示例（价格笼子）:**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "order_id": "ord-20240616-003",
    "symbol": "000001.SZ",
    "side": "buy",
    "price": "11.00",
    "quantity": 1000,
    "filled_qty": 0,
    "status": "rejected",
    "reject_reason": "委托价格 11.00 超出价格笼子上限 10.71 (基准价 10.50, 上限 +2%)",
    "create_time": "2024-06-16T10:30:00.123Z",
    "update_time": "2024-06-16T10:30:00.123Z"
  }
}
```

**状态说明:**
- `filled` - 全部成交（立即撮合成功）
- `queued` - 进入队列等待
- `partial` - 部分成交，剩余在队列中
- `rejected` - 被拒绝（参数错误、资金不足、价格笼子、涨跌停等）

### 2.2 撤销委托

```
DELETE /api/v1/orders/{order_id}
```

**响应:**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "order_id": "ord-20240616-002",
    "status": "cancelled",
    "cancel_time": "2024-06-16T10:31:00.456Z"
  }
}
```

### 2.3 查询单笔委托

```
GET /api/v1/orders/{order_id}
```

**响应:**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "order_id": "ord-20240616-002",
    "symbol": "000001.SZ",
    "side": "buy",
    "price": "10.48",
    "quantity": 1000,
    "filled_qty": 500,
    "remaining_qty": 500,
    "status": "partial",
    "queue_info": {
      "queue_length_at_enter": 15,
      "queue_position_at_enter": 15,
      "current_queue_length": 5,
      "current_queue_position": 2,
      "enter_queue_time": "2024-06-16T10:30:00.123Z"
    },
    "trades": [
      {
        "trade_id": "trd-001",
        "price": "10.48",
        "quantity": 500,
        "trade_time": "2024-06-16T10:30:05.456Z"
      }
    ],
    "create_time": "2024-06-16T10:30:00.123Z",
    "update_time": "2024-06-16T10:30:05.456Z"
  }
}
```

### 2.4 查询委托列表

```
GET /api/v1/orders?symbol=000001.SZ&status=queued&side=buy&page=1&page_size=20
```

**响应:**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "total": 100,
    "page": 1,
    "page_size": 20,
    "orders": [
      {
        "order_id": "ord-001",
        "symbol": "000001.SZ",
        "side": "buy",
        "price": "10.50",
        "quantity": 1000,
        "filled_qty": 0,
        "status": "queued",
        "queue_info": {
          "queue_length": 15,
          "queue_position": 15
        },
        "create_time": "2024-06-16T10:30:00.123Z"
      }
    ]
  }
}
```

---

## 3. 成交接口

### 3.1 查询成交记录

```
GET /api/v1/trades?symbol=000001.SZ&start_time=2024-06-16T10:00:00Z&end_time=2024-06-16T11:00:00Z&page=1&page_size=20
```

**响应:**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "total": 50,
    "page": 1,
    "page_size": 20,
    "trades": [
      {
        "trade_id": "trd-001",
        "order_id": "ord-001",
        "symbol": "000001.SZ",
        "side": "buy",
        "price": "10.50",
        "quantity": 500,
        "trade_time": "2024-06-16T10:30:05.456Z",
        "match_source": "trade_event",
        "trigger_trade_id": "td-20240616-xxx"
      }
    ]
  }
}
```

---

## 4. 行情接口

### 4.1 查询订单簿快照

```
GET /api/v1/orderbook/{symbol}?depth=10
```

**响应:**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "symbol": "000001.SZ",
    "timestamp": "2024-06-16T10:30:00.123Z",
    "bids": [
      {
        "price": "10.50",
        "total_quantity": 5000,
        "order_count": 10,
        "queue_length": 10
      },
      {
        "price": "10.49",
        "total_quantity": 3000,
        "order_count": 5,
        "queue_length": 5
      }
    ],
    "asks": [
      {
        "price": "10.51",
        "total_quantity": 4000,
        "order_count": 8,
        "queue_length": 8
      }
    ],
    "best_bid": "10.50",
    "best_ask": "10.51",
    "spread": "0.01"
  }
}
```

### 4.2 查询标的列表

```
GET /api/v1/symbols
```

**响应:**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "symbols": [
      {
        "symbol": "000001.SZ",
        "status": "active",
        "orders_received": 1500,
        "orders_filled": 800,
        "orders_queued": 700
      }
    ]
  }
}
```

### 4.3 查询标的信息

```
GET /api/v1/symbols/{symbol}
```

### 4.4 查询标的统计

```
GET /api/v1/stats/{symbol}
```

**响应:**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "symbol": "000001.SZ",
    "orders_received": 1500,
    "orders_filled": 800,
    "orders_queued": 700,
    "trades_executed": 800,
    "trades_from_cross": 500,
    "trades_from_feed": 300
  }
}
```

### 4.5 查询成交历史

```
GET /api/v1/market/trade_history?symbol=000001.SZ&limit=100
```

### 4.6 查询价格历史

```
GET /api/v1/market/price_history?symbol=000001.SZ&limit=200
```

---

## 5. 市场规则接口

### 5.1 查询市场规则

```
GET /api/v1/market/rules/{symbol}
```

**响应:**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "previous_close": "10.00",
    "market_type": "main_board",
    "price_limit_ratio": "0.10",
    "upper_limit": "11.00",
    "lower_limit": "9.00",
    "price_tick": "0.01",
    "lot_size": 100
  }
}
```

### 5.2 更新市场规则

```
POST /api/v1/market/rules/{symbol}
```

**请求体:**

```json
{
  "previous_close": "10.50",
  "market_type": "main_board"
}
```

**market_type 可选值:**
- `main_board` - 沪深主板（±10%）
- `st_board` - ST 股票（±5%）
- `star_market` - 科创板（±20%）
- `chinext` - 创业板（±20%）
- `bse` - 北交所（±30%）

---

## 6. 行情参与者接口

### 6.1 查询参与者状态

```
GET /api/v1/market/participants?symbol=000001.SZ
```

**响应:**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "participants": [
      {
        "participant_id": "MM-1",
        "type": "MarketMaker",
        "active": true,
        "orders_sent": 500,
        "trades_executed": 200,
        "pending_orders": 50,
        "cash": 950000.00,
        "position": 1000,
        "pnl": 15000.00,
        "total_fees": 500.00,
        "total_trades": 200
      }
    ]
  }
}
```

### 6.2 查询参与者配置

```
GET /api/v1/market/participants/config?symbol=000001.SZ
```

### 6.3 更新参与者配置

```
POST /api/v1/market/participants/config
```

**请求体:**

```json
{
  "symbol": "000001.SZ",
  "target_price": 10.50,
  "order_interval": 0.2,
  "market_maker_count": 2,
  "trend_follower_count": 1,
  "mean_reversion_count": 1,
  "noise_trader_count": 3,
  "aggressive_trader_count": 1,
  "algorithmic_trader_count": 1,
  "stop_loss_trader_count": 1,
  "order_book_imbalance_count": 1,
  "iceberg_participant_count": 1,
  "directional_trader_count": 1,
  "chip_collector_count": 1,
  "day_trader_count": 2
}
```

---

## 7. 市场微观结构接口

### 7.1 查询当前模式

```
GET /api/v1/market/regime/{symbol}
```

**响应:**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "symbol": "000001.SZ",
    "regime": "normal"
  }
}
```

### 7.2 切换模式

```
POST /api/v1/market/regime/{symbol}
```

**请求体:**

```json
{
  "regime": "flash_crash"
}
```

**regime 可选值:**
- `normal` - 正常模式
- `flash_crash` - 闪崩模式（注入大额卖单）
- `pump` - 拉升模式（注入大额买单）

---

## 8. 延迟注入接口

### 8.1 查询延迟配置

```
GET /api/v1/latency
```

**响应:**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "default_ms": 0,
    "internal_ms": 0,
    "external_ms": 0
  }
}
```

### 8.2 更新延迟配置

```
POST /api/v1/latency
```

**请求体:**

```json
{
  "default_ms": 0,
  "internal_ms": 5,
  "external_ms": 10
}
```

---

## 9. 账户接口

### 9.1 查询账户快照

```
GET /api/v1/account
```

**响应:**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "account_id": "user-001",
    "cash": 500000.00,
    "frozen_cash": 10000.00,
    "available_position": 5000,
    "frozen_position": 1000,
    "today_bought_position": 2000,
    "total_fees": 500.00,
    "trade_count": 50,
    "initial_cash": 1000000.00,
    "initial_position": 0
  }
}
```

### 9.2 日终结算

```
POST /api/v1/account/settle
```

将 `today_bought_position` 转入 `available_position`（T+1 结算）。

### 9.3 重置账户

```
POST /api/v1/account/reset
```

**请求体:**

```json
{
  "initial_cash": "1000000.00",
  "initial_position": 0
}
```

---

## 10. 排行榜接口

### 10.1 查询排行榜

```
GET /api/v1/leaderboard/{symbol}
```

**响应:**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "symbol": "000001.SZ",
    "timestamp": "2024-06-16T10:30:00.123Z",
    "participants": [
      {
        "participant_id": "MM-1",
        "type": "MarketMaker",
        "pnl": 15000.00,
        "win_rate": 0.65,
        "max_drawdown": 2000.00,
        "sharpe_ratio": 1.2,
        "total_trades": 200
      }
    ]
  }
}
```

---

## 11. 分析接口

### 11.1 订单流分析

```
GET /api/v1/analytics/order_flow/{symbol}
```

**响应:**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "symbol": "000001.SZ",
    "bid_depth": 50000,
    "ask_depth": 30000,
    "total_depth": 80000,
    "imbalance": 0.25,
    "spread": "0.01",
    "best_bid": "10.50",
    "best_ask": "10.51",
    "bid_levels": 10,
    "ask_levels": 10
  }
}
```

### 11.2 参与者 P&L 排名

```
GET /api/v1/analytics/participants/pnl?symbol=000001.SZ
```

**响应:**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "participants": [
      {
        "participant_id": "MM-1",
        "type": "MarketMaker",
        "active": true,
        "cash": 950000.00,
        "position": 1000,
        "pnl": 15000.00,
        "total_trades": 200,
        "total_fees": 500.00
      }
    ],
    "count": 12
  }
}
```

### 11.3 深度图数据

```
GET /api/v1/analytics/depth/{symbol}
```

**响应:**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "symbol": "000001.SZ",
    "bid_depths": [
      {"price": "10.50", "quantity": 5000, "cumulative": 5000},
      {"price": "10.49", "quantity": 3000, "cumulative": 8000}
    ],
    "ask_depths": [
      {"price": "10.51", "quantity": 4000, "cumulative": 4000},
      {"price": "10.52", "quantity": 2000, "cumulative": 6000}
    ],
    "best_bid": "10.50",
    "best_ask": "10.51",
    "spread": "0.01"
  }
}
```

---

## 12. 持久化接口

### 12.1 CSV 导出

```
POST /api/v1/persistence/export
```

**请求体:**

```json
{
  "type": "trades",
  "symbol": "000001.SZ"
}
```

**type 可选值:** `orders`, `trades`, `snapshots`, `settlements`

### 12.2 查询最新快照

```
GET /api/v1/persistence/snapshot?symbol=000001.SZ
```

---

## 13. WebSocket 实时接口

### 13.1 连接

```
WS /ws/v1
```

### 13.2 订阅行情

**客户端发送:**

```json
{
  "action": "subscribe",
  "channel": "market",
  "symbols": ["000001.SZ"]
}
```

**服务端推送 - 盘口快照:**

```json
{
  "type": "quote",
  "symbol": "000001.SZ",
  "timestamp": "2024-06-16T10:30:00.123Z",
  "bids": [
    {"price": "10.50", "total_quantity": 5000, "order_count": 10}
  ],
  "asks": [
    {"price": "10.51", "total_quantity": 4000, "order_count": 8}
  ]
}
```

**服务端推送 - 逐笔成交:**

```json
{
  "type": "trade",
  "symbol": "000001.SZ",
  "timestamp": "2024-06-16T10:30:00.123Z",
  "price": "10.50",
  "quantity": 500,
  "side": "buy",
  "match_source": "trade_event"
}
```

### 13.3 订阅订单状态

**客户端发送:**

```json
{
  "action": "subscribe",
  "channel": "orders",
  "order_ids": ["ord-001"]
}
```

**服务端推送:**

```json
{
  "type": "order_status",
  "order_id": "ord-001",
  "timestamp": "2024-06-16T10:30:05.456Z",
  "status": "partial",
  "filled_qty": 500,
  "remaining_qty": 500,
  "queue_position": 2,
  "trade": {
    "trade_id": "trd-001",
    "price": "10.50",
    "quantity": 500
  }
}
```

### 13.4 价格历史推送

```json
{
  "type": "price_history",
  "symbol": "000001.SZ",
  "data": [
    {"price": "10.50", "timestamp": "2024-06-16T10:30:00.123Z"}
  ]
}
```

### 13.5 排行榜推送

```json
{
  "type": "leaderboard",
  "symbol": "000001.SZ",
  "timestamp": "2024-06-16T10:30:00.123Z",
  "participants": [
    {
      "participant_id": "MM-1",
      "pnl": 15000.00,
      "win_rate": 0.65,
      "max_drawdown": 2000.00,
      "sharpe_ratio": 1.2
    }
  ]
}
```

---

## 14. 错误码

| 错误码 | 说明 |
|---|---|
| 0 | 成功 |
| 1001 | 参数错误 |
| 1002 | 订单不存在 |
| 1003 | 订单状态不允许该操作 |
| 1004 | 标的未激活 |
| 2001 | 系统内部错误 |
| 2002 | 撮合引擎繁忙 |