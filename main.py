import streamlit as st
import threading
import uvicorn
import requests
import json
import os
import queue
import time
from datetime import datetime, timedelta
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
import pandas as pd

# ==========================================
# 1. CONFIGURAÇÃO DA API FASTAPI (SERVIDOR)
# ==========================================
app_api = FastAPI(title="Servidor de Automação")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
RELATORIO_DIR = os.path.join(BASE_DIR, "relatorios_temp")

os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(RELATORIO_DIR, exist_ok=True)

task_queue = queue.Queue()
is_processing = False
historico_pedidos = []

class Pedido(BaseModel):
    usuario_codigo: str
    usuario_nome: str
    empresa: str
    servico: str

def registrar_log(usuario_nome, servico, empresa, data_entrega):
    data_hoje = datetime.now().strftime("%Y-%m-%d")
    data_hora_log = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_file = os.path.join(LOG_DIR, f"log_{data_hoje}.txt")
    linha_log = f"{data_hora_log} - {usuario_nome} - {servico} - {empresa} - {data_entrega}\n"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(linha_log)

def limpar_arquivos_antigos():
    """Remove arquivos criados há mais de 3 horas."""
    agora = datetime.now()
    for filename in os.listdir(RELATORIO_DIR):
        file_path = os.path.join(RELATORIO_DIR, filename)
        if os.path.isfile(file_path):
            tempo_criacao = datetime.fromtimestamp(os.path.getmtime(file_path))
            if agora - tempo_criacao > timedelta(hours=3):
                try:
                    os.remove(file_path)
                except Exception as e:
                    print(f"Erro ao remover {filename}: {e}")

def processar_fila():
    global is_processing
    if is_processing:
        return
    
    is_processing = True
    while not task_queue.empty():
        limpar_arquivos_antigos()
        pedido = task_queue.get()
        pedido['status'] = "Em Processamento"
        
        # Simulação do tempo da macro
        time.sleep(8)
        
        data_entrega = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        nome_arquivo = f"Relatorio_ID{pedido['id']}_USER{pedido['usuario_codigo']}.xlsx"
        caminho_arquivo = os.path.join(RELATORIO_DIR, nome_arquivo)
        
        df = pd.DataFrame({
            "ID Solicitação": [pedido['id']],
            "Empresa": [pedido['empresa']],
            "Serviço": [pedido['servico']],
            "Solicitado Por": [pedido['usuario_nome']],
            "Data Entrega": [data_entrega]
        })
        df.to_excel(caminho_arquivo, index=False)
        
        pedido['status'] = "Concluído"
        pedido['arquivo'] = nome_arquivo
        pedido['data_entrega'] = data_entrega
        
        registrar_log(pedido['usuario_nome'], pedido['servico'], pedido['empresa'], data_entrega)
        task_queue.task_done()
        
    is_processing = False

@app_api.post("/solicitar")
def criar_solicitacao(pedido: Pedido, background_tasks: BackgroundTasks):
    pedido_id = len(historico_pedidos) + 1
    novo_pedido = {
        "id": pedido_id,
        "usuario_codigo": pedido.usuario_codigo,
        "usuario_nome": pedido.usuario_nome,
        "empresa": pedido.empresa,
        "servico": pedido.servico,
        "status": "Na Fila",
        "data_solicitacao": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "data_entrega": None,
        "arquivo": None
    }
    historico_pedidos.append(novo_pedido)
    task_queue.put(novo_pedido)
    background_tasks.add_task(processar_fila)
    return {"message": "Solicitação em fila", "id": pedido_id}

@app_api.get("/historico/{usuario_codigo}")
def obter_historico(usuario_codigo: str):
    limpar_arquivos_antigos()
    pedidos_usuario = [p for p in historico_pedidos if p['usuario_codigo'] == usuario_codigo]
    return {"pedidos": pedidos_usuario}

@app_api.get("/download/{nome_arquivo}")
def baixar_arquivo(nome_arquivo: str):
    caminho = os.path.join(RELATORIO_DIR, nome_arquivo)
    if os.path.exists(caminho):
        return FileResponse(caminho, filename=nome_arquivo)
    raise HTTPException(status_code=404, detail="Arquivo expirado ou não encontrado.")

# INICIALIZA A API EM SEGUNDO PLANO (THREAD)
def start_api():
    uvicorn.run(app_api, host="127.0.0.1", port=8000, log_level="error")

if "api_running" not in st.session_state:
    st.session_state["api_running"] = True
    thread = threading.Thread(target=start_api, daemon=True)
    thread.start()

# ==========================================
# 2. INTERFACE CLIENTE STREAMLIT
# ==========================================
SERVER_URL = "http://127.0.0.1:8000"

