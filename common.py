"""Módulo compartilhado entre cliente.py e servidor.py.
Os dois apps precisam enxergar a MESMA pasta de dados (mesma máquina ou pasta de rede).
"""
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

DATA_DIR = Path(os.environ.get("FILA_DIR", "dados"))
FILES_DIR = DATA_DIR / "arquivos"
LOGS_DIR = DATA_DIR / "logs"
DB_PATH = DATA_DIR / "fila.db"
for p in (DATA_DIR, FILES_DIR, LOGS_DIR):
    p.mkdir(parents=True, exist_ok=True)

RETENCAO_HORAS = 3
ADMIN_CODE = os.environ.get("FILA_ADMIN", "1397")  # ideal: mover para st.secrets / variável de ambiente

# ---------- EDITE AQUI ----------
USUARIOS = {
    "220": ["ADILSON"],
    "221": ["EDUARDO FIGUEIREDO"],
    "222": ["GISELE FERREIRA"],
    "224": ["FLAVIO", "ELISANGELA"],
    "225": ["DANIELLI"],
    "226": ["REGINA PONTES"],
    "227": ["ELIZABETH SOUZA"],
    "228": ["DANIEL"],
    "229": ["RODRIGO AZEVEDO"],
    "256": ["SIMONE SILVA"],
    "257": ["BRUNA MOTTA"],
    "259": ["LUCAS LOPES", "TALITA SILVA"],
    "260": ["GABRIELLY"],
    "262": ["CLEBER"],
    "263": ["LEONARDO SANTOS", "KEILLA", "MARCELO SOARES"],
    "264": ["EDUARDO ABREU", "SERGIO"],
    "265": ["LUCIANO"],
    "266": ["RENATA NASCIMENTO"],
    "268": ["PAULO FREITAS"],
    "294": ["RAYANE"],
}
EMPRESAS = ["Empresa A", "Empresa B", "Empresa C"]
SERVICOS = ["Relatório 1", "Relatório 2", "Relatório 3"]
# --------------------------------

FMT = "%Y-%m-%d %H:%M:%S"


def agora() -> str:
    return datetime.now().strftime(FMT)


def conectar():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute(
        """CREATE TABLE IF NOT EXISTS pedidos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_num TEXT, usuario_nome TEXT,
            servico TEXT, empresa TEXT,
            status TEXT,              -- fila | processando | entregue | expirado
            criado_em TEXT, entregue_em TEXT,
            arquivo TEXT, nome_arquivo TEXT)"""
    )
    return con


def criar_pedido(num, nome, servico, empresa) -> int:
    with conectar() as con:
        cur = con.execute(
            "INSERT INTO pedidos (usuario_num, usuario_nome, servico, empresa, status, criado_em) "
            "VALUES (?,?,?,?, 'fila', ?)",
            (num, nome, servico, empresa, agora()),
        )
        return cur.lastrowid


def pedidos_do_usuario(num):
    with conectar() as con:
        return con.execute(
            "SELECT * FROM pedidos WHERE usuario_num=? ORDER BY id DESC", (num,)
        ).fetchall()


def posicao_na_fila(pedido_id) -> int:
    """1 = é o próximo (ou já está sendo processado)."""
    with conectar() as con:
        return con.execute(
            "SELECT COUNT(*) FROM pedidos WHERE status IN ('fila','processando') AND id<=?",
            (pedido_id,),
        ).fetchone()[0]


def fila_completa():
    with conectar() as con:
        return con.execute(
            "SELECT * FROM pedidos WHERE status IN ('fila','processando') ORDER BY id"
        ).fetchall()


def pedido_atual():
    """Devolve o pedido em processamento; se não houver, promove o primeiro da fila."""
    with conectar() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT * FROM pedidos WHERE status='processando' ORDER BY id LIMIT 1"
        ).fetchone()
        if row is None:
            row = con.execute(
                "SELECT * FROM pedidos WHERE status='fila' ORDER BY id LIMIT 1"
            ).fetchone()
            if row is not None:
                con.execute("UPDATE pedidos SET status='processando' WHERE id=?", (row["id"],))
                row = con.execute("SELECT * FROM pedidos WHERE id=?", (row["id"],)).fetchone()
        return row


def escrever_log(pedido, entrega: str):
    linha = f"{datetime.now():%d/%m/%Y} - {pedido['usuario_nome']} - {pedido['servico']} - {pedido['empresa']} - {entrega}\n"
    arq = LOGS_DIR / f"{datetime.now():%Y-%m-%d}.txt"
    with open(arq, "a", encoding="utf-8") as f:
        f.write(linha)


def entregar(pedido_id: int, conteudo: bytes, nome_arquivo: str):
    """Salva o arquivo, marca como entregue e grava no log. Libera o próximo da fila."""
    with conectar() as con:
        pedido = con.execute("SELECT * FROM pedidos WHERE id=?", (pedido_id,)).fetchone()
        destino = FILES_DIR / f"{pedido_id}_{Path(nome_arquivo).name}"
        destino.write_bytes(conteudo)
        entrega = agora()
        con.execute(
            "UPDATE pedidos SET status='entregue', entregue_em=?, arquivo=?, nome_arquivo=? WHERE id=?",
            (entrega, str(destino), Path(nome_arquivo).name, pedido_id),
        )
    escrever_log(pedido, entrega)


def limpar_expirados():
    """Apaga arquivos entregues há mais de 3h (o registro do pedido continua no histórico)."""
    limite = (datetime.now() - timedelta(hours=RETENCAO_HORAS)).strftime(FMT)
    with conectar() as con:
        for r in con.execute(
            "SELECT id, arquivo FROM pedidos WHERE status='entregue' AND entregue_em < ?", (limite,)
        ).fetchall():
            try:
                if r["arquivo"]:
                    Path(r["arquivo"]).unlink(missing_ok=True)
            finally:
                con.execute("UPDATE pedidos SET status='expirado', arquivo=NULL WHERE id=?", (r["id"],))
    # segurança extra: arquivos órfãos antigos
    for f in FILES_DIR.glob("*"):
        if datetime.fromtimestamp(f.stat().st_mtime) < datetime.now() - timedelta(hours=RETENCAO_HORAS):
            f.unlink(missing_ok=True)


def expira_em(entregue_em: str) -> str:
    dt = datetime.strptime(entregue_em, FMT) + timedelta(hours=RETENCAO_HORAS)
    return dt.strftime("%H:%M")
