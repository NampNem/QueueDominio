"""CLIENTE: streamlit run cliente.py --server.port 8501 --server.address 0.0.0.0"""
from pathlib import Path

import streamlit as st

from comum import *

st.set_page_config(page_title="Solicitação de Relatórios", page_icon="📄", layout="wide")
init_db()

# ------------------------------------------------------------------ login
if "usuario" not in st.session_state:
    st.title("📄 Solicitação de Relatórios")
    st.subheader("Login")
    numero = st.text_input("Número", max_chars=3, placeholder="ex: 220").strip()
    if numero:
        if numero in USUARIOS:
            nomes = USUARIOS[numero]
            nome = nomes[0] if len(nomes) == 1 else st.selectbox("Quem é você?", nomes)
            if len(nomes) == 1:
                st.caption(f"Olá, {nome}")
            if st.button("Entrar", type="primary"):
                st.session_state.usuario = {"numero": numero, "nome": nome}
                st.rerun()
        else:
            st.error("Número não cadastrado.")
    st.stop()

user = st.session_state.usuario

# ----------------------------------------------------------------- topo
top1, top2 = st.columns([6, 1])
top1.title("📄 Solicitação de Relatórios")
top1.caption(f"Logado como **{user['nome']}** (ramal {user['numero']})")
if top2.button("Sair"):
    del st.session_state.usuario
    st.rerun()

# ------------------------------------------------------------ solicitação
st.subheader("Nova solicitação")
with st.form("novo_pedido", clear_on_submit=True):
    f1, f2 = st.columns(2)
    empresa = f1.selectbox("Empresa", EMPRESAS)
    servico = f2.selectbox("Serviço solicitado", SERVICOS)
    if st.form_submit_button("Solicitar", type="primary"):
        pid = criar_pedido(user["numero"], user["nome"], empresa, servico)
        st.success(f"Pedido **{codigo(pid)}** enviado para a fila.")


# ------------------------------------------------------- minhas solicitações
@st.fragment(run_every=5)
def minhas_solicitacoes():
    st.subheader("Minhas solicitações")
    st.caption(f"Os arquivos ficam disponíveis por {RETENCAO_HORAS}h após a entrega.")
    pedidos = pedidos_do_usuario(user["numero"])
    if not pedidos:
        st.info("Você ainda não fez nenhuma solicitação.")
        return

    cab = st.columns([1, 2, 2, 2, 2, 2])
    for c, t in zip(cab, ["Código", "Serviço", "Empresa", "Pedido em", "Status", "Arquivo"]):
        c.markdown(f"**{t}**")

    for p in pedidos:
        c = st.columns([1, 2, 2, 2, 2, 2])
        c[0].write(codigo(p["id"]))
        c[1].write(p["servico"])
        c[2].write(p["empresa"])
        c[3].write(fmt(p["criado_em"]))
        if p["status"] == "fila":
            c[4].write(f"⏳ Na fila (posição {posicao_na_fila(p['id'])})")
        elif p["status"] == "processando":
            c[4].write("⚙️ Processando")
        elif p["status"] == "pronto":
            c[4].write(f"✅ Entregue {fmt(p['entregue_em'])}")
            arq = Path(p["arquivo"]) if p["arquivo"] else None
            if arq and arq.exists():
                c[5].download_button("⬇️ Baixar", arq.read_bytes(), file_name=arq.name,
                                     key=f"dl_{p['id']}")
            else:
                c[5].write("indisponível")
        elif p["status"] == "erro":
            c[4].write("❌ Erro")
            c[5].caption(p["erro"] or "")
        else:
            c[4].write("🗑️ Expirado")


minhas_solicitacoes()
