import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from anthropic import Anthropic
from fpdf import FPDF
from datetime import datetime
import os

st.set_page_config(page_title="Análise de Indicadores Mazola Ambiental", page_icon="📊", layout="wide")

client = Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])

COR_LARANJA = "#F26522"
COR_VERDE = "#00A350"
QUALQUER = "__ANY__"
LOGO_ARQUIVO = "MazolaCertificado.ico"

INDICADORES = {
    "Faturamento": {
        "tipo": "simples",
        "categoria": "faturamento",
        "TIPO": "ECONOMICO",
        "GRUPO 01": "FATURAMENTO",
        "GRUPO 02": None,
        "GRUPO 03": None,
        "TIPO DE META": "R$",
    },
    "Faturamento Serviços": {
        "tipo": "simples",
        "categoria": "faturamento",
        "TIPO": "ECONOMICO",
        "GRUPO 01": "FATURAMENTO",
        "GRUPO 02": "SERVICOS",
        "GRUPO 03": None,
        "TIPO DE META": "R$",
    },
    "Faturamento Regular": {
        "tipo": "simples",
        "categoria": "faturamento",
        "TIPO": "ECONOMICO",
        "GRUPO 01": "FATURAMENTO",
        "GRUPO 02": "SERVICOS",
        "GRUPO 03": "REGULAR",
        "TIPO DE META": "R$",
    },
    "Faturamento Incremental": {
        "tipo": "simples",
        "categoria": "faturamento",
        "TIPO": "ECONOMICO",
        "GRUPO 01": "FATURAMENTO",
        "GRUPO 02": "SERVICOS",
        "GRUPO 03": "INCREMENTAL",
        "TIPO DE META": "R$",
    },
    "Faturamento LCSAO": {
        "tipo": "simples",
        "categoria": "faturamento",
        "TIPO": "ECONOMICO",
        "GRUPO 01": "FATURAMENTO",
        "GRUPO 02": "SERVICOS",
        "GRUPO 03": "LCSAO",
        "TIPO DE META": "R$",
    },
    "Faturamento Sucata Diversa": {
        "tipo": "simples",
        "categoria": "faturamento",
        "TIPO": "ECONOMICO",
        "GRUPO 01": "FATURAMENTO",
        "GRUPO 02": "SUCATAS DIVERSAS",
        "GRUPO 03": None,
        "TIPO DE META": "R$",
    },
    "Faturamento Reman Rev": {
        "tipo": "simples",
        "categoria": "faturamento",
        "TIPO": "ECONOMICO",
        "GRUPO 01": "FATURAMENTO",
        "GRUPO 02": "REMAN REV",
        "GRUPO 03": None,
        "TIPO DE META": "R$",
    },
    "Faturamento Reman Cap": {
        "tipo": "simples",
        "categoria": "faturamento",
        "TIPO": "ECONOMICO",
        "GRUPO 01": "FATURAMENTO",
        "GRUPO 02": "REMAN CAP",
        "GRUPO 03": None,
        "TIPO DE META": "R$",
    },
    "Faturamento Reman Total": {
        "tipo": "composto",
        "componentes": ["Faturamento Reman Rev", "Faturamento Reman Cap"]
    },
    "Faturamento Pneus Velhos": {
        "tipo": "simples",
        "categoria": "faturamento",
        "TIPO": "ECONOMICO",
        "GRUPO 01": "FATURAMENTO",
        "GRUPO 02": "PNEUS VELHOS",
        "GRUPO 03": QUALQUER,
        "TIPO DE META": "R$",
    },
    "Faturamento Pneu Exceto Moto": {
        "tipo": "simples",
        "categoria": "faturamento",
        "TIPO": "ECONOMICO",
        "GRUPO 01": "FATURAMENTO",
        "GRUPO 02": "PNEUS VELHOS",
        "GRUPO 03": None,
        "TIPO DE META": "R$",
    },
    "Faturamento Especial": {
        "tipo": "simples",
        "categoria": "faturamento",
        "TIPO": "ECONOMICO",
        "GRUPO 01": "FATURAMENTO",
        "GRUPO 02": "ESPECIAL",
        "GRUPO 03": None,
        "TIPO DE META": "R$",
    },
    "Despesa Geral": {
        "tipo": "simples",
        "categoria": "despesa",
        "TIPO": "ECONOMICO",
        "GRUPO 01": "DESPESAS",
        "GRUPO 02": None,
        "GRUPO 03": QUALQUER,
        "TIPO DE META": "%",
    },
    "Despesa Manutenção": {
        "tipo": "simples",
        "categoria": "despesa",
        "TIPO": "ECONOMICO",
        "GRUPO 01": "DESPESAS",
        "GRUPO 02": "MANUTENCAO",
        "GRUPO 03": QUALQUER,
        "TIPO DE META": "R$",
    },
    "Despesa Hora Extra": {
        "tipo": "simples",
        "categoria": "despesa",
        "TIPO": "ECONOMICO",
        "GRUPO 01": "DESPESAS",
        "GRUPO 02": "HORAS EXTRAS",
        "GRUPO 03": QUALQUER,
        "TIPO DE META": "R$",
    },
}

