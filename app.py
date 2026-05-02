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


# Garante que as tabelas existem ao subir (gunicorn ou local)
with app.app_context():
    init_db()

if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)
