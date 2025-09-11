import streamlit as st
import pandas as pd
import json, base64, os, re, requests, io
import gspread
from google.oauth2.service_account import Credentials
from google import genai
import unicodedata

# ===== Configuração da página =====
st.set_page_config(page_title="PlasPrint IA", page_icon="favicon.ico", layout="wide")

# ===== Funções auxiliares =====

def normalize(text):
    if not isinstance(text, str):
        text = str(text)
    text = text.lower()
    text = ''.join(
        c for c in unicodedata.normalize('NFD', text)
        if unicodedata.category(c) != 'Mn'
    )
    return text

def filter_rows_by_question(dfs, question):
    question_norm = normalize(question)
    filtered = {}
    for name, df in dfs.items():
        if df.empty:
            continue
        mask = df.apply(
            lambda row: row.astype(str)
                            .map(normalize)
                            .str.contains(question_norm)
                            .any(),
            axis=1
        )
        result = df[mask]
        if not result.empty:
            filtered[name] = result
    return filtered

def load_drive_image(file_id):
    url = f"https://drive.google.com/uc?export=download&id={file_id}"
    response = requests.get(url)
    response.raise_for_status()
    return response.content

def show_drive_images_from_sheets(dfs):
    for name, df in dfs.items():
        for col in df.columns:
            for val in df[col].dropna():
                if isinstance(val, str) and "drive.google.com" in val:
                    file_id = re.findall(r"/d/([a-zA-Z0-9_-]+)", val)
                    if file_id:
                        try:
                            img_bytes = io.BytesIO(load_drive_image(file_id[0]))
                            st.image(img_bytes, use_container_width=True, caption=f"{name} → {col}")
                        except:
                            st.warning(f"Não consegui abrir a imagem do Drive na aba {name}.")

def show_drive_images_from_text(text):
    urls = re.findall(r"https://drive\.google\.com/[^\s)]+", text)
    for url in urls:
        file_id = re.findall(r"/d/([a-zA-Z0-9_-]+)", url)
        if file_id:
            try:
                img_bytes = io.BytesIO(load_drive_image(file_id[0]))
                st.image(img_bytes, use_container_width=True)
            except:
                st.warning("Não consegui abrir uma das imagens do Drive citadas no texto.")

# ===== Conexão com Google Sheets =====

def load_sheets_as_dfs():
    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    creds_dict = json.loads(st.secrets["google_service_account"])
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)

    sheets_url = st.secrets["sheets_url"]
    spreadsheet = client.open_by_url(sheets_url)

    dfs = {}
    for sheet in spreadsheet.worksheets():
        data = sheet.get_all_values()
        if not data:
            continue
        headers = data[0]
        rows = data[1:]
        dfs[sheet.title] = pd.DataFrame(rows, columns=headers)
    return dfs

# ===== Consulta Gemini =====

def ask_gemini(pergunta, dfs):
    client = genai.Client(api_key=st.secrets["gemini_api_key"])

    # Concatena os dados filtrados como contexto
    context_parts = []
    for name, df in dfs.items():
        context_parts.append(f"Planilha: {name}")
        context_parts.append(df.to_csv(index=False))
    context_text = "\n\n".join(context_parts)

    prompt = f"""
Você é um assistente especialista em informações internas.
Responda à pergunta do usuário com base apenas nos dados abaixo.

### Dados das planilhas:
{context_text}

### Pergunta do usuário:
{pergunta}

Responda de forma direta, sem inventar nada fora dos dados.
Se não houver nada relacionado, diga: "Não encontrei nada relacionado a '{pergunta}' nas planilhas."
"""

    resp = client.models.generate_content(
        model="gemini-1.5-flash",
        contents=prompt
    )

    return resp

# ===== App =====

st.title("PlasPrint IA")

pergunta = st.text_input("Faça uma pergunta sobre as planilhas:")

if pergunta:
    with st.spinner("Buscando informações..."):
        dfs = load_sheets_as_dfs()
        filtered_dfs = filter_rows_by_question(dfs, pergunta)

        if not filtered_dfs:
            st.write(f"Não encontrei nada relacionado a \"{pergunta}\" nas planilhas.")
        else:
            resp = ask_gemini(pergunta, filtered_dfs)
            st.write(resp.text)

            # Mostrar imagens das linhas filtradas e do texto da resposta
            show_drive_images_from_text(resp.text)
            show_drive_images_from_sheets(filtered_dfs)