FILIAIS_REAIS = ["CANOAS/RS", "CURITIBA/PR", "DUQUE DE CAXIAS/RJ", "VALINHOS/SP"]


@st.cache_data
def carregar(arquivo):
    return pd.read_excel(arquivo)


def aplicar_filtro_coluna(df, coluna, valor):
    if valor == QUALQUER:
        return df
    if valor is None:
        return df[df[coluna].isna()]
    return df[df[coluna] == valor]


def aplicar_filtro_base(df, cfg, filial):
    d = df.copy()

    if cfg.get("TIPO") is not None:
        d = d[d["TIPO"] == cfg["TIPO"]]

    d = aplicar_filtro_coluna(d, "GRUPO 01", cfg.get("GRUPO 01"))
    d = aplicar_filtro_coluna(d, "GRUPO 02", cfg.get("GRUPO 02"))
    d = aplicar_filtro_coluna(d, "GRUPO 03", cfg.get("GRUPO 03"))

    if cfg.get("TIPO DE META") is not None:
        d = d[d["TIPO DE META"] == cfg["TIPO DE META"]]

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
    d = d.sort_values(["ANO", "MÊS", "FILIAL"]).reset_index(drop=True)
    return d


def consolidar_campos(df, nome_indicador):
    d = df.copy()
    categoria = INDICADORES[nome_indicador].get("categoria", "faturamento")

    d["META_CALC"] = pd.to_numeric(d.get("META"), errors="coerce").fillna(0)
    d["VALOR1"] = pd.to_numeric(d.get("VALOR REF 01"), errors="coerce").fillna(0)
    d["VALOR2"] = pd.to_numeric(d.get("VALOR REF 02"), errors="coerce").fillna(0)

    if categoria == "faturamento":
        d["REALIZADO_CALC"] = d["VALOR1"]
        d["RESULTADO_RS"] = d["REALIZADO_CALC"] - d["META_CALC"]
        d["ATINGIMENTO_CALC"] = d["REALIZADO_CALC"] / d["META_CALC"]

    elif categoria == "despesa":
        d["REALIZADO_CALC"] = d["VALOR1"]
        d["RESULTADO_RS"] = d["META_CALC"] - d["REALIZADO_CALC"]
        d["ATINGIMENTO_CALC"] = d["META_CALC"] / d["REALIZADO_CALC"]

    else:
        d["REALIZADO_CALC"] = d["VALOR1"]
        d["RESULTADO_RS"] = d["REALIZADO_CALC"] - d["META_CALC"]
        d["ATINGIMENTO_CALC"] = d["REALIZADO_CALC"] / d["META_CALC"]

    d = d.replace([float("inf"), float("-inf")], pd.NA)
    return d


def filtrar(df, indicador, filial):
    cfg = INDICADORES[indicador]

    if cfg["tipo"] == "simples":
        d = aplicar_filtro_base(df, cfg, filial)
        d = consolidar_campos(d, indicador)
        return d

    if cfg["tipo"] == "composto":
        componentes = []
        for nome_comp in cfg["componentes"]:
            cfg_comp = INDICADORES[nome_comp]
            d_comp = aplicar_filtro_base(df, cfg_comp, filial)
            d_comp = consolidar_campos(d_comp, nome_comp)
            componentes.append(d_comp)

        if not componentes:
            return pd.DataFrame()

        base = pd.concat(componentes, ignore_index=True)

        agrupado = (
            base.groupby(["FILIAL", "REFERÊNCIA", "ANO", "MÊS", "MÊS_ORDEM", "MÊS_NOME"], as_index=False)
            .agg({
                "META_CALC": "sum",
                "REALIZADO_CALC": "sum"
            })
        )
        agrupado["RESULTADO_RS"] = agrupado["REALIZADO_CALC"] - agrupado["META_CALC"]
        agrupado["ATINGIMENTO_CALC"] = agrupado["REALIZADO_CALC"] / agrupado["META_CALC"]
        agrupado = agrupado.replace([float("inf"), float("-inf")], pd.NA)
        return agrupado

    return pd.DataFrame()


