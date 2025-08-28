#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RBN | Sistema de gestão de gastos de viagem
- Melhorias:
  • Saldo e Extrato no Perfil do funcionário (filtros de período, incluir rejeitados, exportação CSV).
  • Utilitários de cálculo de totais e geração de extrato unificado (depósitos + despesas).
  • Pequenos aprimoramentos de UX e organização do código.
"""

import os
import io
import csv
import logging
import sqlite3
import uuid
import datetime as dt
from functools import wraps

from flask import (
    Flask, render_template_string, request, redirect, url_for, flash, session,
    send_from_directory, jsonify, abort, Response
)
from werkzeug.security import generate_password_hash, check_password_hash
    # secure_filename protege nomes de arquivos de upload
from werkzeug.utils import secure_filename
from PIL import Image

APP_TITLE = "RBN | Gastos de Viagem"
BASE_DIR = os.path.dirname(__file__)
UPLOAD_DIR = os.path.join(BASE_DIR, 'uploads')
DB_PATH = os.path.join(BASE_DIR, 'rbn_trip_expenses.db')
ALLOWED_EXTS = {'.jpg', '.jpeg', '.png', '.pdf'}
MAX_IMAGE_PIXELS = 2600

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-change-me')
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10 MB
os.makedirs(UPLOAD_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO)


# =========================
# Template base
# =========================
BASE_HTML = """
{% macro nav() %}
<nav class="navbar navbar-expand-lg bg-body-tertiary border-bottom">
  <div class="container-fluid">
    <a class="navbar-brand fw-semibold" href="{{ url_for('index') }}">{{ title }}</a>
    <button class="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#navbarsExample" aria-controls="navbarsExample" aria-expanded="false" aria-label="Toggle navigation">
      <span class="navbar-toggler-icon"></span>
    </button>
    <div class="collapse navbar-collapse" id="navbarsExample">
      <ul class="navbar-nav me-auto mb-2 mb-lg-0">
        {% if session.get('role') == 'employee' %}
          <li class="nav-item"><a class="nav-link" href="{{ url_for('my_trips') }}">Minhas Viagens</a></li>
          <li class="nav-item"><a class="nav-link" href="{{ url_for('expense_new') }}">Nova Despesa</a></li>
          <li class="nav-item"><a class="nav-link" href="{{ url_for('profile') }}#extrato">Saldo/Extrato</a></li>
        {% endif %}
        {% if session.get('role') == 'admin' %}
          <li class="nav-item"><a class="nav-link" href="{{ url_for('admin_dashboard') }}">Painel Gestor</a></li>
          <li class="nav-item"><a class="nav-link" href="{{ url_for('admin_trips') }}">Viagens</a></li>
          <li class="nav-item"><a class="nav-link" href="{{ url_for('admin_users') }}">Usuários</a></li>
          <li class="nav-item"><a class="nav-link" href="{{ url_for('admin_deposits') }}">Depósitos</a></li>
          <li class="nav-item"><a class="nav-link" href="{{ url_for('admin_reports') }}">Relatórios</a></li>
        {% endif %}
      </ul>
      <div class="d-flex">
        {% if session.get('user_id') %}
          <span class="navbar-text me-3">Olá, {{ session.get('name') }}</span>
          <a class="btn btn-outline-secondary btn-sm" href="{{ url_for('profile') }}">Perfil</a>
          <a class="btn btn-outline-danger btn-sm ms-2" href="{{ url_for('logout') }}">Sair</a>
        {% else %}
          <a class="btn btn-primary btn-sm" href="{{ url_for('login') }}">Entrar</a>
        {% endif %}
      </div>
    </div>
  </div>
</nav>
{% endmacro %}

<!doctype html>
<html lang="pt-br" data-bs-theme="light">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{{ title }}</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
      .card-hover{transition:transform .1s ease, box-shadow .1s ease}
      .card-hover:hover{transform:translateY(-2px); box-shadow:0 0.5rem 1rem rgba(0,0,0,.08)}
      .receipt-thumb{max-height:120px; object-fit:contain}
      .table-fit td, .table-fit th { white-space: nowrap; }
    </style>
  </head>
  <body>
    {{ nav() }}
    <main class="container py-4">
      {% with messages = get_flashed_messages(with_categories=true) %}
        {% if messages %}
          {% for cat, msg in messages %}
            <div class="alert alert-{{ cat }}">{{ msg }}</div>
          {% endfor %}
        {% endif %}
      {% endwith %}
      {{ body|safe }}
    </main>
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
  </body>
</html>
"""


# =========================
# Render helper
# =========================
def render_page(body_tpl: str, **ctx):
    inner = render_template_string(body_tpl, **ctx)
    return render_template_string(BASE_HTML, title=APP_TITLE, body=inner, **ctx)


# =========================
# Utilidades e DB
# =========================
def ext_allowed(filename: str):
    _, ext = os.path.splitext(filename.lower())
    return ext in ALLOWED_EXTS


def resize_if_image(path: str):
    try:
        if path.lower().endswith(('.jpg', '.jpeg', '.png')):
            with Image.open(path) as test:
                test.verify()
        if path.lower().endswith(('.jpg', '.jpeg', '.png')):
            with Image.open(path) as img:
                img = img.convert("RGB")
                img.thumbnail((MAX_IMAGE_PIXELS, MAX_IMAGE_PIXELS))
                img.save(path, optimize=True, quality=85)
    except Exception:
        # Se falhar a validação/conversão, seguimos sem travar a aplicação
        pass


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=10000;")
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()

    # Tabelas principais
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('admin','employee')),
            password_hash TEXT NOT NULL,
            bank_info TEXT DEFAULT ''
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS trips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            daily_limit REAL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'aberta',
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trip_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            category TEXT NOT NULL,
            description TEXT DEFAULT '',
            amount REAL NOT NULL,
            receipt_path TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pendente',
            created_at TEXT NOT NULL,
            FOREIGN KEY(trip_id) REFERENCES trips(id),
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS deposits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            trip_id INTEGER,
            amount REAL NOT NULL,
            date TEXT NOT NULL,
            note TEXT DEFAULT '',
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(trip_id) REFERENCES trips(id)
        );
    """)

    # Índices úteis
    cur.execute("CREATE INDEX IF NOT EXISTS idx_expenses_user ON expenses(user_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_expenses_trip ON expenses(trip_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_expenses_date ON expenses(date);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_deposits_user ON deposits(user_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_deposits_date ON deposits(date);")

    # Admin padrão
    cur.execute("SELECT id FROM users WHERE email=?", ("admin@rbn.local",))
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO users(name,email,role,password_hash) VALUES(?,?,?,?)",
            ("Administrador", "admin@rbn.local", "admin", generate_password_hash("admin123")),
        )

    conn.commit()
    conn.close()


