import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from anthropic import Anthropic
from fpdf import FPDF
from datetime import datetime
from zoneinfo import ZoneInfo
import calendar
import os
import unicodedata

st.set_page_config(page_title="Análise de Indicadores Mazola Ambiental", page_icon="📊", layout="wide")

# =========================
# CONFIGURAÇÕES GERAIS
# =========================
COR_LARANJA = "#F26522"
COR_VERDE = "#00A350"
QUALQUER = "__ANY__"
LOGO_ARQUIVO = "MazolaCertificado.ico"
TZ_BR = ZoneInfo("America/Sao_Paulo")

try:
    client = Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])
except Exception:
    client = None

INDICADORES = {
    "Faturamento": {
        "tipo": "simples", "categoria": "faturamento", "TIPO": "ECONOMICO",
        "GRUPO 01": "FATURAMENTO", "GRUPO 02": None, "GRUPO 03": None, "TIPO DE META": "R$"
    },
    "Faturamento Serviços": {
        "tipo": "simples", "categoria": "faturamento", "TIPO": "ECONOMICO",
        "GRUPO 01": "FATURAMENTO", "GRUPO 02": "SERVICOS", "GRUPO 03": None, "TIPO DE META": "R$"
    },
    "Faturamento Regular": {
        "tipo": "simples", "categoria": "faturamento", "TIPO": "ECONOMICO",
        "GRUPO 01": "FATURAMENTO", "GRUPO 02": "SERVICOS", "GRUPO 03": "REGULAR", "TIPO DE META": "R$"
    },
    "Faturamento Incremental": {
        "tipo": "simples", "categoria": "faturamento", "TIPO": "ECONOMICO",
        "GRUPO 01": "FATURAMENTO", "GRUPO 02": "SERVICOS", "GRUPO 03": "INCREMENTAL", "TIPO DE META": "R$"
    },
    "Faturamento LCSAO": {
        "tipo": "simples", "categoria": "faturamento", "TIPO": "ECONOMICO",
        "GRUPO 01": "FATURAMENTO", "GRUPO 02": "SERVICOS", "GRUPO 03": "LCSAO", "TIPO DE META": "R$"
    },
    "Faturamento Sucata Diversa": {
        "tipo": "simples", "categoria": "faturamento", "TIPO": "ECONOMICO",
        "GRUPO 01": "FATURAMENTO", "GRUPO 02": "SUCATAS DIVERSAS", "GRUPO 03": None, "TIPO DE META": "R$"
    },
    "Faturamento Reman Rev": {
        "tipo": "simples", "categoria": "faturamento", "TIPO": "ECONOMICO",
        "GRUPO 01": "FATURAMENTO", "GRUPO 02": "REMAN REV", "GRUPO 03": None, "TIPO DE META": "R$"
    },
    "Faturamento Reman Cap": {
        "tipo": "simples", "categoria": "faturamento", "TIPO": "ECONOMICO",
        "GRUPO 01": "FATURAMENTO", "GRUPO 02": "REMAN CAP", "GRUPO 03": None, "TIPO DE META": "R$"
    },
    "Faturamento Reman Total": {
        "tipo": "composto", "componentes": ["Faturamento Reman Rev", "Faturamento Reman Cap"]
    },
    "Faturamento Pneus Velhos": {
        "tipo": "simples", "categoria": "faturamento", "TIPO": "ECONOMICO",
        "GRUPO 01": "FATURAMENTO", "GRUPO 02": "PNEUS VELHOS", "GRUPO 03": QUALQUER, "TIPO DE META": "R$"
    },
    "Faturamento Pneu Exceto Moto": {
        "tipo": "simples", "categoria": "faturamento", "TIPO": "ECONOMICO",
        "GRUPO 01": "FATURAMENTO", "GRUPO 02": "PNEUS VELHOS S/MOTO", "GRUPO 03": None, "TIPO DE META": "R$"
    },
    "Faturamento Pneus Velhos Moto": {
        "tipo": "moto_margem", "categoria": "moto_margem", "TIPO": "ECONOMICO",
        "GRUPO 01": "FATURAMENTO", "GRUPO 02": "PNEUS VELHOS", "GRUPO 03": "MOTOS", "TIPO DE META": "%"
    },
    "Faturamento Especial": {
        "tipo": "simples", "categoria": "faturamento", "TIPO": "ECONOMICO",
        "GRUPO 01": "FATURAMENTO", "GRUPO 02": "ESPECIAL", "GRUPO 03": None, "TIPO DE META": "R$"
    },
    "Despesa Geral": {
        "tipo": "simples", "categoria": "despesa", "TIPO": "ECONOMICO",
        "GRUPO 01": "DESPESAS", "GRUPO 02": None, "GRUPO 03": QUALQUER, "TIPO DE META": "%"
    },
    "Despesa Manutenção": {
        "tipo": "simples", "categoria": "despesa", "TIPO": "ECONOMICO",
        "GRUPO 01": "DESPESAS", "GRUPO 02": "MANUTENCAO", "GRUPO 03": QUALQUER, "TIPO DE META": "R$"
    },
    "Despesa Hora Extra": {
        "tipo": "simples", "categoria": "despesa", "TIPO": "ECONOMICO",
        "GRUPO 01": "DESPESAS", "GRUPO 02": "HORAS EXTRAS", "GRUPO 03": QUALQUER, "TIPO DE META": "R$"
    },
}

FILIAIS_REAIS = ["CANOAS/RS", "CURITIBA/PR", "DUQUE DE CAXIAS/RJ", "VALINHOS/SP"]
MESES_MAPA = {
    1: "Jan", 2: "Fev", 3: "Mar", 4: "Abr", 5: "Mai", 6: "Jun",
    7: "Jul", 8: "Ago", 9: "Set", 10: "Out", 11: "Nov", 12: "Dez"
}


# =========================
# FUNÇÕES AUXILIARES
# =========================
def agora_br():
    return datetime.now(TZ_BR)


def fmt_brl(v):
    if pd.isna(v) or v is None:
        return "-"
    return f"R$ {v:,.0f}".replace(",", ".")


def fmt_pct(v):
    if pd.isna(v) or v is None:
        return "-"
    return f"{v:.1%}"


def eh_despesa(indicador):
    cfg = INDICADORES.get(indicador, {})
    if cfg.get("tipo") == "composto" and cfg.get("componentes"):
        return INDICADORES[cfg["componentes"][0]].get("categoria") == "despesa"
    return cfg.get("categoria") == "despesa"


def cor_gap_valor(v, despesa=False):
    if pd.isna(v):
        return ""
    return f"color: {COR_VERDE}; font-weight:bold" if v >= 0 else f"color: {COR_LARANJA}; font-weight:bold"


def cor_atingimento(v):
    if pd.isna(v):
        return ""
    return f"color: {COR_VERDE}; font-weight:bold" if v >= 1 else f"color: {COR_LARANJA}; font-weight:bold"


def cor_variacao(v):
    if pd.isna(v):
        return ""
    return f"color: {COR_VERDE}; font-weight:bold" if v > 0 else f"color: {COR_LARANJA}; font-weight:bold"


@st.cache_data

def carregar(arquivo):
    return pd.read_excel(arquivo)


def normalizar_texto(valor):
    """
    Padroniza textos da planilha para comparação:
    remove acentos, tira espaços, transforma em maiúsculo
    e trata NaN/None/vazios.
    """
    if pd.isna(valor):
        return ""
    txt = str(valor).strip()
    if txt.lower() in ["nan", "none", "null"]:
        return ""
    txt = unicodedata.normalize("NFKD", txt)
    txt = "".join(ch for ch in txt if not unicodedata.combining(ch))
    txt = " ".join(txt.split())
    return txt.upper()


def aplicar_filtro_coluna(df, coluna, valor):
    """
    Aplica filtro com tratamento robusto.

    Corrige casos em que a planilha vem com:
    - espaços extras;
    - diferença entre maiúsculas/minúsculas;
    - acentos;
    - campo vazio como NaN, None, "", espaço, "nan" etc.

    Incremental correto:
    TIPO = ECONOMICO
    GRUPO 01 = FATURAMENTO
    GRUPO 02 = SERVICOS
    GRUPO 03 = INCREMENTAL
    """
    if valor == QUALQUER:
        return df

    serie_normalizada = df[coluna].apply(normalizar_texto)

    if valor is None:
        return df[serie_normalizada == ""]

    valor_normalizado = normalizar_texto(valor)
    return df[serie_normalizada == valor_normalizado]
