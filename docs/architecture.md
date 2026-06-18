# 系统架构设计文档

> 本文档描述高精度队列模拟撮合系统的整体架构、核心模块关系、数据流与并发模型。

---

## 1. 系统概述

本系统是一个基于 **Level-2 逐笔成交与盘口行情** 的高精度队列模拟撮合系统，核心设计目标：

1. **逐笔级精度**：每个逐笔成交事件触发一次队列扫描，精确模拟 FIFO 消耗
2. **价格优先 + 时间优先**：严格遵循交易所撮合规则
3. **队列透明化**：记录进入位置、队列长度、等待时间，支持策略分析
4. **A 股真实规则**：涨跌停、价格笼子、最小变动、T+1 冻结、费用模型
5. **智能行情模拟**：12 类参与者独立策略、虚拟账户、共享市场状态
6. **实时可观测**：REST API + WebSocket 实时推送，前端可视化监控

---

## 2. 整体架构

```mermaid
flowchart TB
    subgraph Client["外部接入层"]
        direction TB
        WebUI["Web 前端\n(委托终端 / 监控面板)"]
        AlgoSys["算法交易系统\n(REST API 接入)"]
        RLAgent["RL 训练 Agent\n(WebSocket 订阅)"]
    end

    subgraph Gateway["API Gateway"]
        direction TB
        FastAPI["FastAPI 应用"]
        REST["REST Router\n(委托 / 查询 / 配置)"]
        WSManager["WebSocket Manager\n(订阅管理 / 广播)"]
    end

    subgraph Core["撮合核心层"]
        direction TB
        EngineMgr["MatchingEngineManager\n(多标的管理)"]
        SME["SymbolMatchingEngine\n(单标的异步事件循环)"]
        OB["OrderBook\n(SortedDict + PriceLevel)"]
        AC["Account\n(T+1 / 冻结 / 费用)"]
        LI["LatencyInjector\n(延迟注入)"]
        Rules["MarketRules\n(涨跌停 / 价格笼子)"]
    end

    subgraph Market["行情模拟层"]
        direction TB
        Feed["MockLevel2Feed\n(异步 tick 生成)"]
        Registry["ParticipantRegistry\n(12 类策略工厂)"]
        Participants["12 类 MarketParticipant\n(独立虚拟账户 + 策略)"]
        SharedState["SharedMarketState\n(EWMA 波动率 / 订单流不平衡)"]
        Regime["MarketRegime\n(冲击订单注入)"]
    end

    subgraph Persist["持久化层"]
        direction TB
        SQLite[(SQLite DB)]
        Leaderboard["Leaderboard\n(10s 周期排名计算)"]
    end

    Client -->|HTTP / WS| FastAPI
    FastAPI --> REST
    FastAPI --> WSManager
    REST -->|路由订单| EngineMgr
    EngineMgr -->|创建 / 路由| SME
    SME -->|校验| Rules
    SME -->|风控| AC
    SME -->|撮合| OB
    SME -->|延迟| LI
    Feed -->|模拟委托| REST
    Feed -->|更新| SharedState
    Participants -->|策略信号| Feed
    Registry -->|管理| Participants
    Regime -->|冲击订单| Feed
    SME -->|持久化| SQLite
    SME -->|排名数据| Leaderboard
    Leaderboard -->|广播| WSManager
    WSManager -->|实时推送| Client

    style Client fill:#f0f9ff,stroke:#bae6fd
    style Gateway fill:#f0fdf4,stroke:#86efac
    style Core fill:#fefce8,stroke:#fde047
    style Market fill:#faf5ff,stroke:#d8b4fe
    style Persist fill:#fff1f2,stroke:#fda4af
```

---

## 3. 核心模块类图

### 3.1 订单与成交模型

