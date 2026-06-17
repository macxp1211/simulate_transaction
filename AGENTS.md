# AGENTS.md

本文件面向 AI 编程助手，汇总了项目的实际结构、技术栈、构建方式、测试方法、代码约定以及当前已知的限制。所有内容均基于当前代码库中的真实文件与运行结果编写，未做推测。

---

## 项目概述

**高精度队列模拟撮合系统**是一个基于 Python + FastAPI 构建的证券撮合模拟引擎。核心目标是：

- 接收外部限价/市价委托；
- 按照交易所“价格优先、时间优先”规则进行撮合；
- 对未立即成交的委托模拟队列排队，记录进入队列时的 `queue_length` 与 `queue_position`；
- 以 Level-2 逐笔成交（TradeEvent）驱动队列消耗；
- 通过 REST API 与 WebSocket 提供实时接入；
- 提供前端委托终端与后端监控面板。

项目主要面向模拟交易、策略回测与队列位置分析场景，当前所有订单与成交数据均保存在内存中，未实现持久化。

---

## 技术栈与依赖

- **Python**：README 声明需要 Python 3.9+；仓库 `.venv` 中实际安装的是 Python 3.14.3。
- **Web 框架**：FastAPI（>=0.104.0）。
- **ASGI 服务器**：uvicorn（>=0.24.0）。
- **WebSocket**：websockets（>=12.0）。
- **数据校验**：pydantic（>=2.5.0）。
- **有序容器**：sortedcontainers（>=2.4.0），订单簿优先使用 `SortedDict`，缺失时回退到自定义 `SimpleSortedDict`。
- **异步运行时**：标准库 `asyncio`。
- **测试框架**：pytest（>=7.4.0）、pytest-asyncio（>=0.21.0）、pytest-benchmark（>=4.0.0）、httpx（>=0.25.0）。

依赖清单见 `requirements.txt`。

---

## 项目结构

```
高精度模拟撮合系统/
├── main.py                       # 服务启动入口（uvicorn）
├── requirements.txt              # Python 依赖
├── README.md                     # 项目介绍与快速开始
├── plan.md                       # 迭代执行计划（现状诊断与阶段目标）
├── .gitignore                    # 忽略 __pycache__、.venv、.pytest_cache 等
├── docs/                         # 设计文档
│   ├── architecture.md           # 系统架构与数据流
│   ├── api_spec.md               # REST/WebSocket 接口规范
│   ├── matching_logic.md         # 撮合逻辑与场景说明
│   └── optimization_report.md    # 性能基准与优化建议
├── frontend/                     # 静态前端（被 server.py 挂载）
│   ├── index.html                # 委托终端
│   ├── monitor.html              # 监控面板
│   ├── css/style.css
│   └── js/app.js / monitor.js
├── src/                          # 后端源码
│   ├── __init__.py               # 仅包含版本号 1.0.0
│   ├── core/                     # 撮合核心
│   │   ├── order.py              # 订单/成交模型与状态机
│   │   ├── order_book.py         # 订单簿、价格层级、队列消耗
│   │   └── matching_engine.py    # 单标引擎 + 多标管理器
│   ├── api/
│   │   └── server.py             # FastAPI REST + WebSocket 服务
│   ├── data/
│   │   ├── market_data.py        # TradeEvent / QuoteEvent 数据模型
│   │   └── level2_feed.py        # 行情源（Mock / FileReplay / WebSocket 预留）
│   └── utils/
│       └── __init__.py           # 当前为空；README 中提到的 config.py 不存在
└── tests/                        # 测试用例
    ├── conftest.py               # pytest fixtures
    ├── mock_data.py              # Mock 数据生成器
    ├── test_order.py             # 订单模型测试
    ├── test_order_book.py        # 订单簿测试
    ├── test_matching_engine.py   # 撮合引擎测试
    ├── test_api.py               # API 集成测试
    ├── test_e2e.py               # 端到端测试
    └── test_benchmark.py         # 性能基准测试
```

---

## 构建与运行

### 1. 环境准备

项目包含 `.venv` 虚拟环境，推荐使用该环境运行，以避免系统 Python 中依赖缺失的问题：

```bash
.venv/Scripts/python -m pip install -r requirements.txt   # 如依赖有更新
```

### 2. 启动服务

```bash
.venv/Scripts/python main.py
```

`main.py` 使用 `uvicorn.run("src.api.server:app", host="0.0.0.0", port=8000, reload=True)` 启动。

服务启动后访问：

- Swagger UI：`http://localhost:8000/docs`
- ReDoc：`http://localhost:8000/redoc`
- 委托终端：`http://localhost:8000/static/index.html`
- 监控面板：`http://localhost:8000/static/monitor.html`

> **注意**：当前 `src/api/server.py` 中存在两处 `app = FastAPI(...)` 定义（第 19 行与第 68 行），第二次定义会覆盖第一次，导致首次挂载的 `/static` 静态文件服务失效。因此前端页面在默认启动后可能无法访问，需要修复静态文件挂载逻辑。

### 3. 行情模拟

