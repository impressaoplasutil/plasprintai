import streamlit as st
import pandas as pd
import json, base64, os, re, requests, io
import gspread
from google.oauth2.service_account import Credentials
from google import genai
import yfinance as yf
import datetime
import time

# ===== Configuração da página =====
st.set_page_config(page_title="PlasPrint IA", page_icon="favicon.ico", layout="wide")

# ===== Funções auxiliares =====
def get_usd_brl_rate():
    """
    Retorna a cotação USD/BRL.
    Usa cache local em st.session_state para evitar excesso de requisições.
    Primeiro tenta AwesomeAPI com retry, depois Yahoo Finance.
    """
    # Verifica cache
    if "usd_brl_cache" in st.session_state:
        cached = st.session_state.usd_brl_cache
        if (datetime.datetime.now() - cached["timestamp"]).seconds < 600:
            return cached["rate"]

    rate = None

    # --- Tentativa 1: AwesomeAPI com retry ---
    url = "https://economia.awesomeapi.com.br/json/last/USD-BRL"
    max_retries = 3
    for attempt in range(max_retries):
        try:
            res = requests.get(url, timeout=10)
            if res.status_code == 429:
                # Too Many Requests -> espera exponencial
                time.sleep(2 ** attempt)
                continue
            data = res.json()
            if "USDBRL" in data and "ask" in data["USDBRL"]:
                rate = float(data["USDBRL"]["ask"])
                break
        except:
            # Não exibe aviso na tela
            pass

    # --- Tentativa 2: Yahoo Finance ---
    if rate is None:
        try:
            ticker = yf.Ticker("USDBRL=X")
            hist = ticker.history(period="1d")
            if not hist.empty:
                rate = float(hist["Close"].iloc[-1])
        except:
            pass

    # --- Salva no cache ---
    st.session_state.usd_brl_cache = {
        "rate": rate,
        "timestamp": datetime.datetime.now()
    }

    return rate

def parse_money_str(s):
    s = s.strip()
    if s.startswith('$'):
        s = s[1:]
    s = s.replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except:
        return None

def to_brazilian(n):
    if 0 < n < 0.01:
        n = 0.01
    return f"{n:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def format_dollar_values(text, rate):
    money_regex = re.compile(r'\$\s?\d+(?:[.,]\d+)?')
    found = False

    def repl(m):
        nonlocal found
        found = True
        orig = m.group(0)
        val = parse_money_str(orig)
        if val is None or rate is None:
            return orig
        converted = val * float(rate)
        brl = to_brazilian(converted)
        return f"{orig} (R$ {brl})"

    formatted = money_regex.sub(repl, text)

    if found:
        if not formatted.endswith("\n"):
            formatted += "\n"
        formatted += "(valores sem impostos)"

    return formatted

def process_response(texto):
    padrao_dolar = r"\$\s?\d+(?:[.,]\d+)?"
    if re.search(padrao_dolar, texto):
        rate = get_usd_brl_rate()
        if rate:
            return format_dollar_values(texto, rate)
        else:
            return texto  # Não mostra erro na tela
    return texto

def inject_favicon():
    try:
        with open("favicon.ico", "rb") as f:
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

# ===== Segredos =====
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

# ===== Função para ler abas =====
@st.cache_data
def read_ws(name):
    try:
        ws = sh.worksheet(name)
        return pd.DataFrame(ws.get_all_records())
    except Exception as e:
        st.warning(f"Aba '{name}' não pôde ser carregada: {e}")
        return pd.DataFrame()

def refresh_data():
    st.session_state.erros_df = read_ws("erros")
    st.session_state.trabalhos_df = read_ws("trabalhos")
    st.session_state.dacen_df = read_ws("dacen")
    st.session_state.psi_df = read_ws("psi")
    st.session_state.gerais_df = read_ws("gerais")

if "erros_df" not in st.session_state:
    refresh_data()

