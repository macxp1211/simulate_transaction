const API_BASE = '';
const WS_URL = `ws://${window.location.host}/ws/v1`;

let ws = null;
let logs = [];
let currentSymbol = '000001.SZ';

function log(msg, type = 'info') {
    const time = new Date().toLocaleTimeString('zh-CN', { hour12: false });
    logs.unshift({ time, msg, type });
    if (logs.length > 50) logs.pop();
    const el = document.getElementById('monitorLogList');
    if (el) {
        el.innerHTML = logs.map(l => `<div class="log-item"><span style="color:#718096">${l.time}</span> ${l.msg}</div>`).join('');
    }
}

async function apiGet(path) {
    const res = await fetch(`${API_BASE}${path}`);
    return res.json();
}

async function refreshStats() {
    try {
        const data = await apiGet('/api/v1/symbols');
        if (data.code !== 0) return;
        const symbols = data.data.symbols || [];
        
        let totalReceived = 0, totalFilled = 0, totalQueued = 0;
        symbols.forEach(s => {
            totalReceived += s.orders_received || 0;
            totalFilled += s.orders_filled || 0;
            totalQueued += s.orders_queued || 0;
        });
        
        document.getElementById('statOrdersReceived').textContent = totalReceived;
        document.getElementById('statOrdersFilled').textContent = totalFilled;
        document.getElementById('statOrdersQueued').textContent = totalQueued;
        document.getElementById('statEngineCount').textContent = symbols.length;
        
        // 刷新标的表格
        const tbody = document.getElementById('enginesTable');
        if (tbody) {
            if (symbols.length === 0) {
                tbody.innerHTML = '<tr><td colspan="6" style="color:#999;padding:20px">暂无活跃标的</td></tr>';
            } else {
                tbody.innerHTML = symbols.map(s => `<tr>
                    <td><strong>${s.symbol}</strong></td>
                    <td><span style="color:#48bb78">${s.status}</span></td>
                    <td>${s.orders_received}</td>
                    <td>${s.orders_filled}</td>
                    <td>${s.orders_queued}</td>
                    <td><a href="/static/index.html?symbol=${s.symbol}" style="color:#4299e1;font-size:12px">查看</a></td>
                </tr>`).join('');
            }
        }
    } catch (err) {
        console.error('刷新统计失败', err);
    }
}

async function refreshCrossStats() {
    try {
        const engines = await apiGet('/api/v1/symbols');
        if (engines.code !== 0) return;
        let crossTotal = 0, feedTotal = 0;
        for (const s of engines.data.symbols || []) {
            const stats = await apiGet(`/api/v1/stats/${s.symbol}`);
            if (stats.code === 0 && stats.data) {
                crossTotal += stats.data.trades_from_cross || 0;
                feedTotal += stats.data.trades_from_feed || 0;
            }
        }
        document.getElementById('statTradesCross').textContent = crossTotal;
        document.getElementById('statTradesFeed').textContent = feedTotal;
    } catch (err) {
        console.error('刷新成交统计失败', err);
    }
}

async function refreshOrderBook(symbol) {
    symbol = symbol || currentSymbol;
    currentSymbol = symbol;
    const bookSymbolEl = document.getElementById('bookSymbol');
    if (bookSymbolEl) bookSymbolEl.textContent = symbol;

    const input = document.getElementById('bookSymbolInput');
    if (input) input.value = symbol;

    try {
        const data = await apiGet(`/api/v1/orderbook/${symbol}?depth=10`);
        if (data.code !== 0) return;
        const book = data.data;

        const askEl = document.getElementById('askBook');
        const bidEl = document.getElementById('bidBook');
        const spreadEl = document.getElementById('spread');

        if (askEl) {
            askEl.innerHTML = (book.asks || []).slice().reverse().map(a =>
                `<tr><td>${a.price}</td><td>${a.total_quantity}</td><td>${a.order_count}</td></tr>`
            ).join('') || '<tr><td colspan="3" style="color:#999">无卖盘</td></tr>';
        }
        if (bidEl) {
            bidEl.innerHTML = (book.bids || []).map(b =>
                `<tr><td>${b.price}</td><td>${b.total_quantity}</td><td>${b.order_count}</td></tr>`
            ).join('') || '<tr><td colspan="3" style="color:#999">无买盘</td></tr>';
        }
        if (spreadEl) {
            spreadEl.textContent = book.spread ? `价差: ${book.spread}` : '价差: --';
        }
    } catch (err) {
        console.error('刷新订单簿失败', err);
    }
}