WebSocket 订阅市场数据时，服务端会自动创建 `MockLevel2Feed` 生成模拟逐笔成交与盘口快照，用于演示。真实行情源接入目前仅预留接口。

---

## 测试

### 运行全部测试

必须使用 `.venv` 中的 Python，否则会出现 `ModuleNotFoundError`（如缺少 `pytest-asyncio`）或模块路径问题：

```bash
.venv/Scripts/python -m pytest tests/ -q
```

当前测试结果：**81 项测试全部通过**（含 3 个 `pytest-benchmark` 基准测试）。

### 测试分类

| 测试文件 | 数量（约） | 说明 |
| --- | --- | --- |
| `test_order.py` | 18 | 订单创建、成交、撤单、状态流转、市价单、队列等待时间 |
| `test_order_book.py` | 27 | SimpleSortedDict、PriceLevel、订单簿插入/撮合/撤单/快照/队列消耗/场景测试 |
| `test_matching_engine.py` | 13 | 单标引擎启停、委托、撮合、撤单、逐笔成交驱动、多标管理器 |
| `test_api.py` | 10 | FastAPI TestClient 验证 REST 端点 |
| `test_e2e.py` | 8 | 完整业务流、状态流转、分页、WebSocket 连接 |
| `test_benchmark.py` | 5 | 订单簿插入/撮合/队列消耗、引擎吞吐量/延迟/内存估算 |

### 测试约定

- `conftest.py` 将 `src` 目录加入 `sys.path`，并提供常用 fixtures（`sample_order_buy`、`sample_order_book`、`api_client` 等）。
- API 测试每个方法前通过 `engine_manager._engines.clear()` 重置全局引擎状态，避免测试间相互污染。
- 基准测试使用 `pytest-benchmark` 的 `benchmark` fixture；吞吐量/延迟测试使用 `asyncio` 直接驱动引擎事件队列。

---

## 代码组织与核心模块

### 1. 订单模型（`src/core/order.py`）

- `Side`、`OrderStatus`、`OrderType` 为 `Enum`。
- `Order` 使用 `@dataclass` 定义，包含 `symbol`、`side`、`price`（`Decimal`）、`quantity`、`filled_qty`、`status`、`queue_info`、`trades` 等字段。
- 关键方法：`fill(qty)`、`cancel()`、`enter_queue(...)`、`update_queue_position(...)`、`to_dict()`。
- 市价单在 `__post_init__` 中被转换为极端价格（买 `999999.99`、卖 `0.01`），后续在 `OrderBook.add_order` 中再按当前最优价处理。

### 2. 订单簿（`src/core/order_book.py`）

- `PriceLevel`：同一价格层级的 FIFO 订单队列，维护 `total_quantity`。
- `SimpleSortedDict`：基于 `bisect` + `list` 的备用有序字典，删除操作为 `O(N)`。
- `OrderBook`：
  - 优先使用 `sortedcontainers.SortedDict`（bids 用 `lambda x: -x` 实现降序，asks 默认升序）；
  - 维护 `_order_index`（`order_id -> (side, price, order)`）用于 `O(1)` 定位；
  - 维护 `_all_orders` 保存所有历史订单；
  - 提供 `add_order`（撮合/入队）、`cancel_order`、`consume_queue_on_trade`（逐笔成交驱动队列消耗）、`get_snapshot` 等。

### 3. 撮合引擎（`src/core/matching_engine.py`）

- `MatchingConfig`：撮合配置（`price_tick`、`lot_size`、`max_queue_depth` 等）。
- `SymbolMatchingEngine`：单标的串行事件循环，通过 `asyncio.Queue` 处理 `order`/`cancel`/`trade`/`quote` 事件。
  - `place_order` / `cancel_order` 通过事件队列提交后使用 `asyncio.sleep(0.001)` 轮询等待结果（最多 100 次）。
  - `_handle_trade` 将逐笔成交交给 `OrderBook.consume_queue_on_trade` 消耗队列。
- `MatchingEngineManager`：管理多个标的引擎，按需创建并启动，提供 `shutdown_all`。

### 4. API 服务（`src/api/server.py`）

- FastAPI 应用，REST 前缀 `/api/v1`。
- WebSocket 端点 `/ws/v1`，支持 `subscribe` 动作（`market` 频道）。
- 全局单例 `engine_manager` 与 `feed_handlers`。
- 使用 `@app.on_event("startup")` / `@app.on_event("shutdown")` 管理生命周期（FastAPI 已弃用，建议迁移到 `lifespan`）。

### 5. 行情数据（`src/data/`）

- `market_data.py`：定义 `TradeEvent` 与 `QuoteEvent`，支持 `from_raw` 多格式解析。
- `level2_feed.py`：
  - `MockLevel2Feed`：随机生成逐笔成交与盘口快照；
  - `FileReplayFeed`：文件回放（当前 `_replay_loop` 为空，TODO）；
  - `WebSocketFeed`：实时行情接入（当前 `_connect_and_listen` 为空，TODO）。

---

## 代码风格与约定

