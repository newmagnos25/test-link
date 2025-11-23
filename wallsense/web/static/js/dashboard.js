/**
 * dashboard.js - Cliente JavaScript para WallSense Dashboard
 *
 * Gerencia WebSocket, atualizações em tempo real, gráficos e UI
 */

// ===== CONFIGURAÇÃO =====
const WS_URL = `ws://${window.location.host}/ws`;
const API_BASE = '/api';

// ===== ESTADO GLOBAL =====
let ws = null;
let reconnectAttempts = 0;
let maxReconnectAttempts = 10;
let reconnectTimeout = null;
let rssiChart = null;
let chartData = {
    labels: [],
    datasets: []
};
let maxDataPoints = 50;

// ===== INICIALIZAÇÃO =====
document.addEventListener('DOMContentLoaded', function() {
    console.log('🚀 WallSense Dashboard inicializando...');

    initWebSocket();
    initChart();
    loadInitialData();

    // Atualiza uptime a cada segundo
    setInterval(updateUptimeDisplay, 1000);
});

// ===== WEBSOCKET =====
function initWebSocket() {
    console.log('🔌 Conectando ao WebSocket...');

    ws = new WebSocket(WS_URL);

    ws.onopen = function() {
        console.log('✅ WebSocket conectado');
        reconnectAttempts = 0;
        updateConnectionStatus(true);
        showNotification('Conexão estabelecida', 'Conectado ao servidor WallSense', 'success');
    };

    ws.onmessage = function(event) {
        try {
            const message = JSON.parse(event.data);
            handleWebSocketMessage(message);
        } catch (error) {
            console.error('❌ Erro ao processar mensagem WebSocket:', error);
        }
    };

    ws.onerror = function(error) {
        console.error('❌ Erro no WebSocket:', error);
        updateConnectionStatus(false);
    };

    ws.onclose = function() {
        console.log('🔌 WebSocket desconectado');
        updateConnectionStatus(false);
        attemptReconnect();
    };
}

function attemptReconnect() {
    if (reconnectAttempts < maxReconnectAttempts) {
        reconnectAttempts++;
        const delay = Math.min(1000 * Math.pow(2, reconnectAttempts), 30000);

        console.log(`🔄 Tentando reconectar (${reconnectAttempts}/${maxReconnectAttempts}) em ${delay}ms...`);

        reconnectTimeout = setTimeout(() => {
            initWebSocket();
        }, delay);
    } else {
        showNotification('Erro de Conexão', 'Não foi possível reconectar ao servidor', 'error');
    }
}

function updateConnectionStatus(connected) {
    const statusDot = document.getElementById('ws-status');
    const statusText = document.getElementById('ws-status-text');

    if (connected) {
        statusDot.className = 'w-3 h-3 rounded-full bg-green-500 animate-pulse';
        statusText.textContent = 'Conectado';
        statusText.className = 'text-sm text-green-400';
    } else {
        statusDot.className = 'w-3 h-3 rounded-full bg-red-500';
        statusText.textContent = 'Desconectado';
        statusText.className = 'text-sm text-red-400';
    }
}

// ===== MENSAGENS WEBSOCKET =====
function handleWebSocketMessage(message) {
    console.log('📨 Mensagem recebida:', message.type);

    switch (message.type) {
        case 'connected':
            console.log('✅ Confirmação de conexão:', message.message);
            break;

        case 'motion_detected':
            handleMotionDetection(message.event);
            break;

        case 'status_update':
            updateStatistics(message.data);
            break;

        case 'system_status':
            updateSystemStatus(message.status);
            break;

        case 'calibration_started':
            showNotification('Calibração Iniciada', message.message, 'info');
            break;

        case 'calibration_complete':
            handleCalibrationComplete(message.data);
            break;

        case 'error':
            showNotification('Erro', message.message, 'error');
            break;

        default:
            console.log('⚠️  Tipo de mensagem desconhecido:', message.type);
    }
}

// ===== DETECÇÃO DE MOVIMENTO =====
function handleMotionDetection(event) {
    console.log('🚨 MOVIMENTO DETECTADO:', event);

    // Adiciona evento à timeline
    addEventToTimeline(event);

    // Atualiza zona se disponível
    if (event.zone) {
        highlightZone(event.zone);
    }

    // Mostra notificação
    const zoneName = event.zone || 'Desconhecida';
    showNotification(
        '🚨 Movimento Detectado!',
        `Zona: ${zoneName} | RSSI: ${event.rssi_current} dBm | Confiança: ${event.confidence}%`,
        'warning'
    );

    // Efeito sonoro (opcional)
    playNotificationSound();
}

