"""Configurações e funções compartilhadas entre cliente.py e servidor.py."""
import os
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

# Pasta compartilhada pelos dois sistemas. Se cliente e servidor estiverem em
# máquinas diferentes, aponte para uma pasta de rede: set DADOS_DIR=\\servidor\fila
DADOS_DIR = Path(os.environ.get("DADOS_DIR", "./dados")).resolve()
SAIDA_DIR = DADOS_DIR / "saida"
LOG_DIR = DADOS_DIR / "logs"
DB_PATH = DADOS_DIR / "fila.db"
RETENCAO_HORAS = 3

# ---------------------------------------------------------------- usuários
USUARIOS = {
    "220": ["CT-ADILSON"],
    "221": ["CT-EDUARDO FIGUEIREDO"],
    "222": ["CT-GISELE FERREIRA"],
    "224": ["CT-FLAVIO", "CT-ELISANGELA"],
    "225": ["CT-DANIELLI"],
    "226": ["CT-REGINA PONTES"],
    "227": ["CT-ELIZABETH SOUZA"],
    "228": ["CT-DANIEL"],
    "229": ["CT-RODRIGO AZEVEDO"],
    "256": ["CT-SIMONE SILVA"],
    "257": ["CT-BRUNA MOTTA"],
    "259": ["CT-LUCAS LOPES", "CT-TALITA SILVA"],
    "260": ["CT-GABRIELLY"],
    "262": ["CT-CLEBER"],
    "263": ["CT-LEONARDO SANTOS", "CT-KEILLA", "CT-MARCELO SOARES"],
    "264": ["CT-EDUARDO ABREU", "CT-SERGIO"],
    "265": ["CT-LUCIANO"],
    "266": ["CT-RENATA NASCIMENTO"],
    "268": ["CT-PAULO FREITAS"],
    "294": ["CT-RAYANE"],
}

# ------------------------------------------- EDITE: empresas e serviços
EMPRESAS = ["Empresa A", "Empresa B", "Empresa C"]
SERVICOS = ["Relatório 1", "Relatório 2", "Relatório 3"]

# ---------------------------------------------------------------- banco
def agora() -> datetime:
    return datetime.now()


def fmt(iso: str | None) -> str:
    return datetime.fromisoformat(iso).strftime("%d/%m/%Y %H:%M:%S") if iso else "-"


def conectar() -> sqlite3.Connection:
    DADOS_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


def init_db():
    SAIDA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with conectar() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS pedidos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                numero TEXT NOT NULL,
                nome TEXT NOT NULL,
                empresa TEXT NOT NULL,
                servico TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'fila',  -- fila|processando|pronto|erro|expirado
                criado_em TEXT NOT NULL,
                inicio_em TEXT,
                entregue_em TEXT,
                arquivo TEXT,
                erro TEXT
            )""")


def codigo(pid: int) -> str:
    return f"P{pid:04d}"


def criar_pedido(numero, nome, empresa, servico) -> int:
    with conectar() as con:
        cur = con.execute(
            "INSERT INTO pedidos (numero,nome,empresa,servico,criado_em) VALUES (?,?,?,?,?)",
            (numero, nome, empresa, servico, agora().isoformat()),
        )
        return cur.lastrowid


def pedidos_do_usuario(numero):
    with conectar() as con:
        return con.execute(
            "SELECT * FROM pedidos WHERE numero=? ORDER BY id DESC", (numero,)
        ).fetchall()


def posicao_na_fila(pid: int) -> int:
    with conectar() as con:
        return con.execute(
            "SELECT COUNT(*) FROM pedidos WHERE status IN ('fila','processando') AND id<=?",
            (pid,),
        ).fetchone()[0]


def processo_atual():
    with conectar() as con:
        return con.execute("SELECT * FROM pedidos WHERE status='processando' LIMIT 1").fetchone()


def fila_espera():
    with conectar() as con:
        return con.execute("SELECT * FROM pedidos WHERE status='fila' ORDER BY id").fetchall()


def ultimos_pedidos(n=30):
    with conectar() as con:
        return con.execute(
            "SELECT * FROM pedidos WHERE status NOT IN ('fila','processando') ORDER BY id DESC LIMIT ?",
            (n,),
        ).fetchall()


# ---------------------------------------------------- operações do servidor
def pegar_proximo():
    """Pega o pedido mais antigo da fila e marca como 'processando'."""
    with conectar() as con:
        con.execute("BEGIN IMMEDIATE")
        p = con.execute("SELECT * FROM pedidos WHERE status='fila' ORDER BY id LIMIT 1").fetchone()
        if p:
            con.execute(
                "UPDATE pedidos SET status='processando', inicio_em=? WHERE id=?",
                (agora().isoformat(), p["id"]),
            )
        return p


def concluir(pid: int, arquivo: Path):
    with conectar() as con:
        con.execute(
            "UPDATE pedidos SET status='pronto', entregue_em=?, arquivo=? WHERE id=?",
            (agora().isoformat(), str(arquivo), pid),
        )


def falhar(pid: int, msg: str):
    with conectar() as con:
        con.execute(
            "UPDATE pedidos SET status='erro', entregue_em=?, erro=? WHERE id=?",
            (agora().isoformat(), msg, pid),
        )


def recuperar_travados():
    """Se o servidor caiu no meio de um processo, devolve ele pra fila."""
    with conectar() as con:
        con.execute("UPDATE pedidos SET status='fila', inicio_em=NULL WHERE status='processando'")


def limpar_expirados():
    """Apaga arquivos com mais de RETENCAO_HORAS horas."""
    limite = (agora() - timedelta(hours=RETENCAO_HORAS)).isoformat()
    with conectar() as con:
        for p in con.execute(
            "SELECT id FROM pedidos WHERE status='pronto' AND entregue_em<?", (limite,)
        ).fetchall():
            shutil.rmtree(SAIDA_DIR / codigo(p["id"]), ignore_errors=True)
            con.execute("UPDATE pedidos SET status='expirado', arquivo=NULL WHERE id=?", (p["id"],))


# ------------------------------------------------------------------ log
def escrever_log(nome, servico, empresa, entrega: str):
    """Formato: data - fulano - solicitação - empresa - data de entrega"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    arq = LOG_DIR / f"{agora():%Y-%m-%d}.log"
    linha = f"{agora():%d/%m/%Y} - {nome} - {servico} - {empresa} - {entrega}\n"
    with open(arq, "a", encoding="utf-8") as f:
        f.write(linha)


def ler_log_do_dia() -> str:
    arq = LOG_DIR / f"{agora():%Y-%m-%d}.log"
    return arq.read_text(encoding="utf-8") if arq.exists() else ""
