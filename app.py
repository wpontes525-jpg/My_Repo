from flask import Flask, render_template, request, redirect, url_for, session, jsonify, make_response
from werkzeug.security import generate_password_hash, check_password_hash
import db as _db
import os
import csv
import io
from datetime import datetime, timedelta, date
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'integrare-cajati-secret-2024')


def get_db():
    return _db.connect()


def init_db():
    conn = get_db()
    c = conn.cursor()
    c.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS fornecedores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            telefone TEXT,
            banco TEXT,
            agencia TEXT,
            conta TEXT,
            pix TEXT,
            observacoes TEXT
        );

        CREATE TABLE IF NOT EXISTS contas_pagar (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            descricao TEXT NOT NULL,
            valor REAL NOT NULL,
            vencimento TEXT NOT NULL,
            status TEXT DEFAULT 'pendente',
            fornecedor_id INTEGER,
            recorrencia TEXT DEFAULT 'unico',
            parcela_atual INTEGER DEFAULT 1,
            total_parcelas INTEGER DEFAULT 1,
            grupo_recorrencia TEXT,
            FOREIGN KEY (fornecedor_id) REFERENCES fornecedores(id)
        );

        CREATE TABLE IF NOT EXISTS contas_receber (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            descricao TEXT NOT NULL,
            valor REAL NOT NULL,
            vencimento TEXT NOT NULL,
            status TEXT DEFAULT 'pendente',
            cliente TEXT,
            recorrencia TEXT DEFAULT 'unico',
            parcela_atual INTEGER DEFAULT 1,
            total_parcelas INTEGER DEFAULT 1,
            grupo_recorrencia TEXT
        );
    ''')
    # Create default admin user if not exists
    c.execute("SELECT id FROM users WHERE username = 'admin'")
    if not c.fetchone():
        c.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
                  ('admin', generate_password_hash('integrare2024')))
    conn.commit()
    conn.close()


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


def get_alerts():
    conn = get_db()
    today = date.today().isoformat()
    warning_date = (date.today() + timedelta(days=3)).isoformat()

    vencidas_pagar = conn.execute(
        "SELECT * FROM contas_pagar WHERE status='pendente' AND vencimento < ?", (today,)
    ).fetchall()
    proximas_pagar = conn.execute(
        "SELECT * FROM contas_pagar WHERE status='pendente' AND vencimento BETWEEN ? AND ?",
        (today, warning_date)
    ).fetchall()
    vencidas_receber = conn.execute(
        "SELECT * FROM contas_receber WHERE status='pendente' AND vencimento < ?", (today,)
    ).fetchall()
    proximas_receber = conn.execute(
        "SELECT * FROM contas_receber WHERE status='pendente' AND vencimento BETWEEN ? AND ?",
        (today, warning_date)
    ).fetchall()
    conn.close()
    return {
        'vencidas_pagar': [dict(r) for r in vencidas_pagar],
        'proximas_pagar': [dict(r) for r in proximas_pagar],
        'vencidas_receber': [dict(r) for r in vencidas_receber],
        'proximas_receber': [dict(r) for r in proximas_receber],
        'total_alertas': len(vencidas_pagar) + len(proximas_pagar) + len(vencidas_receber) + len(proximas_receber)
    }


# --- AUTH ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        conn.close()
        if user and check_password_hash(user['password_hash'], password):
            session['user_id'] = user['id']
            session['username'] = user['username']
            return redirect(url_for('dashboard'))
        error = 'Usuário ou senha inválidos.'
    return render_template('login.html', error=error)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# --- DASHBOARD ---

@app.route('/')
@login_required
def dashboard():
    conn = get_db()
    today = date.today()
    mes_atual = today.strftime('%Y-%m')
    mes_anterior = (today.replace(day=1) - timedelta(days=1)).strftime('%Y-%m')
    proximo_mes = (today.replace(day=28) + timedelta(days=4)).strftime('%Y-%m')

    def mes_stats(mes):
        rec = conn.execute(
            "SELECT COALESCE(SUM(valor),0) as total FROM contas_receber WHERE strftime('%Y-%m', vencimento)=? AND status='recebido'", (mes,)
        ).fetchone()['total']
        pag = conn.execute(
            "SELECT COALESCE(SUM(valor),0) as total FROM contas_pagar WHERE strftime('%Y-%m', vencimento)=? AND status='pago'", (mes,)
        ).fetchone()['total']
        rec_pend = conn.execute(
            "SELECT COALESCE(SUM(valor),0) as total FROM contas_receber WHERE strftime('%Y-%m', vencimento)=? AND status='pendente'", (mes,)
        ).fetchone()['total']
        pag_pend = conn.execute(
            "SELECT COALESCE(SUM(valor),0) as total FROM contas_pagar WHERE strftime('%Y-%m', vencimento)=? AND status='pendente'", (mes,)
        ).fetchone()['total']
        return {'receitas': rec, 'despesas': pag, 'rec_pendente': rec_pend, 'pag_pendente': pag_pend}

    atual = mes_stats(mes_atual)
    anterior = mes_stats(mes_anterior)
    proximo = mes_stats(proximo_mes)

    # Last 6 months for chart
    chart_data = []
    for i in range(5, -1, -1):
        d = today.replace(day=1) - timedelta(days=i * 30)
        m = d.strftime('%Y-%m')
        label = d.strftime('%b/%y')
        s = mes_stats(m)
        chart_data.append({'label': label, 'receitas': s['receitas'], 'despesas': s['despesas']})

    alerts = get_alerts()
    conn.close()

    return render_template('dashboard.html',
                           atual=atual, anterior=anterior, proximo=proximo,
                           chart_data=chart_data, alerts=alerts,
                           mes_atual=mes_atual)


# --- CONTAS A PAGAR ---

@app.route('/pagar')
@login_required
def contas_pagar():
    conn = get_db()
    status_filter = request.args.get('status', '')
    search = request.args.get('search', '')
    periodo = request.args.get('periodo', '')

    query = '''SELECT cp.*, f.nome as fornecedor_nome
               FROM contas_pagar cp
               LEFT JOIN fornecedores f ON cp.fornecedor_id = f.id
               WHERE 1=1'''
    params = []
    if status_filter:
        query += ' AND cp.status = ?'
        params.append(status_filter)
    if search:
        query += ' AND cp.descricao LIKE ?'
        params.append(f'%{search}%')
    if periodo:
        query += " AND strftime('%Y-%m', cp.vencimento) = ?"
        params.append(periodo)
    query += ' ORDER BY cp.vencimento ASC'

    contas = conn.execute(query, params).fetchall()
    fornecedores = conn.execute("SELECT id, nome FROM fornecedores ORDER BY nome").fetchall()
    alerts = get_alerts()
    today = date.today().isoformat()
    warning = (date.today() + timedelta(days=3)).isoformat()
    conn.close()
    return render_template('contas_pagar.html', contas=contas, fornecedores=fornecedores,
                           alerts=alerts, today=today, warning=warning,
                           status_filter=status_filter, search=search, periodo=periodo)


@app.route('/pagar/add', methods=['POST'])
@login_required
def add_pagar():
    data = request.form
    descricao = data['descricao']
    valor = float(data['valor'])
    vencimento = data['vencimento']
    fornecedor_id = data.get('fornecedor_id') or None
    recorrencia = data.get('recorrencia', 'unico')
    total_parcelas = int(data.get('total_parcelas', 1))
    conn = get_db()

    import uuid
    grupo = str(uuid.uuid4())[:8]

    if recorrencia == 'parcelado' and total_parcelas > 1:
        base_date = datetime.strptime(vencimento, '%Y-%m-%d')
        for i in range(total_parcelas):
            venc = (base_date.replace(day=1) + timedelta(days=32 * i)).replace(day=base_date.day)
            try:
                venc_str = venc.strftime('%Y-%m-%d')
            except:
                venc_str = vencimento
            conn.execute(
                "INSERT INTO contas_pagar (descricao, valor, vencimento, fornecedor_id, recorrencia, parcela_atual, total_parcelas, grupo_recorrencia) VALUES (?,?,?,?,?,?,?,?)",
                (f"{descricao} ({i+1}/{total_parcelas})", valor / total_parcelas, venc_str, fornecedor_id, recorrencia, i + 1, total_parcelas, grupo)
            )
    elif recorrencia == 'mensal':
        base_date = datetime.strptime(vencimento, '%Y-%m-%d')
        for i in range(12):
            try:
                month = base_date.month + i
                year = base_date.year + (month - 1) // 12
                month = ((month - 1) % 12) + 1
                venc = base_date.replace(year=year, month=month)
                venc_str = venc.strftime('%Y-%m-%d')
            except:
                venc_str = vencimento
            conn.execute(
                "INSERT INTO contas_pagar (descricao, valor, vencimento, fornecedor_id, recorrencia, parcela_atual, total_parcelas, grupo_recorrencia) VALUES (?,?,?,?,?,?,?,?)",
                (descricao, valor, venc_str, fornecedor_id, recorrencia, i + 1, 12, grupo)
            )
    else:
        conn.execute(
            "INSERT INTO contas_pagar (descricao, valor, vencimento, fornecedor_id, recorrencia, parcela_atual, total_parcelas, grupo_recorrencia) VALUES (?,?,?,?,?,?,?,?)",
            (descricao, valor, vencimento, fornecedor_id, 'unico', 1, 1, grupo)
        )
    conn.commit()
    conn.close()
    return redirect(url_for('contas_pagar'))


@app.route('/pagar/pagar/<int:id>', methods=['POST'])
@login_required
def marcar_pago(id):
    conn = get_db()
    conn.execute("UPDATE contas_pagar SET status='pago' WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return redirect(request.referrer or url_for('contas_pagar'))


@app.route('/pagar/delete/<int:id>', methods=['POST'])
@login_required
def delete_pagar(id):
    conn = get_db()
    conn.execute("DELETE FROM contas_pagar WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('contas_pagar'))


@app.route('/pagar/edit/<int:id>', methods=['POST'])
@login_required
def edit_pagar(id):
    data = request.form
    conn = get_db()
    conn.execute(
        "UPDATE contas_pagar SET descricao=?, valor=?, vencimento=?, status=?, fornecedor_id=? WHERE id=?",
        (data['descricao'], float(data['valor']), data['vencimento'], data['status'],
         data.get('fornecedor_id') or None, id)
    )
    conn.commit()
    conn.close()
    return redirect(url_for('contas_pagar'))


# --- CONTAS A RECEBER ---

@app.route('/receber')
@login_required
def contas_receber():
    conn = get_db()
    status_filter = request.args.get('status', '')
    search = request.args.get('search', '')
    periodo = request.args.get('periodo', '')

    query = "SELECT * FROM contas_receber WHERE 1=1"
    params = []
    if status_filter:
        query += ' AND status = ?'
        params.append(status_filter)
    if search:
        query += ' AND (descricao LIKE ? OR cliente LIKE ?)'
        params.extend([f'%{search}%', f'%{search}%'])
    if periodo:
        query += " AND strftime('%Y-%m', vencimento) = ?"
        params.append(periodo)
    query += ' ORDER BY vencimento ASC'

    contas = conn.execute(query, params).fetchall()
    alerts = get_alerts()
    today = date.today().isoformat()
    warning = (date.today() + timedelta(days=3)).isoformat()
    conn.close()
    return render_template('contas_receber.html', contas=contas, alerts=alerts,
                           today=today, warning=warning,
                           status_filter=status_filter, search=search, periodo=periodo)


@app.route('/receber/add', methods=['POST'])
@login_required
def add_receber():
    data = request.form
    descricao = data['descricao']
    valor = float(data['valor'])
    vencimento = data['vencimento']
    cliente = data.get('cliente', '')
    recorrencia = data.get('recorrencia', 'unico')
    total_parcelas = int(data.get('total_parcelas', 1))
    conn = get_db()
    import uuid
    grupo = str(uuid.uuid4())[:8]

    if recorrencia == 'parcelado' and total_parcelas > 1:
        base_date = datetime.strptime(vencimento, '%Y-%m-%d')
        for i in range(total_parcelas):
            try:
                month = base_date.month + i
                year = base_date.year + (month - 1) // 12
                month = ((month - 1) % 12) + 1
                venc = base_date.replace(year=year, month=month)
                venc_str = venc.strftime('%Y-%m-%d')
            except:
                venc_str = vencimento
            conn.execute(
                "INSERT INTO contas_receber (descricao, valor, vencimento, cliente, recorrencia, parcela_atual, total_parcelas, grupo_recorrencia) VALUES (?,?,?,?,?,?,?,?)",
                (f"{descricao} ({i+1}/{total_parcelas})", valor / total_parcelas, venc_str, cliente, recorrencia, i + 1, total_parcelas, grupo)
            )
    elif recorrencia == 'mensal':
        base_date = datetime.strptime(vencimento, '%Y-%m-%d')
        for i in range(12):
            try:
                month = base_date.month + i
                year = base_date.year + (month - 1) // 12
                month = ((month - 1) % 12) + 1
                venc = base_date.replace(year=year, month=month)
                venc_str = venc.strftime('%Y-%m-%d')
            except:
                venc_str = vencimento
            conn.execute(
                "INSERT INTO contas_receber (descricao, valor, vencimento, cliente, recorrencia, parcela_atual, total_parcelas, grupo_recorrencia) VALUES (?,?,?,?,?,?,?,?)",
                (descricao, valor, venc_str, cliente, recorrencia, i + 1, 12, grupo)
            )
    else:
        conn.execute(
            "INSERT INTO contas_receber (descricao, valor, vencimento, cliente, recorrencia, parcela_atual, total_parcelas, grupo_recorrencia) VALUES (?,?,?,?,?,?,?,?)",
            (descricao, valor, vencimento, cliente, 'unico', 1, 1, grupo)
        )
    conn.commit()
    conn.close()
    return redirect(url_for('contas_receber'))


@app.route('/receber/receber/<int:id>', methods=['POST'])
@login_required
def marcar_recebido(id):
    conn = get_db()
    conn.execute("UPDATE contas_receber SET status='recebido' WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return redirect(request.referrer or url_for('contas_receber'))


@app.route('/receber/delete/<int:id>', methods=['POST'])
@login_required
def delete_receber(id):
    conn = get_db()
    conn.execute("DELETE FROM contas_receber WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('contas_receber'))


@app.route('/receber/edit/<int:id>', methods=['POST'])
@login_required
def edit_receber(id):
    data = request.form
    conn = get_db()
    conn.execute(
        "UPDATE contas_receber SET descricao=?, valor=?, vencimento=?, status=?, cliente=? WHERE id=?",
        (data['descricao'], float(data['valor']), data['vencimento'], data['status'],
         data.get('cliente', ''), id)
    )
    conn.commit()
    conn.close()
    return redirect(url_for('contas_receber'))


# --- FORNECEDORES ---

@app.route('/fornecedores')
@login_required
def fornecedores():
    conn = get_db()
    lista = conn.execute("SELECT * FROM fornecedores ORDER BY nome").fetchall()
    alerts = get_alerts()
    conn.close()
    return render_template('fornecedores.html', fornecedores=lista, alerts=alerts)


@app.route('/fornecedores/add', methods=['POST'])
@login_required
def add_fornecedor():
    data = request.form
    conn = get_db()
    conn.execute(
        "INSERT INTO fornecedores (nome, telefone, banco, agencia, conta, pix, observacoes) VALUES (?,?,?,?,?,?,?)",
        (data['nome'], data.get('telefone', ''), data.get('banco', ''),
         data.get('agencia', ''), data.get('conta', ''), data.get('pix', ''), data.get('observacoes', ''))
    )
    conn.commit()
    conn.close()
    return redirect(url_for('fornecedores'))


@app.route('/fornecedores/delete/<int:id>', methods=['POST'])
@login_required
def delete_fornecedor(id):
    conn = get_db()
    conn.execute("DELETE FROM fornecedores WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('fornecedores'))


@app.route('/fornecedores/edit/<int:id>', methods=['POST'])
@login_required
def edit_fornecedor(id):
    data = request.form
    conn = get_db()
    conn.execute(
        "UPDATE fornecedores SET nome=?, telefone=?, banco=?, agencia=?, conta=?, pix=?, observacoes=? WHERE id=?",
        (data['nome'], data.get('telefone', ''), data.get('banco', ''),
         data.get('agencia', ''), data.get('conta', ''), data.get('pix', ''),
         data.get('observacoes', ''), id)
    )
    conn.commit()
    conn.close()
    return redirect(url_for('fornecedores'))


# --- EXPORT ---

@app.route('/export/<tipo>')
@login_required
def export_csv(tipo):
    conn = get_db()
    if tipo == 'pagar':
        rows = conn.execute("SELECT cp.*, f.nome as fornecedor_nome FROM contas_pagar cp LEFT JOIN fornecedores f ON cp.fornecedor_id=f.id ORDER BY vencimento").fetchall()
        fields = ['id', 'descricao', 'valor', 'vencimento', 'status', 'fornecedor_nome', 'recorrencia', 'parcela_atual', 'total_parcelas']
        filename = 'contas_pagar.csv'
    else:
        rows = conn.execute("SELECT * FROM contas_receber ORDER BY vencimento").fetchall()
        fields = ['id', 'descricao', 'valor', 'vencimento', 'status', 'cliente', 'recorrencia', 'parcela_atual', 'total_parcelas']
        filename = 'contas_receber.csv'
    conn.close()

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction='ignore')
    writer.writeheader()
    for row in rows:
        writer.writerow(dict(row))

    response = make_response(output.getvalue())
    response.headers['Content-Disposition'] = f'attachment; filename={filename}'
    response.headers['Content-Type'] = 'text/csv; charset=utf-8'
    return response


# --- API ALERTS ---

@app.route('/api/alerts')
@login_required
def api_alerts():
    return jsonify(get_alerts())


# --- API DASHBOARD (BI) ---

@app.route('/api/dashboard')
@login_required
def api_dashboard():
    """
    Endpoint JSON para o dashboard BI.
    Parâmetros GET:
      periodo  = hoje | 7d | 15d | 30d | custom
      data_ini = YYYY-MM-DD  (quando periodo=custom)
      data_fim = YYYY-MM-DD  (quando periodo=custom)
      tipo     = receitas | despesas | ambos
    """
    conn = get_db()
    periodo = request.args.get('periodo', '30d')
    tipo = request.args.get('tipo', 'ambos')
    today = date.today()

    # Calcular intervalo
    if periodo == 'hoje':
        data_ini = today
        data_fim = today
    elif periodo == '7d':
        data_ini = today - timedelta(days=7)
        data_fim = today
    elif periodo == '15d':
        data_ini = today - timedelta(days=15)
        data_fim = today
    elif periodo == 'custom':
        try:
            data_ini = datetime.strptime(request.args.get('data_ini', ''), '%Y-%m-%d').date()
            data_fim = datetime.strptime(request.args.get('data_fim', ''), '%Y-%m-%d').date()
        except Exception:
            data_ini = today - timedelta(days=30)
            data_fim = today
    else:  # 30d (padrão)
        data_ini = today - timedelta(days=30)
        data_fim = today

    ini_str = data_ini.isoformat()
    fim_str = data_fim.isoformat()

    # ── Período anterior (mesmo intervalo, deslocado para trás)
    delta = (data_fim - data_ini).days or 1
    ant_ini = (data_ini - timedelta(days=delta + 1)).isoformat()
    ant_fim = (data_ini - timedelta(days=1)).isoformat()

    def soma(tabela, status_col, status_val, di, df):
        q = (f"SELECT COALESCE(SUM(valor),0) as t FROM {tabela} "
             f"WHERE vencimento BETWEEN ? AND ? AND {status_col}=?")
        return conn.execute(q, (di, df, status_val)).fetchone()['t']

    # ── KPIs do período atual
    rec_pago  = soma('contas_receber', 'status', 'recebido', ini_str, fim_str)
    desp_pago = soma('contas_pagar',   'status', 'pago',     ini_str, fim_str)
    rec_pend  = soma('contas_receber', 'status', 'pendente', ini_str, fim_str)
    desp_pend = soma('contas_pagar',   'status', 'pendente', ini_str, fim_str)
    saldo     = rec_pago - desp_pago

    # ── KPIs do período anterior (para tendência)
    rec_ant  = soma('contas_receber', 'status', 'recebido', ant_ini, ant_fim)
    desp_ant = soma('contas_pagar',   'status', 'pago',     ant_ini, ant_fim)
    saldo_ant = rec_ant - desp_ant

    # ── Quantidade de transações
    qtd = conn.execute(
        "SELECT COUNT(*) as c FROM contas_receber WHERE vencimento BETWEEN ? AND ?",
        (ini_str, fim_str)
    ).fetchone()['c']
    qtd += conn.execute(
        "SELECT COUNT(*) as c FROM contas_pagar WHERE vencimento BETWEEN ? AND ?",
        (ini_str, fim_str)
    ).fetchone()['c']

    # ── Gráfico comparativo (por dia se ≤15 dias, por mês se >15)
    agrupamento = 'dia' if delta <= 15 else 'mes'
    chart_comparativo = []

    if agrupamento == 'dia':
        d = data_ini
        while d <= data_fim:
            ds = d.isoformat()
            r = soma('contas_receber', 'status', 'recebido', ds, ds)
            p = soma('contas_pagar',   'status', 'pago',     ds, ds)
            chart_comparativo.append({
                'label': d.strftime('%d/%m'),
                'receitas': r,
                'despesas': p
            })
            d += timedelta(days=1)
    else:
        # Últimos 6 meses completos + período atual
        meses_vistos = set()
        meses = []
        d = data_ini.replace(day=1)
        while d <= data_fim:
            key = d.strftime('%Y-%m')
            if key not in meses_vistos:
                meses_vistos.add(key)
                meses.append(d)
            # Próximo mês
            if d.month == 12:
                d = d.replace(year=d.year + 1, month=1)
            else:
                d = d.replace(month=d.month + 1)
        for m in meses:
            m_ini = m.strftime('%Y-%m-01')
            import calendar
            last_day = calendar.monthrange(m.year, m.month)[1]
            m_fim = m.strftime(f'%Y-%m-{last_day:02d}')
            r = soma('contas_receber', 'status', 'recebido', m_ini, m_fim)
            p = soma('contas_pagar',   'status', 'pago',     m_ini, m_fim)
            chart_comparativo.append({
                'label': m.strftime('%b/%y'),
                'receitas': r,
                'despesas': p
            })

    # ── Fontes de receita (top 5 clientes)
    fontes_receita = conn.execute(
        """SELECT COALESCE(cliente,'Sem cliente') as nome,
                  SUM(valor) as total
           FROM contas_receber
           WHERE vencimento BETWEEN ? AND ? AND status='recebido'
           GROUP BY nome ORDER BY total DESC LIMIT 5""",
        (ini_str, fim_str)
    ).fetchall()

    # ── Fontes de despesa (por fornecedor)
    fontes_despesa = conn.execute(
        """SELECT COALESCE(f.nome, 'Sem fornecedor') as nome,
                  SUM(cp.valor) as total
           FROM contas_pagar cp
           LEFT JOIN fornecedores f ON cp.fornecedor_id = f.id
           WHERE cp.vencimento BETWEEN ? AND ? AND cp.status='pago'
           GROUP BY nome ORDER BY total DESC LIMIT 8""",
        (ini_str, fim_str)
    ).fetchall()

    # ── Fluxo de caixa acumulado (sempre por dia, máx 60 pontos)
    fluxo = []
    saldo_acum = 0.0
    step = max(1, delta // 60)
    d = data_ini
    while d <= data_fim:
        ds = d.isoformat()
        r = soma('contas_receber', 'status', 'recebido', ds, ds)
        p = soma('contas_pagar',   'status', 'pago',     ds, ds)
        saldo_acum += (r - p)
        fluxo.append({'label': d.strftime('%d/%m'), 'saldo': round(saldo_acum, 2)})
        d += timedelta(days=step)

    # ── Maior receita e maior despesa do período
    maior_receita = conn.execute(
        """SELECT descricao, cliente, valor FROM contas_receber
           WHERE vencimento BETWEEN ? AND ? AND status='recebido'
           ORDER BY valor DESC LIMIT 1""",
        (ini_str, fim_str)
    ).fetchone()
    maior_despesa = conn.execute(
        """SELECT cp.descricao, COALESCE(f.nome,'') as fornecedor, cp.valor
           FROM contas_pagar cp
           LEFT JOIN fornecedores f ON cp.fornecedor_id=f.id
           WHERE cp.vencimento BETWEEN ? AND ? AND cp.status='pago'
           ORDER BY cp.valor DESC LIMIT 1""",
        (ini_str, fim_str)
    ).fetchone()

    conn.close()

    def pct_var(atual, anterior):
        if anterior == 0:
            return None
        return round(((atual - anterior) / anterior) * 100, 1)

    return jsonify({
        'periodo': {
            'ini': ini_str,
            'fim': fim_str,
            'label': f"{data_ini.strftime('%d/%m/%Y')} – {data_fim.strftime('%d/%m/%Y')}",
            'agrupamento': agrupamento
        },
        'kpis': {
            'receitas':      round(rec_pago,  2),
            'despesas':      round(desp_pago, 2),
            'saldo':         round(saldo,     2),
            'rec_pendente':  round(rec_pend,  2),
            'desp_pendente': round(desp_pend, 2),
            'qtd_transacoes': qtd,
            'var_receitas':  pct_var(rec_pago,  rec_ant),
            'var_despesas':  pct_var(desp_pago, desp_ant),
            'var_saldo':     pct_var(saldo, saldo_ant),
        },
        'chart_comparativo': chart_comparativo,
        'fontes_receita':    [dict(r) for r in fontes_receita],
        'fontes_despesa':    [dict(r) for r in fontes_despesa],
        'fluxo_caixa':       fluxo,
        'destaques': {
            'maior_receita': dict(maior_receita) if maior_receita else None,
            'maior_despesa': dict(maior_despesa) if maior_despesa else None,
            'saldo_negativo': saldo < 0,
        }
    })


# --- RELATÓRIO GLOBAL ---

def gerar_dados_relatorio(data_ini_str, data_fim_str):
    """Retorna lista unificada de registros para o relatório."""
    conn = get_db()
    pagar = conn.execute(
        """SELECT 'Pagar' as tipo, cp.descricao, COALESCE(f.nome,'—') as pessoa,
                  cp.valor, cp.vencimento, cp.status
           FROM contas_pagar cp
           LEFT JOIN fornecedores f ON cp.fornecedor_id=f.id
           WHERE cp.vencimento BETWEEN ? AND ?
           ORDER BY cp.vencimento""",
        (data_ini_str, data_fim_str)
    ).fetchall()

    receber = conn.execute(
        """SELECT 'Receber' as tipo, descricao, COALESCE(cliente,'—') as pessoa,
                  valor, vencimento, status
           FROM contas_receber
           WHERE vencimento BETWEEN ? AND ?
           ORDER BY vencimento""",
        (data_ini_str, data_fim_str)
    ).fetchall()
    conn.close()

    registros = [dict(r) for r in pagar] + [dict(r) for r in receber]
    registros.sort(key=lambda x: x['vencimento'])

    total_pagar   = sum(r['valor'] for r in registros if r['tipo'] == 'Pagar')
    total_receber = sum(r['valor'] for r in registros if r['tipo'] == 'Receber')
    saldo         = total_receber - total_pagar

    return registros, total_pagar, total_receber, saldo


@app.route('/relatorio')
@login_required
def relatorio():
    """Página de relatório interativo (HTML)."""
    periodo = request.args.get('periodo', '30d')
    data_ini_str = request.args.get('data_ini', '')
    data_fim_str = request.args.get('data_fim', '')
    today = date.today()

    if periodo == 'hoje':
        data_ini = today; data_fim = today
    elif periodo == '7d':
        data_ini = today - timedelta(days=7); data_fim = today
    elif periodo == '15d':
        data_ini = today - timedelta(days=15); data_fim = today
    elif periodo == 'custom' and data_ini_str and data_fim_str:
        data_ini = datetime.strptime(data_ini_str, '%Y-%m-%d').date()
        data_fim = datetime.strptime(data_fim_str, '%Y-%m-%d').date()
    else:
        data_ini = today - timedelta(days=30); data_fim = today

    registros, total_pagar, total_receber, saldo = gerar_dados_relatorio(
        data_ini.isoformat(), data_fim.isoformat()
    )
    alerts = get_alerts()

    return render_template(
        'relatorio.html',
        registros=registros,
        total_pagar=total_pagar,
        total_receber=total_receber,
        saldo=saldo,
        data_ini=data_ini.isoformat(),
        data_fim=data_fim.isoformat(),
        periodo=periodo,
        alerts=alerts
    )


# --- EXPORTAÇÃO PDF ---

@app.route('/relatorio/pdf')
@login_required
def relatorio_pdf():
    """Gera PDF do relatório usando WeasyPrint (HTML→PDF)."""
    periodo = request.args.get('periodo', '30d')
    data_ini_str = request.args.get('data_ini', '')
    data_fim_str = request.args.get('data_fim', '')
    today = date.today()

    if periodo == 'hoje':
        data_ini = today; data_fim = today
    elif periodo == '7d':
        data_ini = today - timedelta(days=7); data_fim = today
    elif periodo == '15d':
        data_ini = today - timedelta(days=15); data_fim = today
    elif periodo == 'custom' and data_ini_str and data_fim_str:
        data_ini = datetime.strptime(data_ini_str, '%Y-%m-%d').date()
        data_fim = datetime.strptime(data_fim_str, '%Y-%m-%d').date()
    else:
        data_ini = today - timedelta(days=30); data_fim = today

    registros, total_pagar, total_receber, saldo = gerar_dados_relatorio(
        data_ini.isoformat(), data_fim.isoformat()
    )

    # Gera HTML do PDF
    html_content = render_template(
        'relatorio_pdf.html',
        registros=registros,
        total_pagar=total_pagar,
        total_receber=total_receber,
        saldo=saldo,
        data_ini=data_ini.strftime('%d/%m/%Y'),
        data_fim=data_fim.strftime('%d/%m/%Y'),
        gerado_em=datetime.now().strftime('%d/%m/%Y às %H:%M')
    )

    # Retorna HTML otimizado para impressão — o browser gera o PDF via Ctrl+P / window.print()
    return html_content, 200, {'Content-Type': 'text/html; charset=utf-8'}


# Garante que as tabelas existem ao subir (gunicorn ou local)
with app.app_context():
    init_db()

if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)
