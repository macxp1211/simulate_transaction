const API_BASE = '';
const WS_URL = `ws://${window.location.host}/ws/v1`;

let ws = null;
let logs = [];

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
                log(`成交: ${msg.symbol} ${msg.price} x ${msg.quantity} [${msg.direction}]`);
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
    connectWebSocket();
    
    setInterval(() => {
        refreshStats();
        refreshCrossStats();
    }, 3000);
});