init_db()


# =========================
# Helpers de Auth
# =========================
def login_required(role=None):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if 'user_id' not in session:
                flash('Faça login para continuar.', 'warning')
                return redirect(url_for('login'))
            if role and session.get('role') != role:
                abort(403)
            return f(*args, **kwargs)
        return wrapper
    return decorator


def current_user():
    if 'user_id' not in session:
        return None
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (session['user_id'],)).fetchone()
    conn.close()
    return user


# =========================
# Helpers de Saldo/Extrato
# =========================
def calc_user_totals(user_id: int, start: str | None = None, end: str | None = None,
                     include_rejected: bool = False) -> dict:
    """
    Retorna dicionário com totais: deposits, expenses, balance.
    Por padrão, despesas rejeitadas NÃO entram no total.
    Se start/end forem fornecidos (YYYY-MM-DD), filtramos o período.
    """
    conn = get_db()
    params = [user_id]
    where_d = ["user_id=?"]
    where_e = ["user_id=?"]

    if start and end:
        where_d.append("date BETWEEN ? AND ?")
        where_e.append("date BETWEEN ? AND ?")
        params += [start, end, start, end]

    if not include_rejected:
        where_e.append("status!='rejeitado'")

    q_dep = f"SELECT COALESCE(SUM(amount),0) s FROM deposits WHERE {' AND '.join(where_d)}"
    q_exp = f"SELECT COALESCE(SUM(amount),0) s FROM expenses WHERE {' AND '.join(where_e)}"

    # Como montamos params em sequência (user, [start,end], [start,end]), precisamos separar:
    # Primeiro executa depósitos (usa 1º bloco de params)
    par_dep = [user_id] + ([start, end] if (start and end) else [])
    par_exp = [user_id] + ([start, end] if (start and end) else [])
    if not include_rejected:
        # nada a acrescentar em par_exp; já está ok.
        pass

    total_dep = conn.execute(q_dep, par_dep).fetchone()['s']
    total_exp = conn.execute(q_exp, par_exp).fetchone()['s']
    conn.close()
    return {
        "deposits": float(total_dep or 0),
        "expenses": float(total_exp or 0),
        "balance": float(total_dep or 0) - float(total_exp or 0),
    }


def fetch_user_statement(user_id: int, start: str | None = None, end: str | None = None,
                         include_rejected: bool = False):
    """
    Retorna lista de movimentos (depósitos e despesas) em um extrato unificado.
    Cada item: {date, type, description, trip, status, amount}
      - Depósito: amount positivo
      - Despesa:  amount negativo; status exibido
    """
    conn = get_db()

    where_dep = ["d.user_id=?"]
    where_exp = ["e.user_id=?"]
    params: list = [user_id]

    if start and end:
        where_dep.append("d.date BETWEEN ? AND ?")
        where_exp.append("e.date BETWEEN ? AND ?")
        params += [start, end, start, end]

    if not include_rejected:
        where_exp.append("e.status!='rejeitado'")

    q = f"""
    SELECT d.date AS date,
           'Depósito' AS type,
           COALESCE(d.note,'') AS description,
           COALESCE(t.title,'—') AS trip,
           '' AS status,
           d.amount AS amount
      FROM deposits d
 LEFT JOIN trips t ON t.id = d.trip_id
     WHERE {' AND '.join(where_dep)}
    UNION ALL
    SELECT e.date AS date,
           'Despesa' AS type,
           (e.category || CASE WHEN e.description!='' THEN (' • '||e.description) ELSE '' END) AS description,
           COALESCE(t2.title,'—') AS trip,
           e.status AS status,
           -e.amount AS amount
      FROM expenses e
      JOIN trips t2 ON t2.id = e.trip_id
     WHERE {' AND '.join(where_exp)}
  ORDER BY date ASC, type DESC
    """

    # Constrói params separadamente por SELECT do UNION
    par_dep = [user_id] + ([start, end] if (start and end) else [])
    par_exp = [user_id] + ([start, end] if (start and end) else [])
    if not include_rejected:
        # sem mudança adicional
        pass
    rows = conn.execute(q, par_dep + par_exp).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# =========================
# Rotas públicas
# =========================
@app.route('/')
def index():
    if session.get('user_id'):
        if session.get('role') == 'admin':
            return redirect(url_for('admin_dashboard'))
        return redirect(url_for('my_trips'))
    body = """
    <div class="text-center py-5">
      <h1 class="display-6 mb-3">{{ title }}</h1>
      <p class="lead">Envie suas despesas de viagem com foto da nota e acompanhe aprovações e reembolsos.</p>
      <a class="btn btn-primary btn-lg" href="{{ url_for('login') }}">Entrar</a>
    </div>
    """
    return render_page(body)


@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email','').strip().lower()
        password = request.form.get('password','')
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        conn.close()
        if user and check_password_hash(user['password_hash'], password):
            session['user_id'] = user['id']
            session['role'] = user['role']
            session['name'] = user['name']
            flash('Login realizado com sucesso.', 'success')
            return redirect(url_for('index'))
        flash('Credenciais inválidas.', 'danger')
    body = """
    <div class="row justify-content-center">
      <div class="col-12 col-md-6">
        <div class="card card-hover"><div class="card-body">
          <h5 class="card-title mb-3">Entrar</h5>
          <form method="post">
            <div class="mb-3">
              <label class="form-label">E-mail</label>
              <input type="email" class="form-control" name="email" required>
            </div>
            <div class="mb-3">
              <label class="form-label">Senha</label>
              <input type="password" class="form-control" name="password" required>
            </div>
            <button class="btn btn-primary w-100">Entrar</button>
          </form>
        </div></div>
      </div>
    </div>
    """
    return render_page(body)


@app.route('/logout')
def logout():
    session.clear()
    flash('Sessão encerrada.', 'info')
    return redirect(url_for('index'))


