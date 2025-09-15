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

# ===== Busca inteligente =====
def similar(a, b):
    return SequenceMatcher(None, a, b).ratio()

def search_relevant_rows(dfs, query, max_per_sheet=200, limiar=0.75):
    palavras = query.lower().split()
    results = {}
    for name, df in dfs.items():
        def contem_palavra(row):
            texto = " ".join(map(str, row.values)).lower()
            for p in palavras:
                for palavra_linha in texto.split():
                    if similar(p, palavra_linha) >= limiar:
                        return True
            return False
        mask = df.apply(contem_palavra, axis=1)
        filtrado = df[mask]
        if not filtrado.empty:
            results[name] = filtrado.head(max_per_sheet)
    return results

def build_context(dfs, max_chars=15000):
    parts = []
    for name, df in dfs.items():
        if df.empty:
            continue
        parts.append(f"--- {name} ---")
        for r in df.to_dict(orient="records"):
            row_items = [f"{k}: {v}" for k, v in r.items() if str(v).strip() not in ["", "None", "nan"]]
            parts.append(" | ".join(row_items))
    context = "\n".join(parts)
    if len(context) > max_chars:
        context = context[:max_chars] + "\n...[CONTEXTO TRUNCADO]"
    return context

@st.cache_data
def load_drive_image(file_id):
    url = f"https://drive.google.com/uc?export=view&id={file_id}"
    res = requests.get(url)
    res.raise_for_status()
    return res.content

def show_drive_images_from_dfs(dfs):
    for df in dfs.values():
        for row in df.to_dict(orient="records"):
            for v in row.values():
                if isinstance(v, str):
                    drive_links = re.findall(
                        r'https?://drive\.google\.com/file/d/([a-zA-Z0-9_-]+)/view', v
                    )
                    for file_id in drive_links:
                        try:
                            img_bytes = io.BytesIO(load_drive_image(file_id))
                            st.image(img_bytes, use_container_width=True)
                        except:
                            st.warning(f"Não foi possível carregar a imagem do Drive: {file_id}")

def remove_drive_links(text):
    return re.sub(r'https?://drive\.google\.com/file/d/[a-zA-Z0-9_-]+/view\?usp=drive_link', '', text)

# ===== Layout principal =====
col_esq, col_meio, col_dir = st.columns([1, 2, 1])
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
                rate = get_usd_brl_rate()
                if rate is None:
                    st.error("Não foi possível obter a cotação do dólar.")
                else:
                    dfs = {
                        "erros": erros_df,
                        "trabalhos": trabalhos_df,
                        "dacen": dacen_df,
                        "psi": psi_df,
                        "gerais": gerais_df
                    }
                    filtered_dfs = search_relevant_rows(dfs, pergunta, max_per_sheet=200)

                    with st.sidebar.expander("Linhas enviadas ao Gemini", expanded=False):
                        for name, df_env in filtered_dfs.items():
                            st.write(f"{name}: {len(df_env)}")

                    if not filtered_dfs:
                        st.warning(f'Não encontrei nada relacionado a "{pergunta}" nas planilhas.')
                    else:
                        context = build_context(filtered_dfs)

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

                        def gerar_resposta_gemini(prompt, tentativas=3, intervalo=5):
                            for tentativa in range(tentativas):
                                try:
                                    response = client.models.generate_content(
                                        model="gemini-2.5-flash",
                                        contents=prompt
                                    )
                                    return response.text
                                except Exception as e:
                                    if "503" in str(e) and tentativa < tentativas - 1:
                                        time.sleep(intervalo)
                                    else:
                                        raise e

                        try:
                            with st.spinner("O sistema pode estar sobrecarregado, tentando gerar resposta..."):
                                resposta_texto = gerar_resposta_gemini(prompt)

                            output_fmt = format_dollar_values(resposta_texto, rate)
                            output_fmt = remove_drive_links(output_fmt)

                            st.markdown(
                                f"<div style='text-align:center; margin-top:20px;'>{output_fmt.replace(chr(10),'<br/>')}</div>",
                                unsafe_allow_html=True,
                            )

                            show_drive_images_from_dfs(filtered_dfs)

                        except Exception as e:
                            st.error(f"Ocorreu um erro ao tentar obter a resposta do Gemini: {e}")
            st.session_state.botao_texto = "Buscar"

# ===== Rodapé e logo =====
st.markdown(
    """
<style>
.version-tag { position: fixed; bottom: 50px; right: 25px; font-size: 12px; color: white; opacity: 0.7; z-index: 100; }
.logo-footer { position: fixed; bottom: 5px; left: 50%; transform: translateX(-50%); width: 120px; z-index: 100; }
</style>
<div class="version-tag">U_V1.0</div>
""",
    unsafe_allow_html=True,
)

def get_base64_img(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()

img_base64_logo = get_base64_img("logo.png")
st.markdown(
    f'<img src="data:image/png;base64,{img_base64_logo}" class="logo-footer" />',
    unsafe_allow_html=True,
)
