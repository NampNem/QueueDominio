"""
Leitor de Excel de NFS-e (Aba 'Relação' do Portal Nacional) - Regras da Macro V2.
Devolve os dados formatados para a geração de lançamentos no Alterdata/Domínio.
"""
import numbers
import re
from datetime import datetime
import pandas as pd

ABA_PREFERIDA = "Relação"
COLUNAS_OBRIGATORIAS = ["Número NFS-e", "Valor do Serviço (R$)"]


def _texto(v):
    try:
        if v is None or pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    return str(v).strip()


def _num(v):
    if isinstance(v, bool):
        return 0.0
    if isinstance(v, numbers.Number):
        return 0.0 if pd.isna(v) else float(v)
    t = _texto(v).replace("R$", "").replace(" ", "")
    if not t or t == "-":
        return 0.0
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return 0.0


def _numero_nota(v):
    t = _texto(v)
    if not t:
        return ""
    try:
        f = float(t)
        if f.is_integer():
            return str(int(f))
    except ValueError:
        pass
    return t


def _data_iso(v):
    if v is None or pd.isna(v):
        return ""
    if isinstance(v, (datetime, pd.Timestamp)):
        return v.strftime("%Y-%m-%d")
    t = _texto(v)
    if not t:
        return ""
    dt = pd.to_datetime(t, dayfirst=True, errors="coerce")
    return "" if pd.isna(dt) else dt.strftime("%Y-%m-%d")


def extrair_nfse_excel(origem, formatar_valor_fn):
    xls = pd.ExcelFile(origem)
    aba = ABA_PREFERIDA if ABA_PREFERIDA in xls.sheet_names else xls.sheet_names[0]
    df = xls.parse(aba, dtype=object)
    df.columns = [str(c).strip() for c in df.columns]

    faltando = [c for c in COLUNAS_OBRIGATORIAS if c not in df.columns]
    if faltando:
        raise ValueError(
            f"Colunas não encontradas na aba '{aba}': {', '.join(faltando)}. "
            "Verifique se o arquivo é a relação do Portal Nacional esperada."
        )

    registros = []
    ignoradas = []

    for row in df.to_dict("records"):
        situacao = _texto(row.get("Situação"))
        numero = _numero_nota(row.get("Número NFS-e"))

        if situacao.lower() == "cancelada" or not numero:
            if numero:
                ignoradas.append({
                    "Número da NFS-e": numero,
                    "Fornecedor": _texto(row.get("Nome Prestador")),
                    "Valor do Serviço": formatar_valor_fn(_num(row.get("Valor do Serviço (R$)"))),
                    "Situação": situacao or "Linha em Branco"
                })
            continue

        nome_empresa = _texto(row.get("Nome Prestador"))
        # Tomador (cliente): usado como "empresa de exemplo" quando o lote é de RECEITA
        nome_tomador = _texto(row.get("Nome Tomador"))
        v_serv = _num(row.get("Valor do Serviço (R$)"))
        v_inss = _num(row.get("Contrib. Previd. Ret. (R$)"))
        v_irrf = _num(row.get("IRRF (R$)"))
        v_iss_bruto = _num(row.get("Valor do ISSQN (R$)"))

        retencao_iss_texto = _texto(row.get("Retenção ISSQN"))
        v_iss_retido = v_iss_bruto if retencao_iss_texto == "2 - Retido pelo Tomador" else 0.0

        descr_contrib = _texto(row.get("Descr. Contrib. Sociais Ret."))
        v_csll_bruto = _num(row.get("Contrib. Sociais Ret. (R$)"))

        if descr_contrib.startswith("3") or descr_contrib.startswith("8") or not descr_contrib:
            v_pcc = v_csll_bruto
            nome_contrib = "CSLL" if descr_contrib.startswith("8") else "PIS/COFINS/CSLL"
        else:
            v_pcc = 0.0
            nome_contrib = "PIS/COFINS/CSLL"

        total_retencoes = round(v_pcc + v_inss + v_irrf + v_iss_retido, 2)
        v_liq = round(v_serv - total_retencoes, 2)

        # Extração do Código e da Descrição do Serviço
        cod_texto = _texto(row.get("Cód. Tributação Nacional"))
        m = re.match(r"^(\d+)\s*-?\s*(.*)$", cod_texto, re.S)
        if m:
            codigo_tributacao, tipo_servico = m.group(1), m.group(2).strip()
        else:
            codigo_tributacao, tipo_servico = cod_texto, ""

        if not tipo_servico:
            tipo_servico = _texto(row.get("Descrição do Serviço"))

        cnpj_limpo = re.sub(r"\D", "", _texto(row.get("CNPJ/CPF Prestador")))
        if len(cnpj_limpo) > 11:
            cnpj_formatado = cnpj_limpo.zfill(14)
        elif cnpj_limpo:
            cnpj_formatado = cnpj_limpo.zfill(11)
        else:
            cnpj_formatado = ""

        registros.append({
            "tipo_xml": "NFSE",
            "Chave NFS-e": _texto(row.get("Chave NFS-e")),
            "Número da NFS-e": numero,
            "Data Competência": _data_iso(row.get("Data Geração")),
            "CNPJ Prestador": cnpj_formatado,
            "Nome da Empresa": nome_empresa,
            "Nome do Tomador": nome_tomador,
            "Código Tributação": codigo_tributacao,
            "Tipo de Serviço": tipo_servico,
            "Valor do Serviço": v_serv,
            "Valor PIS": 0.0,
            "PIS Retido?": "Sem Retenção",
            "Valor COFINS": 0.0,
            "COFINS Retido?": "Sem Retenção",
            "CSLL (Retida)": v_pcc,
            "CSLL Retida?": "Com Retenção" if v_pcc > 0 else "Sem Retenção",
            "IRRF": v_irrf,
            "IRRF Retido?": "Com Retenção" if v_irrf > 0 else "Sem Retenção",
            "INSS (Previdenciária)": v_inss,
            "INSS Retido?": "Com Retenção" if v_inss > 0 else "Sem Retenção",
            "ISS": v_iss_bruto,
            "ISS Retenção": v_iss_retido,
            "ISS Retido?": "Com Retenção" if v_iss_retido > 0 else "Sem Retenção",
            "Valor Líquido": v_liq,
            "Diferença Bruto-Líquido": formatar_valor_fn(total_retencoes),
            "Retenções Identificadas": nome_contrib if v_pcc > 0 else "Sem Retenção",
            "Valor Total Retenções": formatar_valor_fn(total_retencoes),
            "Status Validação": "OK",
            "Combinações Encontradas": 1
        })

    return registros, ignoradas