# =========================
# Perfil (agora com SALDO + EXTRATO)
# =========================
@app.route('/perfil', methods=['GET','POST'])
@login_required()
def profile():
    user = current_user()

    # Atualização cadastral / senha
    if request.method == 'POST':
        name = request.form.get('name','').strip()
        bank_info = request.form.get('bank_info','').strip()
        new_pass = request.form.get('new_password','')
        conn = get_db()
        if new_pass:
            conn.execute(
                "UPDATE users SET name=?, bank_info=?, password_hash=? WHERE id=?",
                (name, bank_info, generate_password_hash(new_pass), user['id'])
            )
        else:
            conn.execute("UPDATE users SET name=?, bank_info=? WHERE id=?",
                         (name, bank_info, user['id']))
        conn.commit()
        conn.close()
        session['name'] = name
        flash('Perfil atualizado.', 'success')
        return redirect(url_for('profile'))

    # --- Filtros do extrato no Perfil
    scope = request.args.get('scope', 'month')  # month|all
    include_rejected = request.args.get('rej', '0') == '1'
    start = request.args.get('start')
    end = request.args.get('end')

    if scope == 'all':
        start = None
        end = None
    else:
        if not start:
            start = (dt.date.today().replace(day=1)).isoformat()
        if not end:
            end = dt.date.today().isoformat()

    totals = calc_user_totals(user['id'], start, end, include_rejected)
    statement = fetch_user_statement(user['id'], start, end, include_rejected)

    body = """
    <div class="row g-4">
      <div class="col-12 col-lg-5">
        <h4>Meu perfil</h4>
        <form method="post">
          <div class="mb-3"><label class="form-label">Nome</label>
            <input class="form-control" name="name" value="{{ user.name }}" required></div>
          <div class="mb-3"><label class="form-label">E-mail</label>
            <input class="form-control" value="{{ user.email }}" disabled></div>
          <div class="mb-3"><label class="form-label">Dados bancários</label>
            <textarea class="form-control" name="bank_info" rows="3">{{ user.bank_info }}</textarea></div>
          <div class="mb-3"><label class="form-label">Nova senha (opcional)</label>
            <input type="password" class="form-control" name="new_password"></div>
          <button class="btn btn-primary">Salvar</button>
        </form>

        <div class="card mt-4"><div class="card-body">
          <h6 class="mb-3">Resumo financeiro</h6>
          <div class="d-flex justify-content-between"><span>Depósitos</span><strong>R$ {{ '%.2f'|format(totals.deposits) }}</strong></div>
          <div class="d-flex justify-content-between"><span>Despesas{% if not include_rejected %} (sem rejeitadas){% endif %}</span><strong>R$ {{ '%.2f'|format(totals.expenses) }}</strong></div>
          <hr>
          <div class="d-flex justify-content-between fs-5">
            <span>Saldo</span><span class="{{ 'text-success' if totals.balance >= 0 else 'text-danger' }}"><strong>R$ {{ '%.2f'|format(totals.balance) }}</strong></span>
          </div>
        </div></div>
      </div>

      <div class="col-12 col-lg-7" id="extrato">
        <div class="d-flex justify-content-between align-items-center">
          <h4 class="mb-0">Extrato</h4>
          <div>
            <a class="btn btn-sm btn-outline-primary" href="{{ url_for('profile_statement_csv', scope=scope, start=start, end=end, rej=('1' if include_rejected else '0')) }}">Exportar CSV</a>
          </div>
        </div>

        <form class="row g-2 mt-2 mb-3">
          <div class="col-md-4">
            <label class="form-label">Escopo</label>
            <select class="form-select" name="scope" onchange="this.form.submit()">
              <option value="month" {% if scope!='all' %}selected{% endif %}>Mês atual / Intervalo</option>
              <option value="all" {% if scope=='all' %}selected{% endif %}>Todos os períodos</option>
            </select>
          </div>

          <div class="col-md-4">
            <label class="form-label">De</label>
            <input type="date" class="form-control" name="start" value="{{ start or '' }}" {% if scope=='all' %}disabled{% endif %}>
          </div>
          <div class="col-md-4">
            <label class="form-label">Até</label>
            <input type="date" class="form-control" name="end" value="{{ end or '' }}" {% if scope=='all' %}disabled{% endif %}>
          </div>

          <div class="col-md-6">
            <div class="form-check mt-4">
              <input class="form-check-input" type="checkbox" id="rej" name="rej" value="1" {% if include_rejected %}checked{% endif %}>
              <label class="form-check-label" for="rej">Incluir despesas rejeitadas</label>
            </div>
          </div>

          <div class="col-md-6 d-flex align-items-end justify-content-end">
            <button class="btn btn-secondary">Filtrar</button>
          </div>
        </form>

        <div class="table-responsive">
          <table class="table table-sm table-fit align-middle">
            <thead>
              <tr><th>Data</th><th>Tipo</th><th>Descrição</th><th>Viagem</th><th>Status</th><th class="text-end">Valor</th></tr>
            </thead>
            <tbody>
              {% for r in statement %}
                <tr>
                  <td>{{ r.date }}</td>
                  <td>
                    <span class="badge text-bg-{{ 'success' if r.type=='Depósito' else 'secondary' }}">{{ r.type }}</span>
                  </td>
                  <td>{{ r.description }}</td>
                  <td>{{ r.trip }}</td>
                  <td>
                    {% if r.type=='Despesa' %}
                      <span class="badge text-bg-{{ 'success' if r.status=='aprovado' else ('danger' if r.status=='rejeitado' else 'secondary') }}">{{ r.status }}</span>
                    {% endif %}
                  </td>
                  <td class="text-end {{ 'text-success' if r.amount >= 0 else 'text-danger' }}">R$ {{ '%.2f'|format(r.amount) }}</td>
                </tr>
              {% else %}
                <tr><td colspan="6" class="text-muted">Sem movimentos neste período.</td></tr>
              {% endfor %}
            </tbody>
            <tfoot>
              <tr>
                <th colspan="5" class="text-end">Saldo do período</th>
                <th class="text-end {{ 'text-success' if totals.balance >= 0 else 'text-danger' }}">R$ {{ '%.2f'|format(totals.balance) }}</th>
              </tr>
            </tfoot>
          </table>
        </div>
      </div>
    </div>
    """
    return render_page(
        body,
        user=user,
        totals=totals,
        statement=statement,
        scope=scope,
        include_rejected=include_rejected,
        start=start,
        end=end
    )