def aplicar_filtro_base(df, cfg, filial):
    d = df.copy()

    if cfg.get("TIPO") is not None:
        d = aplicar_filtro_coluna(d, "TIPO", cfg.get("TIPO"))

    d = aplicar_filtro_coluna(d, "GRUPO 01", cfg.get("GRUPO 01"))
    d = aplicar_filtro_coluna(d, "GRUPO 02", cfg.get("GRUPO 02"))
    d = aplicar_filtro_coluna(d, "GRUPO 03", cfg.get("GRUPO 03"))

    if cfg.get("TIPO DE META") is not None:
        d = aplicar_filtro_coluna(d, "TIPO DE META", cfg.get("TIPO DE META"))

    if filial == "Geral":
        filiais_norm = [normalizar_texto(f) for f in FILIAIS_REAIS]
        d = d[d["FILIAL"].apply(normalizar_texto).isin(filiais_norm)]
    else:
        d = d[d["FILIAL"].apply(normalizar_texto) == normalizar_texto(filial)]

    d = d.copy()
    d["REFERÊNCIA"] = pd.to_datetime(d["REFERÊNCIA"], errors="coerce")
    d = d[d["REFERÊNCIA"].notna()].copy()

    d["ANO"] = d["REFERÊNCIA"].dt.year
    d["MÊS"] = d["REFERÊNCIA"].dt.month
    d["DIA"] = d["REFERÊNCIA"].dt.day
    d["MÊS_ORDEM"] = d["ANO"] * 100 + d["MÊS"]
    d["MÊS_NOME"] = d["MÊS"].map(MESES_MAPA) + "/" + d["ANO"].astype(str)

    return d.sort_values(["ANO", "MÊS", "FILIAL"]).reset_index(drop=True)

def eh_moto_margem(indicador):
    return INDICADORES.get(indicador, {}).get("tipo") == "moto_margem"


def coluna_por_indice(df, indice_1_base):
    """Retorna o nome da coluna usando a posição do Excel: A=1, B=2, J=10, L=12."""
    pos = indice_1_base - 1
    if pos < 0 or pos >= len(df.columns):
        return None
    return df.columns[pos]


def serie_numerica_por_coluna(df, nomes_preferidos=None, indice_1_base=None):
    """Busca uma coluna por nome; se não achar, usa a posição do Excel."""
    nomes_preferidos = nomes_preferidos or []
    for nome in nomes_preferidos:
        if nome in df.columns:
            return pd.to_numeric(df[nome], errors="coerce").fillna(0)

    if indice_1_base is not None:
        col = coluna_por_indice(df, indice_1_base)
        if col is not None:
            return pd.to_numeric(df[col], errors="coerce").fillna(0)

    return pd.Series([0] * len(df), index=df.index, dtype="float64")


def ajustar_percentual_meta(serie):
    """Garante que meta percentual fique em decimal: 50% = 0.50."""
    s = pd.to_numeric(serie, errors="coerce").fillna(0)
    return s.apply(lambda x: x / 100 if x > 1 else x)


def consolidar_pneus_moto(df):
    """
    Estrutura especial: Pneus Velhos Moto.

    Filtro:
    TIPO = ECONOMICO
    GRUPO 01 = FATURAMENTO
    GRUPO 02 = PNEUS VELHOS
    GRUPO 03 = MOTOS
    TIPO DE META = %

    Cálculos:
    COMPRA = coluna L:L
    FATURAMENTO = coluna J:J / VALOR REF 01
    MARGEM BRUTA = FATURAMENTO - COMPRA
    TAXA DE SUCESSO = MARGEM BRUTA / FATURAMENTO
    META = percentual
    """
    d = df.copy()
    d["META_PERCENTUAL_CALC"] = ajustar_percentual_meta(d.get("META", 0))
    d["FATURAMENTO_MOTO_CALC"] = serie_numerica_por_coluna(d, nomes_preferidos=["VALOR REF 01", "FATURAMENTO R$", "FATURAMENTO"], indice_1_base=10)
    d["COMPRA_CALC"] = serie_numerica_por_coluna(d, nomes_preferidos=["VALOR REF 02", "COMPRA", "COMPRA R$"], indice_1_base=12)
    d["MARGEM_BRUTA_CALC"] = d["FATURAMENTO_MOTO_CALC"] - d["COMPRA_CALC"]
    d["TX_SUCESSO_CALC"] = d["MARGEM_BRUTA_CALC"] / d["FATURAMENTO_MOTO_CALC"].replace(0, pd.NA)

    # Campos de compatibilidade com o restante do app
    d["META_CALC"] = d["META_PERCENTUAL_CALC"]
    d["REALIZADO_CALC"] = d["FATURAMENTO_MOTO_CALC"]
    d["RESULTADO_RS"] = d["MARGEM_BRUTA_CALC"]
    d["ATINGIMENTO_CALC"] = d["TX_SUCESSO_CALC"] / d["META_PERCENTUAL_CALC"].replace(0, pd.NA)

    return d.replace([float("inf"), float("-inf")], pd.NA)

def consolidar_campos(df, nome_indicador):
    d = df.copy()
    categoria = INDICADORES[nome_indicador].get("categoria", "faturamento")

    d["META_CALC"] = pd.to_numeric(d.get("META"), errors="coerce").fillna(0)
    d["VALOR1"] = pd.to_numeric(d.get("VALOR REF 01"), errors="coerce").fillna(0)

    if categoria == "despesa":
        d["REALIZADO_CALC"] = d["VALOR1"]
        d["RESULTADO_RS"] = d["META_CALC"] - d["REALIZADO_CALC"]
        d["ATINGIMENTO_CALC"] = d["META_CALC"] / d["REALIZADO_CALC"].replace(0, pd.NA)
    else:
        d["REALIZADO_CALC"] = d["VALOR1"]
        d["RESULTADO_RS"] = d["REALIZADO_CALC"] - d["META_CALC"]
        d["ATINGIMENTO_CALC"] = d["REALIZADO_CALC"] / d["META_CALC"].replace(0, pd.NA)

    return d.replace([float("inf"), float("-inf")], pd.NA)


def filtrar(df, indicador, filial):
    cfg = INDICADORES[indicador]

    if cfg["tipo"] == "moto_margem":
        return consolidar_pneus_moto(aplicar_filtro_base(df, cfg, filial))

    if cfg["tipo"] == "simples":
        return consolidar_campos(aplicar_filtro_base(df, cfg, filial), indicador)

    if cfg["tipo"] == "composto":
        partes = []
        for nome_comp in cfg["componentes"]:
            cfg_comp = INDICADORES[nome_comp]
            parte = consolidar_campos(aplicar_filtro_base(df, cfg_comp, filial), nome_comp)
            partes.append(parte)

        if not partes:
            return pd.DataFrame()

        base = pd.concat(partes, ignore_index=True)
        agrupado = (
            base.groupby(["FILIAL", "REFERÊNCIA", "ANO", "MÊS", "DIA", "MÊS_ORDEM", "MÊS_NOME"], as_index=False)
            .agg({"META_CALC": "sum", "REALIZADO_CALC": "sum"})
        )
        agrupado["RESULTADO_RS"] = agrupado["REALIZADO_CALC"] - agrupado["META_CALC"]
        agrupado["ATINGIMENTO_CALC"] = agrupado["REALIZADO_CALC"] / agrupado["META_CALC"].replace(0, pd.NA)
        return agrupado.replace([float("inf"), float("-inf")], pd.NA)

    return pd.DataFrame()



def tabela_pneus_moto_ano(d, ano):
    base_ano = d[d["ANO"] == ano].copy()
    rows = []
    acumulado = 0

    mensal = (
        base_ano.groupby("MÊS", as_index=False)
        .agg({
            "META_PERCENTUAL_CALC": "mean",
            "COMPRA_CALC": "sum",
            "FATURAMENTO_MOTO_CALC": "sum",
            "MARGEM_BRUTA_CALC": "sum",
        })
        .sort_values("MÊS")
    )

    for i in range(1, 13):
        sub = mensal[mensal["MÊS"] == i]
        if len(sub) > 0:
            meta = sub["META_PERCENTUAL_CALC"].values[0]
            compra = sub["COMPRA_CALC"].values[0]
            faturamento = sub["FATURAMENTO_MOTO_CALC"].values[0]
            margem = sub["MARGEM_BRUTA_CALC"].values[0]
            tx = margem / faturamento if faturamento > 0 else None
            acumulado += margem
        else:
            meta = None
            compra = None
            faturamento = None
            margem = None
            tx = None

        rows.append({
            "Mês": MESES_MAPA[i],
            "Meta": meta,
            "Compra": compra,
            "Faturamento": faturamento,
            "Margem Bruta": margem,
            "Tx. Sucesso": tx,
            "Acumulado": acumulado if acumulado != 0 else None,
        })

    total_compra = base_ano["COMPRA_CALC"].sum()
    total_faturamento = base_ano["FATURAMENTO_MOTO_CALC"].sum()
    total_margem = base_ano["MARGEM_BRUTA_CALC"].sum()
    total_tx = total_margem / total_faturamento if total_faturamento > 0 else None
    total_meta = base_ano["META_PERCENTUAL_CALC"].mean() if not base_ano.empty else None

    rows.append({
        "Mês": "TOTAL",
        "Meta": total_meta,
        "Compra": total_compra,
        "Faturamento": total_faturamento,
        "Margem Bruta": total_margem,
        "Tx. Sucesso": total_tx,
        "Acumulado": total_margem,
    })

    return pd.DataFrame(rows)

