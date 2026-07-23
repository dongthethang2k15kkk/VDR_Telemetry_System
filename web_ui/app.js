const CONFIG = {
    WS_URL: `ws://${location.hostname}:8080/ws/telemetry`, 
    API_URL: `http://${location.hostname}:8080/api`,       
    STREAM_URL: `http://${location.hostname}:8080/stream/camera`,
    MAX_RECONNECT_ATTEMPTS: 5,
    RECONNECT_DELAY_MS: 3000,
    UPDATE_HZ: 1
};

let state = {
    mode: 'live',
    isConnected: false,
    reconnectAttempts: 0,
    hz: CONFIG.UPDATE_HZ
};

// ============================================================
// AUTH: session token (sessionStorage) + wrapper fetch co gan token
// ============================================================
function getAuthToken() {
    return sessionStorage.getItem('vdr_token');
}

function showLoginOverlay() {
    const overlay = document.getElementById('loginOverlay');
    if (!overlay) return;
    overlay.style.display = 'flex';
    overlay.style.opacity = '1';
}

async function apiFetch(path, opts) {
    opts = opts || {};
    const headers = Object.assign({}, opts.headers || {});
    const token = getAuthToken();
    if (token) headers['Authorization'] = 'Bearer ' + token;
    const res = await fetch(`${CONFIG.API_URL}${path}`, Object.assign({}, opts, { headers }));
    if (res.status === 401) {
        sessionStorage.removeItem('vdr_token');
        showLoginOverlay();
    }
    return res;
}

// Cho /stream/camera va /api/evidence/{file} - hai cho nay gan thang vao
// <img src>/<video src>, trinh duyet tu tai, khong gan duoc header Authorization.
// Xin chu ky han ngan qua route co bao ve, roi gan URL da ky vao src.
async function getSignedUrl(path) {
    const res = await apiFetch('/media/sign', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path })
    });
    if (!res.ok) return null;
    const j = await res.json();
    const base = CONFIG.API_URL.replace(/\/api$/, '');
    return `${base}${j.path}?exp=${j.exp}&sig=${j.sig}`;
}

function formatHudTime(unixTimestamp) {
    if (!unixTimestamp) return "-- Chưa có dữ liệu --";
    const date = new Date(unixTimestamp * 1000);
    const pad = n => String(n).padStart(2, "0");
    const ms  = String(date.getMilliseconds()).padStart(3, "0");
    return `${date.getFullYear()}-${pad(date.getMonth()+1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}.${ms}`;

}
// ============================================================
// MODULE 2: WebSocket Manager
// ============================================================
let ws = null;

function connectWebSocket() {
    if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) return;

    UIController.setConnectionStatus('connecting');

    try {
        ws = new WebSocket(CONFIG.WS_URL);

        ws.onopen = () => {
            console.log("WebSocket connected");
            ws.send(JSON.stringify({ type: 'auth', token: getAuthToken() }));
            state.isConnected = true;
            state.reconnectAttempts = 0;
            UIController.setConnectionStatus('connected');
            IncidentLogManager.fetchUnresolvedAlerts(); // Tự động kéo log cảnh báo khi kết nối
        };

        ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                processPayload(data);
            } catch (err) {
                console.warn("Malformed JSON payload:", event.data);
            }
        };

        ws.onclose = (event) => {
            console.log("WebSocket disconnected");
            state.isConnected = false;
            if (event.code === 4401) {
                sessionStorage.removeItem('vdr_token');
                showLoginOverlay();
                return;   // khong tu dong reconnect
            }
            handleDisconnect();
        };

        ws.onerror = (err) => {
            console.error("WebSocket error:", err);
        };
    } catch (err) {
        console.error("Error creating WebSocket:", err);
        handleDisconnect();
    }
}

function disconnectWebSocket() {
    if (ws) {
        ws.close();
        ws = null;
    }
    state.isConnected = false;
    UIController.setConnectionStatus('disconnected');
    UIController.hideDisconnectOverlay();
}

function handleDisconnect() {
    UIController.setConnectionStatus('disconnected');
    if (state.reconnectAttempts < CONFIG.MAX_RECONNECT_ATTEMPTS) {
        state.reconnectAttempts++;
        UIController.showDisconnectOverlay(state.reconnectAttempts);
        setTimeout(() => {
            connectWebSocket();
        }, CONFIG.RECONNECT_DELAY_MS);
    } else {
        UIController.showDisconnectOverlay('max');
    }
}

function sendCommand(cmdObj) {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(cmdObj));
    }
}

// Xử lý dữ liệu chuẩn từ FastAPI
function processPayload(data) {
    if (!data || !data.telemetry) return;

    // Mapping key từ DB ra
    const speed = data.telemetry['Vehicle Speed'] || 0;
    const rpm = data.telemetry['Engine RPM'] || 0;
    const throttle = data.telemetry['Throttle Position'] || 0; 
    const temp = data.telemetry['Coolant Temp'] || 0;

    const speedStr = Math.round(speed).toString().padStart(3, '0');
    document.getElementById('hudSpeed').textContent = speedStr;
    document.getElementById('hudTimestamp').textContent = formatHudTime(data.timestamp);


    const telemetryPack = { speed_kmh: speed, rpm: rpm, brake_pedal: throttle };
    GaugeRenderer.update(telemetryPack);
    HistoryChartRenderer.update(data.timestamp, telemetryPack);
    

    if (data.latest_alert) {
        IncidentLogManager.handleRealtimeAlert(data.latest_alert, data.timestamp, speed);
    }
}