@app.route('/perfil/extrato.csv')
@login_required()
def profile_statement_csv():
    user = current_user()
    scope = request.args.get('scope', 'month')
    include_rejected = request.args.get('rej', '0') == '1'
    start = request.args.get('start')
    end = request.args.get('end')

    if scope != 'all':
        if not start:
            start = (dt.date.today().replace(day=1)).isoformat()
        if not end:
            end = dt.date.today().isoformat()
    else:
        start = None
        end = None

    rows = fetch_user_statement(user['id'], start, end, include_rejected)

    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["Data", "Tipo", "Descrição", "Viagem", "Status", "Valor"])
    for r in rows:
        writer.writerow([r["date"], r["type"], r["description"], r["trip"], r["status"], f"{r['amount']:.2f}"])
    filename = f"extrato_{user['name'].replace(' ', '_')}.csv"
    return Response(out.getvalue().encode("utf-8"), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={filename}"})


# =========================
# Funcionário: viagens e despesas
# =========================
@app.route('/viagens')
@login_required('employee')
def my_trips():
    conn = get_db()
    trips = conn.execute("SELECT * FROM trips WHERE user_id=? ORDER BY id DESC", (session['user_id'],)).fetchall()
    totals = {}
    for t in trips:
        total = conn.execute(
            "SELECT COALESCE(SUM(amount),0) s FROM expenses WHERE trip_id=? AND status!='rejeitado'",
            (t['id'],)
        ).fetchone()['s']
        totals[t['id']] = total
    conn.close()
    body = """
    <div class="mb-3"><h4>Minhas viagens</h4></div>
    <div class="row g-3">
      {% for t in trips %}
      <div class="col-12 col-md-6 col-lg-4">
        <div class="card card-hover h-100"><div class="card-body">
          <h5 class="card-title">{{ t.title }}</h5>
          <p class="mb-1"><strong>Criada em:</strong> {{ t.start_date }}</p>
          <p class="mb-1"><strong>Status:</strong> {{ t.status }}</p>
          <p class="mb-2"><strong>Total enviado:</strong> R$ {{ '%.2f'|format(totals[t.id]) }}</p>
          <a class="btn btn-sm btn-outline-primary" href="{{ url_for('trip_detail', trip_id=t.id) }}">Abrir</a>
          <a class="btn btn-sm btn-primary ms-2" href="{{ url_for('expense_new', trip_id=t.id) }}">Lançar despesa</a>
        </div></div>
      </div>
      {% else %}
        <p>Nenhuma viagem criada ainda.</p>
      {% endfor %}
    </div>
    """
    return render_page(body, trips=trips, totals=totals)


# >>> Detalhe da viagem (admin vê todas; funcionário só a sua)
@app.route('/viagens/<int:trip_id>')
@login_required()
def trip_detail(trip_id):
    conn = get_db()
    t = conn.execute("SELECT * FROM trips WHERE id=?", (trip_id,)).fetchone()
    if not t:
        conn.close()
        abort(404)
    if session.get('role') != 'admin' and t['user_id'] != session['user_id']:
        conn.close()
        abort(403)

    expenses = conn.execute("SELECT * FROM expenses WHERE trip_id=? ORDER BY id DESC", (trip_id,)).fetchall()
    deposits = conn.execute(
        "SELECT * FROM deposits WHERE trip_id=? AND user_id=? ORDER BY date DESC",
        (trip_id, t['user_id'])
    ).fetchall()
    total_ok = conn.execute(
        "SELECT COALESCE(SUM(amount),0) s FROM expenses WHERE trip_id=? AND status!='rejeitado'",
        (trip_id,)
    ).fetchone()['s']
    user = conn.execute("SELECT name FROM users WHERE id=?", (t['user_id'],)).fetchone()
    conn.close()
    body = """
    <div class="d-flex justify-content-between align-items-center mb-3">
      <h4>Viagem: {{ t.title }}</h4>
      <div class="text-muted small">Funcionário: {{ user.name }}</div>
    </div>
    <div class="row g-4">
      <div class="col-12 col-lg-8">
        <div class="d-flex justify-content-between align-items-center">
          <h6>Despesas</h6>
          <a class="btn btn-sm btn-primary" href="{{ url_for('expense_new', trip_id=t.id) }}">Nova despesa</a>
        </div>
        <div class="list-group mt-2">
        {% for e in expenses %}
          <div class="list-group-item">
            <div class="d-flex justify-content-between">
              <div>
                <div class="fw-semibold">{{ e.category }} • R$ {{ '%.2f'|format(e.amount) }}</div>
                <div class="small text-muted">{{ e.date }} • {{ e.description }}</div>
                <div class="small">Status: <span class="badge text-bg-{{ 'success' if e.status=='aprovado' else ('danger' if e.status=='rejeitado' else 'secondary') }}">{{ e.status }}</span></div>
              </div>
              {% if e.receipt_path %}
              <a class="btn btn-sm btn-outline-secondary" target="_blank" href="{{ url_for('uploads', filename=e.receipt_path.split('/')[-1]) }}">Nota</a>
              {% endif %}
            </div>
          </div>
        {% else %}
          <div class="text-muted">Sem despesas ainda.</div>
        {% endfor %}
        </div>
      </div>
      <div class="col-12 col-lg-4">
        <div class="card"><div class="card-body">
          <div class="d-flex justify-content-between"><span>Total válido</span><strong>R$ {{ '%.2f'|format(total_ok) }}</strong></div>
          <div class="d-flex justify-content-between"><span>Criada em</span><span>{{ t.start_date }}</span></div>
          <hr>
          <h6>Depósitos nesta viagem</h6>
          <ul class="list-unstyled">
            {% for d in deposits %}
              <li>R$ {{ '%.2f'|format(d.amount) }} • {{ d.date }} <span class="text-muted small">{{ d.note }}</span></li>
            {% else %}
              <li class="text-muted">Sem depósitos.</li>
            {% endfor %}
          </ul>
        </div></div>
      </div>
    </div>
    """
    return render_page(body, t=t, expenses=expenses, deposits=deposits, total_ok=total_ok, user=user)


# >>> Nova despesa
@app.route('/despesas/nova', methods=['GET','POST'])
@login_required('employee')
def expense_new():
    pre_trip_id = request.args.get('trip_id', type=int)

    conn = get_db()
    trips = conn.execute(
        "SELECT * FROM trips WHERE user_id=? ORDER BY id DESC",
        (session['user_id'],)
    ).fetchall()
    conn.close()

    if not trips:
        body = """
        <div class="alert alert-warning">
          Você ainda não possui viagens cadastradas. Peça ao gestor para criar uma viagem para você em
          <strong>Admin → Viagens</strong>. Depois retorne aqui para lançar as despesas.
        </div>
        <a class="btn btn-secondary" href="{{ url_for('my_trips') }}">Voltar</a>
        """
        return render_page(body)

    if request.method == 'POST':
        trip_id = int(request.form.get('trip_id'))
        date = request.form.get('date')
        category = request.form.get('category')
        description = request.form.get('description','').strip()
        amount = float(request.form.get('amount'))
        receipt = request.files.get('receipt')

        receipt_path = ''
        if receipt and receipt.filename:
            if not ext_allowed(receipt.filename):
                flash('Formato de arquivo não permitido.', 'danger')
                return redirect(request.url)
            fname = secure_filename(f"{uuid.uuid4().hex}_{receipt.filename}")
            fpath = os.path.join(UPLOAD_DIR, fname)
            receipt.save(fpath)
            resize_if_image(fpath)
            receipt_path = fpath

        conn = get_db()
        conn.execute(
            "INSERT INTO expenses(trip_id,user_id,date,category,description,amount,receipt_path,status,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (trip_id, session['user_id'], date, category, description, amount, receipt_path,
             'pendente', dt.datetime.utcnow().isoformat())
        )
        conn.commit()
        conn.close()
        flash('Despesa enviada para aprovação.', 'success')
        return redirect(url_for('trip_detail', trip_id=trip_id))

    body = """
    <h4>Nova despesa</h4>
    <form method="post" enctype="multipart/form-data">
      <div class="row g-3">
        <div class="col-md-4">
          <label class="form-label">Viagem</label>
          <select class="form-select" name="trip_id" required>
            {% for t in trips %}
              <option value="{{ t.id }}" {% if pre_trip_id and pre_trip_id == t.id %}selected{% endif %}>
                {{ t.title }} ({{ t.start_date }})
              </option>
            {% endfor %}
          </select>
        </div>
        <div class="col-md-3">
          <label class="form-label">Data</label>
          <input type="date" class="form-control" name="date" required value="{{ today }}">
        </div>
        <div class="col-md-5">
          <label class="form-label">Categoria</label>
          <select class="form-select" name="category" required>
            <option>Alimentação</option><option>Transporte</option><option>Hospedagem</option>
            <option>Combustível</option><option>Pedágio/Estacionamento</option><option>Outros</option>
          </select>
        </div>
        <div class="col-md-8">
          <label class="form-label">Descrição</label>
          <input class="form-control" name="description" placeholder="Ex.: Almoço com cliente">
        </div>
        <div class="col-md-4">
          <label class="form-label">Valor (R$)</label>
          <input type="number" step="0.01" class="form-control" name="amount" required>
        </div>
        <div class="col-md-6">
          <label class="form-label">Foto/arquivo da nota (JPG/PNG/PDF)</label>
          <input type="file" class="form-control" name="receipt" accept="image/*,.pdf">
        </div>
      </div>
      <button class="btn btn-primary mt-3">Enviar</button>
      <a class="btn btn-light mt-3" href="{{ url_for('my_trips') }}">Cancelar</a>
    </form>
    """
    return render_page(body, trips=trips, pre_trip_id=pre_trip_id, today=dt.date.today().isoformat())


