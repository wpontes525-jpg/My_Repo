"""
seed_data.py — Popula o banco com dados de teste realistas
Uso: python seed_data.py
"""

import sys
import os
import uuid
from datetime import date, timedelta

# Garante que o módulo db.py seja encontrado
sys.path.insert(0, os.path.dirname(__file__))
import db as _db


def get_db():
    return _db.connect()


def seed():
    conn = get_db()
    today = date.today()

    print("🌱 Iniciando inserção de dados de teste...")

    # ─────────────────────────────────────────
    # 1. FORNECEDORES (10)
    # ─────────────────────────────────────────
    fornecedores = [
        ("CPFL Energia S.A.",          "07.285.895/0001-81", "Energia Elétrica"),
        ("Vivo / Telefônica Brasil",   "02.558.157/0001-62", "Telecomunicações"),
        ("SABESP - Saneamento",        "43.776.517/0001-80", "Água e Saneamento"),
        ("Ambimed Suprimentos Ltda",   "12.345.678/0001-99", "Fornecedor de Produtos"),
        ("TechCare Sistemas Ltda",     "98.765.432/0001-11", "Tecnologia / Software"),
        ("Limpeza Total Serv. Ltda",   "11.222.333/0001-44", "Serviços de Limpeza"),
        ("Contabilidade Ferreira ME",  "44.555.666/0001-77", "Contabilidade"),
        ("Locadora Predial Cajati",    "77.888.999/0001-22", "Aluguel / Imóveis"),
        ("Farmácia Vitória Ltda",      "55.666.777/0001-33", "Farmácia / Insumos"),
        ("Gráfica e Comunicação XYZ",  "33.444.555/0001-66", "Marketing / Gráfica"),
    ]

    forn_ids = []
    for nome, cnpj, categoria in fornecedores:
        # Verifica se já existe
        row = conn.execute("SELECT id FROM fornecedores WHERE nome = ?", (nome,)).fetchone()
        if row:
            forn_ids.append(row['id'])
            print(f"  ✓ Fornecedor já existe: {nome}")
            continue
        # Insere com observacoes contendo CNPJ e categoria
        obs = f"CNPJ: {cnpj} | Categoria: {categoria}"
        conn.execute(
            "INSERT INTO fornecedores (nome, telefone, banco, agencia, conta, pix, observacoes) VALUES (?,?,?,?,?,?,?)",
            (nome, "", "", "", "", "", obs)
        )
        conn.commit()
        row = conn.execute("SELECT id FROM fornecedores WHERE nome = ?", (nome,)).fetchone()
        forn_ids.append(row['id'])
        print(f"  + Fornecedor inserido: {nome}")

    # ─────────────────────────────────────────
    # 2. CONTAS A PAGAR (10)
    # ─────────────────────────────────────────
    def venc(delta_days):
        return (today + timedelta(days=delta_days)).strftime('%Y-%m-%d')

    def venc_abs(y, m, d):
        return date(y, m, d).strftime('%Y-%m-%d')

    contas_pagar = [
        # (descricao, fornecedor_idx, valor, vencimento, recorrencia, parcela_atual, total_parcelas, status)
        # Recorrentes mensais
        ("Energia Elétrica",      0, 420.50,  venc(10),   "mensal",    1, 12, "pendente"),
        ("Internet Fibra - Vivo", 1, 189.90,  venc(15),   "mensal",    1, 12, "pendente"),
        ("Água e Esgoto - SABESP",2, 98.70,   venc(-5),   "mensal",    1, 12, "pago"),
        ("Aluguel do Imóvel",     7, 2800.00, venc(5),    "mensal",    1, 12, "pendente"),
        # Parcelado (3x)
        ("Compra de Materiais Clínicos (1/3)", 3, 650.00, venc(-15), "parcelado", 1, 3, "pago"),
        ("Compra de Materiais Clínicos (2/3)", 3, 650.00, venc(15),  "parcelado", 2, 3, "pendente"),
        ("Compra de Materiais Clínicos (3/3)", 3, 650.00, venc(45),  "parcelado", 3, 3, "pendente"),
        # Parcelado (6x)
        ("Novo Software de Gestão (1/6)", 4, 250.00, venc(-30), "parcelado", 1, 6, "pago"),
        # Únicos
        ("Serviço de Limpeza Semestral",  5, 380.00, venc(-20), "unico", 1, 1, "pago"),
        ("Honorários Contábeis - Março",  6, 550.00, venc(20),  "unico", 1, 1, "pendente"),
    ]

    for desc, forn_idx, valor, vencimento, recorrencia, parc_at, tot_parc, status in contas_pagar:
        grupo = str(uuid.uuid4())[:8]
        conn.execute(
            """INSERT INTO contas_pagar
               (descricao, valor, vencimento, status, fornecedor_id,
                recorrencia, parcela_atual, total_parcelas, grupo_recorrencia)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (desc, valor, vencimento, status, forn_ids[forn_idx],
             recorrencia, parc_at, tot_parc, grupo)
        )
        print(f"  + Conta a pagar: {desc} | R$ {valor:.2f} | {status}")

    conn.commit()

    # ─────────────────────────────────────────
    # 3. CONTAS A RECEBER (10)
    # ─────────────────────────────────────────
    contas_receber = [
        # (descricao, cliente, valor, vencimento, recorrencia, parcela_atual, total_parcelas, status)
        # Únicos recebidos
        ("Consulta Particular - Psicologia",  "Maria Aparecida Silva",  250.00, venc(-25), "unico",    1, 1, "recebido"),
        ("Sessão Fisioterapia - 10 sessões",   "João Carlos Mendes",     800.00, venc(-18), "unico",    1, 1, "recebido"),
        ("Avaliação Nutricional",              "Ana Paula Ferreira",     180.00, venc(-10), "unico",    1, 1, "recebido"),
        # Parcelado (3x)
        ("Plano Mensal Consultas (1/3)",       "Roberto Lima",           300.00, venc(-20), "parcelado",1, 3, "recebido"),
        ("Plano Mensal Consultas (2/3)",       "Roberto Lima",           300.00, venc(10),  "parcelado",2, 3, "pendente"),
        ("Plano Mensal Consultas (3/3)",       "Roberto Lima",           300.00, venc(40),  "parcelado",3, 3, "pendente"),
        # Únicos pendentes
        ("Terapia Ocupacional - Pacote 5x",   "Carla Souza",            450.00, venc(5),   "unico",    1, 1, "pendente"),
        ("Consulta Particular - Nutrição",    "Fernando Costa",         200.00, venc(12),  "unico",    1, 1, "pendente"),
        ("Sessão Psicologia - Adolescente",   "Família Rodrigues",      220.00, venc(-3),  "unico",    1, 1, "pendente"),
        # Recorrente mensal
        ("Plano de Saúde Corporativo - Mar",  "Empresa ABC Ltda",      1500.00, venc(8),   "mensal",   1,12, "pendente"),
    ]

    for desc, cliente, valor, vencimento, recorrencia, parc_at, tot_parc, status in contas_receber:
        grupo = str(uuid.uuid4())[:8]
        conn.execute(
            """INSERT INTO contas_receber
               (descricao, valor, vencimento, status, cliente,
                recorrencia, parcela_atual, total_parcelas, grupo_recorrencia)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (desc, valor, vencimento, status, cliente,
             recorrencia, parc_at, tot_parc, grupo)
        )
        print(f"  + Conta a receber: {desc} | R$ {valor:.2f} | {status}")

    conn.commit()
    conn.close()
    print("\n✅ Dados de teste inseridos com sucesso!")
    print("   → 10 fornecedores")
    print("   → 10 contas a pagar")
    print("   → 10 contas a receber")


if __name__ == '__main__':
    seed()
