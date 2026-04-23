import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from anthropic import Anthropic
from fpdf import FPDF
from datetime import datetime
import os

st.set_page_config(page_title="Análise de Indicadores", page_icon="📊", layout="wide")

client = Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])

INDICADORES = {
    "Faturamento": {
        "TIPO": "ECONOMICO",
        "GRUPO 01": "FATURAMENTO",
        "TIPO DE META": "R$",
        "filtrar_grupos": True,
    },
}

FILIAIS_REAIS = ["CANOAS/RS", "CURITIBA/PR", "DUQUE DE CAXIAS/RJ", "VALINHOS/SP"]


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
        d = d[d["FILIAL"].isin(FILIAIS_REAIS)]
    else:
        d = d[d["FILIAL"] == filial]

    d = d.copy()
    d["REFERÊNCIA"] = pd.to_datetime(d["REFERÊNCIA"])
    d["ANO"] = d["REFERÊNCIA"].dt.year
    d["MÊS"] = d["REFERÊNCIA"].dt.month
    d["MÊS_ORDEM"] = d["ANO"] * 100 + d["MÊS"]

    mapa_meses = {
        1: "Jan", 2: "Fev", 3: "Mar", 4: "Abr", 5: "Mai", 6: "Jun",
        7: "Jul", 8: "Ago", 9: "Set", 10: "Out", 11: "Nov", 12: "Dez"
    }

    d["MÊS_NOME"] = d["MÊS"].map(mapa_meses) + "/" + d["ANO"].astype(str)
    d = d[d["VALOR REF 01"] > 0].sort_values(["ANO", "MÊS", "FILIAL"]).reset_index(drop=True)

    return d


def tabela_completa_ano(d, ano):
    meses = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]
    rows = []

    base_ano = d[d["ANO"] == ano].copy()
    mensal = (
        base_ano.groupby("MÊS", as_index=False)
        .agg({
            "VALOR REF 01": "sum",
            "META": "sum"
        })
        .sort_values("MÊS")
    )

    for i, mes in enumerate(meses, 1):
        sub = mensal[mensal["MÊS"] == i]
        if len(sub) > 0:
            real = sub["VALOR REF 01"].values[0]
            meta = sub["META"].values[0]
            gap = real - meta
            ating = real / meta if meta > 0 else None
            rows.append({
                "Mês": mes,
                "Realizado": real,
                "Meta": meta,
                "Gap (R$)": gap,
                "Atingimento": ating
            })
        else:
            rows.append({
                "Mês": mes,
                "Realizado": None,
                "Meta": None,
                "Gap (R$)": None,
                "Atingimento": None
            })

    total_real = base_ano["VALOR REF 01"].sum()
    total_meta = base_ano["META"].sum()

    rows.append({
        "Mês": "TOTAL",
        "Realizado": total_real,
        "Meta": total_meta,
        "Gap (R$)": total_real - total_meta,
        "Atingimento": total_real / total_meta if total_meta > 0 else None,
    })

    return pd.DataFrame(rows)


def calcular_mom(d):
    base = (
        d.groupby(["ANO", "MÊS", "MÊS_NOME", "MÊS_ORDEM"], as_index=False)
        .agg({
            "META": "sum",
            "VALOR REF 01": "sum"
        })
        .sort_values("MÊS_ORDEM")
        .reset_index(drop=True)
    )

    base["Ating."] = base["VALOR REF 01"] / base["META"]
    base["MoM_%"] = base["VALOR REF 01"].pct_change() * 100

    return base.rename(columns={
        "MÊS_NOME": "Mês",
        "VALOR REF 01": "Realizado"
    })[["ANO", "MÊS", "Mês", "META", "Realizado", "Ating.", "MoM_%", "MÊS_ORDEM"]]


def calcular_yoy(d):
    df_yoy = (
        d.groupby("ANO", as_index=False)
        .agg({
            "VALOR REF 01": "sum",
            "META": "sum",
            "MÊS": "nunique"
        })
        .sort_values("ANO")
        .rename(columns={
            "VALOR REF 01": "Realizado",
            "META": "Meta",
            "MÊS": "Meses c/ dado"
        })
    )

    df_yoy["Atingimento"] = df_yoy["Realizado"] / df_yoy["Meta"]
    df_yoy["YoY_%"] = df_yoy["Realizado"].pct_change() * 100

    return df_yoy