# =========================
# Uploads
# =========================
@app.route('/uploads/<path:filename>')
@login_required()
def uploads(filename):
    return send_from_directory(UPLOAD_DIR, filename, as_attachment=False)


# =========================
# Admin: Viagens (listar + reatribuir)
# =========================
@app.route('/admin/viagens')
@login_required('admin')
def admin_trips():
    conn = get_db()
    rows = conn.execute(
        "SELECT t.*, u.name as uname, u.id as uid FROM trips t JOIN users u ON u.id=t.user_id ORDER BY t.id DESC"
    ).fetchall()
    users = conn.execute("SELECT id,name FROM users ORDER BY name").fetchall()
    conn.close()
    body = """
    <div class="d-flex justify-content-between align-items-center mb-3">
      <h4>Viagens</h4>
      <a class="btn btn-primary" href="{{ url_for('trip_new') }}">Criar viagem</a>
    </div>
    <div class="table-responsive">
      <table class="table table-sm align-middle">
        <thead><tr><th>ID</th><th>Título</th><th>Funcionário</th><th>Criada em</th><th>Status</th><th>Reatribuir</th><th></th></tr></thead>
        <tbody>
          {% for t in rows %}
            <tr>
              <td>{{ t.id }}</td>
              <td>{{ t.title }}</td>
              <td>{{ t.uname }}</td>
              <td>{{ t.start_date }}</td>
              <td>{{ t.status }}</td>
              <td style="min-width:280px">
                <form method="post" action="{{ url_for('admin_trip_reassign', trip_id=t.id) }}" class="d-flex gap-2">
                  <select class="form-select form-select-sm" name="user_id" required>
                    {% for u in users %}
                      <option value="{{ u.id }}" {% if u.id==t.uid %}selected{% endif %}>{{ u.name }}</option>
                    {% endfor %}
                  </select>
                  <button class="btn btn-sm btn-outline-primary">Salvar</button>
                </form>
              </td>
              <td><a class="btn btn-sm btn-outline-primary" href="{{ url_for('trip_detail', trip_id=t.id) }}">Abrir</a></td>
            </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
    """
    return render_page(body, rows=rows, users=users)


@app.post('/admin/viagens/<int:trip_id>/reassign')
@login_required('admin')
def admin_trip_reassign(trip_id):
    new_user_id = int(request.form.get('user_id'))
    conn = get_db()
    user = conn.execute("SELECT id FROM users WHERE id=?", (new_user_id,)).fetchone()
    if not user:
        conn.close()
        flash('Funcionário inválido.', 'danger')
        return redirect(url_for('admin_trips'))
    conn.execute("UPDATE trips SET user_id=? WHERE id=?", (new_user_id, trip_id))
    conn.commit()
    conn.close()
    flash('Viagem reatribuída com sucesso.', 'success')
    return redirect(url_for('admin_trips'))


# >>> CRIAR VIAGEM (fim = início)
@app.route('/viagens/nova', methods=['GET','POST'])
@login_required('admin')
def trip_new():
    conn = get_db()
    users = conn.execute("SELECT id,name FROM users ORDER BY name").fetchall()
    conn.close()
    if request.method == 'POST':
        user_id = int(request.form.get('user_id'))
        title = request.form.get('title','').strip()
        start_date = request.form.get('start_date') or dt.date.today().isoformat()
        daily_limit = float(request.form.get('daily_limit') or 0)
        conn = get_db()
        conn.execute(
            "INSERT INTO trips(user_id,title,start_date,end_date,daily_limit) VALUES(?,?,?,?,?)",
            (user_id, title, start_date, start_date, daily_limit)
        )
        conn.commit()
        conn.close()
        flash('Viagem criada pelo gestor.', 'success')
        return redirect(url_for('admin_trips'))
    body = """
    <h4>Criar viagem</h4>
    <form method="post">
      <div class="row g-3">
        <div class="col-md-5"><label class="form-label">Funcionário</label>
          <select class="form-select" name="user_id" required>
            {% for u in users %}<option value="{{ u.id }}">{{ u.name }}</option>{% endfor %}
          </select></div>
        <div class="col-md-4"><label class="form-label">Data</label>
          <input type="date" class="form-control" name="start_date" value="{{ today }}"></div>
        <div class="col-md-3"><label class="form-label">Limite diário (opcional)</label>
          <input type="number" step="0.01" class="form-control" name="daily_limit"></div>
        <div class="col-12"><label class="form-label">Título</label>
          <input class="form-control" name="title" placeholder="Ex.: Viagem a cliente XYZ" required></div>
      </div>
      <button class="btn btn-primary mt-3">Salvar</button>
      <a class="btn btn-light mt-3" href="{{ url_for('admin_trips') }}">Cancelar</a>
    </form>
    """
    return render_page(body, users=users, today=dt.date.today().isoformat())


