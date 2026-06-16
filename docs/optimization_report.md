# 高精度模拟撮合系统 - 架构优化报告

## 1. 测试与性能基准

### 1.1 测试覆盖

当前已实现 **81 项测试**，覆盖率达到核心模块的完整场景：

| 测试类别 | 数量 | 说明 |
|---|---|---|
| 订单模型测试 | 18 | 订单创建、成交、撤单、状态流转 |
| 订单簿测试 | 27 | 插入、撮合、队列消耗、快照、场景测试 |
| 撮合引擎测试 | 13 | 单标的引擎、多标的管理器、并发处理 |
| API 集成测试 | 10 | REST API 端点验证 |
| 端到端测试 | 8 | 完整业务流：委托→撮合→成交→撤单 |
| 性能基准测试 | 5 | 插入、撮合、吞吐量、延迟、内存 |

### 1.2 性能基准数据

基于 `pytest-benchmark` 的实测数据（Python 3.14, Windows）：

| 指标 | 实测值 | 目标值 | 差距 |
|---|---|---|---|
| 订单簿插入 | 1,521 ops/s | >10,000 ops/s | **6.6x 差距** |
| 订单簿撮合 | 3,753 ops/s | >10,000 ops/s | **2.7x 差距** |
| 队列消耗 | 11,296 ops/s | >10,000 ops/s | ✅ 达标 |
| 单引擎延迟 | ~5-15 ms/笔 | <1 ms | **5-15x 差距** |
| 内存占用 | 估算 ~200 bytes/订单 | <1GB/10000订单 | 待优化 |

## 2. 效率瓶颈分析

### 2.1 瓶颈1：SimpleSortedDict 性能不足

**问题描述**：
当前 `OrderBook` 使用自定义的 `SimpleSortedDict` 维护价格层级有序索引：

```python
class SimpleSortedDict:
    def __delitem__(self, key: Decimal):
        if key in self._data:
            self._keys.remove(key)  # O(N) 线性删除！
            del self._data[key]
```

- `bisect` 插入是 O(log N)，但 `list.insert()` 是 O(N)（需要移动元素）
- `list.remove(key)` 删除是 O(N)
- 当价格层级深度达到 1000 时，单次操作可能达到毫秒级

**影响**：
- 订单簿插入性能仅 1,521 ops/s，远低于目标 10,000 ops/s
- 逐笔成交驱动队列消耗时，需要频繁删除空价格层级，触发 O(N) 删除

### 2.2 瓶颈2：轮询同步机制低效

**问题描述**：
`SymbolMatchingEngine` 使用 `asyncio.sleep(0.001)` 轮询等待事件处理完成：

```python
async def place_order(self, order: Order) -> Order:
    await self._event_queue.put({"type": "order", "order": order})
    for _ in range(100):
        if order.status != OrderStatus.PENDING:
            break
        await asyncio.sleep(0.001)  # 轮询 100ms 上限
    return order
```

- 最小等待 1ms，即使事件已经处理完毕
- 100 次轮询上限，在高并发下可能不足
- 本质上是一种 busy-waiting 的变体

**影响**：
- 实测平均延迟 5-15ms/笔，目标 <1ms
- 高并发时轮询次数叠加，导致 CPU 浪费

### 2.3 瓶颈3：逐笔成交队列消耗遍历开销

**问题描述**：
`_consume_bids` 和 `_consume_asks` 遍历所有价格层级：

```python
for price in self.bids.keys():  # 遍历所有价格层级
    if price < min_price or remaining <= 0:
        break
```

- 虽然会在 `price < min_price` 时 break，但最坏情况下仍需遍历大量价格层级
- 对于深订单簿（价格层级 > 1000），单次逐笔成交可能触发数百次价格层级遍历

**影响**：
- 极端行情下（大单砸穿多个价格层级），响应延迟增加

### 2.4 瓶颈4：内存管理缺失

**问题描述**：
- `OrderBook._all_orders` 字典持续累积所有历史订单，没有清理机制
- 长时间运行后，已成交/已撤销的订单仍然占用内存
- 对于高频交易场景，内存会线性增长

**影响**：
- 内存泄漏风险
- 无法支持长时间运行

### 2.5 瓶颈5：事件循环兼容性

**问题描述**：
- 引擎的 `_run_loop` 使用 `asyncio.create_task`，在 `TestClient` 同步测试中，事件循环关闭后任务被中断
- 需要自动重启机制，增加了复杂度
- 未使用 `uvloop` 等高性能事件循环替代方案

**影响**：
- 测试和部署环境的一致性难以保证
- 性能受限于默认 asyncio 实现

### 2.6 瓶颈6：缺少持久化层

**问题描述**：
- 所有订单和成交记录存储在内存中
- 服务重启后数据全部丢失
- 无法支持集群部署和故障恢复

**影响**：
- 生产环境可用性不足
- 无法水平扩展

## 3. 优化方案

### 3.1 优化1：引入 sortedcontainers（高优先级）

**方案**：使用 `sortedcontainers.SortedDict` 替代 `SimpleSortedDict`。

```python
from sortedcontainers import SortedDict

class OrderBook:
    def __init__(self, symbol: str):
        self.bids = SortedDict(lambda x: -x)  # 降序
        self.asks = SortedDict()  # 升序
```

**预期收益**：
- 插入/删除复杂度从 O(N) → O(log N)
- 订单簿插入性能预计提升 **5-10x**
- 代码更简洁，经过充分测试

**实施状态**：requirements.txt 已包含 `sortedcontainers`，但代码中未使用。