@st.cache_data
def carregar_usuarios():
    if os.path.exists("usuarios.json"):
        with open("usuarios.json", "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

USUARIOS = carregar_usuarios()

LISTA_EMPRESAS = [
    "Empresa Alpha S.A.", "Empresa Beta Ltda", 
    "Gama Serviços Contábeis", "Delta Comercial", "Omega Soluções"
]

LISTA_SERVICOS = [
    "Relatório Financeiro Mensal", "Extrato de Pendências Fiscais",
    "Conciliação Bancária", "Apuração de Impostos (DAS/DARF)", "Espelho de Ponto"
]

st.set_page_config(page_title="Central de Solicitação de Macros", layout="wide")

if "usuario_logado" not in st.session_state:
    st.session_state["usuario_logado"] = None

# TELA DE LOGIN
if not st.session_state["usuario_logado"]:
    st.title("🔑 Portal do Cliente - Login")
    codigo_input = st.selectbox(
        "Selecione seu Código de Acesso:", 
        options=list(USUARIOS.keys()), 
        format_func=lambda x: f"{x} - {USUARIOS[x]}"
    )
    
    if st.button("Acessar Sistema", type="primary"):
        st.session_state["usuario_logado"] = {
            "codigo": codigo_input,
            "nome": USUARIOS[codigo_input]
        }
        st.rerun()

# TELA LOGADO
else:
    user = st.session_state["usuario_logado"]
    
    st.sidebar.title("Painel do Usuário")
    st.sidebar.info(f"👤 **Código:** {user['codigo']}\n\n🏷️ **Nome:** {user['nome']}")
    if st.sidebar.button("🔴 Sair"):
        st.session_state["usuario_logado"] = None
        st.rerun()

    st.title("📋 Central de Automação de Processos")
    tab_solicitar, tab_historico = st.tabs(["🚀 Nova Solicitação", "📊 Meus Pedidos & Downloads"])

    # ABA 1: SOLICITAR
    with tab_solicitar:
        st.subheader("Solicitar Execução de Macro")
        with st.form("form_solicitacao", clear_on_submit=True):
            empresa_selecionada = st.selectbox("1. Escolha a Empresa:", LISTA_EMPRESAS)
            servico_selecionado = st.selectbox("2. Escolha o Serviço/Relatório:", LISTA_SERVICOS)
            submit = st.form_submit_button("Enviar Solicitação para a Fila")
            
            if submit:
                payload = {
                    "usuario_codigo": user["codigo"],
                    "usuario_nome": user["nome"],
                    "empresa": empresa_selecionada,
                    "servico": servico_selecionado
                }
                try:
                    res = requests.post(f"{SERVER_URL}/solicitar", json=payload)
                    if res.status_code == 200:
                        st.success("✅ Solicitação enviada à fila! Acompanhe na aba de Histórico.")
                    else:
                        st.error("Erro ao registrar solicitação.")
                except Exception as e:
                    st.error(f"Erro de conexão com o servidor: {e}")

    # ABA 2: HISTÓRICO
    with tab_historico:
        st.subheader("Histórico de Solicitações do Usuário")
        if st.button("🔄 Atualizar Status"):
            st.rerun()

        try:
            res = requests.get(f"{SERVER_URL}/historico/{user['codigo']}")
            if res.status_code == 200:
                pedidos = res.json().get("pedidos", [])
                
                if not pedidos:
                    st.info("Nenhuma solicitação encontrada.")
                else:
                    for p in reversed(pedidos):
                        with st.container():
                            col_id, col_detalhes, col_status, col_acao = st.columns([1, 4, 2, 3])
                            col_id.markdown(f"### #{p['id']}")
                            col_detalhes.write(f"**Empresa:** {p['empresa']}")
                            col_detalhes.write(f"**Serviço:** {p['servico']}")
                            col_detalhes.caption(f"Solicitado em: {p['data_solicitacao']}")
                            
                            status = p['status']
                            if status == "Na Fila":
                                col_status.warning("⏳ Na Fila")
                            elif status == "Em Processamento":
                                col_status.info("⚙️ Processando...")
                            else:
                                col_status.success("✅ Concluído")
                                if p.get('data_entrega'):
                                    col_status.caption(f"Entregue: {p['data_entrega']}")
                            
                            if p['status'] == "Concluído" and p.get('arquivo'):
                                url_dl = f"{SERVER_URL}/download/{p['arquivo']}"
                                req_file = requests.get(url_dl)
                                if req_file.status_code == 200:
                                    col_acao.download_button(
                                        label="📥 Baixar Excel",
                                        data=req_file.content,
                                        file_name=p['arquivo'],
                                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                        key=f"btn_dl_{p['id']}"
                                    )
                                else:
                                    col_acao.error("⚠️️ Expirado (>3h)")
                            else:
                                col_acao.write("Aguardando geração...")
                        st.divider()
        except Exception as e:
            st.error(f"Erro ao buscar histórico: {e}")