function renderQuoteBook(quote) {
    const symbol = quote.symbol || currentSymbol;
    currentSymbol = symbol;

    const bookSymbolEl = document.getElementById('bookSymbol');
    if (bookSymbolEl) bookSymbolEl.textContent = symbol;

    const input = document.getElementById('bookSymbolInput');
    if (input) input.value = symbol;

    const askEl = document.getElementById('askBook');
    const bidEl = document.getElementById('bidBook');
    const spreadEl = document.getElementById('spread');

    const asks = quote.asks || [];
    const bids = quote.bids || [];

    if (askEl) {
        askEl.innerHTML = asks.slice().reverse().map(a =>
            `<tr><td>${a.price}</td><td>${a.total_quantity}</td><td>${a.order_count}</td></tr>`
        ).join('') || '<tr><td colspan="3" style="color:#999">无卖盘</td></tr>';
    }
    if (bidEl) {
        bidEl.innerHTML = bids.map(b =>
            `<tr><td>${b.price}</td><td>${b.total_quantity}</td><td>${b.order_count}</td></tr>`
        ).join('') || '<tr><td colspan="3" style="color:#999">无买盘</td></tr>';
    }
    if (spreadEl) {
        const bestAsk = asks.length > 0 ? parseFloat(asks[0].price) : null;
        const bestBid = bids.length > 0 ? parseFloat(bids[0].price) : null;
        if (bestAsk !== null && bestBid !== null) {
            spreadEl.textContent = `价差: ${(bestAsk - bestBid).toFixed(2)}`;
        } else {
            spreadEl.textContent = '价差: --';
        }
    }
}

function connectWebSocket() {
    try {
        ws = new WebSocket(WS_URL);
        ws.onopen = () => {
            log('监控 WebSocket 已连接');
            ws.send(JSON.stringify({ action: 'subscribe', channel: 'market', symbols: ['000001.SZ'] }));
        };
        ws.onmessage = (event) => {
            const msg = JSON.parse(event.data);
            if (msg.type === 'trade') {
                log(`成交: ${msg.symbol} ${msg.price} x ${msg.quantity} [${msg.side}]`);
                refreshStats();
                refreshCrossStats();
            } else if (msg.type === 'quote') {
                currentSymbol = msg.symbol || currentSymbol;
                renderQuoteBook(msg);
                refreshStats();
                refreshCrossStats();
            }
        };
        ws.onclose = () => {
            log('监控 WebSocket 断开，5秒后重连...');
            setTimeout(connectWebSocket, 5000);
        };
        ws.onerror = () => log('监控 WebSocket 错误', 'error');
    } catch (err) {
        log('WebSocket 连接失败', 'error');
    }
}

// 初始化
document.addEventListener('DOMContentLoaded', () => {
    refreshStats();
    refreshCrossStats();
    refreshOrderBook(currentSymbol);
    connectWebSocket();

    const refreshBtn = document.getElementById('refreshBook');
    if (refreshBtn) refreshBtn.addEventListener('click', () => refreshOrderBook(currentSymbol));

    const symbolInput = document.getElementById('bookSymbolInput');
    if (symbolInput) {
        symbolInput.addEventListener('change', (e) => {
            currentSymbol = e.target.value.trim() || '000001.SZ';
            refreshOrderBook(currentSymbol);
            // 重新订阅新标的行情
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ action: 'subscribe', channel: 'market', symbols: [currentSymbol] }));
            }
        });
    }

    setInterval(() => {
        refreshStats();
        refreshCrossStats();
    }, 3000);
});