```mermaid
classDiagram
    class Side {
        <<enumeration>>
        BUY
        SELL
    }

    class OrderStatus {
        <<enumeration>>
        PENDING
        ACTIVE
        QUEUED
        PARTIAL
        FILLED
        CANCELLED
        REJECTED
    }

    class OrderType {
        <<enumeration>>
        LIMIT
        MARKET
    }

    class Order {
        +str order_id
        +str symbol
        +Side side
        +Decimal price
        +int quantity
        +int filled_qty
        +OrderStatus status
        +OrderType order_type
        +QueueInfo queue_info
        +List~TradeRecord~ trades
        +datetime create_time
        +datetime update_time
        +str reject_reason
        +str source
        +str participant_id
        +bool is_mock
        +bool is_active
        +int remaining_qty
        +cancel()
        +fill(quantity, price)
        +partial_fill(quantity, price)
        +to_dict()
    }

    class QueueInfo {
        +int queue_length
        +int queue_position
        +datetime enter_queue_time
        +datetime leave_queue_time
        +int current_queue_length
        +int current_queue_position
        +to_dict()
    }

    class TradeRecord {
        +str trade_id
        +str order_id
        +str symbol
        +Side side
        +Decimal price
        +int quantity
        +datetime trade_time
        +str match_source
        +str trigger_trade_id
        +to_dict()
    }

    Order --> Side
    Order --> OrderStatus
    Order --> OrderType
    Order --> QueueInfo
    Order --> TradeRecord
```

### 3.2 订单簿

```mermaid
classDiagram
    class OrderBook {
        +str symbol
        +SortedDict bids
        +SortedDict asks
        +Dict price_index
        +Dict queue_counter
        +List _all_orders
        +place_order(Order) Tuple~List~TradeRecord~~, Order~
        +cancel_order(str) Order
        +get_orderbook_snapshot(int) Dict
        +get_best_bid() Decimal
        +get_best_ask() Decimal
        +consume_queue_on_trade(Dict) List~Order~
        +partial_cancel(Decimal, int, str) List~Order~
    }

    class PriceLevel {
        +Decimal price
        +List orders
        +int total_quantity
        +append(Order)
        +remove(str) Order
        +partial_cancel(int, str) List~Order~
        +get_snapshot() Dict
    }

    class SimpleSortedDict {
        +Dict _data
        +List _keys
        +__setitem__(key, value)
        +__getitem__(key)
        +__delitem__(key)
        +keys()
        +first_key()
        +last_key()
    }

    OrderBook --> PriceLevel
    OrderBook --> SimpleSortedDict
```

### 3.3 撮合引擎

```mermaid
classDiagram
    class MatchingEngineManager {
        +Dict engines
        +Account account
        +FeeCalculator fee_calculator
        +List _trade_callbacks
        +get_or_create_engine(str) SymbolMatchingEngine
        +place_order(Order) Order
        +cancel_order(str, str) Order
        +get_order(str, str) Order
        +get_all_orders() List~Order~
        +get_all_engines() Dict
        +on_trade_generated(callback)
        +process_cancel_feed(str, Dict)
    }

    class SymbolMatchingEngine {
        +str symbol
        +MatchingConfig config
        +OrderBook order_book
        +Account account
        +FeeCalculator fee_calculator
        +asyncio.Queue event_queue
        +asyncio.Task task
        +List _trade_callbacks
        +bool running
        +start()
        +stop()
        +place_order(Order) Order
        +cancel_order(str) Order
        +process_cancel_feed(Dict)
        +get_orderbook_snapshot(int) Dict
        +get_stats() Dict
    }

    class MatchingConfig {
        +Decimal price_tick
        +int lot_size
        +int max_queue_depth
        +bool enable_queue_simulation
        +Decimal price_limit_up
        +Decimal price_limit_down
        +MarketType market_type
        +Decimal previous_close
    }

    class MarketType {
        <<enumeration>>
        MAIN_BOARD
        ST_BOARD
        STAR_MARKET
        CHINEXT
        BSE
    }

    MatchingEngineManager --> SymbolMatchingEngine
    SymbolMatchingEngine --> OrderBook
    SymbolMatchingEngine --> MatchingConfig
    MatchingConfig --> MarketType
```

### 3.4 账户与费用