# =========================
# Admin: painel, usuários, depósitos, relatórios
# =========================
@app.route('/admin')
@login_required('admin')
def admin_dashboard():
    conn = get_db()
    pend = conn.execute(
        "SELECT e.*, u.name as uname, t.title as ttitle "
        "FROM expenses e JOIN users u ON u.id=e.user_id JOIN trips t ON t.id=e.trip_id "
        "WHERE e.status='pendente' ORDER BY e.created_at ASC"
    ).fetchall()
    totals = conn.execute(
        "SELECT u.id as uid, u.name, "
        "COALESCE(SUM(CASE WHEN e.status!='rejeitado' THEN e.amount ELSE 0 END),0) as total_despesas, "
        "COALESCE((SELECT SUM(amount) FROM deposits d WHERE d.user_id=u.id),0) as total_depositos "
        "FROM users u LEFT JOIN expenses e ON e.user_id=u.id GROUP BY u.id ORDER BY u.name"
    ).fetchall()
    conn.close()
    body = """
    <h4>Painel do Gestor</h4>
    <div class="row g-4">
      <div class="col-lg-7">
        <div class="card"><div class="card-body">
          <h6 class="mb-3">Despesas pendentes</h6>
          <div class="list-group">
            {% for e in pend %}
              <div class="list-group-item">
                <div class="d-flex justify-content-between">
                  <div>
                    <div class="fw-semibold">{{ e.uname }} • {{ e.ttitle }} • {{ e.category }} • R$ {{ '%.2f'|format(e.amount) }}</div>
                    <div class="small text-muted">{{ e.date }} • {{ e.description }}</div>
                  </div>
                  <div>
                    {% if e.receipt_path %}<a class="btn btn-sm btn-outline-secondary" target="_blank" href="{{ url_for('uploads', filename=e.receipt_path.split('/')[-1]) }}">Nota</a>{% endif %}
                    <a class="btn btn-sm btn-success" href="{{ url_for('admin_set_expense_status', expense_id=e.id, status='aprovado') }}">Aprovar</a>
                    <a class="btn btn-sm btn-danger" href="{{ url_for('admin_set_expense_status', expense_id=e.id, status='rejeitado') }}">Rejeitar</a>
                  </div>
                </div>
              </div>
            {% else %}
              <div class="text-muted">Nenhuma despesa pendente.</div>
            {% endfor %}
          </div>
        </div></div>
      </div>
      <div class="col-lg-5">
        <div class="card"><div class="card-body">
          <h6 class="mb-3">Resumo por funcionário</h6>
          <div class="table-responsive">
          <table class="table table-sm">
            <thead><tr><th>Funcionário</th><th>Despesas</th><th>Depósitos</th><th>Saldo</th></tr></thead>
            <tbody>
              {% for r in totals %}
                <tr>
                  <td>{{ r.name }}</td>
                  <td>R$ {{ '%.2f'|format(r.total_despesas) }}</td>
                  <td>R$ {{ '%.2f'|format(r.total_depositos) }}</td>
                  <td><strong>R$ {{ '%.2f'|format(r.total_depositos - r.total_despesas) }}</strong></td>
                </tr>
              {% endfor %}
            </tbody>
          </table>
          </div>
        </div></div>
      </div>
    </div>
    """
    return render_page(body, pend=pend, totals=totals)


@app.route('/admin/usuarios', methods=['GET','POST'])
@login_required('admin')
def admin_users():
    conn = get_db()
    if request.method == 'POST' and request.form.get('formname') == 'create':
        name = request.form.get('name','').strip()
        email = request.form.get('email','').strip().lower()
        role = request.form.get('role','employee')
        password = request.form.get('password','123456')
        try:
            conn.execute("INSERT INTO users(name,email,role,password_hash) VALUES(?,?,?,?)",
                         (name, email, role, generate_password_hash(password)))
            conn.commit()
            flash('Usuário criado.', 'success')
        except sqlite3.IntegrityError:
            flash('E-mail já cadastrado.', 'danger')
    users = conn.execute("SELECT * FROM users ORDER BY role DESC, name").fetchall()
    conn.close()
    body = """
    <div class="row g-4">
      <div class="col-lg-6">
        <h5>Novo usuário</h5>
        <form method="post">
          <input type="hidden" name="formname" value="create">
          <div class="mb-2"><label class="form-label">Nome</label><input class="form-control" name="name" required></div>
          <div class="mb-2"><label class="form-label">E-mail</label><input type="email" class="form-control" name="email" required></div>
          <div class="mb-2"><label class="form-label">Perfil</label>
            <select class="form-select" name="role">
              <option value="employee">Funcionário</option>
              <option value="admin">Administrador</option>
            </select>
          </div>
          <div class="mb-2"><label class="form-label">Senha inicial</label><input type="text" class="form-control" name="password" value="123456"></div>
          <button class="btn btn-primary">Criar</button>
        </form>
      </div>
      <div class="col-lg-6">
        <h5>Usuários</h5>
        <div class="list-group">
          {% for u in users %}
            <div class="list-group-item">
              <div class="d-flex justify-content-between align-items-start">
                <div class="me-3">
                  <div class="fw-semibold">{{ u.name }}
                    <span class="badge text-bg-{{ 'dark' if u.role=='admin' else 'secondary' }}">{{ u.role }}</span>
                  </div>
                  <div class="small text-muted">{{ u.email }}</div>
                </div>
                <div class="d-flex gap-2">
                  <form method="post" action="{{ url_for('admin_set_user_password', user_id=u.id) }}" class="d-flex gap-2">
                    <input type="password" class="form-control form-control-sm" name="new_password" placeholder="Nova senha" required>
                    <button class="btn btn-sm btn-outline-primary">Definir</button>
                  </form>
                  <form method="post" action="{{ url_for('admin_delete_user', user_id=u.id) }}" onsubmit="return confirm('Remover este usuário?');">
                    <button class="btn btn-sm btn-outline-danger">Remover</button>
                  </form>
                </div>
              </div>
            </div>
          {% endfor %}
        </div>
      </div>
    </div>
    """
    return render_page(body, users=users)


@app.route('/admin/usuarios/<int:user_id>/set_password', methods=['POST'])
@login_required('admin')
def admin_set_user_password(user_id):
    newp = request.form.get('new_password','').strip()
    if not newp:
        flash('Informe a nova senha.', 'danger')
        return redirect(url_for('admin_users'))
    conn = get_db()
    conn.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(newp), user_id))
    conn.commit()
    conn.close()
    flash('Senha atualizada.', 'success')
    return redirect(url_for('admin_users'))