function addEventToTimeline(event) {
    const timeline = document.getElementById('events-timeline');

    // Remove placeholder se existir
    if (timeline.querySelector('.text-gray-500')) {
        timeline.innerHTML = '';
    }

    const eventElement = document.createElement('div');
    eventElement.className = 'event-card bg-gray-700 border-l-4 border-red-500 rounded-lg p-4 animate-fade-in';

    const zoneBadge = event.zone
        ? `<span class="px-2 py-1 bg-blue-600 text-xs rounded">${event.zone}</span>`
        : '<span class="px-2 py-1 bg-gray-600 text-xs rounded">Sem Zona</span>';

    eventElement.innerHTML = `
        <div class="flex items-start justify-between">
            <div class="flex-1">
                <div class="flex items-center space-x-2 mb-2">
                    <i class="fas fa-exclamation-triangle text-red-500"></i>
                    <span class="font-semibold">${event.ssid}</span>
                    ${zoneBadge}
                </div>
                <div class="text-sm text-gray-300 space-y-1">
                    <div>
                        <i class="fas fa-signal text-yellow-500 w-4"></i>
                        RSSI: ${event.rssi_current} dBm (Baseline: ${event.rssi_baseline} dBm)
                    </div>
                    <div>
                        <i class="fas fa-chart-line text-green-500 w-4"></i>
                        Desvio: ${event.deviation} dBm | Confiança: ${event.confidence}%
                    </div>
                </div>
            </div>
            <div class="text-right text-xs text-gray-400">
                <i class="fas fa-clock mr-1"></i>
                ${event.timestamp}
            </div>
        </div>
    `;

    // Adiciona no topo
    timeline.insertBefore(eventElement, timeline.firstChild);

    // Limita número de eventos na timeline
    const maxEvents = 20;
    while (timeline.children.length > maxEvents) {
        timeline.removeChild(timeline.lastChild);
    }

    // Anima entrada
    setTimeout(() => {
        eventElement.style.opacity = '1';
    }, 10);
}

// ===== ZONAS =====
function highlightZone(zoneId) {
    const zoneElement = document.querySelector(`[data-zone-id="${zoneId}"]`);

    if (zoneElement) {
        // Adiciona classe de destaque
        zoneElement.classList.add('zone-active', 'motion-detected');

        // Remove destaque após 3 segundos
        setTimeout(() => {
            zoneElement.classList.remove('motion-detected');
            setTimeout(() => {
                zoneElement.classList.remove('zone-active');
                zoneElement.classList.add('zone-inactive');
            }, 5000);
        }, 3000);
    }
}

function renderZones(zones) {
    const zoneMap = document.getElementById('zone-map');
    zoneMap.innerHTML = '';

    if (!zones || zones.length === 0) {
        zoneMap.innerHTML = '<div class="col-span-2 text-center text-gray-500 py-8">Nenhuma zona configurada</div>';
        return;
    }

    zones.forEach(zone => {
        const zoneElement = document.createElement('div');
        zoneElement.setAttribute('data-zone-id', zone.id);
        zoneElement.className = `zone-card zone-inactive rounded-lg p-6 text-white text-center transition-all duration-300 cursor-pointer hover:scale-105`;

        const activeIcon = zone.active
            ? '<i class="fas fa-running text-2xl mb-2 motion-detected"></i>'
            : '<i class="fas fa-home text-2xl mb-2"></i>';

        zoneElement.innerHTML = `
            ${activeIcon}
            <h3 class="font-bold text-lg">${zone.name}</h3>
            <p class="text-sm opacity-75 mt-1">Dispositivos: ${zone.devices.length}</p>
            ${zone.last_motion ? `<p class="text-xs mt-2">Último: ${zone.last_motion}</p>` : ''}
        `;

        if (zone.active) {
            zoneElement.classList.remove('zone-inactive');
            zoneElement.classList.add('zone-active');
        }

        zoneMap.appendChild(zoneElement);
    });
}

// ===== GRÁFICOS =====
function initChart() {
    const ctx = document.getElementById('rssi-chart');

    if (!ctx) {
        console.error('❌ Canvas do gráfico não encontrado');
        return;
    }

    rssiChart = new Chart(ctx, {
        type: 'line',
        data: chartData,
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: {
                duration: 0
            },
            scales: {
                x: {
                    display: true,
                    ticks: { color: '#9ca3af' },
                    grid: { color: '#374151' }
                },
                y: {
                    display: true,
                    ticks: { color: '#9ca3af' },
                    grid: { color: '#374151' },
                    min: -100,
                    max: -30
                }
            },
            plugins: {
                legend: {
                    display: true,
                    labels: { color: '#e5e7eb' }
                },
                tooltip: {
                    mode: 'index',
                    intersect: false
                }
            }
        }
    });
}