def tabela_completa_ano(d, ano, indicador):
    rows = []
    base_ano = d[d["ANO"] == ano].copy()

    mensal = (
        base_ano.groupby("MÊS", as_index=False)
        .agg({"REALIZADO_CALC": "sum", "META_CALC": "sum"})
        .sort_values("MÊS")
    )

    despesa = eh_despesa(indicador)

    for i in range(1, 13):
        mes_nome = MESES_MAPA[i]
        sub = mensal[mensal["MÊS"] == i]

        if len(sub) > 0:
            real = sub["REALIZADO_CALC"].values[0]
            meta = sub["META_CALC"].values[0]

            if despesa:
                gap = meta - real
                ating = meta / real if real > 0 else None
            else:
                gap = real - meta
                ating = real / meta if meta > 0 else None
        else:
            real = None
            meta = None
            gap = None
            ating = None

        rows.append({
            "Mês": mes_nome,
            "Realizado": real,
            "Meta": meta,
            "Gap (R$)": gap,
            "Atingimento": ating
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
        "Atingimento": total_ating
    })

    return pd.DataFrame(rows)


def calcular_mom(d, indicador):
    base = (
        d.groupby(["ANO", "MÊS", "MÊS_NOME", "MÊS_ORDEM"], as_index=False)
        .agg({"META_CALC": "sum", "REALIZADO_CALC": "sum"})
        .sort_values("MÊS_ORDEM")
        .reset_index(drop=True)
    )

    if eh_despesa(indicador):
        base["Ating."] = base["META_CALC"] / base["REALIZADO_CALC"].replace(0, pd.NA)
        base["Gap"] = base["META_CALC"] - base["REALIZADO_CALC"]
    else:
        base["Ating."] = base["REALIZADO_CALC"] / base["META_CALC"].replace(0, pd.NA)
        base["Gap"] = base["REALIZADO_CALC"] - base["META_CALC"]

    base["MoM_%"] = base["REALIZADO_CALC"].pct_change() * 100
    base = base.replace([float("inf"), float("-inf")], pd.NA)

    return base.rename(columns={
        "MÊS_NOME": "Mês",
        "REALIZADO_CALC": "Realizado",
        "META_CALC": "META"
    })[["ANO", "MÊS", "Mês", "META", "Realizado", "Gap", "Ating.", "MoM_%", "MÊS_ORDEM"]]


def calcular_yoy(d, indicador):
    if eh_moto_margem(indicador):
        df_yoy = (
            d.groupby("ANO", as_index=False)
            .agg({
                "FATURAMENTO_MOTO_CALC": "sum",
                "COMPRA_CALC": "sum",
                "MARGEM_BRUTA_CALC": "sum",
                "META_PERCENTUAL_CALC": "mean",
                "MÊS": "nunique",
            })
            .sort_values("ANO")
        )
        df_yoy["Realizado"] = df_yoy["FATURAMENTO_MOTO_CALC"]
        df_yoy["Meta"] = df_yoy["META_PERCENTUAL_CALC"]
        df_yoy["Atingimento"] = df_yoy["MARGEM_BRUTA_CALC"] / df_yoy["FATURAMENTO_MOTO_CALC"].replace(0, pd.NA)
        df_yoy["YoY_%"] = df_yoy["Realizado"].pct_change() * 100
        df_yoy = df_yoy.rename(columns={"MÊS": "Meses c/ dado"})
        return df_yoy[["ANO", "Realizado", "Meta", "Meses c/ dado", "Atingimento", "YoY_%"]].replace([float("inf"), float("-inf")], pd.NA)

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
        df_yoy["Atingimento"] = df_yoy["Meta"] / df_yoy["Realizado"].replace(0, pd.NA)
    else:
        df_yoy["Atingimento"] = df_yoy["Realizado"] / df_yoy["Meta"].replace(0, pd.NA)

    df_yoy["YoY_%"] = df_yoy["Realizado"].pct_change() * 100
    return df_yoy.replace([float("inf"), float("-inf")], pd.NA)


def comparativo_filiais(d, ano, indicador):
    comp = (
        d[d["ANO"] == ano]
        .groupby("FILIAL", as_index=False)
        .agg({"REALIZADO_CALC": "sum", "META_CALC": "sum"})
        .rename(columns={"REALIZADO_CALC": "Realizado", "META_CALC": "Meta"})
        .sort_values("Realizado", ascending=False)
    )

    if eh_despesa(indicador):
        comp["Gap"] = comp["Meta"] - comp["Realizado"]
        comp["Atingimento"] = comp["Meta"] / comp["Realizado"].replace(0, pd.NA)
    else:
        comp["Gap"] = comp["Realizado"] - comp["Meta"]
        comp["Atingimento"] = comp["Realizado"] / comp["Meta"].replace(0, pd.NA)

    return comp


def comparar_mesmo_periodo(d, indicador, ano_referencia=None):
    if d.empty:
        return pd.DataFrame()

    if ano_referencia is None:
        ano_referencia = int(d["ANO"].max())

    ano_anterior = ano_referencia - 1
    base_atual = d[d["ANO"] == ano_referencia].copy()
    base_ant = d[d["ANO"] == ano_anterior].copy()

    if base_atual.empty and base_ant.empty:
        return pd.DataFrame()

    mes_atual_calendario = agora_br().month
    max_mes_disponivel = int(base_atual["MÊS"].max()) if not base_atual.empty else mes_atual_calendario
    mes_limite = min(mes_atual_calendario, max_mes_disponivel)

    atual_periodo = base_atual[base_atual["MÊS"] <= mes_limite]
    anterior_periodo = base_ant[base_ant["MÊS"] <= mes_limite]

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
    periodo_txt = f"Jan a {MESES_MAPA[mes_limite]}"

    return pd.DataFrame([
        {
            "Ano": ano_anterior,
            "Período": periodo_txt,
            "Realizado": real_ant,
            "Meta": meta_ant,
            "Gap (R$)": gap_ant,
            "Atingimento": ating_ant,
            "Variação Realizado": None,
            "Variação Meta": None,
        },
        {
            "Ano": ano_referencia,
            "Período": periodo_txt,
            "Realizado": real_atual,
            "Meta": meta_atual,
            "Gap (R$)": gap_atual,
            "Atingimento": ating_atual,
            "Variação Realizado": var_real,
            "Variação Meta": var_meta,
        }
    ])


def montar_resumo_pdf(df, indicador, ano_selecionado):
    if df.empty:
        return {}

    hoje = agora_br()
    base_ano = df[df["ANO"] == ano_selecionado].copy()
    if base_ano.empty:
        return {}

    despesa = eh_despesa(indicador)
    mes_atual_calendario = hoje.month
    max_mes_disponivel = int(base_ano["MÊS"].max()) if not base_ano.empty else mes_atual_calendario
    mes_referencia = min(mes_atual_calendario, max_mes_disponivel)

    base_mes = base_ano[base_ano["MÊS"] == mes_referencia].copy()
    realizado_mes = base_mes["REALIZADO_CALC"].sum()
    meta_mes = base_mes["META_CALC"].sum()

    if despesa:
        gap_mes = meta_mes - realizado_mes
        ating_mes = meta_mes / realizado_mes if realizado_mes > 0 else None
    else:
        gap_mes = realizado_mes - meta_mes
        ating_mes = realizado_mes / meta_mes if meta_mes > 0 else None

    realizado_ano = base_ano["REALIZADO_CALC"].sum()
    meta_ano = base_ano["META_CALC"].sum()

    if despesa:
        gap_ano = meta_ano - realizado_ano
        ating_ano = meta_ano / realizado_ano if realizado_ano > 0 else None
    else:
        gap_ano = realizado_ano - meta_ano
        ating_ano = realizado_ano / meta_ano if meta_ano > 0 else None

    dias_restantes = 0
    necessario_dia = None

    if ano_selecionado == hoje.year and mes_referencia == hoje.month:
        total_dias_mes = calendar.monthrange(ano_selecionado, mes_referencia)[1]
        dias_restantes = max(total_dias_mes - hoje.day, 0)
        if not despesa and dias_restantes > 0 and meta_mes > realizado_mes:
            necessario_dia = (meta_mes - realizado_mes) / dias_restantes
        elif not despesa and meta_mes <= realizado_mes:
            necessario_dia = 0

    df_periodo = comparar_mesmo_periodo(df, indicador, ano_selecionado)

    realizado_ant = None
    meta_ant = None
    var_real = None
    var_meta = None
    periodo_txt = None

    if not df_periodo.empty and len(df_periodo) >= 2:
        realizado_ant = df_periodo.iloc[0]["Realizado"]
        meta_ant = df_periodo.iloc[0]["Meta"]
        var_real = df_periodo.iloc[1]["Variação Realizado"]
        var_meta = df_periodo.iloc[1]["Variação Meta"]
        periodo_txt = df_periodo.iloc[1]["Período"]

    return {
        "hoje": hoje,
        "ano": ano_selecionado,
        "mes_referencia": mes_referencia,
        "nome_mes": MESES_MAPA.get(mes_referencia, str(mes_referencia)),
        "realizado_mes": realizado_mes,
        "meta_mes": meta_mes,
        "gap_mes": gap_mes,
        "ating_mes": ating_mes,
        "realizado_ano": realizado_ano,
        "meta_ano": meta_ano,
        "gap_ano": gap_ano,
        "ating_ano": ating_ano,
        "dias_restantes": dias_restantes,
        "necessario_dia": necessario_dia,
        "realizado_ant": realizado_ant,
        "meta_ant": meta_ant,
        "var_real": var_real,
        "var_meta": var_meta,
        "periodo_txt": periodo_txt,
        "despesa": despesa,
    }