@app.route('/admin/usuarios/<int:user_id>/delete', methods=['POST'])
@login_required('admin')
def admin_delete_user(user_id):
    if user_id == session.get('user_id'):
        flash('Você não pode remover a si mesmo.', 'danger')
        return redirect(url_for('admin_users'))
    conn = get_db()
    user = conn.execute("SELECT id, role FROM users WHERE id=?", (user_id,)).fetchone()
    if not user:
        conn.close()
        flash('Usuário não encontrado.', 'warning')
        return redirect(url_for('admin_users'))
    if user['role'] == 'admin':
        admins = conn.execute("SELECT COUNT(*) c FROM users WHERE role='admin'").fetchone()['c']
        if admins <= 1:
            conn.close()
            flash('Não é possível remover o último administrador.', 'danger')
            return redirect(url_for('admin_users'))
    conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    conn.close()
    flash('Usuário removido.', 'success')
    return redirect(url_for('admin_users'))


# Rota para aprovar/rejeitar despesas
@app.route('/admin/despesa/<int:expense_id>/<status>')
@login_required('admin')
def admin_set_expense_status(expense_id, status):
    if status not in ('aprovado', 'rejeitado', 'pendente'):
        abort(400)
    conn = get_db()
    conn.execute("UPDATE expenses SET status=? WHERE id=?", (status, expense_id))
    conn.commit()
    conn.close()
    flash(f'Despesa marcada como {status}.', 'success')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/depositos', methods=['GET','POST'])
@login_required('admin')
def admin_deposits():
    conn = get_db()
    if request.method == 'POST':
        user_id = int(request.form.get('user_id'))
        trip_id = request.form.get('trip_id')
        trip_id = int(trip_id) if trip_id else None
        amount = float(request.form.get('amount'))
        date = request.form.get('date')
        note = request.form.get('note','')
        conn.execute("INSERT INTO deposits(user_id,trip_id,amount,date,note) VALUES(?,?,?,?,?)",
                     (user_id, trip_id, amount, date, note))
        conn.commit()
        flash('Depósito registrado.', 'success')
    users = conn.execute("SELECT id,name FROM users ORDER BY name").fetchall()
    trips = conn.execute("SELECT id,title FROM trips ORDER BY id DESC").fetchall()
    rows = conn.execute(
        "SELECT d.*, u.name as uname, COALESCE(t.title,'—') as ttitle "
        "FROM deposits d LEFT JOIN users u ON u.id=d.user_id "
        "LEFT JOIN trips t ON t.id=d.trip_id "
        "ORDER BY d.date DESC, d.id DESC"
    ).fetchall()
    conn.close()
    body = """
    <div class="row g-4">
      <div class="col-lg-5">
        <h5>Novo depósito</h5>
        <form method="post">
          <div class="mb-2">
            <label class="form-label">Funcionário</label>
            <select class="form-select" name="user_id" required>
              {% for u in users %}<option value="{{ u.id }}">{{ u.name }}</option>{% endfor %}
            </select>
          </div>
          <div class="mb-2">
            <label class="form-label">Viagem (opcional)</label>
            <select class="form-select" name="trip_id">
              <option value="">—</option>
              {% for t in trips %}<option value="{{ t.id }}">{{ t.title }}</option>{% endfor %}
            </select>
          </div>
          <div class="mb-2"><label class="form-label">Data</label><input type="date" class="form-control" name="date" required value="{{ today }}"></div>
          <div class="mb-2"><label class="form-label">Valor (R$)</label><input type="number" step="0.01" class="form-control" name="amount" required></div>
          <div class="mb-2"><label class="form-label">Observação</label><input class="form-control" name="note"></div>
          <button class="btn btn-primary">Registrar</button>
        </form>
      </div>
      <div class="col-lg-7">
        <h5>Depósitos</h5>
        <div class="table-responsive">
        <table class="table table-sm">
          <thead><tr><th>Data</th><th>Funcionário</th><th>Viagem</th><th>Valor</th><th>Obs.</th></tr></thead>
          <tbody>
            {% for r in rows %}
              <tr>
                <td>{{ r.date }}</td>
                <td>{{ r.uname }}</td>
                <td>{{ r.ttitle }}</td>
                <td>R$ {{ '%.2f'|format(r.amount) }}</td>
                <td>{{ r.note }}</td>
              </tr>
            {% endfor %}
          </tbody>
        </table>
        </div>
      </div>
    </div>
    """
    return render_page(body, users=users, trips=trips, rows=rows, today=dt.date.today().isoformat())


