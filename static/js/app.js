// ===== THEME =====
const html = document.documentElement;
const themeIcon = document.getElementById('themeIcon');
const stored = localStorage.getItem('theme') || 'light';
html.setAttribute('data-theme', stored);
if (stored === 'dark' && themeIcon) themeIcon.className = 'fas fa-sun';

document.getElementById('darkModeToggle')?.addEventListener('click', () => {
    const isDark = html.getAttribute('data-theme') === 'dark';
    const next = isDark ? 'light' : 'dark';
    html.setAttribute('data-theme', next);
    localStorage.setItem('theme', next);
    if (themeIcon) themeIcon.className = isDark ? 'fas fa-moon' : 'fas fa-sun';
});

// ===== FULLSCREEN =====
document.getElementById('fullscreenBtn')?.addEventListener('click', () => {
    if (!document.fullscreenElement) {
        document.documentElement.requestFullscreen();
        document.getElementById('fullscreenBtn').querySelector('i').className = 'fas fa-compress';
    } else {
        document.exitFullscreen();
        document.getElementById('fullscreenBtn').querySelector('i').className = 'fas fa-expand';
    }
});

// ===== SIDEBAR TOGGLE =====
const sidebar = document.getElementById('sidebar');
const mainContent = document.getElementById('mainContent');
document.getElementById('sidebarToggle')?.addEventListener('click', () => {
    if (window.innerWidth <= 900) {
        sidebar?.classList.toggle('open');
    } else {
        sidebar?.classList.toggle('collapsed');
        mainContent?.classList.toggle('expanded');
    }
});

// ===== MODAL =====
function openModal(html) {
    document.getElementById('modalContent').innerHTML = html;
    document.getElementById('modalOverlay').classList.remove('hidden');
    document.body.style.overflow = 'hidden';
}
function closeModal(e) {
    if (!e || e.target === document.getElementById('modalOverlay')) {
        document.getElementById('modalOverlay').classList.add('hidden');
        document.body.style.overflow = '';
    }
}
document.addEventListener('keydown', e => {
    if (e.key === 'Escape') closeModal();
});

// ===== ALERTS SIDEBAR =====
async function loadAlerts() {
    try {
        const r = await fetch('/api/alerts');
        const data = await r.json();
        const count = data.total_alertas;
        const badge = document.getElementById('alertCount');
        if (badge) {
            badge.textContent = count;
            badge.className = count > 0 ? 'badge-alert' : 'badge-alert zero';
        }
        const list = document.getElementById('alertsList');
        if (!list) return;
        if (count === 0) {
            list.innerHTML = '<div class="alert-empty">✓ Sem alertas pendentes</div>';
            return;
        }
        let html = '';
        data.vencidas_pagar.forEach(a => {
            html += alertItem(a, 'vencida', 'pagar', 'Pagar');
        });
        data.proximas_pagar.forEach(a => {
            html += alertItem(a, 'proxima', 'pagar', 'Pagar');
        });
        data.vencidas_receber.forEach(a => {
            html += alertItem(a, 'vencida', 'receber', 'Receber');
        });
        data.proximas_receber.forEach(a => {
            html += alertItem(a, 'proxima', 'receber', 'Receber');
        });
        list.innerHTML = html;
    } catch (e) { console.warn('Alerts load failed', e); }
}

function alertItem(a, tipo, acao, label) {
    const route = acao === 'pagar' ? `/pagar/pagar/${a.id}` : `/receber/receber/${a.id}`;
    const btnClass = acao === 'pagar' ? 'btn-alert-pagar' : 'btn-alert-receber';
    const val = parseFloat(a.valor).toLocaleString('pt-BR', {style:'currency', currency:'BRL'});
    const venc = formatDate(a.vencimento);
    return `<div class="alert-item ${tipo}">
        <div class="alert-item-desc" title="${a.descricao}">${a.descricao}</div>
        <div class="alert-item-meta">
            <span class="alert-item-val">${val}</span>
            <form method="POST" action="${route}" style="margin:0">
                <button class="btn-alert-action ${btnClass}" type="submit">✓ ${label}</button>
            </form>
        </div>
        <div style="font-size:0.68rem;color:rgba(255,255,255,0.3);margin-top:2px">${tipo === 'vencida' ? '⚠ Vencida' : '⏰ Vence em breve'} · ${venc}</div>
    </div>`;
}

function formatDate(d) {
    if (!d) return '';
    const [y, m, day] = d.split('-');
    return `${day}/${m}/${y}`;
}

function formatCurrency(v) {
    return parseFloat(v).toLocaleString('pt-BR', {style:'currency', currency:'BRL'});
}

loadAlerts();

// ===== CHART (DASHBOARD) =====
function renderChart(data) {
    const container = document.getElementById('chartContainer');
    if (!container || !data) return;
    const maxVal = Math.max(...data.map(d => Math.max(d.receitas, d.despesas)), 1);
    const H = 120;
    let html = '<div class="chart-bars">';
    data.forEach(d => {
        const rH = Math.max(4, (d.receitas / maxVal) * H);
        const dH = Math.max(4, (d.despesas / maxVal) * H);
        html += `<div class="chart-group">
            <div class="chart-bars-row">
                <div class="chart-bar rec" style="height:${rH}px" data-tip="Receitas: ${formatCurrency(d.receitas)}"></div>
                <div class="chart-bar desp" style="height:${dH}px" data-tip="Despesas: ${formatCurrency(d.despesas)}"></div>
            </div>
            <span class="chart-label">${d.label}</span>
        </div>`;
    });
    html += '</div>';
    html += `<div class="chart-legend">
        <span><span class="legend-dot" style="background:var(--green)"></span>Receitas</span>
        <span><span class="legend-dot" style="background:var(--red)"></span>Despesas</span>
    </div>`;
    container.innerHTML = html;
}

// ===== PARCELAS TOGGLE =====
function setupParcelasToggle(recorrenciaId, parcelasGroupId) {
    const sel = document.getElementById(recorrenciaId);
    const grp = document.getElementById(parcelasGroupId);
    if (!sel || !grp) return;
    const toggle = () => {
        grp.style.display = sel.value === 'parcelado' ? 'flex' : 'none';
    };
    sel.addEventListener('change', toggle);
    toggle();
}
