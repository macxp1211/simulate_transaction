# 高精度队列模拟撮合系统 - 架构设计文档

## 1. 系统概述

本系统是一个基于 **Level-2 逐笔成交和盘口行情** 的高精度队列模拟撮合系统。核心特性包括：

- **逐笔驱动**：基于真实 Level-2 逐笔成交数据驱动撮合逻辑
- **价格优先**：撮合严格遵循价格优先、时间优先原则
- **队列模拟**：非最优价委托进入队列排队，记录队列长度
- **队列消耗**：当市场成交价格优于或等于队列委托价格时，按 FIFO 顺序消耗队列
- **实时撮合**：支持外部 API 委托接入，实时响应行情变化

## 2. 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                    外部客户端 / 策略系统                      │
│              (REST API / WebSocket / gRPC)                  │
└──────────────────┬──────────────────────────────────────────┘
                   │ 委托下单 / 撤单 / 查询
┌──────────────────▼──────────────────────────────────────────┐
│                    API Gateway (FastAPI)                      │
│              • 委托接收与校验                                 │
│              • 订单查询与状态跟踪                              │
│              • 实时推送 (WebSocket)                           │
└──────────────────┬──────────────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────────────────┐
│                    撮合核心引擎 (Matching Engine)              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐    │
│  │  订单簿       │  │  队列管理器   │  │  撮合执行器       │    │
│  │  OrderBook   │  │ QueueManager │  │ MatchingExecutor │    │
│  │              │  │              │  │                  │    │
│  │ • Bid队列    │  │ • 新委托入队  │  │ • 价格交叉检测    │    │
│  │ • Ask队列    │  │ • 队列长度记录│  │ • 成交量计算      │    │
│  │ • 价格索引   │  │ • 行情触发消耗│  │ • 成交记录生成    │    │
│  └──────────────┘  └──────────────┘  └──────────────────┘    │
└──────────────────┬──────────────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────────────────┐
│                    Level-2 行情接入层                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐    │
│  │  逐笔成交    │  │  盘口快照    │  │  行情解析器       │    │
│  │  Trade Feed  │  │  Quote Feed  │  │   Parser         │    │
│  │              │  │              │  │                  │    │
│  │ • 成交价格   │  │ • 买卖十档   │  │ • 数据标准化      │    │
│  │ • 成交数量   │  │ • 队列长度   │  │ • 时间戳对齐      │    │
│  │ • 成交方向   │  │ • 委托总量   │  │ • 事件分发        │    │
│  └──────────────┘  └──────────────┘  └──────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

## 3. 核心模块设计

### 3.1 订单模型 (Order)

```python
class Order:
    order_id: str          # 唯一订单ID
    symbol: str            # 标的代码
    side: Side             # 买卖方向: BUY / SELL
    price: Decimal         # 委托价格
    quantity: int          # 委托数量
    filled_qty: int        # 已成交数量
    status: OrderStatus    # 订单状态
    
    # 队列相关字段
    queue_position: int     # 进入队列时的位置（前面有多少订单）
    queue_length: int       # 进入队列时的总队列长度
    enter_queue_time: datetime  # 进入队列时间
    leave_queue_time: datetime  # 离开队列时间（成交或撤单）
    
    create_time: datetime   # 订单创建时间
    update_time: datetime   # 最后更新时间
```

### 3.2 订单簿 (OrderBook)

订单簿采用 **价格层级 + 时间队列** 的双层结构：

```
OrderBook
├── bids: SortedDict[price -> List[Order]]   # 买队列，按价格降序
├── asks: SortedDict[price -> List[Order]]   # 卖队列，按价格升序
├── price_index: Dict[order_id -> price]     # 订单价格索引，用于O(1)定位
└── queue_counter: Dict[price -> int]        # 各价格层级队列计数器
```

**关键操作复杂度**：
- 插入订单: O(log N) 价格定位 + O(1) 队列追加
- 删除订单: O(1) 通过 price_index 定位
- 最优价格查询: O(1) 通过 sorted dict 的 first/last
- 队列长度查询: O(1) 通过 queue_counter

### 3.3 撮合引擎 (MatchingEngine)

撮合引擎是系统的核心，处理两类事件：

**A. 委托事件 (OrderEvent)**