def gerar_texto_explicativo_pdf(resumo, indicador):
    if not resumo:
        return "Sem dados suficientes para gerar o resumo."

    hoje_txt = resumo["hoje"].strftime("%d/%m/%Y")
    nome_mes = resumo["nome_mes"]
    ano = resumo["ano"]

    if resumo["despesa"]:
        texto = (
            f"Hoje é dia {hoje_txt}. No mês de {nome_mes}/{ano}, o realizado de {indicador.lower()} "
            f"foi de {fmt_brl(resumo['realizado_mes'])}, contra uma meta de {fmt_brl(resumo['meta_mes'])}, "
            f"resultando em gap de {fmt_brl(resumo['gap_mes'])} e atingimento de {fmt_pct(resumo['ating_mes'])}. "
        )
        if resumo["periodo_txt"] and resumo["realizado_ant"] is not None:
            texto += (
                f"No acumulado de {resumo['periodo_txt']} de {ano}, o realizado foi de "
                f"{fmt_brl(resumo['realizado_ano'])}. No mesmo período do ano anterior, "
                f"o realizado foi de {fmt_brl(resumo['realizado_ant'])}, contra uma meta de "
                f"{fmt_brl(resumo['meta_ant'])}. "
            )
            if resumo["var_real"] is not None:
                if resumo["var_real"] >= 0:
                    texto += f"A variação do realizado frente ao ano anterior foi positiva em {fmt_pct(resumo['var_real'])}. "
                else:
                    texto += f"A variação do realizado frente ao ano anterior foi negativa em {fmt_pct(abs(resumo['var_real']))}. "
    else:
        texto = (
            f"Hoje é dia {hoje_txt}. No mês de {nome_mes}/{ano}, o faturamento realizado está em "
            f"{fmt_brl(resumo['realizado_mes'])}, contra uma meta de {fmt_brl(resumo['meta_mes'])}, "
            f"o que representa um gap de {fmt_brl(resumo['gap_mes'])} e um atingimento de {fmt_pct(resumo['ating_mes'])}. "
        )
        if resumo["dias_restantes"] > 0:
            texto += f"Faltam {resumo['dias_restantes']} dias para o encerramento do mês. "
            if resumo["necessario_dia"] is not None and resumo["necessario_dia"] > 0:
                texto += (
                    f"Para atingir a meta mensal, o faturamento estimado necessário por dia é de "
                    f"{fmt_brl(resumo['necessario_dia'])}. "
                )
            elif resumo["necessario_dia"] == 0:
                texto += "A meta mensal já foi atingida. "
        if resumo["periodo_txt"] and resumo["realizado_ant"] is not None:
            texto += (
                f"No acumulado de {resumo['periodo_txt']} de {ano}, o faturamento realizado soma "
                f"{fmt_brl(resumo['realizado_ano'])}. No mesmo período do ano anterior, o faturamento "
                f"foi de {fmt_brl(resumo['realizado_ant'])}, contra uma meta de {fmt_brl(resumo['meta_ant'])}. "
            )
            if resumo["var_real"] is not None:
                if resumo["var_real"] >= 0:
                    texto += f"Isso representa um crescimento de {fmt_pct(resumo['var_real'])} no realizado. "
                else:
                    texto += f"Isso representa uma retração de {fmt_pct(abs(resumo['var_real']))} no realizado. "
            if resumo["var_meta"] is not None:
                if resumo["var_meta"] >= 0:
                    texto += f"A meta do período cresceu {fmt_pct(resumo['var_meta'])} em relação ao ano anterior. "
                else:
                    texto += f"A meta do período recuou {fmt_pct(abs(resumo['var_meta']))} em relação ao ano anterior. "

    texto += (
        f"No acumulado do ano, o realizado está em {fmt_brl(resumo['realizado_ano'])}, "
        f"contra uma meta de {fmt_brl(resumo['meta_ano'])}, com atingimento de "
        f"{fmt_pct(resumo['ating_ano'])}."
    )

    return texto


def grafico_realizado_meta(df_completa, ano, titulo=None):
    dados = df_completa[(df_completa["Mês"] != "TOTAL") & (df_completa["Realizado"].notna())].copy()
    if dados.empty:
        return None

    ultimo_mes = dados["Mês"].iloc[-1]
    titulo_final = titulo or f"Realizado x Meta — até {ultimo_mes}/{ano}"

    cores = [COR_VERDE if r >= m else COR_LARANJA for r, m in zip(dados["Realizado"], dados["Meta"])]

    fig = go.Figure()
    fig.add_bar(
        x=dados["Mês"],
        y=dados["Realizado"],
        name="Realizado",
        marker_color=cores,
        marker_cornerradius=4,
        text=[fmt_brl(v) for v in dados["Realizado"]],
        textposition="outside",
        textfont=dict(size=11),
        hovertemplate="<b>%{x}</b><br>Realizado: R$ %{y:,.0f}<extra></extra>",
    )
    fig.add_scatter(
        x=dados["Mês"],
        y=dados["Meta"],
        name="Meta",
        mode="lines+markers",
        line=dict(color=COR_LARANJA, width=3, dash="dot"),
        marker=dict(size=7),
        hovertemplate="<b>%{x}</b><br>Meta: R$ %{y:,.0f}<extra></extra>",
    )
    fig.update_layout(
        title=titulo_final,
        height=420,
        margin=dict(t=60, b=20, l=20, r=20),
        legend=dict(orientation="h", y=-0.18),
        yaxis_title="R$",
        bargap=0.22,
        uniformtext_minsize=8,
        uniformtext_mode="hide",
    )
    fig.update_yaxes(showgrid=True, gridcolor="#EAEAEA")
    return fig


