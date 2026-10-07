import io
import json
import os
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from itertools import combinations

from github import Github
import pandas as pd
import streamlit as st

PASTA_BANCOS = "planos_empresas"
ARQUIVO_EMPRESAS_JSON = os.path.join(PASTA_BANCOS, "empresas.json")

CONTAS = {
    "alterdata": {
        "debito_padrao": "2135",        # Despesa
        "credito_principal": "708",     # Fornecedores (Despesa) ou Clientes (Receita)
        "credito_receita": "3001",      # Receita de Serviços
        "pcc": "236",
        "irrf": "763",
        "inss": "834",
        "iss": "3332",
        "historico": "99",
    },
    "dominio": {
        "debito_padrao": "325",         # Despesa
        "credito_principal": "3907",    # Fornecedores (Despesa) ou Clientes (Receita)
        "credito_receita": "5001",      # Receita de Serviços
        "pcc": "647",
        "irrf": "178",
        "inss": "184",
        "iss": "183",
        "historico": "99",
    },
}

NOMES_MODO = {"alterdata": "Alterdata", "dominio": "Domínio"}


# ============================================================
# FUNÇÕES UTILITÁRIAS DE FORMATAÇÃO E LIMPEZA
# ============================================================
def limpar_cnpj(cnpj):
    """Remove pontuações (. - / e espaços) mantendo apenas letras e números."""
    if not cnpj:
        return ""
    return re.sub(r"[^a-zA-Z0-9]", "", str(cnpj)).strip().upper()


def extrair_codigo_do_banco(valor):
    if valor is None:
        return ""
    texto = str(valor).strip()
    m = re.match(r"^([a-zA-Z0-9]{2,10})\s*-\s*.+", texto)
    if m:
        return m.group(1)
    return texto


def extrair_descricao_do_banco(valor):
    if valor is None:
        return ""
    texto = str(valor).strip()
    m = re.match(r"^[a-zA-Z0-9]{2,10}\s*-\s*(.+)$", texto)
    if m:
        return m.group(1).strip()
    return ""


def montar_celula_banco(codigo, descricao):
    codigo = str(codigo).strip()
    descricao = str(descricao).strip() if descricao else ""
    if descricao:
        return f"{codigo} - {descricao}"
    return codigo


def limpar_conta(valor):
    if valor is None:
        return ""
    try:
        if pd.isna(valor):
            return ""
    except (TypeError, ValueError):
        pass
    texto = str(valor).strip()
    if re.match(r"^\d+\.0$", texto):
        texto = texto[:-2]
    return "" if texto.lower() == "nan" else texto


def converter_valor(valor):
    if valor is None:
        return None
    try:
        return float(valor)
    except:
        return None


def formatar_valor(valor):
    if valor is None:
        return ""
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def converter_data_obj(data_str):
    if not data_str:
        return None
    s = str(data_str).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        try:
            return pd.to_datetime(s[:10], format="%Y-%m-%d").date()
        except:
            return None
    try:
        return pd.to_datetime(s, dayfirst=True).date()
    except:
        return None


def extrair_pasta_mes_ano(data_str):
    if not data_str:
        return "SEM_DATA"
    try:
        dt = pd.to_datetime(data_str)
        return dt.strftime("%Y-%m")
    except:
        return "SEM_DATA"


def limpar_nome_arquivo(texto):
    return re.sub(r'[\\/*?:"<>|]', "", str(texto)).strip()


def conta_debito_do_banco(mapa, cod, modo):
    chave = "conta_dominio" if modo == "dominio" else "conta"
    valor = str(mapa.get(cod, {}).get(chave, "") or "").strip()
    return valor or CONTAS[modo]["debito_padrao"]


def conta_credito_receita_do_banco(mapa, cod, modo):
    chave = "conta_dominio_rec" if modo == "dominio" else "conta_rec"
    valor = str(mapa.get(cod, {}).get(chave, "") or "").strip()
    return valor or CONTAS[modo]["credito_receita"]


def codigo_precisa_cadastro(mapa, cod, modo, eh_receita=False):
    dados = mapa.get(cod)
    if dados is None:
        return True
    if eh_receita:
        chave = "conta_dominio_rec" if modo == "dominio" else "conta_rec"
        return not str(dados.get(chave, "") or "").strip()
    else:
        chave = "conta_dominio" if modo == "dominio" else "conta"
        return not str(dados.get(chave, "") or "").strip()


def validar_retencoes(v_serv, v_liq, impostos, tol=0.02):
    retidos = {nome: False for nome in impostos}
    diferenca = round(v_serv - v_liq, 2)

    if abs(diferenca) <= tol:
        return retidos, "OK", 0

    candidatos = [(n, v) for n, v in impostos.items() if v > 0]

    for tamanho in range(1, len(candidatos) + 1):
        achados = [
            comb
            for comb in combinations(candidatos, tamanho)
            if abs(v_liq + sum(v for _, v in comb) - v_serv) <= tol
        ]
        if achados:
            for nome, _ in achados[0]:
                retidos[nome] = True
            status = "OK" if len(achados) == 1 else "Ambíguo (revisar)"
            return retidos, status, len(achados)

    return retidos, "Divergente (revisar)", 0


# ============================================================
# GERENCIAMENTO DE PASTAS E REPOSITÓRIO GITHUB
# ============================================================
def garantir_pasta_local():
    if not os.path.exists(PASTA_BANCOS):
        os.makedirs(PASTA_BANCOS, exist_ok=True)