// ============================================================
// MODULE 3: Gauge & Chart Renderer (ECharts)
// ============================================================
const HistoryChartRenderer = (function() {
    let chart;
    const MAX_POINTS = 300;
    const labels = [], speedData = [], rpmData = [], brakeData = [];

    function init() {
        chart = echarts.init(document.getElementById('historyChart'), null, { renderer: 'canvas' });

        const option = {
            animation: false,
            backgroundColor: 'transparent',
            tooltip: {
                trigger: 'axis',
                backgroundColor: '#3b2412',
                borderColor: '#8a5330',
                textStyle: { color: '#ffe9b8', fontFamily: "'Geist Mono', monospace", fontSize: 11 }
            },
            legend: {
                data: ['Speed', 'Throttle', 'RPM'],
                textStyle: { color: '#5f3d20', fontSize: 10, fontFamily: "'Geist Mono', monospace" },
                top: 0, right: 0,
                icon: 'circle',
                itemWidth: 7,
                itemHeight: 7
            },
            grid: { left: '3%', right: '4%', bottom: '2%', top: '24px', containLabel: true },
            xAxis: {
                type: 'category',
                data: labels,
                axisLabel: { show: false },
                axisLine: { lineStyle: { color: 'rgba(92,51,23,0.45)' } },
                splitLine: { show: false }
            },
            yAxis: [
                {
                    type: 'value', min: 0, max: 150,
                    position: 'left',
                    splitLine: { lineStyle: { color: 'rgba(92,51,23,0.30)', type: 'dashed' } },
                    axisLabel: { color: '#5f3d20', fontSize: 10, fontFamily: "'Geist Mono', monospace" }
                },
                {
                    type: 'value', min: 0, max: 8000,
                    position: 'right',
                    splitLine: { show: false },
                    axisLabel: { color: '#5f3d20', fontSize: 10, fontFamily: "'Geist Mono', monospace" }
                }
            ],
            series: [
                { name: 'Speed', type: 'line', data: speedData, showSymbol: false,
                  itemStyle: { color: '#5cc12e' }, lineStyle: { width: 1.5 },
                  areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
                    colorStops: [{ offset: 0, color: 'rgba(92,193,46,0.16)' }, { offset: 1, color: 'rgba(92,193,46,0)' }]
                  }}
                },
                { name: 'Throttle', type: 'line', data: brakeData, showSymbol: false,
                  itemStyle: { color: '#e8402a' }, lineStyle: { width: 1.5 }
                },
                { name: 'RPM', type: 'line', yAxisIndex: 1, data: rpmData, showSymbol: false,
                  itemStyle: { color: '#2f9de0' }, lineStyle: { width: 1, type: 'dashed', opacity: 0.6 }
                }
            ]
        };
        chart.setOption(option);
        window.addEventListener('resize', () => chart.resize());
    }

    function update(timestamp, telemetry) {
        labels.push(formatHudTime(timestamp));
        speedData.push(telemetry.speed_kmh);
        rpmData.push(telemetry.rpm);
        brakeData.push(telemetry.brake_pedal);

        if (labels.length > MAX_POINTS) {
            labels.shift(); speedData.shift(); rpmData.shift(); brakeData.shift();
        }

        chart.setOption({
            xAxis: { data: labels },
            series: [{ data: speedData }, { data: brakeData }, { data: rpmData }]
        });
    }

    return { init, update };
})();

const GaugeRenderer = (function() {
    let speedChart, rpmChart, brakeChart;

    function getGaugeOption(min, max, color, unit) {
        return {
            animationDurationUpdate: 80,
            backgroundColor: 'transparent',
            series: [{
                type: 'gauge',
                center: ['50%', '72%'],
                radius: '108%',
                startAngle: 200,
                endAngle: -20,
                min, max,
                splitNumber: 4,
                itemStyle: { color },
                progress: { show: true, width: 6, roundCap: true },
                pointer: { show: true, length: '62%', width: 3, itemStyle: { color } },
                axisLine: { roundCap: true, lineStyle: { width: 6, color: [[1, 'rgba(70,35,10,0.25)']] } },
                axisTick: { show: false },
                splitLine: { length: 8, lineStyle: { width: 1.5, color: '#8a5330' } },
                axisLabel: { distance: 16, color: '#5f3d20', fontSize: 9,
                             fontFamily: "'Geist Mono', monospace" },
                title: { show: false },
                detail: {
                    valueAnimation: true,
                    offsetCenter: [0, '-18%'],
                    fontSize: 20,
                    fontWeight: 700,
                    fontFamily: "'Geist Mono', monospace",
                    color: '#3b2412',
                    formatter: `{value}${unit}`
                },
                data: [{ value: 0 }]
            }]
        };
    }

    function init() {
        speedChart = echarts.init(document.getElementById('speedGauge'), null, { renderer: 'canvas' });
        rpmChart   = echarts.init(document.getElementById('rpmGauge'),   null, { renderer: 'canvas' });
        brakeChart = echarts.init(document.getElementById('brakeGauge'), null, { renderer: 'canvas' });

        speedChart.setOption(getGaugeOption(0, 150,  '#5cc12e', ''));
        rpmChart.setOption(  getGaugeOption(0, 8000, '#2f9de0', ''));
        brakeChart.setOption(getGaugeOption(0, 100,  '#e8402a', '%'));

        window.addEventListener('resize', () => {
            speedChart.resize(); rpmChart.resize(); brakeChart.resize();
        });
    }

    let isBrakeCritical = false;

    function update(telemetry) {
        speedChart.setOption({ series: [{ data: [{ value: Math.round(telemetry.speed_kmh) }] }] });
        rpmChart.setOption(  { series: [{ data: [{ value: Math.round(telemetry.rpm) }] }] });

        const brakeVal = Math.round(telemetry.brake_pedal);
        const el = document.getElementById('brakeGauge');

        if (brakeVal > 80 && !isBrakeCritical) {
            isBrakeCritical = true;
            brakeChart.setOption({ series: [{ itemStyle: { color: '#ff5a2e' }, progress: { itemStyle: { color: '#ff5a2e' } } }] });
            el.classList.add('gauge-pulse');
        } else if (brakeVal <= 80 && isBrakeCritical) {
            isBrakeCritical = false;
            brakeChart.setOption({ series: [{ itemStyle: { color: '#e8402a' }, progress: { itemStyle: { color: '#e8402a' } } }] });
            el.classList.remove('gauge-pulse');
        }

        brakeChart.setOption({ series: [{ data: [{ value: brakeVal }] }] });
    }

    return { init, update };
})();

