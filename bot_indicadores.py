import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from anthropic import Anthropic

st.set_page_config(page_title="Análise de Indicadores", page_icon="📊", layout="wide")

client = Anthropic()

# ── Filtros configurados conforme a planilha ─────────────────────────────────
INDICADORES = {
    "Faturamento": {
        "TIPO": "ECONOMICO",
        "GRUPO 01": "FATURAMENTO",
        "TIPO DE META": "R$",
        "filtrar_grupos": True,
    },
    "Qualidade": {
        "TIPO": "QUALIDADE",
        "GRUPO 01": None,
        "TIPO DE META": None,
        "filtrar_grupos": False,
    },
}

FILIAIS_REAIS = ["CANOAS/RS", "CURITIBA/PR", "DUQUE DE CAXIAS/RJ", "VALINHOS/SP"]

# ── Funções de dados ─────────────────────────────────────────────────────────
@st.cache_data
def carregar(arquivo):
    return pd.read_excel(arquivo)


def filtrar(df, indicador, filial):
    cfg = INDICADORES[indicador]
    d = df.copy()
    d = d[d["TIPO"] == cfg["TIPO"]]
    if cfg["GRUPO 01"]:
        d = d[d["GRUPO 01"] == cfg["GRUPO 01"]]
    if cfg["TIPO DE META"]:
        d = d[d["TIPO DE META"] == cfg["TIPO DE META"]]
    if cfg["filtrar_grupos"]:
        d = d[d["GRUPO 02"].isna() & d["GRUPO 03"].isna()]
    if filial == "Geral":
        d = d[d["FILIAL"] == "XX GERAL XX"]
    else:
        d = d[d["FILIAL"] == filial]
    d = d.copy()
    d["REFERÊNCIA"] = pd.to_datetime(d["REFERÊNCIA"])
    d["ANO"] = d["REFERÊNCIA"].dt.year
    d["MÊS"] = d["REFERÊNCIA"].dt.month
    d["MÊS_NOME"] = d["REFERÊNCIA"].dt.strftime("%b/%Y")
    d = d[d["VALOR REF 01"] > 0].sort_values("REFERÊNCIA")
    return d


def tabela_anos(d):
    meses = ["Jan","Fev","Mar","Abr","Mai","Jun","Jul","Ago","Set","Out","Nov","Dez"]
    anos = sorted(d["ANO"].unique())
    rows = []
    for i, mes in enumerate(meses, 1):
        row = {"Mês": mes}
        for ano in anos:
            v = d[(d["ANO"] == ano) & (d["MÊS"] == i)]["VALOR REF 01"]
            row[str(ano)] = v.values[0] if len(v) > 0 else None
        rows.append(row)
    total = {"Mês": "TOTAL"}
    for ano in anos:
        total[str(ano)] = d[d["ANO"] == ano]["VALOR REF 01"].sum() or None
    rows.append(total)
    return pd.DataFrame(rows), anos


def tabela_atingimento(d):
    meses = ["Jan","Fev","Mar","Abr","Mai","Jun","Jul","Ago","Set","Out","Nov","Dez"]
    anos = sorted(d["ANO"].unique())
    rows = []
    for i, mes in enumerate(meses, 1):
        row = {"Mês": mes}
        for ano in anos:
            sub = d[(d["ANO"] == ano) & (d["MÊS"] == i)]
            if len(sub) > 0 and sub["META"].values[0] > 0:
                row[str(ano)] = sub["RESULT 01"].values[0]
            else:
                row[str(ano)] = None
        rows.append(row)
    return pd.DataFrame(rows), anos


def calcular_mom(d):
    d = d.copy().reset_index(drop=True)
    d["MoM_%"] = d["VALOR REF 01"].pct_change() * 100
    return d[["MÊS_NOME", "META", "VALOR REF 01", "RESULT 01", "MoM_%"]].rename(
        columns={"MÊS_NOME": "Mês", "VALOR REF 01": "Realizado", "RESULT 01": "Ating."}
    )


def calcular_yoy(d):
    anos = sorted(d["ANO"].unique())
    rows = []
    for ano in anos:
        sub = d[d["ANO"] == ano]
        total = sub["VALOR REF 01"].sum()
        meta = sub["META"].sum()
        rows.append({
            "Ano": ano,
            "Realizado": total,
            "Meta": meta,
            "Atingimento": total / meta if meta > 0 else None,
            "Meses c/ dado": len(sub),
        })
    df_yoy = pd.DataFrame(rows)
    df_yoy["YoY_%"] = df_yoy["Realizado"].pct_change() * 100
    return df_yoy


