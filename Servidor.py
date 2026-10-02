import threading
import time

import streamlit as st

from comum import *
from macros import executar_macro

st.set_page_config(page_title="Servidor de Relatórios", page_icon="🖥️", layout="wide")
init_db()


def worker(ctl):
    while True:
        try:
            limpar_expirados()
            if ctl["pausado"]:
                time.sleep(3)
                continue
            p = pegar_proximo()
            if not p:
                time.sleep(3)
                continue
            try:
                nome_arq, dados = executar_macro(p["servico"], p["empresa"], p["nome"])
                concluir(p["id"], nome_arq, dados)  # entregue -> libera o próximo
                escrever_log(p["nome"], p["servico"], p["empresa"], f"{agora():%d/%m/%Y %H:%M:%S}")
            except Exception as e:
                falhar(p["id"], str(e))
                escrever_log(p["nome"], p["servico"], p["empresa"], f"ERRO: {e}")
        except Exception:
            time.sleep(10)


@st.cache_resource
def iniciar_worker():
    recuperar_travados()
    ctl = {"pausado": False}
    threading.Thread(target=worker, args=(ctl,), daemon=True).start()
    return ctl


ctl = iniciar_worker()
st.title("🖥️ Servidor de Relatórios")
if ctl["pausado"]:
    if st.button("▶️ Retomar fila"):
        ctl["pausado"] = False
        st.rerun()
elif st.button("⏸️ Pausar fila"):
    ctl["pausado"] = True
    st.rerun()


@st.fragment(run_every=4)
def painel():
    st.subheader("Processo atual")
    if ctl["pausado"]:
        st.warning("Fila pausada.")
    a = processo_atual()
    if a:
        st.info(f"**{codigo(a['id'])}** — {a['servico']} | {a['empresa']}\n\n"
                f"Enviar para: **{a['nome']}** (ramal {a['numero']}) — início {fmt(a['inicio_em'])}")
    else:
        st.success("Ocioso — aguardando pedidos.")

    st.subheader("Fila de espera")
    fila = fila_espera()
    if fila:
        st.dataframe([{"Pos.": i + 1, "Código": codigo(p["id"]), "Para": p["nome"],
                       "Serviço": p["servico"], "Empresa": p["empresa"], "Pedido em": fmt(p["criado_em"])}
                      for i, p in enumerate(fila)], hide_index=True, use_container_width=True)
    else:
        st.caption("Fila vazia.")

    st.subheader("Últimos concluídos")
    ult = ultimos_pedidos()
    if ult:
        st.dataframe([{"Código": codigo(p["id"]), "Para": p["nome"], "Serviço": p["servico"],
                       "Empresa": p["empresa"], "Status": p["status"], "Entrega": fmt(p["entregue_em"])}
                      for p in ult], hide_index=True, use_container_width=True)

    st.subheader(f"Log de hoje ({agora():%d/%m/%Y})")
    st.code(ler_log_do_dia() or "Sem registros hoje.", language=None)


painel()