def card_html(titulo, valor, delta=None):
    delta_html = ""
    if delta is not None and pd.notna(delta):
        cor = COR_VERDE if delta >= 0 else COR_LARANJA
        fundo = "#EAF7EF" if delta >= 0 else "#FFF3E8"
        sinal = "+" if delta >= 0 else ""
        delta_html = (
            f'<div class="kpi-delta" style="background:{fundo}; color:{cor};">'
            f'{sinal}{delta:.1%}'
            f'</div>'
        )

    return (
        '<div class="kpi-card">'
        f'<div class="kpi-label">{titulo}</div>'
        f'<div class="kpi-value">{valor}</div>'
        f'{delta_html}'
        '</div>'
    )


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

        candidatos = [
            ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),
            ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
            ("/usr/share/fonts/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
        ]

        for regular, bold in candidatos:
            if os.path.exists(regular) and os.path.exists(bold):
                self.add_font("DejaVu", "", regular)
                self.add_font("DejaVu", "B", bold)
                self._font_family = "DejaVu"
                break

        self._font_ready = True

    def fonte(self, estilo="", tamanho=10):
        self.configurar_fontes()
        self.set_font(self._font_family, estilo, tamanho)

    def safe(self, texto):
        txt = str(texto)
        txt = txt.replace("—", "-").replace("–", "-").replace("•", "-")
        txt = txt.replace("’", "'").replace("“", '"').replace("”", '"')
        if self._font_family == "Helvetica":
            return txt.encode("latin-1", "ignore").decode("latin-1")
        return txt

    def header(self):
        self.configurar_fontes()
        if os.path.exists(LOGO_ARQUIVO):
            try:
                self.image(LOGO_ARQUIVO, 10, 6, 34)
            except Exception:
                pass

        self.fonte("B", 13)
        self.cell(0, 9, self.safe("Relatório - Análise de Indicadores Mazola Ambiental"), align="C", new_x="LMARGIN", new_y="NEXT")
        self.fonte("", 9)
        self.cell(0, 5, self.safe(f"Indicador: {self.indicador} | Filial: {self.filial}"), align="C", new_x="LMARGIN", new_y="NEXT")
        self.cell(0, 5, self.safe(f"Gerado em: {agora_br().strftime('%d/%m/%Y %H:%M')}"), align="C", new_x="LMARGIN", new_y="NEXT")
        self.ln(4)
        self.set_draw_color(200, 200, 200)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(4)

    def footer(self):
        self.set_y(-15)
        self.fonte("", 8)
        self.set_text_color(128, 128, 128)
        self.cell(0, 10, self.safe(f"Página {self.page_no()}"), align="C")

    def secao(self, titulo):
        self.fonte("B", 12)
        self.set_text_color(0, 0, 0)
        self.ln(3)
        self.cell(0, 7, self.safe(titulo), new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def kpi_box(self, x, y, w, h, titulo, valor):
        self.set_draw_color(220, 220, 220)
        self.set_fill_color(248, 248, 248)
        self.rect(x, y, w, h, style="DF")
        self.set_xy(x + 3, y + 3)
        self.fonte("B", 8)
        self.set_text_color(80, 80, 80)
        self.multi_cell(w - 6, 4, self.safe(titulo), border=0)
        self.set_xy(x + 3, y + 12)
        self.fonte("B", 10)
        self.set_text_color(0, 0, 0)
        self.multi_cell(w - 6, 5, self.safe(valor), border=0)

    def resumo_explicativo(self, resumo, texto):
        self.secao("1. Resumo")
        self.fonte("", 10)
        self.set_text_color(0, 0, 0)

        intro = (
            f"Este relatório apresenta a análise do indicador {self.indicador}, considerando a base {self.filial}. "
            f"Os dados abaixo resumem o desempenho do período selecionado, comparando realizado, meta, gap, "
            f"atingimento e evolução em relação ao ano anterior."
        )
        self.multi_cell(0, 6, self.safe(intro))
        self.ln(3)

        y_inicial = self.get_y()
        self.kpi_box(12, y_inicial, 45, 20, "Realizado Ano", fmt_brl(resumo["realizado_ano"]))
        self.kpi_box(60, y_inicial, 45, 20, "Meta Ano", fmt_brl(resumo["meta_ano"]))
        self.kpi_box(108, y_inicial, 45, 20, "Gap Ano", fmt_brl(resumo["gap_ano"]))
        self.kpi_box(156, y_inicial, 42, 20, "Atingimento", fmt_pct(resumo["ating_ano"]))

        y2 = y_inicial + 25
        self.kpi_box(12, y2, 45, 20, f"Realizado {resumo['nome_mes']}", fmt_brl(resumo["realizado_mes"]))
        self.kpi_box(60, y2, 45, 20, f"Meta {resumo['nome_mes']}", fmt_brl(resumo["meta_mes"]))
        self.kpi_box(108, y2, 45, 20, "Dias Restantes", str(resumo["dias_restantes"]))
        self.kpi_box(156, y2, 42, 20, "Necessário/dia", fmt_brl(resumo["necessario_dia"]) if resumo["necessario_dia"] is not None else "-")

        self.set_y(y2 + 27)
        self.secao("2. Análise Explicativa")
        self.fonte("", 10)
        self.multi_cell(0, 6, self.safe(texto))

    def tabela_por_ano(self, df_completa):
        self.fonte("B", 9)
        self.set_fill_color(240, 240, 240)
        headers = ["Mês", "Realizado", "Meta", "Gap (R$)", "Atingimento"]
        widths = [30, 40, 40, 40, 35]

        for h, w in zip(headers, widths):
            self.cell(w, 7, self.safe(h), border=1, fill=True, align="C")
        self.ln()

        self.fonte("", 8)
        for _, row in df_completa.iterrows():
            is_total = row["Mês"] == "TOTAL"
            fill = is_total
            if is_total:
                self.set_fill_color(255, 243, 232)
                self.fonte("B", 8)
            else:
                self.set_fill_color(255, 255, 255)
                self.fonte("", 8)

            self.cell(widths[0], 6, self.safe(str(row["Mês"])), border=1, align="C", fill=fill)
            self.cell(widths[1], 6, fmt_brl(row["Realizado"]), border=1, align="R", fill=fill)
            self.cell(widths[2], 6, fmt_brl(row["Meta"]), border=1, align="R", fill=fill)
            self.cell(widths[3], 6, fmt_brl(row["Gap (R$)"]), border=1, align="R", fill=fill)
            self.cell(widths[4], 6, fmt_pct(row["Atingimento"]), border=1, align="R", fill=fill)
            self.ln()

    def tabela_mom(self, df_mom):
        self.fonte("B", 9)
        self.set_fill_color(240, 240, 240)
        headers = ["Mês", "Realizado", "Meta", "Gap", "Ating.", "MoM %"]
        widths = [36, 32, 32, 32, 24, 24]

        for h, w in zip(headers, widths):
            self.cell(w, 7, self.safe(h), border=1, fill=True, align="C")
        self.ln()

        self.fonte("", 8)
        for _, row in df_mom.iterrows():
            mom_txt = f"{row['MoM_%']:+.1f}%" if pd.notna(row["MoM_%"]) else "-"
            self.cell(widths[0], 6, self.safe(str(row["Mês"])), border=1)
            self.cell(widths[1], 6, fmt_brl(row["Realizado"]), border=1, align="R")
            self.cell(widths[2], 6, fmt_brl(row["META"]), border=1, align="R")
            self.cell(widths[3], 6, fmt_brl(row["Gap"]), border=1, align="R")
            self.cell(widths[4], 6, fmt_pct(row["Ating."]), border=1, align="R")
            self.cell(widths[5], 6, mom_txt, border=1, align="R")
            self.ln()

    def tabela_yoy(self, df_yoy):
        self.fonte("B", 9)
        self.set_fill_color(240, 240, 240)
        headers = ["Ano", "Realizado", "Meta", "Atingimento", "Meses", "YoY %"]
        widths = [20, 40, 40, 35, 20, 30]

        for h, w in zip(headers, widths):
            self.cell(w, 7, self.safe(h), border=1, fill=True, align="C")
        self.ln()

        self.fonte("", 8)
        for _, row in df_yoy.iterrows():
            yoy_str = f"{row['YoY_%']:+.1f}%" if pd.notna(row["YoY_%"]) else "-"
            self.cell(widths[0], 6, str(int(row["ANO"])), border=1, align="C")
            self.cell(widths[1], 6, fmt_brl(row["Realizado"]), border=1, align="R")
            self.cell(widths[2], 6, fmt_brl(row["Meta"]), border=1, align="R")
            self.cell(widths[3], 6, fmt_pct(row["Atingimento"]), border=1, align="R")
            self.cell(widths[4], 6, str(int(row["Meses c/ dado"])), border=1, align="C")
            self.cell(widths[5], 6, yoy_str, border=1, align="R")
            self.ln()

    def tabela_periodo(self, df_periodo):
        self.fonte("B", 9)
        self.set_fill_color(240, 240, 240)
        headers = ["Ano", "Período", "Realizado", "Meta", "Gap", "Ating.", "Var. Real."]
        widths = [16, 24, 32, 28, 26, 28, 26]

        for h, w in zip(headers, widths):
            self.cell(w, 7, self.safe(h), border=1, fill=True, align="C")
        self.ln()

        self.fonte("", 8)
        for _, row in df_periodo.iterrows():
            self.cell(widths[0], 6, str(int(row["Ano"])), border=1, align="C")
            self.cell(widths[1], 6, self.safe(str(row["Período"])), border=1, align="C")
            self.cell(widths[2], 6, fmt_brl(row["Realizado"]), border=1, align="R")
            self.cell(widths[3], 6, fmt_brl(row["Meta"]), border=1, align="R")
            self.cell(widths[4], 6, fmt_brl(row["Gap (R$)"]), border=1, align="R")
            self.cell(widths[5], 6, fmt_pct(row["Atingimento"]), border=1, align="R")
            self.cell(widths[6], 6, fmt_pct(row["Variação Realizado"]) if pd.notna(row["Variação Realizado"]) else "-", border=1, align="R")
            self.ln()

    def tabela_filiais(self, df_filiais):
        self.fonte("B", 9)
        self.set_fill_color(240, 240, 240)
        headers = ["Filial", "Realizado", "Meta", "Gap", "Atingimento"]
        widths = [55, 35, 35, 30, 30]

        for h, w in zip(headers, widths):
            self.cell(w, 7, self.safe(h), border=1, fill=True, align="C")
        self.ln()

        self.fonte("", 8)
        for _, row in df_filiais.iterrows():
            self.cell(widths[0], 6, self.safe(str(row["FILIAL"])), border=1)
            self.cell(widths[1], 6, fmt_brl(row["Realizado"]), border=1, align="R")
            self.cell(widths[2], 6, fmt_brl(row["Meta"]), border=1, align="R")
            self.cell(widths[3], 6, fmt_brl(row["Gap"]), border=1, align="R")
            self.cell(widths[4], 6, fmt_pct(row["Atingimento"]), border=1, align="R")
            self.ln()


def gerar_pdf(df, df_todas, indicador, filial, ano_selecionado):
    pdf = PDFRelatorio(indicador, filial)
    resumo = montar_resumo_pdf(df, indicador, ano_selecionado)
    texto_explicativo = gerar_texto_explicativo_pdf(resumo, indicador)

    pdf.add_page()
    if resumo:
        pdf.resumo_explicativo(resumo, texto_explicativo)
    else:
        pdf.secao("1. Resumo")
        pdf.fonte("", 10)
        pdf.multi_cell(0, 6, pdf.safe("Sem dados suficientes para gerar o resumo."))

    pdf.add_page()
    pdf.secao(f"3. Análise por Mês - Ano {ano_selecionado}")
    pdf.tabela_por_ano(tabela_completa_ano(df, ano_selecionado, indicador))

    pdf.add_page()
    pdf.secao("4. Variação Mês a Mês - Últimos 12 meses")
    df_mom = calcular_mom(df, indicador).sort_values("MÊS_ORDEM").tail(12).copy()
    pdf.tabela_mom(df_mom[["Mês", "Realizado", "META", "Gap", "Ating.", "MoM_%"]])

    pdf.add_page()
    pdf.secao("5. Comparativo Ano a Ano (YoY)")
    pdf.tabela_yoy(calcular_yoy(df, indicador))

    pdf.add_page()
    pdf.secao("6. Comparativo do Mesmo Período vs Ano Anterior")
    df_periodo = comparar_mesmo_periodo(df, indicador, ano_selecionado)
    if not df_periodo.empty:
        pdf.tabela_periodo(df_periodo)
    else:
        pdf.fonte("", 10)
        pdf.multi_cell(0, 6, pdf.safe("Sem dados suficientes para comparação do mesmo período."))

    if filial == "Geral":
        pdf.add_page()
        pdf.secao(f"7. Comparativo entre Filiais - {ano_selecionado}")
        df_filiais = comparativo_filiais(df_todas, ano_selecionado, indicador)
        if not df_filiais.empty:
            pdf.tabela_filiais(df_filiais)
        else:
            pdf.fonte("", 10)
            pdf.multi_cell(0, 6, pdf.safe("Sem dados suficientes para comparativo entre filiais."))

    return bytes(pdf.output())


def resumo_para_ia(d, indicador, filial, pergunta):
    mom = calcular_mom(d, indicador).tail(12).to_string(index=False)
    yoy = calcular_yoy(d, indicador).to_string(index=False)
    periodo = comparar_mesmo_periodo(d, indicador)
    periodo_txt = periodo.to_string(index=False) if not periodo.empty else "Sem dados"

    return f"""Você é um analista financeiro experiente. Analise os dados abaixo e responda em português brasileiro de forma clara e objetiva.

Indicador: {indicador} | Filial: {filial}
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


def diagnosticar_sem_dados(df_raw, indicador, filial):
    """
    Mostra no app o que existe na planilha quando o filtro selecionado não encontra dados.
    Isso ajuda a descobrir se o problema está no nome da filial, grupo, tipo de meta ou campo vazio.
    """
    cfg = INDICADORES[indicador]

    st.warning("Nenhum dado encontrado para os filtros selecionados.")
    st.caption("Diagnóstico automático: confira abaixo o que existe na planilha para esse indicador/filtro.")

    try:
        d0 = df_raw.copy()

        # Garante colunas básicas
        colunas_base = ["FILIAL", "TIPO", "GRUPO 01", "GRUPO 02", "GRUPO 03", "TIPO DE META"]
        faltando = [c for c in colunas_base if c not in d0.columns]
        if faltando:
            st.error(f"Colunas não encontradas na planilha: {faltando}")
            return

        # Filtra por TIPO, GRUPO 01, GRUPO 02 e GRUPO 03, mas sem filtrar filial e sem TIPO DE META
        d = d0.copy()
        if cfg.get("TIPO") is not None:
            d = aplicar_filtro_coluna(d, "TIPO", cfg.get("TIPO"))
        d = aplicar_filtro_coluna(d, "GRUPO 01", cfg.get("GRUPO 01"))
        d = aplicar_filtro_coluna(d, "GRUPO 02", cfg.get("GRUPO 02"))
        d = aplicar_filtro_coluna(d, "GRUPO 03", cfg.get("GRUPO 03"))

        if d.empty:
            st.error("Não encontrei nenhuma linha com a combinação de TIPO, GRUPO 01, GRUPO 02 e GRUPO 03 deste indicador.")
            st.write("Filtro esperado para o indicador selecionado:")
            st.json({
                "Indicador": indicador,
                "TIPO": cfg.get("TIPO"),
                "GRUPO 01": cfg.get("GRUPO 01"),
                "GRUPO 02": cfg.get("GRUPO 02"),
                "GRUPO 03": cfg.get("GRUPO 03"),
                "TIPO DE META": cfg.get("TIPO DE META"),
            })

            st.write("Combinações existentes na planilha para FATURAMENTO:")
            base_fat = d0[d0["GRUPO 01"].apply(normalizar_texto) == "FATURAMENTO"].copy()
            if not base_fat.empty:
                cols = ["TIPO", "GRUPO 01", "GRUPO 02", "GRUPO 03", "TIPO DE META"]
                st.dataframe(
                    base_fat[cols].drop_duplicates().sort_values(cols).head(100),
                    use_container_width=True,
                    hide_index=True,
                )
            return

        st.write("Linhas encontradas para este indicador sem considerar a filial:")
        cols_show = [c for c in ["FILIAL", "TIPO", "GRUPO 01", "GRUPO 02", "GRUPO 03", "TIPO DE META", "META", "VALOR REF 01"] if c in d.columns]
        st.dataframe(d[cols_show].head(50), use_container_width=True, hide_index=True)

        st.write("Filiais disponíveis para este indicador:")
        filiais_disp = (
            d["FILIAL"]
            .dropna()
            .astype(str)
            .str.strip()
            .drop_duplicates()
            .sort_values()
            .reset_index(drop=True)
        )
        st.dataframe(pd.DataFrame({"FILIAL encontrada": filiais_disp}), use_container_width=True, hide_index=True)

        # Verifica se a filial selecionada existe para esse indicador
        d_filial = d[d["FILIAL"].apply(normalizar_texto) == normalizar_texto(filial)]
        if d_filial.empty:
            st.error(
                f"O indicador existe na planilha, mas não encontrei linhas para a filial selecionada: {filial}. "
                "Confira se o nome da filial na planilha está igual ao nome do filtro."
            )
            return

        # Se achou por filial, então o problema pode ser TIPO DE META
        d_meta = aplicar_filtro_coluna(d_filial, "TIPO DE META", cfg.get("TIPO DE META"))
        if d_meta.empty:
            st.error(
                f"Encontrei linhas para a filial {filial}, mas nenhuma com TIPO DE META = {cfg.get('TIPO DE META')}."
            )
            st.write("TIPO DE META encontrado para essa filial/indicador:")
            st.dataframe(
                d_filial[["TIPO DE META"]].drop_duplicates(),
                use_container_width=True,
                hide_index=True,
            )
            return

    except Exception as e:
        st.error(f"Erro ao gerar diagnóstico: {e}")


def filtrar_com_fallback_incremental(df_raw, indicador, filial):
    """
    Aplica o filtro normal.
    Se não encontrar dados, retorna DataFrame vazio.
    O diagnóstico será exibido depois.
    """
    return filtrar(df_raw, indicador, filial)


# =========================
# CSS E CABEÇALHO
# =========================
st.markdown(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=Montserrat:wght@600;700&display=swap');

.titulo-mazola {
    font-family:'Montserrat', sans-serif;
    font-size:34px;
    font-weight:700;
    color:#F26522;
    margin:0;
    line-height:1.1;
}

.subtitulo-mazola {
    font-family:'Montserrat', sans-serif;
    font-size:15px;
    color:#00A350;
    margin:4px 0 0 0;
    line-height:1.1;
}

.logo-alinhada img {
    display:block;
    margin-top:0px;
}

.texto-cabecalho {
    padding-top:28px;
    margin-left:-70px;
}

.kpi-card {
    position: relative;
    border:1px solid #E6E6E6;
    border-radius:14px;
    padding:14px 16px 40px 16px;
    background:#FFFFFF;
    min-height:112px;
    box-shadow:0 1px 3px rgba(0,0,0,0.04);
}

.kpi-label {
    font-size:12px;
    color:#404040;
    margin-bottom:8px;
    white-space:nowrap;
}

.kpi-value {
    font-size:18px;
    font-weight:700;
    color:#111827;
    line-height:1.15;
    white-space:nowrap;
    font-variant-numeric: tabular-nums;
}

.kpi-delta {
    position:absolute;
    left:16px;
    bottom:12px;
    display:inline-block;
    padding:4px 9px;
    border-radius:999px;
    font-size:12px;
    font-weight:600;
    width:fit-content;
}
</style>
""",
    unsafe_allow_html=True,
)