// ============================================================
// MODULE 4: Incident Log Manager (Đã nối API bảo dưỡng)
// ============================================================
const IncidentLogManager = (function() {
    let logData = [];
    let handledAlertIds = new Set();
    const listEl  = document.getElementById('incidentList');
    const countEl = document.getElementById('eventCount');
    const emptyEl = document.getElementById('logEmpty');

    // 1. Lấy toàn bộ danh sách cảnh báo từ API (Lúc mở trang)
    async function fetchUnresolvedAlerts() {
        try {
            const res = await apiFetch(`/alerts`);
            const json = await res.json();
            if (json.status === 'success') {
                listEl.innerHTML = '';
                logData = [];
                handledAlertIds.clear();
                
                json.data.forEach(alert => {
                    renderAlert(alert);
                });
            }
        } catch(e) { console.warn("Lỗi kéo dữ liệu cảnh báo:", e); }
    }

    // 2. Gọi API để xác nhận "Đã bảo trì"
    async function resolveAlert(id, event) {
        event.stopPropagation(); // Ngăn kích hoạt logic bấm vào list để xem video
        try {
            const res = await apiFetch(`/alerts/${id}/resolve`, { method: 'PUT' });
            if (res.ok) {
                UIController.showToast(`Đã xác nhận bảo trì (ID: ${id})`);
                const row = document.getElementById(`alert-row-${id}`);
                if (row) {
                    row.style.transform = 'translateY(10px)';
                    row.style.opacity = '0';
                    setTimeout(() => {
                        row.remove();
                        handledAlertIds.delete(id);
                        updateCountUI();
                    }, 300);
                }
            }
        } catch(e) { console.error("Lỗi xác nhận cảnh báo:", e); }
    }

    // 3. Xử lý alert bắn về theo thời gian thực từ WS
    function handleRealtimeAlert(alertObj, timestamp, speed) {
        if (!alertObj || handledAlertIds.has(alertObj.id)) return;
        renderAlert(alertObj, timestamp, speed);
    }

    function determineSeverityAndBadge(type) {
        if (type.includes('HIGH')) return { iconClass: 'icon-critical', badgeClass: 'badge-red',   label: 'CRITICAL' };
        if (type.includes('WARN')) return { iconClass: 'icon-warning',  badgeClass: 'badge-amber', label: 'WARNING'  };
        return                            { iconClass: 'icon-info',     badgeClass: 'badge-blue',  label: 'INFO'     };
    }

    function updateCountUI() {
        const count = listEl.children.length;
        countEl.textContent = count;
        document.getElementById('infoEvents').textContent = count;
        if (count === 0) emptyEl.style.display = '';
        else emptyEl.style.display = 'none';
    }

    function renderAlert(alertObj, timestamp = null, speed = null) {
        emptyEl.style.display = 'none';
        handledAlertIds.add(alertObj.id);

        const timeStr = timestamp ? formatHudTime(timestamp) : formatHudTime(alertObj.timestamp_sec);
        const speedStr = speed ? Math.round(speed).toString().padStart(3, '0') + ' km/h' : '-- km/h';
        const styleInfo = determineSeverityAndBadge(alertObj.alert_type);

        logData.unshift({ raw: alertObj });

        let iconHtml = '';
        if (styleInfo.iconClass === 'icon-critical') {
            iconHtml = `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" class="row-icon ${styleInfo.iconClass}"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`;
        } else if (styleInfo.iconClass === 'icon-info') {
            iconHtml = `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="row-icon ${styleInfo.iconClass}"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>`;
        } else {
            iconHtml = `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="row-icon ${styleInfo.iconClass}"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`;
        }

        const li = document.createElement('li');
        li.className = 'incident-row';
        li.id = `alert-row-${alertObj.id}`;
        li.innerHTML = `
            ${iconHtml}
            <span class="row-time">${timeStr}</span>
            <span class="row-badge ${styleInfo.badgeClass}">${styleInfo.label}</span>
            <span class="row-desc" title="${alertObj.description}">${alertObj.description}</span>
            <button class="btn btn-ghost" style="padding: 2px 8px; font-size: 10px;" onclick="IncidentLogManager.resolveAlert(${alertObj.id}, event)">XONG</button>
        `;

        li.onclick = (e) => {
            document.querySelectorAll('.incident-row').forEach(el => el.classList.remove('active'));
            li.classList.add('active');
            VideoController.seekVideo(alertObj.timestamp_sec);
            sendCommand({ command: "seek", timestamp: alertObj.timestamp_sec });
            UIController.showToast(`Đang trích xuất dữ liệu camera tại thời điểm sự cố`);
        };

        listEl.insertAdjacentElement('afterbegin', li);
        updateCountUI();
    }

    function clearLog() {
        if (confirm("Chức năng này đã bị khóa ở Database để đảm bảo an toàn truy xuất. Bạn cần nhấn XONG từng dòng.")) {
            return;
        }
    }

    function exportCsv() {
        if (logData.length === 0) { alert("No data to export."); return; }
        let csv = `data:text/csv;charset=utf-8,ID,Timestamp,Time,Type,Description\n`;
        logData.forEach(row => {
            csv += `${row.raw.id},${row.raw.timestamp_sec},${formatHudTime(row.raw.timestamp_sec)},${row.raw.alert_type},"${row.raw.description}"\n`;
        });
        const link = document.createElement("a");
        link.href = encodeURI(csv);
        link.download = `vdr_maintenance_log_${Date.now()}.csv`;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
    }

    return { fetchUnresolvedAlerts, resolveAlert, handleRealtimeAlert, clearLog, exportCsv };
})();

// ============================================================
// MODULE 5: Video Controller
// ============================================================
const VideoController = (function() {
    let player = null;
    const videoEl     = document.getElementById('videoElement');
    const placeholder = document.getElementById('videoPlaceholder');

    function init() {
    const img = document.createElement('img');
    img.style.cssText = 'width:100%;height:100%;object-fit:contain;position:absolute;inset:0;z-index:2;';
    img.onload  = () => { placeholder.classList.add('hidden'); img.style.visibility = 'visible'; };
    img.onerror = () => { placeholder.classList.remove('hidden'); img.style.visibility = 'hidden'; };
    videoEl.replaceWith(img);
    getSignedUrl('/stream/camera').then(u => { if (u) img.src = u; });
}
    function seekVideo(unixTimestamp) {
        if (player) console.log("Seeking video to unix:", unixTimestamp);
    }

    return { init, seekVideo };
})();

// ============================================================
// MODULE 6: UI Controller
// ============================================================
const UIController = (function() {

    function init() {
        document.getElementById('infoWsUrl').textContent = CONFIG.WS_URL.replace('ws://', '');

        document.getElementById('btnConnect').addEventListener('click', () => {
            connectWebSocket(); // Simulator được xử lý trực tiếp trên Backend, luôn chạy logic connect
        });

        document.getElementById('btnDisconnect').addEventListener('click', () => {
            disconnectWebSocket();
        });

        document.getElementById('btnClear').addEventListener('click',  IncidentLogManager.clearLog);
        document.getElementById('btnExport').addEventListener('click', IncidentLogManager.exportCsv);
    }

    function setConnectionStatus(status) {
        const textEl = document.getElementById('wsStatusText');
        const ledEl  = document.getElementById('wsStatusLed');
        ledEl.className = 'status-led';

        if (status === 'connected') {
            textEl.textContent = 'CONNECTED';
            ledEl.classList.add('led-online');
        } else if (status === 'disconnected') {
            textEl.textContent = 'DISCONNECTED';
            ledEl.classList.add('led-offline');
        } else if (status === 'connecting') {
            textEl.textContent = 'CONNECTING...';
            ledEl.classList.add('led-connecting', 'pulse');
        }
    }

    function showDisconnectOverlay(attemptCount) {
        const overlay = document.getElementById('disconnectOverlay');
        const text    = document.getElementById('reconnectText');
        text.textContent = attemptCount === 'max'
            ? 'CONNECTION LOST — Manual reconnect required'
            : `CONNECTION LOST — Retry ${attemptCount}/${CONFIG.MAX_RECONNECT_ATTEMPTS}`;
        overlay.classList.remove('hidden');
    }

    function hideDisconnectOverlay() {
        document.getElementById('disconnectOverlay').classList.add('hidden');
    }

    function showToast(message) {
        const container = document.getElementById('toastContainer');
        const toast = document.createElement('div');
        toast.className = 'toast';
        toast.innerHTML = `
            <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--green)" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/>
            </svg>
            ${message}`;
        container.appendChild(toast);
        setTimeout(() => {
            toast.classList.add('hide');
            setTimeout(() => toast.remove(), 300);
        }, 3000);
    }


    return { init, setConnectionStatus, showDisconnectOverlay, hideDisconnectOverlay, showToast };
})();