def comparativo_filiais(d, ano):
    comp = (
        d[d["ANO"] == ano]
        .groupby("FILIAL", as_index=False)
        .agg({
            "VALOR REF 01": "sum",
            "META": "sum"
        })
        .rename(columns={
            "VALOR REF 01": "Realizado",
            "META": "Meta"
        })
        .sort_values("Realizado", ascending=False)
    )

    comp["Gap"] = comp["Realizado"] - comp["Meta"]
    comp["Atingimento"] = comp["Realizado"] / comp["Meta"]
    return comp


def fmt_brl(v):
    if pd.isna(v) or v is None:
        return "-"
    return f"R$ {v:,.0f}".replace(",", ".")


def fmt_pct(v):
    if pd.isna(v) or v is None:
        return "-"
    return f"{v:.1%}"


class PDFRelatorio(FPDF):
    def __init__(self, indicador, filial):
        super().__init__()
        self.indicador = indicador
        self.filial = filial

    def header(self):
        if os.path.exists("logo.png"):
            try:
                self.image("logo.png", 10, 8, 33)
            except Exception:
                pass

        self.set_font("Helvetica", "B", 14)
        self.cell(0, 10, "Relatório de Análise de Indicadores", align="C", new_x="LMARGIN", new_y="NEXT")

        self.set_font("Helvetica", "", 10)
        self.cell(0, 6, f"{self.indicador} | Base do PDF: Todas as unidades", align="C", new_x="LMARGIN", new_y="NEXT")
        self.cell(0, 6, f"Gerado em: {datetime.now().strftime('%d/%m/%Y %H:%M')}", align="C", new_x="LMARGIN", new_y="NEXT")

        self.ln(4)
        self.set_draw_color(200, 200, 200)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(4)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(128, 128, 128)
        self.cell(0, 10, f"Página {self.page_no()}", align="C")

    def secao(self, titulo):
        self.set_font("Helvetica", "B", 12)
        self.set_text_color(0, 0, 0)
        self.ln(4)
        self.cell(0, 8, titulo, new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def tabela_por_ano(self, df_completa):
        self.set_font("Helvetica", "B", 10)
        self.set_fill_color(240, 240, 240)

        headers = ["Mês", "Realizado", "Meta", "Gap (R$)", "Atingimento"]
        widths = [30, 40, 40, 40, 35]

        for h, w in zip(headers, widths):
            self.cell(w, 8, h, border=1, fill=True, align="C")
        self.ln()

        self.set_font("Helvetica", "", 9)
        for _, row in df_completa.iterrows():
            is_total = row["Mês"] == "TOTAL"

            if is_total:
                self.set_font("Helvetica", "B", 9)

            self.cell(widths[0], 7, str(row["Mês"]), border=1, align="C")
            self.cell(widths[1], 7, fmt_brl(row["Realizado"]), border=1, align="R")
            self.cell(widths[2], 7, fmt_brl(row["Meta"]), border=1, align="R")
            self.cell(widths[3], 7, fmt_brl(row["Gap (R$)"]), border=1, align="R")
            self.cell(widths[4], 7, fmt_pct(row["Atingimento"]), border=1, align="R")
            self.ln()

            if is_total:
                self.set_font("Helvetica", "", 9)

    def tabela_mom(self, df_mom):
        self.set_font("Helvetica", "B", 10)
        self.set_fill_color(240, 240, 240)

        headers = ["Mês", "Meta", "Realizado", "Ating.", "MoM %"]
        widths = [35, 40, 40, 30, 30]

        for h, w in zip(headers, widths):
            self.cell(w, 8, h, border=1, fill=True, align="C")
        self.ln()

        self.set_font("Helvetica", "", 9)
        for _, row in df_mom.iterrows():
            self.cell(widths[0], 7, str(row["Mês"]), border=1)
            self.cell(widths[1], 7, fmt_brl(row["META"]), border=1, align="R")
            self.cell(widths[2], 7, fmt_brl(row["Realizado"]), border=1, align="R")
            self.cell(widths[3], 7, fmt_pct(row["Ating."]), border=1, align="R")
            mom_val = row["MoM_%"]
            mom_str = f"{mom_val:+.1f}%" if pd.notna(mom_val) else "-"
            self.cell(widths[4], 7, mom_str, border=1, align="R")
            self.ln()

    def tabela_yoy(self, df_yoy):
        self.set_font("Helvetica", "B", 10)
        self.set_fill_color(240, 240, 240)

        headers = ["Ano", "Realizado", "Meta", "Atingimento", "Meses", "YoY %"]
        widths = [20, 40, 40, 35, 20, 30]

        for h, w in zip(headers, widths):
            self.cell(w, 8, h, border=1, fill=True, align="C")
        self.ln()

        self.set_font("Helvetica", "", 9)
        for _, row in df_yoy.iterrows():
            self.cell(widths[0], 7, str(int(row["ANO"])), border=1, align="C")
            self.cell(widths[1], 7, fmt_brl(row["Realizado"]), border=1, align="R")
            self.cell(widths[2], 7, fmt_brl(row["Meta"]), border=1, align="R")
            self.cell(widths[3], 7, fmt_pct(row["Atingimento"]), border=1, align="R")
            self.cell(widths[4], 7, str(int(row["Meses c/ dado"])), border=1, align="C")
            yoy_val = row["YoY_%"]
            yoy_str = f"{yoy_val:+.1f}%" if pd.notna(yoy_val) else "-"
            self.cell(widths[5], 7, yoy_str, border=1, align="R")
            self.ln()

    def tabela_filiais(self, df_filiais):
        self.set_font("Helvetica", "B", 10)
        self.set_fill_color(240, 240, 240)

        headers = ["Filial", "Realizado", "Meta", "Gap", "Atingimento"]
        widths = [55, 35, 35, 30, 30]

        for h, w in zip(headers, widths):
            self.cell(w, 8, h, border=1, fill=True, align="C")
        self.ln()

        self.set_font("Helvetica", "", 9)
        for _, row in df_filiais.iterrows():
            self.cell(widths[0], 7, str(row["FILIAL"]), border=1)
            self.cell(widths[1], 7, fmt_brl(row["Realizado"]), border=1, align="R")
            self.cell(widths[2], 7, fmt_brl(row["Meta"]), border=1, align="R")
            self.cell(widths[3], 7, fmt_brl(row["Gap"]), border=1, align="R")
            self.cell(widths[4], 7, fmt_pct(row["Atingimento"]), border=1, align="R")
            self.ln()


def gerar_pdf(df_todas_unidades, indicador, ano_selecionado):
    pdf = PDFRelatorio(indicador, "Todas as unidades")

    pdf.add_page()
    pdf.secao(f"1. Análise por Mês - Ano {ano_selecionado}")
    df_completa = tabela_completa_ano(df_todas_unidades, ano_selecionado)
    pdf.tabela_por_ano(df_completa)

    pdf.add_page()
    pdf.secao(f"2. Variação Mês a Mês (MoM) - Ano {ano_selecionado}")
    df_mom = calcular_mom(df_todas_unidades)
    df_mom_pdf = df_mom[df_mom["ANO"] == ano_selecionado].sort_values("MÊS")
    pdf.tabela_mom(df_mom_pdf)

    pdf.add_page()
    pdf.secao("3. Comparativo Ano a Ano (YoY)")
    df_yoy = calcular_yoy(df_todas_unidades)
    pdf.tabela_yoy(df_yoy)

    pdf.add_page()
    pdf.secao(f"4. Comparativo entre Filiais - {ano_selecionado}")
    df_filiais = comparativo_filiais(df_todas_unidades, ano_selecionado)
    pdf.tabela_filiais(df_filiais)

    return bytes(pdf.output())


def resumo_para_ia(d, indicador, filial, pergunta):
    mom = calcular_mom(d).tail(12).to_string(index=False)
    yoy = calcular_yoy(d).to_string(index=False)

    return f"""Você é um analista financeiro experiente. Analise os dados abaixo e responda em português brasileiro de forma clara e objetiva.

Indicador: {indicador} | Filial em tela: {filial}
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


st.title("📊 Análise de Indicadores")

with st.sidebar:
    st.header("⚙️ Filtros")
    arquivo = st.file_uploader("Upload da planilha (.xlsx)", type=["xlsx"])
    st.divider()
    indicador = st.selectbox("Indicador", list(INDICADORES.keys()))
    filial = st.selectbox("Filial", ["Geral"] + FILIAIS_REAIS)
    st.divider()
    st.caption("v2.0 — Bot Indicadores")

if not arquivo:
    st.info("👈 Faça upload da planilha na barra lateral para começar.")
    st.stop()

df_raw = carregar(arquivo)
df = filtrar(df_raw, indicador, filial)
df_todas_unidades = filtrar(df_raw, indicador, "Geral")

if df.empty:
    st.warning("Nenhum dado encontrado para os filtros selecionados.")
    st.stop()

tab0, tab1, tab2, tab3, tab4 = st.tabs([
    "📊 Dashboard Executivo",
    "📅 Por Ano",
    "📈 MoM",
    "🔁 YoY",
    "🤖 Insights IA"
])

with tab0:
    st.subheader(f"Dashboard Executivo — {indicador} | {filial}")

    anos = sorted(df["ANO"].unique())
    ano_kpi = anos[-1]

    base_kpi = df[df["ANO"] == ano_kpi].copy()

    realizado_total = base_kpi["VALOR REF 01"].sum()
    meta_total = base_kpi["META"].sum()
    gap_total = realizado_total - meta_total
    ating_total = realizado_total / meta_total if meta_total > 0 else None

    if len(anos) >= 2:
        ano_anterior = anos[-2]
        realizado_ano_anterior = df[df["ANO"] == ano_anterior]["VALOR REF 01"].sum()
        yoy_total = ((realizado_total / realizado_ano_anterior) - 1) if realizado_ano_anterior > 0 else None
    else:
        yoy_total = None

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Realizado", fmt_brl(realizado_total))
    col2.metric("Meta", fmt_brl(meta_total))
    col3.metric("Gap", fmt_brl(gap_total))
    col4.metric("Atingimento", fmt_pct(ating_total))
    col5.metric("YoY", fmt_pct(yoy_total) if yoy_total is not None else "-")

    st.divider()

    resumo_mensal_exec = (
        base_kpi.groupby(["MÊS", "MÊS_NOME"], as_index=False)
        .agg({"VALOR REF 01": "sum", "META": "sum"})
        .sort_values("MÊS")
    )

    fig_exec = go.Figure()
    fig_exec.add_bar(
        x=resumo_mensal_exec["MÊS_NOME"],
        y=resumo_mensal_exec["VALOR REF 01"],
        name="Realizado",
        marker_cornerradius=4
    )
    fig_exec.add_scatter(
        x=resumo_mensal_exec["MÊS_NOME"],
        y=resumo_mensal_exec["META"],
        name="Meta",
        mode="lines+markers",
        line=dict(color="#7F77DD", width=2, dash="dot")
    )
    fig_exec.update_layout(
        height=380,
        margin=dict(t=20, b=20),
        legend=dict(orientation="h", y=-0.15)
    )
    st.plotly_chart(fig_exec, use_container_width=True)

    if filial == "Geral":
        st.divider()
        st.subheader("Ranking de filiais")

        ranking_filiais = comparativo_filiais(df, ano_kpi)

        st.dataframe(
            ranking_filiais.style.format({
                "Realizado": "R$ {:,.0f}",
                "Meta": "R$ {:,.0f}",
                "Gap": "R$ {:,.0f}",
                "Atingimento": "{:.1%}",
            }),
            use_container_width=True,
            hide_index=True
        )

with tab1:
    col_title, col_btn = st.columns([3, 1])

    with col_title:
        st.subheader(f"{indicador} — {filial}")

    anos = sorted(df["ANO"].unique())
    ano_selecionado = st.selectbox("Selecione o ano", anos, index=len(anos) - 1)

    df_completa = tabela_completa_ano(df, ano_selecionado)

    with col_btn:
        st.write("")
        pdf_bytes = gerar_pdf(df_todas_unidades, indicador, ano_selecionado)
        st.download_button(
            label="📄 Baixar PDF",
            data=pdf_bytes,
            file_name=f"relatorio_{indicador}_todas_unidades_{datetime.now().strftime('%Y%m%d')}.pdf",
            mime="application/pdf",
            use_container_width=True
        )

    def cor_gap(v):
        if pd.isna(v):
            return ""
        return "color: #1D9E75; font-weight:bold" if v >= 0 else "color: #E24B4A; font-weight:bold"

    def cor_ating(v):
        if pd.isna(v):
            return ""
        if v >= 1.0:
            return "color: #1D9E75; font-weight:bold"
        if v >= 0.85:
            return "color: #BA7517"
        return "color: #E24B4A"

    st.dataframe(
        df_completa.style
        .format({
            "Realizado": lambda v: f"R$ {v:,.0f}".replace(",", ".") if pd.notna(v) else "",
            "Meta": lambda v: f"R$ {v:,.0f}".replace(",", ".") if pd.notna(v) else "",
            "Gap (R$)": lambda v: f"R$ {v:+,.0f}".replace(",", ".") if pd.notna(v) else "",
            "Atingimento": lambda v: f"{v:.0%}" if pd.notna(v) else "",
        })
        .map(cor_gap, subset=["Gap (R$)"])
        .map(cor_ating, subset=["Atingimento"]),
        use_container_width=True,
        hide_index=True
    )

    st.divider()
    st.subheader(f"Realizado x Meta — {ano_selecionado}")

    dados_ano = df_completa[df_completa["Mês"] != "TOTAL"].dropna(subset=["Realizado"])

    if not dados_ano.empty:
        fig = go.Figure()
        fig.add_bar(
            x=dados_ano["Mês"],
            y=dados_ano["Realizado"],
            name="Realizado",
            marker_color=[
                "#1D9E75" if r >= m else "#E24B4A"
                for r, m in zip(dados_ano["Realizado"], dados_ano["Meta"])
            ],
            marker_cornerradius=4
        )
        fig.add_scatter(
            x=dados_ano["Mês"],
            y=dados_ano["Meta"],
            name="Meta",
            mode="lines+markers",
            line=dict(color="#7F77DD", width=2, dash="dot")
        )
        fig.update_layout(
            height=360,
            margin=dict(t=20, b=20),
            legend=dict(orientation="h", y=-0.15)
        )
        st.plotly_chart(fig, use_container_width=True)

with tab2:
    st.subheader(f"{indicador} — {filial} · Variação Mês a Mês")
    df_mom = calcular_mom(df).sort_values("MÊS_ORDEM")

    def hl_mom(v):
        if pd.isna(v):
            return ""
        return "color: #1D9E75; font-weight:bold" if v > 0 else "color: #E24B4A; font-weight:bold"

    def hl_at(v):
        if pd.isna(v):
            return ""
        if v >= 1.0:
            return "color: #1D9E75; font-weight:bold"
        if v >= 0.85:
            return "color: #BA7517"
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
        use_container_width=True,
        hide_index=True
    )

    ultimos = df_mom.tail(16)
    real = ultimos["Realizado"].tolist()
    meta_l = ultimos["META"].tolist()
    cores = ["#1D9E75" if r >= m else "#E24B4A" for r, m in zip(real, meta_l)]

    fig2 = go.Figure()
    fig2.add_bar(
        x=ultimos["Mês"].tolist(),
        y=real,
        name="Realizado",
        marker_color=cores,
        marker_cornerradius=4
    )
    fig2.add_scatter(
        x=ultimos["Mês"].tolist(),
        y=meta_l,
        name="Meta",
        mode="lines",
        line=dict(color="#7F77DD", width=2, dash="dot")
    )
    fig2.update_layout(
        height=360,
        margin=dict(t=20, b=20),
        legend=dict(orientation="h", y=-0.2),
        xaxis_tickangle=-45
    )
    st.plotly_chart(fig2, use_container_width=True)

with tab3:
    st.subheader(f"{indicador} — {filial} · Comparativo Ano a Ano")
    df_yoy = calcular_yoy(df)

    def hl_yoy(v):
        if pd.isna(v):
            return ""
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
        use_container_width=True,
        hide_index=True
    )

    if filial == "Geral":
        st.divider()
        st.subheader("Comparativo entre filiais")

        ano_base = max(df["ANO"].unique())
        comp_filiais = comparativo_filiais(df, ano_base)

        st.dataframe(
            comp_filiais.style.format({
                "Realizado": "R$ {:,.0f}",
                "Meta": "R$ {:,.0f}",
                "Gap": "R$ {:,.0f}",
                "Atingimento": "{:.1%}",
            }),
            use_container_width=True,
            hide_index=True
        )

        fig_filiais = go.Figure()
        fig_filiais.add_bar(
            x=comp_filiais["FILIAL"],
            y=comp_filiais["Realizado"],
            name="Realizado",
            marker_cornerradius=4
        )
        fig_filiais.add_scatter(
            x=comp_filiais["FILIAL"],
            y=comp_filiais["Meta"],
            name="Meta",
            mode="lines+markers",
            line=dict(color="#7F77DD", width=2, dash="dot")
        )
        fig_filiais.update_layout(
            height=380,
            margin=dict(t=20, b=20),
            legend=dict(orientation="h", y=-0.15)
        )
        st.plotly_chart(fig_filiais, use_container_width=True)

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