```mermaid
classDiagram
    class Account {
        +str account_id
        +Decimal cash
        +Decimal frozen_cash
        +int available_position
        +int frozen_position
        +int today_bought_position
        +Decimal total_fees
        +int trade_count
        +Decimal initial_cash
        +int initial_position
        +can_buy(Decimal, int) bool
        +can_sell(int) bool
        +freeze_buy(Decimal, int)
        +freeze_sell(int)
        +settle_buy(int, Decimal, Decimal)
        +settle_sell(int, Decimal, Decimal)
        +unfreeze_buy(int, Decimal)
        +unfreeze_sell(int)
        +settle_day_end()
        +get_snapshot() Dict
    }

    class FeeCalculator {
        <<abstract>>
        +calculate_buy_fee(Decimal, int) Decimal
        +calculate_sell_fee(Decimal, int) Decimal
    }

    class AShareFeeCalculator {
        +Decimal commission_rate
        +Decimal min_commission
        +Decimal stamp_tax_rate
        +Decimal transfer_fee_rate
        +Decimal transfer_fee_min
        +calculate_buy_fee(Decimal, int) Decimal
        +calculate_sell_fee(Decimal, int) Decimal
    }

    FeeCalculator <|-- AShareFeeCalculator
```

### 3.5 市场规则

```mermaid
classDiagram
    class MarketRules {
        +Decimal previous_close
        +MarketType market_type
        +Decimal price_tick
        +int lot_size
        +Decimal price_limit_ratio
        +Decimal upper_limit
        +Decimal lower_limit
        +Decimal price_cage_upper
        +Decimal price_cage_lower
        +validate_price(Decimal, Decimal) Tuple~bool, str~
        +validate_quantity(int) Tuple~bool, str~
        +validate_order(Decimal, int, Decimal) Tuple~bool, str~
        +clamp_to_limit(Decimal) Decimal
        +get_price_cage_bounds(Decimal) Tuple~Decimal, Decimal~
        +to_dict() Dict
        +update_previous_close(Decimal)
    }

    class MarketType {
        <<enumeration>>
        MAIN_BOARD
        ST_BOARD
        STAR_MARKET
        CHINEXT
        BSE
    }

    MarketRules --> MarketType
```

### 3.6 行情参与者

```mermaid
classDiagram
    class MarketParticipant {
        <<abstract>>
        +str participant_id
        +str symbol
        +Decimal base_price
        +Decimal target_price
        +float order_interval
        +bool active
        +Decimal cash
        +int position
        +Decimal total_fees
        +int total_trades
        +List _pending_orders
        +generate_order(Dict) Dict
        +generate_cancel(Dict) Dict
        +on_order_filled(str, Dict)
        +on_order_queued(Dict)
        +get_stats() Dict
    }

    class MarketMaker {
        +Decimal base_spread
        +Decimal inventory_skew
        +int max_inventory
        +generate_order(Dict) Dict
    }

    class TrendFollower {
        +int window_size
        +Decimal momentum_threshold
        +generate_order(Dict) Dict
    }

    class MeanReversionTrader {
        +int ma_window
        +Decimal deviation_threshold
        +generate_order(Dict) Dict
    }

    class NoiseTrader {
        +float irrational_prob
        +float cancel_prob
        +generate_order(Dict) Dict
    }

    class AggressiveTrader {
        +float burst_prob
        +int min_depth
        +int cooldown
        +generate_order(Dict) Dict
    }

    class AlgorithmicTrader {
        +str algo_type
        +int slice_count
        +generate_order(Dict) Dict
    }

    class StopLossTrader {
        +Decimal stop_loss_pct
        +Decimal take_profit_pct
        +generate_order(Dict) Dict
    }

    class OrderBookImbalanceTrader {
        +float imbalance_threshold
        +generate_order(Dict) Dict
    }

    class IcebergParticipant {
        +float visible_ratio
        +generate_order(Dict) Dict
    }

    class DirectionalTrader {
        +Decimal urgency
        +int max_position
        +generate_order(Dict) Dict
    }

    class ChipCollector {
        +int target_position
        +generate_order(Dict) Dict
    }

    class DayTrader {
        +int max_holding_ticks
        +Decimal stop_loss_pct
        +Decimal profit_target_pct
        +generate_order(Dict) Dict
    }

    class ParticipantRegistry {
        +Dict _participants
        +Dict _config
        +build_default_participants()
        +update_config(Dict)
        +get_all_stats() List~Dict~
    }

    class SharedMarketState {
        +List last_trades
        +List price_history
        +Decimal volatility_ewma
        +float order_flow_imbalance
        +on_trade(Dict)
        +on_book_update(Dict)
    }

    MarketParticipant <|-- MarketMaker
    MarketParticipant <|-- TrendFollower
    MarketParticipant <|-- MeanReversionTrader
    MarketParticipant <|-- NoiseTrader
    MarketParticipant <|-- AggressiveTrader
    MarketParticipant <|-- AlgorithmicTrader
    MarketParticipant <|-- StopLossTrader
    MarketParticipant <|-- OrderBookImbalanceTrader
    MarketParticipant <|-- IcebergParticipant
    MarketParticipant <|-- DirectionalTrader
    MarketParticipant <|-- ChipCollector
    MarketParticipant <|-- DayTrader
    ParticipantRegistry --> MarketParticipant
    MarketParticipant --> SharedMarketState
```