col_logo, col_titulo = st.columns([1, 5], vertical_alignment="center", gap="small")

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


# =========================
# SIDEBAR
# =========================
with st.sidebar:
    st.header("⚙️ Filtros")
    arquivo = st.file_uploader("Upload da planilha (.xlsx)", type=["xlsx"])
    st.divider()
    indicador = st.selectbox("Indicador", list(INDICADORES.keys()))
    filial = st.selectbox("Filial", ["Geral"] + FILIAIS_REAIS)
    st.divider()
    st.caption("v4.5 — Bot Indicadores")

if not arquivo:
    st.info("👈 Faça upload da planilha na barra lateral para começar.")
    st.stop()


df_raw = carregar(arquivo)
df = filtrar_com_fallback_incremental(df_raw, indicador, filial)
df_todas_unidades = filtrar(df_raw, indicador, "Geral")

if df.empty:
    diagnosticar_sem_dados(df_raw, indicador, filial)
    st.stop()


tab0, tab1, tab2, tab3, tab4 = st.tabs([
    "📊 Dashboard",
    "📅 Por Ano",
    "📈 MoM",
    "🔁 YoY",
    "🤖 Insights IA"
])


# =========================
# ABA DASHBOARD
# =========================
with tab0:
    st.subheader(f"Dashboard — {indicador} | {filial}")

    anos = sorted(df["ANO"].dropna().unique())
    ano_kpi = int(anos[-1])

    base_kpi = df[df["ANO"] == ano_kpi].copy()

    if eh_moto_margem(indicador):
        compra_total = base_kpi["COMPRA_CALC"].sum()
        faturamento_total = base_kpi["FATURAMENTO_MOTO_CALC"].sum()
        margem_total = base_kpi["MARGEM_BRUTA_CALC"].sum()
        tx_sucesso = margem_total / faturamento_total if faturamento_total > 0 else None
        meta_pct = base_kpi["META_PERCENTUAL_CALC"].mean() if not base_kpi.empty else None

        c1, c2, c3, c4, c5 = st.columns(5)
        with c1:
            st.markdown(card_html("Compra Ano", fmt_brl(compra_total)), unsafe_allow_html=True)
        with c2:
            st.markdown(card_html("Faturamento Ano", fmt_brl(faturamento_total)), unsafe_allow_html=True)
        with c3:
            st.markdown(card_html("Margem Bruta", fmt_brl(margem_total)), unsafe_allow_html=True)
        with c4:
            st.markdown(card_html("Tx. Sucesso", fmt_pct(tx_sucesso)), unsafe_allow_html=True)
        with c5:
            st.markdown(card_html("Meta", fmt_pct(meta_pct)), unsafe_allow_html=True)

        st.divider()
        df_dashboard_ano = tabela_pneus_moto_ano(df, ano_kpi)
        dados_chart = df_dashboard_ano[(df_dashboard_ano["Mês"] != "TOTAL") & (df_dashboard_ano["Faturamento"].notna())]
        if not dados_chart.empty:
            fig_dash = go.Figure()
            fig_dash.add_bar(x=dados_chart["Mês"], y=dados_chart["Faturamento"], name="Faturamento", marker_color=COR_VERDE)
            fig_dash.add_bar(x=dados_chart["Mês"], y=dados_chart["Compra"], name="Compra", marker_color=COR_LARANJA)
            fig_dash.update_layout(title=f"Pneus Moto - Compra x Faturamento — {ano_kpi}", height=420, barmode="group", legend=dict(orientation="h", y=-0.18))
            st.plotly_chart(fig_dash, use_container_width=True, key="grafico_dashboard_moto")
    else:
        periodo_cmp = comparar_mesmo_periodo(df, indicador, ano_kpi)
        realizado_total = base_kpi["REALIZADO_CALC"].sum()
        meta_total = base_kpi["META_CALC"].sum()

        if eh_despesa(indicador):
            gap_total = meta_total - realizado_total
            ating_total = meta_total / realizado_total if realizado_total > 0 else None
        else:
            gap_total = realizado_total - meta_total
            ating_total = realizado_total / meta_total if meta_total > 0 else None

        if not periodo_cmp.empty and len(periodo_cmp) == 2:
            realizado_ytd = periodo_cmp.iloc[1]["Realizado"]
            delta_ytd = periodo_cmp.iloc[1]["Variação Realizado"]
            periodo_label = periodo_cmp.iloc[1]["Período"]
        else:
            realizado_ytd = None
            delta_ytd = None
            periodo_label = "-"

        c1, c2, c3, c4, c5 = st.columns(5)
        with c1:
            st.markdown(card_html("Realizado Ano", fmt_brl(realizado_total)), unsafe_allow_html=True)
        with c2:
            st.markdown(card_html("Meta Ano", fmt_brl(meta_total)), unsafe_allow_html=True)
        with c3:
            st.markdown(card_html("Gap Ano", fmt_brl(gap_total)), unsafe_allow_html=True)
        with c4:
            st.markdown(card_html("Atingimento da meta", fmt_pct(ating_total)), unsafe_allow_html=True)
        with c5:
            st.markdown(card_html(f"YTD {periodo_label}", fmt_brl(realizado_ytd) if realizado_ytd is not None else "-", delta_ytd), unsafe_allow_html=True)

        st.divider()

        df_dashboard_ano = tabela_completa_ano(df, ano_kpi, indicador)
        dados_chart = df_dashboard_ano[df_dashboard_ano["Mês"] != "TOTAL"].dropna(subset=["Realizado"])
        if not dados_chart.empty:
            ultimo_mes = dados_chart["Mês"].iloc[-1]
            fig_dash = grafico_realizado_meta(df_dashboard_ano, ano_kpi, titulo=f"Realizado x Meta — até {ultimo_mes}/{ano_kpi}")
            st.plotly_chart(fig_dash, use_container_width=True, key="grafico_dashboard")

    st.subheader(f"{indicador} — {filial} · Comparativo Ano a Ano")
    df_yoy_dashboard = calcular_yoy(df, indicador)
    st.dataframe(
        df_yoy_dashboard.style
        .format({
            "Realizado": "R$ {:,.0f}",
            "Meta": "R$ {:,.0f}",
            "Atingimento": "{:.1%}",
            "YoY_%": lambda v: f"{v:+.1f}%" if pd.notna(v) else "—",
            "Meses c/ dado": "{:.0f}",
        })
        .map(cor_variacao, subset=["YoY_%"])
        .map(cor_atingimento, subset=["Atingimento"]),
        use_container_width=True,
        hide_index=True,
    )