```
买入委托 (价格 = P, 数量 = Q):
    1. 查询最优卖价 ask1
    2. 如果 P >= ask1:
         → 立即撮合：按 ask 队列 FIFO 顺序成交，直到 Q == 0 或 P < ask_price
         → 记录成交明细
    3. 如果 P < ask1:
         → 进入买方队列对应价格层级
         → 记录当前该价格层级的 queue_length（队列总长度）
         → 记录 queue_position（该订单进入时的位置）
         → 订单状态变为 QUEUED

卖出委托 (价格 = P, 数量 = Q):
    1. 查询最优买价 bid1
    2. 如果 P <= bid1:
         → 立即撮合：按 bid 队列 FIFO 顺序成交，直到 Q == 0 或 P > bid_price
         → 记录成交明细
    3. 如果 P > bid1:
         → 进入卖方队列对应价格层级
         → 记录 queue_length 和 queue_position
         → 订单状态变为 QUEUED
```

**B. 逐笔成交事件 (TradeEvent)**

当收到新的逐笔成交数据时，引擎检查队列中的订单：

```
逐笔成交 (价格 = T, 数量 = TQ, 方向 = D):
    对于买方队列（被动消耗）:
        从最优买价开始，向下遍历各价格层级:
            如果该笔逐笔成交是卖方主动成交（外盘/内盘判断）:
                从该价格层级的队首开始消耗订单:
                    - 每消耗一个订单，减少 TQ
                    - 当 TQ == 0 时停止
                    - 被消耗的订单状态变为 FILLED（全部成交）或 PARTIAL（部分成交）
                
    对于卖方队列:
        从最优卖价开始，向上遍历各价格层级:
            如果该笔逐笔成交是买方主动成交:
                从该价格层级的队首开始消耗订单
                ...
```

### 3.4 队列消耗算法

这是系统的核心算法，决定了模拟撮合的精度：

```python
def consume_queue_on_trade(self, trade: TradeEvent):
    """
    当逐笔成交发生时，根据成交价格消耗队列中的订单。
    
    核心逻辑:
    1. 逐笔成交价格代表了该时刻市场的真实成交价
    2. 所有队列中价格优于或等于该成交价的订单，理论上应该被成交
    3. 按价格层级 + FIFO 顺序消耗
    """
    if trade.is_buy_initiated():  # 买方主动成交（外盘）
        # 从卖方队列中，价格 <= trade.price 的订单应被消耗
        for price_level in self.asks.iter_prices(max_price=trade.price):
            remaining = trade.quantity
            for order in price_level.orders:
                if remaining <= 0:
                    break
                fill_qty = min(order.remaining_qty, remaining)
                self.execute_partial(order, fill_qty, trade)
                remaining -= fill_qty
                
    elif trade.is_sell_initiated():  # 卖方主动成交（内盘）
        # 从买方队列中，价格 >= trade.price 的订单应被消耗
        for price_level in self.bids.iter_prices(min_price=trade.price):
            remaining = trade.quantity
            for order in price_level.orders:
                if remaining <= 0:
                    break
                fill_qty = min(order.remaining_qty, remaining)
                self.execute_partial(order, fill_qty, trade)
                remaining -= fill_qty
```

### 3.5 行情接入设计

```python
class Level2FeedHandler:
    """Level-2 行情处理器"""
    
    async def on_trade(self, trade_data: dict):
        """逐笔成交回调"""
        trade = TradeEvent.from_raw(trade_data)
        # 驱动撮合引擎
        await self.engine.process_trade(trade)
        
    async def on_quote(self, quote_data: dict):
        """盘口快照回调"""
        quote = QuoteEvent.from_raw(quote_data)
        # 更新订单簿的参考价格
        self.engine.order_book.update_reference_prices(quote)
```

## 4. 数据流设计

```
Level-2 行情源
     │
     ├── 逐笔成交 ──→ TradeEvent ──→ MatchingEngine.consume_queue()
     │                                    │
     │                                    ├── 更新订单簿
     │                                    ├── 生成成交记录
     │                                    └── 推送状态变更
     │
     └── 盘口快照 ──→ QuoteEvent ──→ OrderBook.update_reference()
                                          │
                                          └── 更新最优价格、队列参考

外部委托
     │
     ├── 新委托 ──→ OrderEvent ──→ MatchingEngine.place_order()
     │                                    │
     │                                    ├── 价格交叉检测
     │                                    ├── 立即撮合 / 入队
     │                                    └── 返回订单状态
     │
     ├── 撤单 ──→ CancelEvent ──→ MatchingEngine.cancel_order()
     │                                    │
     │                                    └── 从队列移除
     │
     └── 查询 ──→ QueryEvent ──→ OrderBook.get_status()
```

## 5. 状态机设计

订单状态流转：

