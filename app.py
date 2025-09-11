import unicodedata

def normalize(text):
    # Converte para minúsculo e remove acentos
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