function updateChart(networks) {
    if (!rssiChart || !networks || networks.length === 0) return;

    const timestamp = new Date().toLocaleTimeString();

    // Adiciona novo timestamp
    chartData.labels.push(timestamp);
    if (chartData.labels.length > maxDataPoints) {
        chartData.labels.shift();
    }

    // Atualiza cada rede
    networks.slice(0, 5).forEach((network, index) => {
        // Cria dataset se não existe
        if (!chartData.datasets[index]) {
            const colors = ['#3b82f6', '#ef4444', '#10b981', '#f59e0b', '#8b5cf6'];
            chartData.datasets[index] = {
                label: network.ssid,
                data: [],
                borderColor: colors[index],
                backgroundColor: colors[index] + '20',
                borderWidth: 2,
                tension: 0.4
            };
        }

        // Adiciona novo ponto
        chartData.datasets[index].data.push(network.rssi);
        if (chartData.datasets[index].data.length > maxDataPoints) {
            chartData.datasets[index].data.shift();
        }
    });

    rssiChart.update();
}

// ===== ESTATÍSTICAS =====
function updateStatistics(data) {
    // Atualiza cards
    document.getElementById('stat-networks').textContent = data.networks_detected || 0;
    document.getElementById('stat-events').textContent = data.total_events || 0;
    document.getElementById('stat-scans').textContent = data.total_scans || 0;

    // Atualiza zonas
    if (data.zones) {
        renderZones(data.zones);
    }
}

function updateUptimeDisplay() {
    // Isso será atualizado via API ou WebSocket
}

function updateSystemStatus(status) {
    const statusElement = document.getElementById('system-status');

    if (status === 'running') {
        statusElement.innerHTML = '<i class="fas fa-circle text-green-500 text-xs mr-2 animate-pulse"></i><span class="text-sm">Sistema Ativo</span>';
        statusElement.className = 'px-4 py-2 bg-green-900 border border-green-700 rounded-lg';
    } else {
        statusElement.innerHTML = '<i class="fas fa-circle text-gray-500 text-xs mr-2"></i><span class="text-sm">Sistema Parado</span>';
        statusElement.className = 'px-4 py-2 bg-gray-700 rounded-lg';
    }
}

// ===== API CALLS =====
async function apiCall(endpoint, method = 'GET', body = null) {
    try {
        const options = {
            method,
            headers: {
                'Content-Type': 'application/json'
            }
        };

        if (body) {
            options.body = JSON.stringify(body);
        }

        const response = await fetch(`${API_BASE}${endpoint}`, options);

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }

        return await response.json();
    } catch (error) {
        console.error(`❌ Erro na chamada API ${endpoint}:`, error);
        showNotification('Erro na API', error.message, 'error');
        throw error;
    }
}

async function loadInitialData() {
    try {
        // Carrega estatísticas
        const stats = await apiCall('/statistics');
        updateStatistics(stats);

        // Carrega zonas
        const zonesData = await apiCall('/zones');
        renderZones(zonesData.zones);

        // Carrega redes
        const networksData = await apiCall('/networks');
        renderNetworksList(networksData.networks);

        // Carrega eventos
        const eventsData = await apiCall('/events');
        if (eventsData.events && eventsData.events.length > 0) {
            eventsData.events.reverse().forEach(event => addEventToTimeline(event));
        }

    } catch (error) {
        console.error('❌ Erro ao carregar dados iniciais:', error);
    }
}

function renderNetworksList(networks) {
    const networksList = document.getElementById('networks-list');

    if (!networks || networks.length === 0) {
        networksList.innerHTML = '<div class="text-center text-gray-500 py-8"><i class="fas fa-search text-3xl mb-2"></i><p class="text-sm">Nenhuma rede detectada</p></div>';
        return;
    }

    networksList.innerHTML = '';

    networks.forEach(network => {
        const networkElement = document.createElement('div');
        networkElement.className = 'bg-gray-700 rounded-lg p-3';

        const signalStrength = getSignalStrength(network.rssi);

        networkElement.innerHTML = `
            <div class="flex items-center justify-between">
                <div class="flex-1">
                    <div class="font-semibold text-sm">${network.ssid}</div>
                    <div class="text-xs text-gray-400 mt-1">${network.bssid}</div>
                </div>
                <div class="text-right">
                    <div class="text-sm font-mono">${network.rssi} dBm</div>
                    <div class="text-xs ${signalStrength.color}">${signalStrength.text}</div>
                </div>
            </div>
        `;

        networksList.appendChild(networkElement);
    });
}

