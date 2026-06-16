# API 接口规范

## 1. 接口概述

- 基础路径: `/api/v1`
- 数据格式: JSON
- 时间格式: ISO 8601 (`YYYY-MM-DDTHH:MM:SS.sssZ`)
- 价格格式: Decimal（字符串传输，避免浮点精度问题）
- 认证: Bearer Token（预留）

## 2. 委托接口

### 2.1 提交委托

```
POST /api/v1/orders
```

请求体:
```json
{
  "symbol": "000001.SZ",
  "side": "buy",
  "price": "10.50",
  "quantity": 1000,
  "order_type": "limit"
}
```

响应:
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

状态说明:
- `filled` - 全部成交（立即撮合成功）
- `queued` - 进入队列等待
- `partial` - 部分成交，剩余在队列中

### 2.2 撤销委托

```
DELETE /api/v1/orders/{order_id}
```

响应:
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "order_id": "ord-20240616-001",
    "status": "cancelled",
    "cancel_time": "2024-06-16T10:31:00.456Z"
  }
}
```

### 2.3 查询单笔委托

```
GET /api/v1/orders/{order_id}
```

响应:
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
        "price": "10.50",
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

响应:
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

## 3. 成交接口

### 3.1 查询成交记录

```
GET /api/v1/trades?symbol=000001.SZ&start_time=2024-06-16T10:00:00Z&end_time=2024-06-16T11:00:00Z&page=1&page_size=20
```

响应:
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
        "match_source": "trade_event",  // trade_event / order_cross
        "trigger_trade_id": "td-20240616-xxx"  // 触发的逐笔成交ID
      }
    ]
  }
}
```

## 4. 行情接口

### 4.1 查询订单簿快照

```
GET /api/v1/orderbook/{symbol}
```

响应:
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
    ]
  }
}
```

### 4.2 查询标的列表

```
GET /api/v1/symbols
```

响应:
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "symbols": [
      {
        "symbol": "000001.SZ",
        "name": "平安银行",
        "price_tick": "0.01",
        "lot_size": 100,
        "status": "active"
      }
    ]
  }
}
```

## 5. WebSocket 实时接口

### 5.1 连接

```
WS /ws/v1
```

### 5.2 订阅行情

客户端发送:
```json
{
  "action": "subscribe",
  "channel": "market",
  "symbols": ["000001.SZ"]
}
```

服务端推送:
```json
{
  "type": "quote",
  "symbol": "000001.SZ",
  "timestamp": "2024-06-16T10:30:00.123Z",
  "bids": [["10.50", 5000], ["10.49", 3000]],
  "asks": [["10.51", 4000], ["10.52", 2000]]
}
```

```json
{
  "type": "trade",
  "symbol": "000001.SZ",
  "timestamp": "2024-06-16T10:30:00.123Z",
  "price": "10.50",
  "quantity": 500,
  "direction": "buy"
}
```

### 5.3 订阅订单状态

客户端发送:
```json
{
  "action": "subscribe",
  "channel": "orders",
  "order_ids": ["ord-001"]
}
```

服务端推送:
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

## 6. 错误码

| 错误码 | 说明 |
|---|---|
| 0 | 成功 |
| 1001 | 参数错误 |
| 1002 | 订单不存在 |
| 1003 | 订单状态不允许该操作 |
| 1004 | 标的未激活 |
| 2001 | 系统内部错误 |
| 2002 | 撮合引擎繁忙 |
