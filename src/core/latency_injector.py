import asyncio
import heapq
from dataclasses import dataclass, field
from typing import Dict, Optional, Any, Callable


@dataclass(order=False)
class _DelayedEvent:
    """延迟事件包装"""
    scheduled_at: float
    seq: int
    event: Any

    def __lt__(self, other: "_DelayedEvent") -> bool:
        return (self.scheduled_at, self.seq) < (other.scheduled_at, other.seq)


class LatencyInjector:
    """延迟注入器

    为不同来源的订单/事件注入异步网络延迟，模拟真实交易环境中的
    网络往返与撮合所处理延迟。

    典型延迟配置（毫秒）：
    - co_location / 做市商：1-5 ms
    - 机构 / RL 智能体：10-20 ms
    - 散户：50-200 ms
    """

    def __init__(self, default_latency_ms: float = 0.0):
        self._default_latency_ms = default_latency_ms
        # source -> latency_ms
        self._latencies: Dict[str, float] = {}
        self._heap: list[_DelayedEvent] = []
        self._seq = 0
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._lock: Optional[asyncio.Lock] = None
        self._new_event: Optional[asyncio.Event] = None
        self._output_callback: Optional[Callable[[Any], Any]] = None

    def set_latency(self, source: str, latency_ms: float):
        """为某类来源设置延迟（毫秒）"""
        self._latencies[source] = max(0.0, latency_ms)

    def get_latency(self, source: str) -> float:
        """获取某类来源的延迟（毫秒）"""
        return self._latencies.get(source, self._default_latency_ms)

    def remove_latency(self, source: str):
        self._latencies.pop(source, None)

    def reset(self):
        self._latencies.clear()
        self._default_latency_ms = 0.0

    def set_output_callback(self, callback: Callable[[Any], Any]):
        """设置到期事件输出回调"""
        self._output_callback = callback

    def _ensure_loop_objects(self):
        """确保 lock/event 与当前事件循环绑定（测试环境可能切换 loop）"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if self._lock is None or (loop is not None and self._lock._loop is not loop):
            self._lock = asyncio.Lock()
        if self._new_event is None or (loop is not None and self._new_event._loop is not loop):
            self._new_event = asyncio.Event()

    async def start(self):
        if self._running:
            return
        self._ensure_loop_objects()
        self._running = True
        self._new_event.set()
        self._task = asyncio.create_task(self._dispatch_loop())

    async def stop(self):
        if not self._running:
            return
        self._running = False
        if self._new_event is not None:
            self._new_event.set()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def inject(self, event: Any, source: str = "internal") -> Any:
        """注入延迟并将事件放入内部堆

        Args:
            event: 要延迟的事件（通常包含 type/order_id）
            source: 事件来源，用于决定延迟大小

        Returns:
            传入的 event（便于链式调用）
        """
        self._ensure_loop_objects()
        latency_ms = self.get_latency(source)
        scheduled_at = asyncio.get_event_loop().time() + latency_ms / 1000.0
        async with self._lock:
            self._seq += 1
            heapq.heappush(
                self._heap,
                _DelayedEvent(scheduled_at=scheduled_at, seq=self._seq, event=event),
            )
            self._new_event.set()
        return event

    async def _dispatch_loop(self):
        """后台协程：按 scheduled_at 将事件出队并调用 output callback"""
        while self._running:
            self._ensure_loop_objects()
            now = asyncio.get_event_loop().time()
            ready_events = []
            async with self._lock:
                while self._heap and self._heap[0].scheduled_at <= now:
                    ready_events.append(heapq.heappop(self._heap).event)
                if not self._heap:
                    self._new_event.clear()

            for event in ready_events:
                if self._output_callback is not None:
                    try:
                        if asyncio.iscoroutinefunction(self._output_callback):
                            await self._output_callback(event)
                        else:
                            self._output_callback(event)
                    except Exception as e:
                        print(f"[LatencyInjector] output callback error: {e}")

            if self._running:
                if self._heap:
                    wait = max(0.0, self._heap[0].scheduled_at - asyncio.get_event_loop().time())
                    try:
                        await asyncio.wait_for(self._new_event.wait(), timeout=wait)
                    except asyncio.TimeoutError:
                        pass
                else:
                    await self._new_event.wait()
