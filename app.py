"""App ÚNICO: o login decide o perfil.
 - número de usuário (220, 221...) -> tela do CLIENTE
 - 1397                            -> tela do SERVIDOR (operador)
Como é um app só, todos compartilham a mesma fila e a mesma pasta de arquivos.
"""
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

import common as c

st.set_page_config(page_title="Fila de Macros", page_icon="📨", layout="wide")


def sair():
    for k in ("user", "admin"):
        st.session_state.pop(k, None)
    st.rerun()


# ======================= LOGIN =======================
def tela_login():
    st.title("📨 Fila de Macros")
    num = st.text_input("Número de login").strip()
    nome = None
    if num in c.USUARIOS:
        nomes = c.USUARIOS[num]
        nome = nomes[0] if len(nomes) == 1 else st.selectbox("Quem é você?", nomes)
    if st.button("Entrar", type="primary"):
        if num == c.ADMIN_CODE:
            st.session_state.admin = True
            st.rerun()
        elif num in c.USUARIOS and nome:
            st.session_state.user = {"num": num, "nome": nome}
            st.rerun()
        else:
            st.error("Login inválido.")


# ======================= CLIENTE =======================
ROTULO = {
    "fila": "⏳ Na fila",
    "processando": "⚙️ Processando",
    "entregue": "✅ Pronto",
    "expirado": "🗑️ Arquivo expirado",
}


def tela_cliente():
    user = st.session_state.user
    top = st.columns([6, 1])
    top[0].title(f"Olá, {user['nome'].title()} (CT {user['num']})")
    if top[1].button("Sair"):
        sair()

    st.subheader("Nova solicitação")
    c1, c2, c3 = st.columns([3, 3, 1])
    empresa = c1.selectbox("Empresa", c.EMPRESAS)
    servico = c2.selectbox("Serviço solicitado", c.SERVICOS)
    c3.write("")
    c3.write("")
    if c3.button("Solicitar", type="primary"):
        pid = c.criar_pedido(user["num"], user["nome"], servico, empresa)
        st.success(f"Pedido #{pid} enviado! Posição na fila: {c.posicao_na_fila(pid)}")

    @st.fragment(run_every=5)
    def meus_pedidos():
        c.limpar_expirados()
        st.subheader("Meus pedidos")
        pedidos = c.pedidos_do_usuario(user["num"])
        if not pedidos:
            st.info("Você ainda não fez nenhuma solicitação.")
            return
        for p in pedidos:
            a, b, d, e, f = st.columns([1, 3, 3, 2, 3])
            a.write(f"**#{p['id']}**")
            b.write(p["servico"])
            d.write(p["empresa"])
            status = ROTULO[p["status"]]
            if p["status"] in ("fila", "processando"):
                status += f" (posição {c.posicao_na_fila(p['id'])})"
            e.write(status)
            if p["status"] == "entregue" and p["arquivo"] and Path(p["arquivo"]).exists():
                f.download_button(
                    f"⬇️ {p['nome_arquivo']}",
                    data=Path(p["arquivo"]).read_bytes(),
                    file_name=p["nome_arquivo"],
                    key=f"dl_{p['id']}",
                )
                f.caption(f"disponível até {c.expira_em(p['entregue_em'])}")
            else:
                f.caption(p["criado_em"])

    meus_pedidos()


# ======================= SERVIDOR =======================
def tela_servidor():
    top = st.columns([6, 1])
    top[0].title("🖥️ Servidor de macros")
    if top[1].button("Sair"):
        sair()

    @st.fragment(run_every=3)
    def painel():
        c.limpar_expirados()
        atual = c.pedido_atual()

        st.subheader("Processo atual")
        if atual is None:
            st.info("Nenhum pedido na fila. Aguardando solicitações...")
        else:
            with st.container(border=True):
                m = st.columns(4)
                m[0].metric("Código do processo", f"#{atual['id']}")
                m[1].metric("Enviar para", f"{atual['usuario_nome']} (CT {atual['usuario_num']})")
                m[2].metric("Serviço", atual["servico"])
                m[3].metric("Empresa", atual["empresa"])

                st.info(
                    f"Rode a macro de **{atual['servico']}** para **{atual['empresa']}** "
                    f"e envie o arquivo para **{atual['usuario_nome']}**."
                )
                up = st.file_uploader("Arquivo gerado pela macro", key=f"up_{atual['id']}")
                if up is not None and st.button("📤 Enviar e liberar próximo", type="primary", key="send"):
                    c.entregar(atual["id"], up.getvalue(), up.name)
                    st.rerun()

        st.subheader("Fila")
        fila = [p for p in c.fila_completa() if not atual or p["id"] != atual["id"]]
        if fila:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Código": p["id"],
                            "Solicitante": p["usuario_nome"],
                            "Serviço": p["servico"],
                            "Empresa": p["empresa"],
                            "Pedido em": p["criado_em"],
                        }
                        for p in fila
                    ]
                ),
                hide_index=True,
                use_container_width=True,
            )
        else:
            st.caption("Fila vazia.")

    painel()

    st.subheader("Log de hoje")
    arq = c.LOGS_DIR / f"{datetime.now():%Y-%m-%d}.txt"
    if arq.exists():
        st.code(arq.read_text(encoding="utf-8"), language=None)
        st.download_button("⬇️ Baixar log", arq.read_bytes(), file_name=arq.name)
    else:
        st.caption("Nenhuma entrega registrada hoje.")


# ======================= ROTEAMENTO =======================
if st.session_state.get("admin"):
    tela_servidor()
elif "user" in st.session_state:
    tela_cliente()
else:
    tela_login()
