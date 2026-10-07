"""
Integração com a API do SIEG (Cofre): busca NFS-e em lote e entrega ao mesmo
pipeline do "SIEG XML" (extrair_xml -> gerar_aba_alterdata -> downloads).

Autenticação em dois níveis (conforme documentação):
  1) JWT  : POST /api/v1/create-jwt  (headers X-Client-Id / X-Secret-Key) -> vale 24h
  2) OAuth: token definitivo do cliente (vale 30 dias, renovável via /oauth/refresh)
Toda chamada de dados envia:  Authorization: Bearer {jwt}  +  X-OAuth-Token: {token}

Segredos em st.secrets (use o que você tiver):
  SIEG_API_KEY (chave do painel "Integrações API SIEG")  -> mínimo necessário
  SIEG_CLIENT_ID + SIEG_SECRET_KEY                       -> opcional, gera o JWT
  SIEG_OAUTH_TOKEN                                       -> opcional
"""
import base64
import io
import re
import time
import zipfile
from datetime import date, timedelta

import pandas as pd
import requests
import streamlit as st

BASE_URL = "https://api.sieg.com/api/v1"
TIPO_NFSE = 3                 # 1=NFe 2=CTe 3=NFSe 4=NFCe 5=CFe
TAKE = 50                     # máximo por requisição
INTERVALO_BAIXAR = 31         # segundos entre chamadas (limite: 2 req/min)
MAX_PAGINAS = 200

_jwt_cache = {"token": "", "expira": 0.0}
_ritmo = {"ultima": 0.0}
_debug = {"resposta": ""}


class SiegErro(Exception):
    pass


# ============================================================
# CLIENTE DA API
# ============================================================
def _segredo(nome):
    try:
        return str(st.secrets.get(nome, "") or "").strip()
    except Exception:
        return ""


def _requisitar(metodo, caminho, headers=None, json=None, tentativas=4):
    """Request com backoff exponencial para HTTP 429."""
    espera = 20
    r = None
    for i in range(tentativas):
        try:
            r = requests.request(metodo, f"{BASE_URL}{caminho}", headers=headers, json=json, timeout=90)
        except requests.RequestException as e:
            raise SiegErro(f"Falha de conexão com a SIEG: {e}")
        if r.status_code == 429 and i < tentativas - 1:
            time.sleep(espera)
            espera = min(espera * 2, 120)
            continue
        break
    return r


def _json(r):
    try:
        return r.json()
    except ValueError:
        return {}


def _msg_erro(r):
    corpo = _json(r)
    msg = ""
    if isinstance(corpo, dict):
        msg = corpo.get("Message") or corpo.get("message") or corpo.get("ErrorMessage") or ""
    return f"HTTP {r.status_code} - {msg or r.text[:300]}"


def gerar_jwt(forcar=False):
    """JWT reutilizado até perto de expirar (validade de 24h)."""
    if not forcar and _jwt_cache["token"] and time.time() < _jwt_cache["expira"]:
        return _jwt_cache["token"]

    client_id, secret = _segredo("SIEG_CLIENT_ID"), _segredo("SIEG_SECRET_KEY")
    if not client_id or not secret:
        raise SiegErro("Configure SIEG_CLIENT_ID e SIEG_SECRET_KEY em st.secrets.")

    r = _requisitar("POST", "/create-jwt", headers={"X-Client-Id": client_id, "X-Secret-Key": secret})
    if r.status_code != 200:
        raise SiegErro(f"Não foi possível gerar o JWT: {_msg_erro(r)}")

    corpo = _json(r)
    token = (corpo.get("Token") or corpo.get("token") or "") if isinstance(corpo, dict) else ""
    token = token.replace("Bearer ", "").strip()
    if not token:
        raise SiegErro("A SIEG respondeu sem token JWT.")

    _jwt_cache.update(token=token, expira=time.time() + 23 * 3600)
    return token


