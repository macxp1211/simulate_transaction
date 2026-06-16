const API_BASE = '';
const WS_URL = `ws://${window.location.host}/ws/v1`;

let ws = null;
let myOrders = [];
let logs = [];
let currentBookSymbol = '000001.SZ';

function log(msg, type = 'info') {
    const time = new Date().toLocaleTimeString('zh-CN', { hour12: false });
    logs.unshift({ time, msg, type });
    if (logs.length > 100) logs.pop();
    renderLogs();
}

function renderLogs() {
    const el = document.getElementById('logList');
    if (!el) return;
    el.innerHTML = logs.map(l => `<div class="log-item"><span class="time">${l.time}</span> ${l.msg}</div>`).join('');
}

async function apiPost(path, body) {
    const res = await fetch(`${API_BASE}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    return res.json();
}

async function apiGet(path) {
    const res = await fetch(`${API_BASE}${path}`);
    return res.json();
}

async function apiDelete(path) {
    const res = await fetch(`${API_BASE}${path}`, { method: 'DELETE' });
    return res.json();
}

// 提交委托
async function submitOrder(e) {
    e.preventDefault();
    const symbol = document.getElementById('symbol').value.trim();
    const side = document.querySelector('input[name="side"]:checked').value;
    const price = document.getElementById('price').value;
    const quantity = parseInt(document.getElementById('quantity').value);
    const orderType = document.getElementById('orderType').value;

    if (!symbol || !price || quantity <= 0) {
        showResult('请填写完整信息', 'error');
        return;
    }

    const body = { symbol, side, price, quantity, order_type: orderType };
    try {
        const data = await apiPost('/api/v1/orders', body);
        if (data.code === 0) {
            showResult(`委托提交成功: ${data.data.order_id} [${data.data.status}]`, 'success');
            myOrders.unshift(data.data);
            renderOrders();
            log(`提交 ${side} ${symbol} ${price} x${quantity} => ${data.data.status}`);
            refreshOrderBook();
        } else {
            showResult(data.message || '提交失败', 'error');
        }
    } catch (err) {
        showResult('网络错误: ' + err.message, 'error');
    }
}

function showResult(msg, type) {
    const el = document.getElementById('orderResult');
    el.textContent = msg;
    el.className = `result ${type}`;
    setTimeout(() => { el.className = 'result'; el.textContent = ''; }, 5000);
}

// 撤销委托
async function cancelOrder(orderId) {
    try {
        const data = await apiDelete(`/api/v1/orders/${orderId}`);
        if (data.code === 0) {
            log(`撤单成功: ${orderId}`);
            const idx = myOrders.findIndex(o => o.order_id === orderId);
            if (idx >= 0) myOrders[idx] = data.data;
            renderOrders();
        } else {
            log(`撤单失败: ${data.message}`, 'error');
        }
    } catch (err) {
        log(`撤单错误: ${err.message}`, 'error');
    }
}

// 渲染订单列表
function renderOrders() {
    const el = document.getElementById('orderList');
    if (!el) return;
    if (myOrders.length === 0) {
        el.innerHTML = '<div class="order-item">暂无订单</div>';
        return;
    }
    el.innerHTML = myOrders.map(o => {
        const statusClass = o.status || 'pending';
        const qInfo = o.queue_info ? `<br>队列位置: ${o.queue_info.current_queue_position}/${o.queue_info.current_queue_length}` : '';
        const actions = (o.status === 'queued' || o.status === 'partial') 
            ? `<button class="btn-cancel" onclick="cancelOrder('${o.order_id}')">撤单</button>` : '';
        return `<div class="order-item">
            <div class="order-header">
                <span class="order-id">${o.order_id}</span>
                <span class="status ${statusClass}">${o.status}</span>
            </div>
            <div class="details">
                ${o.side === 'buy' ? '买入' : '卖出'} ${o.symbol} ${o.price} x ${o.quantity} (已成交 ${o.filled_qty || 0})${qInfo}
            </div>
            <div class="actions">${actions}</div>
        </div>`;
    }).join('');
}

// 渲染成交记录
function renderTrades(trades) {
    const el = document.getElementById('tradeList');
    if (!el) return;
    if (!trades || trades.length === 0) {
        el.innerHTML = '<div class="trade-item">暂无成交</div>';
        return;
    }
    const myOrderIds = new Set(myOrders.map(o => o.order_id));
    el.innerHTML = trades.map(t => {
        const isMine = myOrderIds.has(t.order_id);
        const mineTag = isMine ? '<span style="color:#ff9800;font-size:11px;margin-left:4px">[我的]</span>' : '';
        const sourceTag = t.match_source === 'order_cross'
            ? '<span style="color:#999;font-size:11px;margin-left:4px">撮合</span>'
            : '<span style="color:#999;font-size:11px;margin-left:4px">行情</span>';
        return `<div class="trade-item">
            <span class="trade-price">${t.price}</span> x <span class="trade-qty">${t.quantity}</span>
            <span style="color:#999">${t.side === 'buy' ? '买入' : '卖出'} ${t.symbol}</span>
            ${mineTag}${sourceTag}
            <span style="float:right;color:#999;font-size:11px">${t.trade_time?.slice(11,19) || ''}</span>
        </div>`;
    }).join('');
}

// 刷新成交记录
async function refreshTrades() {
    try {
        const symbol = document.getElementById('bookSymbolInput').value.trim() || currentBookSymbol;
        const data = await apiGet(`/api/v1/trades?symbol=${symbol}&page_size=50`);
        if (data.code === 0) {
            renderTrades(data.data.trades || []);
        }
    } catch (err) {
        console.error('刷新成交记录失败', err);
    }
}

// 刷新我的订单状态
async function refreshMyOrders() {
    let changed = false;
    const toRemove = [];
    for (let i = 0; i < myOrders.length; i++) {
        const o = myOrders[i];
        if (o.status === 'filled' || o.status === 'cancelled' || o.status === 'rejected') continue;
        try {
            const data = await apiGet(`/api/v1/orders/${o.order_id}`);
            if (data.code === 0 && data.data) {
                myOrders[i] = data.data;
                changed = true;
            } else if (data.code !== 0) {
                // 订单不存在或已失效，标记移除
                toRemove.push(o.order_id);
            }
        } catch (err) {
            console.error('刷新订单状态失败', err);
        }
    }
    if (toRemove.length > 0) {
        myOrders = myOrders.filter(o => !toRemove.includes(o.order_id));
        changed = true;
    }
    if (changed) renderOrders();
}

// 刷新账户信息
async function refreshAccount() {
    try {
        const data = await apiGet('/api/v1/account');
        if (data.code === 0 && data.data) {
            const acc = data.data;
            const cashEl = document.getElementById('accountCash');
            const availEl = document.getElementById('accountAvail');
            const frozenEl = document.getElementById('accountFrozen');
            const feesEl = document.getElementById('accountFees');
            if (cashEl) cashEl.textContent = parseFloat(acc.cash).toFixed(2);
            if (availEl) availEl.textContent = acc.available_position;
            if (frozenEl) frozenEl.textContent = acc.frozen_position;
            if (feesEl) feesEl.textContent = parseFloat(acc.total_fees).toFixed(2);
        }
    } catch (err) {
        console.error('刷新账户信息失败', err);
    }
}

// 刷新订单簿
async function refreshOrderBook() {
    const symbol = document.getElementById('bookSymbolInput').value.trim() || '000001.SZ';
    document.getElementById('bookSymbol').textContent = symbol;
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
    const symbol = quote.symbol || currentBookSymbol;
    currentBookSymbol = symbol;
    document.getElementById('bookSymbol').textContent = symbol;

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

// WebSocket 连接
function connectWebSocket() {
    try {
        ws = new WebSocket(WS_URL);
        ws.onopen = () => {
            log('WebSocket 已连接');
            ws.send(JSON.stringify({ action: 'subscribe', channel: 'market', symbols: ['000001.SZ'] }));
        };
        ws.onmessage = (event) => {
            const msg = JSON.parse(event.data);
            if (msg.type === 'trade') {
                log(`成交: ${msg.symbol} ${msg.price} x ${msg.quantity} [${msg.side}]`);
                refreshTrades();
                refreshMyOrders();
                refreshAccount();
                refreshOrderBook();
            } else if (msg.type === 'quote') {
                // 实时刷新行情订单簿
                renderQuoteBook(msg);
            }
        };
        ws.onclose = () => {
            log('WebSocket 已断开，5秒后重连...', 'warn');
            setTimeout(connectWebSocket, 5000);
        };
        ws.onerror = (err) => {
            log('WebSocket 错误', 'error');
        };
    } catch (err) {
        log('WebSocket 连接失败', 'error');
    }
}

// Tab 切换
document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
        btn.classList.add('active');
        document.getElementById(btn.dataset.tab + 'Tab').classList.add('active');
    });
});

// 初始化
document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('orderForm');
    if (form) form.addEventListener('submit', submitOrder);
    
    const refreshBtn = document.getElementById('refreshBook');
    if (refreshBtn) refreshBtn.addEventListener('click', refreshOrderBook);
    
    const symbolInput = document.getElementById('bookSymbolInput');
    if (symbolInput) symbolInput.addEventListener('change', refreshOrderBook);
    
    refreshOrderBook();
    refreshTrades();
    refreshAccount();
    connectWebSocket();

    // 定时刷新
    setInterval(() => {
        refreshOrderBook();
        refreshTrades();
        refreshMyOrders();
        refreshAccount();
    }, 3000);
});

window.cancelOrder = cancelOrder;