def resumo_para_ia(d, indicador, filial, pergunta):
    mom = calcular_mom(d).tail(12).to_string(index=False)
    yoy = calcular_yoy(d).to_string(index=False)
    return f"""Você é um analista financeiro experiente. Analise os dados abaixo e responda em português brasileiro de forma clara e objetiva.

Indicador: {indicador} | Filial: {filial}
Pergunta/solicitação: {pergunta}

=== YoY (ano a ano) ===
{yoy}

=== MoM (últimos 12 meses) ===
{mom}

Estruture sua resposta com:
1. Resumo executivo (3-4 frases diretas)
2. Pontos de atenção
3. Tendências identificadas
4. Sugestões práticas de melhoria

Use R$ e % nos números. Seja direto e prático."""


# ── Interface ─────────────────────────────────────────────────────────────────
st.title("📊 Análise de Indicadores")

with st.sidebar:
    st.header("⚙️ Filtros")
    arquivo = st.file_uploader("Upload da planilha (.xlsx)", type=["xlsx"])
    st.divider()
    indicador = st.selectbox("Indicador", list(INDICADORES.keys()))
    filial = st.selectbox("Filial", ["Geral"] + FILIAIS_REAIS)
    st.divider()
    st.caption("v1.0 — Bot Indicadores")

if not arquivo:
    st.info("👈 Faça upload da planilha na barra lateral para começar.")
    st.stop()

df_raw = carregar(arquivo)
df = filtrar(df_raw, indicador, filial)

if df.empty:
    st.warning("Nenhum dado encontrado para os filtros selecionados.")
    st.stop()

tab1, tab2, tab3, tab4 = st.tabs(["📅 Por Ano", "📈 MoM", "🔁 YoY", "🤖 Insights IA"])

# ── Tab 1: Por ano ────────────────────────────────────────────────────────────
with tab1:
    st.subheader(f"{indicador} — {filial} · Realizado mensal por ano")
    df_tab, anos = tabela_anos(df)

    def fmt_val(v):
        if v is None or pd.isna(v):
            return ""
        return f"R$ {v:,.0f}".replace(",", ".")

    st.dataframe(
        df_tab.style.format({str(a): fmt_val for a in anos}),
        use_container_width=True, hide_index=True
    )

    st.divider()
    st.subheader("Atingimento de Meta por mês")

    def highlight_at(v):
        if v is None or pd.isna(v):
            return ""
        if v >= 1.0:
            return "color: #1D9E75; font-weight: bold"
        elif v >= 0.85:
            return "color: #BA7517"
        return "color: #E24B4A"

    df_at, _ = tabela_atingimento(df)
    st.dataframe(
        df_at.style
             .format({str(a): (lambda v: f"{v:.0%}" if v and not pd.isna(v) else "") for a in anos})
             .map(highlight_at, subset=[str(a) for a in anos]),
        use_container_width=True, hide_index=True
    )

    st.divider()
    df_yoy_c = calcular_yoy(df)
    fig = go.Figure()
    fig.add_bar(
        x=df_yoy_c["Ano"].astype(str), y=df_yoy_c["Realizado"], name="Realizado",
        marker_color="#1D9E75",
        text=df_yoy_c["Realizado"].apply(lambda v: f"R$ {v/1e6:.1f}M"),
        textposition="outside"
    )
    fig.add_bar(
        x=df_yoy_c["Ano"].astype(str), y=df_yoy_c["Meta"], name="Meta",
        marker_color="rgba(127,119,221,0.4)"
    )
    fig.update_layout(barmode="group", height=360, margin=dict(t=30, b=20),
                      legend=dict(orientation="h", y=-0.15))
    st.plotly_chart(fig, use_container_width=True)