# =========================
# Relatórios
# =========================
@app.route('/admin/relatorios')
@login_required('admin')
def admin_reports():
    scope = request.args.get('scope', 'month')
    include_rejected = request.args.get('rej', '0') == '1'
    start = request.args.get('start')
    end = request.args.get('end')
    user_id = request.args.get('user_id', '')

    if scope == 'all':
        start = None
        end = None
    else:
        if not start:
            start = (dt.date.today().replace(day=1)).isoformat()
        if not end:
            end = dt.date.today().isoformat()

    conn = get_db()
    params, where = [], ["1=1"]
    if start and end:
        where.append("e.date BETWEEN ? AND ?"); params += [start, end]
    if not include_rejected:
        where.append("e.status!='rejeitado'")
    if user_id:
        where.append("u.id=?"); params.append(int(user_id))

    q = ("SELECT e.*, u.name as uname, t.title as ttitle "
         "FROM expenses e JOIN users u ON u.id=e.user_id JOIN trips t ON t.id=e.trip_id "
         f"WHERE {' AND '.join(where)} ORDER BY e.date ASC")
    rows = conn.execute(q, params).fetchall()
    users = conn.execute("SELECT id,name FROM users ORDER BY name").fetchall()
    total = sum([r['amount'] for r in rows])
    conn.close()

    body = """
    <h4>Relatórios</h4>
    <form class="row g-2 mb-3">
      <div class="col-md-3">
        <label class="form-label">Escopo</label>
        <select class="form-select" name="scope" onchange="this.form.submit()">
          <option value="month" {% if scope!='all' %}selected{% endif %}>Mês atual / Intervalo</option>
          <option value="all" {% if scope=='all' %}selected{% endif %}>Todos os períodos</option>
        </select>
      </div>

      <div class="col-md-3">
        <label class="form-label">De</label>
        <input type="date" class="form-control" name="start" value="{{ start or '' }}" {% if scope=='all' %}disabled{% endif %}>
      </div>
      <div class="col-md-3">
        <label class="form-label">Até</label>
        <input type="date" class="form-control" name="end" value="{{ end or '' }}" {% if scope=='all' %}disabled{% endif %}>
      </div>

      <div class="col-md-3">
        <label class="form-label">Funcionário</label>
        <select class="form-select" name="user_id">
          <option value="">Todos</option>
          {% for u in users %}
            <option value="{{ u.id }}" {% if request.args.get('user_id')|int == u.id %}selected{% endif %}>{{ u.name }}</option>
          {% endfor %}
        </select>
      </div>

      <div class="col-md-3">
        <label class="form-label d-block">Opções</label>
        <div class="form-check">
          <input class="form-check-input" type="checkbox" id="rej" name="rej" value="1" {% if include_rejected %}checked{% endif %}>
          <label class="form-check-label" for="rej">Incluir rejeitados</label>
        </div>
      </div>

      <div class="col-md-6 d-flex align-items-end gap-2">
        <button class="btn btn-secondary">Filtrar</button>
        <a class="btn btn-outline-primary" href="{{ url_for('admin_reports_csv', scope=scope, start=start, end=end, user_id=request.args.get('user_id',''), rej=('1' if include_rejected else '0')) }}">Exportar CSV</a>
      </div>
    </form>

    <div class="table-responsive">
    <table class="table table-sm">
      <thead><tr><th>Data</th><th>Funcionário</th><th>Viagem</th><th>Categoria</th><th>Descrição</th><th>Status</th><th>Valor</th><th>Nota</th></tr></thead>
      <tbody>
        {% for r in rows %}
          <tr>
            <td>{{ r.date }}</td>
            <td>{{ r.uname }}</td>
            <td>{{ r.ttitle }}</td>
            <td>{{ r.category }}</td>
            <td>{{ r.description }}</td>
            <td>{{ r.status }}</td>
            <td>R$ {{ '%.2f'|format(r.amount) }}</td>
            <td>{% if r.receipt_path %}<a target="_blank" href="{{ url_for('uploads', filename=r.receipt_path.split('/')[-1]) }}">Abrir</a>{% endif %}</td>
          </tr>
        {% endfor %}
      </tbody>
      <tfoot><tr><th colspan="6" class="text-end">Total</th><th>R$ {{ '%.2f'|format(total) }}</th><th></th></tr></tfoot>
    </table>
    </div>
    """
    return render_page(body, rows=rows, users=users, start=start, end=end,
                       scope=scope, include_rejected=include_rejected, total=total)


@app.route('/admin/relatorios.csv')
@login_required('admin')
def admin_reports_csv():
    scope = request.args.get('scope', 'month')
    include_rejected = request.args.get('rej', '0') == '1'
    start = request.args.get('start')
    end = request.args.get('end')
    user_id = request.args.get('user_id', '')

    if scope != 'all':
        if not start:
            start = (dt.date.today().replace(day=1)).isoformat()
        if not end:
            end = dt.date.today().isoformat()
    else:
        start = None
        end = None

    conn = get_db()
    params, where = [], ["1=1"]
    if start and end:
        where.append("e.date BETWEEN ? AND ?"); params += [start, end]
    if not include_rejected:
        where.append("e.status!='rejeitado'")
    if user_id:
        where.append("u.id=?"); params.append(int(user_id))

    q = ("SELECT e.date, u.name as funcionario, t.title as viagem, "
         "e.category, e.description, e.status, e.amount, e.receipt_path "
         "FROM expenses e JOIN users u ON u.id=e.user_id JOIN trips t ON t.id=e.trip_id "
         f"WHERE {' AND '.join(where)} ORDER BY e.date ASC")
    rows = conn.execute(q, params).fetchall()
    conn.close()

    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["Data","Funcionário","Viagem","Categoria","Descrição","Status","Valor","Arquivo"])
    for r in rows:
        writer.writerow([r["date"], r["funcionario"], r["viagem"], r["category"],
                         r["description"], r["status"], f"{r['amount']:.2f}",
                         os.path.basename(r["receipt_path"] or "")])
    return Response(out.getvalue().encode("utf-8"), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=relatorio_gastos.csv"})


# =========================
# API REST básica
# =========================
API_TOKENS = {}


def issue_token(user_id: int):
    return f"{user_id}|{uuid.uuid4().hex}|{int(dt.datetime.utcnow().timestamp())}"


def api_auth_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        auth = request.headers.get('Authorization','')
        if not auth.startswith('Bearer '):
            return jsonify({'error':'missing_token'}), 401
        token = auth.split(' ',1)[1]
        uid = API_TOKENS.get(token)
        if not uid:
            return jsonify({'error':'invalid_token'}), 401
        request.user_id = uid
        return f(*args, **kwargs)
    return wrapper


@app.post('/api/login')
def api_login():
    data = request.json or {}
    email = data.get('email','').lower().strip()
    password = data.get('password','')
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    conn.close()
    if user and check_password_hash(user['password_hash'], password):
        token = issue_token(user['id'])
        API_TOKENS[token] = user['id']
        return jsonify({'token': token, 'user': {'id': user['id'], 'name': user['name'], 'role': user['role']}})
    return jsonify({'error':'invalid_credentials'}), 401


@app.get('/api/trips')
@api_auth_required
def api_trips():
    conn = get_db()
    rows = conn.execute("SELECT * FROM trips WHERE user_id=? ORDER BY id DESC", (request.user_id,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.get('/api/expenses')
@api_auth_required
def api_expenses_list():
    conn = get_db()
    rows = conn.execute("SELECT * FROM expenses WHERE user_id=? ORDER BY id DESC", (request.user_id,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.post('/api/expenses')
@api_auth_required
def api_expenses_create():
    trip_id = int(request.form.get('trip_id'))
    date = request.form.get('date')
    category = request.form.get('category')
    description = request.form.get('description','')
    amount = float(request.form.get('amount'))
    receipt = request.files.get('receipt')

    receipt_path = ''
    if receipt and receipt.filename:
        if not ext_allowed(receipt.filename):
            return jsonify({'error':'file_type_not_allowed'}), 400
        fname = secure_filename(f"{uuid.uuid4().hex}_{receipt.filename}")
        fpath = os.path.join(UPLOAD_DIR, fname)
        receipt.save(fpath)
        resize_if_image(fpath)
        receipt_path = fpath

    conn = get_db()
    conn.execute(
        "INSERT INTO expenses(trip_id,user_id,date,category,description,amount,receipt_path,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (trip_id, request.user_id, date, category, description, amount, receipt_path, 'pendente', dt.datetime.utcnow().isoformat())
    )
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


# =========================
# Execução
# =========================
if __name__ == '__main__':
    app.run(debug=True, use_reloader=False, threaded=False)