def eh_despesa(indicador):
    return "Despesa" in indicador


def tabela_completa_ano(d, ano, indicador):
    meses = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]
    rows = []

    base_ano = d[d["ANO"] == ano].copy()
    mensal = (
        base_ano.groupby("MÊS", as_index=False)
        .agg({
            "REALIZADO_CALC": "sum",
            "META_CALC": "sum"
        })
        .sort_values("MÊS")
    )

    despesa = eh_despesa(indicador)

    for i, mes in enumerate(meses, 1):
        sub = mensal[mensal["MÊS"] == i]
        if len(sub) > 0:
            real = sub["REALIZADO_CALC"].values[0]
            meta = sub["META_CALC"].values[0]

            if despesa:
                gap = meta - real
                ating = meta / real if real not in [0, None] else None
            else:
                gap = real - meta
                ating = real / meta if meta not in [0, None] else None

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

    total_real = base_ano["REALIZADO_CALC"].sum()
    total_meta = base_ano["META_CALC"].sum()

    if despesa:
        total_gap = total_meta - total_real
        total_ating = total_meta / total_real if total_real > 0 else None
    else:
        total_gap = total_real - total_meta
        total_ating = total_real / total_meta if total_meta > 0 else None

    rows.append({
        "Mês": "TOTAL",
        "Realizado": total_real,
        "Meta": total_meta,
        "Gap (R$)": total_gap,
        "Atingimento": total_ating,
    })

    return pd.DataFrame(rows)


def calcular_mom(d, indicador):
    base = (
        d.groupby(["ANO", "MÊS", "MÊS_NOME", "MÊS_ORDEM"], as_index=False)
        .agg({
            "META_CALC": "sum",
            "REALIZADO_CALC": "sum"
        })
        .sort_values("MÊS_ORDEM")
        .reset_index(drop=True)
    )

    if eh_despesa(indicador):
        base["Ating."] = base["META_CALC"] / base["REALIZADO_CALC"]
    else:
        base["Ating."] = base["REALIZADO_CALC"] / base["META_CALC"]

    base["MoM_%"] = base["REALIZADO_CALC"].pct_change() * 100

    return base.rename(columns={
        "MÊS_NOME": "Mês",
        "REALIZADO_CALC": "Realizado",
        "META_CALC": "META"
    })[["ANO", "MÊS", "Mês", "META", "Realizado", "Ating.", "MoM_%", "MÊS_ORDEM"]]


def calcular_yoy(d, indicador):
    df_yoy = (
        d.groupby("ANO", as_index=False)
        .agg({
            "REALIZADO_CALC": "sum",
            "META_CALC": "sum",
            "MÊS": "nunique"
        })
        .sort_values("ANO")
        .rename(columns={
            "REALIZADO_CALC": "Realizado",
            "META_CALC": "Meta",
            "MÊS": "Meses c/ dado"
        })
    )

    if eh_despesa(indicador):
        df_yoy["Atingimento"] = df_yoy["Meta"] / df_yoy["Realizado"]
    else:
        df_yoy["Atingimento"] = df_yoy["Realizado"] / df_yoy["Meta"]

    df_yoy["YoY_%"] = df_yoy["Realizado"].pct_change() * 100
    return df_yoy


def comparativo_filiais(d, ano, indicador):
    comp = (
        d[d["ANO"] == ano]
        .groupby("FILIAL", as_index=False)
        .agg({
            "REALIZADO_CALC": "sum",
            "META_CALC": "sum"
        })
        .rename(columns={
            "REALIZADO_CALC": "Realizado",
            "META_CALC": "Meta"
        })
        .sort_values("Realizado", ascending=False)
    )

    if eh_despesa(indicador):
        comp["Gap"] = comp["Meta"] - comp["Realizado"]
        comp["Atingimento"] = comp["Meta"] / comp["Realizado"]
    else:
        comp["Gap"] = comp["Realizado"] - comp["Meta"]
        comp["Atingimento"] = comp["Realizado"] / comp["Meta"]

    return comp