def _headers_dados(oauth_token):
    """Monta os headers com o que estiver configurado (chave de API, JWT e/ou token OAuth)."""
    api_key = _segredo("SIEG_API_KEY")
    if not api_key and not oauth_token:
        raise SiegErro("Configure SIEG_API_KEY (ou SIEG_OAUTH_TOKEN) em st.secrets.")
    h = {"Accept": "application/json", "Content-Type": "application/json"}
    if _segredo("SIEG_CLIENT_ID") and _segredo("SIEG_SECRET_KEY"):
        h["Authorization"] = f"Bearer {gerar_jwt()}"
    if oauth_token:
        h["X-OAuth-Token"] = oauth_token
    if api_key:
        h["X-API-Key"] = api_key
    return h


def _headers_credenciais():
    client_id, secret = _segredo("SIEG_CLIENT_ID"), _segredo("SIEG_SECRET_KEY")
    if not client_id or not secret:
        raise SiegErro("Configure SIEG_CLIENT_ID e SIEG_SECRET_KEY em st.secrets.")
    return {"X-Client-Id": client_id, "X-Secret-Key": secret, "Content-Type": "application/json"}


def gerar_token_definitivo(token_temporario, state, redirect_uri):
    """Converte o token temporário (10 min) em definitivo (30 dias)."""
    r = _requisitar(
        "POST", "/oauth/generate-token", headers=_headers_credenciais(),
        json={"AccessToken": token_temporario, "State": state, "RedirectUri": redirect_uri},
    )
    if r.status_code != 200:
        raise SiegErro(f"Falha ao gerar token definitivo: {_msg_erro(r)}")
    return _json(r)


def renovar_token(token):
    """Renova o token definitivo (fazer antes de vencer os 30 dias)."""
    r = _requisitar("POST", "/oauth/refresh", headers=_headers_credenciais(), json={"Token": token})
    if r.status_code != 200:
        raise SiegErro(f"Falha ao renovar token: {_msg_erro(r)}")
    return _json(r)


# ---------- decodificação da resposta (formato tolerante) ----------
def _bytes_para_xmls(bruto):
    if bruto[:2] == b"PK":  # ZIP
        saida = []
        try:
            with zipfile.ZipFile(io.BytesIO(bruto)) as z:
                for nome in z.namelist():
                    if nome.lower().endswith(".xml"):
                        saida.append(z.read(nome))
        except zipfile.BadZipFile:
            return []
        return saida
    if bruto.lstrip(b"\xef\xbb\xbf \r\n\t").startswith(b"<"):
        return [bruto]
    return []


def _decodificar_texto(texto):
    t = texto.strip()
    if t.startswith("<"):
        return [t.encode("utf-8")]
    t = re.sub(r"\s+", "", t)
    if len(t) < 100:
        return []
    try:
        return _bytes_para_xmls(base64.b64decode(t, validate=True))
    except Exception:
        return []


def _extrair_xmls(obj):
    """Percorre o JSON e devolve todos os XMLs encontrados (texto, base64 ou ZIP base64)."""
    achados = []
    if isinstance(obj, dict):
        for v in obj.values():
            achados += _extrair_xmls(v)
    elif isinstance(obj, list):
        for v in obj:
            achados += _extrair_xmls(v)
    elif isinstance(obj, str):
        achados += _decodificar_texto(obj)
    return achados


def _aguardar_janela():
    falta = INTERVALO_BAIXAR - (time.time() - _ritmo["ultima"])
    if falta > 0:
        time.sleep(falta)
    _ritmo["ultima"] = time.time()