---

## 4. 数据流设计

### 4.1 委托下单全流程

```mermaid
sequenceDiagram
    autonumber
    participant Client as 客户端
    participant API as FastAPI
    participant Rules as MarketRules
    participant AC as Account
    participant Engine as SymbolMatchingEngine
    participant OB as OrderBook
    participant WS as WebSocket
    participant DB as SQLite

    Client->>API: POST /api/v1/orders
    API->>Rules: validate_order(price, qty, benchmark)
    alt 校验失败
        Rules-->>API: (False, "价格超出价格笼子上限...")
        API-->>Client: 400, reject_reason
    else 校验通过
        Rules-->>API: (True, "")
        API->>AC: can_buy(price, qty) / can_sell(qty)
        alt 风控失败
            AC-->>API: False
            API-->>Client: 400, "资金不足" / "仓位不足"
        else 风控通过
            AC-->>API: True
            API->>Engine: place_order(Order)
            Engine->>OB: place_order(Order)
            alt 价格交叉
                OB-->>Engine: trades, Order(FILLED/PARTIAL)
                Engine->>AC: settle_buy / settle_sell
                Engine->>DB: save_trade / save_order
                Engine->>WS: broadcast_trade
            else 价格不交叉
                OB-->>Engine: [], Order(QUEUED)
                Engine->>AC: freeze_buy / freeze_sell
            end
            Engine-->>API: Order
            API-->>Client: 200, order_id + status
        end
    end
```

### 4.2 逐笔成交驱动队列消耗

```mermaid
sequenceDiagram
    autonumber
    participant Feed as MockLevel2Feed
    participant Engine as SymbolMatchingEngine
    participant OB as OrderBook
    participant AC as Account
    participant WS as WebSocket
    participant DB as SQLite

    Feed->>Engine: process_trade(TradeEvent)
    Engine->>OB: consume_queue_on_trade(trade)
    OB->>OB: 遍历价格层级
    loop 价格 <= 成交价（卖方队列）
        OB->>OB: 按 FIFO 消耗订单
        OB->>AC: settle_buy / settle_sell（被动方）
        OB->>WS: broadcast_trade
        OB->>DB: save_trade
    end
    OB-->>Engine: 被消耗订单列表
    Engine->>Engine: 更新 queue_info
```

### 4.3 行情生成与参与者交互

```mermaid
sequenceDiagram
    autonumber
    participant Feed as MockLevel2Feed
    participant Registry as ParticipantRegistry
    participant P as MarketParticipant
    participant Shared as SharedMarketState
    participant Engine as SymbolMatchingEngine
    participant OB as OrderBook

    loop 每 tick
        Feed->>Registry: get_participants()
        Registry-->>Feed: 12 类参与者实例
        Feed->>OB: get_orderbook_snapshot()
        OB-->>Feed: 当前盘口
        Feed->>Shared: on_book_update(snapshot)
        loop 每个参与者
            Feed->>P: generate_order(snapshot)
            P->>Shared: 读取波动率 / 不平衡度
            P-->>Feed: 订单 Dict
            Feed->>Engine: place_order(订单)
            Engine->>OB: 撮合 / 入队
        end
        Feed->>P: generate_cancel(snapshot)
        P-->>Feed: 撤单 Dict
        Feed->>Engine: cancel_order(撤单)
    end
```

