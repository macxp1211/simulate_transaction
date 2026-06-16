# 高精度模拟撮合系统 - 执行计划

## 现状诊断

### 代码结构
- `src/core/order.py` — 订单/成交模型
- `src/core/order_book.py` — 基于 bisect 的订单簿（SimpleSortedDict）
- `src/core/matching_engine.py` — 单标的串行引擎 + 多标的并行管理
- `src/api/server.py` — FastAPI REST + WebSocket
- `src/data/level2_feed.py` — Mock 行情源（文件回放/WebSocket 待实现）
- `src/data/market_data.py` — TradeEvent/QuoteEvent

### 核心问题
1. **测试缺失**：`tests/` 目录完全为空，无单元测试、集成测试、端到端测试
2. **效率问题**：
   - `SimpleSortedDict.__delitem__` 使用 `list.remove(key)` → O(N) 删除
   - 引擎等待处理结果使用 `asyncio.sleep(0.001)` 轮询 100 次 → 低效且不可靠
   - 未使用 `sortedcontainers`（README 提及但 requirements.txt 未包含）
   - 逐笔成交驱动队列消耗时遍历所有价格层级，大数据量下性能不足
3. **前端缺失**：无委托提交界面，无订单簿可视化
4. **监控缺失**：无撮合引擎监控面板，无实时统计可视化
5. **架构问题**：
   - 无持久化（Redis 等）
   - 无性能基准测试
   - 无文件回放回测模式（FileReplayFeed 为空）

---

## 执行阶段

### Stage 1 — 测试体系与 Mock 数据
**目标**：设计测试方案，构建 Mock 数据，实现端到端测试

**子任务**：
1. 在 `tests/` 下创建测试框架
2. 构建 Mock 数据生成器（模拟订单簿、逐笔成交、盘口快照）
3. 实现核心模块单元测试（order, order_book, matching_engine）
4. 实现 API 集成测试（FastAPI TestClient）
5. 实现端到端测试（委托→撮合→成交→撤单 完整流程）
6. 添加性能基准测试（pytest-benchmark）

**交付物**：
- `tests/conftest.py` — 测试配置和 fixtures
- `tests/mock_data.py` — Mock 数据生成器
- `tests/test_order.py` — 订单模型测试
- `tests/test_order_book.py` — 订单簿测试
- `tests/test_matching_engine.py` — 撮合引擎测试
- `tests/test_api.py` — API 集成测试
- `tests/test_e2e.py` — 端到端测试
- `tests/test_benchmark.py` — 性能基准测试

### Stage 2 — 前端可视化委托界面
**目标**：构建基于 WebSocket 的实时委托界面

**子任务**：
1. 在 `src/api/server.py` 中挂载静态文件服务
2. 创建 `frontend/` 目录
3. 实现委托提交表单（买卖方向、价格、数量、标的）
4. 实时订单簿深度图
5. 实时成交记录
6. 订单状态跟踪（WebSocket 推送）

**交付物**：
- `frontend/index.html` — 主页面
- `frontend/css/style.css` — 样式
- `frontend/js/app.js` — 核心逻辑（WebSocket + REST API）

### Stage 3 — 后端撮合可视化监控
**目标**：构建撮合引擎监控 Dashboard

**子任务**：
1. 扩展 API 增加监控接口（`/api/v1/monitor/*`）
2. 实现引擎实时统计（吞吐量、延迟、队列深度）
3. 构建监控 Dashboard 页面（HTML/JS）
4. 实时图表（WebSocket 推送监控数据）

**交付物**：
- `src/api/monitor.py` — 监控 API 模块
- `frontend/monitor.html` — 监控面板
- `frontend/js/monitor.js` — 监控逻辑

### Stage 4 — 架构效率评估与优化
**目标**：评估并优化系统架构和执行效率

**子任务**：
1. 评估 `SimpleSortedDict` 性能瓶颈
2. 引入 `sortedcontainers.SortedDict` 优化订单簿
3. 优化引擎同步机制（使用 `asyncio.Event` 替代轮询）
4. 优化逐笔成交队列消耗算法（减少不必要遍历）
5. 性能基准测试验证优化效果
6. 编写架构优化报告

**交付物**：
- `docs/optimization_report.md` — 架构优化报告
- 优化后的 `src/core/order_book.py`
- 优化后的 `src/core/matching_engine.py`
- 性能对比数据

---

## 技能加载

- Stage 1: 无特定技能（纯代码工程）
- Stage 2: 无特定技能（前端开发）
- Stage 3: 无特定技能（前端 + API 开发）
- Stage 4: 无特定技能（性能优化）

## 质量门禁

- 每阶段完成后必须运行测试通过
- 前端页面必须能正常访问和交互
- 监控数据必须实时更新
- 性能基准测试必须有优化前后对比