# ── Tab 2: MoM ───────────────────────────────────────────────────────────────
with tab2:
    st.subheader(f"{indicador} — {filial} · Variação Mês a Mês")
    df_mom = calcular_mom(df)

    def hl_mom(v):
        if pd.isna(v):
            return ""
        return "color: #1D9E75; font-weight:bold" if v > 0 else "color: #E24B4A; font-weight:bold"

    def hl_at(v):
        if pd.isna(v):
            return ""
        if v >= 1.0: return "color: #1D9E75; font-weight:bold"
        if v >= 0.85: return "color: #BA7517"
        return "color: #E24B4A"

    st.dataframe(
        df_mom.style
              .format({
                  "META": "R$ {:,.0f}",
                  "Realizado": "R$ {:,.0f}",
                  "Ating.": "{:.0%}",
                  "MoM_%": lambda v: f"{v:+.1f}%" if pd.notna(v) else "—",
              })
              .map(hl_mom, subset=["MoM_%"])
              .map(hl_at, subset=["Ating."]),
        use_container_width=True, hide_index=True
    )

    ultimos = df.tail(16)
    real = ultimos["VALOR REF 01"].tolist()
    meta_l = ultimos["META"].tolist()
    cores = ["#1D9E75" if r >= m else "#E24B4A" for r, m in zip(real, meta_l)]

    fig2 = go.Figure()
    fig2.add_bar(x=ultimos["MÊS_NOME"].tolist(), y=real, name="Realizado",
                 marker_color=cores, marker_cornerradius=4)
    fig2.add_scatter(x=ultimos["MÊS_NOME"].tolist(), y=meta_l, name="Meta",
                     mode="lines", line=dict(color="#7F77DD", width=2, dash="dot"))
    fig2.update_layout(height=360, margin=dict(t=20, b=20),
                       legend=dict(orientation="h", y=-0.2), xaxis_tickangle=-45)
    st.plotly_chart(fig2, use_container_width=True)

# ── Tab 3: YoY ───────────────────────────────────────────────────────────────
with tab3:
    st.subheader(f"{indicador} — {filial} · Comparativo Ano a Ano")
    df_yoy = calcular_yoy(df)

    def hl_yoy(v):
        if pd.isna(v): return ""
        return "color: #1D9E75; font-weight:bold" if v > 0 else "color: #E24B4A; font-weight:bold"

    st.dataframe(
        df_yoy.style
              .format({
                  "Realizado": "R$ {:,.0f}",
                  "Meta": "R$ {:,.0f}",
                  "Atingimento": "{:.1%}",
                  "YoY_%": lambda v: f"{v:+.1f}%" if pd.notna(v) else "—",
                  "Meses c/ dado": "{:.0f}",
              })
              .map(hl_yoy, subset=["YoY_%"]),
        use_container_width=True, hide_index=True
    )

    if filial == "Geral":
        st.divider()
        st.subheader("YoY Jan–Abr por filial")
        rows_f = []
        for f in FILIAIS_REAIS:
            df_f = filtrar(df_raw, indicador, f)
            for ano in sorted(df_f["ANO"].unique()):
                sub = df_f[(df_f["ANO"] == ano) & (df_f["MÊS"] <= 4)]
                rows_f.append({"Filial": f, "Ano": ano, "Jan–Abr": sub["VALOR REF 01"].sum()})
        pivot = pd.DataFrame(rows_f).pivot(index="Filial", columns="Ano", values="Jan–Abr")
        pivot.columns = [str(c) for c in pivot.columns]
        st.dataframe(pivot.style.format("R$ {:,.0f}"), use_container_width=True)

# ── Tab 4: Insights IA ────────────────────────────────────────────────────────
with tab4:
    st.subheader("🤖 Pergunte ao assistente")
    st.caption("Faça qualquer pergunta sobre os dados filtrados. Ex: 'Analise o MoM e sugira melhorias'")

    if "chat" not in st.session_state:
        st.session_state.chat = []

    for msg in st.session_state.chat:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    pergunta = st.chat_input("Digite sua pergunta...")

    if pergunta:
        st.session_state.chat.append({"role": "user", "content": pergunta})
        with st.chat_message("user"):
            st.markdown(pergunta)

        with st.chat_message("assistant"):
            with st.spinner("Analisando os dados..."):
                prompt = resumo_para_ia(df, indicador, filial, pergunta)
                resp = client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=1500,
                    messages=[{"role": "user", "content": prompt}]
                )
                texto = resp.content[0].text
                st.markdown(texto)

        st.session_state.chat.append({"role": "assistant", "content": texto})