---

## 5. 并发模型

### 5.1 单标的串行处理

每个标的拥有独立的 `SymbolMatchingEngine`，内部使用 `asyncio.Queue` + `asyncio.Task` 实现单线程事件循环：

```mermaid
flowchart LR
    subgraph Engine["SymbolMatchingEngine"]
        direction TB
        Queue["asyncio.Queue\n(事件队列)"]
        Task["asyncio.Task\n(_run_loop)"]
        Process["process_event()\n串行处理"]
    end

    Queue -->|get| Task
    Task -->|process| Process
    Process -->|put| Queue

    style Queue fill:#fefce8,stroke:#fde047
    style Task fill:#f0fdf4,stroke:#86efac
    style Process fill:#f0f9ff,stroke:#bae6fd
```

事件类型：
- `order`: 新委托
- `cancel`: 撤单
- `trade`: 逐笔成交（驱动队列消耗）
- `cancel_feed`: 行情撤单

### 5.2 跨标的并行处理

不同标的之间完全独立，由 `MatchingEngineManager` 路由：

```mermaid
flowchart TB
    subgraph Manager["MatchingEngineManager"]
        direction TB
        Router["route_event(symbol, event)"]
    end

    subgraph Engine1["SymbolMatchingEngine: 000001.SZ"]
        Queue1["Queue"]
        Task1["Task"]
    end

    subgraph Engine2["SymbolMatchingEngine: 600519.SH"]
        Queue2["Queue"]
        Task2["Task"]
    end

    subgraph EngineN["SymbolMatchingEngine: ..."]
        QueueN["Queue"]
        TaskN["Task"]
    end

    Router -->|000001.SZ| Queue1
    Router -->|600519.SH| Queue2
    Router -->|...| QueueN

    style Manager fill:#faf5ff,stroke:#d8b4fe
    style Engine1 fill:#fefce8,stroke:#fde047
    style Engine2 fill:#fefce8,stroke:#fde047
    style EngineN fill:#fefce8,stroke:#fde047
```

### 5.3 同步机制

`place_order` 使用 `asyncio.Future` 实现真正的异步等待（非轮询）：

```mermaid
flowchart LR
    Client["place_order()"] -->|创建 Future| Future["asyncio.Future"]
    Client -->|put event| Queue["Event Queue"]
    Queue -->|get| Loop["Event Loop"]
    Loop -->|处理完成| SetResult["future.set_result()"]
    SetResult -->|唤醒| Future
    Future -->|返回| Client

    style Client fill:#f0f9ff,stroke:#bae6fd
    style Future fill:#f0fdf4,stroke:#86efac
    style Queue fill:#fefce8,stroke:#fde047
```

---

## 6. 配置设计

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

  market_rules:
    previous_close: 10.50     # 昨收价
    market_type: "main_board" # main_board / st_board / star_market / chinext / bse

  latency:
    default_ms: 0
    internal_ms: 0
    external_ms: 0

  api:
    host: "0.0.0.0"
    port: 8000

  logging:
    level: "INFO"
    format: "json"
```

---

## 7. 扩展性设计

| 扩展点 | 方案 | 状态 |
|--------|------|------|
| 新标的接入 | 动态创建 SymbolMatchingEngine，无需重启 | ✅ 已实现 |
| 新行情源 | 实现 Level2FeedHandler 接口 | ⏳ 预留 |
| 新参与者类型 | 继承 MarketParticipant，注册到 ParticipantRegistry | ✅ 已实现 |
| 持久化后端 | 当前 SQLite，可扩展 Redis / PostgreSQL | ⏳ 预留 |
| 集群部署 | 通过消息队列（Kafka / RabbitMQ）分发行情 | ⏳ 预留 |
| 监控告警 | Prometheus / Grafana 指标暴露 | ⏳ 预留 |