```
                    ┌──────────┐
                    │  PENDING │  (创建中)
                    └────┬─────┘
                         │ 校验通过
                    ┌────▼─────┐
    ┌───────────────│  ACTIVE  │  (已激活)
    │               └────┬─────┘
    │                    │ 价格不交叉
    │               ┌────▼─────┐
    │    ┌─────────│  QUEUED  │  (排队中)
    │    │ 撤单     └────┬─────┘
    │    │          ┌────┴────┐
    │    │          │ 行情触发 │
    │    │          └────┬────┘
    │    │               │ 价格交叉
    │    │          ┌────▼─────┐
    │    │          │  MATCHING│  (撮合中)
    │    │          └────┬─────┘
    │    │               │
    │    │    ┌─────────┴─────────┐
    │    │    │                   │
    │    │    │ 部分成交           │ 全部成交
    │    │    │                   │
    │    │    ▼                   ▼
    │    │ ┌──────────┐     ┌──────────┐
    │    │ │  PARTIAL │     │  FILLED  │
    │    │ └──────────┘     └──────────┘
    │    │
    │    ▼
    │ ┌──────────┐
    └─│ CANCELLED│
      └──────────┘
```

## 6. 并发设计

### 6.1 单标的串行处理

为保证撮合顺序性，每个标的 (symbol) 的撮合逻辑在独立的 asyncio 任务中串行执行：

```python
class SymbolMatchingLoop:
    """单标的撮合循环"""
    
    async def run(self):
        while self.running:
            event = await self.event_queue.get()
            # 串行处理：委托事件、逐笔成交事件、撤单事件
            await self.process_event(event)
```

### 6.2 跨标的并行处理

不同标的之间完全独立，可以并行处理：

```python
class EngineManager:
    """管理多个标的的撮合引擎"""
    
    def __init__(self):
        self.engines: Dict[str, SymbolMatchingLoop] = {}
    
    async def route_event(self, symbol: str, event: Event):
        """将事件路由到对应标的的引擎"""
        if symbol not in self.engines:
            self.engines[symbol] = SymbolMatchingLoop(symbol)
        await self.engines[symbol].event_queue.put(event)
```

## 7. API 设计

### 7.1 REST API

```
POST /api/v1/orders
    Body: {"symbol": "000001.SZ", "side": "buy", "price": 10.50, "quantity": 1000}
    Response: {"order_id": "xxx", "status": "queued", "queue_length": 15, "queue_position": 15}

DELETE /api/v1/orders/{order_id}
    Response: {"order_id": "xxx", "status": "cancelled"}

GET /api/v1/orders/{order_id}
    Response: 订单详情

GET /api/v1/orders
    Query: symbol, status, side
    Response: 订单列表

GET /api/v1/trades
    Query: symbol, start_time, end_time
    Response: 成交记录
```

### 7.2 WebSocket 实时推送

```
WS /ws/v1/market
    → 订阅: {"action": "subscribe", "channel": "trades", "symbol": "000001.SZ"}
    ← 推送: {"type": "trade", "symbol": "000001.SZ", "price": 10.50, "quantity": 500, ...}
    
WS /ws/v1/orders
    → 订阅: {"action": "subscribe", "channel": "order_status", "order_id": "xxx"}
    ← 推送: {"type": "status_change", "order_id": "xxx", "status": "filled", ...}
```

## 8. 配置设计

```yaml
engine:
  symbols:
    - "000001.SZ"
    - "600519.SH"
  
  level2_feed:
    source: "mock"  # mock / tencent / sina / custom
    trade_topic: "trade"
    quote_topic: "quote"
  
  matching:
    price_tick: 0.01          # 最小价格变动单位
    lot_size: 100             # 最小交易单位
    max_queue_depth: 10000    # 最大队列深度
  
  api:
    host: "0.0.0.0"
    port: 8000
    
  logging:
    level: "INFO"
    format: "json"
```

## 9. 性能指标

| 指标 | 目标 | 说明 |
|---|---|---|
| 委托处理延迟 | < 1ms | 从接收到入队/撮合 |
| 逐笔响应延迟 | < 1ms | 逐笔成交到队列消耗 |
| 单标吞吐量 | > 10000 笔/秒 | 逐笔成交处理 |
| 并发标的数 | > 1000 | 同时监控的标的数 |
| 内存占用 | < 1GB/1000标的 | 订单簿内存占用 |

## 10. 扩展性设计

- **新标的接入**：动态创建 SymbolMatchingLoop，无需重启
- **新行情源**：实现 Level2FeedHandler 接口即可接入
- **持久化**：可选 Redis 后端，支持重启恢复订单簿状态
- **集群扩展**：通过消息队列（Kafka/RabbitMQ）分发行情到多个撮合节点