def comparar_mesmo_periodo(d, indicador, ano_referencia=None):
    if d.empty:
        return pd.DataFrame()

    if ano_referencia is None:
        ano_referencia = int(d["ANO"].max())

    ano_anterior = ano_referencia - 1

    base_atual = d[d["ANO"] == ano_referencia].copy()
    base_ant = d[d["ANO"] == ano_anterior].copy()

    if base_atual.empty:
        return pd.DataFrame()

    mes_limite = int(base_atual["MÊS"].max())

    atual_periodo = base_atual[base_atual["MÊS"] <= mes_limite]
    anterior_periodo = base_ant[base_ant["MÊS"] <= mes_limite]

    nomes_meses = {
        1: "Jan", 2: "Fev", 3: "Mar", 4: "Abr", 5: "Mai", 6: "Jun",
        7: "Jul", 8: "Ago", 9: "Set", 10: "Out", 11: "Nov", 12: "Dez"
    }

    real_atual = atual_periodo["REALIZADO_CALC"].sum()
    meta_atual = atual_periodo["META_CALC"].sum()

    real_ant = anterior_periodo["REALIZADO_CALC"].sum()
    meta_ant = anterior_periodo["META_CALC"].sum()

    if eh_despesa(indicador):
        ating_atual = meta_atual / real_atual if real_atual > 0 else None
        ating_ant = meta_ant / real_ant if real_ant > 0 else None
        gap_atual = meta_atual - real_atual
        gap_ant = meta_ant - real_ant
    else:
        ating_atual = real_atual / meta_atual if meta_atual > 0 else None
        ating_ant = real_ant / meta_ant if meta_ant > 0 else None
        gap_atual = real_atual - meta_atual
        gap_ant = real_ant - meta_ant

    var_real = ((real_atual / real_ant) - 1) if real_ant > 0 else None
    var_meta = ((meta_atual / meta_ant) - 1) if meta_ant > 0 else None

    return pd.DataFrame([
        {
            "Ano": ano_anterior,
            "Período": f"Jan a {nomes_meses[mes_limite]}",
            "Realizado": real_ant,
            "Meta": meta_ant,
            "Gap (R$)": gap_ant,
            "Atingimento": ating_ant,
            "Variação Realizado": None,
            "Variação Meta": None,
        },
        {
            "Ano": ano_referencia,
            "Período": f"Jan a {nomes_meses[mes_limite]}",
            "Realizado": real_atual,
            "Meta": meta_atual,
            "Gap (R$)": gap_atual,
            "Atingimento": ating_atual,
            "Variação Realizado": var_real,
            "Variação Meta": var_meta,
        }
    ])


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
        self._font_family = "Helvetica"
        self._font_ready = False

    def configurar_fontes(self):
        if self._font_ready:
            return

        regular_exists = os.path.exists("DejaVuSans.ttf")
        bold_exists = os.path.exists("DejaVuSans-Bold.ttf")

        if regular_exists and bold_exists:
            self.add_font("DejaVu", "", "DejaVuSans.ttf")
            self.add_font("DejaVu", "B", "DejaVuSans-Bold.ttf")
            self._font_family = "DejaVu"
        else:
            self._font_family = "Helvetica"

        self._font_ready = True

    def fonte(self, estilo="", tamanho=10):
        self.configurar_fontes()
        self.set_font(self._font_family, estilo, tamanho)

    def safe(self, texto):
        if self._font_family == "Helvetica":
            return (
                str(texto)
                .replace("á", "a").replace("à", "a").replace("ã", "a").replace("â", "a")
                .replace("é", "e").replace("ê", "e")
                .replace("í", "i")
                .replace("ó", "o").replace("ô", "o").replace("õ", "o")
                .replace("ú", "u")
                .replace("ç", "c")
                .replace("Á", "A").replace("À", "A").replace("Ã", "A").replace("Â", "A")
                .replace("É", "E").replace("Ê", "E")
                .replace("Í", "I")
                .replace("Ó", "O").replace("Ô", "O").replace("Õ", "O")
                .replace("Ú", "U")
                .replace("Ç", "C")
            )
        return str(texto)

    def header(self):
        self.configurar_fontes()

        if os.path.exists(LOGO_ARQUIVO):
            try:
                self.image(LOGO_ARQUIVO, 10, 6, 38)
            except Exception:
                pass

        self.fonte("B", 14)
        self.cell(0, 10, self.safe("Relatório - Análise de Indicadores Mazola Ambiental"), align="C", new_x="LMARGIN", new_y="NEXT")

        self.fonte("", 10)
        self.cell(0, 6, self.safe(f"Indicador: {self.indicador} | Base: GERAL"), align="C", new_x="LMARGIN", new_y="NEXT")
        self.cell(0, 6, self.safe(f"Gerado em: {datetime.now().strftime('%d/%m/%Y %H:%M')}"), align="C", new_x="LMARGIN", new_y="NEXT")

        self.ln(4)
        self.set_draw_color(200, 200, 200)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(4)

    def footer(self):
        self.configurar_fontes()
        self.set_y(-15)
        self.fonte("", 8)
        self.set_text_color(128, 128, 128)
        self.cell(0, 10, self.safe(f"Pagina {self.page_no()}"), align="C")

    def secao(self, titulo):
        self.configurar_fontes()
        self.fonte("B", 12)
        self.set_text_color(0, 0, 0)
        self.ln(4)
        self.cell(0, 8, self.safe(titulo), new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def tabela_por_ano(self, df_completa):
        self.fonte("B", 10)
        self.set_fill_color(240, 240, 240)

        headers = ["Mes", "Realizado", "Meta", "Gap (R$)", "Atingimento"]
        widths = [30, 40, 40, 40, 35]

        for h, w in zip(headers, widths):
            self.cell(w, 8, self.safe(h), border=1, fill=True, align="C")
        self.ln()

        self.fonte("", 9)
        for _, row in df_completa.iterrows():
            is_total = row["Mês"] == "TOTAL"

            if is_total:
                self.fonte("B", 9)

            self.cell(widths[0], 7, self.safe(str(row["Mês"])), border=1, align="C")
            self.cell(widths[1], 7, fmt_brl(row["Realizado"]), border=1, align="R")
            self.cell(widths[2], 7, fmt_brl(row["Meta"]), border=1, align="R")
            self.cell(widths[3], 7, fmt_brl(row["Gap (R$)"]), border=1, align="R")
            self.cell(widths[4], 7, fmt_pct(row["Atingimento"]), border=1, align="R")
            self.ln()

            if is_total:
                self.fonte("", 9)

    def tabela_mom(self, df_mom):
        self.fonte("B", 10)
        self.set_fill_color(240, 240, 240)

        headers = ["Mes", "Meta", "Realizado", "Ating.", "MoM %"]
        widths = [35, 40, 40, 30, 30]

        for h, w in zip(headers, widths):
            self.cell(w, 8, self.safe(h), border=1, fill=True, align="C")
        self.ln()

        self.fonte("", 9)
        for _, row in df_mom.iterrows():
            self.cell(widths[0], 7, self.safe(str(row["Mês"])), border=1)
            self.cell(widths[1], 7, fmt_brl(row["META"]), border=1, align="R")
            self.cell(widths[2], 7, fmt_brl(row["Realizado"]), border=1, align="R")
            self.cell(widths[3], 7, fmt_pct(row["Ating."]), border=1, align="R")
            mom_val = row["MoM_%"]
            mom_str = f"{mom_val:+.1f}%" if pd.notna(mom_val) else "-"
            self.cell(widths[4], 7, mom_str, border=1, align="R")
            self.ln()

    def tabela_yoy(self, df_yoy):
        self.fonte("B", 10)
        self.set_fill_color(240, 240, 240)

        headers = ["Ano", "Realizado", "Meta", "Atingimento", "Meses", "YoY %"]
        widths = [20, 40, 40, 35, 20, 30]

        for h, w in zip(headers, widths):
            self.cell(w, 8, self.safe(h), border=1, fill=True, align="C")
        self.ln()

        self.fonte("", 9)
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
        self.fonte("B", 10)
        self.set_fill_color(240, 240, 240)

        headers = ["Filial", "Realizado", "Meta", "Gap", "Atingimento"]
        widths = [55, 35, 35, 30, 30]

        for h, w in zip(headers, widths):
            self.cell(w, 8, self.safe(h), border=1, fill=True, align="C")
        self.ln()

        self.fonte("", 9)
        for _, row in df_filiais.iterrows():
            self.cell(widths[0], 7, self.safe(str(row["FILIAL"])), border=1)
            self.cell(widths[1], 7, fmt_brl(row["Realizado"]), border=1, align="R")
            self.cell(widths[2], 7, fmt_brl(row["Meta"]), border=1, align="R")
            self.cell(widths[3], 7, fmt_brl(row["Gap"]), border=1, align="R")
            self.cell(widths[4], 7, fmt_pct(row["Atingimento"]), border=1, align="R")
            self.ln()

    def tabela_periodo(self, df_periodo):
        self.fonte("B", 10)
        self.set_fill_color(240, 240, 240)

        headers = ["Ano", "Periodo", "Realizado", "Meta", "Gap", "Atingimento", "Var. Real."]
        widths = [18, 28, 32, 28, 26, 28, 26]

        for h, w in zip(headers, widths):
            self.cell(w, 8, self.safe(h), border=1, fill=True, align="C")
        self.ln()

        self.fonte("", 8)
        for _, row in df_periodo.iterrows():
            var_real = row["Variação Realizado"]
            var_real_str = fmt_pct(var_real) if pd.notna(var_real) else "-"

            self.cell(widths[0], 7, str(int(row["Ano"])), border=1, align="C")
            self.cell(widths[1], 7, self.safe(str(row["Período"])), border=1, align="C")
            self.cell(widths[2], 7, fmt_brl(row["Realizado"]), border=1, align="R")
            self.cell(widths[3], 7, fmt_brl(row["Meta"]), border=1, align="R")
            self.cell(widths[4], 7, fmt_brl(row["Gap (R$)"]), border=1, align="R")
            self.cell(widths[5], 7, fmt_pct(row["Atingimento"]), border=1, align="R")
            self.cell(widths[6], 7, var_real_str, border=1, align="R")
            self.ln()


def gerar_pdf(df_todas_unidades, indicador, ano_selecionado):
    pdf = PDFRelatorio(indicador, "GERAL")

    pdf.add_page()
    pdf.secao(f"1. Analise por Mes - Ano {ano_selecionado}")
    df_completa = tabela_completa_ano(df_todas_unidades, ano_selecionado, indicador)
    pdf.tabela_por_ano(df_completa)

    pdf.add_page()
    pdf.secao(f"2. Variacao Mes a Mes (MoM) - Ano {ano_selecionado}")
    df_mom = calcular_mom(df_todas_unidades, indicador)
    df_mom_pdf = df_mom[df_mom["ANO"] == ano_selecionado].sort_values("MÊS")
    pdf.tabela_mom(df_mom_pdf)

    pdf.add_page()
    pdf.secao("3. Comparativo Ano a Ano (YoY)")
    df_yoy = calcular_yoy(df_todas_unidades, indicador)
    pdf.tabela_yoy(df_yoy)

    pdf.add_page()
    pdf.secao("4. Comparativo do Mesmo Periodo vs Ano Anterior")
    df_periodo = comparar_mesmo_periodo(df_todas_unidades, indicador, ano_selecionado)
    if not df_periodo.empty:
        pdf.tabela_periodo(df_periodo)

    pdf.add_page()
    pdf.secao(f"5. Comparativo entre Filiais - {ano_selecionado}")
    df_filiais = comparativo_filiais(df_todas_unidades, ano_selecionado, indicador)
    pdf.tabela_filiais(df_filiais)

    for filial_rel in FILIAIS_REAIS:
        df_filial = df_todas_unidades[df_todas_unidades["FILIAL"] == filial_rel].copy()
        if df_filial.empty:
            continue

        pdf.add_page()
        pdf.secao(f"6. Filial: {filial_rel} - Ano {ano_selecionado}")
        df_filial_ano = tabela_completa_ano(df_filial, ano_selecionado, indicador)
        pdf.tabela_por_ano(df_filial_ano)

    return bytes(pdf.output())


def resumo_para_ia(d, indicador, filial, pergunta):
    mom = calcular_mom(d, indicador).tail(12).to_string(index=False)
    yoy = calcular_yoy(d, indicador).to_string(index=False)
    periodo = comparar_mesmo_periodo(d, indicador)
    periodo_txt = periodo.to_string(index=False) if not periodo.empty else "Sem dados"

    return f"""Você é um analista financeiro experiente. Analise os dados abaixo e responda em português brasileiro de forma clara e objetiva.

Indicador: {indicador} | Filial em tela: {filial}
Pergunta/solicitação: {pergunta}

=== YoY (ano a ano) ===
{yoy}

=== MoM (últimos 12 meses) ===
{mom}

=== Mesmo período vs ano anterior ===
{periodo_txt}

Estruture sua resposta com:
1. Resumo executivo
2. Pontos de atenção
3. Tendências identificadas
4. Sugestões práticas de melhoria

Use R$ e % nos números. Seja direto e prático."""


st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Montserrat:wght@600;700&display=swap');

    .titulo-mazola {
        font-family: 'Montserrat', sans-serif;
        font-size: 34px;
        font-weight: 700;
        color: #F26522;
        margin: 0;
        line-height: 1.1;
    }

    .subtitulo-mazola {
        font-family: 'Montserrat', sans-serif;
        font-size: 15px;
        color: #00A350;
        margin: 4px 0 0 0;
        line-height: 1.1;
    }

    .logo-alinhada img {
        display: block;
        margin-top: 0px;
    }

    .texto-cabecalho {
        padding-top: 3px;
        margin-left: -100px;
    }
    </style>
""", unsafe_allow_html=True)

col_logo, col_titulo = st.columns([1, 4], vertical_alignment="center", gap="small")

with col_logo:
    if os.path.exists(LOGO_ARQUIVO):
        st.markdown('<div class="logo-alinhada">', unsafe_allow_html=True)
        st.image(LOGO_ARQUIVO, width=160)
        st.markdown('</div>', unsafe_allow_html=True)

with col_titulo:
    st.markdown('<div class="texto-cabecalho">', unsafe_allow_html=True)
    st.markdown('<p class="titulo-mazola">Análise de Indicadores Mazola Ambiental</p>', unsafe_allow_html=True)
    st.markdown('<p class="subtitulo-mazola">Painel gerencial de acompanhamento de metas e resultados</p>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

with st.sidebar:
    st.header("⚙️ Filtros")
    arquivo = st.file_uploader("Upload da planilha (.xlsx)", type=["xlsx"])
    st.divider()
    indicador = st.selectbox("Indicador", list(INDICADORES.keys()))
    filial = st.selectbox("Filial", ["Geral"] + FILIAIS_REAIS)
    st.divider()
    st.caption("v3.3 — Bot Indicadores")

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
    periodo_cmp = comparar_mesmo_periodo(df, indicador, ano_kpi)

    base_kpi = df[df["ANO"] == ano_kpi].copy()

    realizado_total = base_kpi["REALIZADO_CALC"].sum()
    meta_total = base_kpi["META_CALC"].sum()

    if eh_despesa(indicador):
        gap_total = meta_total - realizado_total
        ating_total = meta_total / realizado_total if realizado_total > 0 else None
    else:
        gap_total = realizado_total - meta_total
        ating_total = realizado_total / meta_total if meta_total > 0 else None

    if not periodo_cmp.empty and len(periodo_cmp) == 2:
        realizado_ly = periodo_cmp.iloc[0]["Realizado"]
        realizado_ytd = periodo_cmp.iloc[1]["Realizado"]
        delta_ytd = periodo_cmp.iloc[1]["Variação Realizado"]
        periodo_label = periodo_cmp.iloc[1]["Período"]
    else:
        realizado_ly = None
        realizado_ytd = None
        delta_ytd = None
        periodo_label = "-"

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Realizado Ano", fmt_brl(realizado_total))
    col2.metric("Meta Ano", fmt_brl(meta_total))
    col3.metric("Gap Ano", fmt_brl(gap_total))
    col4.metric("Atingimento Ano", fmt_pct(ating_total))
    col5.metric(
        f"YTD {periodo_label}",
        fmt_brl(realizado_ytd) if realizado_ytd is not None else "-",
        fmt_pct(delta_ytd) if delta_ytd is not None else None
    )

    st.divider()

    resumo_mensal_exec = (
        base_kpi.groupby(["MÊS", "MÊS_NOME"], as_index=False)
        .agg({"REALIZADO_CALC": "sum", "META_CALC": "sum"})
        .sort_values("MÊS")
    )

    fig_exec = go.Figure()
    fig_exec.add_bar(
        x=resumo_mensal_exec["MÊS_NOME"],
        y=resumo_mensal_exec["REALIZADO_CALC"],
        name="Realizado",
        marker_color=COR_VERDE,
        marker_cornerradius=4
    )
    fig_exec.add_scatter(
        x=resumo_mensal_exec["MÊS_NOME"],
        y=resumo_mensal_exec["META_CALC"],
        name="Meta",
        mode="lines+markers",
        line=dict(color=COR_LARANJA, width=2, dash="dot")
    )
    fig_exec.update_layout(height=380, margin=dict(t=20, b=20), legend=dict(orientation="h", y=-0.15))
    st.plotly_chart(fig_exec, use_container_width=True)

with tab1:
    col_title, col_btn = st.columns([3, 1])

    with col_title:
        st.subheader(f"{indicador} — {filial}")

    anos = sorted(df["ANO"].unique())
    ano_selecionado = st.selectbox("Selecione o ano", anos, index=len(anos) - 1)

    df_completa = tabela_completa_ano(df, ano_selecionado, indicador)

    with col_btn:
        st.write("")
        pdf_bytes = gerar_pdf(df_todas_unidades, indicador, ano_selecionado)
        st.download_button(
            label="📄 Baixar PDF",
            data=pdf_bytes,
            file_name=f"relatorio_{indicador}_GERAL_{datetime.now().strftime('%Y%m%d')}.pdf",
            mime="application/pdf",
            use_container_width=True
        )

    def cor_gap(v):
        if pd.isna(v):
            return ""
        return f"color: {COR_VERDE}; font-weight:bold" if v >= 0 else f"color: {COR_LARANJA}; font-weight:bold"

    def cor_ating(v):
        if pd.isna(v):
            return ""
        if v >= 1.0:
            return f"color: {COR_VERDE}; font-weight:bold"
        if v >= 0.85:
            return f"color: {COR_LARANJA}; font-weight:bold"
        return f"color: {COR_LARANJA}; font-weight:bold"

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
                COR_VERDE if r >= m else COR_LARANJA
                for r, m in zip(dados_ano["Realizado"], dados_ano["Meta"])
            ],
            marker_cornerradius=4
        )
        fig.add_scatter(
            x=dados_ano["Mês"],
            y=dados_ano["Meta"],
            name="Meta",
            mode="lines+markers",
            line=dict(color=COR_LARANJA, width=2, dash="dot")
        )
        fig.update_layout(height=360, margin=dict(t=20, b=20), legend=dict(orientation="h", y=-0.15))
        st.plotly_chart(fig, use_container_width=True)

with tab2:
    st.subheader(f"{indicador} — {filial} · Variação Mês a Mês")
    df_mom = calcular_mom(df, indicador).sort_values("MÊS_ORDEM")

    def hl_mom(v):
        if pd.isna(v):
            return ""
        return f"color: {COR_VERDE}; font-weight:bold" if v > 0 else f"color: {COR_LARANJA}; font-weight:bold"

    def hl_at(v):
        if pd.isna(v):
            return ""
        if v >= 1.0:
            return f"color: {COR_VERDE}; font-weight:bold"
        if v >= 0.85:
            return f"color: {COR_LARANJA}; font-weight:bold"
        return f"color: {COR_LARANJA}; font-weight:bold"

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
    cores = [COR_VERDE if r >= m else COR_LARANJA for r, m in zip(real, meta_l)]

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
        line=dict(color=COR_LARANJA, width=2, dash="dot")
    )
    fig2.update_layout(height=360, margin=dict(t=20, b=20), legend=dict(orientation="h", y=-0.2), xaxis_tickangle=-45)
    st.plotly_chart(fig2, use_container_width=True)

with tab3:
    st.subheader(f"{indicador} — {filial} · Comparativo Ano a Ano")
    df_yoy = calcular_yoy(df, indicador)

    def hl_yoy(v):
        if pd.isna(v):
            return ""
        return f"color: {COR_VERDE}; font-weight:bold" if v > 0 else f"color: {COR_LARANJA}; font-weight:bold"

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

    st.divider()
    st.subheader("Mesmo período x ano anterior")

    df_periodo_tela = comparar_mesmo_periodo(df, indicador)
    if not df_periodo_tela.empty:
        st.dataframe(
            df_periodo_tela.style.format({
                "Realizado": "R$ {:,.0f}",
                "Meta": "R$ {:,.0f}",
                "Gap (R$)": "R$ {:,.0f}",
                "Atingimento": "{:.1%}",
                "Variação Realizado": lambda v: f"{v:+.1%}" if pd.notna(v) else "—",
                "Variação Meta": lambda v: f"{v:+.1%}" if pd.notna(v) else "—",
            }),
            use_container_width=True,
            hide_index=True
        )

    if filial == "Geral":
        st.divider()
        st.subheader("Comparativo entre filiais")

        ano_base = max(df["ANO"].unique())
        comp_filiais = comparativo_filiais(df, ano_base, indicador)

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
            marker_color=COR_VERDE,
            marker_cornerradius=4
        )
        fig_filiais.add_scatter(
            x=comp_filiais["FILIAL"],
            y=comp_filiais["Meta"],
            name="Meta",
            mode="lines+markers",
            line=dict(color=COR_LARANJA, width=2, dash="dot")
        )
        fig_filiais.update_layout(height=380, margin=dict(t=20, b=20), legend=dict(orientation="h", y=-0.15))
        st.plotly_chart(fig_filiais, use_container_width=True)

with tab4:
    st.subheader("🤖 Pergunte ao assistente")

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
                try:
                    prompt = resumo_para_ia(df, indicador, filial, pergunta)
                    resp = client.messages.create(
                        model="claude-3-5-sonnet-20241022",
                        max_tokens=1500,
                        messages=[{"role": "user", "content": prompt}]
                    )
                    texto = resp.content[0].text
                    st.markdown(texto)

                except Exception as e:
                    erro_txt = str(e)

                    if "credit balance is too low" in erro_txt.lower():
                        texto = "A integração com a IA está ativa, mas a conta da Anthropic está sem créditos no momento."
                        st.warning(texto)
                    else:
                        texto = f"Erro ao consultar a IA: {erro_txt}"
                        st.error(texto)

        st.session_state.chat.append({"role": "assistant", "content": texto})