// ============================================================
// BOOTSTRAP
// ============================================================

// ============================================================
// MODULE 7: Maintenance History
// ============================================================
const MaintHistoryManager = (function() {
    const overlay  = () => document.getElementById('maintHistoryOverlay');
    const listEl   = () => document.getElementById('maintHistoryList');
    const emptyEl  = () => document.getElementById('maintHistoryEmpty');

    function open() {
        overlay().classList.remove('hidden');
        apiFetch(`/maintenance/history`)
            .then(r => r.json())
            .then(res => render(res.data || []))
            .catch(() => render([]));
    }

    function close() {
        overlay().classList.add('hidden');
    }

    function render(rows) {
        const list  = listEl();
        const empty = emptyEl();
        list.innerHTML = '';
        if (!rows.length) {
            empty.classList.remove('hidden');
            return;
        }
        empty.classList.add('hidden');
        rows.forEach(row => {
            const date = new Date(row.timestamp_sec * 1000);
            const dateStr = date.toLocaleDateString('vi-VN', { day:'2-digit', month:'2-digit', year:'numeric' });
            const li = document.createElement('li');
            li.className = 'maint-history-row';
            // Task5d: hien note neu co
            const noteHtml = row.note ? `<div class="maint-history-note"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg> ${row.note}</div>` : '';
            li.innerHTML = `
                <span class="maint-history-item">${row.item.replace(/_/g,' ')}</span>
                <div class="maint-history-meta">
                    <span class="maint-history-km">${Math.round(row.km_at_service).toLocaleString()} km</span>
                    <span class="maint-history-date">${dateStr}</span>
                </div>
                ${noteHtml}`;
            list.appendChild(li);
        });
    }

    function init() {
        document.getElementById('btnMaintHistory').addEventListener('click', open);
        document.getElementById('btnCloseMaintHistory').addEventListener('click', close);
        overlay().addEventListener('click', (e) => {
            if (e.target === overlay()) close();
        });
    }

    return { init };
})();


