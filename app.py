import streamlit as st
import pandas as pd
import json, base64, os, re, requests, io
import gspread
from google.oauth2.service_account import Credentials
from google import genai
import unicodedata  # 🔹 para remover acentos

# ===== Configuração da página =====
st.set_page_config(page_title="Plasprint Ai", layout="wide")

# ===== Funções auxiliares =====
def remove_accents(text):
    return ''.join(c for c in unicodedata.normalize('NFD', text)
                   if unicodedata.category(c) != 'Mn')

# ===== Inicializar cliente Gemini =====
GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
genai_client = genai.Client(api_key=GEMINI_API_KEY)

# ===== Conectar Google Sheets =====
SERVICE_ACCOUNT_B64 = st.secrets["SERVICE_ACCOUNT_B64"]
SERVICE_ACCOUNT_JSON = base64.b64decode(SERVICE_ACCOUNT_B64).decode()
SERVICE_ACCOUNT_INFO = json.loads(SERVICE_ACCOUNT_JSON)

creds = Credentials.from_service_account_info(SERVICE_ACCOUNT_INFO, scopes=["https://www.googleapis.com/auth/spreadsheets"])
client = gspread.authorize(creds)

SHEET_ID = st.secrets["SHEET_ID"]
sh = client.open_by_key(SHEET_ID)

# ===== Carregar DataFrames com cache =====
@st.cache_data(ttl=60)
def read_ws(name):
    try:
        ws = sh.worksheet(name)
        values = ws.get_all_values()
        if not values:
            return pd.DataFrame()
        max_len = max(len(r) for r in values)
        values = [r + [""] * (max_len - len(r)) for r in values]
        header = values[0]
        if len(header) < max_len:
            header = header + [f"col_{i}" for i in range(len(header), max_len)]
        rows = values[1:]
        df = pd.DataFrame(rows, columns=header)
        # 🔹 remove linhas completamente vazias
        is_empty_row = df.apply(lambda r: "".join(map(str, r)).strip() == "", axis=1)
        df = df[~is_empty_row].reset_index(drop=True)
        return df
    except:
        return pd.DataFrame()

# ===== DataFrames =====
dfs = {
    "trabalhos": read_ws("trabalhos"),
    "erros": read_ws("erros"),
    "dacen": read_ws("dacen"),
    "psi": read_ws("psi"),
}

# ===== Sidebar - Contadores =====
st.sidebar.header("Dados carregados")
for name, df in dfs.items():
    st.sidebar.write(f"{name}: {len(df)} linhas")

# ===== Botão para atualizar manualmente =====
if st.sidebar.button("🔄 Atualizar dados", use_container_width=True):
    st.cache_data.clear()
    st.session_state["refresh"] = True

if st.session_state.get("refresh", False):
    st.session_state["refresh"] = False
    st.rerun()

# ===== Busca de linhas relevantes =====
def search_relevant_rows(dfs, max_per_sheet=200):
    results = {}
    for name, df in dfs.items():
        if df.empty:
            continue
        # 🔹 agora todas as abas pegam as últimas linhas
        results[name] = df.tail(max_per_sheet).reset_index(drop=True)
    return results

# ===== Montar contexto para o modelo =====
def build_context(dfs, query):
    relevant_dfs = search_relevant_rows(dfs)
    context_parts = []
    for name, df in relevant_dfs.items():
        context_parts.append(f"Aba {name} (últimas linhas):\n{df.to_string(index=False)}")
    context = "\n\n".join(context_parts)
    return context

# ===== Chat =====
st.title("🤖 Plasprint Ai")

if "messages" not in st.session_state:
    st.session_state["messages"] = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

if prompt := st.chat_input("Digite sua pergunta..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    context = build_context(dfs, prompt)
    full_prompt = f"Você é um assistente. Use os dados abaixo para responder.\n\n{context}\n\nPergunta: {prompt}"

    try:
        response = genai_client.models.generate_content(
            model="gemini-1.5-flash",
            contents=full_prompt
        )
        answer = response.text
    except Exception as e:
        answer = f"Erro ao chamar Gemini: {e}"

    st.session_state.messages.append({"role": "assistant", "content": answer})
    with st.chat_message("assistant"):
        st.markdown(answer)
