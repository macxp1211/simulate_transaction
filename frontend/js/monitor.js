const API_BASE = '';
const WS_URL = `ws://${window.location.host}/ws/v1`;

let ws = null;
let logs = [];
let currentSymbol = '000001.SZ';
let priceHistory = [];
let tradeStream = [];

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

async function apiPost(path, body) {
    const res = await fetch(`${API_BASE}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
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

        const el1 = document.getElementById('statOrdersReceived');
        const el2 = document.getElementById('statOrdersFilled');
        const el3 = document.getElementById('statOrdersQueued');
        const el4 = document.getElementById('statEngineCount');
        if (el1) el1.textContent = totalReceived;
        if (el2) el2.textContent = totalFilled;
        if (el3) el3.textContent = totalQueued;
        if (el4) el4.textContent = symbols.length;

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
        const el1 = document.getElementById('statTradesCross');
        const el2 = document.getElementById('statTradesFeed');
        if (el1) el1.textContent = crossTotal;
        if (el2) el2.textContent = feedTotal;
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

// ─────────── 走势图 ───────────
function drawPriceChart() {
    const canvas = document.getElementById('priceChart');
    if (!canvas || priceHistory.length < 2) return;

    const ctx = canvas.getContext('2d');
    const width = canvas.width;
    const height = canvas.height;
    const padding = { top: 20, bottom: 30, left: 50, right: 20 };
    const chartW = width - padding.left - padding.right;
    const chartH = height - padding.top - padding.bottom;

    ctx.clearRect(0, 0, width, height);

    // 计算价格范围
    let minPrice = Infinity, maxPrice = -Infinity;
    for (const p of priceHistory) {
        const price = parseFloat(p.price);
        if (price < minPrice) minPrice = price;
        if (price > maxPrice) maxPrice = price;
    }
    const priceRange = maxPrice - minPrice || 1;
    const priceScale = chartH / (priceRange * 1.2);
    const priceOffset = minPrice - priceRange * 0.1;

    // 绘制网格
    ctx.strokeStyle = '#e2e8f0';
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
        const y = padding.top + chartH * (i / 4);
        ctx.beginPath();
        ctx.moveTo(padding.left, y);
        ctx.lineTo(width - padding.right, y);
        ctx.stroke();

        const labelPrice = maxPrice - (maxPrice - minPrice) * (i / 4);
        ctx.fillStyle = '#718096';
        ctx.font = '10px sans-serif';
        ctx.textAlign = 'right';
        ctx.fillText(labelPrice.toFixed(2), padding.left - 4, y + 3);
    }

    // 绘制价格线
    ctx.strokeStyle = '#4299e1';
    ctx.lineWidth = 2;
    ctx.beginPath();
    for (let i = 0; i < priceHistory.length; i++) {
        const x = padding.left + (i / (priceHistory.length - 1)) * chartW;
        const y = padding.top + (maxPrice - parseFloat(priceHistory[i].price) + priceRange * 0.1) * priceScale;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
    }
    ctx.stroke();

    // 绘制最新价格
    const last = priceHistory[priceHistory.length - 1];
    const lastPrice = parseFloat(last.price);
    const lastX = padding.left + chartW;
    const lastY = padding.top + (maxPrice - lastPrice + priceRange * 0.1) * priceScale;

    ctx.fillStyle = '#4299e1';
    ctx.beginPath();
    ctx.arc(lastX, lastY, 4, 0, Math.PI * 2);
    ctx.fill();

    ctx.fillStyle = '#1a202c';
    ctx.font = 'bold 12px sans-serif';
    ctx.textAlign = 'right';
    ctx.fillText(`最新: ${lastPrice.toFixed(2)}`, width - padding.right, padding.top - 4);
}

// ─────────── 逐笔成交流水 ───────────
function renderTradeStream() {
    const el = document.getElementById('tradeStream');
    if (!el) return;
    if (tradeStream.length === 0) {
        el.innerHTML = '<div style="color:#999;padding:20px">暂无成交数据</div>';
        return;
    }
    const latest = tradeStream.slice(-50);
    el.innerHTML = latest.map(t => {
        const time = t.trade_time ? t.trade_time.slice(11, 19) : '--';
        const sideClass = t.side === 'buy' ? 'ts-side-buy' : 'ts-side-sell';
        const source = t.match_source === 'order_cross' ? '撮合' : '行情';
        return `<div class="trade-stream-item">
            <span class="ts-time">${time}</span>
            <span class="ts-price ${sideClass}">${t.price}</span>
            <span class="ts-qty">${t.quantity}</span>
            <span class="${sideClass}">${t.side === 'buy' ? '买入' : '卖出'}</span>
            <span class="ts-source">${source}</span>
        </div>`;
    }).join('');
    el.scrollTop = el.scrollHeight;
}

// ─────────── 参与者配置 ───────────
async function loadParticipantConfig() {
    try {
        const data = await apiGet(`/api/v1/market/participants/config?symbol=${currentSymbol}`);
        if (data.code === 0 && data.data && data.data.config) {
            const c = data.data.config;
            const el1 = document.getElementById('configTargetPrice');
            const el2 = document.getElementById('configInterval');
            const el3 = document.getElementById('configMMCount');
            const el4 = document.getElementById('configTFCount');
            const el5 = document.getElementById('configMRCount');
            const el6 = document.getElementById('configNTCount');
            const el7 = document.getElementById('configATCount');
            if (el1 && c.target_price) el1.value = c.target_price;
            if (el2 && c.order_interval) el2.value = c.order_interval;
            if (el3 && c.market_maker_count !== undefined) el3.value = c.market_maker_count;
            if (el4 && c.trend_follower_count !== undefined) el4.value = c.trend_follower_count;
            if (el5 && c.mean_reversion_count !== undefined) el5.value = c.mean_reversion_count;
            if (el6 && c.noise_trader_count !== undefined) el6.value = c.noise_trader_count;
            if (el7 && c.aggressive_trader_count !== undefined) el7.value = c.aggressive_trader_count;
        }
    } catch (err) {
        console.error('加载配置失败', err);
    }
}

async function applyParticipantConfig() {
    const symbol = document.getElementById('configSymbol').value.trim() || currentSymbol;
    const targetPrice = parseFloat(document.getElementById('configTargetPrice').value);
    const interval = parseFloat(document.getElementById('configInterval').value);
    const mmCount = parseInt(document.getElementById('configMMCount').value);
    const tfCount = parseInt(document.getElementById('configTFCount').value);
    const mrCount = parseInt(document.getElementById('configMRCount').value);
    const ntCount = parseInt(document.getElementById('configNTCount').value);
    const atCount = parseInt(document.getElementById('configATCount').value);

    const body = {
        symbol,
        target_price: isNaN(targetPrice) ? undefined : targetPrice,
        order_interval: isNaN(interval) ? undefined : interval,
        market_maker_count: isNaN(mmCount) ? undefined : mmCount,
        trend_follower_count: isNaN(tfCount) ? undefined : tfCount,
        mean_reversion_count: isNaN(mrCount) ? undefined : mrCount,
        noise_trader_count: isNaN(ntCount) ? undefined : ntCount,
        aggressive_trader_count: isNaN(atCount) ? undefined : atCount,
    };

    try {
        const data = await apiPost('/api/v1/market/participants/config', body);
        const resultEl = document.getElementById('configResult');
        if (data.code === 0) {
            if (resultEl) {
                resultEl.textContent = '配置已应用';
                resultEl.className = 'result success';
                setTimeout(() => { resultEl.className = 'result'; resultEl.textContent = ''; }, 3000);
            }
            log(`参与者配置已更新: ${symbol}`);
        } else {
            if (resultEl) {
                resultEl.textContent = data.message || '配置失败';
                resultEl.className = 'result error';
            }
        }
    } catch (err) {
        console.error('应用配置失败', err);
    }
}

async function refreshParticipants() {
    try {
        const data = await apiGet(`/api/v1/market/participants?symbol=${currentSymbol}`);
        if (data.code !== 0) return;
        const participants = data.data.participants || [];
        const tbody = document.getElementById('participantTableBody');
        if (tbody) {
            if (participants.length === 0) {
                tbody.innerHTML = '<tr><td colspan="6" style="color:#999;padding:20px">暂无参与者</td></tr>';
            } else {
                tbody.innerHTML = participants.map(p => `<tr>
                    <td><strong>${p.participant_id}</strong></td>
                    <td>${p.type}</td>
                    <td><span style="color:${p.active ? '#48bb78' : '#f56565'}">${p.active ? '活跃' : '暂停'}</span></td>
                    <td>${p.orders_sent}</td>
                    <td>${p.trades_executed}</td>
                    <td>${p.pending_orders}</td>
                </tr>`).join('');
            }
        }
    } catch (err) {
        console.error('刷新参与者失败', err);
    }
}

// ─────────── WebSocket ───────────
function connectWebSocket() {
    try {
        ws = new WebSocket(WS_URL);
        ws.onopen = () => {
            log('监控 WebSocket 已连接');
            ws.send(JSON.stringify({ action: 'subscribe', channel: 'market', symbols: [currentSymbol] }));
        };
        ws.onmessage = (event) => {
            const msg = JSON.parse(event.data);
            if (msg.type === 'trade') {
                log(`成交: ${msg.symbol} ${msg.price} x ${msg.quantity} [${msg.side}]`);
                tradeStream.push(msg);
                if (tradeStream.length > 200) tradeStream.shift();
                renderTradeStream();
                refreshStats();
                refreshCrossStats();
            } else if (msg.type === 'quote') {
                currentSymbol = msg.symbol || currentSymbol;
                renderQuoteBook(msg);
                refreshStats();
                refreshCrossStats();
            } else if (msg.type === 'price_history') {
                priceHistory = msg.data || [];
                drawPriceChart();
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
    loadParticipantConfig();
    refreshParticipants();
    connectWebSocket();

    const refreshBtn = document.getElementById('refreshBook');
    if (refreshBtn) refreshBtn.addEventListener('click', () => refreshOrderBook(currentSymbol));

    const symbolInput = document.getElementById('bookSymbolInput');
    if (symbolInput) {
        symbolInput.addEventListener('change', (e) => {
            currentSymbol = e.target.value.trim() || '000001.SZ';
            refreshOrderBook(currentSymbol);
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ action: 'subscribe', channel: 'market', symbols: [currentSymbol] }));
            }
        });
    }

    const applyBtn = document.getElementById('applyConfigBtn');
    if (applyBtn) applyBtn.addEventListener('click', applyParticipantConfig);

    const resetBtn = document.getElementById('resetConfigBtn');
    if (resetBtn) resetBtn.addEventListener('click', loadParticipantConfig);

    const chartSymbolInput = document.getElementById('chartSymbolInput');
    if (chartSymbolInput) {
        chartSymbolInput.addEventListener('change', (e) => {
            const sym = e.target.value.trim() || '000001.SZ';
            document.getElementById('chartSymbol').textContent = sym;
            // 重新加载价格历史
            apiGet(`/api/v1/market/price_history?symbol=${sym}&limit=200`).then(data => {
                if (data.code === 0) {
                    priceHistory = data.data.history || [];
                    drawPriceChart();
                }
            });
        });
    }

    const refreshChartBtn = document.getElementById('refreshChartBtn');
    if (refreshChartBtn) {
        refreshChartBtn.addEventListener('click', () => {
            const sym = document.getElementById('chartSymbolInput').value.trim() || currentSymbol;
            apiGet(`/api/v1/market/price_history?symbol=${sym}&limit=200`).then(data => {
                if (data.code === 0) {
                    priceHistory = data.data.history || [];
                    drawPriceChart();
                }
            });
        });
    }

    // 加载初始价格历史
    apiGet(`/api/v1/market/price_history?symbol=${currentSymbol}&limit=200`).then(data => {
        if (data.code === 0) {
            priceHistory = data.data.history || [];
            drawPriceChart();
        }
    });

    setInterval(() => {
        refreshStats();
        refreshCrossStats();
        refreshParticipants();
    }, 3000);
});