// ============================================================
// Task2e: DTC Scanner
// ============================================================
const DTCScanner = (function() {
    const listEl = () => document.getElementById('dtcList');
    const btn = () => document.getElementById('btnDtcScan');

    // P=vang, C=xanh, B=cam, U=do
    function badgeClass(code) {
        const t = (code || '')[0];
        return t === 'P' ? 'dtc-p' : t === 'C' ? 'dtc-c' : t === 'B' ? 'dtc-b' : t === 'U' ? 'dtc-u' : 'dtc-p';
    }

    function renderList(items, emptyMsg) {
        const el = listEl();
        if (!el) return;
        el.innerHTML = '';
        if (!items.length) {
            el.innerHTML = `<li class="dtc-empty"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--green)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg> ${emptyMsg || 'Không có mã lỗi'}</li>`;
            return;
        }
        items.forEach(it => {
            const li = document.createElement('li');
            li.className = 'dtc-row';
            const cleared = it.is_cleared ? ' dtc-cleared' : '';
            const btnHtml = it.id && !it.is_cleared
                ? `<button class="btn btn-ghost dtc-clear-btn" data-id="${it.id}">Đã xử lý</button>` : '';
            li.innerHTML = `
                <div class="dtc-info${cleared}">
                    <span class="dtc-badge ${badgeClass(it.code || it.dtc_code)}">${it.code || it.dtc_code}</span>
                    <span class="dtc-desc">${it.description}</span>
                </div>
                ${btnHtml}`;
            const b = li.querySelector('.dtc-clear-btn');
            if (b) b.addEventListener('click', () => clearDtc(b.dataset.id));
            el.appendChild(li);
        });
    }

    async function scan() {
        const b = btn();
        if (b) { b.disabled = true; b.textContent = 'Đang quét...'; }
        try {
            const res = await apiFetch(`/dtc/scan`, { method: 'POST' });
            const j = await res.json();
            renderList(j.data || [], 'Không tìm thấy mã lỗi nào');
            UIController.showToast(`Quét xong: ${j.count || 0} mã lỗi`);
        } catch (e) {
            UIController.showToast('Quét thất bại');
        } finally {
            if (b) { b.disabled = false; b.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg> Quét mã lỗi'; }
        }
    }

    async function clearDtc(id) {
        try {
            const res = await apiFetch(`/dtc/${id}/clear`, { method: 'PUT' });
            if (res.ok) { UIController.showToast('Đã đánh dấu xử lý'); fetchHistory(); }
        } catch (e) { UIController.showToast('Lỗi'); }
    }

    async function fetchHistory() {
        try {
            const res = await apiFetch(`/dtc/history`);
            const j = await res.json();
            // chi hien ma chua xu ly len dau
            renderList(j.data || [], 'Chưa có mã lỗi');
        } catch (e) { /* im lang */ }
    }

    function init() {
        const b = btn();
        if (b) b.addEventListener('click', scan);
        fetchHistory();
    }

    return { init, scan };
})();

// ============================================================
// Task3f: Predictive Maintenance
// ============================================================
const PredictionManager = (function() {
    const listEl = () => document.getElementById('predList');

    async function fetchPrediction() {
        const el = listEl();
        if (!el) return;
        try {
            const res = await apiFetch(`/maintenance/prediction`);
            const j = await res.json();
            const items = j.data || [];
            el.innerHTML = '';
            if (!items.length) {
                el.innerHTML = '<li class="pred-empty"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--green)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg> Chưa phát hiện xu hướng bất thường</li>';
                return;
            }
            items.forEach(it => {
                const li = document.createElement('li');
                li.className = 'pred-row pred-' + (it.severity || 'warning');
                li.innerHTML = `
                    <span class="pred-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/></svg></span>
                    <span class="pred-text">${it.description}</span>`;
                el.appendChild(li);
            });
        } catch (e) { /* im lang */ }
    }

    function init() {
        fetchPrediction();
        // Lam moi moi 60s
        setInterval(fetchPrediction, 60000);
    }
    return { init, fetchPrediction };
})();

// ══════════════════════════════════════════
// CrashLogManager - Lich su tai nan + video bang chung
// ══════════════════════════════════════════
const CrashLogManager = (() => {
    const SEV_STYLE = {
        'NANG':     { color: '#ff3b3b', label: 'NẶNG',     icon: "<span class='sev-dot'></span>" },
        'VUA':      { color: '#ff9500', label: 'VỪA',      icon: "<span class='sev-dot'></span>" },
        'NHE':      { color: '#ffcc00', label: 'NHẸ',      icon: "<span class='sev-dot'></span>" },
        'NGHI_NGO': { color: '#8e8e93', label: 'NGHI NGỜ', icon: "<span class='sev-dot'></span>" },
    };

    function fmtTime(ts) {
        const d = new Date(ts * 1000);
        const p = n => String(n).padStart(2, '0');
        return `${p(d.getDate())}/${p(d.getMonth()+1)} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
    }

    async function fetchCrashes() {
        try {
            const res = await apiFetch(`/crash-events`);
            if (!res.ok) return;
            const data = await res.json();
            render(data.events || []);
        } catch (e) { console.error('Lỗi tải sự cố tai nạn:', e); }
    }

    function render(events) {
        const listEl = document.getElementById('incidentList');
        if (!listEl) return;
        // Xoa cac card crash cu (giu nguyen alert thuong neu co)
        listEl.querySelectorAll('.crash-row').forEach(el => el.remove());
        const emptyEl = document.getElementById('logEmpty');
        if (events.length > 0 && emptyEl) emptyEl.style.display = 'none';

        events.forEach(ev => {
            const sev = SEV_STYLE[ev.severity] || SEV_STYLE['NGHI_NGO'];
            const li = document.createElement('li');
            li.className = 'incident-row crash-row';
            li.style.borderLeft = `3px solid ${sev.color}`;
            li.innerHTML = `
                <div class="crash-main">
                    <div class="crash-head">
                        <span class="crash-sev" style="color:${sev.color}">${sev.icon} TAI NẠN ${sev.label}</span>
                        <span class="crash-time">${fmtTime(ev.timestamp)}</span>
                    </div>
                    <div class="crash-meta">
                        G-force <b>${ev.gforce}g</b> · Tốc độ trước <b>${ev.speed_before} km/h</b>${ev.tilt > 0 ? ` · Nghiêng <b>${ev.tilt}°</b>` : ''}
                    </div>
                </div>
                ${ev.has_video
                    ? `<button class="btn-evidence" data-id="${ev.id}" data-file="${ev.evidence}"><svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg> Bằng chứng</button>`
                    : `<span class="evidence-pending"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 14"/></svg> Đang tạo bằng chứng</span>`}
            `;
            const btn = li.querySelector('.btn-evidence');
            if (btn) btn.addEventListener('click', () => CrashModal.open(ev.id, ev.evidence, ev));
            listEl.prepend(li);  // tai nan len dau
        });
    }

    function init() {
        fetchCrashes();
        setInterval(fetchCrashes, 15000);
    }
    return { init, fetchCrashes };
})();

// ══════════════════════════════════════════
// CrashModal - Popup xem video + OBD timeline
// ══════════════════════════════════════════
const CrashModal = (() => {
    function open(id, filename, ev) {
        const modal = document.getElementById('crashModal');
        if (!modal) return;
        modal.classList.remove('hidden');
        // Video
        const video = document.getElementById('crashVideo');
        getSignedUrl('/api/evidence/' + filename).then(u => { if (u) video.src = u; });
        // Thong tin
        document.getElementById('crashModalInfo').innerHTML =
            `Mức độ <b>${ev.severity}</b> · G-force <b>${ev.gforce}g</b> · Tốc độ trước <b>${ev.speed_before} km/h</b>`;
        // OBD timeline
        loadObd(id);
    }
    async function loadObd(id) {
        try {
            const res = await apiFetch(`/crash-events/${id}/obd`);
            if (!res.ok) return;
            const data = await res.json();
            drawObd(data);
        } catch (e) { console.error('Lỗi tải OBD timeline:', e); }
    }
    function drawObd(data) {
        const el = document.getElementById('crashObdChart');
        if (!el || typeof Plotly === 'undefined') return;
        const rows = data.data || [];
        const t0 = data.crash_time;
        // gom theo pid_name
        const series = {};
        rows.forEach(r => {
            const name = r.pid_name;
            if (!series[name]) series[name] = { x: [], y: [] };
            series[name].x.push(r.timestamp_sec - t0);  // giay tuong doi va cham
            series[name].y.push(r.value);
        });
        const want = ['Vehicle Speed', 'Engine RPM', 'Throttle Position'];
        const traces = want.filter(n => series[n]).map(n => ({
            x: series[n].x, y: series[n].y, name: n, mode: 'lines', type: 'scatter'
        }));
        Plotly.newPlot(el, traces, {
            margin: { t: 10, r: 10, b: 30, l: 40 },
            paper_bgcolor: 'transparent', plot_bgcolor: 'transparent',
            font: { color: '#ccc', size: 10 },
            xaxis: { title: 'Giây (0 = va chạm)' },
            shapes: [{ type: 'line', x0: 0, x1: 0, y0: 0, y1: 1, yref: 'paper',
                       line: { color: 'red', width: 2, dash: 'dash' } }],
            showlegend: true, legend: { orientation: 'h', y: -0.3 }
        }, { displayModeBar: false, responsive: true });
    }
    function close() {
        const modal = document.getElementById('crashModal');
        if (modal) modal.classList.add('hidden');
        const video = document.getElementById('crashVideo');
        if (video) { video.pause(); video.src = ''; }
    }
    function init() {
        const closeBtn = document.getElementById('crashModalClose');
        if (closeBtn) closeBtn.addEventListener('click', close);
        const modal = document.getElementById('crashModal');
        if (modal) modal.addEventListener('click', e => { if (e.target === modal) close(); });
    }
    return { open, close, init };
})();

// ══════════════════════════════════════════
// CrashTakeover - Canh bao toan man khi co tai nan nghiem trong chua xem
// ══════════════════════════════════════════
const CrashTakeover = (() => {
    let current = null;
    const SEV = { 'NANG': 'NANG', 'VUA': 'VUA' };
    function fmtTime(ts){ const d=new Date(ts*1000); const p=n=>String(n).padStart(2,'0');
        return `${p(d.getDate())}/${p(d.getMonth()+1)} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`; }
    async function check(){
        try {
            const res = await apiFetch(`/crash-events/active`);
            if(!res.ok) return;
            const d = await res.json();
            const el = document.getElementById('crashTakeover');
            if(!el) return;
            if(d.active){
                current = d;
                document.getElementById('takeoverSev').textContent = 'MỨC ĐỘ ' + (d.severity==='NANG'?'NẶNG':'VỪA');
                document.getElementById('takeoverMeta').innerHTML =
                    `${fmtTime(d.timestamp)}<br>G-force <b>${d.gforce}g</b> · Tốc độ trước <b>${d.speed_before} km/h</b>` +
                    (d.tilt>0?` · Nghiêng <b>${d.tilt}°</b>`:'');
                const vbtn = document.getElementById('takeoverVideo');
                vbtn.style.display = d.has_video ? '' : 'none';
                el.classList.remove('hidden');
            } else {
                el.classList.add('hidden');
            }
        } catch(e){ console.error('Loi check takeover:', e); }
    }
    async function ack(){
        if(!current) return;
        try { await apiFetch(`/crash-events/${current.id}/ack`, { method:'PUT' }); } catch(e){}
        const el = document.getElementById('crashTakeover');
        if(el) el.classList.add('hidden');
        current = null;
    }
    function init(){
        const ackBtn = document.getElementById('takeoverAck');
        if(ackBtn) ackBtn.addEventListener('click', ack);
        const vBtn = document.getElementById('takeoverVideo');
        if(vBtn) vBtn.addEventListener('click', () => {
            if(current) CrashModal.open(current.id, current.evidence, current);
        });
        check();
        setInterval(check, 15000);
    }
    return { init, check };
})();


// ============================================================
// MODULE 8: Auto-Calibration
// ============================================================
const CalibrationManager = (function() {
    const overlay = () => document.getElementById('calibrationOverlay');
    const listEl = () => document.getElementById('calibChecklist');
    const emptyEl = () => document.getElementById('calibEmpty');
    const proposalsSection = () => document.getElementById('calibProposalsSection');
    const proposalsList = () => document.getElementById('calibProposalsList');
    const btnStart = () => document.getElementById('btnCalibStart');
    const btnApply = () => document.getElementById('btnCalibApply');
    const statusText = () => document.getElementById('calibStatusText');

    let pollTimer = null;

    const ICONS = {
        OK:   '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--green)" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>',
        WARN: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--amber)" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 9v4M12 17h.01M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z"/></svg>',
        FAIL: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--red)" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>',
        INFO: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--blue)" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>',
    };

    function open() {
        overlay().classList.remove('hidden');
        fetchLast();
    }
    function close() {
        overlay().classList.add('hidden');
        stopPolling();
    }

    function renderChecklist(checks) {
        const list = listEl();
        const empty = emptyEl();
        list.innerHTML = '';
        if (!checks || !checks.length) {
            empty.classList.remove('hidden');
            return;
        }
        empty.classList.add('hidden');
        checks.forEach(c => {
            const li = document.createElement('li');
            li.className = 'calib-row';
            li.innerHTML = `
                <span class="calib-icon">${ICONS[c.status] || ICONS.INFO}</span>
                <span class="calib-name">${c.name}</span>
                <span class="calib-note">${c.note || ''}</span>`;
            list.appendChild(li);
        });
    }

    function renderProposals(proposals) {
        const section = proposalsSection();
        const list = proposalsList();
        list.innerHTML = '';
        if (!proposals || !proposals.length) {
            section.classList.add('hidden');
            btnApply().disabled = true;
            return;
        }
        section.classList.remove('hidden');
        btnApply().disabled = false;
        proposals.forEach(p => {
            const li = document.createElement('li');
            li.className = 'calib-proposal-row';
            li.innerHTML = `<span class="calib-proposal-name">${p.name}</span>
                <span class="calib-proposal-change">${p.old} &rarr; <b class="green">${p.new}</b></span>`;
            list.appendChild(li);
        });
    }

    function fetchLast() {
        apiFetch(`/calibration/last`)
            .then(r => r.ok ? r.json() : null)
            .then(res => {
                if (!res) { renderChecklist([]); renderProposals([]); return; }
                renderChecklist(res.checks || []);
                renderProposals(res.applied ? [] : (res.proposals || []));
                statusText().textContent = res.applied ? 'Đã áp dụng lần chạy trước.' : '';
            })
            .catch(() => { renderChecklist([]); renderProposals([]); });
    }

    function pollStatus() {
        apiFetch(`/calibration/status`)
            .then(r => r.json())
            .then(res => {
                renderChecklist(res.checks || []);
                if (!res.running) {
                    stopPolling();
                    renderProposals(res.proposals || []);
                    btnStart().disabled = false;
                    btnStart().textContent = 'Bắt đầu';
                    statusText().textContent = (res.proposals && res.proposals.length)
                        ? 'Hoàn tất - xem đề xuất bên dưới.'
                        : 'Hoàn tất - không có đề xuất nào.';
                }
            })
            .catch(() => {});
    }

    function startPolling() {
        stopPolling();
        pollTimer = setInterval(pollStatus, 1500);
    }
    function stopPolling() {
        if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    }

    async function start() {
        btnStart().disabled = true;
        btnStart().textContent = 'Đang chạy...';
        statusText().textContent = '';
        proposalsSection().classList.add('hidden');
        try {
            const res = await apiFetch(`/calibration/start`, { method: 'POST' });
            if (!res.ok) {
                const j = await res.json().catch(() => ({}));
                UIController.showToast(j.detail || 'Không bắt đầu được');
                btnStart().disabled = false;
                btnStart().textContent = 'Bắt đầu';
                return;
            }
            startPolling();
        } catch (e) {
            UIController.showToast('Lỗi kết nối');
            btnStart().disabled = false;
            btnStart().textContent = 'Bắt đầu';
        }
    }

    async function apply() {
        btnApply().disabled = true;
        btnApply().textContent = 'Đang ghi...';
        try {
            const res = await apiFetch(`/calibration/apply`, { method: 'POST' });
            const j = await res.json();
            if (!res.ok) {
                UIController.showToast(j.detail || 'Áp dụng thất bại');
            } else if (j.applied) {
                UIController.showToast('Đã ghi config.py - khởi động lại main.py để áp dụng');
                statusText().textContent = 'Đã áp dụng. Cần restart main.py (service) để nhận thay đổi.';
            } else {
                UIController.showToast('Không có gì để áp dụng');
            }
        } catch (e) {
            UIController.showToast('Lỗi kết nối');
        } finally {
            btnApply().textContent = 'Áp dụng';
            btnApply().disabled = false;
        }
    }

    function init() {
        document.getElementById('btnOpenCalibration').addEventListener('click', open);
        document.getElementById('btnCloseCalibration').addEventListener('click', close);
        overlay().addEventListener('click', (e) => { if (e.target === overlay()) close(); });
        btnStart().addEventListener('click', start);
        btnApply().addEventListener('click', apply);
    }

    return { init };
})();


// ============================================================
// MODULE 9: Device Capabilities (badge Gan xe/Tu xa + CPU/nhiet)
// ============================================================
const DeviceCapabilitiesManager = (function() {
    let healthTimer = null;

    function setBadge(text, colorClass) {
        const el = document.getElementById('deviceBadge');
        el.textContent = text;
        el.className = colorClass || '';
    }

    function fetchHealth() {
        apiFetch(`/system/health`)
            .then(r => r.ok ? r.json() : null)
            .then(res => {
                if (!res) return;
                document.getElementById('infoCpu').textContent =
                    (res.cpu_percent != null ? res.cpu_percent + '%' : '--');
                document.getElementById('infoTemp').textContent =
                    (res.temp_c != null ? res.temp_c + '\u00b0C' : '--');
            })
            .catch(() => {});
    }

    function init() {
        apiFetch(`/device/capabilities`)
            .then(r => r.ok ? r.json() : Promise.reject())
            .then(res => {
                if (res.device === 'pi') {
                    setBadge('Gần xe (Pi)', 'green');
                    document.getElementById('cpuChip').classList.remove('hidden');
                    document.getElementById('tempChip').classList.remove('hidden');
                    fetchHealth();
                    healthTimer = setInterval(fetchHealth, 15000);
                } else {
                    setBadge('Từ xa', 'amber');
                }
            })
            .catch(() => {
                setBadge('Từ xa', 'amber');
            });
    }

    return { init };
})();


// ============================================================
// MODULE 10: Storage Manager
// ============================================================
const StorageManagerUI = (function() {
    const overlay = () => document.getElementById('storageOverlay');
    const btnCleanup = () => document.getElementById('btnStorageCleanup');
    const statusText = () => document.getElementById('storageStatusText');

    function formatDate(ts) {
        if (!ts) return '--';
        const d = new Date(ts * 1000);
        return d.toLocaleDateString('vi-VN', { day:'2-digit', month:'2-digit', year:'numeric' });
    }

    function render(data) {
        document.getElementById('storageUsedGb').textContent = data.used_gb;
        document.getElementById('storageTotalGb').textContent = data.total_gb;
        document.getElementById('storageFreeGb').textContent = data.free_gb + ' GB';
        document.getElementById('storageVideoInfo').textContent =
            `${data.video_file_count} file, ${data.video_total_mb} MB`;
        document.getElementById('storageOldest').textContent = formatDate(data.oldest_video_timestamp);
        document.getElementById('storagePolicy').textContent =
            `Dọn khi > ${data.threshold_percent}% | Giữ DB ${data.retention_days} ngày`;

        const fill = document.getElementById('storageBarFill');
        fill.style.width = Math.min(data.usage_percent, 100) + '%';
        fill.className = 'maint-bar-fill ' +
            (data.usage_percent >= data.threshold_percent ? 'critical' :
             data.usage_percent >= data.threshold_percent - 20 ? 'warning' : 'ok');
    }

    function fetchStatus() {
        apiFetch(`/storage/status`)
            .then(r => r.json())
            .then(render)
            .catch(() => { statusText().textContent = 'Không tải được trạng thái'; });
    }

    function open() {
        overlay().classList.remove('hidden');
        statusText().textContent = '';
        fetchStatus();
    }
    function close() {
        overlay().classList.add('hidden');
    }

    async function cleanup() {
        btnCleanup().disabled = true;
        btnCleanup().textContent = 'Đang dọn...';
        statusText().textContent = '';
        try {
            const res = await apiFetch(`/storage/cleanup`, { method: 'POST' });
            const j = await res.json();
            if (!res.ok) {
                UIController.showToast(j.detail || 'Dọn dẹp thất bại');
            } else {
                UIController.showToast(`Đã dọn: ${j.before_percent}% -> ${j.after_percent}%`);
                fetchStatus();
            }
        } catch (e) {
            UIController.showToast('Lỗi kết nối');
        } finally {
            btnCleanup().disabled = false;
            btnCleanup().textContent = 'Dọn ngay';
        }
    }

    function init() {
        document.getElementById('btnOpenStorage').addEventListener('click', open);
        document.getElementById('btnCloseStorage').addEventListener('click', close);
        overlay().addEventListener('click', (e) => { if (e.target === overlay()) close(); });
        btnCleanup().addEventListener('click', cleanup);
    }

    return { init };
})();

document.addEventListener('DOMContentLoaded', () => {
    // ===== Dang nhap Lab =====
    (function setupLogin() {
        const overlay = document.getElementById('loginOverlay');
        const input = document.getElementById('loginPassword');
        const btn = document.getElementById('btnLogin');
        const err = document.getElementById('loginError');
        if (!overlay || !btn) return;
        const box = overlay.querySelector('.login-box');

        // Da co token con han tu lan truoc (F5 khong bi hoi lai). Neu token thuc
        // ra da het han, lan goi API dau tien se tra 401 va apiFetch tu dong hien
        // lai overlay nay - khong can goi rieng 1 API "verify" luc tai trang.
        if (getAuthToken()) {
            overlay.style.display = 'none';
        }

        function fail(msg) {
            err.classList.remove('hidden');
            err.textContent = msg;
            box.classList.remove('shake');
            void box.offsetWidth;
            box.classList.add('shake');
        }
        async function tryLogin() {
            const pw = input.value;
            if (!pw) { fail('Nhập mật khẩu'); return; }
            btn.classList.add('loading');
            try {
                const res = await fetch(`${CONFIG.API_URL}/login`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ password: pw })
                });
                const j = await res.json().catch(() => ({}));
                if (res.ok && j.token) {
                    sessionStorage.setItem('vdr_token', j.token);
                    overlay.style.opacity = '0';
                    overlay.style.transition = 'opacity 0.4s ease';
                    setTimeout(() => { overlay.style.display = 'none'; }, 400);
                } else {
                    btn.classList.remove('loading');
                    fail((j && j.detail) || 'Sai mật khẩu, thử lại');
                    input.value = '';
                    input.focus();
                }
            } catch (e) {
                btn.classList.remove('loading');
                fail('Không kết nối được server');
            }
        }
        btn.addEventListener('click', tryLogin);
        input.addEventListener('keypress', (e) => { if (e.key === 'Enter') tryLogin(); });
        input.focus();
    })();

    // ===== Hieu ung ripple cho nut =====
    document.addEventListener('click', (e) => {
        const btn = e.target.closest('.btn, .btn-icon');
        if (!btn) return;
        const circle = document.createElement('span');
        const rect = btn.getBoundingClientRect();
        const size = Math.max(rect.width, rect.height);
        circle.className = 'ripple-circle';
        circle.style.width = circle.style.height = size + 'px';
        circle.style.left = (e.clientX - rect.left - size/2) + 'px';
        circle.style.top = (e.clientY - rect.top - size/2) + 'px';
        btn.appendChild(circle);
        setTimeout(() => circle.remove(), 600);
    });

    GaugeRenderer.init();
    HistoryChartRenderer.init();
    VideoController.init();
    UIController.init();
    MaintenanceManager.init();
    MaintHistoryManager.init();
    DTCScanner.init();
    PredictionManager.init();
    CrashLogManager.init();
    CrashModal.init();
    CrashTakeover.init();
    CalibrationManager.init();
    DeviceCapabilitiesManager.init();
    StorageManagerUI.init();
    
    const tsEl = document.getElementById('hudTimestamp');
    if (tsEl) {
        tsEl.style.cursor = 'pointer';
        tsEl.addEventListener('click', () => {
            tsEl.style.opacity = tsEl.style.opacity === '0' ? '1' : '0';
        });
    }

    document.body.setAttribute('data-tab', 'drive');
    document.querySelectorAll('.tab-item').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.tab-item').forEach(x => x.classList.remove('active'));
            btn.classList.add('active');
            document.body.setAttribute('data-tab', btn.dataset.tab);
            window.dispatchEvent(new Event('resize'));
        });
    });

});
// ============================================================
// MODULE 7: Maintenance Manager (bảo dưỡng)
// ============================================================
const MaintenanceManager = (function() {
    const LABELS = {
        oil_and_filter: 'Dầu + lọc dầu',
        air_filter:     'Lọc gió',
        spark_plug:     'Bugi',
        gearbox_oil:    'Dầu hộp số',
        brake_pad:      'Má phanh',
    };
    const listEl = document.getElementById('maintList');
    const odoEl  = document.getElementById('odoCurrent');

    async function fetchMaintenance() {
        try {
            const res = await apiFetch(`/maintenance`);
            const json = await res.json();
            if (json.status !== 'success') return;
            if (odoEl) odoEl.textContent = Math.round(json.current_odo).toLocaleString();
            render(json.data || []);
        } catch (e) { console.warn('Lỗi tải bảo dưỡng:', e); }
    }

    function render(items) {
        if (!listEl) return;
        listEl.innerHTML = '';
        items.forEach(it => {
            // Task5a: render chi tiet km / gio may / ngay con lai + bar mau
            const name = LABELS[it.item] || it.item;
            const pct = Math.min(100, Math.round(it.ratio));
            const sev = it.severity || "ok";
            const sevLabel = sev === "critical" ? "<span class='sev-dot' style='color:#ff3b3b'></span> CRITICAL" : sev === "warning" ? "<span class='sev-dot' style='color:#ffcc00'></span> SẮP HẠN" : "<span class='sev-dot' style='color:#34c759'></span> OK";
            const fmt = (n) => Number(n).toLocaleString("vi-VN");
            // Dong so lieu: km | gio may (neu co) | ngay con lai
            const stats = [];
            stats.push(`${fmt(Math.round(it.km_used))} / ${fmt(it.interval_km)} km`);
            if (it.interval_engine_hours) {
                stats.push(`${fmt(Math.round(it.engine_hours_used))} / ${fmt(it.interval_engine_hours)} giờ máy`);
            }
            // Tinh trang theo MOC QUYET DINH (status_text tu backend) - nhat quan voi severity
            if (it.status_text) {
                stats.push(it.status_text);
            } else if (it.days_left !== null && it.days_left !== undefined) {
                stats.push(it.days_left >= 0 ? `còn ~${fmt(it.days_left)} ngày` : `quá ${fmt(-it.days_left)} ngày`);
            }
            const li = document.createElement("li");
            li.className = "maint-row";
            li.innerHTML = `
                <div class="maint-info">
                    <div class="maint-name">
                        <span class="maint-sev maint-sev-${sev}">${sevLabel}</span>
                        <span class="maint-title">${name}</span>
                        <span class="maint-pct">${it.ratio}%</span>
                    </div>
                    <div class="maint-stats">${stats.join("  |  ")}</div>
                    <div class="maint-bar"><div class="maint-bar-fill ${sev}" style="width:${pct}%"></div></div>
                </div>
                <button class="btn btn-ghost maint-done-btn" data-item="${it.item}"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg> Đã bảo dưỡng</button>`;
            li.querySelector('button').addEventListener('click', () => markDone(it.item));
            listEl.appendChild(li);
        });
    }

    async function markDone(item) {
        // Task5b-js: hoi note (tuy chon) truoc khi danh dau xong
        const note = prompt(`Ghi chú (tuỳ chọn) cho "${LABELS[item] || item}":\nVD: Thay Castrol 5W-30 tại Midas Cầu Giấy`, "");
        if (note === null) return;  // bam Cancel -> huy
        try {
            const res = await apiFetch(`/maintenance/${item}/done`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ note })
            });
            if (res.ok) {
                UIController.showToast(`Đã ghi nhận bảo dưỡng: ${LABELS[item] || item}`);
                fetchMaintenance();
            }
        } catch (e) { console.error('Lỗi đánh dấu bảo dưỡng:', e); }
    }

    async function saveOdo() {
        const input = document.getElementById('odoInput');
        const km = parseFloat(input.value);
        if (isNaN(km) || km < 0) { UIController.showToast('Nhập số km hợp lệ'); return; }
        try {
            const res = await apiFetch(`/maintenance/odometer`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ km })
            });
            if (res.ok) {
                input.value = '';
                UIController.showToast(`Đã cập nhật ODO: ${km.toLocaleString()} km`);
                fetchMaintenance();
            }
        } catch (e) { console.error('Lỗi lưu ODO:', e); }
    }

    function init() {
        const btn = document.getElementById('btnSaveOdo');
        if (btn) btn.addEventListener('click', saveOdo);
        fetchMaintenance();
        setInterval(fetchMaintenance, 15000); // tự refresh mỗi 15s
    }

    return { init, fetchMaintenance };
})();
