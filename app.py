import streamlit as st
from nfse_dominio import pagina_nfse_dominio

st.set_page_config(page_title="NFS-e → Domínio", page_icon="🧾", layout="wide")
pagina_nfse_dominio(mostrar_voltar=False)
