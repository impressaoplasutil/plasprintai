import streamlit as st
import pandas as pd
import json, base64, os, re, requests, io, time, unicodedata
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

def format_dollar_values(text, rate):
    if "$" not in text or rate is None:
        return text
    money_regex = re.compile(r'\$\d+(?:[.,]\d{3})*(?:[.,]\d+)?')
    def parse_money_str(s):
        s = s.strip()
        if s.startswith('$'):
            s = s[1:]
        s = s.replace(" ", "")
        if '.' in s and ',' in s:
            if s.rfind(',') > s.rfind('.'):
                dec, thou = ',', '.'
            else:
                dec, thou = '.', ','
            s_clean = s.replace(thou, '').replace(dec, '.')
        elif ',' in s:
            last = s.rsplit(',', 1)[-1]
            if 1 <= len(last) <= 2:
                s_clean = s.replace('.', '').replace(',', '.')
            else:
                s_clean = s.replace(',', '')
        else:
            s_clean = s.replace('.', '')
        try:
            return float(s_clean)
        except:
            return None
    def to_brazilian(n):
        s = f"{n:,.2f}"
        s = s.replace(",", "X").replace(".", ",").replace("X", ".")
        return s
    def repl(m):
        orig = m.group(0)
        val = parse_money_str(orig)
        if val is None:
            return orig
        converted = val * rate
        brl = to_brazilian(converted)
        return f"{orig} (R$ {brl})"
    formatted = money_regex.sub(repl, text)
    if not formatted.endswith("\n"):
        formatted += "\n"
    formatted += "(valores sem impostos)"
    return formatted

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

def get_base64_font(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()

# ===== Carregar background e fonte =====
background_image = "background.jpg"
img_base64 = get_base64_of_jpg(background_image)
font_base64 = get_base64_font("font.ttf")

st.markdown(f"""
<style>
@font-face {{
    font-family: 'CustomFont';
    src: url(data:font/ttf;base64,{font_base64}) format('truetype');
}}
h1.custom-font {{
    font-family: 'CustomFont', sans-serif !important;
    text-align: center;
    font-size: 380%;
}}
p.custom-font {{
    font-family: 'CustomFont', sans-serif !important;
    font-weight: bold;
    text-align: left;
}}
div.stButton > button {{
    font-family: 'CustomFont', sans-serif !important;
}}
div.stTextInput > div > input {{
    font-family: 'CustomFont', sans-serif !important;
}}
.stApp {{
    background-image: url("data:image/jpg;base64,{img_base64}");
    background-size: cover;
    background-position: center;
    background-repeat: no-repeat;
    background-attachment: fixed;
}}
</style>
""", unsafe_allow_html=True)

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

# ===== Busca fuzzy palavra a palavra =====
def buscar_resposta_fuzzy(pergunta, dfs, limiar=0.6):
    palavras_pergunta = pergunta.lower().split()
    melhor_row = None
    melhor_score = 0
    for df in dfs.values():
        for _, row in df.iterrows():
            texto = " ".join([str(val) for val in row.values if isinstance(val, str)]).lower()
            palavras_linha = texto.split()
            score_total = 0
            for p in palavras_pergunta:
                max_sim = 0
                for w in palavras_linha:
                    s = SequenceMatcher(None, p, w).ratio()
                    if s > max_sim:
                        max_sim = s
                score_total += max_sim
            score_total /= len(palavras_pergunta)
            if score_total > melhor_score:
                melhor_score = score_total
                melhor_row = row
    if melhor_score < limiar:
        return None, None
    resposta = melhor_row.get("Resposta") or melhor_row.get("Coluna de resposta") or "Resposta não encontrada"
    imagem = melhor_row.get("Imagem") or melhor_row.get("Coluna de imagem") or ""
    return resposta, imagem

def exibir_imagem(imagem):
    if not imagem:
        return
    # Extrair file_id do link do Google Drive
    match = re.search(r'/d/([^/]+)', imagem)
    if match:
        file_id = match.group(1)
        # URL direto para visualização
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
                resposta_texto, imagem = buscar_resposta_fuzzy(pergunta, dfs, limiar=0.6)

                if not resposta_texto:
                    st.warning(f'Não encontrei nada relacionado a "{pergunta}" nas planilhas.')
                else:
                    st.subheader("Resposta encontrada")
                    st.markdown(resposta_texto)
                    exibir_imagem(imagem)

            st.session_state.botao_texto = "Buscar"