def baixar_nfse(oauth_token, cnpj, papel, inicio, fim, baixar_eventos=True, progresso=None):
    """
    Baixa NFS-e (e eventos) do período, paginando de 50 em 50.
    papel: "prestador" (receita -> CnpjEmit) ou "tomador" (despesa -> CnpjDest).
    Retorna lista de XMLs em bytes.
    """
    campo = "CnpjEmit" if papel == "prestador" else "CnpjDest"
    cnpj = re.sub(r"\D", "", str(cnpj))
    if not cnpj:
        raise SiegErro("CNPJ da empresa não informado.")

    vistos, xmls = set(), []
    skip, pagina, jwt_renovado = 0, 0, False

    while pagina < MAX_PAGINAS:
        _aguardar_janela()
        corpo = {
            "TipoXml": TIPO_NFSE,
            "Take": TAKE,
            "Skip": skip,
            "DataEmissaoInicio": inicio.isoformat(),
            "DataEmissaoFim": fim.isoformat(),
            campo: cnpj,
            "BaixarEventos": baixar_eventos,
        }
        r = _requisitar("POST", "/baixar-xmls", headers=_headers_dados(oauth_token), json=corpo)
        _debug["resposta"] = f"HTTP {r.status_code}\n{r.text[:1500]}"

        if r.status_code == 401 and not jwt_renovado and _segredo("SIEG_CLIENT_ID"):
            gerar_jwt(forcar=True)
            jwt_renovado = True
            continue
        if r.status_code != 200:
            raise SiegErro(f"Erro ao baixar XMLs: {_msg_erro(r)}")

        payload = _json(r)
        if isinstance(payload, dict) and payload.get("success") is False:
            raise SiegErro(f"A SIEG recusou a consulta: {payload.get('message') or payload}")

        novos = []
        for x in _extrair_xmls(payload):
            h = hash(x)
            if h not in vistos:
                vistos.add(h)
                novos.append(x)
        if not novos:   # página vazia (ou repetida): acabou
            break

        xmls.extend(novos)
        skip += TAKE
        pagina += 1
        if progresso:
            progresso(len(xmls), pagina)

    return xmls