function getSignalStrength(rssi) {
    if (rssi >= -50) return { text: 'Excelente', color: 'text-green-400' };
    if (rssi >= -60) return { text: 'Bom', color: 'text-blue-400' };
    if (rssi >= -70) return { text: 'Regular', color: 'text-yellow-400' };
    return { text: 'Fraco', color: 'text-red-400' };
}

// ===== CONTROLES =====
async function calibrateSystem() {
    const duration = 30;

    if (!confirm(`Iniciar calibração de ${duration} segundos?\n\n⚠️ Evite movimento durante a calibração!`)) {
        return;
    }

    try {
        showNotification('Calibração', 'Iniciando calibração...', 'info');
        const result = await apiCall(`/calibrate?duration=${duration}`, 'POST');

        if (result.success) {
            showNotification('Calibração Completa', `${result.networks_calibrated} redes calibradas`, 'success');
        }
    } catch (error) {
        showNotification('Erro', 'Falha na calibração', 'error');
    }
}

async function startMonitoring() {
    try {
        const result = await apiCall('/start', 'POST');

        if (result.success) {
            showNotification('Monitoramento', 'Monitoramento iniciado', 'success');
            updateSystemStatus('running');
        } else {
            showNotification('Aviso', result.message, 'warning');
        }
    } catch (error) {
        console.error('❌ Erro ao iniciar monitoramento:', error);
    }
}

async function stopMonitoring() {
    try {
        const result = await apiCall('/stop', 'POST');

        if (result.success) {
            showNotification('Monitoramento', 'Monitoramento parado', 'info');
            updateSystemStatus('stopped');
        }
    } catch (error) {
        console.error('❌ Erro ao parar monitoramento:', error);
    }
}

async function updateSensitivity(value) {
    document.getElementById('sensitivity-value').textContent = `${value}x`;

    try {
        const result = await apiCall(`/sensitivity?sensitivity=${value}`, 'POST');

        if (result.success) {
            showNotification('Sensibilidade', `Ajustada para ${value}x`, 'info');
        }
    } catch (error) {
        console.error('❌ Erro ao ajustar sensibilidade:', error);
    }
}

function handleCalibrationComplete(data) {
    showNotification(
        'Calibração Completa',
        `${data.networks_calibrated} redes calibradas com ${data.samples_collected} amostras`,
        'success'
    );

    loadInitialData();
}

// ===== NOTIFICAÇÕES =====
function showNotification(title, message, type = 'info') {
    const toast = document.getElementById('toast-notification');
    const toastIcon = document.getElementById('toast-icon');
    const toastTitle = document.getElementById('toast-title');
    const toastMessage = document.getElementById('toast-message');

    // Configura ícone e cor
    const config = {
        info: { icon: 'fa-info', color: 'bg-blue-500' },
        success: { icon: 'fa-check', color: 'bg-green-500' },
        warning: { icon: 'fa-exclamation-triangle', color: 'bg-yellow-500' },
        error: { icon: 'fa-times', color: 'bg-red-500' }
    };

    const iconClass = config[type].icon;
    const colorClass = config[type].color;

    toastIcon.className = `flex-shrink-0 w-10 h-10 flex items-center justify-center rounded-full ${colorClass} mr-3`;
    toastIcon.innerHTML = `<i class="fas ${iconClass} text-white"></i>`;
    toastTitle.textContent = title;
    toastMessage.textContent = message;

    // Mostra toast
    toast.style.transform = 'translateX(0)';

    // Esconde após 5 segundos
    setTimeout(() => {
        toast.style.transform = 'translateX(100%)';
    }, 5000);
}

function playNotificationSound() {
    // Som de notificação (opcional)
    // const audio = new Audio('/static/sounds/notification.mp3');
    // audio.play().catch(e => console.log('Não foi possível reproduzir som'));
}

// ===== UTILITÁRIOS =====
function formatUptime(seconds) {
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = Math.floor(seconds % 60);

    if (hours > 0) {
        return `${hours}h ${minutes}m`;
    } else if (minutes > 0) {
        return `${minutes}m ${secs}s`;
    } else {
        return `${secs}s`;
    }
}

// Exporta funções para uso global
window.calibrateSystem = calibrateSystem;
window.startMonitoring = startMonitoring;
window.stopMonitoring = stopMonitoring;
window.updateSensitivity = updateSensitivity;

console.log('✅ Dashboard JavaScript carregado');