### 3.2 优化2：使用 asyncio.Future 替代轮询（高优先级）

**方案**：引入 `asyncio.Future` 或 `asyncio.Event` 实现真正的异步通知：

```python
class SymbolMatchingEngine:
    def __init__(self, ...):
        self._pending_futures: Dict[str, asyncio.Future] = {}
    
    async def place_order(self, order: Order) -> Order:
        future = asyncio.get_event_loop().create_future()
        self._pending_futures[order.order_id] = future
        await self._event_queue.put({"type": "order", "order": order})
        await future  # 真正等待，无需轮询
        del self._pending_futures[order.order_id]
        return order
    
    async def _handle_order(self, order: Order):
        ...
        future = self._pending_futures.get(order.order_id)
        if future and not future.done():
            future.set_result(order)
```

**预期收益**：
- 延迟从 5-15ms → **<1ms**
- 消除 CPU 轮询开销
- 代码更简洁可靠

### 3.3 优化3：优化队列消耗算法（中优先级）

**方案**：在 `PriceLevel` 中维护快速索引，减少遍历：

```python
class PriceLevel:
    def __init__(self, price: Decimal):
        self.price = price
        self.orders: deque = deque()  # 使用 deque 替代 list
        self._active_qty = 0  # 活跃订单总数量
```

同时，使用 `SortedDict` 的 `irange()` 方法精确遍历指定价格范围，避免不必要的 break 检查。

**预期收益**：
- 大单消耗场景性能提升 **2-3x**
- 减少不必要的内存遍历

### 3.4 优化4：内存清理机制（中优先级）

**方案**：
1. 引入订单归档机制：已成交/已撤销的订单定期移入归档存储
2. 添加 `OrderBook` 的 `gc()` 方法，清理历史订单
3. 配置最大订单历史保留时间（如 24 小时）

```python
async def gc_old_orders(self, max_age_hours: int = 24):
    cutoff = datetime.now() - timedelta(hours=max_age_hours)
    to_remove = [oid for oid, o in self._all_orders.items() 
                 if not o.is_active and o.update_time < cutoff]
    for oid in to_remove:
        del self._all_orders[oid]
```

**预期收益**：
- 内存占用可控
- 支持长时间运行

### 3.5 优化5：使用 uvloop 和 lifespan（中优先级）

**方案**：
1. 使用 `uvloop` 替代 asyncio 默认事件循环（Unix 环境下提升 2-4x）
2. 使用 `lifespan` 替代 `@app.on_event` 管理引擎生命周期
3. 将引擎启动/停止逻辑与 FastAPI 生命周期绑定

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时预加载引擎
    yield
    # 关闭时清理所有引擎
    await engine_manager.shutdown_all()

app = FastAPI(lifespan=lifespan)
```

**预期收益**：
- 事件循环性能提升 2-4x
- 生命周期管理更可靠
- 消除测试中的事件循环兼容性问题

### 3.6 优化6：引入 Redis 持久化（低优先级）

**方案**：
1. 订单簿状态快照定期写入 Redis
2. 订单和成交记录持久化到 Redis Stream
3. 支持多节点共享订单簿状态

```python
class RedisOrderBookPersistence:
    async def snapshot(self, order_book: OrderBook):
        await redis.hset(f"orderbook:{order_book.symbol}", ...)
```

**预期收益**：
- 服务重启后恢复订单簿状态
- 支持多节点集群部署
- 数据可持久化审计

## 4. 优化实施路线图

| 阶段 | 优化项 | 优先级 | 预计工作量 | 预期性能提升 |
|---|---|---|---|---|
| Phase 1 | sortedcontainers + asyncio.Future | 高 | 2-3 天 | **5-10x** |
| Phase 2 | uvloop + lifespan + 内存 GC | 中 | 2 天 | **2-3x** |
| Phase 3 | Redis 持久化 + 集群部署 | 低 | 3-5 天 | 可靠性 |

## 5. 当前已完成的优化

本次迭代中已实施的改进：

1. ✅ **修复了 `_consume_bids` 的遍历顺序 bug**：`reversed()` 与 `reverse=True` 的 `SortedDict` 双重反转导致从低价开始遍历，已修复为直接使用 `self.bids.keys()`
2. ✅ **改进了引擎事件循环重启机制**：`start()` 在检测到 `_task.done()` 时自动重建事件队列和任务，解决了测试中的事件循环中断问题
3. ✅ **改进了 `cancel_order` 的同步机制**：从固定 `sleep(0.01)` 改为轮询检查状态，与 `place_order` 保持一致
4. ✅ **编写了完整的测试体系**：81 项测试覆盖核心功能、API 集成和端到端场景
5. ✅ **添加了性能基准测试**：`pytest-benchmark` 提供可量化的性能数据
6. ✅ **前端可视化委托界面**：`frontend/index.html` 支持实时委托提交、订单簿查看、WebSocket 推送
7. ✅ **后端监控面板**：`frontend/monitor.html` 展示引擎统计、活跃标的、实时日志

## 6. 下一步建议

1. **立即实施**：使用 `sortedcontainers.SortedDict` 替换 `SimpleSortedDict`，验证性能提升
2. **立即实施**：使用 `asyncio.Future` 替代 `asyncio.sleep` 轮询，降低延迟
3. **短期实施**：引入 `uvloop` 和 `lifespan` 优化事件循环和生命周期管理
4. **中期实施**：添加内存 GC 机制，支持长时间运行
5. **长期实施**：引入 Redis 持久化，支持集群部署和故障恢复
