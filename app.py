import streamlit as st
import pandas as pd
import json, base64, os, re, requests, io
import gspread
from google.oauth2.service_account import Credentials

# ==============================
# CONFIGURAÇÃO DA PÁGINA
# ==============================
st.set_page_config(page_title="Quiz de Imagens", layout="wide")

# ==============================
# CONEXÃO COM GOOGLE SHEETS
# ==============================
@st.cache_resource
def carregar_planilha():
    # Credenciais salvas no arquivo service_account.json
    creds = Credentials.from_service_account_file(
        "service_account.json",
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
    )
    cliente = gspread.authorize(creds)

    # URL da planilha
    SHEET_URL = "https://docs.google.com/spreadsheets/d/SEU_ID_AQUI"
    spreadsheet_id = SHEET_URL.split("/d/")[1].split("/")[0]

    planilha = cliente.open_by_key(spreadsheet_id)
    aba = planilha.sheet1
    dados = aba.get_all_records()
    df = pd.DataFrame(dados)
    return df

df = carregar_planilha()

# ==============================
# FUNÇÃO PARA BUSCAR RESPOSTA
# ==============================
def buscar_resposta(pergunta_usuario):
    pergunta_usuario = pergunta_usuario.lower().strip()

    # tenta achar em colunas texto
    colunas_texto = [c for c in df.columns if "texto" in c.lower() or "descrição" in c.lower() or "resposta" in c.lower()]
    colunas_imagem = [c for c in df.columns if "imagem" in c.lower() or "foto" in c.lower() or "url" in c.lower()]

    # Procura em todas as colunas texto se contém palavras da pergunta
    resultado_texto = []
    for i, row in df.iterrows():
        for col in df.columns:
            if isinstance(row[col], str) and row[col].strip() != "":
                if any(palavra in row[col].lower() for palavra in pergunta_usuario.split()):
                    resultado_texto.append((i, col, row[col]))

    if not resultado_texto:
        return None, None

    # Usa a primeira linha encontrada
    idx, col, texto = resultado_texto[0]
    imagem = None
    for col_img in colunas_imagem:
        if col_img in df.columns and isinstance(df.loc[idx, col_img], str) and df.loc[idx, col_img].strip() != "":
            imagem = df.loc[idx, col_img]
            break

    return texto, imagem

# ==============================
# INTERFACE
# ==============================
st.title("Quiz de Imagens")

pergunta = st.text_input("Faça sua pergunta:")

if pergunta:
    texto, imagem = buscar_resposta(pergunta)
    if texto:
        st.write(f"**Resposta:** {texto}")
        if imagem:
            st.image(imagem, use_column_width=True)
    else:
        st.warning("Não encontrei essa informação na planilha.")
