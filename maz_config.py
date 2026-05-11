from zoneinfo import ZoneInfo

# ── Sessão e autenticação ──────────────────────────────────────────────────────
LIMITE_SESSAO_MINUTOS = 60
LIMITE_TENTATIVAS = 3
TEMPO_BLOQUEIO_MINUTOS = 30

# ── Filiais ────────────────────────────────────────────────────────────────────
FILIAL_CODIGO_NOME = {
    "1": "VALINHOS/SP",
    "4": "CANOAS/RS",
    "5": "CURITIBA/PR",
    "6": "DUQUE DE CAXIAS/RJ",
}
FILIAIS_REAIS = ["CANOAS/RS", "CURITIBA/PR", "DUQUE DE CAXIAS/RJ", "VALINHOS/SP"]

# ── Cores ──────────────────────────────────────────────────────────────────────
COR_LARANJA = "#F26522"
COR_VERDE   = "#00A350"
COR_AZUL    = "#0078D4"
COR_AMARELO = "#FFB900"
COR_VERMELHO = "#D13438"
COR_CINZA   = "#A19F9D"

CORES_MAZOLA = {
    "verde":    COR_VERDE,
    "laranja":  COR_LARANJA,
    "azul":     COR_AZUL,
    "amarelo":  COR_AMARELO,
    "vermelho": COR_VERMELHO,
    "cinza":    COR_CINZA,
}

# ── Outros ─────────────────────────────────────────────────────────────────────
QUALQUER = "__ANY__"
LOGO_ARQUIVO = "MazolaCertificado.ico"
TZ_BR = ZoneInfo("America/Sao_Paulo")
LIMITE_DESPESA_GERAL_PADRAO = 0.71
META_RESULTADO_FINANCEIRO_PADRAO = None
SCOPES_DRIVE = ["https://www.googleapis.com/auth/drive.readonly"]