# ============================================================
# PÁGINA STREAMLIT
# ============================================================
def pagina_sieg_api(mapa_contas=None, caminho_arquivo_bd=None, eh_dono=True):
    from sieg_xml import (
        CONTAS, NOMES_MODO, extrair_xml, limpar_cnpj, editor_codigos_lote,
        tabela_codigos_lote, gerar_aba_alterdata, gerar_aba_substituidas, gerar_txt_dominio,
    )

    st.title("🔌 SIEG API - Busca Automática de NFS-e")

    cnpj_emp = limpar_cnpj(st.session_state.get("empresa_ativa_cnpj", ""))
    if not cnpj_emp:
        st.info("Selecione uma empresa acima para buscar as notas pelo CNPJ dela.")
        return

    # ---------------- AUTENTICAÇÃO ----------------
    oauth_token = _segredo("SIEG_OAUTH_TOKEN")
    cred_ok = bool(_segredo("SIEG_API_KEY") or oauth_token)

    with st.expander("🔑 Autenticação SIEG", expanded=not cred_ok):
        st.caption(
            "Credenciais lidas de `st.secrets`: SIEG_API_KEY (chave gerada no painel da SIEG) e, "
            "se você tiver, SIEG_CLIENT_ID / SIEG_SECRET_KEY / SIEG_OAUTH_TOKEN. "
            "Se preferir, cole abaixo um token OAuth só para esta sessão."
        )
        manual = st.text_input("Token OAuth definitivo (opcional)", type="password", key="api_oauth_manual")
        if manual.strip():
            oauth_token = manual.strip()

        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Converter token temporário (10 min) em definitivo**")
            t_temp = st.text_input("Token temporário", type="password", key="api_tok_temp")
            t_state = st.text_input("State", key="api_tok_state")
            t_redir = st.text_input("URL de callback (RedirectUri)", key="api_tok_redir")
            if st.button("Gerar token definitivo", key="api_btn_gerar_tok"):
                try:
                    st.json(gerar_token_definitivo(t_temp.strip(), t_state.strip(), t_redir.strip()))
                    st.warning("Copie o token definitivo para `SIEG_OAUTH_TOKEN` nos secrets.")
                except SiegErro as e:
                    st.error(str(e))
        with c2:
            st.markdown("**Renovar token definitivo (a cada 30 dias)**")
            if st.button("Renovar token atual", key="api_btn_renovar"):
                try:
                    st.json(renovar_token(oauth_token))
                    st.warning("Atualize `SIEG_OAUTH_TOKEN` se a resposta trouxer um novo token.")
                except SiegErro as e:
                    st.error(str(e))

    # ---------------- BUSCA ----------------
    st.markdown("---")
    st.subheader("🔎 Buscar notas no Cofre SIEG")

    hoje = date.today()
    ultimo_mes_fim = hoje.replace(day=1) - timedelta(days=1)
    ultimo_mes_ini = ultimo_mes_fim.replace(day=1)

    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        tipo = st.radio(
            "Tipo de operação",
            ["Despesa (serviços tomados)", "Receita (serviços prestados)"],
            horizontal=True, key="api_tipo",
        )
    with c2:
        ini = st.date_input("Emissão - início", ultimo_mes_ini, format="DD/MM/YYYY", key="api_ini")
    with c3:
        fim = st.date_input("Emissão - fim", ultimo_mes_fim, format="DD/MM/YYYY", key="api_fim")

    incluir_eventos = st.checkbox("Incluir eventos (cancelamentos / substituições)", value=True, key="api_eventos")
    st.caption(
        f"Empresa: CNPJ {cnpj_emp}. A SIEG limita a 2 requisições por minuto (50 XMLs cada), "
        "então lotes grandes levam alguns minutos. Prefira períodos de até 2 meses."
    )

    if st.button("🔎 Buscar na SIEG", type="primary", key="api_btn_buscar"):
        if ini > fim:
            st.error("A data inicial não pode ser maior que a final.")
        else:
            papel = "prestador" if tipo.startswith("Receita") else "tomador"
            barra = st.empty()
            try:
                def _prog(qtd, pag):
                    barra.info(f"Página {pag} concluída - {qtd} XML(s) baixado(s)...")

                barra.info("Autenticando e consultando a SIEG...")
                xmls = baixar_nfse(oauth_token, cnpj_emp, papel, ini, fim, incluir_eventos, _prog)
                st.session_state["api_xmls"] = xmls
                st.session_state.pop("df_api", None)
                barra.success(f"Busca concluída: {len(xmls)} XML(s).")
            except SiegErro as e:
                barra.error(str(e))

    if _debug["resposta"]:
        with st.expander("🛠️ Última resposta bruta da SIEG (diagnóstico)"):
            st.code(_debug["resposta"])

    xmls = st.session_state.get("api_xmls")
    if not xmls:
        return

    # ---------------- PROCESSAMENTO (reaproveita o pipeline do SIEG XML) ----------------
    st.markdown("---")
    st.write(f"📦 **{len(xmls)}** XML(s) em memória. Escolha o sistema de destino:")
    col_a, col_b, _ = st.columns([1, 1, 2])
    with col_a:
        p_alt = st.button("🚀 Processar para Alterdata", key="api_btn_alt")
    with col_b:
        p_dom = st.button("🚀 Processar para Domínio", key="api_btn_dom")

    if p_alt or p_dom:
        st.session_state["modo_api"] = "dominio" if p_dom else "alterdata"
        registros, eventos, erros = [], [], []
        for i, conteudo in enumerate(xmls, 1):
            try:
                dados = extrair_xml(conteudo)
                (eventos if dados.get("tipo_xml") == "EVENTO" else registros).append(dados)
            except Exception as err:
                erros.append(f"XML #{i}: {err}")

        df_nfse = pd.DataFrame(registros)
        cnpjs_prest = (
            set(df_nfse["CNPJ Prestador"].dropna().apply(limpar_cnpj).unique())
            if not df_nfse.empty and "CNPJ Prestador" in df_nfse.columns else set()
        )
        tipos = {}
        if not df_nfse.empty:
            for _, row in df_nfse.iterrows():
                cod = str(row.get("Código Tributação", "") or "").strip()
                desc = str(row.get("Tipo de Serviço", "") or "").strip()
                if cod and desc and cod not in tipos:
                    tipos[cod] = desc

        st.session_state.update(
            df_api=df_nfse, eventos_api=eventos, erros_api=erros, tipos_api=tipos,
            eh_receita_api=bool(cnpj_emp and cnpj_emp in cnpjs_prest),
        )

    df = st.session_state.get("df_api")
    if df is None:
        return
    if df.empty:
        st.warning("Nenhuma NFS-e encontrada nos XMLs baixados.")
        return

    modo = st.session_state.get("modo_api", "alterdata")
    nome_modo = NOMES_MODO[modo]
    eventos_list = st.session_state.get("eventos_api", [])
    tipos = st.session_state.get("tipos_api", {})
    eh_receita = st.session_state.get("eh_receita_api", False)

    if st.session_state.get("erros_api"):
        with st.expander(f"⚠️ {len(st.session_state['erros_api'])} XML(s) com erro de leitura"):
            for e in st.session_state["erros_api"]:
                st.write(e)

    if eh_receita:
        st.success("💰 **TIPO DE OPERAÇÃO IDENTIFICADA: RECEITA (SERVIÇOS PRESTADOS)**")
    else:
        st.info("🛒 **TIPO DE OPERAÇÃO IDENTIFICADA: DESPESA (SERVIÇOS TOMADOS)**")

    ausentes = editor_codigos_lote(
        df, mapa_contas, caminho_arquivo_bd, modo, eh_receita, eh_dono,
        mapa_descricoes=tipos, chave_estado="api",
    )
    if ausentes:
        if eh_dono:
            st.warning(
                f"⚠️ Existem códigos sem conta de {'RECEITA' if eh_receita else 'DESPESA'} {nome_modo} cadastrada. "
                "Clique em **💾 Salvar contas** na tabela acima para liberar a prévia e os downloads."
            )
        else:
            st.error(f"⚠️ Os códigos `{', '.join(ausentes)}` não estão cadastrados. Peça ao dono do plano para adicioná-los.")
        return

    df_codigos = tabela_codigos_lote(df, mapa_contas, modo, eh_receita, tipos)

    st.subheader(f"🧾 Contas dos Impostos e Contrapartida - {nome_modo}")
    padrao = CONTAS[modo]
    campos = [
        ("credito_principal", "Clientes (Débito)" if eh_receita else "Fornecedores (Crédito)"),
        ("pcc", "PIS / COFINS / CSLL"),
        ("irrf", "IRRF"),
        ("inss", "INSS"),
        ("iss", "ISS"),
        ("historico", "Histórico Padrão"),
    ]
    contas_editadas = dict(padrao)
    for col, (chave, rotulo) in zip(st.columns(len(campos)), campos):
        with col:
            v = st.text_input(rotulo, value=padrao[chave], key=f"conta_api_{modo}_{chave}")
            contas_editadas[chave] = v.strip() or padrao[chave]

    df_lanc = gerar_aba_alterdata(df, mapa_contas, modo, contas_editadas, eh_receita=eh_receita)
    df_subst = gerar_aba_substituidas(eventos_list, df)
    nome_aba = "Domínio" if modo == "dominio" else "Alterdata"

    st.subheader(f"📊 Prévia - Aba {nome_aba}")
    st.dataframe(df_lanc, use_container_width=True)

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl", date_format="dd/mm/yyyy") as writer:
        df_lanc.to_excel(writer, index=False, sheet_name=nome_aba)
        df.drop(columns=["Nome do Tomador"], errors="ignore").to_excel(writer, index=False, sheet_name="NFS-e Extraídas")
        if not df_codigos.empty:
            df_codigos.to_excel(writer, index=False, sheet_name="Códigos do Lote")
        if not df_subst.empty:
            df_subst.to_excel(writer, index=False, sheet_name="Notas Canceladas")

    st.markdown("---")
    st.subheader("📥 Downloads Disponíveis")
    if modo == "dominio":
        lote = st.number_input("Nº do primeiro lote (Domínio)", min_value=1, value=1, step=1, key="api_lote")
        txt = gerar_txt_dominio(df_lanc, lote)
        c1, c2 = st.columns(2)
        with c1:
            st.download_button("📄 Baixar Layout Domínio (.txt)", data=txt,
                               file_name="importacao_dominio_api.txt", mime="text/plain")
        with c2:
            st.download_button("📊 Baixar Planilha Domínio (.xlsx)", data=buffer.getvalue(),
                               file_name="importacao_dominio_api.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    else:
        st.download_button("📊 Baixar Planilha Alterdata (.xlsx)", data=buffer.getvalue(),
                           file_name="importacao_alterdata_api.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
