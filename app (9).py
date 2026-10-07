import os
import io
import pandas as pd
import streamlit as st

from usuarios import USUARIOS_PERMITIDOS

st.set_page_config(page_title="Meus Sistemas", page_icon="🗂️", layout="wide")

# ESCONDER BARRA SUPERIOR, GITHUB, TRÊS PONTOS E RODAPÉ
st.markdown(
    """
    <style>
    header {visibility: hidden !important;}
    .stAppHeader {display: none !important;}
    [data-testid="stHeader"] {display: none !important;}
    [data-testid="stToolbar"] {display: none !important;}
    #MainMenu {visibility: hidden !important;}
    footer {visibility: hidden !important;}
    </style>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# TELA DE LOGIN
# ============================================================
def tela_login():
    st.markdown("<h1 style='text-align: center;'>🔒 Acesso ao Sistema</h1>", unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1, 1, 1])
    with col2:
        pin = st.text_input("Digite seu código de acesso (3 dígitos):", type="password", max_chars=3)
        if st.button("Entrar", use_container_width=True, type="primary"):
            if pin in USUARIOS_PERMITIDOS:
                st.session_state["usuario_logado"] = pin
                st.session_state["usuario_nome"] = USUARIOS_PERMITIDOS[pin]
                st.session_state["pagina"] = "menu"
                st.rerun()
            else:
                st.error("Código de acesso inválido!")


if "usuario_logado" not in st.session_state:
    tela_login()
    st.stop()


# ============================================================
# SELETOR E GERENCIADOR DE PLANOS DE CONTAS DA PASTA PLANOS_EMPRESAS
# ============================================================
def selecionar_plano_de_contas():
    from sieg_xml import (
        carregar_banco_dados_github,
        salvar_banco_dados_github,
        carregar_empresas_github,
        salvar_empresas_github,
        obter_caminho_relativo_bd,
        eh_proprietario_do_banco,
        limpar_cnpj,
    )

    user_id = st.session_state["usuario_logado"]

    if "empresas_planos" not in st.session_state or not st.session_state["empresas_planos"]:
        st.session_state["empresas_planos"] = carregar_empresas_github()

    st.subheader("🏢 Seleção do Plano de Contas da Empresa")

    empresas = st.session_state["empresas_planos"]
    col_sel, col_novo = st.columns([2, 1])

    with col_sel:
        opcoes_emp = []
        for cod, d in empresas.items():
            cnpj_str = f" | CNPJ: {limpar_cnpj(d.get('cnpj', 'N/I'))}" if d.get('cnpj') else ""
            criador_str = USUARIOS_PERMITIDOS.get(d.get('criador'), 'Desconhecido')
            opcoes_emp.append(f"{cod} - {d['nome']}{cnpj_str} (Criador: {criador_str})")

        opcoes_emp.insert(0, "Selecione uma Empresa...")
        emp_sel = st.selectbox("Selecione a Empresa:", opcoes_emp)

    with col_novo:
        st.write("")
        with st.popover("➕ Cadastrar Nova Empresa"):
            st.markdown("### 🏢 Criar Plano de Empresa")
            cod_emp = st.text_input("Código da Empresa:")
            cnpj_emp = st.text_input("CNPJ da Empresa:")
            nome_emp = st.text_input("Nome da Empresa:")

            if st.button("Criar Plano de Contas", type="primary"):
                if cod_emp and cnpj_emp and nome_emp:
                    st.session_state["empresas_planos"][cod_emp] = {
                        "nome": nome_emp,
                        "cnpj": limpar_cnpj(cnpj_emp),
                        "criador": user_id
                    }
                    salvar_empresas_github(st.session_state["empresas_planos"])

                    caminho_arq = obter_caminho_relativo_bd(empresa_id=cod_emp)
                    salvar_banco_dados_github({}, caminho_arq)
                    st.success(f"Plano de Contas criado em `{caminho_arq}`!")
                    st.rerun()
                else:
                    st.error("Preencha o Código, o CNPJ e o Nome da Empresa!")

    caminho_arquivo_ativo = None
    cnpj_empresa_ativa = ""
    cod_emp_ativo = None

    if emp_sel != "Selecione uma Empresa...":
        cod_emp_ativo = emp_sel.split(" - ")[0]
        caminho_arquivo_ativo = obter_caminho_relativo_bd(empresa_id=cod_emp_ativo)
        dados_emp_sel = empresas.get(cod_emp_ativo, {})
        cnpj_empresa_ativa = limpar_cnpj(dados_emp_sel.get("cnpj", ""))

    st.session_state["empresa_ativa_cod"] = cod_emp_ativo
    st.session_state["empresa_ativa_cnpj"] = cnpj_empresa_ativa

    if caminho_arquivo_ativo:
        eh_dono = eh_proprietario_do_banco(caminho_arquivo_ativo, user_id, st.session_state["empresas_planos"])
        mapa_contas = carregar_banco_dados_github(caminho_arquivo_ativo)

        if eh_dono:
            st.success(f"🔑 Plano Ativo: `{caminho_arquivo_ativo}` (Você é o **Dono** - Permissão total para editar e apagar).")
        else:
            st.info(f"👁️ Plano Ativo: `{caminho_arquivo_ativo}` (**Apenas Leitura** - Pertence a outro usuário).")

        return mapa_contas, caminho_arquivo_ativo, eh_dono

    return None, None, False


# ============================================================
# FUNÇÕES AUXILIARES DA TABELA EDITÁVEL DO PLANO DE CONTAS
# ============================================================
COLUNAS_PLANO = [
    "Código Serviço",
    "Descrição",
    "Alterdata (Despesa)",
    "Domínio (Despesa)",
    "Alterdata (Receita)",
    "Domínio (Receita)",
]
CHAVES_PLANO = ["conta", "conta_dominio", "conta_rec", "conta_dominio_rec"]


def _txt(v):
    if v is None:
        return ""
    try:
        if pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    return str(v).strip()


def mapa_para_df(mapa):
    linhas = []
    for cod, d in mapa.items():
        linhas.append([cod, d.get("descricao", "")] + [d.get(k, "") for k in CHAVES_PLANO])
    return pd.DataFrame(linhas, columns=COLUNAS_PLANO).astype(str)


def df_para_mapa(df):
    """Converte a tabela editada de volta para o dicionário. Retorna (mapa, erros)."""
    from sieg_xml import limpar_conta

    novo, erros, vistos = {}, [], set()
    for i, r in df.iterrows():
        cod = _txt(r["Código Serviço"])
        desc = _txt(r["Descrição"])
        contas = [limpar_conta(r[c]) for c in COLUNAS_PLANO[2:]]

        if not cod and not desc and not any(contas):
            continue  # linha totalmente vazia: ignora
        if not cod:
            erros.append(f"Linha {i + 1}: informe o Código Serviço.")
            continue
        if cod in vistos:
            erros.append(f"Código `{cod}` está repetido na tabela.")
            continue
        vistos.add(cod)
        novo[cod] = {"descricao": desc, **dict(zip(CHAVES_PLANO, contas))}
    return novo, erros


# ============================================================
# TELA DEDICADA: ALTERAR PLANO DE CONTAS
# ============================================================
def pagina_alterar_plano_de_contas():
    from sieg_xml import (
        salvar_banco_dados_github,
        deletar_empresa_completa_github,
    )

    st.title("📝 Alterar Plano de Contas")

    mapa_contas, caminho_arquivo_ativo, eh_dono = selecionar_plano_de_contas()
    cod_emp_ativo = st.session_state.get("empresa_ativa_cod")
    st.markdown("---")

    if st.session_state.pop("plano_salvo_ok", False):
        st.success("Plano de contas salvo com sucesso!")

    if not (caminho_arquivo_ativo and cod_emp_ativo):
        return

    # ---------------- SOMENTE LEITURA ----------------
    if not eh_dono:
        st.error("⚠️ Você não tem permissão para alterar ou apagar esta empresa. Apenas o criador (dono) do plano tem essa autorização.")
        if mapa_contas:
            st.subheader("📊 Visualização das Contas (Modo Leitura)")
            st.dataframe(mapa_para_df(mapa_contas), use_container_width=True, hide_index=True)
        return

    # ---------------- DONO: TABELA EDITÁVEL ----------------
    col_tit, col_del_emp = st.columns([3, 1])
    with col_del_emp:
        with st.popover("🗑️ Apagar Empresa / Plano", use_container_width=True):
            st.warning("⚠️️ Esta ação vai apagar permanentemente esta empresa e o plano de contas dela!")
            st.write(f"Empresa Código: **{cod_emp_ativo}**")
            if st.button("Confirmar Exclusão Definitiva", type="primary", key="btn_confirm_del_emp"):
                if deletar_empresa_completa_github(cod_emp_ativo, st.session_state["empresas_planos"]):
                    st.success("Empresa e Plano de Contas apagados com sucesso!")
                    st.session_state["empresas_planos"] = {}
                    st.rerun()

    with col_tit:
        st.subheader("⚙️ Plano de Contas")
        st.caption(
            "Edite direto nas células. Para **cadastrar**, use a última linha em branco (＋). "
            "Para **apagar**, marque a caixinha à esquerda da linha e clique na lixeira (ou tecla Delete). "
            "Nada é gravado até clicar em **Salvar alterações**."
        )

    df_original = mapa_para_df(mapa_contas or {})

    # A versão na key reinicia o editor depois de salvar (evita reaplicar edições antigas)
    versao = st.session_state.get("ver_editor_plano", 0)

    df_editado = st.data_editor(
        df_original,
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        key=f"editor_plano_{cod_emp_ativo}_{versao}",
        column_config={
            "Código Serviço": st.column_config.TextColumn("Código Serviço", required=True),
            "Descrição": st.column_config.TextColumn("Descrição", width="large"),
            "Alterdata (Despesa)": st.column_config.TextColumn("Alterdata (Despesa)"),
            "Domínio (Despesa)": st.column_config.TextColumn("Domínio (Despesa)"),
            "Alterdata (Receita)": st.column_config.TextColumn("Alterdata (Receita)"),
            "Domínio (Receita)": st.column_config.TextColumn("Domínio (Receita)"),
        },
    )

    if st.button("💾 Salvar alterações", type="primary", key="btn_salvar_plano"):
        novo_mapa, erros = df_para_mapa(df_editado)
        if erros:
            for e in erros:
                st.error(e)
        else:
            salvar_banco_dados_github(novo_mapa, caminho_arquivo_ativo)
            st.session_state["ver_editor_plano"] = versao + 1
            st.session_state["plano_salvo_ok"] = True
            st.rerun()


# ============================================================
# MENU PRINCIPAL
# ============================================================
def menu_principal():
    st.sidebar.markdown(f"👤 Logado como: **{st.session_state['usuario_nome']}** (`{st.session_state['usuario_logado']}`)")
    if st.sidebar.button("🚪 Sair / Logoff"):
        del st.session_state["usuario_logado"]
        st.rerun()

    st.title("🗂️ Meus Sistemas")
    st.write("Escolha a opção que deseja acessar:")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        if st.button("📄 SIEG XML PARA Importação", use_container_width=True):
            st.session_state["pagina"] = "sieg"
            st.rerun()
    with col2:
        if st.button("📊 Excel NFS-e (Portal Nacional)", use_container_width=True):
            st.session_state["pagina"] = "excel_nfse"
            st.rerun()
    with col3:
        if st.button("📝 Alterar Plano de Contas", use_container_width=True):
            st.session_state["pagina"] = "alterar_plano"
            st.rerun()
    with col4:
        if st.button("🔌 SIEG API (busca automática)", use_container_width=True):
            st.session_state["pagina"] = "sieg_api"
            st.rerun()


# ============================================================
# NAVEGAÇÃO ENTRE OS SISTEMAS
# ============================================================
pagina_atual = st.session_state.get("pagina", "menu")

if pagina_atual != "menu":
    st.sidebar.markdown(f"👤 **{st.session_state['usuario_nome']}**")
    if st.sidebar.button("⬅️ Voltar ao Menu Principal"):
        st.session_state["pagina"] = "menu"
        st.rerun()

    # Botão também na tela principal (a barra superior está escondida, então a
    # barra lateral pode ficar recolhida e sem como abrir)
    if st.button("🏠 Voltar ao Menu Principal", key="btn_voltar_topo"):
        st.session_state["pagina"] = "menu"
        st.rerun()

# ------------------------------------------------------------
# OPÇÃO 1: SIEG XML
# ------------------------------------------------------------
if pagina_atual == "sieg":
    from sieg_xml import pagina_sieg_xml
    mapa_contas, caminho_arquivo_bd, eh_dono = selecionar_plano_de_contas()
    st.markdown("---")
    if mapa_contas is not None:
        pagina_sieg_xml(mapa_contas, caminho_arquivo_bd, eh_dono)

# ------------------------------------------------------------
# OPÇÃO 2: EXCEL NFSE
# ------------------------------------------------------------
elif pagina_atual == "excel_nfse":
    from sieg_xml import (
        formatar_valor,
        gerar_aba_alterdata,
        gerar_txt_dominio,
        limpar_cnpj,
        editor_codigos_lote,
        tabela_codigos_lote,
        CONTAS,
        NOMES_MODO,
    )
    from leitor_excel import extrair_nfse_excel

    mapa_contas, caminho_arquivo_bd, eh_dono = selecionar_plano_de_contas()

    if mapa_contas is not None:
        st.markdown("---")
        arquivo_excel = st.file_uploader("Selecione a planilha Excel (.xlsx)", type=["xlsx"])

        if arquivo_excel is not None:
            col_btn1, col_btn2, _ = st.columns([1, 1, 2])
            with col_btn1:
                processar_alterdata = st.button("🚀 Processar para Alterdata")
            with col_btn2:
                processar_dominio = st.button("🚀 Processar para Domínio")

            if processar_alterdata or processar_dominio:
                modo = "dominio" if processar_dominio else "alterdata"
                st.session_state["modo_excel"] = modo

                with st.spinner("Processando dados e aplicando regras V2..."):
                    registros, ignoradas = extrair_nfse_excel(arquivo_excel, formatar_valor)

                    df_nfse = pd.DataFrame(registros)
                    mapa_descricoes = {}

                    # DETERMINA RECEITA x DESPESA
                    cnpj_emp_sel = limpar_cnpj(st.session_state.get("empresa_ativa_cnpj", ""))
                    cnpjs_prest_lote = set(df_nfse["CNPJ Prestador"].dropna().apply(limpar_cnpj).unique()) if not df_nfse.empty and "CNPJ Prestador" in df_nfse.columns else set()
                    eh_receita = bool(cnpj_emp_sel and cnpj_emp_sel in cnpjs_prest_lote)
                    st.session_state["eh_receita_excel"] = eh_receita

                    if not df_nfse.empty:
                        for _, row in df_nfse.iterrows():
                            cod = str(row.get("Código Tributação", "") or "").strip()
                            tipo = row.get("Tipo de Serviço")
                            tipo = "" if pd.isna(tipo) else str(tipo).strip()
                            if cod and cod not in mapa_descricoes:
                                mapa_descricoes[cod] = tipo

                    st.session_state["df_excel_processado"] = df_nfse
                    st.session_state["df_excel_ignoradas"] = pd.DataFrame(ignoradas)
                    st.session_state["mapa_descricoes_excel"] = mapa_descricoes

        if "df_excel_processado" in st.session_state and not st.session_state["df_excel_processado"].empty:
            df_nfse = st.session_state["df_excel_processado"]
            df_ignoradas = st.session_state.get("df_excel_ignoradas", pd.DataFrame())
            mapa_descricoes = st.session_state.get("mapa_descricoes_excel", {})
            modo = st.session_state.get("modo_excel", "alterdata")
            nome_modo = NOMES_MODO[modo]
            eh_receita = st.session_state.get("eh_receita_excel", False)

            if eh_receita:
                st.success("💰 **TIPO DE OPERAÇÃO: RECEITA (SERVIÇOS PRESTADOS)**")
            else:
                st.info("🛒 **TIPO DE OPERAÇÃO: DESPESA (SERVIÇOS TOMADOS)**")

            # Tabela editável de códigos do lote (um único botão para salvar)
            ausentes = editor_codigos_lote(
                df_nfse, mapa_contas, caminho_arquivo_bd, modo, eh_receita, eh_dono,
                mapa_descricoes=mapa_descricoes, chave_estado="excel",
            )
            df_codigos_lote = tabela_codigos_lote(df_nfse, mapa_contas, modo, eh_receita, mapa_descricoes)

            if ausentes:
                if eh_dono:
                    st.warning(
                        f"⚠️ Existem códigos de serviço sem conta de {'RECEITA' if eh_receita else 'DESPESA'} {nome_modo} cadastrada. "
                        "Confira a tabela acima e clique em **💾 Salvar contas** para liberar a prévia e os downloads."
                    )
                else:
                    st.error(f"⚠️️ Existem códigos sem conta cadastrada (`{', '.join(ausentes)}`). Como você está no modo apenas leitura, peça ao dono do plano para registrá-los.")

            else:
                st.subheader(f"🧾 Contas dos Impostos e Contrapartida - {nome_modo}")
                padrao = CONTAS[modo]
                rotulo_principal = "Clientes (Débito)" if eh_receita else "Fornecedores (Crédito)"

                campos_contas = [
                    ("credito_principal", rotulo_principal),
                    ("pcc", "PIS / COFINS / CSLL"),
                    ("irrf", "IRRF"),
                    ("inss", "INSS"),
                    ("iss", "ISS"),
                    ("historico", "Histórico Padrão"),
                ]

                contas_editadas = dict(padrao)
                cols = st.columns(len(campos_contas))
                for col, (chave, rotulo) in zip(cols, campos_contas):
                    with col:
                        v_dig = st.text_input(rotulo, value=padrao.get(chave, ""), key=f"v2_cnt_{modo}_{chave}")
                        contas_editadas[chave] = v_dig.strip() or padrao.get(chave, "")

                df_lancamentos = gerar_aba_alterdata(df_nfse, mapa_contas, modo, contas_editadas, eh_receita=eh_receita)
                nome_aba = "Domínio" if modo == "dominio" else "Alterdata"

                st.subheader(f"📊 Prévia dos Lançamentos ({nome_aba})")
                st.dataframe(df_lancamentos, use_container_width=True)

                if not df_ignoradas.empty:
                    st.subheader("⚠️ Notas Canceladas / Ignoradas")
                    st.dataframe(df_ignoradas, use_container_width=True)

                buffer_excel = io.BytesIO()
                with pd.ExcelWriter(buffer_excel, engine="openpyxl", date_format="dd/mm/yyyy") as writer:
                    df_lancamentos.to_excel(writer, index=False, sheet_name=nome_aba)
                    df_nfse.drop(columns=["Nome do Tomador"], errors="ignore").to_excel(writer, index=False, sheet_name="NFS-e Extraídas")
                    if not df_codigos_lote.empty:
                        df_codigos_lote.to_excel(writer, index=False, sheet_name="Códigos do Lote")

                st.markdown("---")
                st.subheader("📥 Downloads Disponíveis")

                if modo == "dominio":
                    lote_init = st.number_input("Nº do primeiro lote (Domínio)", min_value=1, value=1, step=1)
                    txt_dominio = gerar_txt_dominio(df_lancamentos, lote_init)

                    c1, c2 = st.columns(2)
                    with c1:
                        st.download_button("📄 Baixar Layout Domínio (.txt)", data=txt_dominio, file_name="importacao_dominio_excel.txt", mime="text/plain")
                    with c2:
                        st.download_button("📊 Baixar Planilha Domínio (.xlsx)", data=buffer_excel.getvalue(), file_name="importacao_dominio_excel.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                else:
                    st.download_button("📊 Baixar Planilha Alterdata (.xlsx)", data=buffer_excel.getvalue(), file_name="importacao_alterdata_excel.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ------------------------------------------------------------
# OPÇÃO 3: ALTERAR PLANO DE CONTAS
# ------------------------------------------------------------
elif pagina_atual == "alterar_plano":
    pagina_alterar_plano_de_contas()

# ------------------------------------------------------------
# OPÇÃO 4: SIEG API (BUSCA AUTOMÁTICA)
# ------------------------------------------------------------
elif pagina_atual == "sieg_api":
    from sieg_api import pagina_sieg_api
    mapa_contas, caminho_arquivo_bd, eh_dono = selecionar_plano_de_contas()
    st.markdown("---")
    if mapa_contas is not None:
        pagina_sieg_api(mapa_contas, caminho_arquivo_bd, eh_dono)

else:
    menu_principal()