def obter_caminho_relativo_bd(empresa_id):
    return os.path.join(PASTA_BANCOS, f"plano_empresa_{empresa_id}.xlsx")


def carregar_empresas_github():
    garantir_pasta_local()
    if os.path.exists(ARQUIVO_EMPRESAS_JSON):
        try:
            with open(ARQUIVO_EMPRESAS_JSON, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def salvar_empresas_github(empresas_dict):
    garantir_pasta_local()
    with open(ARQUIVO_EMPRESAS_JSON, "w", encoding="utf-8") as f:
        json.dump(empresas_dict, f, ensure_ascii=False, indent=4)

    try:
        token = st.secrets.get("GITHUB_TOKEN")
        repo_name = st.secrets.get("REPO_NAME")
        if token and repo_name:
            g = Github(token)
            repo = g.get_repo(repo_name)
            content = json.dumps(empresas_dict, ensure_ascii=False, indent=4)
            caminho_repo = ARQUIVO_EMPRESAS_JSON.replace("\\", "/")
            try:
                contents = repo.get_contents(caminho_repo)
                repo.update_file(contents.path, "Atualizando lista de empresas", content, contents.sha)
            except Exception:
                repo.create_file(caminho_repo, "Criando lista de empresas", content)
    except Exception as e:
        st.error(f"Erro ao salvar empresas.json no GitHub: {e}")


def deletar_empresa_completa_github(cod_empresa, empresas_dict):
    caminho_local = obter_caminho_relativo_bd(cod_empresa)
    caminho_repo = caminho_local.replace("\\", "/")

    if cod_empresa in empresas_dict:
        del empresas_dict[cod_empresa]
        salvar_empresas_github(empresas_dict)

    if os.path.exists(caminho_local):
        os.remove(caminho_local)

    try:
        token = st.secrets.get("GITHUB_TOKEN")
        repo_name = st.secrets.get("REPO_NAME")
        if token and repo_name:
            g = Github(token)
            repo = g.get_repo(repo_name)
            try:
                contents = repo.get_contents(caminho_repo)
                repo.delete_file(contents.path, f"Deletando plano da empresa {cod_empresa}", contents.sha)
            except Exception:
                pass
        return True
    except Exception as e:
        st.error(f"Erro ao deletar empresa do GitHub: {e}")
        return False


def eh_proprietario_do_banco(nome_arquivo, usuario_logado, empresas_planos=None):
    if not usuario_logado or not nome_arquivo:
        return True

    nome_simples = os.path.basename(nome_arquivo)
    if empresas_planos and isinstance(empresas_planos, dict):
        for cod_emp, dados in empresas_planos.items():
            if f"plano_empresa_{cod_emp}.xlsx" == nome_simples:
                return str(dados.get("criador")) == str(usuario_logado)

    return False


def carregar_banco_dados_github(caminho_arquivo):
    garantir_pasta_local()
    mapa = {}
    if os.path.exists(caminho_arquivo):
        try:
            df_bd = pd.read_excel(caminho_arquivo, header=None)
            for _, r in df_bd.iterrows():
                cod = extrair_codigo_do_banco(r.iloc[0])
                descricao = extrair_descricao_do_banco(r.iloc[0])
                conta = limpar_conta(r.iloc[1]) if len(r) > 1 else ""
                conta_dom = limpar_conta(r.iloc[2]) if len(r) > 2 else ""
                conta_rec = limpar_conta(r.iloc[3]) if len(r) > 3 else ""
                conta_dom_rec = limpar_conta(r.iloc[4]) if len(r) > 4 else ""
                if cod:
                    mapa[cod] = {
                        "descricao": descricao,
                        "conta": conta,                  # Despesa Alterdata
                        "conta_dominio": conta_dom,      # Despesa Domínio
                        "conta_rec": conta_rec,          # Receita Alterdata
                        "conta_dominio_rec": conta_dom_rec # Receita Domínio
                    }
        except Exception as e:
            st.error(f"Erro ao carregar o Plano de Contas ({caminho_arquivo}): {e}")
    return mapa


def salvar_banco_dados_github(mapa, caminho_arquivo):
    garantir_pasta_local()
    linhas = [
        (
            montar_celula_banco(cod, dados.get("descricao", "")),
            dados.get("conta", ""),
            dados.get("conta_dominio", ""),
            dados.get("conta_rec", ""),
            dados.get("conta_dominio_rec", ""),
        )
        for cod, dados in mapa.items()
    ]
    df_bd = pd.DataFrame(linhas)
    df_bd.to_excel(caminho_arquivo, index=False, header=False)

    try:
        token = st.secrets.get("GITHUB_TOKEN")
        repo_name = st.secrets.get("REPO_NAME")

        if token and repo_name:
            g = Github(token)
            repo = g.get_repo(repo_name)

            with open(caminho_arquivo, "rb") as f:
                novo_conteudo = f.read()

            caminho_repo = caminho_arquivo.replace("\\", "/")

            try:
                contents = repo.get_contents(caminho_repo)
                repo.update_file(
                    contents.path,
                    f"Atualizando BD: {caminho_repo}",
                    novo_conteudo,
                    contents.sha,
                )
            except:
                repo.create_file(
                    caminho_repo,
                    f"Criando BD: {caminho_repo}",
                    novo_conteudo,
                )
            st.success(f"Plano de Contas salvo no GitHub em `{caminho_repo}`!")
        else:
            st.warning("Salvo apenas localmente (sem token configurado).")
    except Exception as e:
        st.error(f"Erro ao sincronizar com o GitHub ({caminho_arquivo}): {e}")


def deletar_conta_do_banco(mapa, codigo_deletar, caminho_arquivo):
    if codigo_deletar in mapa:
        del mapa[codigo_deletar]
        salvar_banco_dados_github(mapa, caminho_arquivo)
        return True
    return False


# ============================================================
# PARSER DE XML E GERADOR DE RELATÓRIOS
# ============================================================
def extrair_xml(caminho_ou_conteudo):
    if isinstance(caminho_ou_conteudo, bytes):
        root = ET.fromstring(caminho_ou_conteudo)
    elif isinstance(caminho_ou_conteudo, str) and caminho_ou_conteudo.endswith(".xml"):
        tree = ET.parse(caminho_ou_conteudo)
        root = tree.getroot()
    else:
        root = ET.fromstring(caminho_ou_conteudo)

    def find_tag(element, tag_name):
        if element is None:
            return None
        for child in element.iter():
            if child.tag.endswith(tag_name):
                return child
        return None

    def get_text(element, tag_name, default=""):
        node = find_tag(element, tag_name)
        return node.text.strip() if (node is not None and node.text) else default

    def get_float(element, tag_name, default=0.0):
        val = get_text(element, tag_name)
        try:
            return float(val) if val else default
        except ValueError:
            return default

    if root.tag.endswith("evento") or find_tag(root, "pedRegEvento") is not None:
        return {
            "tipo_xml": "EVENTO",
            "Chave NFS-e Original": get_text(root, "chNFSe"),
            "Chave NFS-e Substituta": get_text(root, "chSubstituta"),
            "Descrição Evento": get_text(root, "xDesc"),
            "Motivo Cancelamento": get_text(root, "xMotivo"),
            "Data Evento": get_text(root, "dhEvento"),
            "CNPJ Autor": get_text(root, "CNPJAutor") or get_text(root, "CNPJ"),
        }

    inf_nfse_node = find_tag(root, "infNFSe")
    chave_nfse = inf_nfse_node.attrib.get("Id", "") if inf_nfse_node is not None else ""
    if chave_nfse.startswith("NFS"):
        chave_nfse = chave_nfse[3:]

    numero_nfse = get_text(root, "nNFSe") or get_text(root, "Numero")
    data_competencia = get_text(root, "dCompet") or get_text(root, "DataEmissao")

    emit_node = find_tag(root, "emit") or find_tag(root, "PrestadorServico") or find_tag(root, "Prestador")

    nome_empresa = (
        get_text(emit_node, "xNome")
        or get_text(emit_node, "RazaoSocial")
        or get_text(root, "xNome")
    )

    cnpj_prestador = (
        get_text(emit_node, "CNPJ")
        or get_text(emit_node, "Cnpj")
        or get_text(root, "CNPJ")
        or get_text(root, "Cnpj")
    )

    toma_node = (
        find_tag(root, "toma")
        or find_tag(root, "TomadorServico")
        or find_tag(root, "Tomador")
    )
    nome_tomador = (
        get_text(toma_node, "xNome") or get_text(toma_node, "RazaoSocial")
        if toma_node is not None
        else ""
    )

    codigo_tributacao = get_text(root, "cTribNac") or get_text(root, "CodigoListaServico") or get_text(root, "ItemListaServico")
    tipo_servico = (
        get_text(root, "xTribNac")
        or get_text(root, "xTribMun")
        or get_text(root, "xDescServ")
        or get_text(root, "Discriminação")
        or get_text(root, "Discriminacao")
    )

    v_serv = get_float(root, "vServ") or get_float(root, "ValorServicos")
    v_liq = get_float(root, "vLiq") or get_float(root, "ValorLiquidoNfse")
    if v_liq == 0.0 and v_serv > 0.0:
        v_liq = v_serv

    def get_float_multi(element, nomes):
        for nome in nomes:
            valor = get_float(element, nome)
            if valor > 0:
                return valor
        return 0.0

    v_pis = get_float_multi(root, ["vPis", "ValorPis"])
    v_cofins = get_float_multi(root, ["vCofins", "ValorCofins"])
    v_csll = get_float_multi(root, ["vRetCSLL", "vCSLL", "ValorCsll"])
    v_irrf = get_float_multi(root, ["vRetIRRF", "vIRRF", "ValorIr"])
    v_inss = get_float_multi(root, ["vRetCP", "vINSS", "ValorInss"])
    v_iss = get_float_multi(root, ["vISSQN", "ValorIss"])

    v_desc = get_float(root, "vDescIncond") + get_float(root, "vDescCond") + get_float(root, "DescontoIncondicionado")
    v_base = round(v_serv - v_desc, 2)

    impostos = {
        "IRRF": v_irrf,
        "PIS": v_pis,
        "COFINS": v_cofins,
        "CSLL": v_csll,
        "INSS": v_inss,
        "ISS": v_iss,
    }
    retidos, status_validacao, qtd_comb = validar_retencoes(v_base, v_liq, impostos)

    def val_ret(nome):
        return impostos[nome] if retidos[nome] else 0.0

    def flag(nome):
        return "Com Retenção" if retidos[nome] else "Sem Retenção"

    lista_ret = [n for n in impostos if retidos[n]]
    texto_retencoes = (
        "Retenção " + "/".join(lista_ret) if lista_ret else "Sem Retenção"
    )
    diferenca = round(v_base - v_liq, 2)

    return {
        "tipo_xml": "NFSE",
        "Chave NFS-e": chave_nfse,
        "Número da NFS-e": numero_nfse,
        "Data Competência": data_competencia,
        "CNPJ Prestador": limpar_cnpj(cnpj_prestador),
        "Nome da Empresa": nome_empresa,
        "Nome do Tomador": nome_tomador,
        "Código Tributação": codigo_tributacao,
        "Tipo de Serviço": tipo_servico,
        "Valor do Serviço": v_serv,
        "Valor PIS": val_ret("PIS"),
        "PIS Retido?": flag("PIS"),
        "Valor COFINS": val_ret("COFINS"),
        "COFINS Retido?": flag("COFINS"),
        "CSLL (Retida)": val_ret("CSLL"),
        "CSLL Retida?": flag("CSLL"),
        "IRRF": val_ret("IRRF"),
        "IRRF Retido?": flag("IRRF"),
        "INSS (Previdenciária)": val_ret("INSS"),
        "INSS Retido?": flag("INSS"),
        "ISS": v_iss,
        "ISS Retenção": val_ret("ISS"),
        "ISS Retido?": flag("ISS"),
        "Valor Líquido": v_liq,
        "Diferença Bruto-Líquido": formatar_valor(diferenca),
        "Retenções Identificadas": texto_retencoes,
        "Valor Total Retenções": formatar_valor(diferenca),
        "Status Validação": status_validacao,
        "Combinações Encontradas": qtd_comb,
    }


def gerar_aba_alterdata(df_extrato, mapa_contas, modo="alterdata", contas=None, eh_receita=False):
    contas = contas or CONTAS[modo]
    historico = str(contas.get("historico", "99")).strip() or "99"
    if historico.isdigit():
        historico = int(historico)
    linhas_alterdata = []

    for _, row in df_extrato.iterrows():
        num_nota = str(row.get("Número da NFS-e", "") or "").strip()
        data_comp = converter_data_obj(row.get("Data Competência", ""))
        nome_empresa = str(row.get("Nome da Empresa", "") or "").strip()
        cod_trib = str(row.get("Código Tributação", "") or "").strip()
        descr_servico = str(mapa_contas.get(cod_trib, {}).get("descricao") or row.get("Tipo de Serviço") or "SERVIÇO").strip()

        if eh_receita:
            # RECEITA: Débito = Clientes e Crédito = Receita de Serviços
            conta_deb = contas["credito_principal"]
            conta_cred = conta_credito_receita_do_banco(mapa_contas, cod_trib, modo)
        else:
            # DESPESA: Débito = Despesa e Crédito = Fornecedores
            conta_deb = conta_debito_do_banco(mapa_contas, cod_trib, modo)
            conta_cred = contas["credito_principal"]

        # Descrição / complemento do histórico: "NF 00000 - NOME DO PRESTADOR"
        desc_padrao = f"NF {num_nota} - {nome_empresa}".strip()

        val_bruto = converter_valor(row.get("Valor do Serviço")) or 0.0
        val_liquido = converter_valor(row.get("Valor Líquido")) or 0.0

        val_pis = converter_valor(row.get("Valor PIS")) or 0.0
        val_cofins = converter_valor(row.get("Valor COFINS")) or 0.0
        val_csll = converter_valor(row.get("CSLL (Retida)")) or 0.0
        val_irrf = converter_valor(row.get("IRRF")) or 0.0
        val_inss = converter_valor(row.get("INSS (Previdenciária)")) or 0.0
        val_iss = converter_valor(row.get("ISS Retenção")) or 0.0

        soma_retencoes = val_pis + val_cofins + val_csll + val_irrf + val_inss + val_iss

        if soma_retencoes == 0.0:
            linhas_alterdata.append({
                "Data": data_comp,
                "debito": conta_deb,
                "credito": conta_cred,
                "valor": val_bruto,
                "documento": num_nota,
                "historico": historico,
                "descrição": desc_padrao,
            })
        else:
            linhas_alterdata.append({
                "Data": data_comp,
                "debito": conta_deb,
                "credito": "",
                "valor": val_bruto,
                "documento": num_nota,
                "historico": historico,
                "descrição": desc_padrao,
            })

            soma_pcc = 0.0
            if str(row.get("PIS Retido?", "")).strip().upper() == "COM RETENÇÃO":
                soma_pcc += val_pis
            if str(row.get("COFINS Retido?", "")).strip().upper() == "COM RETENÇÃO":
                soma_pcc += val_cofins
            if str(row.get("CSLL Retida?", "")).strip().upper() == "COM RETENÇÃO":
                soma_pcc += val_csll

            if soma_pcc > 0:
                desc_pcc = f"Retenção PCC s/ NF {num_nota} - {nome_empresa}"
                linhas_alterdata.append({
                    "Data": data_comp,
                    "debito": "",
                    "credito": contas["pcc"],
                    "valor": soma_pcc,
                    "documento": num_nota,
                    "historico": historico,
                    "descrição": desc_pcc,
                })

            if str(row.get("IRRF Retido?", "")).strip().upper() == "COM RETENÇÃO" and val_irrf > 0:
                desc_irrf = f"Retenção IRRF s/ NF {num_nota} - {nome_empresa}"
                linhas_alterdata.append({
                    "Data": data_comp,
                    "debito": "",
                    "credito": contas["irrf"],
                    "valor": val_irrf,
                    "documento": num_nota,
                    "historico": historico,
                    "descrição": desc_irrf,
                })

            if str(row.get("INSS Retido?", "")).strip().upper() == "COM RETENÇÃO" and val_inss > 0:
                desc_inss = f"Retenção INSS s/ NF {num_nota} - {nome_empresa}"
                linhas_alterdata.append({
                    "Data": data_comp,
                    "debito": "",
                    "credito": contas["inss"],
                    "valor": val_inss,
                    "documento": num_nota,
                    "historico": historico,
                    "descrição": desc_inss,
                })

            if str(row.get("ISS Retido?", "")).strip().upper() == "COM RETENÇÃO" and val_iss > 0:
                desc_iss = f"Retenção ISS s/ NF {num_nota} - {nome_empresa}"
                linhas_alterdata.append({
                    "Data": data_comp,
                    "debito": "",
                    "credito": contas["iss"],
                    "valor": val_iss,
                    "documento": num_nota,
                    "historico": historico,
                    "descrição": desc_iss,
                })

            linhas_alterdata.append({
                "Data": data_comp,
                "debito": "",
                "credito": conta_cred,
                "valor": val_liquido,
                "documento": num_nota,
                "historico": historico,
                "descrição": desc_padrao,
            })

    return pd.DataFrame(linhas_alterdata)


def gerar_txt_dominio(df_dominio, lote_inicial=1):
    def limpo(v):
        if v is None or (not isinstance(v, str) and pd.isna(v)):
            return ""
        return str(v).strip()

    linhas = []
    lote = int(lote_inicial) - 1

    for _, r in df_dominio.iterrows():
        deb = limpo(r.get("debito"))
        cred = limpo(r.get("credito"))

        data = r.get("Data")
        data_txt = data.strftime("%d/%m/%Y") if hasattr(data, "strftime") else ""

        valor = converter_valor(r.get("valor")) or 0.0
        valor_txt = f"{valor:.2f}".replace(".", ",")

        hist = limpo(r.get("descrição")).replace(";", " ")

        if deb:
            lote += 1
            lote_txt = str(lote)
        else:
            lote_txt = ""

        linhas.append(f"{data_txt};{deb};{cred};{valor_txt};{hist};{lote_txt};;;")

    conteudo = "\r\n".join(linhas) + "\r\n"
    return conteudo.encode("cp1252", errors="replace")


def gerar_aba_substituidas(eventos_list, df_nfse):
    if not eventos_list:
        return pd.DataFrame()

    linhas_subst = []
    mapa_nfse = {}

    if df_nfse is not None and not df_nfse.empty:
        for _, row in df_nfse.iterrows():
            chave = str(row.get("Chave NFS-e", "")).strip()
            if chave:
                mapa_nfse[chave] = row

    for ev in eventos_list:
        ch_orig = ev.get("Chave NFS-e Original", "")
        ch_sub = ev.get("Chave NFS-e Substituta", "")

        nf_orig = mapa_nfse.get(ch_orig, {})
        nf_sub = mapa_nfse.get(ch_sub, {})

        fornecedor_nome = (
            nf_orig.get("Nome da Empresa")
            or nf_sub.get("Nome da Empresa")
            or "Não encontrado no lote"
        )
        fornecedor_cnpj = (
            nf_orig.get("CNPJ Prestador")
            or nf_sub.get("CNPJ Prestador")
            or ev.get("CNPJ Autor", "N/A")
        )

        linhas_subst.append({
            "Fornecedor / Prestador": fornecedor_nome,
            "CNPJ Fornecedor": fornecedor_cnpj,
            "Chave Nota Cancelada": ch_orig,
            "Nº Nota Cancelada": nf_orig.get("Número da NFS-e", "Não importada no lote"),
            "Valor Nota Cancelada": nf_orig.get("Valor do Serviço", "N/A"),
            "Chave Nota Substituta (Nova)": ch_sub,
            "Nº Nota Substituta (Nova)": nf_sub.get("Número da NFS-e", "Não importada no lote"),
            "Valor Nota Substituta": nf_sub.get("Valor do Serviço", "N/A"),
            "Motivo Cancelamento": ev.get("Motivo Cancelamento", "Não informado"),
            "Descrição do Evento": ev.get("Descrição Evento", ""),
            "Data do Evento": ev.get("Data Evento", ""),
        })

    return pd.DataFrame(linhas_subst)


def gerar_zip_pdfs_renomeados(df_nfse, pdfs_mapeados):
    if not pdfs_mapeados or df_nfse.empty:
        return None

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_out:
        for _, row in df_nfse.iterrows():
            chave = str(row.get("Chave NFS-e", "")).strip()
            num_nota = str(row.get("Número da NFS-e", "")).strip()
            fornecedor = limpar_nome_arquivo(row.get("Nome da Empresa", "FORNECEDOR"))
            pasta_mes = extrair_pasta_mes_ano(row.get("Data Competência"))

            caminho_pdf_original = pdfs_mapeados.get(chave) or pdfs_mapeados.get(num_nota)

            if caminho_pdf_original and os.path.exists(caminho_pdf_original):
                nome_pdf = f"{fornecedor} - NF {num_nota}.pdf"
                caminho_no_zip = os.path.join(pasta_mes, nome_pdf)
                zip_out.write(caminho_pdf_original, arcname=caminho_no_zip)

    zip_buffer.seek(0)
    return zip_buffer.getvalue() if zip_buffer.getbuffer().nbytes > 0 else None


# ============================================================
# EMPRESA DE EXEMPLO POR CÓDIGO DE TRIBUTAÇÃO
# (ajuda a classificar cada código: mostra uma empresa que o usa)
# ============================================================
def empresa_exemplo_por_codigo(df, eh_receita=False):
    """{código: nome}. Despesa -> prestador (fornecedor); receita -> tomador (cliente)."""
    mapa = {}
    col = "Nome do Tomador" if eh_receita else "Nome da Empresa"
    if df is None or df.empty or col not in df.columns:
        return mapa
    for _, r in df.iterrows():
        cod = str(r.get("Código Tributação", "") or "").strip()
        nome = str(r.get(col, "") or "").strip()
        if cod and nome and nome.lower() != "nan" and cod not in mapa:
            mapa[cod] = nome
    return mapa


def tabela_codigos_lote(df, mapa_contas, modo, eh_receita=False, mapa_descricoes=None):
    """DataFrame com um código por linha: serviço, empresa de exemplo, qtd. de notas e conta."""
    mapa_descricoes = mapa_descricoes or {}
    exemplos = empresa_exemplo_por_codigo(df, eh_receita)
    if df is None or df.empty or "Código Tributação" not in df.columns:
        return pd.DataFrame()
    chave_conta = (
        ("conta_dominio_rec" if eh_receita else "conta_dominio")
        if modo == "dominio"
        else ("conta_rec" if eh_receita else "conta")
    )
    rotulo = "Cliente de exemplo" if eh_receita else "Empresa de exemplo"
    nome_modo = NOMES_MODO.get(modo, modo)
    linhas = []
    for cod, grupo in df.groupby(df["Código Tributação"].astype(str).str.strip()):
        if not cod or cod.lower() == "nan":
            continue
        linhas.append({
            "Código": cod,
            "Serviço": mapa_contas.get(cod, {}).get("descricao") or mapa_descricoes.get(cod, ""),
            rotulo: exemplos.get(cod, ""),
            "Qtd. notas": len(grupo),
            f"Conta {nome_modo}": mapa_contas.get(cod, {}).get(chave_conta, "") or "— não cadastrada —",
        })
    return pd.DataFrame(linhas)


def editor_codigos_lote(df, mapa_contas, caminho_arquivo_bd, modo, eh_receita, eh_dono,
                        mapa_descricoes=None, chave_estado="lote"):
    """
    Mostra a tabela de códigos do lote. O dono do plano edita a coluna da conta
    direto na tabela e salva tudo com UM botão. Retorna a lista de códigos que
    ainda estão sem conta cadastrada.
    """
    mapa_descricoes = mapa_descricoes or {}
    if df is None or df.empty or "Código Tributação" not in df.columns:
        return []

    if st.session_state.pop(f"codigos_salvos_ok_{chave_estado}", False):
        st.success("Contas salvas no plano de contas!")

    nome_modo = NOMES_MODO.get(modo, modo)
    tipo_conta = "Receita" if eh_receita else "Despesa"
    col_conta = f"Conta {nome_modo} ({tipo_conta})"
    rotulo_emp = "Cliente de exemplo" if eh_receita else "Empresa de exemplo"
    chave_conta = (
        ("conta_dominio_rec" if eh_receita else "conta_dominio")
        if modo == "dominio"
        else ("conta_rec" if eh_receita else "conta")
    )
    conta_padrao = CONTAS[modo]["credito_receita"] if eh_receita else CONTAS[modo]["debito_padrao"]
    exemplos = empresa_exemplo_por_codigo(df, eh_receita)

    linhas, ausentes = [], []
    for cod, grupo in df.groupby(df["Código Tributação"].astype(str).str.strip()):
        if not cod or cod.lower() == "nan":
            continue
        falta = codigo_precisa_cadastro(mapa_contas, cod, modo, eh_receita=eh_receita)
        if falta:
            ausentes.append(cod)
        atual = str(mapa_contas.get(cod, {}).get(chave_conta, "") or "").strip()
        servico = mapa_contas.get(cod, {}).get("descricao") or mapa_descricoes.get(cod, "")
        linhas.append({
            "Código": cod,
            "Serviço": str(servico or ""),
            rotulo_emp: exemplos.get(cod, ""),
            "Qtd. notas": len(grupo),
            "Situação": "⚠️ Falta cadastrar" if falta else "✅ Cadastrada",
            col_conta: atual if atual else (conta_padrao if eh_dono else "— não cadastrada —"),
        })

    df_tab = pd.DataFrame(linhas)
    if df_tab.empty:
        return []

    with st.expander("📋 Códigos de tributação deste lote (com empresa de exemplo)", expanded=True):
        if not eh_dono:
            st.dataframe(df_tab, use_container_width=True, hide_index=True)
            return ausentes

        st.caption(
            f"Ajuste a coluna **{col_conta}** direto na tabela (dois cliques na célula) "
            "e clique em **Salvar contas** no final. Nas linhas ⚠️ já vem a conta padrão sugerida."
        )
        versao = st.session_state.get(f"ver_codigos_{chave_estado}", 0)
        editado = st.data_editor(
            df_tab,
            hide_index=True,
            use_container_width=True,
            num_rows="fixed",
            disabled=[c for c in df_tab.columns if c != col_conta],
            key=f"editor_codigos_{chave_estado}_{modo}_{int(eh_receita)}_{versao}",
            column_config={col_conta: st.column_config.TextColumn(col_conta)},
        )

        if st.button("💾 Salvar contas", type="primary", key=f"btn_salvar_codigos_{chave_estado}"):
            alterou = False
            for _, r in editado.iterrows():
                cod = str(r["Código"]).strip()
                nova = limpar_conta(r[col_conta])
                existente = mapa_contas.get(cod)
                if existente is None:
                    if not nova:
                        continue
                    existente = {
                        "descricao": str(r["Serviço"] or ""),
                        "conta": "", "conta_dominio": "",
                        "conta_rec": "", "conta_dominio_rec": "",
                    }
                if str(existente.get(chave_conta, "") or "").strip() != nova:
                    existente[chave_conta] = nova
                    mapa_contas[cod] = existente
                    alterou = True
            if alterou:
                salvar_banco_dados_github(mapa_contas, caminho_arquivo_bd)
                st.session_state[f"ver_codigos_{chave_estado}"] = versao + 1
                st.session_state[f"codigos_salvos_ok_{chave_estado}"] = True
                st.rerun()
            else:
                st.info("Nenhuma alteração para salvar.")

    return ausentes


# ============================================================
# PÁGINA STREAMLIT SIEG XML
# ============================================================
def pagina_sieg_xml(mapa_contas=None, caminho_arquivo_bd=None, eh_dono=True):
    st.title("📄 SIEG XML PARA Importação")
    st.write("Faça o upload dos arquivos **XML**, **PDF** ou **ZIP**.")

    if mapa_contas is None and caminho_arquivo_bd:
        mapa_contas = carregar_banco_dados_github(caminho_arquivo_bd)

    uploaded_files = st.file_uploader(
        "Arraste ou selecione os arquivos XML, PDF ou ZIP aqui",
        type=["xml", "pdf", "zip"],
        accept_multiple_files=True,
    )

    if uploaded_files:
        col_btn1, col_btn2, _ = st.columns([1, 1, 2])
        with col_btn1:
            processar_alterdata = st.button("🚀 Processar para Alterdata")
        with col_btn2:
            processar_dominio = st.button("🚀 Processar para Domínio")

        if processar_alterdata or processar_dominio:
            modo_escolhido = "dominio" if processar_dominio else "alterdata"
            st.session_state["modo"] = modo_escolhido

            temp_dir = tempfile.mkdtemp()
            xmls_para_processar = []
            pdfs_encontrados = {}

            for uploaded_file in uploaded_files:
                nome_arquivo = uploaded_file.name
                extensao = os.path.splitext(nome_arquivo)[1].lower()

                if extensao == ".xml":
                    caminho_xml = os.path.join(temp_dir, nome_arquivo)
                    with open(caminho_xml, "wb") as f:
                        f.write(uploaded_file.getbuffer())
                    xmls_para_processar.append(caminho_xml)

                elif extensao == ".pdf":
                    caminho_pdf = os.path.join(temp_dir, nome_arquivo)
                    with open(caminho_pdf, "wb") as f:
                        f.write(uploaded_file.getbuffer())
                    nome_sem_ext = os.path.splitext(nome_arquivo)[0]
                    pdfs_encontrados[nome_sem_ext] = caminho_pdf

                elif extensao == ".zip":
                    caminho_zip = os.path.join(temp_dir, nome_arquivo)
                    with open(caminho_zip, "wb") as f:
                        f.write(uploaded_file.getbuffer())

                    pasta_zip = os.path.join(temp_dir, os.path.splitext(nome_arquivo)[0])
                    os.makedirs(pasta_zip, exist_ok=True)

                    try:
                        with zipfile.ZipFile(caminho_zip, "r") as zip_ref:
                            zip_ref.extractall(pasta_zip)

                        for raiz, _, arquivos in os.walk(pasta_zip):
                            for arq in arquivos:
                                ext = os.path.splitext(arq)[1].lower()
                                caminho_completo = os.path.join(raiz, arq)
                                if ext == ".xml":
                                    xmls_para_processar.append(caminho_completo)
                                elif ext == ".pdf":
                                    nome_sem_ext = os.path.splitext(arq)[0]
                                    pdfs_encontrados[nome_sem_ext] = caminho_completo
                    except Exception as e:
                        st.error(f"Erro ao descompactar {nome_arquivo}: {e}")

            if xmls_para_processar:
                registros_nfse = []
                registros_eventos = []
                erros_processamento = []
                progress_bar = st.progress(0)
                status_text = st.empty()

                for i, caminho_xml in enumerate(xmls_para_processar):
                    nome_xml = os.path.basename(caminho_xml)
                    status_text.text(f"Processando [{i+1}/{len(xmls_para_processar)}]: {nome_xml}")
                    try:
                        dados = extrair_xml(caminho_xml)
                        if dados.get("tipo_xml") == "EVENTO":
                            registros_eventos.append(dados)
                        else:
                            registros_nfse.append(dados)
                    except Exception as err:
                        erros_processamento.append(f"{nome_xml}: {str(err)}")
                    progress_bar.progress((i + 1) / len(xmls_para_processar))

                status_text.text("Extração concluída!")

                df_nfse = pd.DataFrame(registros_nfse)
                st.session_state["df_extrato"] = df_nfse
                st.session_state["eventos_list"] = registros_eventos
                st.session_state["zip_pdf_bytes"] = gerar_zip_pdfs_renomeados(df_nfse, pdfs_encontrados)

                # DETERMINA RECEITA x DESPESA
                cnpj_emp_sel = limpar_cnpj(st.session_state.get("empresa_ativa_cnpj", ""))
                cnpjs_prest_lote = set(df_nfse["CNPJ Prestador"].dropna().apply(limpar_cnpj).unique()) if not df_nfse.empty else set()
                eh_receita = bool(cnpj_emp_sel and cnpj_emp_sel in cnpjs_prest_lote)
                st.session_state["eh_receita_lote"] = eh_receita

                mapa_tipo_servico_xml = {}
                if not df_nfse.empty:
                    for _, row in df_nfse.iterrows():
                        cod = str(row.get("Código Tributação", "") or "").strip()
                        tipo = str(row.get("Tipo de Serviço", "") or "").strip()
                        if cod and tipo and cod not in mapa_tipo_servico_xml:
                            mapa_tipo_servico_xml[cod] = tipo
                st.session_state["mapa_tipo_servico_xml"] = mapa_tipo_servico_xml

            shutil.rmtree(temp_dir, ignore_errors=True)

    if "df_extrato" in st.session_state and st.session_state["df_extrato"] is not None:
        modo = st.session_state.get("modo", "alterdata")
        nome_modo = NOMES_MODO[modo]
        df = st.session_state["df_extrato"]
        eventos_list = st.session_state.get("eventos_list", [])
        mapa_tipo_servico_xml = st.session_state.get("mapa_tipo_servico_xml", {})
        zip_pdf_bytes = st.session_state.get("zip_pdf_bytes")
        eh_receita = st.session_state.get("eh_receita_lote", False)

        if eh_receita:
            st.success("💰 **TIPO DE OPERAÇÃO IDENTIFICADA: RECEITA (SERVIÇOS PRESTADOS)**\n\nO CNPJ da empresa é o mesmo do prestador nas notas.")
        else:
            st.info("🛒 **TIPO DE OPERAÇÃO IDENTIFICADA: DESPESA (SERVIÇOS TOMADOS)**\n\nO CNPJ da empresa é diferente do prestador nas notas.")

        # ------------------------------------------------------------
        # TABELA EDITÁVEL DE CÓDIGOS DO LOTE (um único botão para salvar)
        # ------------------------------------------------------------
        ausentes = editor_codigos_lote(
            df, mapa_contas, caminho_arquivo_bd, modo, eh_receita, eh_dono,
            mapa_descricoes=mapa_tipo_servico_xml, chave_estado="sieg",
        )

        if ausentes:
            if eh_dono:
                st.warning(
                    f"⚠️ Existem códigos sem conta de {'RECEITA' if eh_receita else 'DESPESA'} {nome_modo} cadastrada. "
                    "Confira a tabela acima e clique em **💾 Salvar contas** para liberar a prévia e os downloads."
                )
            else:
                st.error(f"⚠️ Os códigos `{', '.join(ausentes)}` não estão cadastrados. Solicite ao dono deste plano que adicione as contas.")

        else:
            df_codigos_lote = tabela_codigos_lote(df, mapa_contas, modo, eh_receita, mapa_tipo_servico_xml)

            st.subheader(f"🧾 Contas dos Impostos e Conta Contrapartida - {nome_modo}")
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
            colunas_contas = st.columns(len(campos_contas))
            for coluna, (chave, rotulo) in zip(colunas_contas, campos_contas):
                with coluna:
                    v_dig = st.text_input(rotulo, value=padrao[chave], key=f"conta_sieg_{modo}_{chave}")
                    contas_editadas[chave] = v_dig.strip() or padrao[chave]

            df_lancamentos = gerar_aba_alterdata(df, mapa_contas, modo, contas_editadas, eh_receita=eh_receita)
            df_substituidas = gerar_aba_substituidas(eventos_list, df)
            nome_aba = "Domínio" if modo == "dominio" else "Alterdata"

            st.subheader(f"📊 Prévia - Aba {nome_aba}")
            st.dataframe(df_lancamentos, use_container_width=True)

            buffer_excel = io.BytesIO()
            with pd.ExcelWriter(buffer_excel, engine="openpyxl", date_format="dd/mm/yyyy") as writer:
                df_lancamentos.to_excel(writer, index=False, sheet_name=nome_aba)
                df.drop(columns=["Nome do Tomador"], errors="ignore").to_excel(writer, index=False, sheet_name="NFS-e Extraídas")
                if not df_codigos_lote.empty:
                    df_codigos_lote.to_excel(writer, index=False, sheet_name="Códigos do Lote")
                if not df_substituidas.empty:
                    df_substituidas.to_excel(writer, index=False, sheet_name="Notas Canceladas")

            st.markdown("---")
            st.subheader("📥 Downloads Disponíveis")

            if modo == "dominio":
                lote_inicial = st.number_input("Nº do primeiro lote (Domínio)", min_value=1, value=1, step=1)
                txt_dominio = gerar_txt_dominio(df_lancamentos, lote_inicial)

                col1, col2, col3 = st.columns(3)
                with col1:
                    st.download_button("📄 Baixar Layout Domínio (.txt)", data=txt_dominio, file_name="importacao_dominio.txt", mime="text/plain")
                with col2:
                    st.download_button("📊 Baixar Planilha Domínio (.xlsx)", data=buffer_excel.getvalue(), file_name="importacao_dominio.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                with col3:
                    if zip_pdf_bytes:
                        st.download_button("📦 Baixar PDFs Organizados (.zip)", data=zip_pdf_bytes, file_name="NFS_PDFs_Organizados.zip", mime="application/zip")
            else:
                col1, col2 = st.columns(2)
                with col1:
                    st.download_button("📊 Baixar Planilha Alterdata (.xlsx)", data=buffer_excel.getvalue(), file_name="importacao_alterdata.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                with col2:
                    if zip_pdf_bytes:
                        st.download_button("📦 Baixar PDFs Organizados (.zip)", data=zip_pdf_bytes, file_name="NFS_PDFs_Organizados.zip", mime="application/zip")
