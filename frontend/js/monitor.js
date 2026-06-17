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
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
}

async function apiPost(path, body) {
    const res = await fetch(`${API_BASE}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
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

    let minPrice = Infinity, maxPrice = -Infinity;
    for (const p of priceHistory) {
        const price = parseFloat(p.price);
        if (price < minPrice) minPrice = price;
        if (price > maxPrice) maxPrice = price;
    }
    const priceRange = maxPrice - minPrice || 1;
    const priceScale = chartH / (priceRange * 1.2);

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

// ─────────── 账户设置 ───────────
async function loadAccountSnapshot() {
    try {
        const data = await apiGet('/api/v1/account');
        if (data.code !== 0) return;
        const acc = data.data || {};
        const el = document.getElementById('accountSnapshot');
        if (el) {
            el.innerHTML = `当前账户: 现金 ${acc.cash || '--'} | 可用仓 ${acc.available_position ?? '--'} | 冻结仓 ${acc.frozen_position ?? '--'} | 今日买入 ${acc.today_bought_position ?? '--'} | 累计费用 ${acc.total_fees || '--'}`;
        }
        const cashInput = document.getElementById('accountInitialCash');
        const posInput = document.getElementById('accountInitialPosition');
        if (cashInput && acc.initial_cash) cashInput.value = acc.initial_cash;
        if (posInput && acc.initial_position !== undefined) posInput.value = acc.initial_position;
    } catch (err) {
        console.error('加载账户快照失败', err);
    }
}

async function resetAccount() {
    const initialCash = document.getElementById('accountInitialCash').value;
    const initialPosition = document.getElementById('accountInitialPosition').value;
    const body = {
        initial_cash: initialCash,
        initial_position: initialPosition === '' ? undefined : parseInt(initialPosition, 10),
    };

    try {
        const data = await apiPost('/api/v1/account/reset', body);
        const resultEl = document.getElementById('accountResetResult');
        if (data.code === 0) {
            if (resultEl) {
                resultEl.textContent = '账户已重置';
                resultEl.className = 'result success';
                setTimeout(() => { resultEl.className = 'result'; resultEl.textContent = ''; }, 3000);
            }
            await loadAccountSnapshot();
            log(`账户已重置: 现金 ${data.data.cash}, 初始持仓 ${data.data.initial_position}`);
        } else {
            if (resultEl) {
                resultEl.textContent = data.message || '重置失败';
                resultEl.className = 'result error';
            }
        }
    } catch (err) {
        console.error('重置账户失败', err);
    }
}

// ─────────── 市场规则 ───────────
async function loadMarketRules() {
    const symbol = document.getElementById('rulesSymbol').value.trim() || currentSymbol;
    try {
        const data = await apiGet(`/api/v1/market/rules/${symbol}`);
        if (data.code !== 0) return;
        const rules = data.data || {};
        const el1 = document.getElementById('rulesPreviousClose');
        const el2 = document.getElementById('rulesMarketType');
        const el3 = document.getElementById('rulesUpperLimit');
        const el4 = document.getElementById('rulesLowerLimit');
        const el5 = document.getElementById('rulesCageUpper');
        const el6 = document.getElementById('rulesCageLower');
        if (el1) el1.value = rules.previous_close || '';
        if (el2) el2.value = rules.market_type || 'main_board';
        if (el3) el3.value = rules.upper_limit || '';
        if (el4) el4.value = rules.lower_limit || '';
        if (el5) el5.value = rules.price_cage_upper || '';
        if (el6) el6.value = rules.price_cage_lower || '';
    } catch (err) {
        console.error('加载市场规则失败', err);
    }
}

async function applyMarketRules() {
    const symbol = document.getElementById('rulesSymbol').value.trim() || currentSymbol;
    const previousClose = document.getElementById('rulesPreviousClose').value;
    const marketType = document.getElementById('rulesMarketType').value;
    try {
        const data = await apiPost(`/api/v1/market/rules/${symbol}`, {
            previous_close: previousClose,
            market_type: marketType,
        });
        const resultEl = document.getElementById('rulesResult');
        if (data.code === 0) {
            if (resultEl) {
                resultEl.textContent = '规则已应用';
                resultEl.className = 'result success';
                setTimeout(() => { resultEl.className = 'result'; resultEl.textContent = ''; }, 3000);
            }
            log(`市场规则已更新: ${symbol}`);
            loadMarketRules();
        } else {
            if (resultEl) {
                resultEl.textContent = data.message || '规则更新失败';
                resultEl.className = 'result error';
            }
        }
    } catch (err) {
        console.error('应用市场规则失败', err);
    }
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
            const el8 = document.getElementById('configALGOCount');
            const el9 = document.getElementById('configSLCount');
            const el10 = document.getElementById('configOBICount');
            const el11 = document.getElementById('configICECount');
            if (el1 && c.target_price) el1.value = c.target_price;
            if (el2 && c.order_interval) el2.value = c.order_interval;
            if (el3 && c.market_maker_count !== undefined) el3.value = c.market_maker_count;
            if (el4 && c.trend_follower_count !== undefined) el4.value = c.trend_follower_count;
            if (el5 && c.mean_reversion_count !== undefined) el5.value = c.mean_reversion_count;
            if (el6 && c.noise_trader_count !== undefined) el6.value = c.noise_trader_count;
            if (el7 && c.aggressive_trader_count !== undefined) el7.value = c.aggressive_trader_count;
            if (el8 && c.algorithmic_trader_count !== undefined) el8.value = c.algorithmic_trader_count;
            if (el9 && c.stop_loss_trader_count !== undefined) el9.value = c.stop_loss_trader_count;
            if (el10 && c.order_book_imbalance_count !== undefined) el10.value = c.order_book_imbalance_count;
            if (el11 && c.iceberg_participant_count !== undefined) el11.value = c.iceberg_participant_count;
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
    const algoCount = parseInt(document.getElementById('configALGOCount').value);
    const slCount = parseInt(document.getElementById('configSLCount').value);
    const obiCount = parseInt(document.getElementById('configOBICount').value);
    const iceCount = parseInt(document.getElementById('configICECount').value);

    const body = {
        symbol,
        target_price: isNaN(targetPrice) ? undefined : targetPrice,
        order_interval: isNaN(interval) ? undefined : interval,
        market_maker_count: isNaN(mmCount) ? undefined : mmCount,
        trend_follower_count: isNaN(tfCount) ? undefined : tfCount,
        mean_reversion_count: isNaN(mrCount) ? undefined : mrCount,
        noise_trader_count: isNaN(ntCount) ? undefined : ntCount,
        aggressive_trader_count: isNaN(atCount) ? undefined : atCount,
        algorithmic_trader_count: isNaN(algoCount) ? undefined : algoCount,
        stop_loss_trader_count: isNaN(slCount) ? undefined : slCount,
        order_book_imbalance_count: isNaN(obiCount) ? undefined : obiCount,
        iceberg_participant_count: isNaN(iceCount) ? undefined : iceCount,
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

// ─────────── 参与者 P&L ───────────
async function refreshParticipantsPnl() {
    try {
        const data = await apiGet(`/api/v1/analytics/participants/pnl?symbol=${currentSymbol}`);
        if (data.code !== 0) return;
        const participants = data.data.participants || [];
        const tbody = document.getElementById('pnlTableBody');
        if (tbody) {
            if (participants.length === 0) {
                tbody.innerHTML = '<tr><td colspan="8" style="color:#999;padding:20px">暂无数据</td></tr>';
            } else {
                tbody.innerHTML = participants.map((p, i) => {
                    const pnlColor = p.pnl > 0 ? '#48bb78' : (p.pnl < 0 ? '#f56565' : '#718096');
                    return `<tr>
                        <td><strong>${i + 1}</strong></td>
                        <td><strong>${p.participant_id}</strong></td>
                        <td>${p.type}</td>
                        <td>${p.cash?.toFixed(2) ?? '--'}</td>
                        <td>${p.position ?? '--'}</td>
                        <td style="color:${pnlColor};font-weight:600">${p.pnl?.toFixed(2) ?? '--'}</td>
                        <td>${p.total_trades ?? '--'}</td>
                        <td>${p.total_fees?.toFixed(2) ?? '--'}</td>
                    </tr>`;
                }).join('');
            }
        }
    } catch (err) {
        console.error('刷新 P&L 失败', err);
    }
}

// ─────────── 订单流分析 ───────────
async function refreshOrderFlow(symbol) {
    symbol = symbol || currentSymbol;
    try {
        const data = await apiGet(`/api/v1/analytics/order_flow/${symbol}`);
        if (data.code !== 0) return;
        const flow = data.data || {};
        const el1 = document.getElementById('flowBidDepth');
        const el2 = document.getElementById('flowAskDepth');
        const el3 = document.getElementById('flowImbalance');
        const el4 = document.getElementById('flowSpread');
        if (el1) el1.textContent = flow.bid_depth ?? '--';
        if (el2) el2.textContent = flow.ask_depth ?? '--';
        if (el3) el3.textContent = flow.imbalance !== undefined ? flow.imbalance.toFixed(4) : '--';
        if (el4) el4.textContent = flow.spread ?? '--';

        // 更新不平衡条
        const fill = document.getElementById('imbalanceFill');
        if (fill && flow.imbalance !== undefined) {
            const pct = 50 + flow.imbalance * 50;  // [-1,1] -> [0,100]
            fill.style.width = `${Math.max(0, Math.min(100, pct))}%`;
            fill.style.background = flow.imbalance > 0 ? '#48bb78' : (flow.imbalance < 0 ? '#f56565' : '#4299e1');
        }
    } catch (err) {
        console.error('刷新订单流失败', err);
    }
}

// ─────────── 深度图 ───────────
async function refreshDepthChart(symbol) {
    symbol = symbol || currentSymbol;
    try {
        const data = await apiGet(`/api/v1/analytics/depth/${symbol}`);
        if (data.code !== 0) return;
        drawDepthChart(data.data);
    } catch (err) {
        console.error('刷新深度图失败', err);
    }
}

function drawDepthChart(data) {
    const canvas = document.getElementById('depthChart');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const width = canvas.width;
    const height = canvas.height;
    const padding = { top: 20, bottom: 30, left: 50, right: 50 };
    const chartW = width - padding.left - padding.right;
    const chartH = height - padding.top - padding.bottom;

    ctx.clearRect(0, 0, width, height);

    const bidDepths = data.bid_depths || [];
    const askDepths = data.ask_depths || [];
    if (bidDepths.length === 0 && askDepths.length === 0) return;

    // 找出最大累积深度和价格范围
    let maxDepth = 0;
    let minPrice = Infinity, maxPrice = -Infinity;
    bidDepths.forEach(d => {
        maxDepth = Math.max(maxDepth, d.cumulative);
        minPrice = Math.min(minPrice, parseFloat(d.price));
    });
    askDepths.forEach(d => {
        maxDepth = Math.max(maxDepth, d.cumulative);
        maxPrice = Math.max(maxPrice, parseFloat(d.price));
    });

    if (maxDepth === 0 || minPrice === Infinity) return;
    const depthScale = chartH / maxDepth;
    const priceRange = maxPrice - minPrice || 1;
    const priceScale = chartW / priceRange;

    // 绘制买盘深度（绿色，从右到左）
    ctx.fillStyle = 'rgba(72, 187, 120, 0.3)';
    ctx.strokeStyle = '#48bb78';
    ctx.lineWidth = 2;
    ctx.beginPath();
    const midX = padding.left + (parseFloat(data.best_bid || minPrice) - minPrice) * priceScale;
    ctx.moveTo(midX, padding.top + chartH);
    for (let i = 0; i < bidDepths.length; i++) {
        const x = padding.left + (parseFloat(bidDepths[i].price) - minPrice) * priceScale;
        const y = padding.top + chartH - bidDepths[i].cumulative * depthScale;
        ctx.lineTo(x, y);
    }
    ctx.lineTo(padding.left, padding.top + chartH - bidDepths[bidDepths.length - 1]?.cumulative * depthScale || 0);
    ctx.lineTo(padding.left, padding.top + chartH);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();

    // 绘制卖盘深度（红色，从左到右）
    ctx.fillStyle = 'rgba(245, 101, 101, 0.3)';
    ctx.strokeStyle = '#f56565';
    ctx.beginPath();
    const askStartX = padding.left + (parseFloat(data.best_ask || maxPrice) - minPrice) * priceScale;
    ctx.moveTo(askStartX, padding.top + chartH);
    for (let i = 0; i < askDepths.length; i++) {
        const x = padding.left + (parseFloat(askDepths[i].price) - minPrice) * priceScale;
        const y = padding.top + chartH - askDepths[i].cumulative * depthScale;
        ctx.lineTo(x, y);
    }
    ctx.lineTo(width - padding.right, padding.top + chartH - askDepths[askDepths.length - 1]?.cumulative * depthScale || 0);
    ctx.lineTo(width - padding.right, padding.top + chartH);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();

    // 绘制价格轴
    ctx.fillStyle = '#1a202c';
    ctx.font = '10px sans-serif';
    ctx.textAlign = 'center';
    const steps = 5;
    for (let i = 0; i <= steps; i++) {
        const price = minPrice + priceRange * (i / steps);
        const x = padding.left + (i / steps) * chartW;
        ctx.fillText(price.toFixed(2), x, height - 5);
    }

    // 标注最新价
    if (data.best_bid && data.best_ask) {
        const midPrice = (parseFloat(data.best_bid) + parseFloat(data.best_ask)) / 2;
        const midX = padding.left + (midPrice - minPrice) * priceScale;
        ctx.fillStyle = '#1a202c';
        ctx.font = 'bold 12px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText(`最新: ${midPrice.toFixed(2)}`, midX, padding.top - 5);
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
            let msg;
            try {
                msg = JSON.parse(event.data);
            } catch (e) {
                console.error('WebSocket 消息解析失败', event.data);
                return;
            }
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
            } else if (msg.type === 'price_history_delta') {
                const incoming = msg.data || [];
                priceHistory = priceHistory.concat(incoming);
                const maxHistory = 300;
                if (priceHistory.length > maxHistory) {
                    priceHistory = priceHistory.slice(-maxHistory);
                }
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
    loadAccountSnapshot();
    loadMarketRules();
    loadParticipantConfig();
    refreshParticipants();
    refreshParticipantsPnl();
    refreshOrderFlow(currentSymbol);
    refreshDepthChart(currentSymbol);
    connectWebSocket();

    const refreshBtn = document.getElementById('refreshBook');
    if (refreshBtn) refreshBtn.addEventListener('click', () => refreshOrderBook(currentSymbol));

    const symbolInput = document.getElementById('bookSymbolInput');
    if (symbolInput) {
        symbolInput.addEventListener('change', (e) => {
            const oldSymbol = currentSymbol;
            currentSymbol = e.target.value.trim() || '000001.SZ';
            refreshOrderBook(currentSymbol);
            refreshOrderFlow(currentSymbol);
            refreshDepthChart(currentSymbol);
            document.getElementById('flowSymbol').textContent = currentSymbol;
            document.getElementById('depthSymbol').textContent = currentSymbol;
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ action: 'unsubscribe', channel: 'market', symbols: [oldSymbol] }));
                ws.send(JSON.stringify({ action: 'subscribe', channel: 'market', symbols: [currentSymbol] }));
            }
        });
    }

    const applyBtn = document.getElementById('applyConfigBtn');
    if (applyBtn) applyBtn.addEventListener('click', applyParticipantConfig);

    const resetBtn = document.getElementById('resetConfigBtn');
    if (resetBtn) resetBtn.addEventListener('click', loadParticipantConfig);

    const resetAccountBtn = document.getElementById('resetAccountBtn');
    if (resetAccountBtn) resetAccountBtn.addEventListener('click', resetAccount);

    // 市场规则
    const applyRulesBtn = document.getElementById('applyRulesBtn');
    if (applyRulesBtn) applyRulesBtn.addEventListener('click', applyMarketRules);

    const refreshRulesBtn = document.getElementById('refreshRulesBtn');
    if (refreshRulesBtn) refreshRulesBtn.addEventListener('click', loadMarketRules);

    const rulesSymbolInput = document.getElementById('rulesSymbol');
    if (rulesSymbolInput) {
        rulesSymbolInput.addEventListener('change', loadMarketRules);
    }

    // 走势图
    const chartSymbolInput = document.getElementById('chartSymbolInput');
    if (chartSymbolInput) {
        chartSymbolInput.addEventListener('change', (e) => {
            const sym = e.target.value.trim() || '000001.SZ';
            document.getElementById('chartSymbol').textContent = sym;
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

    // 订单流
    const flowSymbolInput = document.getElementById('flowSymbolInput');
    if (flowSymbolInput) {
        flowSymbolInput.addEventListener('change', (e) => {
            refreshOrderFlow(e.target.value.trim() || currentSymbol);
        });
    }

    const refreshFlowBtn = document.getElementById('refreshFlowBtn');
    if (refreshFlowBtn) {
        refreshFlowBtn.addEventListener('click', () => {
            refreshOrderFlow(document.getElementById('flowSymbolInput').value.trim() || currentSymbol);
        });
    }

    // 深度图
    const depthSymbolInput = document.getElementById('depthSymbolInput');
    if (depthSymbolInput) {
        depthSymbolInput.addEventListener('change', (e) => {
            refreshDepthChart(e.target.value.trim() || currentSymbol);
        });
    }

    const refreshDepthBtn = document.getElementById('refreshDepthBtn');
    if (refreshDepthBtn) {
        refreshDepthBtn.addEventListener('click', () => {
            refreshDepthChart(document.getElementById('depthSymbolInput').value.trim() || currentSymbol);
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
        refreshParticipantsPnl();
        refreshOrderFlow(currentSymbol);
        refreshDepthChart(currentSymbol);
        loadAccountSnapshot();
    }, 3000);
});