- **语言**：代码注释、文档、README、前端 UI 均以中文为主。
- **命名**：使用 `snake_case`；类名使用 `PascalCase`；常量/枚举成员使用大写。
- **价格处理**：统一使用 `Decimal` 避免浮点误差；API 与字典序列化时价格转为字符串。
- **时间戳**：使用 `datetime.now()` 与 ISO 8601 字符串输出。
- **异步**：撮合核心基于 `asyncio`，事件队列串行处理保证单标内顺序；跨标引擎相互独立可并行。
- **状态机**：订单状态包括 `pending` → `active` → `queued`/`matching`/`partial` → `filled` / `cancelled` / `rejected`。
- **最小改动原则**：新增功能时应尽量与现有模块风格保持一致，避免破坏已有的 81 项测试。

---

## API 与前端

### REST 接口

- `POST   /api/v1/orders` — 提交委托
- `DELETE /api/v1/orders/{order_id}` — 撤销委托
- `GET    /api/v1/orders/{order_id}` — 查询单笔委托
- `GET    /api/v1/orders` — 查询委托列表（支持 `symbol`、`status`、`side`、`page`、`page_size`）
- `GET    /api/v1/trades` — 查询成交记录
- `GET    /api/v1/orderbook/{symbol}` — 订单簿快照
- `GET    /api/v1/symbols` — 已激活标的列表
- `GET    /api/v1/stats/{symbol}` — 标的统计信息

返回体统一为 `{ "code": 0, "message": "success", "data": ... }`。

### WebSocket 接口

- 路径：`/ws/v1`
- 支持动作：
  - `{"action": "subscribe", "channel": "market", "symbols": ["000001.SZ"]}`
  - `{"action": "ping"}` → 返回 `{"type": "pong", "timestamp": ...}`

### 前端

- `index.html`：委托表单、实时订单簿、我的订单、成交记录、实时日志。
- `monitor.html`：统计卡片、活跃标的表格、实时日志。
- 前端通过 `fetch` 调用 REST API，通过 WebSocket 接收成交/行情推送，并定时 3 秒刷新。

---

## 已知问题与注意事项

1. **静态文件服务重复定义**
   `src/api/server.py` 中 `app` 与 `engine_manager` 均被定义两次，第二次定义会覆盖第一次，导致首次挂载的 `/static` 静态文件路由丢失。前端页面可能无法通过 `/static/index.html` 访问。

2. **`src/utils/config.py` 缺失**
   README 的项目结构图中列出 `src/utils/config.py`，但实际文件不存在，`src/utils/__init__.py` 为空。

3. **`@app.on_event` 已弃用**
   FastAPI 9.x 已弃用 `on_event`，启动/关闭事件应迁移到 `lifespan` 管理器。

4. **行情源实现不完整**
   `FileReplayFeed` 与 `WebSocketFeed` 的接收循环为 TODO；当前仅 `MockLevel2Feed` 可用。

5. **订单状态同步采用轮询**
   `SymbolMatchingEngine.place_order` / `cancel_order` 使用 `asyncio.sleep(0.001)` 轮询等待结果，延迟在 5–15ms 量级，未达到 README 中 <1ms 的目标。`optimization_report.md` 建议改用 `asyncio.Future`。

6. **性能差距**
   根据 `pytest-benchmark` 实测：
   - 订单簿插入：约 1.34 Kops/s（目标 >10 Kops/s）；
   - 订单簿撮合：约 4.42 Kops/s（目标 >10 Kops/s）；
   - 队列消耗：约 10.41 Kops/s（已达标）。

7. **内存管理**
   `_all_orders` 字典保存所有历史订单，长期运行存在内存增长风险；未实现归档或 GC 机制。

8. **无持久化与高可用**
   所有数据保存在内存，服务重启后丢失；Redis、集群、监控告警均在扩展计划中。

---

## 安全考虑

- **认证授权**：API 规范中标注“认证：Bearer Token（预留）”，但当前代码未实现任何认证/授权机制，服务默认对本地网络开放。
- **输入校验**：依赖 FastAPI/Pydantic 进行基础类型校验；`SymbolMatchingEngine._validate_order` 仅校验 `symbol`、数量正数、手数倍数、限价单价格正数。缺少价格精度、涨跌停、标的白名单等校验。
- **错误处理**：服务端异常统一包装为 `HTTPException(500, detail=str(e))`，可能泄露内部异常信息。
- **静态文件**：`StaticFiles` 挂载到 `/static`，目录包含前端资源，无额外访问控制。
- **WebSocket**：未对订阅的 `symbols` 进行校验，任意字符串均可触发创建对应标的的 `MockLevel2Feed`。
- **生产部署**：当前实现仅适合本地开发/测试/模拟场景，不建议直接暴露到公网或用于真实交易。

---

## 扩展计划（来自 README / plan.md）

已完成：

- 81 项测试体系；
- 前端可视化委托界面；
- 后端监控面板；
- 架构优化报告。

待实现：

- Redis 持久化支持；
- 真实 Level-2 行情源接入（腾讯、新浪、券商接口）；
- 文件回放回测模式（`FileReplayFeed`）；
- 多节点集群部署；
- Prometheus/Grafana 监控告警；
- 用户管理和权限控制。