# ===== Sidebar =====
st.sidebar.header("Dados carregados")
st.sidebar.write("erros:", len(st.session_state.erros_df))
st.sidebar.write("trabalhos:", len(st.session_state.trabalhos_df))
st.sidebar.write("dacen:", len(st.session_state.dacen_df))
st.sidebar.write("psi:", len(st.session_state.psi_df))
st.sidebar.write("gerais:", len(st.session_state.gerais_df))

if st.sidebar.button("Atualizar planilha"):
    refresh_data()
    st.rerun()

# ===== Cliente Gemini =====
os.environ["GEMINI_API_KEY"] = GEMINI_API_KEY
client = genai.Client()

def build_context(dfs, max_chars=15000):
    parts = []
    for name, df in dfs.items():
        if df.empty:
            continue
        parts.append(f"--- {name} ---")
        for r in df.head(50).to_dict(orient="records"):
            row_items = [f"{k}: {v}" for k,v in r.items() if v is not None and str(v).strip() != '']
            parts.append(" | ".join(row_items))
    context = "\n".join(parts)
    if len(context) > max_chars:
        context = context[:max_chars] + "\n...[CONTEXTO TRUNCADO]"
    return context

# ===== Cache de imagens do Drive =====
@st.cache_data
def load_drive_image(file_id):
    url = f"https://drive.google.com/uc?export=view&id={file_id}"
    res = requests.get(url)
    res.raise_for_status()
    return res.content

def show_drive_images_from_text(text):
    drive_links = re.findall(r'https?://drive\.google\.com/file/d/([a-zA-Z0-9_-]+)[^/]*/view', text)
    for file_id in drive_links:
        try:
            img_bytes = io.BytesIO(load_drive_image(file_id))
            st.image(img_bytes, use_container_width=True)
        except:
            st.warning(f"Não foi possível carregar imagem do Drive: {file_id}")

def remove_drive_links(text):
    return re.sub(r'https?://drive\.google\.com/file/d/[a-zA-Z0-9_-]+/view\?usp=drive_link', '', text)

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
                    "erros": st.session_state.erros_df,
                    "trabalhos": st.session_state.trabalhos_df,
                    "dacen": st.session_state.dacen_df,
                    "psi": st.session_state.psi_df,
                    "gerais": st.session_state.gerais_df
                }
                context = build_context(dfs)
                prompt = f"""
Você é um assistente técnico que responde em português.
Baseie-se **apenas** nos dados abaixo (planilhas). 
Responda de forma objetiva, sem citar de onde veio a informação ou a fonte.
Se houver links de imagens, inclua-os no final.

Dados:
{context}

Pergunta:
{pergunta}

Responda de forma clara, sem citar a aba ou linha da planilha.
"""
                try:
                    resp = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
                    output_fmt = process_response(resp.text)
                    output_fmt = remove_drive_links(output_fmt)
                    st.markdown(f"<div style='text-align:center; margin-top:20px;'>{output_fmt.replace(chr(10),'<br/>')}</div>", unsafe_allow_html=True)
                    show_drive_images_from_text(resp.text)
                except Exception as e:
                    st.error(f"Erro ao chamar Gemini: {e}")
        st.session_state.botao_texto = "Buscar"

# ===== Rodapé e logo =====
st.markdown("""
<style>
.version-tag { position: fixed; bottom: 50px; right: 25px; font-size: 12px; color: white; opacity: 0.7; z-index: 100; }
.logo-footer { position: fixed; bottom: 5px; left: 50%; transform: translateX(-50%); width: 120px; z-index: 100; }
</style>
<div class="version-tag">V1.0</div>
""", unsafe_allow_html=True)

def get_base64_img(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()

img_base64_logo = get_base64_img("logo.png")
st.markdown(f'<img src="data:image/png;base64,{img_base64_logo}" class="logo-footer" />', unsafe_allow_html=True)