# =========================
# ABA POR ANO
# =========================
with tab1:
    col_title, col_btn = st.columns([3, 1])
    with col_title:
        st.subheader(f"{indicador} — {filial}")

    anos = sorted(df["ANO"].dropna().unique())
    ano_selecionado = st.selectbox("Selecione o ano", anos, index=len(anos) - 1)
    if eh_moto_margem(indicador):
        df_completa = tabela_pneus_moto_ano(df, ano_selecionado)
    else:
        df_completa = tabela_completa_ano(df, ano_selecionado, indicador)

    with col_btn:
        st.write("")
        with st.spinner("Gerando PDF..."):
            pdf_bytes = gerar_pdf(df, df_todas_unidades, indicador, filial, ano_selecionado)
        st.download_button(
            label="📄 Baixar PDF",
            data=pdf_bytes,
            file_name=f"relatorio_{indicador}_{filial}_{agora_br().strftime('%Y%m%d')}.pdf",
            mime="application/pdf",
            use_container_width=True,
        )

    if eh_moto_margem(indicador):
        def destacar_total_moto(row):
            if str(row["Mês"]).startswith("TOTAL"):
                return ["background-color: #FFF3E8; font-weight: bold; border-top: 2px solid #F26522;" for _ in row]
            return ["" for _ in row]

        st.dataframe(
            df_completa.style
            .apply(destacar_total_moto, axis=1)
            .format({
                "Meta": lambda v: fmt_pct(v) if pd.notna(v) else "—",
                "Compra": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                "Faturamento": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                "Margem Bruta": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                "Tx. Sucesso": lambda v: fmt_pct(v) if pd.notna(v) else "—",
                "Acumulado": lambda v: fmt_brl(v) if pd.notna(v) else "—",
            }),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.dataframe(
            df_completa.style
            .format({
                "Realizado": lambda v: fmt_brl(v) if pd.notna(v) else "",
                "Meta": lambda v: fmt_brl(v) if pd.notna(v) else "",
                "Gap (R$)": lambda v: f"R$ {v:+,.0f}".replace(",", ".") if pd.notna(v) else "",
                "Atingimento": lambda v: f"{v:.0%}" if pd.notna(v) else "",
            })
            .map(lambda v: cor_gap_valor(v, eh_despesa(indicador)), subset=["Gap (R$)"])
            .map(cor_atingimento, subset=["Atingimento"]),
            use_container_width=True,
            hide_index=True,
        )

    st.divider()
    if eh_moto_margem(indicador):
        dados_chart = df_completa[(df_completa["Mês"] != "TOTAL") & (df_completa["Faturamento"].notna())]
        if not dados_chart.empty:
            fig_ano = go.Figure()
            fig_ano.add_bar(x=dados_chart["Mês"], y=dados_chart["Faturamento"], name="Faturamento", marker_color=COR_VERDE)
            fig_ano.add_bar(x=dados_chart["Mês"], y=dados_chart["Compra"], name="Compra", marker_color=COR_LARANJA)
            fig_ano.update_layout(title=f"Pneus Moto - Compra x Faturamento — {ano_selecionado}", height=420, barmode="group", legend=dict(orientation="h", y=-0.18))
            st.plotly_chart(fig_ano, use_container_width=True, key="grafico_por_ano_moto")
    else:
        fig_ano = grafico_realizado_meta(df_completa, ano_selecionado)
        if fig_ano is not None:
            st.plotly_chart(fig_ano, use_container_width=True, key="grafico_por_ano")


# =========================
# ABA MOM
# =========================
with tab2:
    st.subheader(f"{indicador} — {filial} · Variação Mês a Mês")
    df_mom = calcular_mom(df, indicador).sort_values("MÊS_ORDEM").copy()

    linhas_finais = []
    for ano in sorted(df_mom["ANO"].dropna().unique()):
        base_ano = df_mom[df_mom["ANO"] == ano].copy()
        linhas_finais.append(base_ano[["ANO", "MÊS", "Mês", "META", "Realizado", "Gap", "Ating.", "MoM_%"]])

        total_meta = base_ano["META"].sum()
        total_realizado = base_ano["Realizado"].sum()
        if eh_despesa(indicador):
            total_gap = total_meta - total_realizado
            total_ating = total_meta / total_realizado if total_realizado > 0 else None
        else:
            total_gap = total_realizado - total_meta
            total_ating = total_realizado / total_meta if total_meta > 0 else None

        linhas_finais.append(pd.DataFrame([{
            "ANO": ano,
            "MÊS": None,
            "Mês": f"TOTAL {ano}",
            "META": total_meta,
            "Realizado": total_realizado,
            "Gap": total_gap,
            "Ating.": total_ating,
            "MoM_%": None,
        }]))

    df_mom_tela = pd.concat(linhas_finais, ignore_index=True)

    def destacar_total(row):
        if str(row["Mês"]).startswith("TOTAL"):
            return ["background-color: #FFF3E8; font-weight: bold; border-top: 2px solid #F26522;" for _ in row]
        return ["" for _ in row]

    st.dataframe(
        df_mom_tela.style
        .apply(destacar_total, axis=1)
        .format({
            "ANO": lambda v: f"{int(v)}" if pd.notna(v) else "",
            "MÊS": lambda v: f"{int(v)}" if pd.notna(v) else "",
            "META": lambda v: fmt_brl(v) if pd.notna(v) else "—",
            "Realizado": lambda v: fmt_brl(v) if pd.notna(v) else "—",
            "Gap": lambda v: f"R$ {v:+,.0f}".replace(",", ".") if pd.notna(v) else "—",
            "Ating.": lambda v: f"{v:.0%}" if pd.notna(v) else "—",
            "MoM_%": lambda v: f"{v:+.1f}%" if pd.notna(v) else "—",
        })
        .map(lambda v: cor_gap_valor(v, eh_despesa(indicador)), subset=["Gap"])
        .map(cor_atingimento, subset=["Ating."])
        .map(cor_variacao, subset=["MoM_%"]),
        use_container_width=True,
        hide_index=True,
    )


# =========================
# ABA YOY
# =========================
with tab3:
    st.subheader(f"{indicador} — {filial} · Comparativo Ano a Ano")
    df_yoy = calcular_yoy(df, indicador)

    st.dataframe(
        df_yoy.style
        .format({
            "Realizado": "R$ {:,.0f}",
            "Meta": "R$ {:,.0f}",
            "Atingimento": "{:.1%}",
            "YoY_%": lambda v: f"{v:+.1f}%" if pd.notna(v) else "—",
            "Meses c/ dado": "{:.0f}",
        })
        .map(cor_variacao, subset=["YoY_%"])
        .map(cor_atingimento, subset=["Atingimento"]),
        use_container_width=True,
        hide_index=True,
    )

    st.divider()
    st.subheader("Mesmo período x ano anterior")
    df_periodo_tela = comparar_mesmo_periodo(df, indicador)

    if not df_periodo_tela.empty:
        st.dataframe(
            df_periodo_tela.style
            .format({
                "Realizado": "R$ {:,.0f}",
                "Meta": "R$ {:,.0f}",
                "Gap (R$)": "R$ {:,.0f}",
                "Atingimento": "{:.1%}",
                "Variação Realizado": lambda v: f"{v:+.1%}" if pd.notna(v) else "—",
                "Variação Meta": lambda v: f"{v:+.1%}" if pd.notna(v) else "—",
            })
            .map(cor_variacao, subset=["Variação Realizado", "Variação Meta"])
            .map(cor_atingimento, subset=["Atingimento"]),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("Sem dados suficientes para comparar o mesmo período.")

    if filial == "Geral":
        st.divider()
        anos_filial = sorted(df_todas_unidades["ANO"].dropna().unique())
        ano_base_filial = st.selectbox("Ano do comparativo entre filiais", anos_filial, index=len(anos_filial) - 1, key="ano_filiais")
        st.subheader(f"Comparativo entre filiais — {ano_base_filial}")

        comp_filiais = comparativo_filiais(df_todas_unidades, ano_base_filial, indicador)
        st.dataframe(
            comp_filiais.style
            .format({
                "Realizado": "R$ {:,.0f}",
                "Meta": "R$ {:,.0f}",
                "Gap": "R$ {:,.0f}",
                "Atingimento": "{:.1%}",
            })
            .map(lambda v: cor_gap_valor(v, eh_despesa(indicador)), subset=["Gap"])
            .map(cor_atingimento, subset=["Atingimento"]),
            use_container_width=True,
            hide_index=True,
        )

        fig_filiais = go.Figure()
        fig_filiais.add_bar(
            x=comp_filiais["FILIAL"],
            y=comp_filiais["Realizado"],
            name="Realizado",
            marker_color=COR_VERDE,
            marker_cornerradius=4,
            text=[fmt_brl(v) for v in comp_filiais["Realizado"]],
            textposition="outside",
        )
        fig_filiais.add_scatter(
            x=comp_filiais["FILIAL"],
            y=comp_filiais["Meta"],
            name="Meta",
            mode="lines+markers",
            line=dict(color=COR_LARANJA, width=3, dash="dot"),
        )
        fig_filiais.update_layout(height=420, margin=dict(t=30, b=20, l=20, r=20), legend=dict(orientation="h", y=-0.15))
        st.plotly_chart(fig_filiais, use_container_width=True, key="grafico_filiais")


# =========================
# ABA IA
# =========================
with tab4:
    col_chat1, col_chat2 = st.columns([4, 1])
    with col_chat1:
        st.subheader("🤖 Pergunte ao assistente")
    with col_chat2:
        if st.button("🗑️ Limpar chat", use_container_width=True):
            st.session_state.chat = []
            st.rerun()

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
                    if client is None:
                        raise Exception("Cliente Anthropic não configurado.")

                    prompt = resumo_para_ia(df, indicador, filial, pergunta)
                    resp = client.messages.create(
                        model="claude-sonnet-4-20250514",
                        max_tokens=1500,
                        messages=[{"role": "user", "content": prompt}],
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
