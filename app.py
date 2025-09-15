import streamlit as st
import pandas as pd
import json, base64, os, re, requests, io, time
import gspread
from google.oauth2.service_account import Credentials
from google import genai
from difflib import SequenceMatcher

# ===== Configuração da página =====
st.set_page_config(page_title="PlasPrint IA", page_icon="favicon.ico", layout="wide")

# ===== Funções auxiliares =====
@st.cache_data(ttl=300)
def get_usd_brl_rate():
    try:
        res = requests.get("https://economia.awesomeapi.com.br/json/last/USD-BRL")
        data = res.json()
        return float(data["USDBRL"]["ask"])
    except:
        return None

def inject_favicon():
    favicon_path = "favicon.ico"
    try:
        with open(favicon_path, "rb") as f:
            data = base64.b64encode(f.read()).decode()
        st.markdown(f'<link rel="icon" href="data:image/x-icon;base64,{data}" type="image/x-icon" />', unsafe_allow_html=True)
    except:
        pass
inject_favicon()

def get_base64_of_jpg(image_path):
    with open(image_path, "rb") as img_file:
        return base64.b64encode(img_file.read()).decode()

# ===== Carregar segredos =====
try:
    GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
    SHEET_ID = st.secrets["SHEET_ID"]
    SERVICE_ACCOUNT_B64 = st.secrets["SERVICE_ACCOUNT_B64"]
except:
    st.error("Configure os segredos GEMINI_API_KEY, SHEET_ID e SERVICE_ACCOUNT_B64.")
    st.stop()

sa_json = json.loads(base64.b64decode(SERVICE_ACCOUNT_B64).decode())
scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
creds = Credentials.from_service_account_info(sa_json, scopes=scopes)
gc = gspread.authorize(creds)
try:
    sh = gc.open_by_key(SHEET_ID)
except Exception as e:
    st.error(f"Não consegui abrir a planilha: {e}")
    st.stop()

# ===== Carregar DataFrames com cache =====
@st.cache_data
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
        return pd.DataFrame(rows, columns=header)
    except:
        return pd.DataFrame()

erros_df = read_ws("erros")
trabalhos_df = read_ws("trabalhos")
dacen_df = read_ws("dacen")
psi_df = read_ws("psi")
gerais_df = read_ws("gerais")

st.sidebar.header("Dados carregados")
st.sidebar.write("erros:", len(erros_df))
st.sidebar.write("trabalhos:", len(trabalhos_df))
st.sidebar.write("dacen:", len(dacen_df))
st.sidebar.write("psi:", len(psi_df))
st.sidebar.write("gerais:", len(gerais_df))

# ===== Cliente Gemini =====
os.environ["GEMINI_API_KEY"] = GEMINI_API_KEY
client = genai.Client()

# ===== Função de busca fuzzy =====
def buscar_resposta_fuzzy(pergunta, dfs, limiar=0.5):
    palavras_pergunta = pergunta.lower().split()
    melhor_row = None
    melhor_score = 0
    melhor_texto = ""
    melhor_imagem = ""

    for df in dfs.values():
        for _, row in df.iterrows():
            # Unir todas as colunas como texto da linha
            texto = " ".join([str(val) for val in row.values if val and isinstance(val, str)]).lower()
            palavras_linha = texto.split()

            score_total = 0
            for p in palavras_pergunta:
                max_sim = max([SequenceMatcher(None, p, w).ratio() for w in palavras_linha] or [0])
                score_total += max_sim
            score_total /= len(palavras_pergunta)

            if score_total > melhor_score:
                melhor_score = score_total
                melhor_row = row
                melhor_texto = texto
                # Pegar o primeiro link de imagem da linha
                imgs = re.findall(r'(https?://drive\.google\.com/file/d/[^/\s]+/view)', texto)
                melhor_imagem = imgs[0] if imgs else ""

    if melhor_score < limiar:
        return None, None
    return melhor_texto, melhor_imagem

def exibir_imagem(imagem):
    if not imagem:
        return
    match = re.search(r'/d/([^/]+)', imagem)
    if match:
        file_id = match.group(1)
        url = f"https://drive.google.com/uc?export=view&id={file_id}"
        st.image(url, use_container_width=True)
    else:
        st.image(imagem, use_container_width=True)

# ===== Layout principal =====
col_esq, col_meio, col_dir = st.columns([1,2,1])
with col_meio:
    st.markdown("<h1 class='custom-font'>PlasPrint IA</h1><br>", unsafe_allow_html=True)
    st.markdown("<p class='custom-font'>Qual a sua dúvida?</p>", unsafe_allow_html=True)
    pergunta = st.text_input("", key="central_input", label_visibility="collapsed")

    if "botao_texto" not in st.session_state:
        st.session_state.botao_texto = "Buscar"

    buscar = st.button(st.session_state.botao_texto, use_container_width=True)

    if buscar:
        if not pergunta.strip():
            st.warning("Digite uma pergunta.")
        else:
            st.session_state.botao_texto = "Aguarde"
            with st.spinner("Processando resposta..."):
                dfs = {
                    "erros": erros_df,
                    "trabalhos": trabalhos_df,
                    "dacen": dacen_df,
                    "psi": psi_df,
                    "gerais": gerais_df
                }
                resposta_texto, imagem = buscar_resposta_fuzzy(pergunta, dfs, limiar=0.5)

                if not resposta_texto:
                    st.warning(f'Não encontrei nada relacionado a "{pergunta}" nas planilhas.')
                else:
                    st.subheader("Resposta encontrada")
                    st.markdown(resposta_texto)
                    exibir_imagem(imagem)

            st.session_state.botao_texto = "Buscar"
