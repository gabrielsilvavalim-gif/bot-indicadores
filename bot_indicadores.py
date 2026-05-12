import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from anthropic import Anthropic
from fpdf import FPDF
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
import calendar
import os
import unicodedata
import smtplib
from email.message import EmailMessage
import io
import hmac
import time
import logging
try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload
except ModuleNotFoundError:
    service_account = None
    build = None
    MediaIoBaseDownload = None

# bcrypt é opcional — se estiver instalado, suporta hashes de senha mais fortes.
# Se não estiver, o app continua funcionando com senhas em texto puro nos Secrets.
try:
    import bcrypt
    BCRYPT_DISPONIVEL = True
except ModuleNotFoundError:
    bcrypt = None
    BCRYPT_DISPONIVEL = False

st.set_page_config(
    page_title="Mazola Indicadores",
    page_icon="MazolaCertificado.ico",
    layout="wide",
    initial_sidebar_state="collapsed"
)







# =========================
# CONTROLE DE ACESSO POR USUÁRIO E SENHA
# =========================
LIMITE_SESSAO_MINUTOS = 60        # Sessão expira após 60 min sem atividade
LIMITE_TENTATIVAS = 3             # Bloqueia após 3 tentativas erradas
TEMPO_BLOQUEIO_MINUTOS = 30       # Bloqueio dura 30 minutos


FILIAL_CODIGO_NOME = {
    "1": "VALINHOS/SP",
    "4": "CANOAS/RS",
    "5": "CURITIBA/PR",
    "6": "DUQUE DE CAXIAS/RJ",
}
FILIAIS_REAIS = ["CANOAS/RS", "CURITIBA/PR", "DUQUE DE CAXIAS/RJ", "VALINHOS/SP"]


def _verificar_senha(senha_digitada, senha_armazenada):
    if senha_armazenada is None:
        return False

    senha_armazenada_str = str(senha_armazenada).strip()
    senha_digitada_str = str(senha_digitada).strip()

    if BCRYPT_DISPONIVEL and senha_armazenada_str.startswith(("$2a$", "$2b$", "$2y$")):
        try:
            return bcrypt.checkpw(
                senha_digitada_str.encode("utf-8"),
                senha_armazenada_str.encode("utf-8"),
            )
        except Exception:
            return False

    return hmac.compare_digest(senha_digitada_str, senha_armazenada_str)


@st.cache_data(ttl=120, show_spinner=False)
def _carregar_base_login():
    """Carrega a planilha BaseLogin.xlsx do Google Drive. Cache de 2 minutos."""
    try:
        credentials = service_account.Credentials.from_service_account_info(
            st.secrets["gcp_service_account"],
            scopes=["https://www.googleapis.com/auth/drive.readonly"],
        )
        service = build("drive", "v3", credentials=credentials)

        pasta_id = str(st.secrets.get("DRIVE_FOLDER_ID", "")).strip()
        nome_query = "BaseLogin.xlsx"
        query_partes = [f"name = '{nome_query}'", "trashed = false"]
        if pasta_id:
            query_partes.append(f"'{pasta_id}' in parents")

        resultado = service.files().list(
            q=" and ".join(query_partes),
            spaces="drive",
            fields="files(id, name)",
            pageSize=1,
        ).execute()

        arquivos = resultado.get("files", [])
        if not arquivos:
            return None

        request = service.files().get_media(fileId=arquivos[0]["id"])
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        buf.seek(0)
        return pd.read_excel(buf, dtype=str)
    except Exception:
        return None


def _buscar_usuario_login(login_digitado):
    """Busca o usuário na BaseLogin e retorna a linha como dict ou None."""
    df = _carregar_base_login()

    if df is None:
        st.error("Não foi possível carregar a BaseLogin.xlsx do Drive. Verifique se o arquivo existe na pasta correta.")
        st.stop()

    if df.empty:
        st.error("A planilha BaseLogin.xlsx está vazia.")
        st.stop()

    # Usa posição das colunas (A=0, B=1, ...) para não depender do nome do cabeçalho
    def _val(row, idx, padrao=""):
        try:
            v = row.iloc[idx]
            return "" if pd.isna(v) else str(v).strip()
        except Exception:
            return padrao

    login_lower = str(login_digitado).strip().lower()
    for _, row in df.iterrows():
        if _val(row, 0).lower() == login_lower:
            return {
                "login":  _val(row, 0),
                "senha":  _val(row, 1),
                "tipo":   _val(row, 2, "MAZOLA").upper(),
                "adm":    _val(row, 3, "NAO").upper(),
                "filial": _val(row, 4, "T").upper(),
                "ativo":  _val(row, 7, "SIM").upper(),
            }
    return None


def filiais_permitidas_usuario():
    """Retorna a lista de filiais que o usuário logado pode visualizar."""
    return st.session_state.get("filiais_permitidas", ["Geral"] + FILIAIS_REAIS)


def verificar_senha_acesso():
    """Autentica o usuário lendo credenciais e permissões da BaseLogin.xlsx no Drive."""

    # ---- Verifica se já está autenticado e se a sessão ainda é válida ----
    if st.session_state.get("acesso_liberado", False):
        ultimo_acesso = st.session_state.get("ultimo_acesso", 0)
        if time.time() - ultimo_acesso > LIMITE_SESSAO_MINUTOS * 60:
            for chave in ["acesso_liberado", "usuario_logado", "ultimo_acesso", "filiais_permitidas", "usuario_adm"]:
                st.session_state.pop(chave, None)
            st.warning("⏰ Sua sessão expirou por inatividade. Faça login novamente.")
        else:
            st.session_state["ultimo_acesso"] = time.time()
            return True

    # ---- Inicializa controles de tentativas ----
    if "tentativas_login" not in st.session_state:
        st.session_state["tentativas_login"] = 0
    if "bloqueado_ate" not in st.session_state:
        st.session_state["bloqueado_ate"] = 0

    # ---- CSS da tela de login ----
    st.markdown(
        """
        <style>
            [data-testid="stSidebar"] {
                display: none;
            }

            .block-container {
                padding-top: 3rem !important;
                padding-bottom: 2rem !important;
                max-width: 100% !important;
                background:
                    radial-gradient(circle at top left, rgba(242,101,34,0.07), transparent 24%),
                    radial-gradient(circle at bottom right, rgba(0,163,80,0.07), transparent 26%),
                    linear-gradient(135deg, #FFFFFF 0%, #FAFAFA 100%);
            }

            .login-bg {
                min-height: auto;
                display: block;
                padding: 0;
                background: transparent;
                border-radius: 0;
            }

                        .login-logo-wrap {
                display: flex;
                align-items: center;
                justify-content: center;
                margin-bottom: 12px;
            }

            .login-badge {
                display: inline-flex;
                align-items: center;
                justify-content: center;
                gap: 8px;
                padding: 7px 12px;
                border-radius: 999px;
                background: rgba(242,101,34,0.09);
                color: #F26522;
                font-size: 12px;
                font-weight: 850;
                margin: 0 auto 12px auto;
                border: 1px solid rgba(242,101,34,0.18);
            }

            .login-title {
                text-align: center;
                font-size: 34px;
                color: #111827;
                font-weight: 950;
                margin: 4px 0 8px 0;
                line-height: 1.08;
            }

            .login-subtitle {
                text-align: center;
                font-size: 14.5px;
                color: #6B7280;
                font-weight: 600;
                line-height: 1.45;
                margin-bottom: 22px;
            }

            .login-security {
                display: flex;
                align-items: flex-start;
                gap: 8px;
                margin-top: 16px;
                padding: 11px 12px;
                border-radius: 14px;
                background: #F9FAFB;
                border: 1px solid #E5E7EB;
                color: #6B7280;
                font-size: 12px;
                font-weight: 650;
                line-height: 1.35;
            }

            div[data-testid="stForm"] {
                border: none !important;
                padding: 0 !important;
                box-shadow: none !important;
                background: transparent !important;
            }

            div[data-testid="stTextInput"] label p {
                font-weight: 800 !important;
                font-size: 12px !important;
                color: #374151 !important;
            }

            /* Campo externo do input: remove sombra/dupla borda do Streamlit */
            div[data-testid="stTextInput"] [data-baseweb="input"] {
                border: 1.4px solid #D1D5DB !important;
                border-radius: 14px !important;
                background: #FFFFFF !important;
                box-shadow: none !important;
                outline: none !important;
                transition: border-color .12s ease !important;
            }

            div[data-testid="stTextInput"] [data-baseweb="input"]:focus-within {
                border: 1.4px solid #F26522 !important;
                box-shadow: none !important;
                outline: none !important;
            }

            div[data-testid="stTextInput"] [data-baseweb="input"] > div {
                background: transparent !important;
                box-shadow: none !important;
                outline: none !important;
                border: none !important;
            }

            div[data-testid="stTextInput"] input {
                border: none !important;
                border-radius: 14px !important;
                background: transparent !important;
                min-height: 45px !important;
                font-size: 14px !important;
                font-weight: 600 !important;
                padding-left: 12px !important;
                box-shadow: none !important;
                outline: none !important;
                background-image: none !important;
                -webkit-appearance: none !important;
            }

            div[data-testid="stTextInput"] input:focus,
            div[data-testid="stTextInput"] input:active,
            div[data-testid="stTextInput"] input:focus-visible {
                border: none !important;
                box-shadow: none !important;
                outline: none !important;
                background-image: none !important;
            }

            /* Remove a mensagem "Press Enter to submit form" */
            div[data-testid="InputInstructions"] {
                display: none !important;
            }


            div[data-testid="stTextInput"] {
                margin-bottom: 12px !important;
            }

            div[data-testid="stTextInput"] label {
                margin-bottom: 4px !important;
            }

            div[data-testid="stFormSubmitButton"] button {
                background: linear-gradient(90deg, #F26522, #F47B3E) !important;
                color: #FFFFFF !important;
                border: none !important;
                border-radius: 14px !important;
                min-height: 46px !important;
                font-weight: 900 !important;
                font-size: 15px !important;
                box-shadow: 0 10px 22px rgba(242,101,34,0.24) !important;
                transition: all .15s ease !important;
            }

            div[data-testid="stFormSubmitButton"] button:hover {
                transform: translateY(-1px);
                box-shadow: 0 13px 26px rgba(242,101,34,0.30) !important;
                filter: brightness(1.02);
            }

            .login-footer {
                text-align: center;
                color: #9CA3AF;
                font-size: 11px;
                margin-top: 12px;
                font-weight: 600;
            }

            .login-blocked-card {
                width: min(520px, 100%);
                background: #FFFFFF;
                border: 1px solid #E5E7EB;
                border-radius: 22px;
                padding: 34px 36px;
                box-shadow: 0 18px 48px rgba(17,24,39,0.10);
                text-align: center;
            }

            .login-alert {
                background: rgba(242,101,34,0.10);
                color: #9A3412;
                border-left: 5px solid #F26522;
                border-radius: 14px;
                padding: 13px 14px;
                font-size: 14px;
                font-weight: 700;
                text-align: left;
            }
        

/* Padronização final dos KPIs — mesmo tamanho em todos os cenários */
div[data-testid="column"] .kpi-card {
    width:100% !important;
    height:118px !important;
    min-height:118px !important;
    max-height:118px !important;
}

div[data-testid="stMetric"] {
    background:#FFFFFF !important;
    border:1px solid #E5E7EB !important;
    border-radius:16px !important;
    padding:16px 16px 14px 16px !important;
    height:118px !important;
    min-height:118px !important;
    max-height:118px !important;
    box-sizing:border-box !important;
    box-shadow:0 4px 14px rgba(17,24,39,0.07) !important;
    position:relative !important;
    overflow:hidden !important;
}

div[data-testid="stMetric"]::before {
    content:"";
    position:absolute;
    left:0;
    top:0;
    height:5px;
    width:100%;
    background:linear-gradient(90deg, #F26522 0%, #00A350 100%) !important;
}

div[data-testid="stMetricLabel"] {
    min-height:28px !important;
    max-height:28px !important;
}

div[data-testid="stMetricLabel"] p {
    font-size:11px !important;
    color:#6B7280 !important;
    font-weight:850 !important;
    text-transform:uppercase !important;
    letter-spacing:.02em !important;
    line-height:1.15 !important;
    margin:0 !important;
    overflow:hidden !important;
    display:-webkit-box !important;
    -webkit-line-clamp:2 !important;
    -webkit-box-orient:vertical !important;
}

div[data-testid="stMetricValue"] div {
    font-size:clamp(17px, 1.35vw, 22px) !important;
    font-weight:900 !important;
    color:#111827 !important;
    white-space:nowrap !important;
    overflow:hidden !important;
    text-overflow:ellipsis !important;
    font-variant-numeric:tabular-nums !important;
}

div[data-testid="stMetricDelta"] {
    min-height:20px !important;
    max-height:20px !important;
    font-size:11px !important;
    font-weight:850 !important;
}



/* Linha auxiliar dentro dos KPIs */
.kpi-note {
    position:absolute;
    left:16px;
    right:16px;
    bottom:10px;
    font-size:11px;
    font-weight:800;
    line-height:1.15;
    color:#6B7280;
    white-space:nowrap;
    overflow:hidden;
    text-overflow:ellipsis;
}

.kpi-note.good { color:#00A350; }
.kpi-note.warn { color:#F26522; }
.kpi-note.info { color:#6B7280; }

.kpi-card.has-note .kpi-delta {
    display:none;
}

</style>
        """,
        unsafe_allow_html=True,
    )

    # ---- Verifica se está bloqueado por tempo ----
    if time.time() < st.session_state["bloqueado_ate"]:
        minutos_restantes = int((st.session_state["bloqueado_ate"] - time.time()) / 60) + 1
        st.markdown(
            f"""
            <div class="login-bg">
                <div class="login-blocked-card">
                    <div style="font-size: 44px;">🚫</div>
                    <div class="login-title">Acesso temporariamente bloqueado</div>
                    <div class="login-subtitle">
                        Houve excesso de tentativas de login.
                    </div>
                    <div class="login-alert">
                        Tente novamente em aproximadamente {minutos_restantes} minuto(s).
                    </div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.stop()

    # ---- Estrutura visual da tela de login ----
    # Importante: não usar uma DIV HTML envolvendo componentes Streamlit.
    # Isso evita o espaço grande e as linhas duplicadas acima da logo.
    col_esq, col_centro, col_dir = st.columns([1.35, 1.0, 1.35])

    with col_centro:
        st.markdown(
            """
            <div style="
                height: 5px;
                width: 100%;
                border-radius: 999px;
                background: linear-gradient(90deg, #F26522, #00A350);
                margin: 8px 0 18px 0;
            "></div>
            """,
            unsafe_allow_html=True,
        )

        logo_renderizado = False
        logo_login_arquivo = globals().get("LOGO_ARQUIVO", "MazolaCertificado.ico")
        if os.path.exists(logo_login_arquivo):
            try:
                col_logo_1, col_logo_2, col_logo_3 = st.columns([1, 1.15, 1])
                with col_logo_2:
                    st.image(logo_login_arquivo, use_container_width=True)
                logo_renderizado = True
            except Exception:
                logo_renderizado = False

        if not logo_renderizado:
            st.markdown(
                """
                <div class="login-logo-wrap">
                    <div style="font-size: 46px;">♻️</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        st.markdown(
            """
            <div style="text-align:center; margin-top: 8px;">
                <div class="login-badge">🔐 Ambiente seguro</div>
            </div>
            <div class="login-title">Acesso ao Painel</div>
            <div class="login-subtitle">
                Informe suas credenciais para acessar o painel gerencial de indicadores.
            </div>
            """,
            unsafe_allow_html=True,
        )

        with st.form("form_login_acesso"):
            usuario_digitado = st.text_input("Usuário", placeholder="Digite seu usuário")
            senha_digitada = st.text_input("Senha", type="password", placeholder="Digite sua senha")
            entrar = st.form_submit_button("Entrar no painel", use_container_width=True)

        st.markdown(
            """
            <div class="login-security">
                <span>🛡️</span>
                <span>Acesso restrito a usuários autorizados. Após tentativas incorretas, o login é temporariamente bloqueado.</span>
            </div>
            <div class="login-footer">Mazola Ambiental • Painel de Indicadores</div>
            """,
            unsafe_allow_html=True,
        )

    if entrar:
        usuario_digitado_limpo = str(usuario_digitado).strip()
        senha_digitada_limpa = str(senha_digitada).strip()

        dados_usuario = _buscar_usuario_login(usuario_digitado_limpo)

        senha_ok = (
            dados_usuario is not None
            and dados_usuario["ativo"] == "SIM"
            and dados_usuario["tipo"] == "MAZOLA"
            and _verificar_senha(senha_digitada_limpa, dados_usuario["senha"])
        )

        if dados_usuario is not None and dados_usuario["tipo"] != "MAZOLA" and _verificar_senha(senha_digitada_limpa, dados_usuario["senha"]):
            st.warning("⚠️ Este acesso ainda não está disponível para o seu perfil. Entre em contato com o administrador.")
        elif senha_ok:
            # Monta lista de filiais permitidas
            codigo_filial = dados_usuario["filial"]
            is_adm = dados_usuario["adm"].startswith("S")  # "SIM" ou "S"
            if is_adm or codigo_filial == "T" or codigo_filial == "":
                filiais_perm = ["Geral"] + FILIAIS_REAIS
            elif codigo_filial in FILIAL_CODIGO_NOME:
                filiais_perm = [FILIAL_CODIGO_NOME[codigo_filial]]
            else:
                filiais_perm = ["Geral"] + FILIAIS_REAIS

            st.session_state["acesso_liberado"] = True
            st.session_state["usuario_logado"] = dados_usuario["login"]
            st.session_state["usuario_adm"] = is_adm
            st.session_state["filiais_permitidas"] = filiais_perm
            st.session_state["tentativas_login"] = 0
            st.session_state["bloqueado_ate"] = 0
            st.session_state["ultimo_acesso"] = time.time()
            st.rerun()
        else:
            st.session_state["tentativas_login"] += 1

            if st.session_state["tentativas_login"] >= LIMITE_TENTATIVAS:
                st.session_state["bloqueado_ate"] = time.time() + TEMPO_BLOQUEIO_MINUTOS * 60
                st.error(f"🚫 Bloqueado por {TEMPO_BLOQUEIO_MINUTOS} minutos por excesso de tentativas.")
                st.stop()

            restantes = max(0, LIMITE_TENTATIVAS - st.session_state["tentativas_login"])
            st.error(f"❌ Usuário ou senha incorretos. Tentativas restantes: {restantes}")

    return False


if not verificar_senha_acesso():
    st.stop()


# =========================
# PERFIS E PERMISSÕES DO APP
# =========================
def usuario_atual():
    """Retorna o usuário logado na sessão atual."""
    return str(st.session_state.get("usuario_logado", "")).strip()


def perfil_usuario():
    """
    Retorna o perfil do usuário logado.

    Configure no Secrets:

    [perfis]
    Mazola = "admin"
    valim = "usuario"

    Se o usuário não estiver em [perfis], ele será tratado como usuário comum.
    """
    usuario = usuario_atual()
    perfis = st.secrets.get("perfis", {})

    try:
        return str(perfis.get(usuario, "usuario")).strip().lower()
    except Exception:
        return "usuario"


def eh_admin():
    """True para admins vindos da BaseLogin ou do perfil Secrets."""
    return st.session_state.get("usuario_adm", False) or perfil_usuario() == "admin"


def eh_gabriel():
    """
    True apenas para o perfil especial Gabriel.

    Esse perfil é o único autorizado a visualizar as abas:
    - Projeção
    - Análise
    """
    usuario = usuario_atual().strip().lower()
    return usuario == "gabriel" or perfil_usuario() == "gabriel"


def nome_perfil_exibicao():
    if eh_gabriel():
        return "Gabriel"
    return "Administrador" if eh_admin() else "Usuário comum"




# =========================
# REFATORAÇÃO VISUAL v5.0 — ETAPA 1
# =========================
# Esta versão mantém a lógica original e inicia a padronização visual
# para aproximar o app do padrão Power BI solicitado.

# =========================
# CONFIGURAÇÕES GERAIS
# =========================
COR_LARANJA = "#F26522"
COR_VERDE = "#00A350"
COR_AZUL = "#0078D4"
COR_AMARELO = "#FFB900"
COR_VERMELHO = "#D13438"
COR_CINZA = "#A19F9D"

CORES_MAZOLA = {
    "verde": COR_VERDE,
    "laranja": COR_LARANJA,
    "azul": COR_AZUL,
    "amarelo": COR_AMARELO,
    "vermelho": COR_VERMELHO,
    "cinza": COR_CINZA,
}

QUALQUER = "__ANY__"
LOGO_ARQUIVO = "MazolaCertificado.ico"
TZ_BR = ZoneInfo("America/Sao_Paulo")
LIMITE_DESPESA_GERAL_PADRAO = 0.71  # 71% - limite válido a partir de 2026 para Despesa Geral
META_RESULTADO_FINANCEIRO_PADRAO = None  # Resultado Financeiro: meta vem da coluna H da base

try:
    client = Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])
except Exception:
    client = None



# =========================
# GOOGLE DRIVE
# =========================
SCOPES_DRIVE = ["https://www.googleapis.com/auth/drive.readonly"]


@st.cache_data(ttl=300, show_spinner=False)
def baixar_planilha_drive(nome_arquivo="BaseSistema.xlsx"):
    """
    Busca automaticamente a planilha no Google Drive.

    Melhorias:
    - Se DRIVE_FOLDER_ID existir no Secrets, busca apenas dentro dessa pasta.
    - Evita mostrar traceback técnico para usuário final.
    - Atualiza o cache a cada 5 minutos.
    """
    if service_account is None or build is None or MediaIoBaseDownload is None:
        st.error("Bibliotecas do Google Drive não instaladas.")
        st.info(
            "Adicione no requirements.txt: google-api-python-client, google-auth, "
            "google-auth-httplib2 e openpyxl. Depois faça Reboot app no Streamlit."
        )
        st.stop()

    try:
        credentials = service_account.Credentials.from_service_account_info(
            st.secrets["gcp_service_account"],
            scopes=SCOPES_DRIVE,
        )

        service = build("drive", "v3", credentials=credentials)

        nome_arquivo = str(nome_arquivo).strip()
        nome_query = nome_arquivo.replace("'", "\\'")
        pasta_id = str(st.secrets.get("DRIVE_FOLDER_ID", "")).strip()

        query_partes = [
            f"name = '{nome_query}'",
            "trashed = false",
        ]

        if pasta_id:
            query_partes.append(f"'{pasta_id}' in parents")

        query = " and ".join(query_partes)

        resultado = service.files().list(
            q=query,
            spaces="drive",
            fields="files(id, name, modifiedTime)",
            orderBy="modifiedTime desc",
            pageSize=1,
        ).execute()

        arquivos = resultado.get("files", [])

        if not arquivos:
            st.error(f"Arquivo '{nome_arquivo}' não encontrado no Google Drive.")
            if pasta_id:
                st.info("Confira se o arquivo está dentro da pasta configurada em DRIVE_FOLDER_ID e se a pasta foi compartilhada com a Service Account.")
            else:
                st.info("Confira se o arquivo está com esse nome e se a pasta foi compartilhada com o e-mail da Service Account.")
            st.stop()

        file_id = arquivos[0]["id"]

        request = service.files().get_media(fileId=file_id)
        arquivo_bytes = io.BytesIO()
        downloader = MediaIoBaseDownload(arquivo_bytes, request)

        done = False
        while not done:
            status, done = downloader.next_chunk()

        arquivo_bytes.seek(0)
        return pd.read_excel(arquivo_bytes)

    except Exception as e:
        logging.exception("Erro ao buscar a planilha no Google Drive")
        st.error("Erro ao buscar a planilha no Google Drive.")
        st.info("Verifique o Secrets, a Service Account, a API do Google Drive e o compartilhamento da pasta/arquivo.")
        if str(st.secrets.get("DEBUG_MODE", "false")).lower() in ["true", "1", "yes", "sim"]:
            st.exception(e)
        st.stop()


def enviar_email_relatorio(destinatario, assunto, corpo, nome_arquivo, pdf_bytes):
    """
    Envia o relatório em PDF por e-mail usando SMTP.

    Configure no Streamlit Secrets:
    EMAIL_HOST
    EMAIL_PORT
    EMAIL_USER
    EMAIL_PASSWORD
    EMAIL_FROM opcional
    EMAIL_USE_TLS opcional: true/false
    """
    try:
        email_host = st.secrets["EMAIL_HOST"]
        email_port = int(st.secrets.get("EMAIL_PORT", 587))
        email_user = st.secrets["EMAIL_USER"]
        email_password = st.secrets["EMAIL_PASSWORD"]
        email_from = st.secrets.get("EMAIL_FROM", email_user)
        email_use_tls = str(st.secrets.get("EMAIL_USE_TLS", "true")).lower() in ["true", "1", "yes", "sim"]

        msg = EmailMessage()
        msg["From"] = email_from
        msg["To"] = destinatario
        msg["Subject"] = assunto
        msg.set_content(corpo)

        msg.add_attachment(
            pdf_bytes,
            maintype="application",
            subtype="pdf",
            filename=nome_arquivo,
        )

        if email_port == 465:
            with smtplib.SMTP_SSL(email_host, email_port) as smtp:
                smtp.login(email_user, email_password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(email_host, email_port) as smtp:
                if email_use_tls:
                    smtp.starttls()
                smtp.login(email_user, email_password)
                smtp.send_message(msg)

        return True, "Relatório enviado por e-mail com sucesso."

    except Exception as e:
        return False, f"Erro ao enviar e-mail: {e}"


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
        "GRUPO 01": "FATURAMENTO", "GRUPO 02": "SERVICOS", "GRUPO 03": "INCREMETAL", "TIPO DE META": "R$"
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
        "tipo": "despesa_geral_receita", "categoria": "despesa_geral_receita", "TIPO": "ECONOMICO",
        "GRUPO 01": "DESPESAS", "GRUPO 02": None, "GRUPO 03": QUALQUER, "TIPO DE META": "%"
    },
    "Resultado Financeiro": {
        "tipo": "resultado_financeiro", "categoria": "resultado_financeiro", "TIPO": "ECONOMICO",
        "GRUPO 01": "RESULTADO FINANCEIRO", "GRUPO 02": None, "GRUPO 03": None, "TIPO DE META": "%"
    },
    "Tecfil Geral": {
        "tipo": "tecfil", "categoria": "tecfil", "estado_tecfil": "GERAL"
    },
    "Tecfil SP": {
        "tipo": "tecfil", "categoria": "tecfil", "estado_tecfil": "SP"
    },
    "Tecfil MS": {
        "tipo": "tecfil", "categoria": "tecfil", "estado_tecfil": "MS"
    },
    "Tecfil ES": {
        "tipo": "tecfil", "categoria": "tecfil", "estado_tecfil": "ES"
    },
    "Tecfil PR": {
        "tipo": "tecfil", "categoria": "tecfil", "estado_tecfil": "PR"
    },
    "Avaliação de Equipe": {
        "tipo": "qualidade", "categoria": "qualidade", "modelo_qualidade": "avaliacao_equipe",
        "TIPO": "QUALIDADE", "GRUPO 01": "AVALIACAO EQUIPE", "GRUPO 02": None, "GRUPO 03": None
    },
    "Parâmetro de Coleta": {
        "tipo": "qualidade", "categoria": "qualidade", "modelo_qualidade": "parametro_coleta",
        "TIPO": "QUALIDADE", "GRUPO 01": "PARAMETROS COLETAS", "GRUPO 02": None, "GRUPO 03": None
    },
    "Parâmetro de Coleta Crítico": {
        "tipo": "qualidade", "categoria": "qualidade", "modelo_qualidade": "parametro_coleta_critico",
        "TIPO": "QUALIDADE", "GRUPO 01": "PARAMETROS COLETAS", "GRUPO 02": "CRITICO", "GRUPO 03": None
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


# =========================
# BLOCOS DE INDICADORES
# =========================
INDICADORES_POR_BLOCO = {
    "Econômico": {
        "Faturamentos": [
            "Faturamento",
            "Faturamento Serviços",
            "Faturamento Regular",
            "Faturamento Incremental",
            "Faturamento LCSAO",
            "Faturamento Sucata Diversa",
            "Faturamento Reman Total",
            "Faturamento Reman Rev",
            "Faturamento Reman Cap",
            "Faturamento Pneus Velhos",
            "Faturamento Pneu Exceto Moto",
            "Faturamento Pneus Velhos Moto",
            "Faturamento Especial",
        ],
        "Resultado Financeiro": [
            "Resultado Financeiro",
        ],
        "Despesas": [
            "Despesa Geral",
            "Despesa Manutenção",
            "Despesa Hora Extra",
        ],
    },
    "Qualidade": {
        "Qualidade": [
            "Avaliação de Equipe",
            "Parâmetro de Coleta",
            "Parâmetro de Coleta Crítico",
        ],
    },
    "Tecfil": {
        "Tecfil": [
            "Tecfil Geral",
            "Tecfil SP",
            "Tecfil MS",
            "Tecfil ES",
            "Tecfil PR",
        ],
    },
}


def _opcoes_validas_indicadores(lista):
    """Garante que só apareçam indicadores existentes no dicionário INDICADORES."""
    return [i for i in lista if i in INDICADORES]


def selecionar_indicador_por_blocos():
    """
    Seleção organizada:
    1. Bloco principal: Econômico / Qualidade / Tecfil
    2. Grupo: Faturamentos / Resultado Financeiro / Despesas etc.
    3. Indicador final.
    """
    blocos = list(INDICADORES_POR_BLOCO.keys())

    bloco_atual = st.session_state.get("bloco_principal_indicador", "Econômico")
    if bloco_atual not in blocos:
        bloco_atual = "Econômico"

    bloco = st.selectbox(
        "Bloco",
        blocos,
        index=blocos.index(bloco_atual),
        key="bloco_principal_indicador",
    )

    grupos_dict = INDICADORES_POR_BLOCO[bloco]
    grupos = list(grupos_dict.keys())

    grupo_atual = st.session_state.get("grupo_indicador", grupos[0])
    if grupo_atual not in grupos:
        grupo_atual = grupos[0]

    # Se o bloco só tiver um grupo, não precisa poluir a tela com outro selectbox.
    if len(grupos) > 1:
        grupo = st.selectbox(
            "Tipo",
            grupos,
            index=grupos.index(grupo_atual),
            key="grupo_indicador",
        )
    else:
        grupo = grupos[0]
        st.session_state["grupo_indicador"] = grupo

    opcoes = _opcoes_validas_indicadores(grupos_dict[grupo])

    if not opcoes:
        st.error("Nenhum indicador encontrado para o bloco selecionado.")
        st.stop()

    indicador_atual = st.session_state.get("indicador_selecionado", opcoes[0])
    if indicador_atual not in opcoes:
        indicador_atual = opcoes[0]

    indicador = st.selectbox(
        "Indicador",
        opcoes,
        index=opcoes.index(indicador_atual),
        key="indicador_selecionado",
    )

    return indicador


FILIAIS_REAIS = ["CANOAS/RS", "CURITIBA/PR", "DUQUE DE CAXIAS/RJ", "VALINHOS/SP"]
MESES_MAPA = {
    1: "Jan", 2: "Fev", 3: "Mar", 4: "Abr", 5: "Mai", 6: "Jun",
    7: "Jul", 8: "Ago", 9: "Set", 10: "Out", 11: "Nov", 12: "Dez"
}


# =========================
# FUNÇÕES AUXILIARES
# =========================


def eh_despesa_manutencao(indicador):
    return normalizar_texto(indicador) == "DESPESA MANUTENCAO"


def eh_despesa_hora_extra(indicador):
    return normalizar_texto(indicador) == "DESPESA HORA EXTRA"


def eh_despesa_com_limite(indicador):
    return eh_despesa_manutencao(indicador) or eh_despesa_hora_extra(indicador)


def rotulo_meta(indicador):
    return "Limite" if eh_despesa_com_limite(indicador) else "Meta"


def rotulo_realizado(indicador):
    if eh_despesa_hora_extra(indicador):
        return "Pago em Hora Extra"
    if eh_despesa_manutencao(indicador):
        return "Despesa"
    return "Realizado"


def rotulo_gap(indicador):
    if eh_despesa_manutencao(indicador):
        return "Resultado R$"
    return "Saldo do Limite" if eh_despesa_com_limite(indicador) else "Gap"


def rotulo_percentual(indicador):
    if eh_despesa_hora_extra(indicador):
        return "Resultado %"
    if eh_despesa_manutencao(indicador):
        return "Uso do Limite"
    return "Atingimento"


def atingimento_despesa_manutencao(realizado, limite):
    """
    Para despesas com limite:
    - Limite é o máximo que pode gastar.
    - Se a despesa for menor ou igual ao limite, está bom.
    - Indicador mostrado = Despesa/Pago / Limite.
    """
    if limite is None or pd.isna(limite) or limite == 0:
        return None
    return realizado / limite


def cor_despesa_manutencao(v):
    """
    Regra de cor para indicadores de limite.

    - Quando o valor for uso do limite em razão direta, verde até 100%.
    - Quando o valor for Resultado % no formato (realizado / limite - 1),
      verde quando <= 0 e laranja quando > 0.

    Como após os ajustes principais a tela usa Resultado % para manutenção/HE,
    a regra prática fica:
    verde para valor <= 0 ou dentro do limite; laranja quando positivo/excedente.
    """
    if pd.isna(v):
        return ""
    return f"color: {COR_VERDE}; font-weight:bold" if v <= 0 else f"color: {COR_LARANJA}; font-weight:bold"

def agora_br():
    return datetime.now(TZ_BR)



def fmt_num(v):
    if v is None or pd.isna(v):
        return "-"
    try:
        return f"{float(v):,.0f}".replace(",", ".")
    except Exception:
        return "-"






def formatar_df_periodo_tela_seguro(df_periodo_tela):
    """
    Formata a tabela de mesmo período sem usar Pandas Styler.
    Isso evita erro TypeError em formatter.format(x).
    """
    if df_periodo_tela is None or df_periodo_tela.empty:
        return pd.DataFrame()

    d = df_periodo_tela.copy()
    d = d.replace([float("inf"), float("-inf")], pd.NA)

    for col in d.columns:
        nome = normalizar_texto(col)

        if nome in ["ANO", "MESES C DADO"]:
            d[col] = d[col].apply(fmt_int_seguro)

        elif nome in ["PERIODO", "FILIAL"]:
            d[col] = d[col].apply(lambda v: "—" if pd.isna(v) else str(v))

        elif nome.startswith("VARIACAO"):
            d[col] = d[col].apply(fmt_var_pct_seguro)

        elif "%" in str(col) or nome in ["ATINGIMENTO", "ATING", "TX SUCESSO", "TX. SUCESSO", "USO DO LIMITE"]:
            d[col] = d[col].apply(fmt_pct)

        elif any(p in nome for p in ["R$", "RECEITA", "DESPESA", "FATURAMENTO", "COMPRA", "MARGEM", "REALIZADO", "META", "GAP", "RESULTADO"]):
            # Se for Resultado % já caiu no caso anterior; Resultado R$/Gap fica financeiro.
            if "KG" in nome or "QTD" in nome or nome in ["RESULTADO"]:
                d[col] = d[col].apply(fmt_num)
            else:
                d[col] = d[col].apply(fmt_brl)

        elif any(p in nome for p in ["QTD", "KG", "DIFERENCA", "CRITICO"]):
            d[col] = d[col].apply(fmt_num)

        else:
            d[col] = d[col].apply(lambda v: "—" if pd.isna(v) else v)

    return d


def preparar_df_para_styler(df_entrada):
    """
    Evita erros do pandas Styler convertendo valores problemáticos.
    Mantém números como números e troca infinitos por NA.
    """
    if df_entrada is None:
        return pd.DataFrame()

    d = df_entrada.copy()
    d = d.replace([float("inf"), float("-inf")], pd.NA)

    return d


def fmt_int_seguro(v):
    if v is None or pd.isna(v):
        return "—"
    try:
        return f"{int(float(v))}"
    except Exception:
        return str(v)


def fmt_var_pct_seguro(v):
    if v is None or pd.isna(v):
        return "—"
    try:
        return f"{float(v):+.1%}"
    except Exception:
        return "—"


def fmt_mom_seguro(v):
    if v is None or pd.isna(v):
        return "-"
    try:
        return f"{float(v):+.1f}%"
    except Exception:
        return "-"


def fmt_brl(v):
    if pd.isna(v) or v is None:
        return "-"
    return f"R$ {v:,.0f}".replace(",", ".")


def fmt_pct(v):
    if pd.isna(v) or v is None:
        return "-"
    return f"{v:.1%}"



def eh_faturamento_simples(indicador):
    """Indicadores de faturamento padrão: Faturamento, Serviços, Regular, Incremental etc."""
    cfg = INDICADORES.get(indicador, {})
    return cfg.get("categoria") == "faturamento" and cfg.get("tipo") in ["simples", "composto"]


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


@st.cache_data(ttl=300)
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





def obter_data_geracao_planilha(df_base):
    """
    Busca a data de geração da planilha.

    Regra:
    - usa preferencialmente a coluna cujo nome contém "Data e Hora Geração";
    - se não encontrar, usa a última coluna da planilha;
    - pega o primeiro valor preenchido;
    - formata como dd/mm/aaaa HH:mm.
    """
    try:
        if df_base is None or df_base.empty:
            return "-"

        colunas = list(df_base.columns)

        coluna_data = None
        for col in colunas:
            nome_norm = normalizar_texto(col)
            if "DATA" in nome_norm and "HORA" in nome_norm and "GERACAO" in nome_norm:
                coluna_data = col
                break

        if coluna_data is None:
            coluna_data = colunas[-1]

        serie = df_base[coluna_data].dropna()
        if serie.empty:
            return "-"

        valor = serie.iloc[0]

        data = pd.to_datetime(valor, errors="coerce", dayfirst=True)
        if pd.isna(data):
            return str(valor)

        return data.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return "-"


def renderizar_cabecalho(data_geracao_planilha="-"):
    """
    Renderiza o cabeçalho principal do app sem data/hora.
    A data da base fica concentrada na aba Análise.
    """
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


def validar_colunas_base(df_base):
    """
    Valida se a planilha possui as colunas mínimas necessárias para o painel.

    Isso evita erro quebrado no meio do app e mostra uma mensagem clara
    quando a planilha enviada/carregada estiver fora do padrão.
    """
    if df_base is None or df_base.empty:
        st.error("A planilha carregada está vazia.")
        st.stop()

    colunas_obrigatorias = [
        "TIPO",
        "GRUPO 01",
        "GRUPO 02",
        "GRUPO 03",
        "FILIAL",
        "REFERÊNCIA",
        "META",
        "VALOR REF 01",
    ]

    faltando = [col for col in colunas_obrigatorias if col not in df_base.columns]

    if faltando:
        st.error("A planilha carregada não possui todas as colunas obrigatórias.")
        st.write("Colunas faltando:", faltando)
        st.info("Confira se você está usando a BaseSistema.xlsx correta.")
        st.stop()

    return df_base


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
        # Tenta usar a linha "XX GERAL XX" da planilha primeiro
        # (já tem meta consolidada e totais corretos do sistema)
        opcoes_geral = [
            normalizar_texto("XX GERAL XX"),
            normalizar_texto("GERAL"),
            normalizar_texto("TOTAL"),
        ]
        d_geral = d[d["FILIAL"].apply(normalizar_texto).isin(opcoes_geral)].copy()

        if not d_geral.empty:
            # Encontrou linha consolidada — usa ela
            d = d_geral
        else:
            # Não tem linha consolidada — soma as 4 filiais (fallback)
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

    if eh_despesa_hora_extra(nome_indicador):
        # Para Despesa Hora Extra:
        # META_CALC representa o LIMITE máximo de gasto com HE.
        # REALIZADO_CALC representa o valor PAGO EM HORA EXTRA.
        # SALARIO_CALC busca o salário/folha na coluna L:L.
        # HE_FOLHA_CALC = Pago em Hora Extra / Salário.
        # RESULTADO % = Pago em Hora Extra / Limite.
        d["REALIZADO_CALC"] = d["VALOR1"]
        d["SALARIO_CALC"] = serie_numerica_por_coluna(
            d,
            nomes_preferidos=["VALOR REF 02", "SALARIO", "SALÁRIO", "SALARIOS", "SALÁRIOS", "FOLHA", "FOLHA R$"],
            indice_1_base=12
        )
        d["HE_FOLHA_CALC"] = d["REALIZADO_CALC"] / d["SALARIO_CALC"].replace(0, pd.NA)
        d["RESULTADO_RS"] = d["META_CALC"] - d["REALIZADO_CALC"]
        d["ATINGIMENTO_CALC"] = d["REALIZADO_CALC"] / d["META_CALC"].replace(0, pd.NA)

    elif eh_despesa_manutencao(nome_indicador):
        # Para Despesa Manutenção:
        # META_CALC representa o LIMITE máximo de gasto.
        # REALIZADO_CALC representa a DESPESA realizada.
        #
        # Estrutura solicitada:
        # RESULT. R$ = LIMITE - DESPESA
        # RESULT. %  = 1 - (DESPESA / LIMITE)
        d["REALIZADO_CALC"] = d["VALOR1"]
        d["RESULTADO_RS"] = d["META_CALC"] - d["REALIZADO_CALC"]
        d["ATINGIMENTO_CALC"] = 1 - (d["REALIZADO_CALC"] / d["META_CALC"].replace(0, pd.NA))

    elif categoria == "despesa":
        d["REALIZADO_CALC"] = d["VALOR1"]
        d["RESULTADO_RS"] = d["META_CALC"] - d["REALIZADO_CALC"]
        d["ATINGIMENTO_CALC"] = d["META_CALC"] / d["REALIZADO_CALC"].replace(0, pd.NA)

    else:
        d["REALIZADO_CALC"] = d["VALOR1"]
        d["RESULTADO_RS"] = d["REALIZADO_CALC"] - d["META_CALC"]
        d["ATINGIMENTO_CALC"] = d["REALIZADO_CALC"] / d["META_CALC"].replace(0, pd.NA)

    return d.replace([float("inf"), float("-inf")], pd.NA)


def eh_despesa_geral(indicador):
    return normalizar_texto(indicador) == "DESPESA GERAL"


def eh_despesa_geral_receita(indicador):
    return INDICADORES.get(indicador, {}).get("tipo") == "despesa_geral_receita"


def meta_ponderada_por_receita(grupo):
    """
    Regra específica da Despesa Geral:
    - 2023, 2024 e 2025 não possuem limite/meta.
    - A partir de 2026, o limite de referência é 71%.
    - Resultado R$ = (Receita x Limite %) - Despesa.
    """
    if grupo is None or grupo.empty:
        return None

    if "ANO" in grupo.columns and grupo["ANO"].notna().any():
        ano = int(pd.to_numeric(grupo["ANO"], errors="coerce").dropna().max())
        if ano < 2026:
            return None

    return LIMITE_DESPESA_GERAL_PADRAO

def resumo_despesa_geral_por_grupo(grupo):
    despesa = pd.to_numeric(grupo.get("DESPESA_CALC"), errors="coerce").fillna(0).sum()
    receita = pd.to_numeric(grupo.get("RECEITA_CALC"), errors="coerce").fillna(0).sum()

    limite_pct = meta_ponderada_por_receita(grupo)

    if limite_pct is None or pd.isna(limite_pct):
        limite_rs = None
        resultado = None
    else:
        limite_rs = receita * limite_pct
        resultado = limite_rs - despesa

    # Fórmula correta:
    # Tx. Sucesso = Despesa / Receita
    # Interpretação: quanto da receita foi consumido pela despesa.
    # Para Despesa Geral, menor ou igual ao Limite % é melhor.
    tx_sucesso = despesa / receita if receita > 0 else None

    return {
        "Limite %": limite_pct,
        "Limite R$": limite_rs,
        "Despesa": despesa,
        "Receita": receita,
        "Resultado R$": resultado,
        "Tx. Sucesso": tx_sucesso,
    }

def consolidar_despesa_geral(df):
    """
    Estrutura especial: Despesa Geral.

    Filtro:
    TIPO = ECONOMICO
    GRUPO 01 = DESPESAS
    TIPO DE META = %

    Cálculos:
    LIMITE % = 71% somente a partir de 2026
    DESPESA = coluna J:J / VALOR REF 01
    RECEITA = coluna L:L / VALOR REF 02
    RESULTADO R$ = (RECEITA x LIMITE %) - DESPESA
    TX. SUCESSO = DESPESA / RECEITA

    Interpretação:
    A Tx. Sucesso mostra quanto da receita foi consumido pela despesa.
    Para Despesa Geral, menor ou igual ao Limite % é melhor.

    2023, 2024 e 2025 não têm limite/meta.
    """
    d = df.copy()

    d["LIMITE_PERCENTUAL_CALC"] = ajustar_percentual_meta(d.get("META", 0))

    # Anos anteriores a 2026 não possuem limite.
    d.loc[d["ANO"] < 2026, "LIMITE_PERCENTUAL_CALC"] = pd.NA

    # A partir de 2026, usar o limite correto de 71%.
    d.loc[d["ANO"] >= 2026, "LIMITE_PERCENTUAL_CALC"] = LIMITE_DESPESA_GERAL_PADRAO

    d["DESPESA_CALC"] = serie_numerica_por_coluna(
        d,
        nomes_preferidos=["VALOR REF 01", "DESPESA", "DESPESA R$", "DESP."],
        indice_1_base=10
    )
    d["RECEITA_CALC"] = serie_numerica_por_coluna(
        d,
        nomes_preferidos=["VALOR REF 02", "RECEITA", "RECEITA R$"],
        indice_1_base=12
    )

    d["LIMITE_RS_CALC"] = d["RECEITA_CALC"] * d["LIMITE_PERCENTUAL_CALC"]
    d["RESULTADO_RS"] = d["LIMITE_RS_CALC"] - d["DESPESA_CALC"]

    # Fórmula correta:
    # Tx. Sucesso = Despesa / Receita
    d["TX_SUCESSO_CALC"] = d["DESPESA_CALC"] / d["RECEITA_CALC"].replace(0, pd.NA)

    # Campos de compatibilidade
    d["META_CALC"] = d["LIMITE_PERCENTUAL_CALC"]
    d["REALIZADO_CALC"] = d["DESPESA_CALC"]
    d["ATINGIMENTO_CALC"] = d["TX_SUCESSO_CALC"]

    return d.replace([float("inf"), float("-inf")], pd.NA)


def eh_resultado_financeiro(indicador):
    return normalizar_texto(indicador) == "RESULTADO FINANCEIRO"


def meta_ponderada_resultado_financeiro(grupo):
    """
    Meta do Resultado Financeiro.

    Regra correta:
    a meta deve ser buscada na coluna H da base, respeitando os filtros:
    - coluna A = filial selecionada
    - coluna B = ECONOMICO
    - coluna C = RESULTADO FINANCEIRO
    - coluna D = vazia
    - coluna E = vazia
    - coluna F = mês/referência
    - coluna H = meta

    Observação:
    Não existe mais meta padrão fixa de 5%.
    Se a coluna H estiver vazia no mês/período, a meta fica vazia.
    """
    if grupo is None or grupo.empty:
        return None

    if "META_PERCENTUAL_CALC" in grupo.columns:
        meta_raw = pd.to_numeric(grupo["META_PERCENTUAL_CALC"], errors="coerce").dropna()
        meta_raw = meta_raw[meta_raw != 0]
        if not meta_raw.empty:
            return meta_raw.mean()

    return None

def resumo_resultado_financeiro_por_grupo(grupo):
    if grupo is None or grupo.empty:
        return {
            "Meta %": None,
            "Despesa": 0,
            "Receita": 0,
            "Resultado R$": None,
            "Resultado %": None,
        }

    despesa = pd.to_numeric(grupo.get("DESPESA_CALC"), errors="coerce").fillna(0).sum()
    receita = pd.to_numeric(grupo.get("RECEITA_CALC"), errors="coerce").fillna(0).sum()
    meta_pct = meta_ponderada_resultado_financeiro(grupo)

    # Fórmula do Resultado %:
    # =SEERRO((1-Despesa/Receita);0)
    resultado_pct = (1 - (despesa / receita)) if receita > 0 else None

    # Fórmula do Resultado R$:
    # =(Receita*Resultado%) - (Receita*Meta%)
    # Exemplo total da planilha:
    # Receita total x (-21,5%) - Receita total x 5,0%
    if receita > 0 and resultado_pct is not None and meta_pct is not None and pd.notna(meta_pct):
        resultado_rs = (receita * resultado_pct) - (receita * meta_pct)
    else:
        resultado_rs = None

    return {
        "Meta %": meta_pct,
        "Despesa": despesa,
        "Receita": receita,
        "Resultado R$": resultado_rs,
        "Resultado %": resultado_pct,
    }

def consolidar_resultado_financeiro(df):
    """
    Resultado Financeiro.

    Filtro:
    TIPO = ECONOMICO
    GRUPO 01 = RESULTADO FINANCEIRO
    GRUPO 02 = vazio
    GRUPO 03 = vazio
    TIPO DE META = %

    Fórmulas:
    META % = coluna H da base, após os filtros de filial, econômico, resultado financeiro e mês
    Resultado % = 1 - (Despesa / Receita)
    Resultado R$ = (Receita x Resultado %) - (Receita x Meta %)

    Observação:
    Não há meta padrão fixa.
    Se a coluna H estiver vazia para o mês/filial, a meta fica vazia.
    """
    d = df.copy()

    # Meta do Resultado Financeiro:
    # REGRA CORRETA: buscar a meta na coluna H da base.
    # Como aplicar_filtro_base já filtrou:
    # A = filial, B = ECONOMICO, C = RESULTADO FINANCEIRO, D/E vazias e F = mês,
    # aqui usamos a coluna H como fonte da meta.
    col_meta_h = coluna_por_indice(d, 8)  # H
    if col_meta_h is not None:
        meta_raw = pd.to_numeric(d[col_meta_h], errors="coerce")
        d["META_PERCENTUAL_CALC"] = ajustar_percentual_meta(meta_raw)
        d.loc[meta_raw.fillna(0) == 0, "META_PERCENTUAL_CALC"] = pd.NA
    else:
        d["META_PERCENTUAL_CALC"] = pd.NA

    # Despesa: coluna J:J / VALOR REF 01
    d["DESPESA_CALC"] = serie_numerica_por_coluna(
        d,
        nomes_preferidos=["VALOR REF 01", "DESPESA", "DESPESA R$", "DESP."],
        indice_1_base=10
    )

    # Receita: coluna L:L / VALOR REF 02
    d["RECEITA_CALC"] = serie_numerica_por_coluna(
        d,
        nomes_preferidos=["VALOR REF 02", "RECEITA", "RECEITA R$"],
        indice_1_base=12
    )

    d["RESULTADO_PERCENTUAL_CALC"] = 1 - (d["DESPESA_CALC"] / d["RECEITA_CALC"].replace(0, pd.NA))

    # Fórmula da planilha:
    # Resultado R$ = (Receita x Resultado %) - (Receita x Meta %)
    d["RESULTADO_RS"] = (d["RECEITA_CALC"] * d["RESULTADO_PERCENTUAL_CALC"]) - (d["RECEITA_CALC"] * d["META_PERCENTUAL_CALC"])

    # Compatibilidade
    d["META_CALC"] = d["META_PERCENTUAL_CALC"]
    d["REALIZADO_CALC"] = d["RESULTADO_RS"]
    d["ATINGIMENTO_CALC"] = d["RESULTADO_PERCENTUAL_CALC"]

    return d.replace([float("inf"), float("-inf")], pd.NA)


def eh_tecfil(indicador):
    return normalizar_texto(indicador).startswith("TECFIL")


def estado_tecfil_indicador(indicador):
    cfg = INDICADORES.get(indicador, {})
    return cfg.get("estado_tecfil", "GERAL")


def cor_tecfil_resultado(v):
    if pd.isna(v):
        return ""
    return f"color: {COR_VERDE}; font-weight:bold" if v >= 0 else f"color: {COR_LARANJA}; font-weight:bold"


def consolidar_tecfil(df_raw, filial, indicador):
    """
    Estrutura especial TECFIL.

    Colunas da base:
    B = TECFIL KG / TECFIL VALOR
    C = filial/base, exemplo XX GERAL XX
    F = data/referência
    H = meta em KG ou R$
    J = realizado em KG ou R$

    O indicador consolida KG e R$ no mesmo quadro:
    - Meta KG
    - Meta R$
    - Realizado KG
    - Realizado R$
    - % Diferença KG/R$
    - Diferença KG/R$
    - Acumulado KG/R$
    """
    d = df_raw.copy()

    col_tipo = coluna_por_indice(d, 2)   # B
    col_filial = coluna_por_indice(d, 3) # C
    col_ref = coluna_por_indice(d, 6)    # F
    col_meta = coluna_por_indice(d, 8)   # H
    col_real = coluna_por_indice(d, 10)  # J

    if any(c is None for c in [col_tipo, col_filial, col_ref, col_meta, col_real]):
        return pd.DataFrame()

    base = pd.DataFrame({
        "TIPO_TECFIL": d[col_tipo],
        "FILIAL_ORIGEM": d[col_filial],
        "REFERÊNCIA": d[col_ref],
        "META_RAW": pd.to_numeric(d[col_meta], errors="coerce").fillna(0),
        "REAL_RAW": pd.to_numeric(d[col_real], errors="coerce").fillna(0),
    })

    base["TIPO_NORM"] = base["TIPO_TECFIL"].apply(normalizar_texto)
    base["FILIAL_NORM"] = base["FILIAL_ORIGEM"].apply(normalizar_texto)

    # Mantém apenas linhas Tecfil KG e Tecfil Valor
    base = base[
        base["TIPO_NORM"].str.contains("TECFIL", na=False)
        & (
            base["TIPO_NORM"].str.contains("KG", na=False)
            | base["TIPO_NORM"].str.contains("VALOR", na=False)
            | base["TIPO_NORM"].str.contains("R$", na=False)
        )
    ].copy()

    if base.empty:
        return pd.DataFrame()

    estado_indicador = estado_tecfil_indicador(indicador)

    if estado_indicador == "GERAL":
        # Tecfil Geral deve continuar separado.
        # Na planilha pode aparecer como TECFIL GERAL ou, em bases antigas, como XX GERAL XX.
        opcoes_geral = [
            normalizar_texto("XX GERAL XX"),
            normalizar_texto("GERAL"),
        ]

        base = base[base["FILIAL_NORM"].isin(opcoes_geral)].copy()
        base["FILIAL"] = "Tecfil Geral"
    else:
        # Indicadores separados por estado:
        # Tecfil SP, Tecfil MS, Tecfil ES e Tecfil PR.
        base = base[base["FILIAL_NORM"] == normalizar_texto(estado_indicador)].copy()
        base["FILIAL"] = f"Tecfil {estado_indicador}"

    if base.empty:
        return pd.DataFrame()

    base["REFERÊNCIA"] = pd.to_datetime(base["REFERÊNCIA"], errors="coerce")
    base = base[base["REFERÊNCIA"].notna()].copy()

    if base.empty:
        return pd.DataFrame()

    base["ANO"] = base["REFERÊNCIA"].dt.year
    base["MÊS"] = base["REFERÊNCIA"].dt.month
    base["DIA"] = base["REFERÊNCIA"].dt.day
    base["MÊS_ORDEM"] = base["ANO"] * 100 + base["MÊS"]
    base["MÊS_NOME"] = base["MÊS"].map(MESES_MAPA) + "/" + base["ANO"].astype(str)

    kg = base[base["TIPO_NORM"].str.contains("KG", na=False)].copy()
    valor = base[
        base["TIPO_NORM"].str.contains("VALOR", na=False)
        | base["TIPO_NORM"].str.contains("R$", na=False)
    ].copy()

    chaves = ["FILIAL", "REFERÊNCIA", "ANO", "MÊS", "DIA", "MÊS_ORDEM", "MÊS_NOME"]

    kg_agg = (
        kg.groupby(chaves, as_index=False)
        .agg({"META_RAW": "sum", "REAL_RAW": "sum"})
        .rename(columns={"META_RAW": "META_KG", "REAL_RAW": "REAL_KG"})
    )

    valor_agg = (
        valor.groupby(chaves, as_index=False)
        .agg({"META_RAW": "sum", "REAL_RAW": "sum"})
        .rename(columns={"META_RAW": "META_RS", "REAL_RAW": "REAL_RS"})
    )

    out = pd.merge(kg_agg, valor_agg, on=chaves, how="outer").fillna(0)

    out["DIF_KG"] = out["REAL_KG"] - out["META_KG"]
    out["DIF_RS"] = out["REAL_RS"] - out["META_RS"]
    out["PCT_KG"] = out["REAL_KG"] / out["META_KG"].replace(0, pd.NA) - 1
    out["PCT_RS"] = out["REAL_RS"] / out["META_RS"].replace(0, pd.NA) - 1

    # Compatibilidade com partes padrão do app
    out["META_CALC"] = out["META_RS"]
    out["REALIZADO_CALC"] = out["REAL_RS"]
    out["RESULTADO_RS"] = out["DIF_RS"]
    out["ATINGIMENTO_CALC"] = out["REAL_RS"] / out["META_RS"].replace(0, pd.NA)

    return out.sort_values(["ANO", "MÊS", "FILIAL"]).replace([float("inf"), float("-inf")], pd.NA).reset_index(drop=True)


def resumo_tecfil_por_grupo(grupo):
    if grupo is None or grupo.empty:
        return {
            "Meta KG": 0, "Meta R$": 0, "Realizado KG": 0, "Realizado R$": 0,
            "% Dif. KG": None, "% Dif. R$": None, "Dif. KG": 0, "Dif. R$": 0
        }

    meta_kg = pd.to_numeric(grupo.get("META_KG"), errors="coerce").fillna(0).sum()
    meta_rs = pd.to_numeric(grupo.get("META_RS"), errors="coerce").fillna(0).sum()
    real_kg = pd.to_numeric(grupo.get("REAL_KG"), errors="coerce").fillna(0).sum()
    real_rs = pd.to_numeric(grupo.get("REAL_RS"), errors="coerce").fillna(0).sum()

    dif_kg = real_kg - meta_kg
    dif_rs = real_rs - meta_rs
    pct_kg = real_kg / meta_kg - 1 if meta_kg > 0 else None
    pct_rs = real_rs / meta_rs - 1 if meta_rs > 0 else None

    return {
        "Meta KG": meta_kg,
        "Meta R$": meta_rs,
        "Realizado KG": real_kg,
        "Realizado R$": real_rs,
        "% Dif. KG": pct_kg,
        "% Dif. R$": pct_rs,
        "Dif. KG": dif_kg,
        "Dif. R$": dif_rs,
    }



def eh_qualidade(indicador):
    return INDICADORES.get(indicador, {}).get("tipo") == "qualidade"


def modelo_qualidade(indicador):
    return INDICADORES.get(indicador, {}).get("modelo_qualidade")


def rotulos_qualidade(indicador):
    modelo = modelo_qualidade(indicador)

    if modelo == "avaliacao_equipe":
        return {
            "meta": "Meta",
            "qtd_total": "Qtd. coletas",
            "qtd_sucesso": "Qtd. col. ótimo+bom",
            "diferenca": "Diferença",
            "resultado": "Tx sucesso %",
        }

    if modelo == "parametro_coleta":
        return {
            "meta": "Meta",
            "qtd_total": "Qtd. coletas",
            "qtd_sucesso": "Qtd. coletas normais",
            "diferenca": "Diferença",
            "resultado": "Resultado %",
        }

    if modelo == "parametro_coleta_critico":
        return {
            "meta": "Limite crítico",
            "qtd_total": "Qtd. coletas",
            "qtd_sucesso": "Qtd. crítico",
            "diferenca": "Resultado",
            "resultado": "Result %",
        }

    return {
        "meta": "Meta",
        "qtd_total": "QTD. COLETAS",
        "qtd_sucesso": "QTD. SUCESSO",
        "diferenca": "DIFER.",
        "resultado": "RESULT. %",
    }

def consolidar_qualidade(df, indicador):
    """
    Indicadores de Qualidade.

    1. Avaliação de Equipe
    TIPO = QUALIDADE
    GRUPO 01 = AVALIACAO EQUIPE
    GRUPO 02 = vazio
    GRUPO 03 = vazio
    META = meta %
    VALOR REF 01 = quantidade coletada
    VALOR REF 02 = quantidade coletada ótimo + bom
    Resultado = VALOR REF 02 / VALOR REF 01
    Diferença = VALOR REF 01 - VALOR REF 02

    2. Parâmetro de Coleta
    TIPO = QUALIDADE
    GRUPO 01 = PARAMETROS COLETAS
    GRUPO 02 = vazio
    GRUPO 03 = vazio
    META = meta %
    VALOR REF 01 = quantidade coletada
    VALOR REF 02 = coletas normais
    Resultado = VALOR REF 02 / VALOR REF 01
    Diferença = VALOR REF 01 - VALOR REF 02

    3. Parâmetro de Coleta Crítico
    TIPO = QUALIDADE
    GRUPO 01 = PARAMETROS COLETAS
    GRUPO 02 = CRITICO
    GRUPO 03 = vazio
    META = limite crítico
    VALOR REF 01 = quantidade coletada
    VALOR REF 02 = quantidade crítica
    Resultado = VALOR REF 02 - META
    Resultado % = VALOR REF 02 / VALOR REF 01
    """
    d = df.copy()
    modelo = modelo_qualidade(indicador)

    d["QTD_TOTAL_CALC"] = serie_numerica_por_coluna(
        d,
        nomes_preferidos=["VALOR REF 01", "QTD. COLETAS", "QTD COLETAS", "QUANTIDADE COLETADA"],
        indice_1_base=10
    )

    d["QTD_SUCESSO_CALC"] = serie_numerica_por_coluna(
        d,
        nomes_preferidos=["VALOR REF 02", "QTD. COL. OTIMO+BOM", "QTD. COLETAS NORMAIS", "QTD. COL. CRITICO"],
        indice_1_base=12
    )

    if modelo == "parametro_coleta_critico":
        d["META_QUALIDADE_CALC"] = pd.to_numeric(d.get("META", 0), errors="coerce").fillna(0)
        d["DIFERENCA_CALC"] = d["QTD_SUCESSO_CALC"] - d["META_QUALIDADE_CALC"]
    else:
        d["META_QUALIDADE_CALC"] = ajustar_percentual_meta(d.get("META", 0))
        d["DIFERENCA_CALC"] = d["QTD_TOTAL_CALC"] - d["QTD_SUCESSO_CALC"]

    d["RESULTADO_QUALIDADE_CALC"] = d["QTD_SUCESSO_CALC"] / d["QTD_TOTAL_CALC"].replace(0, pd.NA)

    # Compatibilidade com blocos antigos
    d["META_CALC"] = d["META_QUALIDADE_CALC"]
    d["REALIZADO_CALC"] = d["QTD_SUCESSO_CALC"]
    d["ATINGIMENTO_CALC"] = d["RESULTADO_QUALIDADE_CALC"]

    return d.replace([float("inf"), float("-inf")], pd.NA)



def consolidar_parametro_coleta_critico(df_raw, filial):
    """
    Consolidação específica do indicador Parâmetro de Coleta Crítico.

    Lógica correta:
    - Limite crítico: vem das linhas QUALIDADE / PARAMETROS COLETAS / CRITICO.
    - Qtd. coletas: vem do indicador Parâmetro de Coleta.
    - Diferença do Parâmetro de Coleta: vem do VALOR REF 02 do Parâmetro de Coleta.
    - Qtd. coletas normais = Qtd. coletas - Diferença.
    - Qtd. crítico = Qtd. coletas - Qtd. coletas normais.
      Na prática, isso é igual à Diferença do Parâmetro de Coleta.
    - Resultado = Qtd. crítico - Limite crítico.
    - Result % = Qtd. crítico / Qtd. coletas.
    """
    cfg_critico = INDICADORES["Parâmetro de Coleta Crítico"]
    cfg_parametro = INDICADORES["Parâmetro de Coleta"]

    d_limite = aplicar_filtro_base(df_raw, cfg_critico, filial).copy()
    d_param = aplicar_filtro_base(df_raw, cfg_parametro, filial).copy()

    if d_limite.empty and d_param.empty:
        return pd.DataFrame()

    chaves = ["FILIAL", "REFERÊNCIA", "ANO", "MÊS", "DIA", "MÊS_ORDEM", "MÊS_NOME"]

    if not d_param.empty:
        base_param = d_param[chaves].copy()

        base_param["QTD_TOTAL_CALC"] = serie_numerica_por_coluna(
            d_param,
            nomes_preferidos=["VALOR REF 01", "QTD. COLETAS", "QTD COLETAS", "QUANTIDADE COLETADA"],
            indice_1_base=10,
        )

        # IMPORTANTE:
        # Nesta base, o VALOR REF 02 do Parâmetro de Coleta representa a DIFERENÇA,
        # que é exatamente:
        # Qtd. coletas - Qtd. coletas normais.
        #
        # Essa diferença é o que o Crítico precisa usar como Qtd. crítico.
        base_param["DIFERENCA_PARAMETRO_CALC"] = serie_numerica_por_coluna(
            d_param,
            nomes_preferidos=["VALOR REF 02", "DIFERENCA", "DIFER.", "QTD. CRITICO", "QTD CRITICO"],
            indice_1_base=12,
        )

        param_agg = (
            base_param.groupby(chaves, as_index=False)
            .agg({
                "QTD_TOTAL_CALC": "sum",
                "DIFERENCA_PARAMETRO_CALC": "sum",
            })
        )

        # Qtd. coletas normais é derivada apenas para manter rastreabilidade:
        # Qtd. normais = Qtd. coletas - Diferença
        param_agg["QTD_NORMAIS_CALC"] = (
            param_agg["QTD_TOTAL_CALC"] - param_agg["DIFERENCA_PARAMETRO_CALC"]
        )
    else:
        param_agg = pd.DataFrame(
            columns=chaves + ["QTD_TOTAL_CALC", "DIFERENCA_PARAMETRO_CALC", "QTD_NORMAIS_CALC"]
        )

    if not d_limite.empty:
        base_limite = d_limite[chaves].copy()
        base_limite["META_QUALIDADE_CALC"] = pd.to_numeric(
            d_limite.get("META", 0), errors="coerce"
        ).fillna(0)

        limite_agg = (
            base_limite.groupby(chaves, as_index=False)
            .agg({"META_QUALIDADE_CALC": "sum"})
        )
    else:
        limite_agg = pd.DataFrame(columns=chaves + ["META_QUALIDADE_CALC"])

    if param_agg.empty:
        out = limite_agg.copy()
        out["QTD_TOTAL_CALC"] = 0
        out["DIFERENCA_PARAMETRO_CALC"] = 0
        out["QTD_NORMAIS_CALC"] = 0
    elif limite_agg.empty:
        out = param_agg.copy()
        out["META_QUALIDADE_CALC"] = 0
    else:
        out = pd.merge(param_agg, limite_agg, on=chaves, how="outer")

    for col in ["QTD_TOTAL_CALC", "DIFERENCA_PARAMETRO_CALC", "QTD_NORMAIS_CALC", "META_QUALIDADE_CALC"]:
        if col not in out.columns:
            out[col] = 0
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0)

    # Fórmula definitiva:
    # Qtd. crítico = mesma Diferença do Parâmetro de Coleta
    # Também equivale a: Qtd. coletas - Qtd. coletas normais
    out["QTD_CRITICO_CALC"] = out["DIFERENCA_PARAMETRO_CALC"]
    out["QTD_SUCESSO_CALC"] = out["QTD_CRITICO_CALC"]

    # Resultado = Qtd. crítico - Limite crítico
    out["DIFERENCA_CALC"] = out["QTD_CRITICO_CALC"] - out["META_QUALIDADE_CALC"]

    # Result % = Qtd. crítico / Qtd. coletas
    out["RESULTADO_QUALIDADE_CALC"] = (
        out["QTD_CRITICO_CALC"] / out["QTD_TOTAL_CALC"].replace(0, pd.NA)
    )

    # Compatibilidade com blocos antigos do app
    out["META_CALC"] = out["META_QUALIDADE_CALC"]
    out["REALIZADO_CALC"] = out["QTD_CRITICO_CALC"]
    out["ATINGIMENTO_CALC"] = out["RESULTADO_QUALIDADE_CALC"]

    return (
        out.sort_values(["ANO", "MÊS", "FILIAL"])
        .replace([float("inf"), float("-inf")], pd.NA)
        .reset_index(drop=True)
    )


def resumo_qualidade_por_grupo(grupo, indicador):
    modelo = modelo_qualidade(indicador)

    if grupo is None or grupo.empty:
        return {
            "Meta": None,
            "Qtd. Coletas": 0,
            "Qtd. Sucesso": 0,
            "Diferença": None,
            "Resultado %": None,
        }

    qtd_total = pd.to_numeric(grupo.get("QTD_TOTAL_CALC"), errors="coerce").fillna(0).sum()
    qtd_sucesso_base = pd.to_numeric(grupo.get("QTD_SUCESSO_CALC"), errors="coerce").fillna(0).sum()

    if modelo == "parametro_coleta_critico":
        # Parâmetro de Coleta Crítico:
        # Qtd. crítico = mesma Diferença do Parâmetro de Coleta.
        # Ou seja: Qtd. crítico = Qtd. coletas - Qtd. coletas normais.
        meta = pd.to_numeric(grupo.get("META_QUALIDADE_CALC"), errors="coerce").fillna(0).sum()

        if "QTD_CRITICO_CALC" in grupo.columns:
            qtd_sucesso = pd.to_numeric(grupo.get("QTD_CRITICO_CALC"), errors="coerce").fillna(0).sum()
        elif "DIFERENCA_PARAMETRO_CALC" in grupo.columns:
            qtd_sucesso = pd.to_numeric(grupo.get("DIFERENCA_PARAMETRO_CALC"), errors="coerce").fillna(0).sum()
        elif "QTD_NORMAIS_CALC" in grupo.columns:
            qtd_normais = pd.to_numeric(grupo.get("QTD_NORMAIS_CALC"), errors="coerce").fillna(0).sum()
            qtd_sucesso = qtd_total - qtd_normais
        else:
            qtd_sucesso = qtd_sucesso_base

        diferenca = qtd_sucesso - meta
        resultado = qtd_sucesso / qtd_total if qtd_total > 0 else None
    elif modelo == "parametro_coleta":
        # No layout do Parâmetro de Coleta da planilha:
        # - Qtd. coletas normais = quantidade de coletas dentro do padrão
        # - Diferença = Qtd. coletas - Qtd. coletas normais
        #
        # Nesta base, o campo carregado em QTD_SUCESSO_CALC vinha invertido em relação
        # ao layout exibido. Então aqui fazemos a correção final para a tabela ficar igual
        # à planilha do usuário.
        meta_serie = pd.to_numeric(grupo.get("META_QUALIDADE_CALC"), errors="coerce")
        peso = pd.to_numeric(grupo.get("QTD_TOTAL_CALC"), errors="coerce").fillna(0)
        validos = meta_serie.notna() & (meta_serie > 0) & (peso > 0)

        if validos.any() and peso[validos].sum() > 0:
            metas_validas = meta_serie[validos].round(6).dropna().unique()
            if len(metas_validas) == 1:
                meta = float(metas_validas[0])
            else:
                meta = (meta_serie[validos] * peso[validos]).sum() / peso[validos].sum()
        else:
            meta = None

        diferenca = qtd_sucesso_base
        qtd_sucesso = qtd_total - diferenca
        resultado = qtd_sucesso / qtd_total if qtd_total > 0 else None
    else:
        # Avaliação de Equipe:
        # Fórmula da meta total conforme a planilha:
        # SOMARPRODUTO(META; QTD. COLETAS) / SOMA(QTD. COLETAS)
        meta_serie = pd.to_numeric(grupo.get("META_QUALIDADE_CALC"), errors="coerce")
        peso = pd.to_numeric(grupo.get("QTD_TOTAL_CALC"), errors="coerce").fillna(0)

        validos = meta_serie.notna() & (meta_serie > 0) & (peso > 0)

        if validos.any() and peso[validos].sum() > 0:
            metas_validas = meta_serie[validos].round(6).dropna().unique()
            if len(metas_validas) == 1:
                meta = float(metas_validas[0])
            else:
                meta = (meta_serie[validos] * peso[validos]).sum() / peso[validos].sum()
        else:
            meta = None

        qtd_sucesso = qtd_sucesso_base
        diferenca = qtd_total - qtd_sucesso
        resultado = qtd_sucesso / qtd_total if qtd_total > 0 else None

    return {
        "Meta": meta,
        "Qtd. Coletas": qtd_total,
        "Qtd. Sucesso": qtd_sucesso,
        "Diferença": diferenca,
        "Resultado %": resultado,
    }

def renomear_colunas_qualidade_para_exibicao(df_exibir, indicador):
    rot = rotulos_qualidade(indicador)
    return df_exibir.rename(columns={
        "Meta": rot["meta"],
        "Qtd. Coletas": rot["qtd_total"],
        "Qtd. Sucesso": rot["qtd_sucesso"],
        "Diferença": rot["diferenca"],
        "Resultado %": rot["resultado"],
    })

def cor_resultado_qualidade_por_linha(row, indicador=None):
    estilos = ["" for _ in row.index]

    if "Resultado %" not in row.index:
        return estilos

    resultado = row["Resultado %"]
    meta = row["Meta"] if "Meta" in row.index else None

    if pd.isna(resultado):
        return estilos

    idx_result = list(row.index).index("Resultado %")

    # Crítico: quanto menor o percentual crítico, melhor.
    # Para o Result %, até 10% fica verde; acima disso, laranja.
    if indicador and modelo_qualidade(indicador) == "parametro_coleta_critico":
        estilos[idx_result] = f"color: {COR_VERDE}; font-weight:bold" if resultado <= 0.10 else f"color: {COR_LARANJA}; font-weight:bold"
        return estilos

    # Avaliação e Parâmetro: quanto maior, melhor.
    if pd.notna(meta):
        estilos[idx_result] = f"color: {COR_VERDE}; font-weight:bold" if resultado >= meta else f"color: {COR_LARANJA}; font-weight:bold"

    return estilos


def cor_diferenca_qualidade(v):
    if pd.isna(v):
        return ""

    # Para avaliação/parâmetro, diferença positiva significa falta para atingir o ideal.
    # Por padrão, deixa positivo em laranja e zero/negativo em verde.
    return f"color: {COR_VERDE}; font-weight:bold" if v <= 0 else f"color: {COR_LARANJA}; font-weight:bold"



def preparar_tabela_qualidade_exibicao(df_exibir, indicador):
    """
    Prepara a tabela de Qualidade apenas para exibição.

    Para Parâmetro de Coleta Crítico:
    - troca o nome visual da coluna "Diferença" para "Resultado".
    - mantém o cálculo interno em "Diferença" para não quebrar funções antigas.

    Para os demais indicadores, mantém o padrão.
    """
    d = df_exibir.copy()
    rot = rotulos_qualidade(indicador)

    rename_map = {
        "Qtd. Sucesso": rot["qtd_sucesso"],
    }

    if modelo_qualidade(indicador) == "parametro_coleta_critico":
        rename_map["Diferença"] = "Resultado"

    return d.rename(columns=rename_map)


def colunas_qualidade_exibicao(indicador, incluir_ano=False, incluir_mom=False, incluir_acumulado=False):
    """Define a ordem/visibilidade das colunas dos indicadores de qualidade."""
    base = []
    if incluir_ano:
        base += ["ANO", "MÊS"]

    rot = rotulos_qualidade(indicador)
    col_qtd_sucesso = rot["qtd_sucesso"]
    col_diferenca = "Resultado" if modelo_qualidade(indicador) == "parametro_coleta_critico" else "Diferença"

    base += [
        "Mês",
        "Meta",
        "Qtd. Coletas",
        col_qtd_sucesso,
        col_diferenca,
        "Resultado %",
    ]

    # Para Parâmetro de Coleta, o usuário pediu para remover o Acumulado.
    if incluir_acumulado and modelo_qualidade(indicador) != "parametro_coleta":
        base.append("Acumulado")

    if incluir_mom:
        base.append("MoM_%")

    return base


def aplicar_estilo_diferenca_qualidade(styler, indicador, subset=("Diferença",)):
    """Aplica cor na diferença/resultado apenas quando fizer sentido."""
    if modelo_qualidade(indicador) == "parametro_coleta":
        return styler

    try:
        colunas_existentes = list(styler.data.columns)

        subset_lista = list(subset)
        if modelo_qualidade(indicador) == "parametro_coleta_critico":
            subset_lista = ["Resultado" if c == "Diferença" else c for c in subset_lista]

        subset_existente = [c for c in subset_lista if c in colunas_existentes]

        if not subset_existente:
            return styler

        return styler.map(cor_diferenca_qualidade, subset=subset_existente)
    except Exception:
        return styler

@st.cache_data(show_spinner=False)
def filtrar(df, indicador, filial):
    cfg = INDICADORES[indicador]

    if cfg["tipo"] == "moto_margem":
        return consolidar_pneus_moto(aplicar_filtro_base(df, cfg, filial))

    if cfg["tipo"] == "despesa_geral_receita":
        return consolidar_despesa_geral(aplicar_filtro_base(df, cfg, filial))

    if cfg["tipo"] == "resultado_financeiro":
        return consolidar_resultado_financeiro(aplicar_filtro_base(df, cfg, filial))

    if cfg["tipo"] == "tecfil":
        return consolidar_tecfil(df, filial, indicador)

    if cfg["tipo"] == "qualidade":
        if indicador == "Parâmetro de Coleta Crítico":
            return consolidar_parametro_coleta_critico(df, filial)
        return consolidar_qualidade(aplicar_filtro_base(df, cfg, filial), indicador)

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

    for i in range(1, 13):
        sub = base_ano[base_ano["MÊS"] == i]
        if not sub.empty:
            resumo = resumo_moto_por_grupo(sub)
            meta = resumo["Meta %"]
            compra = resumo["Compra"]
            faturamento = resumo["Faturamento"]
            margem = resumo["Margem Bruta"]
            tx = resumo["Tx. Sucesso"]
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

    # TOTAL do Pneus Moto seguindo exatamente a fórmula do Excel:
    # Meta Total = SOMARPRODUTO(Meta mensal ; Faturamento mensal) / SOMA(Faturamento mensal)
    tabela_mensal_tmp = pd.DataFrame(rows)

    compra_total = pd.to_numeric(tabela_mensal_tmp.get("Compra"), errors="coerce").fillna(0).sum()
    faturamento_total = pd.to_numeric(tabela_mensal_tmp.get("Faturamento"), errors="coerce").fillna(0).sum()
    margem_total = faturamento_total - compra_total
    tx_total = margem_total / faturamento_total if faturamento_total > 0 else None

    metas_mensais = pd.to_numeric(tabela_mensal_tmp.get("Meta"), errors="coerce")
    faturamentos_mensais = pd.to_numeric(tabela_mensal_tmp.get("Faturamento"), errors="coerce")

    mask_meta_total = metas_mensais.notna() & faturamentos_mensais.notna() & (faturamentos_mensais != 0)
    if mask_meta_total.any() and faturamentos_mensais[mask_meta_total].sum() > 0:
        meta_total = (metas_mensais[mask_meta_total] * faturamentos_mensais[mask_meta_total]).sum() / faturamentos_mensais[mask_meta_total].sum()
    else:
        meta_total = None

    rows.append({
        "Mês": "TOTAL",
        "Meta": meta_total,
        "Compra": compra_total,
        "Faturamento": faturamento_total,
        "Margem Bruta": margem_total,
        "Tx. Sucesso": tx_total,
        "Acumulado": margem_total,
    })

    tabela = pd.DataFrame(rows)

    # Pneus Velhos Moto:
    # 2023, 2024 e 2025 não possuem meta na planilha.
    # Portanto a meta deve ficar vazia em todos os meses e também no TOTAL.
    if int(ano) < 2026:
        tabela["Meta"] = pd.NA

    return tabela

def meta_mensal_moto(grupo):
    """
    Retorna a meta % do mês para Pneus Velhos Moto.

    Diferente da meta total, a meta mensal não deve depender do faturamento.
    Ela deve refletir a referência/meta cadastrada para aquele mês.
    """
    if grupo is None or grupo.empty or "META_PERCENTUAL_CALC" not in grupo.columns:
        return None

    metas = pd.to_numeric(grupo.get("META_PERCENTUAL_CALC"), errors="coerce").dropna()
    metas = metas[metas != 0]

    if metas.empty:
        return None

    # Normalmente a meta do mês é única. Quando houver mais de uma linha,
    # usa a última meta válida do mês, preservando a referência mais recente.
    return metas.iloc[-1]


def meta_ponderada_moto(grupo):
    """
    Meta total/período para Pneus Velhos Moto.

    Fórmula correta conforme Excel:
    =SEERRO(((Meta%_Jan*Faturamento_Jan)+...)/(Faturamento_Jan+...);0)

    Em termos matemáticos:
    Meta Total = SOMARPRODUTO(Meta % ; Faturamento) / SOMA(Faturamento)

    Observação:
    - A meta total NÃO é média simples.
    - A meta total é ponderada pelo faturamento de cada mês/linha.
    - Meses sem faturamento entram com peso zero.
    """
    if grupo is None or grupo.empty:
        return None

    faturamento = pd.to_numeric(grupo.get("FATURAMENTO_MOTO_CALC"), errors="coerce")
    meta_pct = pd.to_numeric(grupo.get("META_PERCENTUAL_CALC"), errors="coerce")

    valido = faturamento.notna() & meta_pct.notna() & (faturamento != 0)
    faturamento_valido = faturamento[valido]
    meta_valida = meta_pct[valido]

    total_faturamento = faturamento_valido.sum()

    if total_faturamento > 0:
        return (meta_valida * faturamento_valido).sum() / total_faturamento

    return None


def resumo_moto_por_grupo(grupo):
    faturamento = pd.to_numeric(grupo.get("FATURAMENTO_MOTO_CALC"), errors="coerce").fillna(0).sum()
    compra = pd.to_numeric(grupo.get("COMPRA_CALC"), errors="coerce").fillna(0).sum()
    margem = faturamento - compra

    # Se o grupo representa apenas um mês, a meta exibida deve ser a meta do mês.
    # Se representa vários meses, a meta deve ser ponderada pelo faturamento.
    try:
        meses_validos = pd.to_numeric(grupo.get("MÊS"), errors="coerce").dropna().unique()
    except Exception:
        meses_validos = []

    if len(meses_validos) == 1:
        meta_pct = meta_mensal_moto(grupo)
    else:
        meta_pct = meta_ponderada_moto(grupo)

    meta_margem = faturamento * meta_pct if meta_pct is not None and pd.notna(meta_pct) else None
    gap = margem - meta_margem if meta_margem is not None and pd.notna(meta_margem) else None
    tx_sucesso = margem / faturamento if faturamento > 0 else None

    return {
        "Faturamento": faturamento,
        "Compra": compra,
        "Margem Bruta": margem,
        "Meta %": meta_pct,
        "Meta Margem (R$)": meta_margem,
        "Gap (R$)": gap,
        "Tx. Sucesso": tx_sucesso,
    }



def remover_meta_moto_anos_sem_meta(df_tabela):
    """
    Para Pneus Velhos Moto, os anos de 2023, 2024 e 2025 não possuem meta.
    A meta passa a ser considerada a partir de 2026.
    """
    d = df_tabela.copy()
    if "ANO" in d.columns:
        mask = pd.to_numeric(d["ANO"], errors="coerce") < 2026
    elif "Ano" in d.columns:
        mask = pd.to_numeric(d["Ano"], errors="coerce") < 2026
    else:
        return d

    for col in ["Meta %", "Meta", "META", "Meta Margem (R$)", "Gap (R$)", "Gap"]:
        if col in d.columns:
            d.loc[mask, col] = pd.NA

    return d




def cor_resultado_financeiro_por_linha(row):
    estilos = ["" for _ in row.index]

    if "Resultado R$" in row.index and pd.notna(row["Resultado R$"]):
        idx = list(row.index).index("Resultado R$")
        estilos[idx] = f"color: {COR_VERDE}; font-weight:bold" if row["Resultado R$"] >= 0 else f"color: {COR_LARANJA}; font-weight:bold"

    if "Resultado %" in row.index and pd.notna(row["Resultado %"]):
        idx = list(row.index).index("Resultado %")
        # Resultado % >= Meta % é bom
        meta = row["Meta %"] if "Meta %" in row.index else None
        if pd.notna(meta):
            estilos[idx] = f"color: {COR_VERDE}; font-weight:bold" if row["Resultado %"] >= meta else f"color: {COR_LARANJA}; font-weight:bold"

    return estilos

def cor_tx_sucesso_despesa_geral_por_linha(row):
    """
    Para Despesa Geral com a fórmula:
    Tx. Sucesso = Despesa / Receita

    Interpretação:
    - Verde quando Tx. Sucesso <= Limite %.
    - Laranja quando Tx. Sucesso > Limite %.
    - Se o ano não possui limite, mantém sem destaque.
    """
    estilos = ["" for _ in row.index]

    if "Tx. Sucesso" not in row.index:
        return estilos

    ano_val = None
    for col_ano in ["ANO", "Ano"]:
        if col_ano in row.index and pd.notna(row[col_ano]):
            try:
                ano_val = int(row[col_ano])
            except Exception:
                ano_val = None

    if ano_val is not None and ano_val < 2026:
        return estilos

    tx = row["Tx. Sucesso"]
    limite = row["Limite %"] if "Limite %" in row.index else None

    if pd.notna(tx):
        idx = list(row.index).index("Tx. Sucesso")
        if limite is not None and pd.notna(limite):
            estilos[idx] = f"color: {COR_VERDE}; font-weight:bold" if tx <= limite else f"color: {COR_LARANJA}; font-weight:bold"
        else:
            estilos[idx] = f"color: {COR_LARANJA}; font-weight:bold"

    return estilos

def cor_tx_sucesso_moto_por_linha(row):
    """
    Para Pneus Velhos Moto:
    - Taxa de Sucesso verde quando estiver acima ou igual à Meta %.
    - Taxa de Sucesso laranja quando estiver abaixo da Meta %.
    - Se não existir meta, mantém sem destaque.
    """
    estilos = ["" for _ in row.index]

    col_tx = None
    for nome in ["Tx. Sucesso", "Ating.", "Atingimento"]:
        if nome in row.index:
            col_tx = nome
            break

    col_meta = None
    for nome in ["Meta %", "Meta", "META"]:
        if nome in row.index:
            col_meta = nome
            break

    if col_tx is None or col_meta is None:
        return estilos

    tx = row[col_tx]
    meta = row[col_meta]

    if pd.notna(tx) and pd.notna(meta):
        idx = list(row.index).index(col_tx)
        if tx >= meta:
            estilos[idx] = f"color: {COR_VERDE}; font-weight:bold"
        else:
            estilos[idx] = f"color: {COR_LARANJA}; font-weight:bold"

    return estilos

def tabela_completa_ano(d, ano, indicador):
    if eh_moto_margem(indicador):
        rows = []
        base_ano = d[d["ANO"] == ano].copy()
        acumulado = 0

        for i in range(1, 13):
            mes_nome = MESES_MAPA[i]
            sub = base_ano[base_ano["MÊS"] == i]

            if not sub.empty:
                resumo = resumo_moto_por_grupo(sub)
                acumulado += resumo["Margem Bruta"]
                rows.append({
                    "Mês": mes_nome,
                    "Meta %": resumo["Meta %"],
                    "Compra": resumo["Compra"],
                    "Faturamento": resumo["Faturamento"],
                    "Margem Bruta": resumo["Margem Bruta"],
                    "Tx. Sucesso": resumo["Tx. Sucesso"],
                    "Acumulado": acumulado,
                })
            else:
                rows.append({
                    "Mês": mes_nome,
                    "Meta %": None,
                    "Compra": None,
                    "Faturamento": None,
                    "Margem Bruta": None,
                    "Tx. Sucesso": None,
                    "Acumulado": acumulado if acumulado != 0 else None,
                })

        resumo_total = resumo_moto_por_grupo(base_ano) if not base_ano.empty else {
            "Meta %": None, "Compra": 0, "Faturamento": 0, "Margem Bruta": 0, "Tx. Sucesso": None
        }

        rows.append({
            "Mês": "TOTAL",
            "Meta %": resumo_total["Meta %"],
            "Compra": resumo_total["Compra"],
            "Faturamento": resumo_total["Faturamento"],
            "Margem Bruta": resumo_total["Margem Bruta"],
            "Tx. Sucesso": resumo_total["Tx. Sucesso"],
            "Acumulado": resumo_total["Margem Bruta"],
        })

        return pd.DataFrame(rows)

    if eh_despesa_geral(indicador):
        rows = []
        base_ano = d[d["ANO"] == ano].copy()
        acumulado = 0

        for i in range(1, 13):
            mes_nome = MESES_MAPA[i]
            sub = base_ano[base_ano["MÊS"] == i]

            if not sub.empty:
                resumo = resumo_despesa_geral_por_grupo(sub)
                if pd.notna(resumo["Resultado R$"]):
                    acumulado += resumo["Resultado R$"]
                    acumulado_exibir = acumulado
                else:
                    acumulado_exibir = None

                rows.append({
                    "Mês": mes_nome,
                    "Limite %": resumo["Limite %"],
                    "Despesa": resumo["Despesa"],
                    "Receita": resumo["Receita"],
                    "Resultado R$": resumo["Resultado R$"],
                    "Tx. Sucesso": resumo["Tx. Sucesso"],
                    "Acumulado": acumulado_exibir,
                })
            else:
                rows.append({
                    "Mês": mes_nome,
                    "Limite %": None,
                    "Despesa": None,
                    "Receita": None,
                    "Resultado R$": None,
                    "Tx. Sucesso": None,
                    "Acumulado": acumulado if acumulado != 0 else None,
                })

        resumo_total = resumo_despesa_geral_por_grupo(base_ano) if not base_ano.empty else {
            "Limite %": None, "Despesa": 0, "Receita": 0, "Resultado R$": 0, "Tx. Sucesso": None
        }

        rows.append({
            "Mês": "TOTAL",
            "Limite %": resumo_total["Limite %"],
            "Despesa": resumo_total["Despesa"],
            "Receita": resumo_total["Receita"],
            "Resultado R$": resumo_total["Resultado R$"],
            "Tx. Sucesso": resumo_total["Tx. Sucesso"],
            "Acumulado": resumo_total["Resultado R$"],
        })

        return pd.DataFrame(rows)

    if eh_resultado_financeiro(indicador):
        rows = []
        base_ano = d[d["ANO"] == ano].copy()
        acumulado = 0

        for i in range(1, 13):
            mes_nome = MESES_MAPA[i]
            sub = base_ano[base_ano["MÊS"] == i]

            if not sub.empty:
                resumo = resumo_resultado_financeiro_por_grupo(sub)
                if pd.notna(resumo["Resultado R$"]):
                    acumulado += resumo["Resultado R$"]
                    acumulado_exibir = acumulado
                else:
                    acumulado_exibir = None

                rows.append({
                    "Mês": mes_nome,
                    "Meta %": resumo["Meta %"],
                    "Despesa": resumo["Despesa"],
                    "Receita": resumo["Receita"],
                    "Resultado R$": resumo["Resultado R$"],
                    "Resultado %": resumo["Resultado %"],
                    "Acumulado": acumulado_exibir,
                })
            else:
                rows.append({
                    "Mês": mes_nome,
                    "Meta %": None,
                    "Despesa": None,
                    "Receita": None,
                    "Resultado R$": None,
                    "Resultado %": None,
                    "Acumulado": acumulado if acumulado != 0 else None,
                })

        resumo_total = resumo_resultado_financeiro_por_grupo(base_ano)

        rows.append({
            "Mês": "TOTAL",
            "Meta %": resumo_total["Meta %"],
            "Despesa": resumo_total["Despesa"],
            "Receita": resumo_total["Receita"],
            "Resultado R$": resumo_total["Resultado R$"],
            "Resultado %": resumo_total["Resultado %"],
            "Acumulado": resumo_total["Resultado R$"],
        })

        return pd.DataFrame(rows)

    if eh_qualidade(indicador):
        rows = []
        base_ano = d[d["ANO"] == ano].copy()
        acumulado = 0

        for i in range(1, 13):
            mes_nome = MESES_MAPA[i]
            sub = base_ano[base_ano["MÊS"] == i]

            if not sub.empty:
                resumo = resumo_qualidade_por_grupo(sub, indicador)
                acumulado += resumo["Diferença"] if pd.notna(resumo["Diferença"]) else 0

                rows.append({
                    "Mês": mes_nome,
                    "Meta": resumo["Meta"],
                    "Qtd. Coletas": resumo["Qtd. Coletas"],
                    "Qtd. Sucesso": resumo["Qtd. Sucesso"],
                    "Diferença": resumo["Diferença"],
                    "Resultado %": resumo["Resultado %"],
                    "Acumulado": acumulado,
                })
            else:
                rows.append({
                    "Mês": mes_nome,
                    "Meta": None,
                    "Qtd. Coletas": None,
                    "Qtd. Sucesso": None,
                    "Diferença": None,
                    "Resultado %": None,
                    "Acumulado": acumulado if acumulado != 0 else None,
                })

        resumo_total = resumo_qualidade_por_grupo(base_ano, indicador)

        rows.append({
            "Mês": "TOTAL",
            "Meta": resumo_total["Meta"],
            "Qtd. Coletas": resumo_total["Qtd. Coletas"],
            "Qtd. Sucesso": resumo_total["Qtd. Sucesso"],
            "Diferença": resumo_total["Diferença"],
            "Resultado %": resumo_total["Resultado %"],
            "Acumulado": resumo_total["Diferença"],
        })

        return pd.DataFrame(rows)

    if eh_tecfil(indicador):
        rows = []
        base_ano = d[d["ANO"] == ano].copy()
        acum_kg = 0
        acum_rs = 0

        for i in range(1, 13):
            mes_nome = MESES_MAPA[i]
            sub = base_ano[base_ano["MÊS"] == i]

            if not sub.empty:
                resumo = resumo_tecfil_por_grupo(sub)
                acum_kg += resumo["Dif. KG"]
                acum_rs += resumo["Dif. R$"]

                rows.append({
                    "Mês": mes_nome,
                    "Meta KG": resumo["Meta KG"],
                    "Meta R$": resumo["Meta R$"],
                    "Realizado KG": resumo["Realizado KG"],
                    "Realizado R$": resumo["Realizado R$"],
                    "% Dif. KG": resumo["% Dif. KG"],
                    "% Dif. R$": resumo["% Dif. R$"],
                    "Dif. KG": resumo["Dif. KG"],
                    "Dif. R$": resumo["Dif. R$"],
                    "Acum. KG": acum_kg,
                    "Acum. R$": acum_rs,
                })
            else:
                rows.append({
                    "Mês": mes_nome,
                    "Meta KG": None,
                    "Meta R$": None,
                    "Realizado KG": None,
                    "Realizado R$": None,
                    "% Dif. KG": None,
                    "% Dif. R$": None,
                    "Dif. KG": None,
                    "Dif. R$": None,
                    "Acum. KG": acum_kg if acum_kg != 0 else None,
                    "Acum. R$": acum_rs if acum_rs != 0 else None,
                })

        resumo_total = resumo_tecfil_por_grupo(base_ano)

        rows.append({
            "Mês": "TOTAL",
            "Meta KG": resumo_total["Meta KG"],
            "Meta R$": resumo_total["Meta R$"],
            "Realizado KG": resumo_total["Realizado KG"],
            "Realizado R$": resumo_total["Realizado R$"],
            "% Dif. KG": resumo_total["% Dif. KG"],
            "% Dif. R$": resumo_total["% Dif. R$"],
            "Dif. KG": resumo_total["Dif. KG"],
            "Dif. R$": resumo_total["Dif. R$"],
            "Acum. KG": resumo_total["Dif. KG"],
            "Acum. R$": resumo_total["Dif. R$"],
        })

        return pd.DataFrame(rows)

    if eh_despesa_manutencao(indicador):
        rows = []
        base_ano = d[d["ANO"] == ano].copy()
        acumulado = 0

        mensal = (
            base_ano.groupby("MÊS", as_index=False)
            .agg({"REALIZADO_CALC": "sum", "META_CALC": "sum"})
            .sort_values("MÊS")
        )

        for i in range(1, 13):
            mes_nome = MESES_MAPA[i]
            sub = mensal[mensal["MÊS"] == i]

            if len(sub) > 0:
                limite = sub["META_CALC"].values[0]
                despesa = sub["REALIZADO_CALC"].values[0]
                resultado_rs = limite - despesa if pd.notna(limite) and pd.notna(despesa) else None
                resultado_pct = 1 - (despesa / limite) if pd.notna(limite) and limite > 0 else None

                if pd.notna(resultado_rs):
                    acumulado += resultado_rs
                    acumulado_exibir = acumulado
                else:
                    acumulado_exibir = None
            else:
                limite = None
                despesa = None
                resultado_rs = None
                resultado_pct = None
                acumulado_exibir = acumulado if acumulado != 0 else None

            rows.append({
                "Mês": mes_nome,
                "Limite": limite,
                "Despesa": despesa,
                "Result. R$": resultado_rs,
                "Result. %": resultado_pct,
                "Acumulado": acumulado_exibir,
            })

        total_limite = base_ano["META_CALC"].sum()
        total_despesa = base_ano["REALIZADO_CALC"].sum()
        total_resultado = total_limite - total_despesa
        total_resultado_pct = 1 - (total_despesa / total_limite) if total_limite > 0 else None

        rows.append({
            "Mês": "TOTAL",
            "Limite": total_limite,
            "Despesa": total_despesa,
            "Result. R$": total_resultado,
            "Result. %": total_resultado_pct,
            "Acumulado": total_resultado,
        })

        # Linha MÉDIA removida conforme solicitado.
        # Mantém apenas meses e TOTAL.
        return pd.DataFrame(rows)

    if eh_despesa_hora_extra(indicador):
        rows = []
        base_ano = d[d["ANO"] == ano].copy()

        mensal = (
            base_ano.groupby("MÊS", as_index=False)
            .agg({"META_CALC": "sum", "REALIZADO_CALC": "sum", "SALARIO_CALC": "sum"})
            .sort_values("MÊS")
        )

        for i in range(1, 13):
            mes_nome = MESES_MAPA[i]
            sub = mensal[mensal["MÊS"] == i]

            if len(sub) > 0:
                limite = sub["META_CALC"].values[0]
                pago_he = sub["REALIZADO_CALC"].values[0]
                salario = sub["SALARIO_CALC"].values[0]
                he_folha = pago_he / salario if salario > 0 else None
                saldo = limite - pago_he
                # Fórmula conforme planilha:
                # Result.% = (Pago em Hora Extra / Limite) - 1
                resultado_pct = (pago_he / limite - 1) if limite > 0 else None
            else:
                limite = None
                pago_he = None
                salario = None
                he_folha = None
                saldo = None
                resultado_pct = None

            rows.append({
                "Mês": mes_nome,
                "Limite": limite,
                "Pago em Hora Extra": pago_he,
                "Salário": salario,
                "HE da Folha": he_folha,
                "Saldo do Limite": saldo,
                "Resultado %": resultado_pct,
            })

        total_limite = base_ano["META_CALC"].sum()
        total_pago = base_ano["REALIZADO_CALC"].sum()
        total_salario = base_ano["SALARIO_CALC"].sum() if "SALARIO_CALC" in base_ano.columns else 0
        total_he_folha = total_pago / total_salario if total_salario > 0 else None
        total_saldo = total_limite - total_pago
        # Fórmula conforme planilha:
        # Result.% = (Pago em Hora Extra / Limite) - 1
        total_resultado = (total_pago / total_limite - 1) if total_limite > 0 else None

        rows.append({
            "Mês": "TOTAL",
            "Limite": total_limite,
            "Pago em Hora Extra": total_pago,
            "Salário": total_salario,
            "HE da Folha": total_he_folha,
            "Saldo do Limite": total_saldo,
            "Resultado %": total_resultado,
        })

        return pd.DataFrame(rows)

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

            if eh_despesa_manutencao(indicador):
                gap = meta - real
                ating = real / meta if meta > 0 else None
            elif despesa:
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

    if eh_despesa_manutencao(indicador):
        total_gap = total_meta - total_real
        total_ating = total_real / total_meta if total_meta > 0 else None
    elif despesa:
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
    
@st.cache_data(show_spinner=False)
def calcular_mom(d, indicador):
    if eh_moto_margem(indicador):
        linhas = []
        acumulado_por_ano = {}

        for (ano, mes, mes_nome, mes_ordem), sub in d.groupby(["ANO", "MÊS", "MÊS_NOME", "MÊS_ORDEM"]):
            resumo = resumo_moto_por_grupo(sub)
            acumulado_por_ano.setdefault(ano, 0)
            acumulado_por_ano[ano] += resumo["Margem Bruta"]

            linhas.append({
                "ANO": ano,
                "MÊS": mes,
                "Mês": mes_nome,
                "META": resumo["Meta %"],
                "Compra": resumo["Compra"],
                "Realizado": resumo["Faturamento"],
                "Margem Bruta": resumo["Margem Bruta"],
                "Gap": resumo["Gap (R$)"],
                "Ating.": resumo["Tx. Sucesso"],
                "Acumulado": acumulado_por_ano[ano],
                "MÊS_ORDEM": mes_ordem,
            })

        base = pd.DataFrame(linhas).sort_values("MÊS_ORDEM").reset_index(drop=True)

        # Pneus Velhos Moto: 2023, 2024 e 2025 não possuem meta
        mask_sem_meta = pd.to_numeric(base["ANO"], errors="coerce") < 2026
        base.loc[mask_sem_meta, "META"] = pd.NA
        base.loc[mask_sem_meta, "Gap"] = pd.NA

        base["MoM_%"] = base["Realizado"].pct_change() * 100
        return base.replace([float("inf"), float("-inf")], pd.NA)

    if eh_despesa_geral(indicador):
        linhas = []
        acumulado_por_ano = {}

        for (ano, mes, mes_nome, mes_ordem), sub in d.groupby(["ANO", "MÊS", "MÊS_NOME", "MÊS_ORDEM"]):
            resumo = resumo_despesa_geral_por_grupo(sub)
            acumulado_por_ano.setdefault(ano, 0)
            if pd.notna(resumo["Resultado R$"]):
                acumulado_por_ano[ano] += resumo["Resultado R$"]
                acumulado_exibir = acumulado_por_ano[ano]
            else:
                acumulado_exibir = None

            linhas.append({
                "ANO": ano,
                "MÊS": mes,
                "Mês": mes_nome,
                "Limite %": resumo["Limite %"],
                "Despesa": resumo["Despesa"],
                "Receita": resumo["Receita"],
                "Resultado R$": resumo["Resultado R$"],
                "Tx. Sucesso": resumo["Tx. Sucesso"],
                "Acumulado": acumulado_exibir,
                "MÊS_ORDEM": mes_ordem,
            })

        base = pd.DataFrame(linhas).sort_values("MÊS_ORDEM").reset_index(drop=True)
        base["MoM_%"] = base["Despesa"].pct_change() * 100
        return base.replace([float("inf"), float("-inf")], pd.NA)

    if eh_resultado_financeiro(indicador):
        linhas = []
        acumulado_por_ano = {}

        for (ano, mes, mes_nome, mes_ordem), sub in d.groupby(["ANO", "MÊS", "MÊS_NOME", "MÊS_ORDEM"]):
            resumo = resumo_resultado_financeiro_por_grupo(sub)
            acumulado_por_ano.setdefault(ano, 0)

            if pd.notna(resumo["Resultado R$"]):
                acumulado_por_ano[ano] += resumo["Resultado R$"]
                acumulado_exibir = acumulado_por_ano[ano]
            else:
                acumulado_exibir = None

            linhas.append({
                "ANO": ano,
                "MÊS": mes,
                "Mês": mes_nome,
                "Meta %": resumo["Meta %"],
                "Despesa": resumo["Despesa"],
                "Receita": resumo["Receita"],
                "Resultado R$": resumo["Resultado R$"],
                "Resultado %": resumo["Resultado %"],
                "Acumulado": acumulado_exibir,
                "MÊS_ORDEM": mes_ordem,
            })

        base = pd.DataFrame(linhas).sort_values("MÊS_ORDEM").reset_index(drop=True)
        base["MoM_%"] = base["Resultado R$"].pct_change() * 100
        return base.replace([float("inf"), float("-inf")], pd.NA)

    if eh_qualidade(indicador):
        linhas = []
        acumulado_por_ano = {}

        for (ano, mes, mes_nome, mes_ordem), sub in d.groupby(["ANO", "MÊS", "MÊS_NOME", "MÊS_ORDEM"]):
            resumo = resumo_qualidade_por_grupo(sub, indicador)
            acumulado_por_ano.setdefault(ano, 0)
            acumulado_por_ano[ano] += resumo["Diferença"] if pd.notna(resumo["Diferença"]) else 0

            linhas.append({
                "ANO": ano,
                "MÊS": mes,
                "Mês": mes_nome,
                "Meta": resumo["Meta"],
                "Qtd. Coletas": resumo["Qtd. Coletas"],
                "Qtd. Sucesso": resumo["Qtd. Sucesso"],
                "Diferença": resumo["Diferença"],
                "Resultado %": resumo["Resultado %"],
                "Acumulado": acumulado_por_ano[ano],
                "MÊS_ORDEM": mes_ordem,
            })

        base = pd.DataFrame(linhas).sort_values("MÊS_ORDEM").reset_index(drop=True)
        base["MoM_%"] = base["Resultado %"].pct_change() * 100
        return base.replace([float("inf"), float("-inf")], pd.NA)

    if eh_tecfil(indicador):
        linhas = []
        acumulado_por_ano = {}

        for (ano, mes, mes_nome, mes_ordem), sub in d.groupby(["ANO", "MÊS", "MÊS_NOME", "MÊS_ORDEM"]):
            resumo = resumo_tecfil_por_grupo(sub)
            acumulado_por_ano.setdefault(ano, {"KG": 0, "RS": 0})
            acumulado_por_ano[ano]["KG"] += resumo["Dif. KG"]
            acumulado_por_ano[ano]["RS"] += resumo["Dif. R$"]

            linhas.append({
                "ANO": ano,
                "MÊS": mes,
                "Mês": mes_nome,
                "Meta KG": resumo["Meta KG"],
                "Meta R$": resumo["Meta R$"],
                "Realizado KG": resumo["Realizado KG"],
                "Realizado R$": resumo["Realizado R$"],
                "% Dif. KG": resumo["% Dif. KG"],
                "% Dif. R$": resumo["% Dif. R$"],
                "Dif. KG": resumo["Dif. KG"],
                "Dif. R$": resumo["Dif. R$"],
                "Acum. KG": acumulado_por_ano[ano]["KG"],
                "Acum. R$": acumulado_por_ano[ano]["RS"],
                "MÊS_ORDEM": mes_ordem,
            })

        base = pd.DataFrame(linhas).sort_values("MÊS_ORDEM").reset_index(drop=True)
        base["MoM_%"] = base["Realizado R$"].pct_change() * 100
        return base.replace([float("inf"), float("-inf")], pd.NA)

    if eh_despesa_hora_extra(indicador):
        base = (
            d.groupby(["ANO", "MÊS", "MÊS_NOME", "MÊS_ORDEM"], as_index=False)
            .agg({"META_CALC": "sum", "REALIZADO_CALC": "sum", "SALARIO_CALC": "sum"})
            .sort_values("MÊS_ORDEM")
            .reset_index(drop=True)
        )
        base["HE da Folha"] = base["REALIZADO_CALC"] / base["SALARIO_CALC"].replace(0, pd.NA)
        # Fórmula conforme planilha:
        # Result.% = (Pago em Hora Extra / Limite) - 1
        base["Resultado %"] = (base["REALIZADO_CALC"] / base["META_CALC"].replace(0, pd.NA)) - 1
        base["Gap"] = base["META_CALC"] - base["REALIZADO_CALC"]
        base["MoM_%"] = base["REALIZADO_CALC"].pct_change() * 100
        base = base.replace([float("inf"), float("-inf")], pd.NA)

        return base.rename(columns={
            "MÊS_NOME": "Mês",
            "REALIZADO_CALC": "Pago em Hora Extra",
            "META_CALC": "Limite",
            "SALARIO_CALC": "Salário",
            "Gap": "Saldo do Limite",
        })[["ANO", "MÊS", "Mês", "Limite", "Pago em Hora Extra", "Salário", "HE da Folha", "Saldo do Limite", "Resultado %", "MÊS_ORDEM"]]

    base = (
        d.groupby(["ANO", "MÊS", "MÊS_NOME", "MÊS_ORDEM"], as_index=False)
        .agg({"META_CALC": "sum", "REALIZADO_CALC": "sum"})
        .sort_values("MÊS_ORDEM")
        .reset_index(drop=True)
    )

    if eh_despesa_manutencao(indicador):
        # Despesa Manutenção:
        # Resultado R$ = Limite - Despesa
        # Resultado % = 1 - (Despesa / Limite)
        base["Ating."] = 1 - (base["REALIZADO_CALC"] / base["META_CALC"].replace(0, pd.NA))
        base["Gap"] = base["META_CALC"] - base["REALIZADO_CALC"]
    elif eh_despesa(indicador):
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
    
@st.cache_data(show_spinner=False)
def calcular_yoy(d, indicador):
    if eh_moto_margem(indicador):
        linhas = []
        for ano, sub in d.groupby("ANO"):
            resumo = resumo_moto_por_grupo(sub)
            linhas.append({
                "ANO": ano,
                "Faturamento": resumo["Faturamento"],
                "Compra": resumo["Compra"],
                "Margem Bruta": resumo["Margem Bruta"],
                "Meta %": resumo["Meta %"],
                "Meta Margem (R$)": resumo["Meta Margem (R$)"],
                "Gap (R$)": resumo["Gap (R$)"],
                "Tx. Sucesso": resumo["Tx. Sucesso"],
                "Meses c/ dado": sub["MÊS"].nunique(),
            })

        df_yoy = pd.DataFrame(linhas).sort_values("ANO")
        df_yoy["YoY_%"] = df_yoy["Faturamento"].pct_change() * 100
        df_yoy = remover_meta_moto_anos_sem_meta(df_yoy)

        # Colunas de compatibilidade para PDF/IA, mas a tela usa as colunas especiais
        df_yoy["Realizado"] = df_yoy["Faturamento"]
        df_yoy["Meta"] = df_yoy["Meta %"]
        df_yoy["Atingimento"] = df_yoy["Tx. Sucesso"]

        return df_yoy.replace([float("inf"), float("-inf")], pd.NA)

    if eh_despesa_geral(indicador):
        linhas = []
        for ano, sub in d.groupby("ANO"):
            resumo = resumo_despesa_geral_por_grupo(sub)
            linhas.append({
                "ANO": ano,
                "Limite %": resumo["Limite %"],
                "Despesa": resumo["Despesa"],
                "Receita": resumo["Receita"],
                "Resultado R$": resumo["Resultado R$"],
                "Tx. Sucesso": resumo["Tx. Sucesso"],
                "Meses c/ dado": sub["MÊS"].nunique(),
            })

        df_yoy = pd.DataFrame(linhas).sort_values("ANO")
        df_yoy["YoY_%"] = df_yoy["Despesa"].pct_change() * 100

        # Compatibilidade
        df_yoy["Realizado"] = df_yoy["Despesa"]
        df_yoy["Meta"] = df_yoy["Limite %"]
        df_yoy["Atingimento"] = df_yoy["Tx. Sucesso"]

        return df_yoy.replace([float("inf"), float("-inf")], pd.NA)

    if eh_resultado_financeiro(indicador):
        linhas = []
        for ano, sub in d.groupby("ANO"):
            resumo = resumo_resultado_financeiro_por_grupo(sub)
            linhas.append({
                "ANO": ano,
                "Meta %": resumo["Meta %"],
                "Despesa": resumo["Despesa"],
                "Receita": resumo["Receita"],
                "Resultado R$": resumo["Resultado R$"],
                "Resultado %": resumo["Resultado %"],
                "Meses c/ dado": sub["MÊS"].nunique(),
            })

        df_yoy = pd.DataFrame(linhas).sort_values("ANO")
        df_yoy["YoY_%"] = df_yoy["Resultado R$"].pct_change() * 100

        # Compatibilidade
        df_yoy["Realizado"] = df_yoy["Resultado R$"]
        df_yoy["Meta"] = df_yoy["Meta %"]
        df_yoy["Atingimento"] = df_yoy["Resultado %"]

        return df_yoy.replace([float("inf"), float("-inf")], pd.NA)

    if eh_qualidade(indicador):
        linhas = []
        for ano, sub in d.groupby("ANO"):
            resumo = resumo_qualidade_por_grupo(sub, indicador)
            linhas.append({
                "ANO": ano,
                "Meta": resumo["Meta"],
                "Qtd. Coletas": resumo["Qtd. Coletas"],
                "Qtd. Sucesso": resumo["Qtd. Sucesso"],
                "Diferença": resumo["Diferença"],
                "Resultado %": resumo["Resultado %"],
                "Meses c/ dado": sub["MÊS"].nunique(),
                # compatibilidade
                "Realizado": resumo["Qtd. Sucesso"],
                "Meta_Compat": resumo["Meta"],
                "Atingimento": resumo["Resultado %"],
            })

        df_yoy = pd.DataFrame(linhas).sort_values("ANO")
        df_yoy["YoY_%"] = df_yoy["Resultado %"].pct_change() * 100
        return df_yoy.replace([float("inf"), float("-inf")], pd.NA)

    if eh_tecfil(indicador):
        linhas = []
        for ano, sub in d.groupby("ANO"):
            resumo = resumo_tecfil_por_grupo(sub)
            linhas.append({
                "ANO": ano,
                "Meta KG": resumo["Meta KG"],
                "Meta R$": resumo["Meta R$"],
                "Realizado KG": resumo["Realizado KG"],
                "Realizado R$": resumo["Realizado R$"],
                "% Dif. KG": resumo["% Dif. KG"],
                "% Dif. R$": resumo["% Dif. R$"],
                "Dif. KG": resumo["Dif. KG"],
                "Dif. R$": resumo["Dif. R$"],
                "Meses c/ dado": sub["MÊS"].nunique(),
                # compatibilidade
                "Realizado": resumo["Realizado R$"],
                "Meta": resumo["Meta R$"],
                "Atingimento": (resumo["Realizado R$"] / resumo["Meta R$"]) if resumo["Meta R$"] else None,
            })

        df_yoy = pd.DataFrame(linhas).sort_values("ANO")
        df_yoy["YoY_%"] = df_yoy["Realizado R$"].pct_change() * 100
        return df_yoy.replace([float("inf"), float("-inf")], pd.NA)

    if eh_despesa_hora_extra(indicador):
        df_yoy = (
            d.groupby("ANO", as_index=False)
            .agg({
                "REALIZADO_CALC": "sum",
                "META_CALC": "sum",
                "SALARIO_CALC": "sum",
                "MÊS": "nunique"
            })
            .sort_values("ANO")
            .rename(columns={
                "REALIZADO_CALC": "Pago em Hora Extra",
                "META_CALC": "Limite",
                "SALARIO_CALC": "Salário",
                "MÊS": "Meses c/ dado"
            })
        )
        df_yoy["HE da Folha"] = df_yoy["Pago em Hora Extra"] / df_yoy["Salário"].replace(0, pd.NA)
        df_yoy["Saldo do Limite"] = df_yoy["Limite"] - df_yoy["Pago em Hora Extra"]
        df_yoy["Resultado %"] = df_yoy["Pago em Hora Extra"] / df_yoy["Limite"].replace(0, pd.NA)
        df_yoy["YoY_%"] = df_yoy["Pago em Hora Extra"].pct_change() * 100

        # Compatibilidade com blocos antigos
        df_yoy["Realizado"] = df_yoy["Pago em Hora Extra"]
        df_yoy["Meta"] = df_yoy["Limite"]
        df_yoy["Atingimento"] = df_yoy["Resultado %"]

        return df_yoy.replace([float("inf"), float("-inf")], pd.NA)

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

    if eh_despesa_manutencao(indicador):
        df_yoy["Atingimento"] = df_yoy["Realizado"] / df_yoy["Meta"].replace(0, pd.NA)
    elif eh_despesa(indicador):
        df_yoy["Atingimento"] = df_yoy["Meta"] / df_yoy["Realizado"].replace(0, pd.NA)
    else:
        df_yoy["Atingimento"] = df_yoy["Realizado"] / df_yoy["Meta"].replace(0, pd.NA)

    df_yoy["YoY_%"] = df_yoy["Realizado"].pct_change() * 100
    return df_yoy.replace([float("inf"), float("-inf")], pd.NA)


def ordenar_df_seguro(df_saida, coluna, ascending=False):
    """
    Ordena um DataFrame sem quebrar quando ele está vazio
    ou quando a coluna de ordenação não existe.
    """
    if df_saida is None or df_saida.empty:
        return pd.DataFrame()

    if coluna in df_saida.columns:
        return df_saida.sort_values(coluna, ascending=ascending)

    return df_saida


@st.cache_data(show_spinner=False)
def comparativo_filiais(d, ano, indicador):
    d = d.copy()
    if "FILIAL" in d.columns and not eh_tecfil(indicador):
        # Para indicadores comuns, o comparativo entre filiais não deve mostrar linhas consolidadas.
        # Para Tecfil, NÃO removemos "TECFIL GERAL", porque ele é um indicador/base válida.
        filiais_consolidadas = [
            normalizar_texto("XX GERAL XX"),
            normalizar_texto("GERAL"),
            normalizar_texto("TOTAL"),
        ]
        d = d[~d["FILIAL"].apply(normalizar_texto).isin(filiais_consolidadas)].copy()

    if eh_moto_margem(indicador):
        linhas = []
        base = d[d["ANO"] == ano].copy()

        for filial_nome, sub in base.groupby("FILIAL"):
            resumo = resumo_moto_por_grupo(sub)
            linhas.append({
                "FILIAL": filial_nome,
                "Faturamento": resumo["Faturamento"],
                "Compra": resumo["Compra"],
                "Margem Bruta": resumo["Margem Bruta"],
                "Meta %": resumo["Meta %"],
                "Meta Margem (R$)": resumo["Meta Margem (R$)"],
                "Gap (R$)": resumo["Gap (R$)"],
                "Tx. Sucesso": resumo["Tx. Sucesso"],
                # Compatibilidade
                "Realizado": resumo["Faturamento"],
                "Meta": resumo["Meta %"],
                "Gap": resumo["Gap (R$)"],
                "Atingimento": resumo["Tx. Sucesso"],
            })

        return ordenar_df_seguro(pd.DataFrame(linhas), "Margem Bruta", ascending=False)

    if eh_despesa_geral(indicador):
        linhas = []
        base = d[d["ANO"] == ano].copy()

        for filial_nome, sub in base.groupby("FILIAL"):
            resumo = resumo_despesa_geral_por_grupo(sub)
            linhas.append({
                "FILIAL": filial_nome,
                "Limite %": resumo["Limite %"],
                "Despesa": resumo["Despesa"],
                "Receita": resumo["Receita"],
                "Resultado R$": resumo["Resultado R$"],
                "Tx. Sucesso": resumo["Tx. Sucesso"],
                # Compatibilidade
                "Realizado": resumo["Despesa"],
                "Meta": resumo["Limite %"],
                "Gap": resumo["Resultado R$"],
                "Atingimento": resumo["Tx. Sucesso"],
            })

        return ordenar_df_seguro(pd.DataFrame(linhas), "Despesa", ascending=False)

    if eh_resultado_financeiro(indicador):
        linhas = []
        base = d[d["ANO"] == ano].copy()

        for filial_nome, sub in base.groupby("FILIAL"):
            resumo = resumo_resultado_financeiro_por_grupo(sub)
            linhas.append({
                "FILIAL": filial_nome,
                "Meta %": resumo["Meta %"],
                "Despesa": resumo["Despesa"],
                "Receita": resumo["Receita"],
                "Resultado R$": resumo["Resultado R$"],
                "Resultado %": resumo["Resultado %"],
                # Compatibilidade
                "Realizado": resumo["Resultado R$"],
                "Meta": resumo["Meta %"],
                "Gap": resumo["Resultado R$"],
                "Atingimento": resumo["Resultado %"],
            })

        return ordenar_df_seguro(pd.DataFrame(linhas), "Resultado R$", ascending=False)

    if eh_qualidade(indicador):
        linhas = []
        base = d[d["ANO"] == ano].copy()

        for filial_nome, sub in base.groupby("FILIAL"):
            resumo = resumo_qualidade_por_grupo(sub, indicador)
            linhas.append({
                "FILIAL": filial_nome,
                "Meta": resumo["Meta"],
                "Qtd. Coletas": resumo["Qtd. Coletas"],
                "Qtd. Sucesso": resumo["Qtd. Sucesso"],
                "Diferença": resumo["Diferença"],
                "Resultado %": resumo["Resultado %"],
                # compatibilidade
                "Realizado": resumo["Qtd. Sucesso"],
                "Meta_Compat": resumo["Meta"],
                "Gap": resumo["Diferença"],
                "Atingimento": resumo["Resultado %"],
            })

        return ordenar_df_seguro(pd.DataFrame(linhas), "Resultado %", ascending=False)

    if eh_tecfil(indicador):
        linhas = []
        base = d[d["ANO"] == ano].copy()

        for filial_nome, sub in base.groupby("FILIAL"):
            resumo = resumo_tecfil_por_grupo(sub)
            linhas.append({
                "FILIAL": filial_nome,
                "Meta KG": resumo["Meta KG"],
                "Meta R$": resumo["Meta R$"],
                "Realizado KG": resumo["Realizado KG"],
                "Realizado R$": resumo["Realizado R$"],
                "% Dif. KG": resumo["% Dif. KG"],
                "% Dif. R$": resumo["% Dif. R$"],
                "Dif. KG": resumo["Dif. KG"],
                "Dif. R$": resumo["Dif. R$"],
                # compatibilidade
                "Realizado": resumo["Realizado R$"],
                "Meta": resumo["Meta R$"],
                "Gap": resumo["Dif. R$"],
                "Atingimento": (resumo["Realizado R$"] / resumo["Meta R$"]) if resumo["Meta R$"] else None,
            })

        return ordenar_df_seguro(pd.DataFrame(linhas), "Realizado R$", ascending=False)

    if eh_despesa_hora_extra(indicador):
        base_he = d[d["ANO"] == ano].copy()

        colunas_retorno_he = [
            "FILIAL", "Pago em Hora Extra", "Limite", "Salário", "HE da Folha",
            "Saldo do Limite", "Resultado %", "Realizado", "Meta", "Gap", "Atingimento"
        ]

        # Evita KeyError no PDF quando não há base por filial disponível
        # ou quando os dados foram filtrados/removidos antes do comparativo.
        if base_he.empty or "FILIAL" not in base_he.columns:
            return pd.DataFrame(columns=colunas_retorno_he)

        for col in ["REALIZADO_CALC", "META_CALC", "SALARIO_CALC"]:
            if col not in base_he.columns:
                base_he[col] = 0

        comp = (
            base_he
            .groupby("FILIAL", as_index=False)
            .agg({"REALIZADO_CALC": "sum", "META_CALC": "sum", "SALARIO_CALC": "sum"})
            .rename(columns={
                "REALIZADO_CALC": "Pago em Hora Extra",
                "META_CALC": "Limite",
                "SALARIO_CALC": "Salário",
            })
        )

        if comp.empty:
            return pd.DataFrame(columns=colunas_retorno_he)

        # Garante que as colunas existam mesmo se o agrupamento retornar vazio/instável.
        for col in ["Pago em Hora Extra", "Limite", "Salário"]:
            if col not in comp.columns:
                comp[col] = 0

        comp = ordenar_df_seguro(comp, "Pago em Hora Extra", ascending=False)

        comp["HE da Folha"] = comp["Pago em Hora Extra"] / comp["Salário"].replace(0, pd.NA)
        comp["Saldo do Limite"] = comp["Limite"] - comp["Pago em Hora Extra"]
        # Fórmula conforme planilha:
        # Result.% = (Pago em Hora Extra / Limite) - 1
        comp["Resultado %"] = (comp["Pago em Hora Extra"] / comp["Limite"].replace(0, pd.NA)) - 1

        # Compatibilidade com blocos antigos
        comp["Realizado"] = comp["Pago em Hora Extra"]
        comp["Meta"] = comp["Limite"]
        comp["Gap"] = comp["Saldo do Limite"]
        comp["Atingimento"] = comp["Resultado %"]

        return comp[colunas_retorno_he]

    base_comp = d[d["ANO"] == ano].copy()

    if base_comp.empty or "FILIAL" not in base_comp.columns:
        return pd.DataFrame(columns=["FILIAL", "Realizado", "Meta", "Gap", "Atingimento"])

    colunas_necessarias = ["REALIZADO_CALC", "META_CALC"]
    for col in colunas_necessarias:
        if col not in base_comp.columns:
            base_comp[col] = 0

    comp = (
        base_comp
        .groupby("FILIAL", as_index=False)
        .agg({"REALIZADO_CALC": "sum", "META_CALC": "sum"})
        .rename(columns={"REALIZADO_CALC": "Realizado", "META_CALC": "Meta"})
    )

    if comp.empty:
        return pd.DataFrame(columns=["FILIAL", "Realizado", "Meta", "Gap", "Atingimento"])

    if "Realizado" not in comp.columns:
        comp["Realizado"] = 0
    if "Meta" not in comp.columns:
        comp["Meta"] = 0

    if eh_despesa_manutencao(indicador):
        comp["Gap"] = comp["Meta"] - comp["Realizado"]
        comp["Atingimento"] = comp["Realizado"] / comp["Meta"].replace(0, pd.NA)
    elif eh_despesa(indicador):
        comp["Gap"] = comp["Meta"] - comp["Realizado"]
        comp["Atingimento"] = comp["Meta"] / comp["Realizado"].replace(0, pd.NA)
    else:
        comp["Gap"] = comp["Realizado"] - comp["Meta"]
        comp["Atingimento"] = comp["Realizado"] / comp["Meta"].replace(0, pd.NA)

    return ordenar_df_seguro(comp, "Realizado", ascending=False)
    
@st.cache_data(show_spinner=False)
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
    periodo_txt = f"Jan a {MESES_MAPA[mes_limite]}"

    if eh_moto_margem(indicador):
        resumo_ant = resumo_moto_por_grupo(anterior_periodo) if not anterior_periodo.empty else {
            "Faturamento": 0, "Compra": 0, "Margem Bruta": 0, "Meta %": None,
            "Meta Margem (R$)": None, "Gap (R$)": None, "Tx. Sucesso": None
        }
        resumo_atual = resumo_moto_por_grupo(atual_periodo) if not atual_periodo.empty else {
            "Faturamento": 0, "Compra": 0, "Margem Bruta": 0, "Meta %": None,
            "Meta Margem (R$)": None, "Gap (R$)": None, "Tx. Sucesso": None
        }

        var_faturamento = ((resumo_atual["Faturamento"] / resumo_ant["Faturamento"]) - 1) if resumo_ant["Faturamento"] > 0 else None
        var_margem = ((resumo_atual["Margem Bruta"] / resumo_ant["Margem Bruta"]) - 1) if resumo_ant["Margem Bruta"] > 0 else None

        tabela_periodo_moto = pd.DataFrame([
            {
                "Ano": ano_anterior,
                "Período": periodo_txt,
                "Faturamento": resumo_ant["Faturamento"],
                "Compra": resumo_ant["Compra"],
                "Margem Bruta": resumo_ant["Margem Bruta"],
                "Meta %": resumo_ant["Meta %"],
                "Tx. Sucesso": resumo_ant["Tx. Sucesso"],
                "Variação Faturamento": None,
                "Variação Margem": None,
                # Compatibilidade
                "Realizado": resumo_ant["Faturamento"],
                "Meta": resumo_ant["Meta %"],
                "Atingimento": resumo_ant["Tx. Sucesso"],
                "Variação Realizado": None,
                "Variação Meta": None,
            },
            {
                "Ano": ano_referencia,
                "Período": periodo_txt,
                "Faturamento": resumo_atual["Faturamento"],
                "Compra": resumo_atual["Compra"],
                "Margem Bruta": resumo_atual["Margem Bruta"],
                "Meta %": resumo_atual["Meta %"],
                "Tx. Sucesso": resumo_atual["Tx. Sucesso"],
                "Variação Faturamento": var_faturamento,
                "Variação Margem": var_margem,
                # Compatibilidade
                "Realizado": resumo_atual["Faturamento"],
                "Meta": resumo_atual["Meta %"],
                "Atingimento": resumo_atual["Tx. Sucesso"],
                "Variação Realizado": var_faturamento,
                "Variação Meta": None,
            }
        ])

        return remover_meta_moto_anos_sem_meta(tabela_periodo_moto)

    if eh_qualidade(indicador):
        resumo_ant = resumo_qualidade_por_grupo(anterior_periodo, indicador)
        resumo_atual = resumo_qualidade_por_grupo(atual_periodo, indicador)

        var_resultado = ((resumo_atual["Resultado %"] / resumo_ant["Resultado %"]) - 1) if resumo_ant["Resultado %"] not in [None, 0] and pd.notna(resumo_ant["Resultado %"]) else None

        return pd.DataFrame([
            {
                "Ano": ano_anterior,
                "Período": periodo_txt,
                "Meta": resumo_ant["Meta"],
                "Qtd. Coletas": resumo_ant["Qtd. Coletas"],
                "Qtd. Sucesso": resumo_ant["Qtd. Sucesso"],
                "Diferença": resumo_ant["Diferença"],
                "Resultado %": resumo_ant["Resultado %"],
                "Variação Resultado": None,
                # compatibilidade
                "Realizado": resumo_ant["Qtd. Sucesso"],
                "Meta_Compat": resumo_ant["Meta"],
                "Gap (R$)": resumo_ant["Diferença"],
                "Atingimento": resumo_ant["Resultado %"],
                "Variação Realizado": None,
                "Variação Meta": None,
            },
            {
                "Ano": ano_referencia,
                "Período": periodo_txt,
                "Meta": resumo_atual["Meta"],
                "Qtd. Coletas": resumo_atual["Qtd. Coletas"],
                "Qtd. Sucesso": resumo_atual["Qtd. Sucesso"],
                "Diferença": resumo_atual["Diferença"],
                "Resultado %": resumo_atual["Resultado %"],
                "Variação Resultado": var_resultado,
                # compatibilidade
                "Realizado": resumo_atual["Qtd. Sucesso"],
                "Meta_Compat": resumo_atual["Meta"],
                "Gap (R$)": resumo_atual["Diferença"],
                "Atingimento": resumo_atual["Resultado %"],
                "Variação Realizado": var_resultado,
                "Variação Meta": None,
            }
        ])

    if eh_tecfil(indicador):
        resumo_ant = resumo_tecfil_por_grupo(anterior_periodo)
        resumo_atual = resumo_tecfil_por_grupo(atual_periodo)

        var_real_rs = ((resumo_atual["Realizado R$"] / resumo_ant["Realizado R$"]) - 1) if resumo_ant["Realizado R$"] > 0 else None
        var_real_kg = ((resumo_atual["Realizado KG"] / resumo_ant["Realizado KG"]) - 1) if resumo_ant["Realizado KG"] > 0 else None

        return pd.DataFrame([
            {
                "Ano": ano_anterior,
                "Período": periodo_txt,
                "Meta KG": resumo_ant["Meta KG"],
                "Meta R$": resumo_ant["Meta R$"],
                "Realizado KG": resumo_ant["Realizado KG"],
                "Realizado R$": resumo_ant["Realizado R$"],
                "% Dif. KG": resumo_ant["% Dif. KG"],
                "% Dif. R$": resumo_ant["% Dif. R$"],
                "Dif. KG": resumo_ant["Dif. KG"],
                "Dif. R$": resumo_ant["Dif. R$"],
                "Variação KG": None,
                "Variação R$": None,
                # compatibilidade
                "Realizado": resumo_ant["Realizado R$"],
                "Meta": resumo_ant["Meta R$"],
                "Gap (R$)": resumo_ant["Dif. R$"],
                "Atingimento": (resumo_ant["Realizado R$"] / resumo_ant["Meta R$"]) if resumo_ant["Meta R$"] else None,
                "Variação Realizado": None,
                "Variação Meta": None,
            },
            {
                "Ano": ano_referencia,
                "Período": periodo_txt,
                "Meta KG": resumo_atual["Meta KG"],
                "Meta R$": resumo_atual["Meta R$"],
                "Realizado KG": resumo_atual["Realizado KG"],
                "Realizado R$": resumo_atual["Realizado R$"],
                "% Dif. KG": resumo_atual["% Dif. KG"],
                "% Dif. R$": resumo_atual["% Dif. R$"],
                "Dif. KG": resumo_atual["Dif. KG"],
                "Dif. R$": resumo_atual["Dif. R$"],
                "Variação KG": var_real_kg,
                "Variação R$": var_real_rs,
                # compatibilidade
                "Realizado": resumo_atual["Realizado R$"],
                "Meta": resumo_atual["Meta R$"],
                "Gap (R$)": resumo_atual["Dif. R$"],
                "Atingimento": (resumo_atual["Realizado R$"] / resumo_atual["Meta R$"]) if resumo_atual["Meta R$"] else None,
                "Variação Realizado": var_real_rs,
                "Variação Meta": None,
            }
        ])

    if eh_resultado_financeiro(indicador):
        resumo_ant = resumo_resultado_financeiro_por_grupo(anterior_periodo) if not anterior_periodo.empty else {
            "Meta %": None, "Despesa": 0, "Receita": 0, "Resultado R$": None, "Resultado %": None
        }
        resumo_atual = resumo_resultado_financeiro_por_grupo(atual_periodo) if not atual_periodo.empty else {
            "Meta %": None, "Despesa": 0, "Receita": 0, "Resultado R$": None, "Resultado %": None
        }

        var_resultado = ((resumo_atual["Resultado R$"] / resumo_ant["Resultado R$"]) - 1) if resumo_ant["Resultado R$"] not in [None, 0] and pd.notna(resumo_ant["Resultado R$"]) else None
        var_receita = ((resumo_atual["Receita"] / resumo_ant["Receita"]) - 1) if resumo_ant["Receita"] > 0 else None

        return pd.DataFrame([
            {
                "Ano": ano_anterior,
                "Período": periodo_txt,
                "Meta %": resumo_ant["Meta %"],
                "Despesa": resumo_ant["Despesa"],
                "Receita": resumo_ant["Receita"],
                "Resultado R$": resumo_ant["Resultado R$"],
                "Resultado %": resumo_ant["Resultado %"],
                "Variação Resultado": None,
                "Variação Receita": None,
                # Compatibilidade
                "Realizado": resumo_ant["Resultado R$"],
                "Meta": resumo_ant["Meta %"],
                "Gap (R$)": resumo_ant["Resultado R$"],
                "Atingimento": resumo_ant["Resultado %"],
                "Variação Realizado": None,
                "Variação Meta": None,
            },
            {
                "Ano": ano_referencia,
                "Período": periodo_txt,
                "Meta %": resumo_atual["Meta %"],
                "Despesa": resumo_atual["Despesa"],
                "Receita": resumo_atual["Receita"],
                "Resultado R$": resumo_atual["Resultado R$"],
                "Resultado %": resumo_atual["Resultado %"],
                "Variação Resultado": var_resultado,
                "Variação Receita": var_receita,
                # Compatibilidade
                "Realizado": resumo_atual["Resultado R$"],
                "Meta": resumo_atual["Meta %"],
                "Gap (R$)": resumo_atual["Resultado R$"],
                "Atingimento": resumo_atual["Resultado %"],
                "Variação Realizado": var_resultado,
                "Variação Meta": None,
            }
        ])

    if eh_despesa_geral(indicador):
        resumo_ant = resumo_despesa_geral_por_grupo(anterior_periodo) if not anterior_periodo.empty else {
            "Limite %": None, "Despesa": 0, "Receita": 0, "Resultado R$": None, "Tx. Sucesso": None
        }
        resumo_atual = resumo_despesa_geral_por_grupo(atual_periodo) if not atual_periodo.empty else {
            "Limite %": None, "Despesa": 0, "Receita": 0, "Resultado R$": None, "Tx. Sucesso": None
        }

        var_despesa = ((resumo_atual["Despesa"] / resumo_ant["Despesa"]) - 1) if resumo_ant["Despesa"] > 0 else None
        var_receita = ((resumo_atual["Receita"] / resumo_ant["Receita"]) - 1) if resumo_ant["Receita"] > 0 else None

        return pd.DataFrame([
            {
                "Ano": ano_anterior,
                "Período": periodo_txt,
                "Limite %": resumo_ant["Limite %"],
                "Despesa": resumo_ant["Despesa"],
                "Receita": resumo_ant["Receita"],
                "Resultado R$": resumo_ant["Resultado R$"],
                "Tx. Sucesso": resumo_ant["Tx. Sucesso"],
                "Variação Despesa": None,
                "Variação Receita": None,
                # Compatibilidade
                "Realizado": resumo_ant["Despesa"],
                "Meta": resumo_ant["Limite %"],
                "Gap (R$)": resumo_ant["Resultado R$"],
                "Atingimento": resumo_ant["Tx. Sucesso"],
                "Variação Realizado": None,
                "Variação Meta": None,
            },
            {
                "Ano": ano_referencia,
                "Período": periodo_txt,
                "Limite %": resumo_atual["Limite %"],
                "Despesa": resumo_atual["Despesa"],
                "Receita": resumo_atual["Receita"],
                "Resultado R$": resumo_atual["Resultado R$"],
                "Tx. Sucesso": resumo_atual["Tx. Sucesso"],
                "Variação Despesa": var_despesa,
                "Variação Receita": var_receita,
                # Compatibilidade
                "Realizado": resumo_atual["Despesa"],
                "Meta": resumo_atual["Limite %"],
                "Gap (R$)": resumo_atual["Resultado R$"],
                "Atingimento": resumo_atual["Tx. Sucesso"],
                "Variação Realizado": var_despesa,
                "Variação Meta": None,
            }
        ])

    atual_periodo = base_atual[base_atual["MÊS"] <= mes_limite]
    anterior_periodo = base_ant[base_ant["MÊS"] <= mes_limite]

    real_atual = atual_periodo["REALIZADO_CALC"].sum()
    meta_atual = atual_periodo["META_CALC"].sum()
    real_ant = anterior_periodo["REALIZADO_CALC"].sum()
    meta_ant = anterior_periodo["META_CALC"].sum()

    if eh_despesa_manutencao(indicador):
        ating_atual = real_atual / meta_atual if meta_atual > 0 else None
        ating_ant = real_ant / meta_ant if meta_ant > 0 else None
        gap_atual = meta_atual - real_atual
        gap_ant = meta_ant - real_ant
    elif eh_despesa(indicador):
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

    mes_atual_calendario = hoje.month
    max_mes_disponivel = int(base_ano["MÊS"].max()) if not base_ano.empty else mes_atual_calendario
    mes_referencia = min(mes_atual_calendario, max_mes_disponivel)

    if eh_moto_margem(indicador):
        base_mes = base_ano[base_ano["MÊS"] == mes_referencia].copy()
        resumo_mes = resumo_moto_por_grupo(base_mes) if not base_mes.empty else resumo_moto_por_grupo(base_ano.iloc[0:0])
        resumo_ano = resumo_moto_por_grupo(base_ano)

        dias_restantes = 0
        necessario_dia = None
        if ano_selecionado == hoje.year and mes_referencia == hoje.month:
            total_dias_mes = calendar.monthrange(ano_selecionado, mes_referencia)[1]
            dias_restantes = max(total_dias_mes - hoje.day, 0)

        df_periodo = comparar_mesmo_periodo(df, indicador, ano_selecionado)
        realizado_ant = None
        var_real = None
        periodo_txt = None
        if not df_periodo.empty and len(df_periodo) >= 2:
            realizado_ant = df_periodo.iloc[0]["Faturamento"]
            var_real = df_periodo.iloc[1]["Variação Faturamento"]
            periodo_txt = df_periodo.iloc[1]["Período"]

        return {
            "hoje": hoje,
            "ano": ano_selecionado,
            "mes_referencia": mes_referencia,
            "nome_mes": MESES_MAPA.get(mes_referencia, str(mes_referencia)),
            "realizado_mes": resumo_mes["Faturamento"],
            "meta_mes": resumo_mes["Meta %"],
            "gap_mes": resumo_mes["Gap (R$)"],
            "ating_mes": resumo_mes["Tx. Sucesso"],
            "compra_mes": resumo_mes["Compra"],
            "margem_mes": resumo_mes["Margem Bruta"],
            "meta_margem_mes": resumo_mes["Meta Margem (R$)"],
            "realizado_ano": resumo_ano["Faturamento"],
            "meta_ano": resumo_ano["Meta %"],
            "gap_ano": resumo_ano["Gap (R$)"],
            "ating_ano": resumo_ano["Tx. Sucesso"],
            "compra_ano": resumo_ano["Compra"],
            "margem_ano": resumo_ano["Margem Bruta"],
            "meta_margem_ano": resumo_ano["Meta Margem (R$)"],
            "dias_restantes": dias_restantes,
            "necessario_dia": necessario_dia,
            "realizado_ant": realizado_ant,
            "meta_ant": None,
            "var_real": var_real,
            "var_meta": None,
            "periodo_txt": periodo_txt,
            "despesa": False,
            "moto_margem": True,
        }

    if eh_qualidade(indicador):
        base_mes = base_ano[base_ano["MÊS"] == mes_referencia].copy()
        resumo_mes = resumo_qualidade_por_grupo(base_mes, indicador)
        resumo_ano = resumo_qualidade_por_grupo(base_ano, indicador)

        df_periodo = comparar_mesmo_periodo(df, indicador, ano_selecionado)
        realizado_ant = None
        var_real = None
        periodo_txt = None
        if not df_periodo.empty and len(df_periodo) >= 2:
            realizado_ant = df_periodo.iloc[0]["Resultado %"]
            var_real = df_periodo.iloc[1]["Variação Resultado"]
            periodo_txt = df_periodo.iloc[1]["Período"]

        return {
            "hoje": hoje,
            "ano": ano_selecionado,
            "mes_referencia": mes_referencia,
            "nome_mes": MESES_MAPA.get(mes_referencia, str(mes_referencia)),
            "realizado_mes": resumo_mes["Qtd. Sucesso"],
            "meta_mes": resumo_mes["Meta"],
            "gap_mes": resumo_mes["Diferença"],
            "ating_mes": resumo_mes["Resultado %"],
            "qtd_total_mes": resumo_mes["Qtd. Coletas"],
            "qtd_sucesso_mes": resumo_mes["Qtd. Sucesso"],
            "realizado_ano": resumo_ano["Qtd. Sucesso"],
            "meta_ano": resumo_ano["Meta"],
            "gap_ano": resumo_ano["Diferença"],
            "ating_ano": resumo_ano["Resultado %"],
            "qtd_total_ano": resumo_ano["Qtd. Coletas"],
            "qtd_sucesso_ano": resumo_ano["Qtd. Sucesso"],
            "dias_restantes": 0,
            "necessario_dia": None,
            "realizado_ant": realizado_ant,
            "meta_ant": None,
            "var_real": var_real,
            "var_meta": None,
            "periodo_txt": periodo_txt,
            "qualidade": True,
            "despesa": False,
            "moto_margem": False,
        }

    if eh_tecfil(indicador):
        base_mes = base_ano[base_ano["MÊS"] == mes_referencia].copy()
        resumo_mes = resumo_tecfil_por_grupo(base_mes)
        resumo_ano = resumo_tecfil_por_grupo(base_ano)

        df_periodo = comparar_mesmo_periodo(df, indicador, ano_selecionado)
        realizado_ant = None
        var_real = None
        periodo_txt = None
        if not df_periodo.empty and len(df_periodo) >= 2:
            realizado_ant = df_periodo.iloc[0]["Realizado R$"]
            var_real = df_periodo.iloc[1]["Variação R$"]
            periodo_txt = df_periodo.iloc[1]["Período"]

        return {
            "hoje": hoje,
            "ano": ano_selecionado,
            "mes_referencia": mes_referencia,
            "nome_mes": MESES_MAPA.get(mes_referencia, str(mes_referencia)),
            "realizado_mes": resumo_mes["Realizado R$"],
            "meta_mes": resumo_mes["Meta R$"],
            "gap_mes": resumo_mes["Dif. R$"],
            "ating_mes": (resumo_mes["Realizado R$"] / resumo_mes["Meta R$"]) if resumo_mes["Meta R$"] else None,
            "realizado_ano": resumo_ano["Realizado R$"],
            "meta_ano": resumo_ano["Meta R$"],
            "gap_ano": resumo_ano["Dif. R$"],
            "ating_ano": (resumo_ano["Realizado R$"] / resumo_ano["Meta R$"]) if resumo_ano["Meta R$"] else None,
            "meta_kg_ano": resumo_ano["Meta KG"],
            "real_kg_ano": resumo_ano["Realizado KG"],
            "dif_kg_ano": resumo_ano["Dif. KG"],
            "meta_kg_mes": resumo_mes["Meta KG"],
            "real_kg_mes": resumo_mes["Realizado KG"],
            "dif_kg_mes": resumo_mes["Dif. KG"],
            "dias_restantes": 0,
            "necessario_dia": None,
            "realizado_ant": realizado_ant,
            "meta_ant": None,
            "var_real": var_real,
            "var_meta": None,
            "periodo_txt": periodo_txt,
            "tecfil": True,
            "despesa": False,
            "moto_margem": False,
        }

    if eh_resultado_financeiro(indicador):
        base_mes = base_ano[base_ano["MÊS"] == mes_referencia].copy()
        resumo_mes = resumo_resultado_financeiro_por_grupo(base_mes) if not base_mes.empty else resumo_resultado_financeiro_por_grupo(base_ano.iloc[0:0])
        resumo_ano = resumo_resultado_financeiro_por_grupo(base_ano)

        df_periodo = comparar_mesmo_periodo(df, indicador, ano_selecionado)
        realizado_ant = None
        var_real = None
        periodo_txt = None
        if not df_periodo.empty and len(df_periodo) >= 2:
            realizado_ant = df_periodo.iloc[0]["Resultado R$"]
            var_real = df_periodo.iloc[1]["Variação Resultado"]
            periodo_txt = df_periodo.iloc[1]["Período"]

        return {
            "hoje": hoje,
            "ano": ano_selecionado,
            "mes_referencia": mes_referencia,
            "nome_mes": MESES_MAPA.get(mes_referencia, str(mes_referencia)),
            "realizado_mes": resumo_mes["Resultado R$"],
            "meta_mes": resumo_mes["Meta %"],
            "gap_mes": resumo_mes["Resultado R$"],
            "ating_mes": resumo_mes["Resultado %"],
            "despesa_mes": resumo_mes["Despesa"],
            "receita_mes": resumo_mes["Receita"],
            "realizado_ano": resumo_ano["Resultado R$"],
            "meta_ano": resumo_ano["Meta %"],
            "gap_ano": resumo_ano["Resultado R$"],
            "ating_ano": resumo_ano["Resultado %"],
            "despesa_ano": resumo_ano["Despesa"],
            "receita_ano": resumo_ano["Receita"],
            "dias_restantes": 0,
            "necessario_dia": None,
            "realizado_ant": realizado_ant,
            "meta_ant": None,
            "var_real": var_real,
            "var_meta": None,
            "periodo_txt": periodo_txt,
            "resultado_financeiro": True,
            "despesa": False,
            "moto_margem": False,
        }

    if eh_despesa_geral(indicador):
        base_mes = base_ano[base_ano["MÊS"] == mes_referencia].copy()
        resumo_mes = resumo_despesa_geral_por_grupo(base_mes) if not base_mes.empty else resumo_despesa_geral_por_grupo(base_ano.iloc[0:0])
        resumo_ano = resumo_despesa_geral_por_grupo(base_ano)

        df_periodo = comparar_mesmo_periodo(df, indicador, ano_selecionado)
        realizado_ant = None
        var_real = None
        periodo_txt = None
        if not df_periodo.empty and len(df_periodo) >= 2:
            realizado_ant = df_periodo.iloc[0]["Despesa"]
            var_real = df_periodo.iloc[1]["Variação Despesa"]
            periodo_txt = df_periodo.iloc[1]["Período"]

        return {
            "hoje": hoje,
            "ano": ano_selecionado,
            "mes_referencia": mes_referencia,
            "nome_mes": MESES_MAPA.get(mes_referencia, str(mes_referencia)),
            "realizado_mes": resumo_mes["Despesa"],
            "meta_mes": resumo_mes["Limite %"],
            "gap_mes": resumo_mes["Resultado R$"],
            "ating_mes": resumo_mes["Tx. Sucesso"],
            "receita_mes": resumo_mes["Receita"],
            "limite_rs_mes": resumo_mes["Limite R$"],
            "realizado_ano": resumo_ano["Despesa"],
            "meta_ano": resumo_ano["Limite %"],
            "gap_ano": resumo_ano["Resultado R$"],
            "ating_ano": resumo_ano["Tx. Sucesso"],
            "receita_ano": resumo_ano["Receita"],
            "limite_rs_ano": resumo_ano["Limite R$"],
            "dias_restantes": 0,
            "necessario_dia": None,
            "realizado_ant": realizado_ant,
            "meta_ant": None,
            "var_real": var_real,
            "var_meta": None,
            "periodo_txt": periodo_txt,
            "despesa": True,
            "despesa_geral": True,
            "moto_margem": False,
        }

    despesa = eh_despesa(indicador)

    base_mes = base_ano[base_ano["MÊS"] == mes_referencia].copy()
    realizado_mes = base_mes["REALIZADO_CALC"].sum()
    meta_mes = base_mes["META_CALC"].sum()

    if eh_despesa_manutencao(indicador):
        gap_mes = meta_mes - realizado_mes
        ating_mes = realizado_mes / meta_mes if meta_mes > 0 else None
    elif despesa:
        gap_mes = meta_mes - realizado_mes
        ating_mes = meta_mes / realizado_mes if realizado_mes > 0 else None
    else:
        gap_mes = realizado_mes - meta_mes
        ating_mes = realizado_mes / meta_mes if meta_mes > 0 else None

    realizado_ano = base_ano["REALIZADO_CALC"].sum()
    meta_ano = base_ano["META_CALC"].sum()

    if eh_despesa_manutencao(indicador):
        gap_ano = meta_ano - realizado_ano
        ating_ano = realizado_ano / meta_ano if meta_ano > 0 else None
    elif despesa:
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
        "moto_margem": False,
    }


def gerar_texto_explicativo_pdf(resumo, indicador):
    if not resumo:
        return "Sem dados suficientes para gerar o resumo."

    hoje_txt = resumo["hoje"].strftime("%d/%m/%Y")
    nome_mes = resumo["nome_mes"]
    ano = resumo["ano"]


    if resumo.get("qualidade"):
        rot = rotulos_qualidade(indicador)
        meta_txt = fmt_num(resumo["meta_mes"]) if modelo_qualidade(indicador) == "parametro_coleta_critico" else fmt_pct(resumo["meta_mes"])
        texto = (
            f"Hoje é dia {hoje_txt}. No mês de {nome_mes}/{ano}, o indicador {indicador} apresentou "
            f"{fmt_num(resumo['qtd_sucesso_mes'])} em {rot['qtd_sucesso']}, sobre "
            f"{fmt_num(resumo['qtd_total_mes'])} em {rot['qtd_total']}. "
            f"O resultado do mês foi {fmt_pct(resumo['ating_mes'])}, contra meta/limite de {meta_txt}. "
            f"A diferença do mês foi {fmt_num(resumo['gap_mes'])}. "
        )

        meta_ano_txt = fmt_num(resumo["meta_ano"]) if modelo_qualidade(indicador) == "parametro_coleta_critico" else fmt_pct(resumo["meta_ano"])
        texto += (
            f"No acumulado do ano, o resultado é {fmt_pct(resumo['ating_ano'])}, "
            f"com {fmt_num(resumo['qtd_sucesso_ano'])} em {rot['qtd_sucesso']} "
            f"para {fmt_num(resumo['qtd_total_ano'])} em {rot['qtd_total']}. "
            f"A meta/limite acumulado considerado é {meta_ano_txt}."
        )

        return texto

    if resumo.get("tecfil"):
        texto = (
            f"Hoje é dia {hoje_txt}. No mês de {nome_mes}/{ano}, o realizado Tecfil foi de "
            f"{fmt_brl(resumo['realizado_mes'])}, contra meta de {fmt_brl(resumo['meta_mes'])}. "
            f"A diferença do mês foi de {fmt_brl(resumo['gap_mes'])}. "
            f"Em KG, o realizado foi {fmt_num(resumo['real_kg_mes'])}, contra meta de {fmt_num(resumo['meta_kg_mes'])}, "
            f"com diferença de {fmt_num(resumo['dif_kg_mes'])}. "
        )

        texto += (
            f"No acumulado do ano, o realizado em valor é {fmt_brl(resumo['realizado_ano'])}, "
            f"contra meta de {fmt_brl(resumo['meta_ano'])}, gerando diferença de {fmt_brl(resumo['gap_ano'])}. "
            f"No acumulado em KG, o realizado é {fmt_num(resumo['real_kg_ano'])}, "
            f"contra meta de {fmt_num(resumo['meta_kg_ano'])}, com diferença de {fmt_num(resumo['dif_kg_ano'])}."
        )

        return texto

    if resumo.get("resultado_financeiro"):
        texto = (
            f"Hoje é dia {hoje_txt}. No mês de {nome_mes}/{ano}, o resultado financeiro foi de "
            f"{fmt_brl(resumo['realizado_mes'])}, considerando despesa de {fmt_brl(resumo['despesa_mes'])} "
            f"e receita de {fmt_brl(resumo['receita_mes'])}. "
            f"A meta do mês é de {fmt_pct(resumo['meta_mes'])} e o resultado percentual foi de "
            f"{fmt_pct(resumo['ating_mes'])}. "
        )

        if resumo["realizado_mes"] is not None:
            if resumo["realizado_mes"] >= 0:
                texto += f"O resultado ficou positivo em {fmt_brl(resumo['realizado_mes'])}. "
            else:
                texto += f"O resultado ficou negativo em {fmt_brl(resumo['realizado_mes'])}. "

        texto += (
            f"No acumulado do ano, o resultado financeiro soma {fmt_brl(resumo['realizado_ano'])}, "
            f"com despesa de {fmt_brl(resumo['despesa_ano'])} e receita de {fmt_brl(resumo['receita_ano'])}."
        )

        return texto

    if resumo.get("despesa_geral"):
        texto = (
            f"Hoje é dia {hoje_txt}. No mês de {nome_mes}/{ano}, a despesa geral foi de "
            f"{fmt_brl(resumo['realizado_mes'])}, contra uma receita de {fmt_brl(resumo['receita_mes'])}. "
            f"O limite do mês é de {fmt_pct(resumo['meta_mes'])}, equivalente a {fmt_brl(resumo['limite_rs_mes'])}. "
            f"A taxa de sucesso, calculada como limite permitido dividido pela despesa, foi de {fmt_pct(resumo['ating_mes'])}. "
        )

        if resumo["gap_mes"] is not None:
            if resumo["gap_mes"] >= 0:
                texto += f"A despesa ficou dentro do limite, com resultado positivo de {fmt_brl(resumo['gap_mes'])}. "
            else:
                texto += f"A despesa ultrapassou o limite, gerando resultado negativo de {fmt_brl(resumo['gap_mes'])}. "

        texto += (
            f"No acumulado do ano, a despesa soma {fmt_brl(resumo['realizado_ano'])}, "
            f"a receita soma {fmt_brl(resumo['receita_ano'])}, e o limite ponderado anual é de "
            f"{fmt_pct(resumo['meta_ano'])}, equivalente a {fmt_brl(resumo['limite_rs_ano'])}. "
            f"O resultado acumulado é de {fmt_brl(resumo['gap_ano'])} e a taxa de sucesso acumulada é "
            f"{fmt_pct(resumo['ating_ano'])}."
        )

        if resumo["periodo_txt"] and resumo["realizado_ant"] is not None and resumo["var_real"] is not None:
            if resumo["var_real"] >= 0:
                texto += f" No acumulado de {resumo['periodo_txt']}, a despesa cresceu {fmt_pct(resumo['var_real'])} frente ao ano anterior."
            else:
                texto += f" No acumulado de {resumo['periodo_txt']}, a despesa recuou {fmt_pct(abs(resumo['var_real']))} frente ao ano anterior."

        return texto

    if eh_despesa_manutencao(indicador):
        texto = (
            f"Hoje é dia {hoje_txt}. No mês de {nome_mes}/{ano}, a despesa de manutenção foi de "
            f"{fmt_brl(resumo['realizado_mes'])}, contra um limite de {fmt_brl(resumo['meta_mes'])}. "
        )

        if resumo["gap_mes"] is not None:
            if resumo["gap_mes"] >= 0:
                texto += f"A despesa ficou dentro do limite, com saldo disponível de {fmt_brl(resumo['gap_mes'])}. "
            else:
                texto += f"A despesa ultrapassou o limite em {fmt_brl(abs(resumo['gap_mes']))}. "

        texto += (
            f"No acumulado do ano, a despesa realizada está em {fmt_brl(resumo['realizado_ano'])}, "
            f"contra um limite de {fmt_brl(resumo['meta_ano'])}. "
        )

        if resumo["gap_ano"] is not None:
            if resumo["gap_ano"] >= 0:
                texto += f"O saldo anual disponível dentro do limite é de {fmt_brl(resumo['gap_ano'])}."
            else:
                texto += f"O limite anual foi ultrapassado em {fmt_brl(abs(resumo['gap_ano']))}."

        return texto

    if resumo.get("moto_margem"):
        texto = (
            f"Hoje é dia {hoje_txt}. No mês de {nome_mes}/{ano}, o faturamento de Pneus Velhos Moto foi de "
            f"{fmt_brl(resumo['realizado_mes'])}, com compra de {fmt_brl(resumo['compra_mes'])} e margem bruta de "
            f"{fmt_brl(resumo['margem_mes'])}. A meta mínima de margem do mês é de {fmt_pct(resumo['meta_mes'])}, "
            f"equivalente a {fmt_brl(resumo['meta_margem_mes'])}, e a taxa de sucesso realizada foi de "
            f"{fmt_pct(resumo['ating_mes'])}. "
        )

        if resumo["gap_mes"] is not None:
            if resumo["gap_mes"] >= 0:
                texto += f"A margem ficou acima da meta em {fmt_brl(resumo['gap_mes'])}. "
            else:
                texto += f"A margem ficou abaixo da meta em {fmt_brl(abs(resumo['gap_mes']))}. "

        texto += (
            f"No acumulado do ano, o faturamento soma {fmt_brl(resumo['realizado_ano'])}, a compra soma "
            f"{fmt_brl(resumo['compra_ano'])} e a margem bruta acumulada é de {fmt_brl(resumo['margem_ano'])}. "
            f"A meta ponderada do ano é de {fmt_pct(resumo['meta_ano'])}, calculada por "
            f"SOMARPRODUTO(faturamento; meta %) dividido pelo faturamento total. "
            f"A taxa de sucesso acumulada está em {fmt_pct(resumo['ating_ano'])}."
        )

        if resumo["periodo_txt"] and resumo["realizado_ant"] is not None and resumo["var_real"] is not None:
            if resumo["var_real"] >= 0:
                texto += f" No acumulado de {resumo['periodo_txt']}, o faturamento cresceu {fmt_pct(resumo['var_real'])} frente ao ano anterior."
            else:
                texto += f" No acumulado de {resumo['periodo_txt']}, o faturamento recuou {fmt_pct(abs(resumo['var_real']))} frente ao ano anterior."

        return texto

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


def data_pascoa(ano):
    """
    Calcula a Páscoa pelo algoritmo de Meeus/Jones/Butcher.
    Base para feriados móveis nacionais.
    """
    a = ano % 19
    b = ano // 100
    c = ano % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes = (h + l - 7 * m + 114) // 31
    dia = ((h + l - 7 * m + 114) % 31) + 1
    return date(ano, mes, dia)


def feriados_brasil_nacionais(ano):
    """
    Feriados nacionais considerados na análise executiva.
    Inclui fixos e móveis mais usados no calendário corporativo.
    """
    pascoa = data_pascoa(ano)
    return {
        date(ano, 1, 1),
        date(ano, 4, 21),
        date(ano, 5, 1),
        date(ano, 9, 7),
        date(ano, 10, 12),
        date(ano, 11, 2),
        date(ano, 11, 15),
        date(ano, 11, 20),
        date(ano, 12, 25),
        pascoa - timedelta(days=48),
        pascoa - timedelta(days=47),
        pascoa - timedelta(days=2),
        pascoa + timedelta(days=60),
    }


def dias_uteis_mes(ano, mes):
    """
    Quantidade de dias úteis do mês, considerando segunda a sexta
    e feriados nacionais.
    """
    try:
        ultimo = calendar.monthrange(int(ano), int(mes))[1]
        feriados = feriados_brasil_nacionais(int(ano))
        total = 0
        for dia in range(1, ultimo + 1):
            d = date(int(ano), int(mes), dia)
            if d.weekday() < 5 and d not in feriados:
                total += 1
        return total
    except Exception:
        return None


def parse_data_geracao(data_geracao_planilha):
    try:
        data = pd.to_datetime(data_geracao_planilha, dayfirst=True, errors="coerce")
        if pd.isna(data):
            return None
        return data.to_pydatetime()
    except Exception:
        return None


def dias_uteis_ate_data(data_base):
    """
    Dias úteis decorridos no mês até a data da base, incluindo o dia da base
    se ele for dia útil.
    """
    try:
        if data_base is None:
            return None
        ano = data_base.year
        mes = data_base.month
        feriados = feriados_brasil_nacionais(ano)
        total = 0
        for dia in range(1, data_base.day + 1):
            d = date(ano, mes, dia)
            if d.weekday() < 5 and d not in feriados:
                total += 1
        return total
    except Exception:
        return None



def dias_uteis_ano(ano):
    """Total de dias úteis no ano, considerando feriados nacionais."""
    try:
        ano = int(ano)
        feriados = feriados_brasil_nacionais(ano)
        total = 0
        for mes in range(1, 13):
            ultimo = calendar.monthrange(ano, mes)[1]
            for dia in range(1, ultimo + 1):
                d = date(ano, mes, dia)
                if d.weekday() < 5 and d not in feriados:
                    total += 1
        return total
    except Exception:
        return None


def dias_uteis_ano_ate_data(data_base):
    """Dias úteis decorridos no ano até a data-base, considerando feriados nacionais."""
    try:
        if data_base is None:
            return None

        ano = int(data_base.year)
        feriados = feriados_brasil_nacionais(ano)
        total = 0

        inicio = date(ano, 1, 1)
        fim = date(ano, int(data_base.month), int(data_base.day))

        atual = inicio
        while atual <= fim:
            if atual.weekday() < 5 and atual not in feriados:
                total += 1
            atual += timedelta(days=1)

        return total
    except Exception:
        return None


def atingimento_esperado_ano(ano, data_geracao_planilha):
    """
    Calcula quanto da meta anual já deveria estar cumprido pela proporção
    de dias úteis decorridos no ano.

    Exemplo de leitura:
    estamos em 30,5% da meta; pelo calendário útil, era para estar em 34,0%.
    """
    try:
        data_base = parse_data_geracao(data_geracao_planilha)
        if data_base is None or int(data_base.year) != int(ano):
            return None

        total = dias_uteis_ano(ano)
        decorridos = dias_uteis_ano_ate_data(data_base)

        if not total or not decorridos:
            return None

        return decorridos / total
    except Exception:
        return None


def nota_atingimento_esperado(atingimento_atual, ano, data_geracao_planilha, despesa=False):
    """
    Monta nota curta para o card:
    Era p/ estar X%; estamos Y p.p. acima/abaixo.
    """
    esperado = atingimento_esperado_ano(ano, data_geracao_planilha)

    if esperado is None or atingimento_atual is None or pd.isna(atingimento_atual):
        return None, "info"

    diferenca = atingimento_atual - esperado

    if despesa:
        # Para despesa/limite, menor costuma ser melhor. Mantém leitura neutra.
        return f"Esperado: {fmt_pct(esperado)}", "info"

    if diferenca >= 0:
        return f"Esperado: {fmt_pct(esperado)} | {abs(diferenca):.1%} acima", "good"

    return f"Esperado: {fmt_pct(esperado)} | {abs(diferenca):.1%} abaixo", "warn"


def valor_principal_para_analise(df_mensal, indicador):
    """
    Escolhe a coluna principal para análise executiva.
    """
    if df_mensal is None or df_mensal.empty:
        return None, None, "soma"

    if eh_tecfil(indicador) and "Realizado R$" in df_mensal.columns:
        return "Realizado R$", "Realizado R$", "soma"

    if eh_moto_margem(indicador) and "Faturamento" in df_mensal.columns:
        return "Faturamento", "Faturamento", "soma"

    if eh_resultado_financeiro(indicador):
        if "Resultado %" in df_mensal.columns:
            return "Resultado %", "Resultado %", "media"
        if "Resultado R$" in df_mensal.columns:
            return "Resultado R$", "Resultado R$", "soma"

    if eh_despesa_geral(indicador) and "Tx. Sucesso" in df_mensal.columns:
        return "Tx. Sucesso", "Tx. Sucesso", "media"

    if eh_qualidade(indicador) and "Resultado %" in df_mensal.columns:
        return "Resultado %", "Resultado %", "media"

    if "Realizado" in df_mensal.columns:
        return "Realizado", "Realizado", "soma"

    if "Receita" in df_mensal.columns:
        return "Receita", "Receita", "soma"

    return None, None, "soma"


def agrega_analise(df_mensal, coluna, modo):
    if coluna is None or df_mensal is None or df_mensal.empty or coluna not in df_mensal.columns:
        return None
    serie = pd.to_numeric(df_mensal[coluna], errors="coerce").dropna()
    if serie.empty:
        return None
    if modo == "media":
        return serie.mean()
    return serie.sum()


def formata_valor_analise(valor, coluna, modo):
    if valor is None or pd.isna(valor):
        return "-"
    nome = normalizar_texto(coluna)
    if "%" in str(coluna) or "PERCENTUAL" in nome or "RESULTADO %" in nome or "TX SUCESSO" in nome or "ATING" in nome:
        return fmt_pct(valor)
    if "KG" in nome or "QTD" in nome:
        return fmt_num(valor)
    return fmt_brl(valor)


def mes_base_por_data(df, ano, data_geracao_planilha="-"):
    """
    Mês base para análise/projeção:
    1. Usa a data da base, se ela for do mesmo ano.
    2. Senão, usa o maior mês disponível no dataframe.
    """
    try:
        data_base = parse_data_geracao(data_geracao_planilha)
        if data_base is not None and int(data_base.year) == int(ano):
            return int(data_base.month)

        base_ano = df[df["ANO"] == ano].copy()
        if not base_ano.empty and "MÊS" in base_ano.columns and base_ano["MÊS"].notna().any():
            return int(base_ano["MÊS"].max())
    except Exception:
        pass
    return agora_br().month


def obter_tabela_mensal_ano(df, indicador, ano):
    try:
        tabela = tabela_completa_ano(df, ano, indicador)
        if tabela is None:
            return pd.DataFrame()
        tabela = tabela.copy()
        if "Mês" in tabela.columns:
            tabela = tabela[tabela["Mês"] != "TOTAL"].copy()
        return tabela
    except Exception:
        return pd.DataFrame()


def coluna_valor_principal_projecao(tabela, indicador):
    """
    Define a coluna que será usada na projeção.
    A projeção deve ser objetiva e diferente da aba Análise.
    """
    if tabela is None or tabela.empty:
        return None, "Valor"

    if eh_tecfil(indicador) and "Realizado R$" in tabela.columns:
        return "Realizado R$", "Realizado R$"

    if eh_moto_margem(indicador) and "Faturamento" in tabela.columns:
        return "Faturamento", "Faturamento"

    if eh_resultado_financeiro(indicador) and "Receita" in tabela.columns:
        return "Receita", "Receita"

    if eh_despesa_geral(indicador) and "Despesa" in tabela.columns:
        return "Despesa", "Despesa"

    if eh_qualidade(indicador) and "Resultado %" in tabela.columns:
        return "Resultado %", "Resultado %"

    if "Realizado" in tabela.columns:
        return "Realizado", "Realizado"

    for col in ["Receita", "Faturamento", "Realizado R$", "Resultado R$"]:
        if col in tabela.columns:
            return col, col

    return None, "Valor"


def coluna_meta_principal_projecao(tabela, indicador):
    if tabela is None or tabela.empty:
        return None, "Meta"

    if eh_tecfil(indicador) and "Meta R$" in tabela.columns:
        return "Meta R$", "Meta R$"

    if eh_moto_margem(indicador):
        if "Meta Margem (R$)" in tabela.columns:
            return "Meta Margem (R$)", "Meta Margem (R$)"
        if "Meta %" in tabela.columns:
            return "Meta %", "Meta %"

    if eh_resultado_financeiro(indicador) and "Meta %" in tabela.columns:
        return "Meta %", "Meta %"

    if eh_despesa_geral(indicador):
        if "Limite R$" in tabela.columns:
            return "Limite R$", "Limite R$"
        if "Limite %" in tabela.columns:
            return "Limite %", "Limite %"

    if eh_qualidade(indicador) and "Meta" in tabela.columns:
        return "Meta", "Meta"

    if "Meta" in tabela.columns:
        return "Meta", "Meta"

    return None, "Meta"


def formatar_generico_por_coluna(valor, coluna):
    if valor is None or pd.isna(valor):
        return "-"
    nome = normalizar_texto(coluna)
    if "%" in str(coluna) or "PERCENT" in nome or "TX" in nome or "ATING" in nome or "RESULTADO %" in nome:
        return fmt_pct(valor)
    if "KG" in nome or "QTD" in nome:
        return fmt_num(valor)
    return fmt_brl(valor)


def calcular_projecao_mes(df, indicador, data_geracao_planilha):
    """
    Calcula projeção mensal.
    Diferente da aba Análise: aqui o foco é ritmo, fechamento projetado e gap projetado.
    """
    anos = sorted(df["ANO"].dropna().unique()) if df is not None and not df.empty else []
    if not anos:
        return {}

    ano = int(anos[-1])
    mes = mes_base_por_data(df, ano, data_geracao_planilha)
    mes_nome = MESES_MAPA.get(mes, str(mes))
    tabela = obter_tabela_mensal_ano(df, indicador, ano)

    if tabela.empty or "MÊS" not in tabela.columns:
        return {"ano": ano, "mes": mes, "mes_nome": mes_nome}

    linha_mes = tabela[tabela["MÊS"] == mes].copy()
    if linha_mes.empty:
        linha_mes = tabela[tabela["MÊS"] <= mes].tail(1).copy()

    col_valor, label_valor = coluna_valor_principal_projecao(tabela, indicador)
    col_meta, label_meta = coluna_meta_principal_projecao(tabela, indicador)

    valor_mes = None
    meta_mes = None

    if col_valor and col_valor in linha_mes.columns:
        valor_mes = pd.to_numeric(linha_mes[col_valor], errors="coerce").fillna(0).sum()

    if col_meta and col_meta in linha_mes.columns:
        meta_mes = pd.to_numeric(linha_mes[col_meta], errors="coerce").dropna()
        meta_mes = meta_mes.sum() if not meta_mes.empty else None

    data_base = parse_data_geracao(data_geracao_planilha)
    dias_total = dias_uteis_mes(ano, mes)

    if data_base is not None and data_base.year == ano and data_base.month == mes:
        dias_decorridos = dias_uteis_ate_data(data_base)
    else:
        # Se a data da base não corresponde ao mês analisado, evita criar projeção falsa de "mês em aberto".
        dias_decorridos = dias_total

    dias_restantes = None
    if dias_total is not None and dias_decorridos is not None:
        dias_restantes = max(dias_total - dias_decorridos, 0)

    media_atual = None
    if valor_mes is not None and dias_decorridos not in [None, 0]:
        media_atual = valor_mes / dias_decorridos

    projecao = None
    if media_atual is not None and dias_total is not None:
        projecao = media_atual * dias_total

    media_necessaria = None
    gap_projetado = None
    if meta_mes is not None and pd.notna(meta_mes):
        if dias_restantes is not None and dias_restantes > 0:
            media_necessaria = max((meta_mes - (valor_mes or 0)) / dias_restantes, 0)
        gap_projetado = (projecao - meta_mes) if projecao is not None else None

    return {
        "ano": ano,
        "mes": mes,
        "mes_nome": mes_nome,
        "col_valor": col_valor,
        "label_valor": label_valor,
        "col_meta": col_meta,
        "label_meta": label_meta,
        "valor_mes": valor_mes,
        "meta_mes": meta_mes,
        "dias_total": dias_total,
        "dias_decorridos": dias_decorridos,
        "dias_restantes": dias_restantes,
        "media_atual": media_atual,
        "media_necessaria": media_necessaria,
        "projecao": projecao,
        "gap_projetado": gap_projetado,
        "data_base": data_geracao_planilha,
    }


def card_ouro(label, value, note="", status="info"):
    st.markdown(
        f"""
        <div class="gold-card {status}">
            <div class="gold-label">{label}</div>
            <div class="gold-value">{value}</div>
            <div class="gold-note">{note}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def renderizar_projecao_executiva(df, indicador, filial, data_geracao_planilha):
    """
    Aba Projeção:
    informação de ritmo e tendência de fechamento.
    Não repete a aba Análise.
    """
    st.markdown(f"<h2 style='margin-bottom:6px;'>📌 Projeção — {indicador} | {filial}</h2>", unsafe_allow_html=True)

    if df is None or df.empty:
        st.warning("Não há dados suficientes para gerar projeção.")
        return

    info = calcular_projecao_mes(df, indicador, data_geracao_planilha)
    if not info:
        st.warning("Não foi possível calcular a projeção para este indicador.")
        return

    contexto_dashboard(indicador, filial, info.get("ano", "-"), f"{info.get('mes_nome', '-')}/{info.get('ano', '-')}", data_geracao_planilha)

    titulo_secao("Ritmo de fechamento", "Leitura projetada com base no realizado do mês e nos dias úteis.")

    valor_mes = info.get("valor_mes")
    meta_mes = info.get("meta_mes")
    projecao = info.get("projecao")
    gap_projetado = info.get("gap_projetado")
    media_atual = info.get("media_atual")
    media_necessaria = info.get("media_necessaria")
    col_valor = info.get("col_valor")
    col_meta = info.get("col_meta")

    status_proj = "good" if gap_projetado is not None and pd.notna(gap_projetado) and gap_projetado >= 0 else "warn"

    renderizar_semaforo_projecao(info)

    col1, col2, col3 = st.columns(3)
    with col1:
        card_ouro(
            "Realizado no mês",
            formatar_generico_por_coluna(valor_mes, col_valor),
            f"Valor acumulado em {info.get('mes_nome', '-')}/{info.get('ano', '-')}.",
            "info",
        )
    with col2:
        card_ouro(
            "Projeção de fechamento",
            formatar_generico_por_coluna(projecao, col_valor),
            "Estimativa usando a média por dia útil.",
            status_proj,
        )
    with col3:
        card_ouro(
            "Gap projetado",
            formatar_generico_por_coluna(gap_projetado, col_valor),
            "Diferença estimada contra a meta do mês.",
            status_proj,
        )

    col4, col5, col6 = st.columns(3)
    with col4:
        card_ouro(
            "Média diária atual",
            formatar_generico_por_coluna(media_atual, col_valor),
            f"Base: {fmt_num(info.get('dias_decorridos'))} dias úteis decorridos.",
            "info",
        )
    with col5:
        card_ouro(
            "Média diária necessária",
            formatar_generico_por_coluna(media_necessaria, col_valor),
            f"Para buscar a meta nos {fmt_num(info.get('dias_restantes'))} dias úteis restantes.",
            "warn" if media_necessaria and media_atual and media_necessaria > media_atual else "good",
        )
    with col6:
        card_ouro(
            "Meta do mês",
            formatar_generico_por_coluna(meta_mes, col_meta),
            f"Dias úteis do mês: {fmt_num(info.get('dias_total'))}.",
            "info",
        )

    if gap_projetado is not None and pd.notna(gap_projetado):
        if gap_projetado >= 0:
            alerta_executivo_dashboard(
                "Tendência positiva",
                f"Mantido o ritmo atual, a projeção indica fechamento acima da meta em <strong>{formatar_generico_por_coluna(gap_projetado, col_valor)}</strong>.",
                "good",
            )
        else:
            alerta_executivo_dashboard(
                "Risco de não atingir a meta",
                f"Mantido o ritmo atual, a projeção indica fechamento abaixo da meta em <strong>{formatar_generico_por_coluna(abs(gap_projetado), col_valor)}</strong>. O ponto de atenção é a média diária necessária até o fim do mês.",
                "warn",
            )

    # Gráfico simples e objetivo da projeção, diferente da aba Análise.
    if valor_mes is not None or meta_mes is not None or projecao is not None:
        graf = pd.DataFrame({
            "Indicador": ["Realizado atual", "Projeção", "Meta"],
            "Valor": [valor_mes or 0, projecao or 0, meta_mes or 0],
        })
        fig = go.Figure()
        cores = [COR_LARANJA, COR_VERDE if status_proj == "good" else COR_LARANJA, COR_AZUL]
        fig.add_bar(
            x=graf["Indicador"],
            y=graf["Valor"],
            marker_color=cores,
            text=[formatar_generico_por_coluna(v, col_valor) for v in graf["Valor"]],
            textposition="outside",
            hovertemplate="<b>%{x}</b><br>Valor: %{text}<extra></extra>",
        )
        fig.update_layout(
            title="Realizado x Projeção x Meta",
            height=420,
            margin=dict(t=70, b=30, l=20, r=20),
            yaxis_title="Valor",
            showlegend=False,
        )
        st.plotly_chart(aplicar_tema_plotly_mazola(fig), use_container_width=True, key="grafico_projecao_executiva")

    titulo_secao("Tabela da projeção", "Base numérica usada no cálculo do ritmo.")
    st.markdown('<div class="table-note-v5">Use esta tabela para validar os números utilizados na projeção de fechamento.</div>', unsafe_allow_html=True)
    tabela = pd.DataFrame([
        {"Item": "Base atualizada em", "Resultado": info.get("data_base", "-")},
        {"Item": "Mês base", "Resultado": f"{info.get('mes_nome', '-')}/{info.get('ano', '-')}" },
        {"Item": "Dias úteis no mês", "Resultado": fmt_num(info.get("dias_total"))},
        {"Item": "Dias úteis decorridos", "Resultado": fmt_num(info.get("dias_decorridos"))},
        {"Item": "Dias úteis restantes", "Resultado": fmt_num(info.get("dias_restantes"))},
        {"Item": "Realizado no mês", "Resultado": formatar_generico_por_coluna(valor_mes, col_valor)},
        {"Item": "Meta do mês", "Resultado": formatar_generico_por_coluna(meta_mes, col_meta)},
        {"Item": "Média diária atual", "Resultado": formatar_generico_por_coluna(media_atual, col_valor)},
        {"Item": "Média diária necessária", "Resultado": formatar_generico_por_coluna(media_necessaria, col_valor)},
        {"Item": "Projeção de fechamento", "Resultado": formatar_generico_por_coluna(projecao, col_valor)},
        {"Item": "Gap projetado", "Resultado": formatar_generico_por_coluna(gap_projetado, col_valor)},
    ])
    st.dataframe(tabela, use_container_width=True, hide_index=True)


def montar_ranking_atingimento(df_filiais, indicador, ano, mes_limite):
    """
    Ranking justo por % de atingimento de meta.
    Ao lado, mostra faturamento/realizado, meta e gap.
    """
    if df_filiais is None or df_filiais.empty:
        return pd.DataFrame()

    base = df_filiais[(df_filiais["ANO"] == ano) & (df_filiais["MÊS"] <= mes_limite)].copy()
    if base.empty:
        return pd.DataFrame()

    comp = comparativo_filiais(base, ano, indicador)
    if comp is None or comp.empty:
        return pd.DataFrame()

    out = comp.copy()

    if "Atingimento" not in out.columns:
        if "Realizado" in out.columns and "Meta" in out.columns:
            out["Atingimento"] = out["Realizado"] / out["Meta"].replace(0, pd.NA)
        else:
            out["Atingimento"] = pd.NA

    if "Realizado" not in out.columns:
        if "Faturamento" in out.columns:
            out["Realizado"] = out["Faturamento"]
        elif "Realizado R$" in out.columns:
            out["Realizado"] = out["Realizado R$"]
        elif "Receita" in out.columns:
            out["Realizado"] = out["Receita"]
        else:
            out["Realizado"] = pd.NA

    if "Meta" not in out.columns:
        if "Meta R$" in out.columns:
            out["Meta"] = out["Meta R$"]
        elif "Meta Margem (R$)" in out.columns:
            out["Meta"] = out["Meta Margem (R$)"]
        else:
            out["Meta"] = pd.NA

    if "Gap" not in out.columns:
        out["Gap"] = out["Realizado"] - out["Meta"]

    # Para despesas com limite, menor uso do limite é melhor.
    if eh_despesa_com_limite(indicador):
        out = out.sort_values("Atingimento", ascending=True, na_position="last")
    else:
        out = out.sort_values("Atingimento", ascending=False, na_position="last")

    out = out.reset_index(drop=True)
    out.insert(0, "Ranking", [f"{i}º" for i in range(1, len(out) + 1)])

    cols = ["Ranking", "FILIAL", "Atingimento", "Realizado", "Meta", "Gap"]
    return out[[c for c in cols if c in out.columns]]


def renderizar_analise_executiva(df, indicador, filial, data_geracao_planilha, df_filiais=None):
    """
    Aba Análise:
    informações executivas de alto impacto.
    Diferente da aba Projeção: aqui o foco é comparação, ranking e pontos importantes.
    """
    st.markdown(f"<h2 style='margin-bottom:6px;'>🧠 Análise Executiva — {indicador} | {filial}</h2>", unsafe_allow_html=True)

    if df is None or df.empty:
        st.warning("Não há dados suficientes para gerar a análise executiva.")
        return

    anos = sorted(df["ANO"].dropna().unique())
    if not anos:
        st.warning("Não há ano válido para análise.")
        return

    ano_atual = int(anos[-1])
    mes_atual = mes_base_por_data(df, ano_atual, data_geracao_planilha)
    mes_nome = MESES_MAPA.get(mes_atual, str(mes_atual))
    periodo_label = f"Jan a {mes_nome}"

    contexto_dashboard(indicador, filial, ano_atual, periodo_label, data_geracao_planilha)

    titulo_secao("Top insights", "Três informações de leitura rápida para decisão executiva.")
    renderizar_top_insights(df, indicador, filial, data_geracao_planilha, df_filiais)

    titulo_secao("Informações que merecem atenção", "Leitura executiva do desempenho, com foco em comparação e priorização.")

    try:
        periodo_cmp = comparar_mesmo_periodo(df, indicador, ano_atual)
    except Exception:
        periodo_cmp = pd.DataFrame()

    if periodo_cmp is not None and not periodo_cmp.empty and len(periodo_cmp) >= 2:
        linha_atual = periodo_cmp.iloc[1]
        linha_ant = periodo_cmp.iloc[0]
        col_var = next((c for c in periodo_cmp.columns if normalizar_texto(c).startswith("VARIACAO")), None)
        col_valor = next((c for c in ["Realizado", "Faturamento", "Realizado R$", "Receita", "Resultado R$", "Resultado %", "Tx. Sucesso"] if c in periodo_cmp.columns), None)

        valor_atual = linha_atual.get(col_valor) if col_valor else None
        valor_ant = linha_ant.get(col_valor) if col_valor else None
        variacao = linha_atual.get(col_var) if col_var else None

        status = "good" if pd.notna(variacao) and variacao >= 0 else "warn"
        titulo = "Crescimento contra o mesmo período" if status == "good" else "Queda contra o mesmo período"

        alerta_executivo_dashboard(
            titulo,
            f"Em <strong>{periodo_label}/{ano_atual}</strong>, o valor principal está em <strong>{formatar_generico_por_coluna(valor_atual, col_valor)}</strong>. "
            f"No mesmo período do ano anterior, estava em <strong>{formatar_generico_por_coluna(valor_ant, col_valor)}</strong>. "
            f"A variação é <strong>{fmt_var_pct_seguro(variacao) if variacao is not None else '—'}</strong>.",
            status,
        )
    else:
        alerta_executivo_dashboard(
            "Comparativo anual indisponível",
            "Não há dados suficientes para comparar o mesmo período com o ano anterior.",
            "warn",
        )

    # Melhor e pior mês do ano pelo valor principal
    try:
        tabela = obter_tabela_mensal_ano(df, indicador, ano_atual)
        coluna, label, modo = valor_principal_para_analise(tabela, indicador)
        base_meses = tabela[(tabela["MÊS"] <= mes_atual)].copy() if "MÊS" in tabela.columns else tabela.copy()

        if coluna and coluna in base_meses.columns and not base_meses.empty:
            serie = pd.to_numeric(base_meses[coluna], errors="coerce")
            base_meses = base_meses.assign(_valor=serie)
            base_validos = base_meses[base_meses["_valor"].notna()].copy()

            if not base_validos.empty:
                melhor = base_validos.sort_values("_valor", ascending=False).iloc[0]
                pior = base_validos.sort_values("_valor", ascending=True).iloc[0]

                col1, col2 = st.columns(2)
                with col1:
                    card_ouro(
                        "Melhor mês do período",
                        f"{melhor.get('Mês', '-')}",
                        f"{label}: {formatar_generico_por_coluna(melhor.get('_valor'), coluna)}",
                        "good",
                    )
                with col2:
                    card_ouro(
                        "Pior mês do período",
                        f"{pior.get('Mês', '-')}",
                        f"{label}: {formatar_generico_por_coluna(pior.get('_valor'), coluna)}",
                        "warn",
                    )
    except Exception:
        pass

    # Ranking por atingimento de meta
    if filial == "Geral":
        titulo_secao("Ranking de unidades por atingimento da meta", "Ranking justo por percentual de meta; ao lado ficam os valores de realizado, meta e gap.")
        st.markdown('<div class="table-note-v5">Ordenação principal por % de atingimento, para não favorecer apenas unidades com maior volume absoluto.</div>', unsafe_allow_html=True)

        ranking = montar_ranking_atingimento(df_filiais, indicador, ano_atual, mes_atual)

        if ranking is not None and not ranking.empty:
            st.dataframe(
                ranking.style
                .format({
                    "Atingimento": lambda v: fmt_pct(v) if pd.notna(v) else "—",
                    "Realizado": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                    "Meta": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                    "Gap": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                })
                .map(cor_atingimento, subset=["Atingimento"])
                .map(lambda v: cor_gap_valor(v, False), subset=["Gap"]),
                use_container_width=True,
                hide_index=True,
            )

            try:
                top = ranking.iloc[0]
                alerta_executivo_dashboard(
                    "Unidade destaque",
                    f"A unidade com melhor atingimento no período é <strong>{top.get('FILIAL', '-')}</strong>, com <strong>{fmt_pct(top.get('Atingimento'))}</strong> de atingimento da meta.",
                    "good",
                )
            except Exception:
                pass
        else:
            st.info("Não há dados suficientes para montar o ranking por unidade.")

    titulo_secao("Resumo de apoio", "Tabela objetiva com os principais dados da leitura executiva.")
    st.markdown('<div class="table-note-v5">Resumo compacto dos dados usados na análise automática.</div>', unsafe_allow_html=True)
    apoio = pd.DataFrame([
        {"Item": "Período analisado", "Resultado": f"{periodo_label}/{ano_atual}"},
        {"Item": "Base atualizada em", "Resultado": data_geracao_planilha},
        {"Item": "Foco da análise", "Resultado": "comparação anual, melhor/pior mês e ranking por atingimento"},
    ])
    st.dataframe(apoio, use_container_width=True, hide_index=True)







def _primeira_coluna_existente(df_obj, candidatos):
    if df_obj is None or df_obj.empty:
        return None
    for col in candidatos:
        if col in df_obj.columns:
            return col
    return None


def _formatar_compare(valor, coluna):
    if valor is None or pd.isna(valor):
        return "—"
    nome = normalizar_texto(coluna)
    if "%" in str(coluna) or "TX" in nome or "ATING" in nome or "RESULTADO %" in nome or "VARIACAO" in nome:
        return fmt_pct(valor)
    if "KG" in nome or "QTD" in nome:
        return fmt_num(valor)
    return fmt_brl(valor)


def cards_comparativo_periodo(df_periodo_tela, indicador):
    """
    Cards resumidos para a seção 'Mesmo período x ano anterior'.
    Detecta a estrutura do indicador e deixa explícito o que está sendo comparado.
    """
    try:
        if df_periodo_tela is None or df_periodo_tela.empty or len(df_periodo_tela) < 2:
            return

        atual = df_periodo_tela.iloc[-1]
        anterior = df_periodo_tela.iloc[-2]

        if eh_moto_margem(indicador):
            col_principal = "Faturamento" if "Faturamento" in df_periodo_tela.columns else "Realizado"
            label_atual = "Faturamento atual do período"
            label_anterior = "Faturamento no mesmo período anterior"
            nota_atual = "Comparação acumulada do faturamento."
            nota_anterior = "Referência de faturamento do ano anterior."
        elif eh_tecfil(indicador):
            col_principal = "Realizado R$" if "Realizado R$" in df_periodo_tela.columns else "Realizado"
            label_atual = "Realizado atual do período"
            label_anterior = "Realizado no mesmo período anterior"
            nota_atual = "Comparativo principal em R$."
            nota_anterior = "Referência do mesmo período no ano anterior."
        elif eh_qualidade(indicador):
            col_principal = "Resultado %" if "Resultado %" in df_periodo_tela.columns else "Atingimento"
            label_atual = "Resultado atual do período"
            label_anterior = "Resultado no mesmo período anterior"
            nota_atual = "Comparativo da taxa/resultado de qualidade."
            nota_anterior = "Referência do mesmo período no ano anterior."
        elif eh_resultado_financeiro(indicador):
            col_principal = "Resultado R$" if "Resultado R$" in df_periodo_tela.columns else "Resultado %"
            label_atual = "Resultado financeiro atual"
            label_anterior = "Resultado financeiro anterior"
            nota_atual = "Diferença em R$ entre o resultado % realizado e a meta do período."
            nota_anterior = "Referência do resultado financeiro no mesmo período do ano anterior."
        elif eh_despesa_geral(indicador):
            col_principal = "Tx. Sucesso" if "Tx. Sucesso" in df_periodo_tela.columns else "Resultado R$"
            label_atual = "Despesa/Receita atual"
            label_anterior = "Despesa/Receita anterior"
            nota_atual = "Percentual da receita consumido pela despesa no período."
            nota_anterior = "Referência do mesmo período no ano anterior."
        else:
            col_principal = _primeira_coluna_existente(
                df_periodo_tela,
                ["Realizado", "Faturamento", "Realizado R$", "Receita", "Resultado R$", "Resultado %", "Tx. Sucesso"]
            )
            label_atual = "Valor atual do período"
            label_anterior = "Mesmo período anterior"
            nota_atual = "Resultado principal do período atual."
            nota_anterior = "Referência do mesmo período no ano anterior."

        principal_txt = _formatar_compare(atual.get(col_principal), col_principal) if col_principal else "—"
        anterior_txt = _formatar_compare(anterior.get(col_principal), col_principal) if col_principal else "—"

        st.markdown(
            f"""
            <div class="compare-card-grid compare-card-grid-two">
                <div class="compare-mini-card">
                    <div class="compare-label">{label_atual}</div>
                    <div class="compare-value">{principal_txt}</div>
                    <div class="compare-note">{nota_atual}</div>
                </div>
                <div class="compare-mini-card">
                    <div class="compare-label">{label_anterior}</div>
                    <div class="compare-value">{anterior_txt}</div>
                    <div class="compare-note">{nota_anterior}</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    except Exception:
        pass


def titulo_comparativo_filiais(indicador, ano_base_filial):
    if eh_tecfil(indicador):
        return "Comparativo entre unidades"
    return "Comparativo entre filiais"




def status_geral_indicador(df, indicador, ano):
    """
    Status visual simples para capa premium.
    Respeita estruturas principais e evita alterar cálculo existente.
    """
    try:
        tabela = obter_tabela_mensal_ano(df, indicador, ano)
        if tabela is None or tabela.empty:
            return "info", "Em acompanhamento", "Sem dados suficientes para status automático."

        # Totais/indicadores principais
        col_valor, _ = coluna_valor_principal_projecao(tabela, indicador)
        col_meta, _ = coluna_meta_principal_projecao(tabela, indicador)

        if eh_qualidade(indicador):
            col_result = "Resultado %" if "Resultado %" in tabela.columns else col_valor
            col_meta_q = "Meta" if "Meta" in tabela.columns else col_meta
            serie_res = pd.to_numeric(tabela.get(col_result), errors="coerce").dropna()
            serie_meta = pd.to_numeric(tabela.get(col_meta_q), errors="coerce").dropna()
            if not serie_res.empty and not serie_meta.empty:
                res = serie_res.iloc[-1]
                meta = serie_meta.iloc[-1]
                if modelo_qualidade(indicador) == "parametro_coleta_critico":
                    ok = res <= meta
                else:
                    ok = res >= meta
                return ("good" if ok else "warn"), ("Dentro da referência" if ok else "Ponto de atenção"), f"Resultado atual: {fmt_pct(res)} | Referência: {fmt_pct(meta)}"

        if eh_moto_margem(indicador):
            if "Tx. Sucesso" in tabela.columns:
                tx_serie = pd.to_numeric(tabela["Tx. Sucesso"], errors="coerce").dropna()
                meta_serie = pd.to_numeric(tabela["Meta %"], errors="coerce").dropna() if "Meta %" in tabela.columns else pd.Series(dtype="float64")
                if not tx_serie.empty:
                    tx_atual = tx_serie.iloc[-1]
                    if not meta_serie.empty:
                        meta_atual = meta_serie.iloc[-1]
                        ok = tx_atual >= meta_atual
                        return ("good" if ok else "warn"), ("Meta atingida" if ok else "Abaixo da meta"), f"Tx. Sucesso: {fmt_pct(tx_atual)} | Meta: {fmt_pct(meta_atual)}"
                    return "info", "Em acompanhamento", f"Tx. Sucesso: {fmt_pct(tx_atual)}"

        if eh_despesa_geral(indicador):
            if "Tx. Sucesso" in tabela.columns and "Limite %" in tabela.columns:
                tabela_valida = tabela.copy()
                if "Mês" in tabela_valida.columns:
                    total_rows = tabela_valida[tabela_valida["Mês"].astype(str).str.upper() == "TOTAL"]
                    if not total_rows.empty:
                        linha = total_rows.iloc[-1]
                    else:
                        linha = tabela_valida.dropna(subset=["Tx. Sucesso"]).tail(1).iloc[0] if not tabela_valida.dropna(subset=["Tx. Sucesso"]).empty else None
                else:
                    linha = tabela_valida.dropna(subset=["Tx. Sucesso"]).tail(1).iloc[0] if not tabela_valida.dropna(subset=["Tx. Sucesso"]).empty else None

                if linha is not None:
                    tx = pd.to_numeric(pd.Series([linha.get("Tx. Sucesso")]), errors="coerce").iloc[0]
                    limite = pd.to_numeric(pd.Series([linha.get("Limite %")]), errors="coerce").iloc[0]
                    if pd.notna(tx) and pd.notna(limite):
                        ok = tx <= limite
                        return (
                            "good" if ok else "warn",
                            "Dentro do limite" if ok else "Acima do limite",
                            f"Despesa/Receita: {fmt_pct(tx)} | Limite: {fmt_pct(limite)}",
                        )

        if col_valor and col_meta and col_valor in tabela.columns and col_meta in tabela.columns:
            realizado = pd.to_numeric(tabela[col_valor], errors="coerce").fillna(0).sum()
            meta = pd.to_numeric(tabela[col_meta], errors="coerce").dropna()
            meta = meta[meta != 0].sum() if not meta.empty else 0
            if meta:
                ating = realizado / meta
                if eh_despesa_com_limite(indicador):
                    ok = ating <= 1
                    return ("good" if ok else "warn"), ("Dentro do limite" if ok else "Acima do limite"), f"Uso do limite: {fmt_pct(ating)}"
                return ("good" if ating >= 1 else "warn"), ("Meta atingida" if ating >= 1 else "Abaixo da meta"), f"Atingimento acumulado: {fmt_pct(ating)}"

        return "info", "Em acompanhamento", "Indicador monitorado conforme estrutura selecionada."
    except Exception:
        return "info", "Em acompanhamento", "Status automático indisponível."


def renderizar_capa_premium(indicador, filial, ano, periodo, data_base, df_contexto=None):
    # Status geral removido da capa conforme solicitado.
    # A capa fica apenas com o título, descrição e filtros contextuais.
    st.markdown(
        f"""
        <div class="premium-hero">
            <div class="premium-hero-top">
                <div>
                    <div class="premium-hero-title">Painel executivo</div>
                    <div class="premium-hero-main">{indicador} | {filial}</div>
                    <div class="premium-hero-sub">Leitura consolidada para acompanhamento de metas, tendência e desempenho.</div>
                </div>
            </div>
            <div class="premium-hero-pills">
                <div class="premium-pill">📅 Ano <span>{ano}</span></div>
                <div class="premium-pill">🧭 Período <span>{periodo}</span></div>
                <div class="premium-pill">🏢 Unidade <span>{filial}</span></div>
                <div class="premium-pill">🔄 Base <span>{data_base}</span></div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )



# =========================
# ANÁLISE GEOGRÁFICA — FATURAMENTOS NORMAIS
# =========================

COORDENADAS_FILIAIS = {
    normalizar_texto("VALINHOS/SP"): {"lat": -22.9706, "lon": -46.9958, "label": "Valinhos/SP", "uf": "SP", "estado": "São Paulo"},
    normalizar_texto("VALINHOS"): {"lat": -22.9706, "lon": -46.9958, "label": "Valinhos/SP", "uf": "SP", "estado": "São Paulo"},

    normalizar_texto("CANOAS/RS"): {"lat": -29.9177, "lon": -51.1839, "label": "Canoas/RS", "uf": "RS", "estado": "Rio Grande do Sul"},
    normalizar_texto("CANOAS"): {"lat": -29.9177, "lon": -51.1839, "label": "Canoas/RS", "uf": "RS", "estado": "Rio Grande do Sul"},

    normalizar_texto("CURITIBA/PR"): {"lat": -25.4284, "lon": -49.2733, "label": "Curitiba/PR", "uf": "PR", "estado": "Paraná"},
    normalizar_texto("CURITIBA"): {"lat": -25.4284, "lon": -49.2733, "label": "Curitiba/PR", "uf": "PR", "estado": "Paraná"},

    normalizar_texto("DUQUE DE CAXIAS/RJ"): {"lat": -22.7856, "lon": -43.3117, "label": "Duque de Caxias/RJ", "uf": "RJ", "estado": "Rio de Janeiro"},
    normalizar_texto("DUQUE DE CAXIAS"): {"lat": -22.7856, "lon": -43.3117, "label": "Duque de Caxias/RJ", "uf": "RJ", "estado": "Rio de Janeiro"},
    normalizar_texto("CAXIAS/RJ"): {"lat": -22.7856, "lon": -43.3117, "label": "Duque de Caxias/RJ", "uf": "RJ", "estado": "Rio de Janeiro"},
    normalizar_texto("CAXIAS"): {"lat": -22.7856, "lon": -43.3117, "label": "Duque de Caxias/RJ", "uf": "RJ", "estado": "Rio de Janeiro"},
}


# GeoJSON público com os estados brasileiros.
# Usado apenas para pintar os estados no mapa.
GEOJSON_ESTADOS_BRASIL_URL = "https://raw.githubusercontent.com/codeforamerica/click_that_hood/master/public/data/brazil-states.geojson"

CENTROIDES_ESTADOS_MAPA = {
    "SP": {"lat": -22.8, "lon": -48.3, "nome": "São Paulo"},
    "RS": {"lat": -30.3, "lon": -53.2, "nome": "Rio Grande do Sul"},
    "PR": {"lat": -24.7, "lon": -51.7, "nome": "Paraná"},
    "RJ": {"lat": -22.4, "lon": -42.7, "nome": "Rio de Janeiro"},
}


def eh_faturamento_normal_geografico(indicador):
    """
    Define se o indicador deve exibir a análise geográfica.

    A regra é:
    - precisa ser um indicador de faturamento;
    - não pode ser Tecfil;
    - não pode ser Pneus Velhos Moto, pois essa estrutura é especial;
    - não pode ser despesa, qualidade ou resultado financeiro.
    """
    try:
        if eh_tecfil(indicador) or eh_moto_margem(indicador):
            return False

        if eh_despesa(indicador) or eh_qualidade(indicador) or eh_resultado_financeiro(indicador):
            return False

        cfg = INDICADORES.get(indicador, {})
        categoria = normalizar_texto(str(cfg.get("categoria", "")))
        grupo_01 = normalizar_texto(str(cfg.get("GRUPO 01", "")))
        tipo = normalizar_texto(str(cfg.get("TIPO", "")))

        return categoria == "FATURAMENTO" or grupo_01 == "FATURAMENTO" or "FATURAMENTO" in normalizar_texto(indicador) or tipo == "FATURAMENTOS"
    except Exception:
        return False


def preparar_analise_geografica_faturamento(df_filiais, indicador, ano):
    """
    Consolida o faturamento por filial real e calcula participação no total.
    """
    try:
        if df_filiais is None or df_filiais.empty:
            return pd.DataFrame()

        comp = comparativo_filiais(df_filiais, ano, indicador)

        if comp is None or comp.empty or "FILIAL" not in comp.columns:
            return pd.DataFrame()

        valor_col = "Realizado" if "Realizado" in comp.columns else None
        if valor_col is None:
            return pd.DataFrame()

        geo = comp[["FILIAL", valor_col]].copy()
        geo = geo.rename(columns={valor_col: "Faturamento"})
        geo["Faturamento"] = pd.to_numeric(geo["Faturamento"], errors="coerce").fillna(0)
        geo = geo[geo["Faturamento"] > 0].copy()

        if geo.empty:
            return pd.DataFrame()

        coords = []
        for filial_nome in geo["FILIAL"]:
            chave = normalizar_texto(str(filial_nome))
            coord = COORDENADAS_FILIAIS.get(chave)
            coords.append(coord)

        geo["_coord"] = coords
        geo = geo[geo["_coord"].notna()].copy()

        if geo.empty:
            return pd.DataFrame()

        geo["Latitude"] = geo["_coord"].apply(lambda c: c["lat"])
        geo["Longitude"] = geo["_coord"].apply(lambda c: c["lon"])
        geo["Filial Mapa"] = geo["_coord"].apply(lambda c: c["label"])
        geo["UF"] = geo["_coord"].apply(lambda c: c.get("uf"))
        geo["Estado"] = geo["_coord"].apply(lambda c: c.get("estado"))

        total = geo["Faturamento"].sum()
        geo["Participação"] = geo["Faturamento"] / total if total > 0 else pd.NA

        geo = geo.sort_values("Faturamento", ascending=False).reset_index(drop=True)
        geo["Texto Mapa"] = geo.apply(
            lambda r: f"{r['Filial Mapa']}<br>{fmt_brl(r['Faturamento'])}<br>{fmt_pct(r['Participação'])} do total",
            axis=1
        )
        geo["Texto Marcador"] = geo.apply(
            lambda r: f"{fmt_brl(r['Faturamento'])}<br>{fmt_pct(r['Participação'])}",
            axis=1
        )

        return geo[["Filial Mapa", "UF", "Estado", "Faturamento", "Participação", "Latitude", "Longitude", "Texto Mapa", "Texto Marcador"]]
    except Exception:
        return pd.DataFrame()


def renderizar_analise_geografica_faturamento(df_filiais, indicador, ano):
    """
    Renderiza a seção visual de análise geográfica.
    """
    geo = preparar_analise_geografica_faturamento(df_filiais, indicador, ano)

    if geo is None or geo.empty:
        return

    total_faturamento = geo["Faturamento"].sum()

    titulo_secao(
        "Análise geográfica",
        "Mapa fixo do Brasil com os 4 estados pintados e participação percentual no faturamento total."
    )

    c_geo1, c_geo2, c_geo3 = st.columns([1.2, 1, 1])
    with c_geo1:
        kpi_metric("Faturamento Geral", fmt_brl(total_faturamento))
    with c_geo2:
        maior = geo.iloc[0]
        kpi_metric("Maior participação", f"{maior['Filial Mapa']}", nota=f"{fmt_pct(maior['Participação'])} do total", nota_status="info")
    with c_geo3:
        kpi_metric("Estados no mapa", fmt_num(geo["UF"].nunique()))

    fig = go.Figure()

    estados = (
        geo.groupby(["Estado", "UF"], as_index=False)
        .agg({"Faturamento": "sum"})
        .sort_values("Faturamento", ascending=False)
    )
    estados["Participação"] = estados["Faturamento"] / total_faturamento if total_faturamento > 0 else pd.NA
    estados["lat"] = estados["UF"].map(lambda uf: CENTROIDES_ESTADOS_MAPA.get(uf, {}).get("lat"))
    estados["lon"] = estados["UF"].map(lambda uf: CENTROIDES_ESTADOS_MAPA.get(uf, {}).get("lon"))
    estados["Texto Estado"] = estados.apply(
        lambda r: f"{r['UF']}<br>{fmt_pct(r['Participação'])}",
        axis=1,
    )
    estados["Hover"] = estados.apply(
        lambda r: f"{r['Estado']} ({r['UF']})<br>{fmt_brl(r['Faturamento'])}<br>{fmt_pct(r['Participação'])} do total",
        axis=1,
    )

    fig.add_trace(
        go.Choropleth(
            geojson=GEOJSON_ESTADOS_BRASIL_URL,
            locations=estados["Estado"],
            z=estados["Faturamento"],
            featureidkey="properties.name",
            colorscale=[
                [0.00, "#EAF8F1"],
                [0.45, "#7AD7A4"],
                [1.00, "#00A350"],
            ],
            marker_line_color="#FFFFFF",
            marker_line_width=1.4,
            showscale=False,
            hovertext=estados["Hover"],
            hovertemplate="<b>%{hovertext}</b><extra></extra>",
            name="Estados",
        )
    )

    fig.add_trace(
        go.Scattergeo(
            lon=estados["lon"],
            lat=estados["lat"],
            mode="text",
            text=estados["Texto Estado"],
            textfont=dict(size=12, color="#111827"),
            hoverinfo="skip",
            name="Rótulos",
        )
    )

    fig.update_layout(
        height=560,
        margin=dict(l=0, r=0, t=10, b=0),
        showlegend=False,
        paper_bgcolor="rgba(0,0,0,0)",
        geo=dict(
            scope="south america",
            projection_type="mercator",
            lataxis_range=[-34, 6],
            lonaxis_range=[-75, -33],
            showland=True,
            landcolor="#F8FAFC",
            showcountries=False,
            showsubunits=True,
            subunitcolor="#D1D5DB",
            showocean=True,
            oceancolor="#EEF6F3",
            showlakes=True,
            lakecolor="#EEF6F3",
            showframe=False,
            coastlinecolor="#CBD5E1",
            bgcolor="rgba(0,0,0,0)",
            fitbounds="locations",
        ),
    )

    st.plotly_chart(
        fig,
        use_container_width=True,
        key=f"mapa_geo_faturamento_{indicador}_{ano}",
        config={"displayModeBar": False, "scrollZoom": False, "doubleClick": False},
    )

    filial_por_estado = (
        geo.groupby("Estado")["Filial Mapa"]
        .apply(lambda x: ", ".join(sorted(x.unique())))
        .reset_index()
        .rename(columns={"Filial Mapa": "Filial"})
    )

    ranking_geo = estados[["Estado", "UF", "Faturamento", "Participação"]].rename(columns={
        "Participação": "% do total",
    })
    ranking_geo = filial_por_estado.merge(ranking_geo, on="Estado", how="right")
    ranking_geo = ranking_geo[["Filial", "Estado", "UF", "Faturamento", "% do total"]]

    st.dataframe(
        ranking_geo.style.format({
            "Faturamento": lambda v: fmt_brl(v) if pd.notna(v) else "—",
            "% do total": lambda v: fmt_pct(v) if pd.notna(v) else "—",
        }),
        use_container_width=True,
        hide_index=True,
    )

def preparar_analise_geografica_moto(df_filiais, indicador, ano):
    """Prepara dados geográficos para Pneus Velhos Moto."""
    try:
        if df_filiais is None or df_filiais.empty:
            return pd.DataFrame()

        comp = comparativo_filiais(df_filiais, ano, indicador)
        if comp is None or comp.empty or "FILIAL" not in comp.columns:
            return pd.DataFrame()

        geo = comp[["FILIAL", "Realizado", "Meta", "Atingimento"]].copy()
        geo = geo.rename(columns={"Realizado": "Faturamento", "Atingimento": "Tx. Sucesso"})
        geo["Faturamento"] = pd.to_numeric(geo["Faturamento"], errors="coerce").fillna(0)
        geo = geo[geo["Faturamento"] > 0].copy()

        if geo.empty:
            return pd.DataFrame()

        coords = []
        for filial_nome in geo["FILIAL"]:
            chave = normalizar_texto(str(filial_nome))
            coords.append(COORDENADAS_FILIAIS.get(chave))

        geo["_coord"] = coords
        geo = geo[geo["_coord"].notna()].copy()

        if geo.empty:
            return pd.DataFrame()

        geo["Latitude"]    = geo["_coord"].apply(lambda c: c["lat"])
        geo["Longitude"]   = geo["_coord"].apply(lambda c: c["lon"])
        geo["Filial Mapa"] = geo["_coord"].apply(lambda c: c["label"])
        geo["UF"]          = geo["_coord"].apply(lambda c: c.get("uf"))
        geo["Estado"]      = geo["_coord"].apply(lambda c: c.get("estado"))

        total_fat = geo["Faturamento"].sum()
        geo["Participação"] = geo["Faturamento"] / total_fat if total_fat > 0 else pd.NA
        geo["Texto Mapa"] = geo.apply(
            lambda r: f"{r['Filial Mapa']}<br>Fat: {fmt_brl(r['Faturamento'])}<br>Tx: {fmt_pct(r['Tx. Sucesso'])}",
            axis=1
        )

        return geo.sort_values("Faturamento", ascending=False).reset_index(drop=True)
    except Exception:
        return pd.DataFrame()


def renderizar_analise_geografica_moto(df_filiais, indicador, ano):
    """Renderiza a análise geográfica para Pneus Velhos Moto."""
    geo = preparar_analise_geografica_moto(df_filiais, indicador, ano)

    if geo is None or geo.empty:
        st.info("Sem dados geográficos disponíveis para este indicador.")
        return

    titulo_secao(
        "Análise geográfica",
        "Mapa com as filiais do Pneus Velhos Moto e desempenho por estado."
    )

    # KPIs
    total_fat = geo["Faturamento"].sum()
    total_compra = geo.get("Compra", pd.Series(dtype=float)).sum() if "Compra" in geo.columns else None

    # Meta ponderada por faturamento
    comp = comparativo_filiais(df_filiais, ano, indicador)
    meta_pond = None
    tx_geral = None
    if comp is not None and not comp.empty:
        fat_col = pd.to_numeric(comp.get("Realizado", pd.Series(dtype=float)), errors="coerce").fillna(0)
        meta_col = pd.to_numeric(comp.get("Meta", pd.Series(dtype=float)), errors="coerce")
        mask = fat_col.notna() & meta_col.notna() & (fat_col > 0)
        if mask.any() and fat_col[mask].sum() > 0:
            meta_pond = (fat_col[mask] * meta_col[mask]).sum() / fat_col[mask].sum()
        tx_geral = pd.to_numeric(comp.get("Atingimento", pd.Series(dtype=float)), errors="coerce").mean()

    c1, c2, c3 = st.columns(3)
    with c1:
        kpi_metric("Meta (pond.)", fmt_pct(meta_pond) if meta_pond is not None else "-")
    with c2:
        kpi_metric("Tx. Sucesso Geral", fmt_pct(tx_geral) if tx_geral is not None else "-")
    with c3:
        kpi_metric("Estados no mapa", str(geo["UF"].nunique()))

    # Mapa
    estados = (
        geo.groupby(["Estado", "UF"], as_index=False)
        .agg({"Faturamento": "sum"})
        .sort_values("Faturamento", ascending=False)
    )
    total_faturamento = estados["Faturamento"].sum()
    estados["Participação"] = estados["Faturamento"] / total_faturamento if total_faturamento > 0 else pd.NA
    estados["lat"] = estados["UF"].map(lambda uf: CENTROIDES_ESTADOS_MAPA.get(uf, {}).get("lat"))
    estados["lon"] = estados["UF"].map(lambda uf: CENTROIDES_ESTADOS_MAPA.get(uf, {}).get("lon"))
    estados["Texto Estado"] = estados.apply(
        lambda r: f"{r['UF']}<br>{fmt_pct(r['Participação'])}", axis=1
    )

    fig = go.Figure()
    fig.add_trace(go.Choropleth(
        geojson=GEOJSON_ESTADOS_BRASIL_URL,
        locations=estados["Estado"],
        z=estados["Faturamento"],
        featureidkey="properties.name",
        colorscale=[[0.00, "#EAF8F1"], [0.45, "#7AD7A4"], [1.00, "#00A350"]],
        marker_line_color="#FFFFFF",
        marker_line_width=1.4,
        showscale=False,
        name="Estados",
    ))
    fig.add_trace(go.Scattergeo(
        lon=estados["lon"],
        lat=estados["lat"],
        mode="text",
        text=estados["Texto Estado"],
        textfont=dict(size=12, color="#111827"),
        hoverinfo="skip",
    ))
    fig.update_layout(
        height=560,
        margin=dict(l=0, r=0, t=10, b=0),
        showlegend=False,
        paper_bgcolor="rgba(0,0,0,0)",
        geo=dict(
            scope="south america",
            projection_type="mercator",
            lataxis_range=[-34, 6],
            lonaxis_range=[-75, -33],
            showland=True,
            landcolor="#F8FAFC",
            showcountries=False,
            showsubunits=True,
            subunitcolor="#D1D5DB",
            showocean=True,
            oceancolor="#EEF6F3",
            showframe=False,
            coastlinecolor="#CBD5E1",
            fitbounds="locations",
        ),
    )
    st.plotly_chart(fig, use_container_width=True, key=f"mapa_geo_moto_{indicador}_{ano}",
                    config={"displayModeBar": False})

    # Tabela
    tabela_geo = geo[["Filial Mapa", "Estado", "UF", "Faturamento", "Meta", "Tx. Sucesso"]].copy()
    st.dataframe(
        tabela_geo.style.format({
            "Faturamento": lambda v: fmt_brl(v) if pd.notna(v) else "-",
            "Meta":        lambda v: fmt_pct(v) if pd.notna(v) else "-",
            "Tx. Sucesso": lambda v: fmt_pct(v) if pd.notna(v) else "-",
        }),
        use_container_width=True,
        hide_index=True,
    )


def renderizar_semaforo_projecao(info):
    gap = info.get("gap_projetado")
    media_atual = info.get("media_atual")
    media_necessaria = info.get("media_necessaria")
    col_valor = info.get("col_valor")
    dias_restantes = info.get("dias_restantes")

    if gap is not None and pd.notna(gap) and gap >= 0:
        status = "good"
        icon = "🟢"
        titulo = "Tendência de bater a meta"
        texto = f"No ritmo atual, a projeção indica fechamento acima da meta em {formatar_generico_por_coluna(gap, col_valor)}."
    elif gap is not None and pd.notna(gap):
        status = "warn"
        icon = "🟠"
        falta_ritmo = ""
        if media_atual not in [None, 0] and media_necessaria is not None and pd.notna(media_necessaria):
            try:
                dif = (media_necessaria / media_atual) - 1
                falta_ritmo = f" A média diária necessária está {fmt_pct(dif)} acima da média atual."
            except Exception:
                falta_ritmo = ""
        titulo = "Atenção: precisa acelerar"
        texto = f"No ritmo atual, a projeção indica fechamento abaixo da meta em {formatar_generico_por_coluna(abs(gap), col_valor)}.{falta_ritmo} Dias úteis restantes: {fmt_num(dias_restantes)}."
    else:
        status = "warn"
        icon = "🟠"
        titulo = "Projeção em acompanhamento"
        texto = "Ainda não há informações suficientes para determinar a tendência de fechamento."

    st.markdown(
        f"""
        <div class="semaforo-box {status}">
            <div class="semaforo-icon">{icon}</div>
            <div class="semaforo-content">
                <div class="semaforo-title">{titulo}</div>
                <div class="semaforo-text">{texto}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def renderizar_top_insights(df, indicador, filial, data_geracao_planilha, df_filiais=None):
    try:
        anos = sorted(df["ANO"].dropna().unique())
        ano = int(anos[-1])
        mes = mes_base_por_data(df, ano, data_geracao_planilha)
        mes_nome = MESES_MAPA.get(mes, str(mes))
    except Exception:
        return

    insight1_titulo = "Comparativo anual"
    insight1_texto = "Sem dados suficientes para comparar com o mesmo período anterior."
    insight1_status = "warn"

    try:
        periodo_cmp = comparar_mesmo_periodo(df, indicador, ano)
        if periodo_cmp is not None and not periodo_cmp.empty and len(periodo_cmp) >= 2:
            linha = periodo_cmp.iloc[-1]
            col_var = _primeira_coluna_existente(periodo_cmp, ["Variação", "Variação R$", "Variação Faturamento", "Variação Resultado", "Variação Receita", "Variação %"])
            var = linha.get(col_var) if col_var else None
            insight1_status = "good" if var is not None and pd.notna(var) and var >= 0 else "warn"
            insight1_titulo = "Acima do ano anterior" if insight1_status == "good" else "Abaixo do ano anterior"
            insight1_texto = f"No acumulado Jan a {mes_nome}, a variação contra o ano anterior é {fmt_var_pct_seguro(var) if var is not None else '—'}."
    except Exception:
        pass

    insight2_titulo = "Melhor mês"
    insight2_texto = "Ainda não identificado."
    insight2_status = "good"

    insight3_titulo = "Ponto de atenção"
    insight3_texto = "Nenhum alerta automático relevante identificado."
    insight3_status = "info"

    try:
        tabela = obter_tabela_mensal_ano(df, indicador, ano)
        col, label, modo = valor_principal_para_analise(tabela, indicador)
        if tabela is not None and not tabela.empty and col in tabela.columns:
            base = tabela[(tabela["MÊS"] <= mes)].copy() if "MÊS" in tabela.columns else tabela.copy()
            base["_valor"] = pd.to_numeric(base[col], errors="coerce")
            base = base[base["_valor"].notna()]
            if not base.empty:
                melhor = base.sort_values("_valor", ascending=False).iloc[0]
                pior = base.sort_values("_valor", ascending=True).iloc[0]
                insight2_texto = f"{melhor.get('Mês', '-')} foi o melhor mês, com {formatar_generico_por_coluna(melhor.get('_valor'), col)}."
                insight3_texto = f"{pior.get('Mês', '-')} foi o menor resultado do período, com {formatar_generico_por_coluna(pior.get('_valor'), col)}."
                insight3_status = "warn"
    except Exception:
        pass

    # Ranking se estiver geral
    if filial == "Geral" and df_filiais is not None:
        try:
            ranking = montar_ranking_atingimento(df_filiais, indicador, ano, mes)
            if ranking is not None and not ranking.empty:
                top = ranking.iloc[0]
                insight2_titulo = "Unidade destaque"
                insight2_texto = f"{top.get('FILIAL', '-')} lidera o ranking com {fmt_pct(top.get('Atingimento'))} de atingimento."
        except Exception:
            pass

    st.markdown(
        f"""
        <div class="top-insights-grid">
            <div class="top-insight {insight1_status}">
                <div class="top-insight-label">Insight 1</div>
                <div class="top-insight-title">{insight1_titulo}</div>
                <div class="top-insight-text">{insight1_texto}</div>
            </div>
            <div class="top-insight {insight2_status}">
                <div class="top-insight-label">Insight 2</div>
                <div class="top-insight-title">{insight2_titulo}</div>
                <div class="top-insight-text">{insight2_texto}</div>
            </div>
            <div class="top-insight {insight3_status}">
                <div class="top-insight-label">Insight 3</div>
                <div class="top-insight-title">{insight3_titulo}</div>
                <div class="top-insight-text">{insight3_texto}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def dataframe_premium(styler_ou_df, **kwargs):
    """
    Mantém compatibilidade: centraliza use_container_width/hide_index quando possível.
    Nem todas as tabelas serão convertidas agora, mas a função fica disponível para próximos ajustes.
    """
    kwargs.setdefault("use_container_width", True)
    return st.dataframe(styler_ou_df, **kwargs)




def aplicar_tema_plotly_mazola(fig):
    """
    Tema visual padrão para gráficos Plotly do painel.
    """
    try:
        fig.update_layout(
            plot_bgcolor="#FFFFFF",
            paper_bgcolor="#FFFFFF",
            font=dict(family="Arial", size=12, color="#111827"),
            hoverlabel=dict(
                bgcolor="#FFFFFF",
                font_size=12,
                font_color="#111827",
                bordercolor="#E5E7EB",
            ),
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="center",
                x=0.5,
            ),
            margin=dict(t=70, b=35, l=25, r=25),
            title=dict(font=dict(size=16, color="#111827")),
        )
        fig.update_xaxes(showgrid=False, tickangle=0, linecolor="#E5E7EB")
        fig.update_yaxes(showgrid=True, gridcolor="#EAEAEA", zeroline=False, linecolor="#E5E7EB")
    except Exception:
        pass
    return fig


def grafico_realizado_meta(df_completa, ano, titulo=None, indicador=None):
    if indicador and eh_qualidade(indicador):
        dados = df_completa[(df_completa["Mês"] != "TOTAL") & (df_completa["Resultado %"].notna())].copy()
        if dados.empty:
            return None

        rot = rotulos_qualidade(indicador)
        ultimo_mes = dados["Mês"].iloc[-1]
        titulo_final = titulo or f"{indicador} — {rot['resultado']} x {rot['meta']} — até {ultimo_mes}/{ano}"

        if modelo_qualidade(indicador) == "parametro_coleta_critico":
            cores = [COR_VERDE if pd.notna(v) and v <= 0.10 else COR_LARANJA for v in dados["Resultado %"]]
        else:
            cores = [
                COR_VERDE if pd.notna(r) and pd.notna(m) and r >= m else COR_LARANJA
                for r, m in zip(dados["Resultado %"], dados["Meta"])
            ]

        fig = go.Figure()
        fig.add_bar(
            x=mes_ano_label(dados["Mês"], ano),
            y=dados["Resultado %"],
            name=rot["resultado"],
            marker_color=cores,
            marker_cornerradius=4,
            text=[fmt_pct(v) for v in dados["Resultado %"]],
            textposition="outside",
            textfont=dict(size=11),
        )

        if modelo_qualidade(indicador) != "parametro_coleta_critico":
            fig.add_scatter(
                x=mes_ano_label(dados["Mês"], ano),
                y=dados["Meta"],
                name=rot["meta"],
                mode="lines+markers",
                line=dict(color=COR_AZUL, width=3, dash="dot"),
                marker=dict(size=7),
            )

        fig.update_layout(
            title=titulo_final,
            height=420,
            margin=dict(t=60, b=20, l=20, r=20),
            legend=dict(orientation="h", y=-0.18),
            yaxis_title="%",
            bargap=0.22,
            uniformtext_minsize=8,
            uniformtext_mode="hide",
            yaxis_tickformat=".0%",
        )
        fig.update_yaxes(showgrid=True, gridcolor="#EAEAEA")
        return aplicar_tema_plotly_mazola(fig)

    if indicador and eh_tecfil(indicador):
        dados = df_completa[(df_completa["Mês"] != "TOTAL") & (df_completa["Realizado R$"].notna())].copy()
        if dados.empty:
            return None

        ultimo_mes = dados["Mês"].iloc[-1]
        titulo_final = titulo or f"Tecfil — Realizado R$ x Meta R$ — até {ultimo_mes}/{ano}"

        cores = [
            COR_VERDE if pd.notna(r) and pd.notna(m) and r >= m else COR_LARANJA
            for r, m in zip(dados["Realizado R$"], dados["Meta R$"])
        ]

        texto_barras_tecfil = []
        for realizado_valor, meta_valor in zip(dados["Realizado R$"], dados["Meta R$"]):
            if pd.notna(realizado_valor) and pd.notna(meta_valor) and meta_valor != 0:
                texto_barras_tecfil.append(fmt_pct(realizado_valor / meta_valor))
            else:
                texto_barras_tecfil.append("")

        fig = go.Figure()
        fig.add_bar(
            x=mes_ano_label(dados["Mês"], ano),
            y=dados["Realizado R$"],
            name="Realizado R$",
            marker_color=cores,
            marker_cornerradius=4,
            text=texto_barras_tecfil,
            textposition="inside",
            insidetextanchor="middle",
            textfont=dict(size=12, color="white"),
            cliponaxis=False,
        )
        fig.add_scatter(
            x=mes_ano_label(dados["Mês"], ano),
            y=dados["Meta R$"],
            name="Meta R$",
            mode="lines+markers+text",
            line=dict(color=COR_AZUL, width=3, dash="dot"),
            marker=dict(size=7),
            text=[fmt_brl(v) for v in dados["Meta R$"]],
            textposition="top center",
            textfont=dict(size=10, color=COR_AZUL),
            cliponaxis=False,
        )
        fig.update_layout(
            title=titulo_final,
            height=470,
            margin=dict(t=100, b=35, l=20, r=20),
            legend=dict(orientation="h", y=1.10, x=0.5, xanchor="center"),
            yaxis_title="R$",
            bargap=0.22,
            uniformtext_minsize=8,
            uniformtext_mode="show",
        )
        fig.update_yaxes(showgrid=True, gridcolor="#EAEAEA")
        return aplicar_tema_plotly_mazola(fig)

    if indicador and eh_resultado_financeiro(indicador):
        dados = df_completa[(df_completa["Mês"] != "TOTAL") & (df_completa["Receita"].notna())].copy()
        if dados.empty:
            return None

        ultimo_mes = dados["Mês"].iloc[-1]
        titulo_final = titulo or f"Receita x Despesa — Resultado Financeiro — até {ultimo_mes}/{ano}"

        cores_receita = [
            COR_LARANJA if pd.notna(receita) and pd.notna(despesa) and receita < despesa else COR_VERDE
            for receita, despesa in zip(dados["Receita"], dados["Despesa"])
        ]

        fig = go.Figure()
        fig.add_bar(
            x=mes_ano_label(dados["Mês"], ano),
            y=dados["Receita"],
            name="Receita",
            marker_color=cores_receita,
            marker_cornerradius=4,
            text=[fmt_brl(v) for v in dados["Receita"]],
            textposition="outside",
            textfont=dict(size=11),
        )
        fig.add_scatter(
            x=mes_ano_label(dados["Mês"], ano),
            y=dados["Despesa"],
            name="Despesa",
            mode="lines+markers",
            line=dict(color=COR_LARANJA, width=3),
            marker=dict(size=7),
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
        return aplicar_tema_plotly_mazola(fig)

    if indicador and eh_despesa_geral(indicador):
        dados = df_completa[(df_completa["Mês"] != "TOTAL") & (df_completa["Tx. Sucesso"].notna())].copy()
        if dados.empty:
            return None

        ultimo_mes = dados["Mês"].iloc[-1]
        titulo_final = titulo or f"Despesa/Receita x Meta Limite — até {ultimo_mes}/{ano}"

        cor_barras = [
            COR_VERDE if pd.notna(tx) and pd.notna(limite) and tx <= limite else COR_LARANJA
            for tx, limite in zip(dados["Tx. Sucesso"], dados["Limite %"])
        ]

        fig = go.Figure()
        fig.add_bar(
            x=mes_ano_label(dados["Mês"], ano),
            y=dados["Tx. Sucesso"],
            name="Tx. Sucesso",
            marker_color=cor_barras,
            marker_cornerradius=4,
            text=[fmt_pct(v) for v in dados["Tx. Sucesso"]],
            textposition="inside",
            insidetextanchor="middle",
            textfont=dict(size=12, color="white"),
            cliponaxis=False,
            hovertemplate=(
                "<b>%{x}</b><br>"
                "Tx. Sucesso: %{y:.1%}<br>"
                "Despesa/Receita<extra></extra>"
            ),
        )
        fig.add_scatter(
            x=mes_ano_label(dados["Mês"], ano),
            y=dados["Limite %"],
            name="Meta Limite",
            mode="lines+markers+text",
            line=dict(color=COR_AZUL, width=3, dash="dot"),
            marker=dict(size=7),
            text=[fmt_pct(v) if pd.notna(v) else "" for v in dados["Limite %"]],
            textposition="top center",
            textfont=dict(size=10, color=COR_AZUL),
            cliponaxis=False,
            hovertemplate="<b>%{x}</b><br>Meta Limite: %{y:.1%}<extra></extra>",
        )
        fig.update_layout(
            title=titulo_final,
            height=430,
            margin=dict(t=85, b=35, l=20, r=20),
            legend=dict(orientation="h", y=1.08, x=0.5, xanchor="center"),
            yaxis_title="%",
            bargap=0.24,
            uniformtext_minsize=8,
            uniformtext_mode="show",
        )
        fig.update_yaxes(showgrid=True, gridcolor="#EAEAEA", tickformat=".0%")
        return aplicar_tema_plotly_mazola(fig)

    if indicador and eh_despesa_hora_extra(indicador):
        dados = df_completa[(df_completa["Mês"] != "TOTAL") & (df_completa["Pago em Hora Extra"].notna())].copy()
        if dados.empty:
            return None

        ultimo_mes = dados["Mês"].iloc[-1]
        titulo_final = titulo or f"Pago em Hora Extra x Limite — até {ultimo_mes}/{ano}"
        nome_realizado = "Pago em Hora Extra"
        nome_meta = "Limite"

        cor_barras = [
            COR_VERDE if pd.notna(r) and pd.notna(m) and r <= m else COR_LARANJA
            for r, m in zip(dados["Pago em Hora Extra"], dados["Limite"])
        ]

        fig = go.Figure()
        fig.add_bar(
            x=mes_ano_label(dados["Mês"], ano),
            y=dados["Pago em Hora Extra"],
            name=nome_realizado,
            marker_color=cor_barras,
            marker_cornerradius=4,
            text=[fmt_brl(v) for v in dados["Pago em Hora Extra"]],
            textposition="outside",
            textfont=dict(size=11),
            hovertemplate=f"<b>%{{x}}</b><br>{nome_realizado}: R$ %{{y:,.0f}}<extra></extra>",
        )
        fig.add_scatter(
            x=mes_ano_label(dados["Mês"], ano),
            y=dados["Limite"],
            name=nome_meta,
            mode="lines+markers",
            line=dict(color=COR_AZUL, width=3, dash="dot"),
            marker=dict(size=7),
            hovertemplate=f"<b>%{{x}}</b><br>{nome_meta}: R$ %{{y:,.0f}}<extra></extra>",
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
        return aplicar_tema_plotly_mazola(fig)

    # Bloco genérico do gráfico.
    # Correção: Despesa Manutenção agora usa a tabela:
    # Mês | Limite | Despesa | Result. R$ | Result. % | Acumulado
    # Portanto, nesse caso não existe mais a coluna "Realizado".
    if indicador and eh_despesa_manutencao(indicador) and "Despesa" in df_completa.columns and "Limite" in df_completa.columns:
        dados = df_completa[(df_completa["Mês"] != "TOTAL") & (df_completa["Despesa"].notna())].copy()
        if dados.empty:
            return None

        dados = dados.rename(columns={"Despesa": "Realizado", "Limite": "Meta"})
        ultimo_mes = dados["Mês"].iloc[-1]

        titulo_final = titulo or f"Despesa x Limite — até {ultimo_mes}/{ano}"
        nome_realizado = "Despesa"
        nome_meta = "Limite"

        cor_barras = [
            COR_VERDE if pd.notna(r) and pd.notna(m) and r <= m else COR_LARANJA
            for r, m in zip(dados["Realizado"], dados["Meta"])
        ]

    else:
        if "Realizado" not in df_completa.columns:
            return None

        dados = df_completa[(df_completa["Mês"] != "TOTAL") & (df_completa["Realizado"].notna())].copy()
        if dados.empty:
            return None

        ultimo_mes = dados["Mês"].iloc[-1]

        titulo_final = titulo or f"{rotulo_realizado(indicador)} x {rotulo_meta(indicador)} — até {ultimo_mes}/{ano}"
        nome_realizado = rotulo_realizado(indicador)
        nome_meta = rotulo_meta(indicador)

        if indicador and eh_despesa_manutencao(indicador):
            cor_barras = [
                COR_VERDE if pd.notna(r) and pd.notna(m) and r <= m else COR_LARANJA
                for r, m in zip(dados["Realizado"], dados["Meta"])
            ]
        else:
            cor_barras = [COR_VERDE if r >= m else COR_LARANJA for r, m in zip(dados["Realizado"], dados["Meta"])]

    texto_barras = []
    for realizado_valor, meta_valor in zip(dados["Realizado"], dados["Meta"]):
        if pd.notna(realizado_valor) and pd.notna(meta_valor) and meta_valor != 0:
            texto_barras.append(fmt_pct(realizado_valor / meta_valor))
        else:
            texto_barras.append("")

    fig = go.Figure()
    fig.add_bar(
        x=mes_ano_label(dados["Mês"], ano),
        y=dados["Realizado"],
        name=nome_realizado,
        marker_color=cor_barras,
        marker_cornerradius=4,
        text=texto_barras,
        textposition="inside",
        insidetextanchor="middle",
        textfont=dict(size=12, color="white"),
        cliponaxis=False,
        hovertemplate=f"<b>%{{x}}</b><br>{nome_realizado}: R$ %{{y:,.0f}}<extra></extra>",
    )
    fig.add_scatter(
        x=mes_ano_label(dados["Mês"], ano),
        y=dados["Meta"],
        name=nome_meta,
        mode="lines+markers+text",
        line=dict(color=COR_AZUL, width=3, dash="dot"),
        marker=dict(size=7),
        text=[fmt_brl(v) for v in dados["Meta"]],
        textposition="top center",
        textfont=dict(size=10, color=COR_AZUL),
        cliponaxis=False,
        hovertemplate=f"<b>%{{x}}</b><br>{nome_meta}: R$ %{{y:,.0f}}<extra></extra>",
    )
    fig.update_layout(
        title=titulo_final,
        height=470,
        margin=dict(t=100, b=35, l=20, r=20),
        legend=dict(orientation="h", y=1.10, x=0.5, xanchor="center"),
        yaxis_title="R$",
        bargap=0.22,
        uniformtext_minsize=8,
        uniformtext_mode="show",
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


def kpi_metric(titulo, valor, delta=None, nota=None, nota_status="info"):
    """
    KPI padrão visual Mazola.

    Corrigido para todos os cards ficarem exatamente com o mesmo tamanho.
    Permite uma nota curta no rodapé do card, sem aumentar a altura.
    """
    delta_html = '<div class="kpi-delta empty">0.0%</div>'
    note_html = ""
    classe_extra = ""

    if nota:
        classe_extra = " has-note"
        note_html = f'<div class="kpi-note {nota_status}">{nota}</div>'
    elif delta is not None and pd.notna(delta):
        try:
            delta_float = float(delta)
            sinal = "+" if delta_float >= 0 else ""
            cor = COR_VERDE if delta_float >= 0 else COR_LARANJA
            fundo = "rgba(0,163,80,0.10)" if delta_float >= 0 else "rgba(242,101,34,0.10)"
            delta_html = (
                f'<div class="kpi-delta" style="background:{fundo}; color:{cor};">'
                f'{sinal}{delta_float:.1%}'
                f'</div>'
            )
        except Exception:
            delta_html = '<div class="kpi-delta empty">0.0%</div>'

    st.markdown(
        f"""
        <div class="kpi-card{classe_extra}">
            <div class="kpi-label">{titulo}</div>
            <div class="kpi-value">{valor}</div>
            {delta_html}
            {note_html}
        </div>
        """,
        unsafe_allow_html=True,
    )



def rotulo_realizado_kpi(indicador):
    """
    Rótulo do card de realizado na Visão Geral.

    Para Faturamento Especial, a leitura executiva é melhor como Faturamento,
    não Realizado.
    """
    if normalizar_texto(indicador) == normalizar_texto("Faturamento Especial"):
        return "Faturamento"
    return rotulo_realizado(indicador)


def rotulo_gap_kpi(indicador):
    """
    Rótulo do card de diferença/resultado na Visão Geral.

    Para Faturamento Especial, usar Resultado R$ em vez de Gap.
    """
    if normalizar_texto(indicador) == normalizar_texto("Faturamento Especial"):
        return "Resultado R$"
    return rotulo_gap(indicador)



def rotulo_ytd_kpi(indicador):
    """
    Define o rótulo do card YTD deixando claro o que está sendo analisado.

    Correção:
    Não usa eh_faturamento(), pois essa função não existe no código.
    """
    if eh_moto_margem(indicador):
        return "YTD Fat."
    if eh_tecfil(indicador):
        return "YTD Fat."
    if eh_despesa_geral(indicador):
        return "YTD Desp."
    if eh_despesa_manutencao(indicador):
        return "YTD Desp."
    if eh_despesa_hora_extra(indicador):
        return "YTD HE"
    if eh_resultado_financeiro(indicador):
        return "YTD Resultado"

    try:
        cfg = INDICADORES.get(indicador, {})
        categoria = normalizar_texto(str(cfg.get("categoria", "")))
        grupo_01 = normalizar_texto(str(cfg.get("GRUPO 01", "")))

        if categoria == "FATURAMENTO" or grupo_01 == "FATURAMENTO":
            return "YTD Fat."
    except Exception:
        pass

    return "YTD"


def mes_ano_label(serie_meses, ano):
    """
    Formata o eixo X dos gráficos no padrão solicitado:
    Jan/26, Fev/26, Mar/26...
    """
    sufixo = str(int(ano))[-2:] if ano is not None else ""
    return [f"{str(m)}/{sufixo}" for m in serie_meses]


def titulo_secao(titulo, subtitulo=None):
    """
    Título de seção com visual mais próximo de dashboard executivo.
    """
    subtitulo_html = ""
    if subtitulo:
        subtitulo_html = f'<div class="section-subtitle-v5">{subtitulo}</div>'

    st.markdown(
        f"""
        <div class="section-title-v5">{titulo}</div>
        {subtitulo_html}
        """,
        unsafe_allow_html=True,
    )


def periodo_contexto_por_data_ou_dados(df_base_periodo, ano, data_geracao_planilha="-"):
    """
    Define o período mostrado na Visão Geral.
    Preferência:
    1. Mês da data de geração da planilha, se for do mesmo ano.
    2. Último mês com algum valor relevante diferente de zero.
    3. Último mês encontrado na base.
    """
    try:
        data_base = parse_data_geracao(data_geracao_planilha) if "parse_data_geracao" in globals() else None
        if data_base is not None and int(data_base.year) == int(ano):
            mes = int(data_base.month)
            return f"Jan a {MESES_MAPA.get(mes, mes)}"

        d = df_base_periodo.copy()
        if d is None or d.empty or "MÊS" not in d.columns:
            return "-"

        colunas_valor = [
            "VALOR REF 01", "VALOR REF 02", "META", "REALIZADO_CALC",
            "META_CALC", "DESPESA_CALC", "RECEITA_CALC"
        ]
        colunas_existentes = [c for c in colunas_valor if c in d.columns]

        if colunas_existentes:
            mascara = pd.Series(False, index=d.index)
            for col in colunas_existentes:
                mascara = mascara | (pd.to_numeric(d[col], errors="coerce").fillna(0) != 0)
            d_valida = d[mascara].copy()
            if not d_valida.empty:
                mes = int(d_valida["MÊS"].max())
                return f"Jan a {MESES_MAPA.get(mes, mes)}"

        mes = int(d["MÊS"].max())
        return f"Jan a {MESES_MAPA.get(mes, mes)}"
    except Exception:
        return "-"


def contexto_dashboard(indicador, filial, ano, periodo="-", data_base="-"):
    """
    Bloco de contexto da Visão Geral.
    Mantém indicador, unidade, ano e período à esquerda; data/hora da base à direita.
    """
    st.markdown(
        f"""
        <div class="dashboard-context">
            <div class="context-left">
                <span class="context-pill">📌 Indicador: <strong>{indicador}</strong></span>
                <span class="context-pill">🏢 Unidade: <strong>{filial}</strong></span>
                <span class="context-pill">📅 Ano: <strong>{ano}</strong></span>
                <span class="context-pill">🧭 Período: <strong>{periodo}</strong></span>
            </div>
            <div class="context-update">
                <div class="context-update-label">Base atualizada em</div>
                <div class="context-update-value">{data_base}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

def alerta_executivo_dashboard(titulo, texto, status="warn"):
    """
    Pequeno resumo executivo abaixo dos KPIs.
    status = "good" ou "warn".
    """
    st.markdown(
        f"""
        <div class="exec-alert {status}">
            <div>
                <div class="exec-alert-title">{titulo}</div>
                <div class="exec-alert-text">{texto}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def insight_meta_texto(atingimento=None, gap=None, indicador=None):
    """
    Gera um texto simples de status para indicadores com meta.
    Não altera cálculo, apenas melhora a leitura executiva.
    """
    try:
        if atingimento is None or pd.isna(atingimento):
            return "Status do período", "Ainda não há dados suficientes para avaliar o atingimento da meta.", "warn"

        ating_fmt = fmt_pct(atingimento)

        if eh_despesa_com_limite(indicador):
            if atingimento <= 1:
                saldo = fmt_brl(gap) if gap is not None and pd.notna(gap) else "-"
                return "Dentro do limite", f"O indicador está em {ating_fmt} do limite. Saldo disponível: {saldo}.", "good"
            excesso = fmt_brl(abs(gap)) if gap is not None and pd.notna(gap) else "-"
            return "Atenção ao limite", f"O indicador está em {ating_fmt} do limite. Excesso identificado: {excesso}.", "warn"

        if atingimento >= 1:
            folga = fmt_brl(gap) if gap is not None and pd.notna(gap) else "-"
            return "Meta atingida", f"O indicador atingiu {ating_fmt} da meta. Resultado acima da meta: {folga}.", "good"

        falta = fmt_brl(abs(gap)) if gap is not None and pd.notna(gap) else "-"
        return "Atenção à meta", f"O indicador está em {ating_fmt} da meta. Falta aproximadamente {falta} para atingir o objetivo.", "warn"
    except Exception:
        return "Status do período", "Resumo indisponível para este indicador.", "warn"


def insight_variacao_texto(delta=None, nome="YTD"):
    """
    Texto para indicadores cuja leitura principal é comparação de mesmo período.
    """
    if delta is None or pd.isna(delta):
        return "Comparativo indisponível", f"Não há dados suficientes para calcular a variação {nome}.", "warn"

    if delta >= 0:
        return "Evolução positiva", f"O {nome} está {delta:+.1%} acima do mesmo período anterior.", "good"

    return "Ponto de atenção", f"O {nome} está {delta:+.1%} abaixo do mesmo período anterior.", "warn"


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

        if resumo.get("qualidade"):
            rot = rotulos_qualidade(self.indicador)
            intro = (
                f"Este relatório apresenta a análise do indicador {self.indicador}, considerando a base {self.filial}. "
                f"Os dados abaixo resumem meta/limite, coletas, diferença e resultado percentual."
            )
            self.multi_cell(0, 6, self.safe(intro))
            self.ln(3)

            y_inicial = self.get_y()
            self.kpi_box(12, y_inicial, 45, 20, rot["qtd_total"], fmt_num(resumo["qtd_total_ano"]))
            self.kpi_box(60, y_inicial, 45, 20, rot["qtd_sucesso"], fmt_num(resumo["qtd_sucesso_ano"]))
            self.kpi_box(108, y_inicial, 45, 20, rot["diferenca"], fmt_num(resumo["gap_ano"]))
            self.kpi_box(156, y_inicial, 42, 20, rot["resultado"], fmt_pct(resumo["ating_ano"]))

            y2 = y_inicial + 25
            meta_mes_txt = fmt_num(resumo["meta_mes"]) if modelo_qualidade(self.indicador) == "parametro_coleta_critico" else fmt_pct(resumo["meta_mes"])
            self.kpi_box(12, y2, 45, 20, f"{rot['meta']} {resumo['nome_mes']}", meta_mes_txt)
            self.kpi_box(60, y2, 45, 20, f"Qtd. {resumo['nome_mes']}", fmt_num(resumo["qtd_total_mes"]))
            self.kpi_box(108, y2, 45, 20, f"Sucesso {resumo['nome_mes']}", fmt_num(resumo["qtd_sucesso_mes"]))
            self.kpi_box(156, y2, 42, 20, f"Result. {resumo['nome_mes']}", fmt_pct(resumo["ating_mes"]))

        elif resumo.get("tecfil"):
            intro = (
                f"Este relatório apresenta a análise do indicador {self.indicador}, considerando a base {self.filial}. "
                f"Os dados abaixo resumem metas e realizados Tecfil em KG e R$."
            )
            self.multi_cell(0, 6, self.safe(intro))
            self.ln(3)

            y_inicial = self.get_y()
            self.kpi_box(12, y_inicial, 45, 20, "Realizado R$ Ano", fmt_brl(resumo["realizado_ano"]))
            self.kpi_box(60, y_inicial, 45, 20, "Meta R$ Ano", fmt_brl(resumo["meta_ano"]))
            self.kpi_box(108, y_inicial, 45, 20, "Dif. R$ Ano", fmt_brl(resumo["gap_ano"]))
            self.kpi_box(156, y_inicial, 42, 20, "Dif. KG Ano", fmt_num(resumo["dif_kg_ano"]))

            y2 = y_inicial + 25
            self.kpi_box(12, y2, 45, 20, f"Real KG {resumo['nome_mes']}", fmt_num(resumo["real_kg_mes"]))
            self.kpi_box(60, y2, 45, 20, f"Meta KG {resumo['nome_mes']}", fmt_num(resumo["meta_kg_mes"]))
            self.kpi_box(108, y2, 45, 20, f"Real R$ {resumo['nome_mes']}", fmt_brl(resumo["realizado_mes"]))
            self.kpi_box(156, y2, 42, 20, f"Meta R$ {resumo['nome_mes']}", fmt_brl(resumo["meta_mes"]))

        elif resumo.get("despesa_geral"):
            intro = (
                f"Este relatório apresenta a análise do indicador {self.indicador}, considerando a base {self.filial}. "
                f"Os dados abaixo resumem o desempenho de despesa operacional sobre receita, comparando limite percentual, "
                f"despesa, receita, resultado e taxa de sucesso."
            )
            self.multi_cell(0, 6, self.safe(intro))
            self.ln(3)

            y_inicial = self.get_y()
            self.kpi_box(12, y_inicial, 45, 20, "Despesa Ano", fmt_brl(resumo["realizado_ano"]))
            self.kpi_box(60, y_inicial, 45, 20, "Receita Ano", fmt_brl(resumo["receita_ano"]))
            self.kpi_box(108, y_inicial, 45, 20, "Resultado Ano", fmt_brl(resumo["gap_ano"]))
            self.kpi_box(156, y_inicial, 42, 20, "Tx. Sucesso", fmt_pct(resumo["ating_ano"]))

            y2 = y_inicial + 25
            self.kpi_box(12, y2, 45, 20, f"Despesa {resumo['nome_mes']}", fmt_brl(resumo["realizado_mes"]))
            self.kpi_box(60, y2, 45, 20, f"Receita {resumo['nome_mes']}", fmt_brl(resumo["receita_mes"]))
            self.kpi_box(108, y2, 45, 20, f"Limite {resumo['nome_mes']}", fmt_pct(resumo["meta_mes"]))
            self.kpi_box(156, y2, 42, 20, "Limite R$", fmt_brl(resumo["limite_rs_mes"]))

        elif resumo.get("moto_margem"):
            intro = (
                f"Este relatório apresenta a análise do indicador {self.indicador}, considerando a base {self.filial}. "
                f"Os dados abaixo resumem faturamento, compra, margem bruta, meta de margem e taxa de sucesso."
            )
            self.multi_cell(0, 6, self.safe(intro))
            self.ln(3)

            y_inicial = self.get_y()
            self.kpi_box(12, y_inicial, 45, 20, "Faturamento Ano", fmt_brl(resumo["realizado_ano"]))
            self.kpi_box(60, y_inicial, 45, 20, "Compra Ano", fmt_brl(resumo["compra_ano"]))
            self.kpi_box(108, y_inicial, 45, 20, "Margem Ano", fmt_brl(resumo["margem_ano"]))
            self.kpi_box(156, y_inicial, 42, 20, "Tx. Sucesso", fmt_pct(resumo["ating_ano"]))

            y2 = y_inicial + 25
            self.kpi_box(12, y2, 45, 20, f"Fatur. {resumo['nome_mes']}", fmt_brl(resumo["realizado_mes"]))
            self.kpi_box(60, y2, 45, 20, f"Compra {resumo['nome_mes']}", fmt_brl(resumo["compra_mes"]))
            self.kpi_box(108, y2, 45, 20, f"Margem {resumo['nome_mes']}", fmt_brl(resumo["margem_mes"]))
            self.kpi_box(156, y2, 42, 20, "Meta %", fmt_pct(resumo["meta_mes"]))

        elif eh_despesa_manutencao(self.indicador):
            intro = (
                f"Este relatório apresenta a análise do indicador {self.indicador}, considerando a base {self.filial}. "
                f"Os dados abaixo resumem despesa, limite, saldo do limite e uso do limite."
            )
            self.multi_cell(0, 6, self.safe(intro))
            self.ln(3)

            y_inicial = self.get_y()
            self.kpi_box(12, y_inicial, 45, 20, "Despesa Ano", fmt_brl(resumo["realizado_ano"]))
            self.kpi_box(60, y_inicial, 45, 20, "Limite Ano", fmt_brl(resumo["meta_ano"]))
            self.kpi_box(108, y_inicial, 45, 20, "Saldo Ano", fmt_brl(resumo["gap_ano"]))
            self.kpi_box(156, y_inicial, 42, 20, "Uso do Limite", fmt_pct(resumo["ating_ano"]))

            y2 = y_inicial + 25
            self.kpi_box(12, y2, 45, 20, f"Despesa {resumo['nome_mes']}", fmt_brl(resumo["realizado_mes"]))
            self.kpi_box(60, y2, 45, 20, f"Limite {resumo['nome_mes']}", fmt_brl(resumo["meta_mes"]))
            self.kpi_box(108, y2, 45, 20, "Dias Restantes", str(resumo["dias_restantes"]))
            self.kpi_box(156, y2, 42, 20, "Necessário/dia", fmt_brl(resumo["necessario_dia"]) if resumo["necessario_dia"] is not None else "-")

        else:
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
        self.fonte("B", 8)
        self.set_fill_color(240, 240, 240)



        if "Qtd. Coletas" in df_completa.columns and "Resultado %" in df_completa.columns:
            headers = ["Mês", "Meta", "Qtd.", "Qtd. Suc.", "Dif.", "Result.", "Acum."]
            widths = [24, 22, 28, 28, 24, 24, 28]

            for h, w in zip(headers, widths):
                self.cell(w, 7, self.safe(h), border=1, fill=True, align="C")
            self.ln()

            self.fonte("", 7)
            for _, row in df_completa.iterrows():
                self.cell(widths[0], 6, self.safe(str(row["Mês"])), border=1)
                self.cell(widths[1], 6, fmt_pct(row["Meta"]) if pd.notna(row["Meta"]) and row["Meta"] <= 1 else fmt_num(row["Meta"]), border=1, align="R")
                self.cell(widths[2], 6, fmt_num(row["Qtd. Coletas"]), border=1, align="R")
                qtd_sucesso_pdf = row["Qtd. Sucesso"] if "Qtd. Sucesso" in row.index else row.get("QTD. COLETAS NORMAIS", row.get("QTD.COL. OTIMO+BOM", None))
                self.cell(widths[3], 6, fmt_num(qtd_sucesso_pdf), border=1, align="R")
                self.cell(widths[4], 6, fmt_num(row["Diferença"] if "Diferença" in row.index else row.get("Resultado", None)), border=1, align="R")
                self.cell(widths[5], 6, fmt_pct(row["Resultado %"]), border=1, align="R")
                acumulado_pdf = row["Acumulado"] if "Acumulado" in row.index else None
                self.cell(widths[6], 6, fmt_num(acumulado_pdf), border=1, align="R")
                self.ln()
            return

        if "Meta KG" in df_completa.columns and "Realizado R$" in df_completa.columns:
            headers = ["Mês", "Meta KG", "Real KG", "% KG", "Dif KG", "Meta R$", "Real R$", "% R$", "Dif R$"]
            widths = [20, 22, 22, 18, 22, 28, 28, 18, 28]

            for h, w in zip(headers, widths):
                self.cell(w, 7, self.safe(h), border=1, fill=True, align="C")
            self.ln()

            self.fonte("", 6)
            for _, row in df_completa.iterrows():
                self.cell(widths[0], 6, self.safe(str(row["Mês"])), border=1)
                self.cell(widths[1], 6, fmt_num(row["Meta KG"]), border=1, align="R")
                self.cell(widths[2], 6, fmt_num(row["Realizado KG"]), border=1, align="R")
                self.cell(widths[3], 6, fmt_pct(row["% Dif. KG"]), border=1, align="R")
                self.cell(widths[4], 6, fmt_num(row["Dif. KG"]), border=1, align="R")
                self.cell(widths[5], 6, fmt_brl(row["Meta R$"]), border=1, align="R")
                self.cell(widths[6], 6, fmt_brl(row["Realizado R$"]), border=1, align="R")
                self.cell(widths[7], 6, fmt_pct(row["% Dif. R$"]), border=1, align="R")
                self.cell(widths[8], 6, fmt_brl(row["Dif. R$"]), border=1, align="R")
                self.ln()
            return
        if "Resultado %" in df_completa.columns and "Meta %" in df_completa.columns and "Resultado R$" in df_completa.columns:
            headers = ["Mês", "Meta %", "Desp.", "Receita", "Result.", "Result. %", "Acumul."]
            widths = [22, 24, 30, 30, 30, 22, 30]

            for h, w in zip(headers, widths):
                self.cell(w, 7, self.safe(h), border=1, fill=True, align="C")
            self.ln()

            for _, row in df_completa.iterrows():
                is_total = row["Mês"] == "TOTAL"
                fill = is_total
                if is_total:
                    self.set_fill_color(255, 243, 232)
                    self.fonte("B", 7)
                else:
                    self.set_fill_color(255, 255, 255)
                    self.fonte("", 7)

                self.cell(widths[0], 6, self.safe(str(row["Mês"])), border=1, align="C", fill=fill)
                self.cell(widths[1], 6, fmt_pct(row["Meta %"]), border=1, align="R", fill=fill)
                self.cell(widths[2], 6, fmt_brl(row["Despesa"]), border=1, align="R", fill=fill)
                self.cell(widths[3], 6, fmt_brl(row["Receita"]), border=1, align="R", fill=fill)
                self.cell(widths[4], 6, fmt_brl(row["Resultado R$"]), border=1, align="R", fill=fill)
                self.cell(widths[5], 6, fmt_pct(row["Resultado %"]), border=1, align="R", fill=fill)
                self.cell(widths[6], 6, fmt_brl(row["Acumulado"]), border=1, align="R", fill=fill)
                self.ln()
            return

        if "Despesa" in df_completa.columns and "Receita" in df_completa.columns and "Resultado R$" in df_completa.columns:
            headers = ["Mês", "Limite %", "Desp.", "Receita", "Result.", "Tx.", "Acumul."]
            widths = [22, 24, 30, 30, 30, 22, 30]

            for h, w in zip(headers, widths):
                self.cell(w, 7, self.safe(h), border=1, fill=True, align="C")
            self.ln()

            for _, row in df_completa.iterrows():
                is_total = row["Mês"] == "TOTAL"
                fill = is_total
                if is_total:
                    self.set_fill_color(255, 243, 232)
                    self.fonte("B", 7)
                else:
                    self.set_fill_color(255, 255, 255)
                    self.fonte("", 7)

                self.cell(widths[0], 6, self.safe(str(row["Mês"])), border=1, align="C", fill=fill)
                self.cell(widths[1], 6, fmt_pct(row["Limite %"]), border=1, align="R", fill=fill)
                self.cell(widths[2], 6, fmt_brl(row["Despesa"]), border=1, align="R", fill=fill)
                self.cell(widths[3], 6, fmt_brl(row["Receita"]), border=1, align="R", fill=fill)
                self.cell(widths[4], 6, fmt_brl(row["Resultado R$"]), border=1, align="R", fill=fill)
                self.cell(widths[5], 6, fmt_pct(row["Tx. Sucesso"]), border=1, align="R", fill=fill)
                self.cell(widths[6], 6, fmt_brl(row["Acumulado"]), border=1, align="R", fill=fill)
                self.ln()
            return

        if "Pago em Hora Extra" in df_completa.columns:
            headers = ["Mês", "Limite", "Pago HE", "Salário", "HE Folha", "Saldo", "Resultado %"]
            widths = [22, 26, 30, 30, 25, 28, 28]

            for h, w in zip(headers, widths):
                self.cell(w, 7, self.safe(h), border=1, fill=True, align="C")
            self.ln()

            for _, row in df_completa.iterrows():
                is_total = row["Mês"] == "TOTAL"
                fill = is_total
                if is_total:
                    self.set_fill_color(255, 243, 232)
                    self.fonte("B", 7)
                else:
                    self.set_fill_color(255, 255, 255)
                    self.fonte("", 7)

                self.cell(widths[0], 6, self.safe(str(row["Mês"])), border=1, align="C", fill=fill)
                self.cell(widths[1], 6, fmt_brl(row["Limite"]), border=1, align="R", fill=fill)
                self.cell(widths[2], 6, fmt_brl(row["Pago em Hora Extra"]), border=1, align="R", fill=fill)
                self.cell(widths[3], 6, fmt_brl(row["Salário"]), border=1, align="R", fill=fill)
                self.cell(widths[4], 6, fmt_pct(row["HE da Folha"]), border=1, align="R", fill=fill)
                self.cell(widths[5], 6, fmt_brl(row["Saldo do Limite"]), border=1, align="R", fill=fill)
                self.cell(widths[6], 6, fmt_pct(row["Resultado %"]), border=1, align="R", fill=fill)
                self.ln()
            return

        if "Faturamento" in df_completa.columns and "Margem Bruta" in df_completa.columns:
            headers = ["Mês", "Meta %", "Compra", "Fatur.", "Margem", "Tx.", "Acumul."]
            widths = [22, 24, 27, 30, 30, 22, 30]

            for h, w in zip(headers, widths):
                self.cell(w, 7, self.safe(h), border=1, fill=True, align="C")
            self.ln()

            for _, row in df_completa.iterrows():
                is_total = row["Mês"] == "TOTAL"
                fill = is_total
                if is_total:
                    self.set_fill_color(255, 243, 232)
                    self.fonte("B", 7)
                else:
                    self.set_fill_color(255, 255, 255)
                    self.fonte("", 7)

                self.cell(widths[0], 6, self.safe(str(row["Mês"])), border=1, align="C", fill=fill)
                self.cell(widths[1], 6, fmt_pct(row["Meta"]), border=1, align="R", fill=fill)
                self.cell(widths[2], 6, fmt_brl(row["Compra"]), border=1, align="R", fill=fill)
                self.cell(widths[3], 6, fmt_brl(row["Faturamento"]), border=1, align="R", fill=fill)
                self.cell(widths[4], 6, fmt_brl(row["Margem Bruta"]), border=1, align="R", fill=fill)
                self.cell(widths[5], 6, fmt_pct(row["Tx. Sucesso"]), border=1, align="R", fill=fill)
                self.cell(widths[6], 6, fmt_brl(row["Acumulado"]), border=1, align="R", fill=fill)
                self.ln()
            return

        if eh_despesa_manutencao(self.indicador):
            headers = ["Mês", "Limite", "Despesa", "Result. R$", "Result. %", "Acumul."]
            widths = [26, 32, 32, 34, 26, 34]

            for h, w in zip(headers, widths):
                self.cell(w, 7, self.safe(h), border=1, fill=True, align="C")
            self.ln()

            self.fonte("", 7)
            for _, row in df_completa.iterrows():
                is_total = row["Mês"] == "TOTAL"
                fill = is_total
                if is_total:
                    self.set_fill_color(255, 243, 232)
                    self.fonte("B", 7)
                else:
                    self.set_fill_color(255, 255, 255)
                    self.fonte("", 7)

                self.cell(widths[0], 6, self.safe(str(row["Mês"])), border=1, align="C", fill=fill)
                self.cell(widths[1], 6, fmt_brl(row.get("Limite")), border=1, align="R", fill=fill)
                self.cell(widths[2], 6, fmt_brl(row.get("Despesa")), border=1, align="R", fill=fill)
                self.cell(widths[3], 6, fmt_brl(row.get("Result. R$")), border=1, align="R", fill=fill)
                self.cell(widths[4], 6, fmt_pct(row.get("Result. %")), border=1, align="R", fill=fill)
                self.cell(widths[5], 6, fmt_brl(row.get("Acumulado")), border=1, align="R", fill=fill)
                self.ln()
            return

        if "Compra" in df_completa.columns and "Margem Bruta" in df_completa.columns:
            headers = ["Mês", "Meta %", "Compra", "Faturamento", "Tx. Sucesso", "Acumulado"]
            widths  = [25, 22, 35, 35, 28, 35]

            for h, w in zip(headers, widths):
                self.cell(w, 7, self.safe(h), border=1, fill=True, align="C")
            self.ln()

            self.fonte("", 8)
            for _, row in df_completa.iterrows():
                is_total = str(row.get("Mês", "")).upper() == "TOTAL"
                fill = is_total
                if is_total:
                    self.set_fill_color(255, 243, 232)
                    self.fonte("B", 8)
                else:
                    self.set_fill_color(255, 255, 255)
                    self.fonte("", 8)

                meta_val = row.get("Meta")
                tx_val   = row.get("Tx. Sucesso")
                acum_val = row.get("Acumulado")

                self.cell(widths[0], 6, self.safe(str(row.get("Mês", ""))), border=1, align="C", fill=fill)
                self.cell(widths[1], 6, fmt_pct(meta_val) if pd.notna(meta_val) else "-", border=1, align="R", fill=fill)
                self.cell(widths[2], 6, fmt_brl(row.get("Compra")), border=1, align="R", fill=fill)
                self.cell(widths[3], 6, fmt_brl(row.get("Faturamento")), border=1, align="R", fill=fill)
                self.cell(widths[4], 6, fmt_pct(tx_val) if pd.notna(tx_val) else "-", border=1, align="R", fill=fill)
                self.cell(widths[5], 6, fmt_brl(acum_val) if pd.notna(acum_val) else "-", border=1, align="R", fill=fill)
                self.ln()
            return

        if "Faturamento" in df_completa.columns and "Atendimento da Meta" in df_completa.columns:
            headers = ["Mês", "Meta", "Faturamento", "Atend. Meta"]
            widths = [30, 42, 48, 38]

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
                self.cell(widths[1], 6, fmt_brl(row["Meta"]), border=1, align="R", fill=fill)
                self.cell(widths[2], 6, fmt_brl(row["Faturamento"]), border=1, align="R", fill=fill)
                self.cell(widths[3], 6, fmt_pct(row["Atendimento da Meta"]), border=1, align="R", fill=fill)
                self.ln()
            return

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
        self.fonte("B", 7)
        self.set_fill_color(240, 240, 240)



        if "Qtd. Coletas" in df_mom.columns and "Resultado %" in df_mom.columns:
            headers = ["Mês", "Meta", "Qtd.", "Qtd. Suc.", "Dif.", "Result.", "Acum."]
            widths = [24, 22, 28, 28, 24, 24, 28]

            for h, w in zip(headers, widths):
                self.cell(w, 7, self.safe(h), border=1, fill=True, align="C")
            self.ln()

            self.fonte("", 7)
            for _, row in df_mom.iterrows():
                self.cell(widths[0], 6, self.safe(str(row["Mês"])), border=1)
                self.cell(widths[1], 6, fmt_pct(row["Meta"]) if pd.notna(row["Meta"]) and row["Meta"] <= 1 else fmt_num(row["Meta"]), border=1, align="R")
                self.cell(widths[2], 6, fmt_num(row["Qtd. Coletas"]), border=1, align="R")
                self.cell(widths[3], 6, fmt_num(row["Qtd. Sucesso"]), border=1, align="R")
                self.cell(widths[4], 6, fmt_num(row["Diferença"] if "Diferença" in row.index else row.get("Resultado", None)), border=1, align="R")
                self.cell(widths[5], 6, fmt_pct(row["Resultado %"]), border=1, align="R")
                self.cell(widths[6], 6, fmt_num(row["Acumulado"]), border=1, align="R")
                self.ln()
            return

        if "Meta KG" in df_mom.columns and "Realizado R$" in df_mom.columns:
            headers = ["Mês", "Meta KG", "Real KG", "% KG", "Dif KG", "Meta R$", "Real R$", "% R$", "Dif R$"]
            widths = [20, 22, 22, 18, 22, 28, 28, 18, 28]

            for h, w in zip(headers, widths):
                self.cell(w, 7, self.safe(h), border=1, fill=True, align="C")
            self.ln()

            self.fonte("", 6)
            for _, row in df_mom.iterrows():
                self.cell(widths[0], 6, self.safe(str(row["Mês"])), border=1)
                self.cell(widths[1], 6, fmt_num(row["Meta KG"]), border=1, align="R")
                self.cell(widths[2], 6, fmt_num(row["Realizado KG"]), border=1, align="R")
                self.cell(widths[3], 6, fmt_pct(row["% Dif. KG"]), border=1, align="R")
                self.cell(widths[4], 6, fmt_num(row["Dif. KG"]), border=1, align="R")
                self.cell(widths[5], 6, fmt_brl(row["Meta R$"]), border=1, align="R")
                self.cell(widths[6], 6, fmt_brl(row["Realizado R$"]), border=1, align="R")
                self.cell(widths[7], 6, fmt_pct(row["% Dif. R$"]), border=1, align="R")
                self.cell(widths[8], 6, fmt_brl(row["Dif. R$"]), border=1, align="R")
                self.ln()
            return
        if "Resultado %" in df_mom.columns and "Meta %" in df_mom.columns and "Resultado R$" in df_mom.columns:
            headers = ["Mês", "Meta %", "Desp.", "Receita", "Result.", "Result. %", "Acum."]
            widths = [28, 22, 28, 30, 30, 24, 28]

            for h, w in zip(headers, widths):
                self.cell(w, 7, self.safe(h), border=1, fill=True, align="C")
            self.ln()

            self.fonte("", 7)
            for _, row in df_mom.iterrows():
                self.cell(widths[0], 6, self.safe(str(row["Mês"])), border=1)
                self.cell(widths[1], 6, fmt_pct(row["Meta %"]), border=1, align="R")
                self.cell(widths[2], 6, fmt_brl(row["Despesa"]), border=1, align="R")
                self.cell(widths[3], 6, fmt_brl(row["Receita"]), border=1, align="R")
                self.cell(widths[4], 6, fmt_brl(row["Resultado R$"]), border=1, align="R")
                self.cell(widths[5], 6, fmt_pct(row["Resultado %"]), border=1, align="R")
                self.cell(widths[6], 6, fmt_brl(row["Acumulado"]), border=1, align="R")
                self.ln()
            return

        if "Despesa" in df_mom.columns and "Receita" in df_mom.columns and "Resultado R$" in df_mom.columns:
            headers = ["Mês", "Limite %", "Desp.", "Receita", "Result.", "Tx.", "Acum."]
            widths = [28, 22, 28, 30, 30, 22, 30]

            for h, w in zip(headers, widths):
                self.cell(w, 7, self.safe(h), border=1, fill=True, align="C")
            self.ln()

            self.fonte("", 7)
            for _, row in df_mom.iterrows():
                self.cell(widths[0], 6, self.safe(str(row["Mês"])), border=1)
                self.cell(widths[1], 6, fmt_pct(row["Limite %"]), border=1, align="R")
                self.cell(widths[2], 6, fmt_brl(row["Despesa"]), border=1, align="R")
                self.cell(widths[3], 6, fmt_brl(row["Receita"]), border=1, align="R")
                self.cell(widths[4], 6, fmt_brl(row["Resultado R$"]), border=1, align="R")
                self.cell(widths[5], 6, fmt_pct(row["Tx. Sucesso"]), border=1, align="R")
                self.cell(widths[6], 6, fmt_brl(row["Acumulado"]), border=1, align="R")
                self.ln()
            return

        if "Pago em Hora Extra" in df_mom.columns:
            headers = ["Mês", "Limite", "Pago HE", "Salário", "HE Folha", "Saldo", "Res. %"]
            widths = [28, 26, 30, 30, 24, 28, 24]

            for h, w in zip(headers, widths):
                self.cell(w, 7, self.safe(h), border=1, fill=True, align="C")
            self.ln()

            self.fonte("", 7)
            for _, row in df_mom.iterrows():
                self.cell(widths[0], 6, self.safe(str(row["Mês"])), border=1)
                self.cell(widths[1], 6, fmt_brl(row["Limite"]), border=1, align="R")
                self.cell(widths[2], 6, fmt_brl(row["Pago em Hora Extra"]), border=1, align="R")
                self.cell(widths[3], 6, fmt_brl(row["Salário"]), border=1, align="R")
                self.cell(widths[4], 6, fmt_pct(row["HE da Folha"]), border=1, align="R")
                self.cell(widths[5], 6, fmt_brl(row["Saldo do Limite"]), border=1, align="R")
                self.cell(widths[6], 6, fmt_pct(row["Resultado %"]), border=1, align="R")
                self.ln()
            return

        if "Margem Bruta" in df_mom.columns:
            headers = ["Mês", "Meta %", "Compra", "Fatur.", "Margem", "Tx.", "Acumul."]
            widths = [28, 22, 27, 30, 30, 22, 30]

            for h, w in zip(headers, widths):
                self.cell(w, 7, self.safe(h), border=1, fill=True, align="C")
            self.ln()

            self.fonte("", 7)
            for _, row in df_mom.iterrows():
                self.cell(widths[0], 6, self.safe(str(row["Mês"])), border=1)
                self.cell(widths[1], 6, fmt_pct(row["META"]), border=1, align="R")
                self.cell(widths[2], 6, fmt_brl(row["Compra"]), border=1, align="R")
                self.cell(widths[3], 6, fmt_brl(row["Realizado"]), border=1, align="R")
                self.cell(widths[4], 6, fmt_brl(row["Margem Bruta"]), border=1, align="R")
                self.cell(widths[5], 6, fmt_pct(row["Ating."]), border=1, align="R")
                self.cell(widths[6], 6, fmt_brl(row["Acumulado"]), border=1, align="R")
                self.ln()
            return

        headers = ["Mês", "Realizado", "Meta", "Gap", "Ating.", "MoM %"]
        widths = [36, 32, 32, 32, 24, 24]

        for h, w in zip(headers, widths):
            self.cell(w, 7, self.safe(h), border=1, fill=True, align="C")
        self.ln()

        self.fonte("", 8)
        for _, row in df_mom.iterrows():
            self.cell(widths[0], 6, self.safe(str(row["Mês"])), border=1)
            self.cell(widths[1], 6, fmt_brl(row["Realizado"]), border=1, align="R")
            self.cell(widths[2], 6, fmt_brl(row["META"]), border=1, align="R")
            self.cell(widths[3], 6, fmt_brl(row["Gap"]), border=1, align="R")
            self.cell(widths[4], 6, fmt_pct(row["Ating."]), border=1, align="R")
            mom_val = row["MoM_%"] if "MoM_%" in row.index else None
            self.cell(widths[5], 6, fmt_mom_seguro(mom_val), border=1, align="R")
            self.ln()

    def tabela_yoy(self, df_yoy):
        self.fonte("B", 7)
        self.set_fill_color(240, 240, 240)



        if "Qtd. Coletas" in df_yoy.columns and "Resultado %" in df_yoy.columns:
            headers = ["Ano", "Meta", "Qtd.", "Qtd. Suc.", "Dif.", "Result.", "Meses"]
            widths = [18, 22, 30, 30, 26, 26, 20]

            for h, w in zip(headers, widths):
                self.cell(w, 7, self.safe(h), border=1, fill=True, align="C")
            self.ln()

            self.fonte("", 7)
            for _, row in df_yoy.iterrows():
                self.cell(widths[0], 6, str(int(row["ANO"])), border=1, align="C")
                self.cell(widths[1], 6, fmt_pct(row["Meta"]) if pd.notna(row["Meta"]) and row["Meta"] <= 1 else fmt_num(row["Meta"]), border=1, align="R")
                self.cell(widths[2], 6, fmt_num(row["Qtd. Coletas"]), border=1, align="R")
                self.cell(widths[3], 6, fmt_num(row["Qtd. Sucesso"]), border=1, align="R")
                self.cell(widths[4], 6, fmt_num(row["Diferença"] if "Diferença" in row.index else row.get("Resultado", None)), border=1, align="R")
                self.cell(widths[5], 6, fmt_pct(row["Resultado %"]), border=1, align="R")
                self.cell(widths[6], 6, str(int(row["Meses c/ dado"])), border=1, align="C")
                self.ln()
            return

        if "Meta KG" in df_yoy.columns and "Realizado R$" in df_yoy.columns:
            headers = ["Ano", "Meta KG", "Real KG", "% KG", "Dif KG", "Meta R$", "Real R$", "% R$", "Dif R$"]
            widths = [16, 22, 22, 18, 22, 28, 28, 18, 28]

            for h, w in zip(headers, widths):
                self.cell(w, 7, self.safe(h), border=1, fill=True, align="C")
            self.ln()

            self.fonte("", 6)
            for _, row in df_yoy.iterrows():
                self.cell(widths[0], 6, str(int(row["ANO"])), border=1, align="C")
                self.cell(widths[1], 6, fmt_num(row["Meta KG"]), border=1, align="R")
                self.cell(widths[2], 6, fmt_num(row["Realizado KG"]), border=1, align="R")
                self.cell(widths[3], 6, fmt_pct(row["% Dif. KG"]), border=1, align="R")
                self.cell(widths[4], 6, fmt_num(row["Dif. KG"]), border=1, align="R")
                self.cell(widths[5], 6, fmt_brl(row["Meta R$"]), border=1, align="R")
                self.cell(widths[6], 6, fmt_brl(row["Realizado R$"]), border=1, align="R")
                self.cell(widths[7], 6, fmt_pct(row["% Dif. R$"]), border=1, align="R")
                self.cell(widths[8], 6, fmt_brl(row["Dif. R$"]), border=1, align="R")
                self.ln()
            return

        if "Resultado %" in df_yoy.columns and "Meta %" in df_yoy.columns and "Resultado R$" in df_yoy.columns:
            headers = ["Ano", "Meta %", "Desp.", "Receita", "Result.", "Result. %", "Meses"]
            widths = [18, 24, 30, 30, 30, 24, 20]

            for h, w in zip(headers, widths):
                self.cell(w, 7, self.safe(h), border=1, fill=True, align="C")
            self.ln()

            self.fonte("", 7)
            for _, row in df_yoy.iterrows():
                self.cell(widths[0], 6, str(int(row["ANO"])), border=1, align="C")
                self.cell(widths[1], 6, fmt_pct(row["Meta %"]), border=1, align="R")
                self.cell(widths[2], 6, fmt_brl(row["Despesa"]), border=1, align="R")
                self.cell(widths[3], 6, fmt_brl(row["Receita"]), border=1, align="R")
                self.cell(widths[4], 6, fmt_brl(row["Resultado R$"]), border=1, align="R")
                self.cell(widths[5], 6, fmt_pct(row["Resultado %"]), border=1, align="R")
                self.cell(widths[6], 6, str(int(row["Meses c/ dado"])), border=1, align="C")
                self.ln()
            return

        if "Despesa" in df_yoy.columns and "Receita" in df_yoy.columns and "Resultado R$" in df_yoy.columns:
            headers = ["Ano", "Limite %", "Desp.", "Receita", "Result.", "Tx.", "Meses"]
            widths = [18, 24, 30, 30, 30, 22, 20]

            for h, w in zip(headers, widths):
                self.cell(w, 7, self.safe(h), border=1, fill=True, align="C")
            self.ln()

            self.fonte("", 7)
            for _, row in df_yoy.iterrows():
                self.cell(widths[0], 6, str(int(row["ANO"])), border=1, align="C")
                self.cell(widths[1], 6, fmt_pct(row["Limite %"]), border=1, align="R")
                self.cell(widths[2], 6, fmt_brl(row["Despesa"]), border=1, align="R")
                self.cell(widths[3], 6, fmt_brl(row["Receita"]), border=1, align="R")
                self.cell(widths[4], 6, fmt_brl(row["Resultado R$"]), border=1, align="R")
                self.cell(widths[5], 6, fmt_pct(row["Tx. Sucesso"]), border=1, align="R")
                self.cell(widths[6], 6, str(int(row["Meses c/ dado"])), border=1, align="C")
                self.ln()
            return

        if "Margem Bruta" in df_yoy.columns:
            headers = ["Ano", "Fatur.", "Compra", "Margem", "Meta %", "Meta R$", "Tx."]
            widths = [18, 30, 30, 30, 22, 30, 22]

            for h, w in zip(headers, widths):
                self.cell(w, 7, self.safe(h), border=1, fill=True, align="C")
            self.ln()

            self.fonte("", 7)
            for _, row in df_yoy.iterrows():
                self.cell(widths[0], 6, str(int(row["ANO"])), border=1, align="C")
                self.cell(widths[1], 6, fmt_brl(row["Faturamento"]), border=1, align="R")
                self.cell(widths[2], 6, fmt_brl(row["Compra"]), border=1, align="R")
                self.cell(widths[3], 6, fmt_brl(row["Margem Bruta"]), border=1, align="R")
                self.cell(widths[4], 6, fmt_pct(row["Meta %"]), border=1, align="R")
                self.cell(widths[5], 6, fmt_brl(row["Meta Margem (R$)"]), border=1, align="R")
                self.cell(widths[6], 6, fmt_pct(row["Tx. Sucesso"]), border=1, align="R")
                self.ln()
            return

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
        self.fonte("B", 7)
        self.set_fill_color(240, 240, 240)

        # Resultado Financeiro precisa vir antes de Despesa Geral,
        # porque os dois possuem Despesa, Receita e Resultado R$.


        if "Qtd. Coletas" in df_periodo.columns and "Resultado %" in df_periodo.columns:
            headers = ["Ano", "Período", "Meta", "Qtd.", "Qtd. Suc.", "Dif.", "Result."]
            widths = [14, 22, 22, 30, 30, 24, 24]

            for h, w in zip(headers, widths):
                self.cell(w, 7, self.safe(h), border=1, fill=True, align="C")
            self.ln()

            self.fonte("", 7)
            for _, row in df_periodo.iterrows():
                self.cell(widths[0], 6, str(int(row["Ano"])), border=1, align="C")
                self.cell(widths[1], 6, self.safe(str(row["Período"])), border=1, align="C")
                self.cell(widths[2], 6, fmt_pct(row["Meta"]) if pd.notna(row["Meta"]) and row["Meta"] <= 1 else fmt_num(row["Meta"]), border=1, align="R")
                self.cell(widths[3], 6, fmt_num(row["Qtd. Coletas"]), border=1, align="R")
                self.cell(widths[4], 6, fmt_num(row["Qtd. Sucesso"]), border=1, align="R")
                self.cell(widths[5], 6, fmt_num(row["Diferença"] if "Diferença" in row.index else row.get("Resultado", None)), border=1, align="R")
                self.cell(widths[6], 6, fmt_pct(row["Resultado %"]), border=1, align="R")
                self.ln()
            return

        if "Meta KG" in df_periodo.columns and "Realizado R$" in df_periodo.columns:
            headers = ["Ano", "Período", "Meta KG", "Real KG", "Dif KG", "Meta R$", "Real R$", "Dif R$"]
            widths = [14, 22, 22, 22, 22, 30, 30, 30]

            for h, w in zip(headers, widths):
                self.cell(w, 7, self.safe(h), border=1, fill=True, align="C")
            self.ln()

            self.fonte("", 6)
            for _, row in df_periodo.iterrows():
                self.cell(widths[0], 6, str(int(row["Ano"])), border=1, align="C")
                self.cell(widths[1], 6, self.safe(str(row["Período"])), border=1, align="C")
                self.cell(widths[2], 6, fmt_num(row["Meta KG"]), border=1, align="R")
                self.cell(widths[3], 6, fmt_num(row["Realizado KG"]), border=1, align="R")
                self.cell(widths[4], 6, fmt_num(row["Dif. KG"]), border=1, align="R")
                self.cell(widths[5], 6, fmt_brl(row["Meta R$"]), border=1, align="R")
                self.cell(widths[6], 6, fmt_brl(row["Realizado R$"]), border=1, align="R")
                self.cell(widths[7], 6, fmt_brl(row["Dif. R$"]), border=1, align="R")
                self.ln()
            return

        if "Resultado %" in df_periodo.columns and "Meta %" in df_periodo.columns and "Resultado R$" in df_periodo.columns:
            headers = ["Ano", "Período", "Meta %", "Desp.", "Receita", "Result.", "Result. %"]
            widths = [14, 22, 22, 30, 30, 30, 22]

            for h, w in zip(headers, widths):
                self.cell(w, 7, self.safe(h), border=1, fill=True, align="C")
            self.ln()

            self.fonte("", 7)
            for _, row in df_periodo.iterrows():
                self.cell(widths[0], 6, str(int(row["Ano"])), border=1, align="C")
                self.cell(widths[1], 6, self.safe(str(row["Período"])), border=1, align="C")
                self.cell(widths[2], 6, fmt_pct(row["Meta %"]), border=1, align="R")
                self.cell(widths[3], 6, fmt_brl(row["Despesa"]), border=1, align="R")
                self.cell(widths[4], 6, fmt_brl(row["Receita"]), border=1, align="R")
                self.cell(widths[5], 6, fmt_brl(row["Resultado R$"]), border=1, align="R")
                self.cell(widths[6], 6, fmt_pct(row["Resultado %"]), border=1, align="R")
                self.ln()
            return

        if "Despesa" in df_periodo.columns and "Receita" in df_periodo.columns and "Resultado R$" in df_periodo.columns:
            headers = ["Ano", "Período", "Limite %", "Desp.", "Receita", "Result.", "Tx."]
            widths = [14, 22, 22, 30, 30, 30, 22]

            for h, w in zip(headers, widths):
                self.cell(w, 7, self.safe(h), border=1, fill=True, align="C")
            self.ln()

            self.fonte("", 7)
            for _, row in df_periodo.iterrows():
                self.cell(widths[0], 6, str(int(row["Ano"])), border=1, align="C")
                self.cell(widths[1], 6, self.safe(str(row["Período"])), border=1, align="C")
                self.cell(widths[2], 6, fmt_pct(row["Limite %"]), border=1, align="R")
                self.cell(widths[3], 6, fmt_brl(row["Despesa"]), border=1, align="R")
                self.cell(widths[4], 6, fmt_brl(row["Receita"]), border=1, align="R")
                self.cell(widths[5], 6, fmt_brl(row["Resultado R$"]), border=1, align="R")
                self.cell(widths[6], 6, fmt_pct(row["Tx. Sucesso"]), border=1, align="R")
                self.ln()
            return

        if "Margem Bruta" in df_periodo.columns:
            headers = ["Ano", "Período", "Fatur.", "Compra", "Margem", "Meta %", "Tx.", "Var. Fat."]
            widths = [14, 22, 28, 28, 28, 20, 20, 25]

            for h, w in zip(headers, widths):
                self.cell(w, 7, self.safe(h), border=1, fill=True, align="C")
            self.ln()

            self.fonte("", 7)
            for _, row in df_periodo.iterrows():
                self.cell(widths[0], 6, str(int(row["Ano"])), border=1, align="C")
                self.cell(widths[1], 6, self.safe(str(row["Período"])), border=1, align="C")
                self.cell(widths[2], 6, fmt_brl(row["Faturamento"]), border=1, align="R")
                self.cell(widths[3], 6, fmt_brl(row["Compra"]), border=1, align="R")
                self.cell(widths[4], 6, fmt_brl(row["Margem Bruta"]), border=1, align="R")
                self.cell(widths[5], 6, fmt_pct(row["Meta %"]), border=1, align="R")
                self.cell(widths[6], 6, fmt_pct(row["Tx. Sucesso"]), border=1, align="R")
                self.cell(widths[7], 6, fmt_pct(row["Variação Faturamento"]) if pd.notna(row["Variação Faturamento"]) else "-", border=1, align="R")
                self.ln()
            return

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
        self.fonte("B", 7)
        self.set_fill_color(240, 240, 240)

        # Resultado Financeiro antes de Despesa Geral.


        if "Qtd. Coletas" in df_filiais.columns and "Resultado %" in df_filiais.columns:
            headers = ["Filial", "Meta", "Qtd.", "Qtd. Suc.", "Dif.", "Result."]
            widths = [45, 22, 30, 30, 26, 26]

            for h, w in zip(headers, widths):
                self.cell(w, 7, self.safe(h), border=1, fill=True, align="C")
            self.ln()

            self.fonte("", 7)
            for _, row in df_filiais.iterrows():
                self.cell(widths[0], 6, self.safe(str(row["FILIAL"])), border=1)
                self.cell(widths[1], 6, fmt_pct(row["Meta"]) if pd.notna(row["Meta"]) and row["Meta"] <= 1 else fmt_num(row["Meta"]), border=1, align="R")
                self.cell(widths[2], 6, fmt_num(row["Qtd. Coletas"]), border=1, align="R")
                self.cell(widths[3], 6, fmt_num(row["Qtd. Sucesso"]), border=1, align="R")
                self.cell(widths[4], 6, fmt_num(row["Diferença"] if "Diferença" in row.index else row.get("Resultado", None)), border=1, align="R")
                self.cell(widths[5], 6, fmt_pct(row["Resultado %"]), border=1, align="R")
                self.ln()
            return

        if "Meta KG" in df_filiais.columns and "Realizado R$" in df_filiais.columns:
            headers = ["Filial", "Meta KG", "Real KG", "Dif KG", "Meta R$", "Real R$", "Dif R$"]
            widths = [38, 22, 22, 22, 30, 30, 30]

            for h, w in zip(headers, widths):
                self.cell(w, 7, self.safe(h), border=1, fill=True, align="C")
            self.ln()

            self.fonte("", 6)
            for _, row in df_filiais.iterrows():
                self.cell(widths[0], 6, self.safe(str(row["FILIAL"])), border=1)
                self.cell(widths[1], 6, fmt_num(row["Meta KG"]), border=1, align="R")
                self.cell(widths[2], 6, fmt_num(row["Realizado KG"]), border=1, align="R")
                self.cell(widths[3], 6, fmt_num(row["Dif. KG"]), border=1, align="R")
                self.cell(widths[4], 6, fmt_brl(row["Meta R$"]), border=1, align="R")
                self.cell(widths[5], 6, fmt_brl(row["Realizado R$"]), border=1, align="R")
                self.cell(widths[6], 6, fmt_brl(row["Dif. R$"]), border=1, align="R")
                self.ln()
            return

        if "Resultado %" in df_filiais.columns and "Meta %" in df_filiais.columns and "Resultado R$" in df_filiais.columns:
            headers = ["Filial", "Meta %", "Desp.", "Receita", "Result.", "Result. %"]
            widths = [45, 22, 30, 30, 30, 22]

            for h, w in zip(headers, widths):
                self.cell(w, 7, self.safe(h), border=1, fill=True, align="C")
            self.ln()

            self.fonte("", 7)
            for _, row in df_filiais.iterrows():
                self.cell(widths[0], 6, self.safe(str(row["FILIAL"])), border=1)
                self.cell(widths[1], 6, fmt_pct(row["Meta %"]), border=1, align="R")
                self.cell(widths[2], 6, fmt_brl(row["Despesa"]), border=1, align="R")
                self.cell(widths[3], 6, fmt_brl(row["Receita"]), border=1, align="R")
                self.cell(widths[4], 6, fmt_brl(row["Resultado R$"]), border=1, align="R")
                self.cell(widths[5], 6, fmt_pct(row["Resultado %"]), border=1, align="R")
                self.ln()
            return

        if "Despesa" in df_filiais.columns and "Receita" in df_filiais.columns and "Resultado R$" in df_filiais.columns:
            headers = ["Filial", "Limite %", "Desp.", "Receita", "Result.", "Tx."]
            widths = [45, 22, 30, 30, 30, 22]

            for h, w in zip(headers, widths):
                self.cell(w, 7, self.safe(h), border=1, fill=True, align="C")
            self.ln()

            self.fonte("", 7)
            for _, row in df_filiais.iterrows():
                self.cell(widths[0], 6, self.safe(str(row["FILIAL"])), border=1)
                self.cell(widths[1], 6, fmt_pct(row["Limite %"]), border=1, align="R")
                self.cell(widths[2], 6, fmt_brl(row["Despesa"]), border=1, align="R")
                self.cell(widths[3], 6, fmt_brl(row["Receita"]), border=1, align="R")
                self.cell(widths[4], 6, fmt_brl(row["Resultado R$"]), border=1, align="R")
                self.cell(widths[5], 6, fmt_pct(row["Tx. Sucesso"]), border=1, align="R")
                self.ln()
            return

        if "Margem Bruta" in df_filiais.columns:
            headers = ["Filial", "Fatur.", "Compra", "Margem", "Meta %", "Tx."]
            widths = [45, 30, 30, 30, 22, 22]

            for h, w in zip(headers, widths):
                self.cell(w, 7, self.safe(h), border=1, fill=True, align="C")
            self.ln()

            self.fonte("", 7)
            for _, row in df_filiais.iterrows():
                self.cell(widths[0], 6, self.safe(str(row["FILIAL"])), border=1)
                self.cell(widths[1], 6, fmt_brl(row["Faturamento"]), border=1, align="R")
                self.cell(widths[2], 6, fmt_brl(row["Compra"]), border=1, align="R")
                self.cell(widths[3], 6, fmt_brl(row["Margem Bruta"]), border=1, align="R")
                self.cell(widths[4], 6, fmt_pct(row["Meta %"]), border=1, align="R")
                self.cell(widths[5], 6, fmt_pct(row["Tx. Sucesso"]), border=1, align="R")
                self.ln()
            return

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


def gerar_imagem_mapa_pdf(df_filiais, indicador, ano):
    """Gera o mapa geográfico de faturamento como imagem PNG para uso no PDF."""
    try:
        import io
        geo = preparar_analise_geografica_faturamento(df_filiais, indicador, ano)
        if geo is None or geo.empty:
            return None, None

        total_faturamento = geo["Faturamento"].sum()

        estados = (
            geo.groupby(["Estado", "UF"], as_index=False)
            .agg({"Faturamento": "sum"})
            .sort_values("Faturamento", ascending=False)
        )
        estados["Participação"] = estados["Faturamento"] / total_faturamento if total_faturamento > 0 else pd.NA
        estados["lat"] = estados["UF"].map(lambda uf: CENTROIDES_ESTADOS_MAPA.get(uf, {}).get("lat"))
        estados["lon"] = estados["UF"].map(lambda uf: CENTROIDES_ESTADOS_MAPA.get(uf, {}).get("lon"))
        estados["Texto Estado"] = estados.apply(
            lambda r: f"{r['UF']} {fmt_pct(r['Participação'])}", axis=1
        )

        fig = go.Figure()
        fig.add_trace(go.Choropleth(
            geojson=GEOJSON_ESTADOS_BRASIL_URL,
            locations=estados["Estado"],
            z=estados["Faturamento"],
            featureidkey="properties.name",
            colorscale=[[0.00, "#EAF8F1"], [0.45, "#7AD7A4"], [1.00, "#00A350"]],
            marker_line_color="#FFFFFF",
            marker_line_width=1.4,
            showscale=False,
            name="Estados",
        ))
        fig.add_trace(go.Scattergeo(
            lon=estados["lon"],
            lat=estados["lat"],
            mode="text",
            text=estados["Texto Estado"],
            textfont=dict(size=11, color="#111827"),
            hoverinfo="skip",
        ))
        fig.update_layout(
            height=420, width=700,
            margin=dict(l=0, r=0, t=10, b=0),
            showlegend=False,
            paper_bgcolor="white",
            geo=dict(
                scope="south america",
                projection_type="mercator",
                lataxis_range=[-34, 6],
                lonaxis_range=[-75, -33],
                showland=True,
                landcolor="#F8FAFC",
                showcountries=False,
                showsubunits=True,
                subunitcolor="#D1D5DB",
                showocean=True,
                oceancolor="#EEF6F3",
                showframe=False,
                coastlinecolor="#CBD5E1",
                fitbounds="locations",
            ),
        )

        img_bytes = fig.to_image(format="png", width=700, height=420, scale=3)

        # Monta tabela de ranking
        filial_por_estado = (
            geo.groupby("Estado")["Filial Mapa"]
            .apply(lambda x: ", ".join(sorted(x.unique())))
            .reset_index()
            .rename(columns={"Filial Mapa": "Filial"})
        )
        ranking = estados[["Estado", "UF", "Faturamento", "Participação"]].copy()
        ranking = filial_por_estado.merge(ranking, on="Estado", how="right")
        ranking = ranking[["Filial", "Estado", "UF", "Faturamento", "Participação"]]

        return io.BytesIO(img_bytes), ranking

    except Exception:
        return None, None


def gerar_pdf(df, df_todas, indicador, filial, ano_selecionado, df_filiais_comp=None):
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
    tabela_pdf = tabela_pneus_moto_ano(df, ano_selecionado) if eh_moto_margem(indicador) else tabela_completa_ano(df, ano_selecionado, indicador)
    if eh_faturamento_simples(indicador):
        mask_total = tabela_pdf["Mês"].astype(str).str.upper() == "TOTAL"
        mask_dados = pd.to_numeric(tabela_pdf["Realizado"], errors="coerce").fillna(0) > 0
        tabela_pdf = tabela_pdf[mask_total | mask_dados].copy()
        tabela_pdf = tabela_pdf.rename(columns={
            "Realizado": "Faturamento",
            "Atingimento": "Atendimento da Meta",
        })
    pdf.tabela_por_ano(tabela_pdf)

    pdf.add_page()
    pdf.secao("4. Variação Mês a Mês - Últimos 12 meses")
    df_mom = calcular_mom(df, indicador).sort_values("MÊS_ORDEM").tail(12).copy()
    if eh_moto_margem(indicador):
        pdf.tabela_mom(df_mom[["Mês", "META", "Compra", "Realizado", "Margem Bruta", "Gap", "Ating.", "Acumulado"]])
    elif eh_qualidade(indicador):
        # No PDF, manter os nomes internos esperados pelo método tabela_mom.
        # Não renomear "Qtd. Sucesso", pois tabela_mom usa row["Qtd. Sucesso"].
        df_mom_pdf_q = df_mom.copy()

        # Para Parâmetro de Coleta, o Acumulado foi removido da tela,
        # mas o método PDF ainda usa a estrutura de 7 colunas.
        # Então criamos a coluna vazia apenas para o PDF não quebrar.
        if "Acumulado" not in df_mom_pdf_q.columns:
            df_mom_pdf_q["Acumulado"] = pd.NA

        cols_pdf_q = ["Mês", "Meta", "Qtd. Coletas", "Qtd. Sucesso", "Diferença", "Resultado %", "Acumulado"]
        cols_pdf_q = [c for c in cols_pdf_q if c in df_mom_pdf_q.columns]
        pdf.tabela_mom(df_mom_pdf_q[cols_pdf_q])
    elif eh_tecfil(indicador):
        pdf.tabela_mom(df_mom[["Mês", "Meta KG", "Meta R$", "Realizado KG", "Realizado R$", "% Dif. KG", "% Dif. R$", "Dif. KG", "Dif. R$", "Acum. KG", "Acum. R$"]])
    elif eh_resultado_financeiro(indicador):
        pdf.tabela_mom(df_mom[["Mês", "Meta %", "Despesa", "Receita", "Resultado R$", "Resultado %", "Acumulado"]])
    elif eh_despesa_geral(indicador):
        pdf.tabela_mom(df_mom[["Mês", "Limite %", "Despesa", "Receita", "Resultado R$", "Tx. Sucesso", "Acumulado"]])
    elif eh_despesa_hora_extra(indicador):
        pdf.tabela_mom(df_mom[["Mês", "Limite", "Pago em Hora Extra", "Salário", "HE da Folha", "Saldo do Limite", "Resultado %"]])
    elif eh_faturamento_simples(indicador):
        pdf.tabela_mom(df_mom[["Mês", "Realizado", "META", "Gap", "Ating.", "MoM_%"]])
    else:
        pdf.tabela_mom(df_mom[["Mês", "Realizado", "META", "Gap", "Ating."]])

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
        base_filiais = df_filiais_comp if (df_filiais_comp is not None and not df_filiais_comp.empty) else df_todas
        df_filiais = comparativo_filiais(base_filiais, ano_selecionado, indicador)

        if eh_faturamento_simples(indicador):
            img_buf, ranking_geo = gerar_imagem_mapa_pdf(base_filiais, indicador, ano_selecionado)
            if img_buf is not None:
                page_w = pdf.w - pdf.l_margin - pdf.r_margin
                img_h = page_w * 420 / 700
                pdf.image(img_buf, x=pdf.l_margin, y=pdf.get_y(), w=page_w, h=img_h)
                pdf.ln(img_h + 4)
            if ranking_geo is not None and not ranking_geo.empty:
                pdf.fonte("B", 8)
                pdf.set_fill_color(240, 240, 240)
                headers_geo = ["Filial", "Estado", "UF", "Faturamento", "% do Total"]
                widths_geo  = [50, 48, 14, 40, 30]
                for h, w in zip(headers_geo, widths_geo):
                    pdf.cell(w, 7, pdf.safe(h), border=1, fill=True, align="C")
                pdf.ln()
                pdf.fonte("", 8)
                for _, row in ranking_geo.iterrows():
                    pdf.cell(widths_geo[0], 6, pdf.safe(str(row.get("Filial", ""))), border=1)
                    pdf.cell(widths_geo[1], 6, pdf.safe(str(row.get("Estado", ""))), border=1)
                    pdf.cell(widths_geo[2], 6, pdf.safe(str(row.get("UF", ""))), border=1, align="C")
                    pdf.cell(widths_geo[3], 6, fmt_brl(row.get("Faturamento")), border=1, align="R")
                    pdf.cell(widths_geo[4], 6, fmt_pct(row.get("Participação")), border=1, align="R")
                    pdf.ln()
            elif not df_filiais.empty:
                pdf.tabela_filiais(df_filiais)
            else:
                pdf.fonte("", 10)
                pdf.multi_cell(0, 6, pdf.safe("Sem dados geograficos disponiveis."))
        elif not df_filiais.empty:
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
1. Resumo
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

    if eh_qualidade(indicador):
        st.warning("Nenhum dado encontrado para os filtros selecionados.")
        st.caption("Para Qualidade, confira se existem linhas com TIPO = QUALIDADE, os grupos informados, REFERÊNCIA válida, META, VALOR REF 01 e VALOR REF 02.")
        st.stop()

    if eh_tecfil(indicador):
        st.warning("Nenhum dado encontrado para os filtros selecionados.")
        st.caption("Para Tecfil, confira se a planilha possui: coluna B = TECFIL KG/TECFIL VALOR, coluna C = base/estado, coluna F = data, coluna H = meta e coluna J = realizado.")
        st.stop()

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
    border:1px solid #E5E7EB;
    border-radius:16px;
    padding:16px 16px 32px 16px;
    background:#FFFFFF;
    height:118px;
    min-height:118px;
    max-height:118px;
    box-sizing:border-box;
    box-shadow:0 4px 14px rgba(17,24,39,0.07);
    overflow:hidden;
}

.kpi-card::before {
    content:"";
    position:absolute;
    left:0;
    top:0;
    height:5px;
    width:100%;
    border-radius:16px 16px 0 0;
    background:linear-gradient(90deg, #F26522 0%, #00A350 100%);
}

.kpi-label {
    min-height:28px;
    max-height:28px;
    font-size:11px;
    color:#6B7280;
    margin-bottom:6px;
    font-weight:850;
    text-transform:uppercase;
    letter-spacing:.02em;
    line-height:1.15;
    overflow:hidden;
    display:-webkit-box;
    -webkit-line-clamp:2;
    -webkit-box-orient:vertical;
}

.kpi-value {
    min-height:34px;
    display:flex;
    align-items:center;
    font-size:clamp(17px, 1.35vw, 22px);
    font-weight:900;
    color:#111827;
    line-height:1.05;
    white-space:nowrap;
    overflow:hidden;
    text-overflow:ellipsis;
    font-variant-numeric: tabular-nums;
}

.kpi-delta {
    position:absolute;
    left:16px;
    bottom:10px;
    display:inline-flex;
    align-items:center;
    justify-content:center;
    padding:4px 9px;
    border-radius:999px;
    font-size:11px;
    font-weight:850;
    width:fit-content;
    min-height:20px;
}

.kpi-delta.empty {
    visibility:hidden;
}

.kpi-card:hover {
    border-color:rgba(242,101,34,0.35);
    box-shadow:0 6px 18px rgba(17,24,39,0.10);
}

div[data-testid="stDataFrame"] {
    border-radius: 10px;
}

.main .block-container {
    padding-top: 1.2rem;
}

/* Cards st.metric — Dashboard v5.0 */
div[data-testid="stMetric"] {
    background: #FFFFFF;
    border: 1px solid #E5E7EB;
    border-radius: 12px;
    padding: 10px 14px;
    min-height: 88px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.06);
    position: relative;
    overflow: hidden;
}

div[data-testid="stMetric"]::before {
    content: "";
    position: absolute;
    left: 0;
    top: 0;
    height: 4px;
    width: 100%;
    background: #F26522;
}

div[data-testid="stMetricLabel"] {
    font-size: 11px;
    color: #404040;
    white-space: nowrap;
}

div[data-testid="stMetricValue"] {
    font-size: 18px;
    font-weight: 700;
    color: #111827;
    white-space: nowrap;
}

div[data-testid="stMetricDelta"] {
    font-size: 11px;
}


/* Refinamento executivo v5.0 etapa 3 */
.dashboard-context {
    display:flex;
    align-items:center;
    justify-content:space-between;
    gap:14px;
    width:100%;
    padding:12px 16px;
    margin:2px 0 18px 0;
    border-radius:14px;
    background: linear-gradient(90deg, rgba(242,101,34,0.08), rgba(0,163,80,0.06));
    border:1px solid #F1F1F1;
}

.context-left {
    display:flex;
    flex-wrap:wrap;
    align-items:center;
    gap:8px;
}

.context-pill {
    display:inline-flex;
    align-items:center;
    gap:6px;
    padding:6px 10px;
    border-radius:999px;
    background:#FFFFFF;
    border:1px solid #E5E7EB;
    color:#111827;
    font-size:12px;
    font-weight:600;
    white-space:nowrap;
}

.context-pill strong {
    color:#F26522;
}

.context-update {
    text-align:right;
    min-width:185px;
}

.context-update-label {
    font-size:11px;
    color:#6B7280;
    font-weight:600;
    margin-bottom:2px;
    white-space:nowrap;
}

.context-update-value {
    font-size:14px;
    color:#111827;
    font-weight:800;
    white-space:nowrap;
}

.exec-alert {
    display:flex;
    align-items:flex-start;
    justify-content:space-between;
    gap:12px;
    padding:12px 15px;
    border-radius:14px;
    margin:14px 0 16px 0;
    background:#FFFFFF;
    border-left:5px solid #F26522;
    box-shadow:0 1px 5px rgba(0,0,0,0.06);
}

.exec-alert.good {
    border-left-color:#00A350;
    background:rgba(0,163,80,0.06);
}

.exec-alert.warn {
    border-left-color:#F26522;
    background:rgba(242,101,34,0.07);
}

.exec-alert-title {
    font-size:13px;
    font-weight:800;
    color:#111827;
    margin-bottom:2px;
}

.exec-alert-text {
    font-size:12px;
    color:#4B5563;
    line-height:1.35;
}

.section-title-v5 {
    display:flex;
    align-items:center;
    gap:8px;
    margin:18px 0 8px 0;
    color:#111827;
    font-size:18px;
    font-weight:800;
}

.section-title-v5::before {
    content:"";
    display:inline-block;
    width:5px;
    height:22px;
    border-radius:999px;
    background:#F26522;
}

.section-subtitle-v5 {
    margin:-4px 0 10px 13px;
    color:#6B7280;
    font-size:12px;
}

div[data-testid="stMetric"] {
    transition: transform .12s ease, box-shadow .12s ease;
}

div[data-testid="stMetric"]:hover {
    transform: translateY(-1px);
    box-shadow: 0 4px 12px rgba(0,0,0,0.08);
}

div[data-testid="stMetric"] [data-testid="stMetricLabel"] p {
    font-weight:700;
}

div[data-testid="stMetricValue"] {
    color:#111827;
}

div[data-testid="stMetricDelta"] svg {
    display:none;
}

hr {
    margin: 1.1rem 0;
}


/* Ajuste do bloco de contexto com data à direita */
.dashboard-context {
    align-items:center;
}

.context-left {
    flex: 1;
}

.context-update {
    text-align:right;
    min-width:190px;
    padding-left:14px;
    border-left:1px solid rgba(17,24,39,0.08);
}

.context-update-label {
    font-size:10.5px;
    color:#6B7280;
    font-weight:700;
    margin-bottom:2px;
    white-space:nowrap;
}

.context-update-value {
    font-size:13.5px;
    color:#111827;
    font-weight:800;
    white-space:nowrap;
}

div[data-baseweb="tab-list"] {
    background:#FFFFFF;
    border-radius:12px;
    padding:4px 6px 0 6px;
}


/* Projeção e ranking executivo */
.gold-card-grid {
    display:grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap:14px;
    margin: 10px 0 18px 0;
}

.gold-card {
    background:#FFFFFF;
    border:1px solid #E5E7EB;
    border-radius:16px;
    padding:14px 16px;
    box-shadow:0 1px 5px rgba(0,0,0,0.06);
    position:relative;
    overflow:hidden;
}

.gold-card::before {
    content:"";
    position:absolute;
    left:0;
    top:0;
    width:100%;
    height:4px;
    background:#F26522;
}

.gold-card.good::before { background:#00A350; }
.gold-card.warn::before { background:#F26522; }
.gold-card.info::before { background:#0078D4; }

.gold-label {
    font-size:12px;
    color:#6B7280;
    font-weight:700;
    margin-bottom:6px;
}

.gold-value {
    font-size:20px;
    color:#111827;
    font-weight:850;
    line-height:1.1;
    white-space:nowrap;
}

.gold-note {
    font-size:12px;
    color:#4B5563;
    margin-top:6px;
    line-height:1.35;
}

.ranking-title {
    font-size:15px;
    font-weight:850;
    margin: 8px 0 8px 0;
    color:#111827;
}

@media (max-width: 900px) {
    .gold-card-grid {
        grid-template-columns: 1fr;
    }
}


/* Acabamento visual etapa 7 */
div[data-testid="stDataFrame"] {
    border: 1px solid #E5E7EB;
    border-radius: 14px;
    box-shadow: 0 1px 6px rgba(0,0,0,0.05);
    overflow: hidden;
    background: #FFFFFF;
}

div[data-testid="stSelectbox"] label p {
    font-weight: 700 !important;
    color: #374151 !important;
    font-size: 12px !important;
}

div[data-testid="stSelectbox"] > div {
    border-radius: 12px !important;
}

.section-wrapper-note {
    color:#6B7280;
    font-size:12px;
    margin:-4px 0 12px 13px;
}

.compare-card-grid {
    display:grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 12px;
    margin: 8px 0 14px 0;
}

.compare-mini-card {
    background:#FFFFFF;
    border:1px solid #E5E7EB;
    border-radius:16px;
    padding:16px 16px 14px 16px;
    box-shadow:0 4px 14px rgba(17,24,39,0.07);
    position:relative;
    overflow:hidden;
    min-height:112px;
    box-sizing:border-box;
}

.compare-mini-card::before {
    content:"";
    position:absolute;
    top:0;
    left:0;
    height:5px;
    width:100%;
    background:linear-gradient(90deg, #F26522 0%, #00A350 100%);
}

.compare-mini-card.good::before { background:#00A350; }
.compare-mini-card.warn::before { background:#F26522; }

.compare-label {
    min-height:24px;
    font-size:11px;
    color:#6B7280;
    font-weight:850;
    margin-bottom:8px;
    text-transform:uppercase;
    letter-spacing:.02em;
    line-height:1.15;
}

.compare-value {
    font-size:clamp(17px, 1.35vw, 22px);
    color:#111827;
    font-weight:900;
    white-space:nowrap;
    line-height:1.05;
    font-variant-numeric: tabular-nums;
}

.compare-note {
    font-size:12px;
    color:#4B5563;
    margin-top:10px;
    line-height:1.35;
    font-weight:600;
}

@media (max-width: 900px) {
    .compare-card-grid {
        grid-template-columns: 1fr;
    }
}


/* Sidebar executiva — etapa 8 */
section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #F7F7F7 0%, #FFFFFF 100%);
}

.sidebar-section-title {
    display:flex;
    align-items:center;
    gap:8px;
    font-size:13px;
    font-weight:850;
    color:#111827;
    margin: 14px 0 8px 0;
    padding: 0;
}

.sidebar-section-title::before {
    content:"";
    display:inline-block;
    width:4px;
    height:18px;
    border-radius:999px;
    background:#F26522;
}

.sidebar-info-card {
    background:#FFFFFF;
    border:1px solid #E5E7EB;
    border-radius:14px;
    padding:11px 12px;
    margin: 8px 0 14px 0;
    box-shadow:0 1px 5px rgba(0,0,0,0.05);
}

.sidebar-info-title {
    font-size:11px;
    color:#6B7280;
    font-weight:700;
    margin-bottom:6px;
}

.sidebar-info-value {
    font-size:13px;
    color:#111827;
    font-weight:800;
    line-height:1.25;
}

.sidebar-chip-list {
    display:flex;
    flex-direction:column;
    gap:6px;
    margin-top:8px;
}

.sidebar-chip {
    display:flex;
    align-items:center;
    justify-content:space-between;
    gap:8px;
    background:#FFFFFF;
    border:1px solid #E5E7EB;
    border-radius:999px;
    padding:6px 9px;
}

.sidebar-chip-label {
    font-size:10.5px;
    color:#6B7280;
    font-weight:700;
    white-space:nowrap;
}

.sidebar-chip-value {
    font-size:11.5px;
    color:#F26522;
    font-weight:800;
    text-align:right;
    overflow:hidden;
    text-overflow:ellipsis;
    white-space:nowrap;
    max-width:150px;
}

.sidebar-user-card {
    background: linear-gradient(90deg, rgba(242,101,34,0.08), rgba(0,163,80,0.06));
    border:1px solid #E5E7EB;
    border-radius:14px;
    padding:12px;
    margin: 12px 0;
}

.sidebar-user-name {
    font-size:13px;
    font-weight:850;
    color:#111827;
    margin-bottom:2px;
}

.sidebar-user-profile {
    font-size:11px;
    color:#4B5563;
    font-weight:650;
}

.sidebar-small-note {
    font-size:11px;
    color:#6B7280;
    line-height:1.35;
    margin: 4px 0 8px 0;
}

section[data-testid="stSidebar"] div[data-testid="stSelectbox"] {
    margin-bottom: 8px;
}

section[data-testid="stSidebar"] div[data-testid="stSelectbox"] label p {
    font-size: 11.5px !important;
    font-weight: 800 !important;
    color: #374151 !important;
}

section[data-testid="stSidebar"] button {
    border-radius: 12px !important;
}


/* Pacote visual completo — etapa 9 */
.premium-hero {
    width:100%;
    border-radius:18px;
    padding:18px 20px;
    margin: 4px 0 18px 0;
    background:
        radial-gradient(circle at 0% 0%, rgba(242,101,34,0.14), transparent 32%),
        radial-gradient(circle at 100% 20%, rgba(0,163,80,0.12), transparent 30%),
        linear-gradient(90deg, #FFFFFF 0%, #FAFAFA 100%);
    border:1px solid #E5E7EB;
    box-shadow:0 2px 10px rgba(0,0,0,0.06);
}

.premium-hero-top {
    display:flex;
    align-items:flex-start;
    justify-content:space-between;
    gap:18px;
}

.premium-hero-title {
    font-size:13px;
    color:#6B7280;
    font-weight:800;
    margin-bottom:4px;
    text-transform:uppercase;
    letter-spacing:.03em;
}

.premium-hero-main {
    font-size:25px;
    color:#111827;
    font-weight:900;
    line-height:1.1;
    margin:0;
}

.premium-hero-sub {
    margin-top:6px;
    color:#4B5563;
    font-size:13px;
    font-weight:600;
}

.premium-status {
    min-width:210px;
    text-align:right;
    padding:10px 12px;
    border-radius:14px;
    background:#FFFFFF;
    border:1px solid #E5E7EB;
    box-shadow:0 1px 5px rgba(0,0,0,0.04);
}

.premium-status-label {
    font-size:11px;
    color:#6B7280;
    font-weight:800;
    margin-bottom:3px;
}

.premium-status-value {
    font-size:15px;
    font-weight:900;
}

.premium-status.good .premium-status-value { color:#00A350; }
.premium-status.warn .premium-status-value { color:#F26522; }
.premium-status.info .premium-status-value { color:#0078D4; }

.premium-hero-pills {
    display:flex;
    flex-wrap:wrap;
    gap:8px;
    margin-top:14px;
}

.premium-pill {
    display:inline-flex;
    align-items:center;
    gap:6px;
    border-radius:999px;
    border:1px solid #E5E7EB;
    background:#FFFFFF;
    padding:7px 10px;
    font-size:12px;
    font-weight:750;
    color:#111827;
}

.premium-pill span {
    color:#F26522;
    font-weight:900;
}

.semaforo-box {
    border-radius:18px;
    padding:16px 18px;
    margin: 12px 0 18px 0;
    display:flex;
    align-items:center;
    justify-content:space-between;
    gap:16px;
    border:1px solid #E5E7EB;
    box-shadow:0 2px 9px rgba(0,0,0,0.06);
    background:#FFFFFF;
}

.semaforo-box.good {
    background: linear-gradient(90deg, rgba(0,163,80,0.12), #FFFFFF);
    border-left:6px solid #00A350;
}

.semaforo-box.warn {
    background: linear-gradient(90deg, rgba(242,101,34,0.13), #FFFFFF);
    border-left:6px solid #F26522;
}

.semaforo-icon {
    font-size:30px;
    line-height:1;
}

.semaforo-content {
    flex:1;
}

.semaforo-title {
    font-size:16px;
    font-weight:900;
    color:#111827;
    margin-bottom:3px;
}

.semaforo-text {
    font-size:13px;
    color:#374151;
    font-weight:600;
    line-height:1.35;
}

.top-insights-grid {
    display:grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap:14px;
    margin: 8px 0 18px 0;
}

.top-insight {
    position:relative;
    border-radius:16px;
    border:1px solid #E5E7EB;
    background:#FFFFFF;
    padding:14px 15px 14px 15px;
    box-shadow:0 1px 6px rgba(0,0,0,0.05);
    overflow:hidden;
}

.top-insight::before {
    content:"";
    position:absolute;
    left:0;
    top:0;
    width:100%;
    height:4px;
    background:#0078D4;
}

.top-insight.good::before { background:#00A350; }
.top-insight.warn::before { background:#F26522; }

.top-insight-label {
    font-size:11px;
    color:#6B7280;
    font-weight:850;
    margin-bottom:5px;
    text-transform:uppercase;
    letter-spacing:.02em;
}

.top-insight-title {
    font-size:15px;
    color:#111827;
    font-weight:900;
    margin-bottom:4px;
}

.top-insight-text {
    font-size:13px;
    color:#374151;
    font-weight:550;
    line-height:1.35;
}

.table-note-v5 {
    font-size:12px;
    color:#6B7280;
    margin: -2px 0 10px 13px;
    font-weight:600;
}

div[data-testid="stDataFrame"] {
    margin-top: 6px;
    margin-bottom: 18px;
}

@media (max-width: 950px) {
    .premium-hero-top {
        flex-direction:column;
    }
    .premium-status {
        width:100%;
        text-align:left;
    }
    .top-insights-grid {
        grid-template-columns:1fr;
    }
}


/* Ajuste etapa 9.2 — comparativo com dois cards */
.compare-card-grid-two {
    grid-template-columns: repeat(2, minmax(0, 1fr));
}
@media (max-width: 900px) {
    .compare-card-grid-two {
        grid-template-columns: 1fr;
    }
}

</style>
""",
    unsafe_allow_html=True,
)

# =========================
# SIDEBAR
# =========================
with st.sidebar:
    # Cabeçalho da sidebar com ícone personalizado alinhado
    if os.path.exists("reciclar-simbolo.png"):
        import base64

        with open("reciclar-simbolo.png", "rb") as img_file:
            img_base64 = base64.b64encode(img_file.read()).decode()

        st.markdown(
            f"""
            <div style="
                display: flex;
                align-items: center;
                gap: 10px;
                margin: 0 0 10px 0;
                padding: 0;
                line-height: 1;
            ">
                <img src="data:image/png;base64,{img_base64}" style="
                    width: 23px;
                    height: 23px;
                    object-fit: contain;
                    display: block;
                    margin: 0;
                    padding: 0;
                ">
                <span style="
                    font-size: 1.45rem;
                    font-weight: 850;
                    color: inherit;
                    line-height: 1;
                    margin: 0;
                    padding: 0;
                ">Filtros</span>
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            """
            <div style="
                display: flex;
                align-items: center;
                gap: 10px;
                margin: 0 0 10px 0;
                line-height: 1;
            ">
                <span style="font-size: 22px; line-height: 1;">♻️</span>
                <span style="font-size: 1.45rem; font-weight: 850; line-height: 1;">Filtros</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # Fonte da base continua escondida para usuários comuns.
    if eh_admin():
        fonte_dados = st.session_state.get("fonte_dados_admin", "Google Drive")
        nome_arquivo_drive = st.session_state.get("nome_arquivo_drive_admin", "BaseSistema.xlsx")
        arquivo = None
    else:
        fonte_dados = "Google Drive"
        nome_arquivo_drive = "BaseSistema.xlsx"
        arquivo = None

    st.markdown('<div class="sidebar-section-title">Estrutura da análise</div>', unsafe_allow_html=True)
    indicador = selecionar_indicador_por_blocos()

    # Para Tecfil, não permitir troca de unidade.
    # O usuário deve alterar apenas Bloco e Indicador.
    if eh_tecfil(indicador):
        filial = "Geral"
        st.session_state["filial_tecf_il_travada"] = filial
    else:
        st.markdown('<div class="sidebar-section-title">Unidade analisada</div>', unsafe_allow_html=True)
        opcoes_filial = filiais_permitidas_usuario()
        if len(opcoes_filial) == 1:
            filial = opcoes_filial[0]
            st.markdown(f'<div class="sidebar-info-card" style="padding:8px 12px;">📍 {filial}</div>', unsafe_allow_html=True)
        else:
            filial = st.selectbox("Filial", opcoes_filial)

    bloco_selecionado = st.session_state.get("bloco_principal_indicador", "-")
    grupo_selecionado = st.session_state.get("grupo_indicador", "-")

    st.markdown(
        f"""
        <div class="sidebar-info-card">
            <div class="sidebar-info-title">Resumo dos filtros ativos</div>
            <div class="sidebar-chip-list">
                <div class="sidebar-chip">
                    <span class="sidebar-chip-label">Bloco</span>
                    <span class="sidebar-chip-value">{bloco_selecionado}</span>
                </div>
                <div class="sidebar-chip">
                    <span class="sidebar-chip-label">Tipo</span>
                    <span class="sidebar-chip-value">{grupo_selecionado}</span>
                </div>
                <div class="sidebar-chip">
                    <span class="sidebar-chip-label">Indicador</span>
                    <span class="sidebar-chip-value">{indicador}</span>
                </div>
                <div class="sidebar-chip">
                    <span class="sidebar-chip-label">Filial</span>
                    <span class="sidebar-chip-value">{filial}</span>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    usuario_logado = st.session_state.get("usuario_logado", "usuário")
    perfil_exibicao = nome_perfil_exibicao()

    st.markdown(
        f"""
        <div class="sidebar-user-card">
            <div class="sidebar-user-name">👤 {usuario_logado}</div>
            <div class="sidebar-user-profile">Perfil: <strong>{perfil_exibicao}</strong></div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Mostra tempo restante de sessão (sutil)
    ultimo_acesso = st.session_state.get("ultimo_acesso", time.time())
    minutos_inativo = int((time.time() - ultimo_acesso) / 60)
    minutos_restantes = max(0, LIMITE_SESSAO_MINUTOS - minutos_inativo)
    if minutos_restantes <= 10:
        st.caption(f"⏰ Sessão expira em ~{minutos_restantes} min")

    col_side_1, col_side_2 = st.columns(2)
    with col_side_1:
        if st.button("🔄 Atualizar", use_container_width=True, help="Força nova leitura da planilha (limpa o cache)"):
            st.cache_data.clear()
            st.success("Dados atualizados!")
            st.rerun()

    with col_side_2:
        if st.button("🚪 Sair", use_container_width=True):
            for chave in [
                "acesso_liberado",
                "usuario_logado",
                "tentativas_login",
                "bloqueado_ate",
                "ultimo_acesso",
                "arquivo_manual_bytes_admin",
                "arquivo_manual_nome_admin",
            ]:
                st.session_state.pop(chave, None)
            st.rerun()

    st.caption("v5.0 etapa 13.3 — Versão Final")



if fonte_dados == "Google Drive":
    df_raw = baixar_planilha_drive(nome_arquivo_drive)
else:
    # Upload manual é recurso exclusivo do administrador.
    if not eh_admin():
        st.error("Apenas administradores podem usar upload manual.")
        st.stop()

    arquivo_manual_bytes = st.session_state.get("arquivo_manual_bytes_admin")

    if not arquivo_manual_bytes:
        st.warning("Upload manual selecionado pelo administrador, mas nenhum arquivo foi carregado.")
        st.info("Acesse Opções > Administração da base e carregue a planilha, ou volte para Google Drive.")
        st.stop()

    df_raw = carregar(io.BytesIO(arquivo_manual_bytes))

df_raw = validar_colunas_base(df_raw)

data_geracao_planilha = obter_data_geracao_planilha(df_raw)
renderizar_cabecalho(data_geracao_planilha)

df = filtrar(df_raw, indicador, filial)
df_todas_unidades = filtrar(df_raw, indicador, "Geral")

# Base correta para o Comparativo entre filiais/unidades.
#
# Regra geral:
# - Para indicadores normais: compara as filiais reais.
# - Para Tecfil: compara Tecfil Geral + Tecfil SP/MS/ES/PR.
_partes_comparativo_filiais = []

if eh_tecfil(indicador):
    indicadores_tecfil_comparativo = [
        "Tecfil Geral",
        "Tecfil SP",
        "Tecfil MS",
        "Tecfil ES",
        "Tecfil PR",
    ]

    for _indicador_tecfil in indicadores_tecfil_comparativo:
        try:
            _df_tecfil = filtrar(df_raw, _indicador_tecfil, "Geral")
            if _df_tecfil is not None and not _df_tecfil.empty:
                _partes_comparativo_filiais.append(_df_tecfil)
        except Exception:
            pass
else:
    for _filial_real in FILIAIS_REAIS:
        try:
            _df_filial_real = filtrar(df_raw, indicador, _filial_real)
            if _df_filial_real is not None and not _df_filial_real.empty:
                _partes_comparativo_filiais.append(_df_filial_real)
        except Exception:
            pass

df_comparativo_filiais = (
    pd.concat(_partes_comparativo_filiais, ignore_index=True)
    if _partes_comparativo_filiais
    else pd.DataFrame()
)

if df.empty:
    diagnosticar_sem_dados(df_raw, indicador, filial)
    st.stop()



def label_aba_com_icone(arquivo_icone, texto, emoji_fallback="📊"):
    """
    Cria um rótulo de aba com imagem local.
    O Streamlit aceita Markdown em st.tabs; o tamanho da imagem é controlado por CSS antes das abas.
    Se o arquivo não existir, usa emoji de fallback.
    """
    try:
        if os.path.exists(arquivo_icone):
            with open(arquivo_icone, "rb") as f:
                img_base64 = base64.b64encode(f.read()).decode("utf-8")
            return f"![{texto}](data:image/png;base64,{img_base64}) {texto}"
    except Exception:
        pass

    return f"{emoji_fallback} {texto}"




# CSS para alinhar e aumentar ícones nas abas do Streamlit
st.markdown(
    """
    <style>
        div[data-baseweb="tab-list"] img {
            height: 20px !important;
            width: 20px !important;
            object-fit: contain !important;
            vertical-align: -4px !important;
            margin-right: 4px !important;
        }

        div[data-baseweb="tab"] p {
            display: flex !important;
            align-items: center !important;
            gap: 4px !important;
        }
    </style>
    """,
    unsafe_allow_html=True,
)


if eh_admin():
    tab0, tab1, tab2, tab3, tab_geo, tab6 = st.tabs([
        "📊 Visão Geral",
        "📅 Períodos",
        "📈 MoM",
        "🔁 YoY",
        "🗺️ Mapa",
        "📤 Opções"
    ])
else:
    tab0, tab1, tab2, tab3, tab_geo = st.tabs([
        "📊 Visão Geral",
        "📅 Períodos",
        "📈 MoM",
        "🔁 YoY",
        "🗺️ Mapa"
    ])
    tab6 = None

tab4 = None
tab5 = None



# =========================
# RESUMOS ACUMULADOS — TRIMESTRE / SEMESTRE
# =========================
def _mes_numero_por_nome(valor):
    """Converte nome abreviado do mês em número."""
    if valor is None or pd.isna(valor):
        return None

    txt = normalizar_texto(str(valor)).replace(".", "").strip()

    mapa = {
        "JAN": 1, "JANEIRO": 1,
        "FEV": 2, "FEVEREIRO": 2,
        "MAR": 3, "MARCO": 3, "MARÇO": 3,
        "ABR": 4, "ABRIL": 4,
        "MAI": 5, "MAIO": 5,
        "JUN": 6, "JUNHO": 6,
        "JUL": 7, "JULHO": 7,
        "AGO": 8, "AGOSTO": 8,
        "SET": 9, "SETEMBRO": 9,
        "OUT": 10, "OUTUBRO": 10,
        "NOV": 11, "NOVEMBRO": 11,
        "DEZ": 12, "DEZEMBRO": 12,
    }

    return mapa.get(txt)


def _periodo_por_mes(mes, modo):
    if mes is None or pd.isna(mes):
        return None

    mes = int(mes)

    if modo == "Trimestre":
        tri = ((mes - 1) // 3) + 1
        inicio = ((tri - 1) * 3) + 1
        fim = tri * 3
        return f"{tri}º Trim ({MESES_MAPA[inicio]} a {MESES_MAPA[fim]})"

    if modo == "Semestre":
        sem = 1 if mes <= 6 else 2
        return "1º Sem (Jan a Jun)" if sem == 1 else "2º Sem (Jul a Dez)"

    if modo == "Ano Completo":
        return "Ano Completo (Jan a Dez)"

    return MESES_MAPA.get(mes, str(mes))


def _somar_coluna(df, coluna):
    if df is None or df.empty or coluna not in df.columns:
        return None
    serie = pd.to_numeric(df[coluna], errors="coerce")
    if serie.dropna().empty:
        return None
    return serie.fillna(0).sum()


def _media_coluna(df, coluna):
    if df is None or df.empty or coluna not in df.columns:
        return None
    serie = pd.to_numeric(df[coluna], errors="coerce").dropna()
    if serie.empty:
        return None
    return serie.mean()


def montar_resumo_periodo(df_mensal, indicador, modo):
    """
    Monta resumo acumulado por Trimestre ou Semestre sem alterar as fórmulas-base.
    """
    if df_mensal is None or df_mensal.empty or modo not in ["Trimestre", "Semestre", "Ano Completo"]:
        return pd.DataFrame()

    if "Mês" not in df_mensal.columns:
        return pd.DataFrame()

    base = df_mensal.copy()
    base = base[~base["Mês"].astype(str).str.upper().isin(["TOTAL", "MÉDIA", "MEDIA"])].copy()
    base["_MES_NUM"] = base["Mês"].apply(_mes_numero_por_nome)
    base = base[base["_MES_NUM"].notna()].copy()

    if base.empty:
        return pd.DataFrame()

    base["_PERIODO"] = base["_MES_NUM"].apply(lambda m: _periodo_por_mes(m, modo))

    rows = []
    acumulado = 0

    for periodo, sub in base.groupby("_PERIODO", sort=False):
        row = {"Mês": periodo}

        if eh_moto_margem(indicador):
            faturamento = _somar_coluna(sub, "Faturamento")
            compra = _somar_coluna(sub, "Compra")
            margem = (faturamento or 0) - (compra or 0) if faturamento is not None or compra is not None else None

            meta_col = "Meta" if "Meta" in sub.columns else "Meta %"
            meta_pond = None
            if faturamento and faturamento > 0 and meta_col in sub.columns:
                fat = pd.to_numeric(sub.get("Faturamento"), errors="coerce")
                meta = pd.to_numeric(sub.get(meta_col), errors="coerce")
                mask = fat.notna() & meta.notna() & (fat != 0)
                meta_pond = (fat[mask] * meta[mask]).sum() / fat[mask].sum() if mask.any() and fat[mask].sum() > 0 else None

            tx = margem / faturamento if faturamento and faturamento > 0 else None

            row.update({
                "Meta": meta_pond,
                "Compra": compra,
                "Faturamento": faturamento,
                "Margem Bruta": margem,
                "Tx. Sucesso": tx,
                "Acumulado": margem,
            })

        elif eh_despesa_geral(indicador):
            despesa = _somar_coluna(sub, "Despesa")
            receita = _somar_coluna(sub, "Receita")

            limite_pct = None
            if receita and receita > 0 and "Limite %" in sub.columns:
                rec = pd.to_numeric(sub.get("Receita"), errors="coerce").fillna(0)
                lim = pd.to_numeric(sub.get("Limite %"), errors="coerce").fillna(0)
                limite_pct = (rec * lim).sum() / rec.sum() if rec.sum() > 0 else None
            else:
                limite_pct = _media_coluna(sub, "Limite %")

            limite_rs = receita * limite_pct if receita is not None and limite_pct is not None and pd.notna(limite_pct) else None
            resultado_rs = limite_rs - despesa if limite_rs is not None and despesa is not None else None
            tx = despesa / receita if receita and receita > 0 else None

            acumulado += resultado_rs if resultado_rs is not None and pd.notna(resultado_rs) else 0

            row.update({
                "Limite %": limite_pct,
                "Despesa": despesa,
                "Receita": receita,
                "Resultado R$": resultado_rs,
                "Tx. Sucesso": tx,
                "Acumulado": acumulado,
            })

        elif eh_resultado_financeiro(indicador):
            despesa = _somar_coluna(sub, "Despesa")
            receita = _somar_coluna(sub, "Receita")

            meta_pct = None
            if receita and receita > 0 and "Meta %" in sub.columns:
                rec = pd.to_numeric(sub.get("Receita"), errors="coerce").fillna(0)
                meta = pd.to_numeric(sub.get("Meta %"), errors="coerce").fillna(0)
                meta_pct = (rec * meta).sum() / rec.sum() if rec.sum() > 0 else None
            else:
                meta_pct = _media_coluna(sub, "Meta %")

            resultado_pct = 1 - (despesa / receita) if receita and receita > 0 else None
            resultado_rs = receita * (resultado_pct - meta_pct) if receita is not None and resultado_pct is not None and meta_pct is not None and pd.notna(meta_pct) else None

            acumulado += resultado_rs if resultado_rs is not None and pd.notna(resultado_rs) else 0

            row.update({
                "Meta %": meta_pct,
                "Despesa": despesa,
                "Receita": receita,
                "Resultado R$": resultado_rs,
                "Resultado %": resultado_pct,
                "Acumulado": acumulado,
            })

        elif eh_despesa_manutencao(indicador):
            limite = _somar_coluna(sub, "Limite")
            despesa = _somar_coluna(sub, "Despesa")
            resultado_rs = limite - despesa if limite is not None and despesa is not None else None
            resultado_pct = 1 - (despesa / limite) if limite and limite > 0 else None

            acumulado += resultado_rs if resultado_rs is not None and pd.notna(resultado_rs) else 0

            row.update({
                "Limite": limite,
                "Despesa": despesa,
                "Result. R$": resultado_rs,
                "Result. %": resultado_pct,
                "Acumulado": acumulado,
            })

        elif eh_despesa_hora_extra(indicador):
            limite = _somar_coluna(sub, "Limite")
            pago = _somar_coluna(sub, "Pago em Hora Extra")
            salario = _somar_coluna(sub, "Salário")
            he_folha = pago / salario if salario and salario > 0 else None
            saldo = limite - pago if limite is not None and pago is not None else None
            resultado_pct = pago / limite if limite and limite > 0 else None

            row.update({
                "Limite": limite,
                "Pago em Hora Extra": pago,
                "Salário": salario,
                "HE da Folha": he_folha,
                "Saldo do Limite": saldo,
                "Resultado %": resultado_pct,
            })

        elif eh_tecfil(indicador):
            meta_kg = _somar_coluna(sub, "Meta KG")
            meta_rs = _somar_coluna(sub, "Meta R$")
            real_kg = _somar_coluna(sub, "Realizado KG")
            real_rs = _somar_coluna(sub, "Realizado R$")
            dif_kg = real_kg - meta_kg if real_kg is not None and meta_kg is not None else None
            dif_rs = real_rs - meta_rs if real_rs is not None and meta_rs is not None else None
            pct_kg = dif_kg / meta_kg if meta_kg and meta_kg > 0 else None
            pct_rs = dif_rs / meta_rs if meta_rs and meta_rs > 0 else None

            row.update({
                "Meta KG": meta_kg,
                "Meta R$": meta_rs,
                "Realizado KG": real_kg,
                "Realizado R$": real_rs,
                "% Dif. KG": pct_kg,
                "% Dif. R$": pct_rs,
                "Dif. KG": dif_kg,
                "Dif. R$": dif_rs,
            })

        elif eh_qualidade(indicador):
            meta = _media_coluna(sub, "Meta")
            qtd = _somar_coluna(sub, "Qtd. Coletas")

            qtd_sucesso_col = "Qtd. Sucesso"
            for c in sub.columns:
                if "Sucesso" in str(c) and c != "Tx. Sucesso":
                    qtd_sucesso_col = c
                    break

            qtd_sucesso = _somar_coluna(sub, qtd_sucesso_col)
            resultado_pct = qtd_sucesso / qtd if qtd and qtd > 0 and qtd_sucesso is not None else _media_coluna(sub, "Resultado %")

            if modelo_qualidade(indicador) == "parametro_coleta_critico":
                diferenca = _somar_coluna(sub, "Resultado")
                row.update({
                    "Meta": meta,
                    "Qtd. Coletas": qtd,
                    qtd_sucesso_col: qtd_sucesso,
                    "Resultado": diferenca,
                    "Resultado %": resultado_pct,
                })
            else:
                diferenca = _somar_coluna(sub, "Diferença")
                row.update({
                    "Meta": meta,
                    "Qtd. Coletas": qtd,
                    qtd_sucesso_col: qtd_sucesso,
                    "Diferença": diferenca,
                    "Resultado %": resultado_pct,
                })

        else:
            meta = _somar_coluna(sub, "Meta")
            realizado = _somar_coluna(sub, "Realizado")
            gap = realizado - meta if realizado is not None and meta is not None else None
            ating = realizado / meta if meta and meta > 0 else None

            acumulado += gap if gap is not None and pd.notna(gap) else 0

            row.update({
                "Meta": meta,
                "Realizado": realizado,
                "Gap (R$)": gap,
                "Atingimento": ating,
                "Acumulado": acumulado,
            })

        rows.append(row)

    return pd.DataFrame(rows).replace([float("inf"), float("-inf")], pd.NA)


def renderizar_tabela_periodo_acumulado(df_periodo, indicador, modo):
    """Renderiza o resumo trimestral/semestral com formatação compatível."""
    if df_periodo is None or df_periodo.empty:
        st.info(f"Sem dados suficientes para montar o resumo por {modo.lower()}.")
        return

    titulo_modo = "ano completo" if modo == "Ano Completo" else modo.lower()
    st.markdown(f"#### Resumo acumulado por {titulo_modo}")
    st.caption("Os valores são acumulados a partir da própria tabela mensal do ano selecionado, sem alterar as fórmulas-base do indicador.")

    fmt = {
        "Meta": lambda v: (fmt_pct(v) if eh_moto_margem(indicador) or eh_qualidade(indicador) else fmt_brl(v)) if pd.notna(v) else "",
        "Realizado": lambda v: fmt_brl(v) if pd.notna(v) else "",
        "Gap (R$)": lambda v: f"R$ {v:+,.0f}".replace(",", ".") if pd.notna(v) else "",
        "Atingimento": lambda v: fmt_pct(v) if pd.notna(v) else "",
        "Limite": lambda v: fmt_brl(v) if pd.notna(v) else "",
        "Despesa": lambda v: fmt_brl(v) if pd.notna(v) else "",
        "Receita": lambda v: fmt_brl(v) if pd.notna(v) else "",
        "Resultado R$": lambda v: f"R$ {v:+,.0f}".replace(",", ".") if pd.notna(v) else "",
        "Result. R$": lambda v: f"R$ {v:+,.0f}".replace(",", ".") if pd.notna(v) else "",
        "Resultado %": lambda v: fmt_pct(v) if pd.notna(v) else "",
        "Result. %": lambda v: fmt_pct(v) if pd.notna(v) else "",
        "Limite %": lambda v: fmt_pct(v) if pd.notna(v) else "",
        "Meta %": lambda v: fmt_pct(v) if pd.notna(v) else "",
        "Tx. Sucesso": lambda v: fmt_pct(v) if pd.notna(v) else "",
        "Compra": lambda v: fmt_brl(v) if pd.notna(v) else "",
        "Faturamento": lambda v: fmt_brl(v) if pd.notna(v) else "",
        "Margem Bruta": lambda v: fmt_brl(v) if pd.notna(v) else "",
        "Acumulado": lambda v: f"R$ {v:+,.0f}".replace(",", ".") if pd.notna(v) else "",
        "Pago em Hora Extra": lambda v: fmt_brl(v) if pd.notna(v) else "",
        "Salário": lambda v: fmt_brl(v) if pd.notna(v) else "",
        "HE da Folha": lambda v: fmt_pct(v) if pd.notna(v) else "",
        "Saldo do Limite": lambda v: f"R$ {v:+,.0f}".replace(",", ".") if pd.notna(v) else "",
        "Meta KG": lambda v: fmt_num(v) if pd.notna(v) else "",
        "Meta R$": lambda v: fmt_brl(v) if pd.notna(v) else "",
        "Realizado KG": lambda v: fmt_num(v) if pd.notna(v) else "",
        "Realizado R$": lambda v: fmt_brl(v) if pd.notna(v) else "",
        "% Dif. KG": lambda v: fmt_pct(v) if pd.notna(v) else "",
        "% Dif. R$": lambda v: fmt_pct(v) if pd.notna(v) else "",
        "Dif. KG": lambda v: fmt_num(v) if pd.notna(v) else "",
        "Dif. R$": lambda v: fmt_brl(v) if pd.notna(v) else "",
        "Qtd. Coletas": lambda v: fmt_num(v) if pd.notna(v) else "",
        "Qtd. Sucesso": lambda v: fmt_num(v) if pd.notna(v) else "",
        "Diferença": lambda v: fmt_num(v) if pd.notna(v) else "",
        "Resultado": lambda v: fmt_num(v) if pd.notna(v) else "",
    }
    fmt = {k: v for k, v in fmt.items() if k in df_periodo.columns}

    styler = df_periodo.style.format(fmt)

    try:
        for col in ["Gap (R$)", "Resultado R$", "Result. R$", "Saldo do Limite", "Acumulado"]:
            if col in df_periodo.columns:
                styler = styler.map(lambda v: cor_gap_valor(v, False), subset=[col])

        # Regra especial para Despesa Geral:
        # Tx. Sucesso = Despesa / Receita.
        # Se Tx. Sucesso passar do Limite %, está ruim e deve ficar laranja.
        if eh_despesa_geral(indicador) and "Tx. Sucesso" in df_periodo.columns and "Limite %" in df_periodo.columns:
            def cor_tx_periodo_despesa_geral(row):
                estilos = ["" for _ in row.index]

                tx = row.get("Tx. Sucesso")
                limite = row.get("Limite %")

                if pd.notna(tx) and pd.notna(limite) and "Tx. Sucesso" in row.index:
                    idx = list(row.index).index("Tx. Sucesso")
                    estilos[idx] = (
                        f"color: {COR_VERDE}; font-weight:bold"
                        if tx <= limite
                        else f"color: {COR_LARANJA}; font-weight:bold"
                    )

                return estilos

            styler = styler.apply(cor_tx_periodo_despesa_geral, axis=1)

        else:
            for col in ["Atingimento", "Resultado %", "Result. %", "Tx. Sucesso"]:
                if col in df_periodo.columns:
                    styler = styler.map(lambda v: f"color: {COR_VERDE}; font-weight:bold" if pd.notna(v) and v >= 0 else (f"color: {COR_LARANJA}; font-weight:bold" if pd.notna(v) else ""), subset=[col])
    except Exception:
        pass

    st.dataframe(styler, use_container_width=True, hide_index=True)


# =========================
# ABA VISÃO GERAL
# =========================
with tab0:
    st.markdown(f"<h2 style='margin-bottom:6px;'>📊 Visão Geral — {indicador} | {filial}</h2>", unsafe_allow_html=True)

    anos = sorted(df["ANO"].dropna().unique())
    ano_kpi = int(anos[-1])
    base_kpi = df[df["ANO"] == ano_kpi].copy()

    periodo_contexto = periodo_contexto_por_data_ou_dados(base_kpi, ano_kpi, data_geracao_planilha)
    renderizar_capa_premium(indicador, filial, ano_kpi, periodo_contexto, data_geracao_planilha, df)
    titulo_secao("Resumo", "Principais indicadores consolidados do período selecionado.")

    if eh_moto_margem(indicador):
        resumo_ano_moto = resumo_moto_por_grupo(base_kpi)
        periodo_cmp = comparar_mesmo_periodo(df, indicador, ano_kpi)

        if not periodo_cmp.empty and len(periodo_cmp) == 2:
            ytd_valor = periodo_cmp.iloc[1]["Faturamento"]
            delta_ytd = periodo_cmp.iloc[1]["Variação Faturamento"]
            periodo_label = periodo_cmp.iloc[1]["Período"]
        else:
            ytd_valor = None
            delta_ytd = None
            periodo_label = "-"

        c1, c2, c3, c4, c5 = st.columns(5)
        with c1:
            kpi_metric("Faturamento Ano", fmt_brl(resumo_ano_moto["Faturamento"]))
        with c2:
            kpi_metric("Compra Ano", fmt_brl(resumo_ano_moto["Compra"]))
        with c3:
            kpi_metric("Margem Bruta Ano", fmt_brl(resumo_ano_moto["Margem Bruta"]))
        with c4:
            kpi_metric("Tx. Sucesso", fmt_pct(resumo_ano_moto["Tx. Sucesso"]))
        with c5:
            kpi_metric(f"{rotulo_ytd_kpi(indicador)} {periodo_label}", fmt_brl(ytd_valor) if ytd_valor is not None else "-", delta_ytd)

        titulo_secao("Evolução mensal", "Acompanhamento visual do indicador ao longo do ano.")

        df_dashboard_ano = tabela_pneus_moto_ano(df, ano_kpi)
        dados_chart = df_dashboard_ano[(df_dashboard_ano["Mês"] != "TOTAL") & (df_dashboard_ano["Faturamento"].notna())]

        if not dados_chart.empty:
            fig_dash = go.Figure()

            cores_tx = [
                COR_VERDE if pd.notna(tx) and pd.notna(meta) and tx >= meta else COR_LARANJA
                for tx, meta in zip(dados_chart["Tx. Sucesso"], dados_chart["Meta"])
            ]

            fig_dash.add_bar(
                x=mes_ano_label(dados_chart["Mês"], ano_kpi if "ano_kpi" in globals() else ano_selecionado),
                y=dados_chart["Tx. Sucesso"],
                name="Tx. Sucesso",
                marker_color=cores_tx,
                text=[fmt_pct(v) for v in dados_chart["Tx. Sucesso"]],
                textposition="outside",
            )

            if ano_kpi >= 2026:
                fig_dash.add_scatter(
                    x=mes_ano_label(dados_chart["Mês"], ano_kpi if "ano_kpi" in globals() else ano_selecionado),
                    y=dados_chart["Meta"],
                    name="Meta %",
                    mode="lines+markers",
                    line=dict(color=COR_AZUL, width=3, dash="dot"),
                    marker=dict(size=7),
                )

            fig_dash.update_layout(
                title=f"Pneus Moto — Taxa de Sucesso x Meta — {ano_kpi}",
                height=420,
                legend=dict(orientation="h", y=-0.18),
                yaxis_title="%",
                margin=dict(t=60, b=20, l=20, r=20),
                yaxis_tickformat=".0%",
            )

            st.plotly_chart(fig_dash, use_container_width=True, key="grafico_dashboard_moto")

        titulo_secao("Comparativo ano a ano", "Resumo histórico consolidado do indicador selecionado.")
        df_yoy_dashboard = calcular_yoy(df, indicador)

        st.dataframe(
            df_yoy_dashboard[["ANO", "Faturamento", "Compra", "Margem Bruta", "Meta %", "Meta Margem (R$)", "Tx. Sucesso"]].style
            .format({
                "Faturamento": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                "Compra": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                "Margem Bruta": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                "Meta %": lambda v: fmt_pct(v) if pd.notna(v) else "—",
                "Meta Margem (R$)": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                "Tx. Sucesso": lambda v: fmt_pct(v) if pd.notna(v) else "—",
            })
            .apply(cor_tx_sucesso_moto_por_linha, axis=1),
            use_container_width=True,
            hide_index=True,
        )

    elif eh_qualidade(indicador):
        rot = rotulos_qualidade(indicador)
        resumo_ano_qualidade = resumo_qualidade_por_grupo(base_kpi, indicador)
        periodo_cmp = comparar_mesmo_periodo(df, indicador, ano_kpi)

        if not periodo_cmp.empty and len(periodo_cmp) == 2:
            ytd_valor = periodo_cmp.iloc[1]["Resultado %"]
            delta_ytd = periodo_cmp.iloc[1]["Variação Resultado"]
            periodo_label = periodo_cmp.iloc[1]["Período"]
        else:
            ytd_valor = None
            delta_ytd = None
            periodo_label = "-"

        c1, c2, c3, c4, c5 = st.columns(5)
        with c1:
            meta_valor = fmt_num(resumo_ano_qualidade["Meta"]) if modelo_qualidade(indicador) == "parametro_coleta_critico" else fmt_pct(resumo_ano_qualidade["Meta"])
            kpi_metric(rot["meta"], meta_valor)
        with c2:
            kpi_metric(rot["qtd_total"], fmt_num(resumo_ano_qualidade["Qtd. Coletas"]))
        with c3:
            kpi_metric(rot["qtd_sucesso"], fmt_num(resumo_ano_qualidade["Qtd. Sucesso"]))
        with c4:
            kpi_metric(rot["diferenca"], fmt_num(resumo_ano_qualidade["Diferença"]))
        with c5:
            kpi_metric(rot["resultado"], fmt_pct(resumo_ano_qualidade["Resultado %"]))

        titulo_secao("Evolução mensal", "Acompanhamento visual do indicador ao longo do ano.")

        df_dashboard_ano = tabela_completa_ano(df, ano_kpi, indicador)
        dados_chart = df_dashboard_ano[df_dashboard_ano["Mês"] != "TOTAL"].dropna(subset=["Resultado %"])

        if not dados_chart.empty:
            fig_dash = grafico_realizado_meta(df_dashboard_ano, ano_kpi, indicador=indicador)
            st.plotly_chart(fig_dash, use_container_width=True, key="grafico_dashboard_qualidade")

        titulo_secao("Comparativo ano a ano", "Resumo histórico consolidado do indicador selecionado.")
        df_yoy_dashboard = calcular_yoy(df, indicador)

        rot = rotulos_qualidade(indicador)
        col_qtd_sucesso = rot["qtd_sucesso"]
        df_yoy_dashboard_tela = preparar_tabela_qualidade_exibicao(df_yoy_dashboard[["ANO", "Meta", "Qtd. Coletas", "Qtd. Sucesso", "Diferença", "Resultado %"]], indicador)
        styler_yoy_q = (
            df_yoy_dashboard_tela.style
            .format({
                "Meta": lambda v: fmt_num(v) if modelo_qualidade(indicador) == "parametro_coleta_critico" and pd.notna(v) else (fmt_pct(v) if pd.notna(v) else "—"),
                "Qtd. Coletas": lambda v: fmt_num(v) if pd.notna(v) else "—",
                col_qtd_sucesso: lambda v: fmt_num(v) if pd.notna(v) else "—",
                "Diferença": lambda v: fmt_num(v) if pd.notna(v) else "—",
                "Resultado": lambda v: fmt_num(v) if pd.notna(v) else "—",
                "Resultado %": lambda v: fmt_pct(v) if pd.notna(v) else "—",
            })
            .apply(lambda row: cor_resultado_qualidade_por_linha(row, indicador), axis=1)
        )
        styler_yoy_q = aplicar_estilo_diferenca_qualidade(styler_yoy_q, indicador, subset=["Diferença"])
        st.dataframe(
            styler_yoy_q,
            use_container_width=True,
            hide_index=True,
        )

    elif eh_tecfil(indicador):
        resumo_ano_tecfil = resumo_tecfil_por_grupo(base_kpi)
        periodo_cmp = comparar_mesmo_periodo(df, indicador, ano_kpi)

        if not periodo_cmp.empty and len(periodo_cmp) == 2:
            ytd_valor = periodo_cmp.iloc[1]["Realizado R$"]
            delta_ytd = periodo_cmp.iloc[1]["Variação R$"]
            periodo_label = periodo_cmp.iloc[1]["Período"]
        else:
            ytd_valor = None
            delta_ytd = None
            periodo_label = "-"

        c1, c2, c3, c4, c5 = st.columns(5)
        with c1:
            kpi_metric("Realizado KG Ano", fmt_num(resumo_ano_tecfil["Realizado KG"]))
        with c2:
            kpi_metric("Meta KG Ano", fmt_num(resumo_ano_tecfil["Meta KG"]))
        with c3:
            kpi_metric("Diferença KG", fmt_num(resumo_ano_tecfil["Dif. KG"]))
        with c4:
            kpi_metric("Realizado R$ Ano", fmt_brl(resumo_ano_tecfil["Realizado R$"]))
        with c5:
            kpi_metric(f"{rotulo_ytd_kpi(indicador)} {periodo_label}", fmt_brl(ytd_valor) if ytd_valor is not None else "-", delta_ytd)

        titulo_secao("Evolução mensal", "Acompanhamento visual do indicador ao longo do ano.")

        df_dashboard_ano = tabela_completa_ano(df, ano_kpi, indicador)
        dados_chart = df_dashboard_ano[df_dashboard_ano["Mês"] != "TOTAL"].dropna(subset=["Realizado R$"])

        if not dados_chart.empty:
            fig_dash = grafico_realizado_meta(df_dashboard_ano, ano_kpi, indicador=indicador)
            st.plotly_chart(fig_dash, use_container_width=True, key="grafico_dashboard_tecfil")

        titulo_secao("Comparativo ano a ano", "Resumo histórico consolidado do indicador selecionado.")
        df_yoy_dashboard = calcular_yoy(df, indicador)

        st.dataframe(
            df_yoy_dashboard[["ANO", "Meta KG", "Meta R$", "Realizado KG", "Realizado R$", "% Dif. KG", "% Dif. R$", "Dif. KG", "Dif. R$"]].style
            .format({
                "Meta KG": lambda v: fmt_num(v) if pd.notna(v) else "—",
                "Meta R$": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                "Realizado KG": lambda v: fmt_num(v) if pd.notna(v) else "—",
                "Realizado R$": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                "% Dif. KG": lambda v: fmt_pct(v) if pd.notna(v) else "—",
                "% Dif. R$": lambda v: fmt_pct(v) if pd.notna(v) else "—",
                "Dif. KG": lambda v: fmt_num(v) if pd.notna(v) else "—",
                "Dif. R$": lambda v: fmt_brl(v) if pd.notna(v) else "—",
            })
            .map(cor_tecfil_resultado, subset=["% Dif. KG", "% Dif. R$", "Dif. KG", "Dif. R$"]),
            use_container_width=True,
            hide_index=True,
        )

    elif eh_resultado_financeiro(indicador):
        resumo_ano_rf = resumo_resultado_financeiro_por_grupo(base_kpi)
        periodo_cmp = comparar_mesmo_periodo(df, indicador, ano_kpi)

        if not periodo_cmp.empty and len(periodo_cmp) == 2:
            ytd_valor = periodo_cmp.iloc[1]["Resultado R$"]
            delta_ytd = periodo_cmp.iloc[1]["Variação Resultado"]
            periodo_label = periodo_cmp.iloc[1]["Período"]
        else:
            ytd_valor = None
            delta_ytd = None
            periodo_label = "-"

        c1, c2, c3, c4, c5 = st.columns(5)
        with c1:
            kpi_metric("Despesa Ano", fmt_brl(resumo_ano_rf["Despesa"]))
        with c2:
            kpi_metric("Receita Ano", fmt_brl(resumo_ano_rf["Receita"]))
        with c3:
            kpi_metric("Resultado Ano", fmt_brl(resumo_ano_rf["Resultado R$"]))
        with c4:
            kpi_metric("Resultado %", fmt_pct(resumo_ano_rf["Resultado %"]))
        with c5:
            kpi_metric(f"{rotulo_ytd_kpi(indicador)} {periodo_label}", fmt_brl(ytd_valor) if ytd_valor is not None else "-", delta_ytd)

        titulo_secao("Evolução mensal", "Acompanhamento visual do indicador ao longo do ano.")

        df_dashboard_ano = tabela_completa_ano(df, ano_kpi, indicador)
        dados_chart = df_dashboard_ano[df_dashboard_ano["Mês"] != "TOTAL"].dropna(subset=["Resultado R$"])

        if not dados_chart.empty:
            fig_dash = grafico_realizado_meta(df_dashboard_ano, ano_kpi, indicador=indicador)
            st.plotly_chart(fig_dash, use_container_width=True, key="grafico_dashboard_resultado_financeiro")

        titulo_secao("Comparativo ano a ano", "Resumo histórico consolidado do indicador selecionado.")
        df_yoy_dashboard = calcular_yoy(df, indicador)

        st.dataframe(
            df_yoy_dashboard[["ANO", "Meta %", "Despesa", "Receita", "Resultado R$", "Resultado %"]].style
            .format({
                "Meta %": lambda v: fmt_pct(v) if pd.notna(v) else "—",
                "Despesa": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                "Receita": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                "Resultado R$": lambda v: f"R$ {v:+,.0f}".replace(",", ".") if pd.notna(v) else "—",
                "Resultado %": lambda v: fmt_pct(v) if pd.notna(v) else "—",
            })
            .apply(cor_resultado_financeiro_por_linha, axis=1),
            use_container_width=True,
            hide_index=True,
        )

    elif eh_despesa_geral(indicador):
        resumo_ano_geral = resumo_despesa_geral_por_grupo(base_kpi)
        periodo_cmp = comparar_mesmo_periodo(df, indicador, ano_kpi)

        if not periodo_cmp.empty and len(periodo_cmp) == 2:
            ytd_valor = periodo_cmp.iloc[1]["Despesa"]
            delta_ytd = periodo_cmp.iloc[1]["Variação Despesa"]
            periodo_label = periodo_cmp.iloc[1]["Período"]
        else:
            ytd_valor = None
            delta_ytd = None
            periodo_label = "-"

        c1, c2, c3, c4, c5 = st.columns(5)
        with c1:
            kpi_metric("Despesa Ano", fmt_brl(resumo_ano_geral["Despesa"]))
        with c2:
            kpi_metric("Receita Ano", fmt_brl(resumo_ano_geral["Receita"]))
        with c3:
            kpi_metric("Resultado Ano", fmt_brl(resumo_ano_geral["Resultado R$"]))
        with c4:
            kpi_metric("Tx. Sucesso", fmt_pct(resumo_ano_geral["Tx. Sucesso"]))
        with c5:
            kpi_metric(f"{rotulo_ytd_kpi(indicador)} {periodo_label}", fmt_brl(ytd_valor) if ytd_valor is not None else "-", delta_ytd)

        titulo_secao("Evolução mensal", "Acompanhamento visual do indicador ao longo do ano.")

        df_dashboard_ano = tabela_completa_ano(df, ano_kpi, indicador)
        dados_chart = df_dashboard_ano[df_dashboard_ano["Mês"] != "TOTAL"].dropna(subset=["Despesa"])

        if not dados_chart.empty:
            fig_dash = grafico_realizado_meta(df_dashboard_ano, ano_kpi, indicador=indicador)
            st.plotly_chart(fig_dash, use_container_width=True, key="grafico_dashboard_despesa_geral")

        titulo_secao("Comparativo ano a ano", "Resumo histórico consolidado do indicador selecionado.")
        df_yoy_dashboard = calcular_yoy(df, indicador)

        st.dataframe(
            df_yoy_dashboard[["ANO", "Limite %", "Despesa", "Receita", "Resultado R$", "Tx. Sucesso"]].style
            .format({
                "Limite %": lambda v: fmt_pct(v) if pd.notna(v) else "—",
                "Despesa": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                "Receita": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                "Resultado R$": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                "Tx. Sucesso": lambda v: fmt_pct(v) if pd.notna(v) else "—",
            })
            .map(lambda v: cor_gap_valor(v, False), subset=["Resultado R$"])
            .apply(cor_tx_sucesso_despesa_geral_por_linha, axis=1),
            use_container_width=True,
            hide_index=True,
        )

    elif eh_despesa_hora_extra(indicador):
        periodo_cmp = comparar_mesmo_periodo(df, indicador, ano_kpi)

        limite_total = base_kpi["META_CALC"].sum()
        pago_total = base_kpi["REALIZADO_CALC"].sum()
        salario_total = base_kpi["SALARIO_CALC"].sum() if "SALARIO_CALC" in base_kpi.columns else 0
        resultado_pct_total = (pago_total / limite_total - 1) if limite_total > 0 else None

        if not periodo_cmp.empty and len(periodo_cmp) == 2:
            ytd_valor = periodo_cmp.iloc[1]["Realizado"]
            delta_ytd = periodo_cmp.iloc[1]["Variação Realizado"]
            periodo_label = periodo_cmp.iloc[1]["Período"]
        else:
            ytd_valor = None
            delta_ytd = None
            periodo_label = "-"

        c1, c2, c3, c4, c5 = st.columns(5)
        with c1:
            kpi_metric("Limite Ano", fmt_brl(limite_total))
        with c2:
            kpi_metric("Pago em Hora Extra Ano", fmt_brl(pago_total))
        with c3:
            kpi_metric("Salário Ano", fmt_brl(salario_total))
        with c4:
            kpi_metric("Result. %", fmt_pct(resultado_pct_total))
        with c5:
            kpi_metric(f"{rotulo_ytd_kpi(indicador)} {periodo_label}", fmt_brl(ytd_valor) if ytd_valor is not None else "-", delta_ytd)

        titulo_secao("Evolução mensal", "Acompanhamento visual do indicador ao longo do ano.")

        df_dashboard_ano = tabela_completa_ano(df, ano_kpi, indicador)
        dados_chart = df_dashboard_ano[df_dashboard_ano["Mês"] != "TOTAL"].dropna(subset=["Pago em Hora Extra"])

        if not dados_chart.empty:
            fig_dash = grafico_realizado_meta(df_dashboard_ano, ano_kpi, indicador=indicador)
            st.plotly_chart(fig_dash, use_container_width=True, key="grafico_dashboard_hora_extra")

        titulo_secao("Comparativo ano a ano", "Resumo histórico consolidado do indicador selecionado.")
        df_yoy_dashboard = calcular_yoy(df, indicador)

        st.dataframe(
            df_yoy_dashboard[["ANO", "Limite", "Pago em Hora Extra", "Salário", "HE da Folha", "Saldo do Limite", "Resultado %"]].style
            .format({
                "ANO": lambda v: f"{int(v)}" if pd.notna(v) else "",
                "Limite": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                "Pago em Hora Extra": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                "Salário": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                "HE da Folha": lambda v: fmt_pct(v) if pd.notna(v) else "—",
                "Saldo do Limite": lambda v: f"R$ {v:+,.0f}".replace(",", ".") if pd.notna(v) else "—",
                "Resultado %": lambda v: fmt_pct(v) if pd.notna(v) else "—",
            })
            .map(lambda v: cor_gap_valor(v, False), subset=["Saldo do Limite"])
            .map(cor_despesa_manutencao, subset=["Resultado %"]),
            use_container_width=True,
            hide_index=True,
        )

    else:
        periodo_cmp = comparar_mesmo_periodo(df, indicador, ano_kpi)
        realizado_total = base_kpi["REALIZADO_CALC"].sum()
        meta_total = base_kpi["META_CALC"].sum()

        if eh_despesa_manutencao(indicador):
            # Despesa Manutenção:
            # Resultado R$ = Limite - Despesa
            # Resultado % = 1 - (Despesa / Limite)
            gap_total = meta_total - realizado_total
            ating_total = 1 - (realizado_total / meta_total) if meta_total > 0 else None
        elif eh_despesa(indicador):
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
            kpi_metric(f"{rotulo_meta(indicador)} Ano", fmt_brl(meta_total))
        with c2:
            kpi_metric(f"{rotulo_realizado_kpi(indicador)} Ano", fmt_brl(realizado_total))
        with c3:
            kpi_metric(f"{rotulo_gap_kpi(indicador)} Ano", fmt_brl(gap_total))
        with c4:
            # Removido o "Esperado" do card conforme solicitado.
            # O card agora exibe apenas o percentual principal.
            kpi_metric(
                ("Resultado %" if eh_despesa_manutencao(indicador) else "Atingimento da meta"),
                fmt_pct(ating_total)
            )
        with c5:
            kpi_metric(f"{rotulo_ytd_kpi(indicador)} {periodo_label}", fmt_brl(realizado_ytd) if realizado_ytd is not None else "-", delta_ytd)

        titulo_secao("Evolução mensal", "Acompanhamento visual do indicador ao longo do ano.")

        df_dashboard_ano = tabela_completa_ano(df, ano_kpi, indicador)

        if eh_despesa_hora_extra(indicador):
            dados_chart = df_dashboard_ano[df_dashboard_ano["Mês"] != "TOTAL"].dropna(subset=["Pago em Hora Extra"])
        else:
            # Despesa Manutenção agora usa a estrutura:
            # Mês | Limite | Despesa | Result. R$ | Result. % | Acumulado
            # Portanto não existe mais a coluna "Realizado" nessa tabela.
            if eh_despesa_manutencao(indicador):
                dados_chart = df_dashboard_ano[df_dashboard_ano["Mês"] != "TOTAL"].dropna(subset=["Despesa"])
            elif eh_despesa_hora_extra(indicador):
                dados_chart = df_dashboard_ano[df_dashboard_ano["Mês"] != "TOTAL"].dropna(subset=["Pago em Hora Extra"])
            else:
                dados_chart = df_dashboard_ano[df_dashboard_ano["Mês"] != "TOTAL"].dropna(subset=["Realizado"])

        if not dados_chart.empty:
            ultimo_mes = dados_chart["Mês"].iloc[-1]
            fig_dash = grafico_realizado_meta(df_dashboard_ano, ano_kpi, indicador=indicador)
            st.plotly_chart(fig_dash, use_container_width=True, key="grafico_dashboard")

        titulo_secao("Comparativo ano a ano", "Resumo histórico consolidado do indicador selecionado.")
        df_yoy_dashboard = calcular_yoy(df, indicador)

        if eh_despesa_hora_extra(indicador):
            df_yoy_dashboard_exibir = df_yoy_dashboard[[
                "ANO",
                "Pago em Hora Extra",
                "Limite",
                "Salário",
                "HE da Folha",
                "Saldo do Limite",
                "Resultado %",
            ]].copy()

            st.dataframe(
                df_yoy_dashboard_exibir.style
                .format({
                    "Pago em Hora Extra": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                    "Limite": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                    "Salário": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                    "HE da Folha": lambda v: f"{v:.1%}" if pd.notna(v) else "—",
                    "Saldo do Limite": lambda v: f"R$ {v:+,.0f}".replace(",", ".") if pd.notna(v) else "—",
                    "Resultado %": lambda v: f"{v:.1%}" if pd.notna(v) else "—",
                })
                .map(lambda v: cor_gap_valor(v, False), subset=["Saldo do Limite"])
                .map(cor_despesa_manutencao, subset=["Resultado %"]),
                use_container_width=True,
                hide_index=True,
            )

        elif eh_despesa_manutencao(indicador):
            df_yoy_dashboard_exibir = df_yoy_dashboard.rename(columns={
                "Realizado": "Despesa",
                "Meta": "Limite",
                "Atingimento": "Uso do Limite",
            })[[
                "ANO",
                "Limite",
                "Despesa",
                "Uso do Limite",
            ]]

            st.dataframe(
                df_yoy_dashboard_exibir.style
                .format({
                    "Limite": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                    "Despesa": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                    "Uso do Limite": lambda v: fmt_pct(v) if pd.notna(v) else "—",
                })
                .map(cor_despesa_manutencao, subset=["Uso do Limite"]),
                use_container_width=True,
                hide_index=True,
            )

        else:
            df_yoy_dashboard_exibir = df_yoy_dashboard[[
                "ANO",
                "Meta",
                "Realizado",
                "Atingimento",
            ]].copy()

            st.dataframe(
                df_yoy_dashboard_exibir.style
                .format({
                    "Meta": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                    "Realizado": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                    "Atingimento": lambda v: fmt_pct(v) if pd.notna(v) else "—",
                })
                .map(cor_atingimento, subset=["Atingimento"]),
                use_container_width=True,
                hide_index=True,
            )


# =========================
# ABA PERÍODOS
# =========================
with tab1:
    titulo_secao("Períodos", f"{indicador} — {filial}: acompanhamento mensal, trimestral, semestral e anual com tabela detalhada e gráfico interativo.")

    anos = sorted(df["ANO"].dropna().unique())
    ano_selecionado = st.selectbox("Selecione o ano", anos, index=len(anos) - 1)

    modo_periodo = st.radio(
        "Tipo de visualização",
        ["Mensal", "Trimestre", "Semestre", "Ano Completo"],
        horizontal=True,
        key=f"modo_periodo_por_ano_{indicador}_{filial}_{ano_selecionado}",
        help="Use Trimestre, Semestre ou Ano Completo para visualizar o acumulado do período selecionado. As fórmulas não mudam; os valores apenas são acumulados."
    )

    if eh_moto_margem(indicador):
        df_completa = tabela_pneus_moto_ano(df, ano_selecionado)
    else:
        df_completa = tabela_completa_ano(df, ano_selecionado, indicador)
            # =========================
    # GRÁFICO DA ABA PERÍODOS CONFORME MODO SELECIONADO
    # =========================
    df_grafico_periodo = pd.DataFrame()
    titulo_grafico_periodo = ""

    if modo_periodo == "Mensal":
        df_grafico_periodo = df_completa.copy()
        titulo_grafico_periodo = f"Evolução mensal — {ano_selecionado}"

    elif modo_periodo in ["Trimestre", "Semestre", "Ano Completo"]:
        df_grafico_periodo = montar_resumo_periodo(df_completa, indicador, modo_periodo).copy()
        titulo_grafico_periodo = f"Resumo por {modo_periodo.lower()} — {ano_selecionado}"

    if modo_periodo in ["Trimestre", "Semestre", "Ano Completo"]:
        df_periodo_acumulado = montar_resumo_periodo(df_completa, indicador, modo_periodo)
        renderizar_tabela_periodo_acumulado(df_periodo_acumulado, indicador, modo_periodo)
        st.divider()

    if not df_grafico_periodo.empty:
        st.markdown("#### Gráfico do período selecionado")
        st.caption("O gráfico abaixo acompanha exatamente o tipo de visualização escolhido acima.")

        fig_periodo = grafico_realizado_meta(
            df_grafico_periodo,
            ano_selecionado,
            titulo=titulo_grafico_periodo,
            indicador=indicador
        )

        if fig_periodo is not None:
            st.plotly_chart(
                fig_periodo,
                use_container_width=True,
                key=f"grafico_periodos_{indicador}_{filial}_{ano_selecionado}_{modo_periodo}"
            )

        st.divider()

    if modo_periodo in ["Trimestre", "Semestre", "Ano Completo"]:
        st.markdown("#### Detalhamento mensal")
        st.caption("Abaixo permanece a abertura mensal original para conferência dos valores que formam o acumulado.")

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
        df_completa_tela = df_completa.copy()
        if eh_qualidade(indicador):
            rot = rotulos_qualidade(indicador)
            col_qtd_sucesso = rot["qtd_sucesso"]
            df_completa_tela = preparar_tabela_qualidade_exibicao(df_completa_tela, indicador)
            cols_exibir = [c for c in colunas_qualidade_exibicao(indicador) if c in df_completa_tela.columns]
            styler_q = (
                df_completa_tela[cols_exibir].style
                .format({
                    "Meta": lambda v: fmt_num(v) if modelo_qualidade(indicador) == "parametro_coleta_critico" and pd.notna(v) else (fmt_pct(v) if pd.notna(v) else ""),
                    "Qtd. Coletas": lambda v: fmt_num(v) if pd.notna(v) else "",
                    col_qtd_sucesso: lambda v: fmt_num(v) if pd.notna(v) else "",
                    "Diferença": lambda v: fmt_num(v) if pd.notna(v) else "",
                    "Resultado": lambda v: fmt_num(v) if pd.notna(v) else "",
                    "Resultado %": lambda v: fmt_pct(v) if pd.notna(v) else "",
                    "Acumulado": lambda v: fmt_num(v) if pd.notna(v) else "",
                })
                .apply(lambda row: cor_resultado_qualidade_por_linha(row, indicador), axis=1)
            )
            styler_q = aplicar_estilo_diferenca_qualidade(styler_q, indicador, subset=["Diferença", "Acumulado"])
            st.dataframe(
                styler_q,
                use_container_width=True,
                hide_index=True,
            )
        elif eh_tecfil(indicador):
            st.dataframe(
                df_completa_tela.style
                .format({
                    "Meta KG": lambda v: fmt_num(v) if pd.notna(v) else "",
                    "Meta R$": lambda v: fmt_brl(v) if pd.notna(v) else "",
                    "Realizado KG": lambda v: fmt_num(v) if pd.notna(v) else "",
                    "Realizado R$": lambda v: fmt_brl(v) if pd.notna(v) else "",
                    "% Dif. KG": lambda v: fmt_pct(v) if pd.notna(v) else "",
                    "% Dif. R$": lambda v: fmt_pct(v) if pd.notna(v) else "",
                    "Dif. KG": lambda v: fmt_num(v) if pd.notna(v) else "",
                    "Dif. R$": lambda v: fmt_brl(v) if pd.notna(v) else "",
                    "Acum. KG": lambda v: fmt_num(v) if pd.notna(v) else "",
                    "Acum. R$": lambda v: fmt_brl(v) if pd.notna(v) else "",
                })
                .map(cor_tecfil_resultado, subset=["% Dif. KG", "% Dif. R$", "Dif. KG", "Dif. R$", "Acum. KG", "Acum. R$"]),
                use_container_width=True,
                hide_index=True,
            )
        elif eh_resultado_financeiro(indicador):
            st.dataframe(
                df_completa_tela.style
                .format({
                    "Meta %": lambda v: fmt_pct(v) if pd.notna(v) else "",
                    "Despesa": lambda v: fmt_brl(v) if pd.notna(v) else "",
                    "Receita": lambda v: fmt_brl(v) if pd.notna(v) else "",
                    "Resultado R$": lambda v: f"R$ {v:+,.0f}".replace(",", ".") if pd.notna(v) else "",
                    "Resultado %": lambda v: f"{v:.1%}" if pd.notna(v) else "",
                    "Acumulado": lambda v: fmt_brl(v) if pd.notna(v) else "",
                })
                .apply(cor_resultado_financeiro_por_linha, axis=1),
                use_container_width=True,
                hide_index=True,
            )
        elif eh_despesa_geral(indicador):
            st.dataframe(
                df_completa_tela.style
                .format({
                    "Limite %": lambda v: fmt_pct(v) if pd.notna(v) else "",
                    "Despesa": lambda v: fmt_brl(v) if pd.notna(v) else "",
                    "Receita": lambda v: fmt_brl(v) if pd.notna(v) else "",
                    "Resultado R$": lambda v: f"R$ {v:+,.0f}".replace(",", ".") if pd.notna(v) else "",
                    "Tx. Sucesso": lambda v: f"{v:.1%}" if pd.notna(v) else "",
                    "Acumulado": lambda v: fmt_brl(v) if pd.notna(v) else "",
                })
                .map(lambda v: cor_gap_valor(v, False), subset=["Resultado R$"])
                .apply(cor_tx_sucesso_despesa_geral_por_linha, axis=1),
                use_container_width=True,
                hide_index=True,
            )
        elif eh_despesa_hora_extra(indicador):
            st.dataframe(
                df_completa_tela.style
                .format({
                    "Limite": lambda v: fmt_brl(v) if pd.notna(v) else "",
                    "Pago em Hora Extra": lambda v: fmt_brl(v) if pd.notna(v) else "",
                    "Salário": lambda v: fmt_brl(v) if pd.notna(v) else "",
                    "HE da Folha": lambda v: f"{v:.1%}" if pd.notna(v) else "",
                    "Saldo do Limite": lambda v: f"R$ {v:+,.0f}".replace(",", ".") if pd.notna(v) else "",
                    "Resultado %": lambda v: f"{v:.0%}" if pd.notna(v) else "",
                })
                .map(lambda v: cor_gap_valor(v, False), subset=["Saldo do Limite"])
                .map(cor_despesa_manutencao, subset=["Resultado %"]),
                use_container_width=True,
                hide_index=True,
            )
        elif eh_despesa_manutencao(indicador):
            # Estrutura solicitada para Despesa Manutenção:
            # Mês | Limite | Despesa | Result. R$ | Result. % | Acumulado
            cols_manutencao = ["Mês", "Limite", "Despesa", "Result. R$", "Result. %", "Acumulado"]
            cols_manutencao = [c for c in cols_manutencao if c in df_completa_tela.columns]

            def destacar_total_media_manutencao(row):
                nome = str(row.get("Mês", "")).upper()
                if nome.startswith("TOTAL") or nome.startswith("MÉDIA"):
                    return [
                        "background-color: #FFF3E8; font-weight: bold; border-top: 2px solid #F26522;"
                        for _ in row
                    ]
                return ["" for _ in row]

            st.dataframe(
                df_completa_tela[cols_manutencao].style
                .apply(destacar_total_media_manutencao, axis=1)
                .format({
                    "Limite": lambda v: fmt_brl(v) if pd.notna(v) else "",
                    "Despesa": lambda v: fmt_brl(v) if pd.notna(v) else "",
                    "Result. R$": lambda v: f"R$ {v:+,.0f}".replace(",", ".") if pd.notna(v) else "",
                    "Result. %": lambda v: f"{v:.1%}" if pd.notna(v) else "",
                    "Acumulado": lambda v: f"R$ {v:+,.0f}".replace(",", ".") if pd.notna(v) else "",
                })
                .map(lambda v: cor_gap_valor(v, False), subset=["Result. R$", "Acumulado"])
                .map(lambda v: f"color: {COR_VERDE}; font-weight:bold" if pd.notna(v) and v >= 0 else (f"color: {COR_LARANJA}; font-weight:bold" if pd.notna(v) else ""), subset=["Result. %"])
                ,
                use_container_width=True,
                hide_index=True,
            )
        else:
            if eh_faturamento_simples(indicador):
                # Faturamento: ordem solicitada pelo usuário
                # Mês | Meta | Faturamento | Atendimento da Meta
                df_faturamento_tela = df_completa_tela.rename(columns={
                    "Realizado": "Faturamento",
                    "Atingimento": "Atendimento da Meta",
                })

                cols_faturamento = [
                    "Mês",
                    "Meta",
                    "Faturamento",
                    "Atendimento da Meta",
                ]
                cols_faturamento = [c for c in cols_faturamento if c in df_faturamento_tela.columns]

                def destacar_total_faturamento(row):
                    if str(row.get("Mês", "")).upper().startswith("TOTAL"):
                        return [
                            "background-color: #FFF3E8; font-weight: bold; border-top: 2px solid #F26522;"
                            for _ in row
                        ]
                    return ["" for _ in row]

                st.dataframe(
                    df_faturamento_tela[cols_faturamento].style
                    .apply(destacar_total_faturamento, axis=1)
                    .format({
                        "Meta": lambda v: fmt_brl(v) if pd.notna(v) else "",
                        "Faturamento": lambda v: fmt_brl(v) if pd.notna(v) else "",
                        "Atendimento da Meta": lambda v: f"{v:.0%}" if pd.notna(v) else "",
                    })
                    .map(cor_atingimento, subset=["Atendimento da Meta"]),
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.dataframe(
                    df_completa_tela.style
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



# =========================
# ABA MOM
# =========================

with tab2:
    titulo_secao("MoM — Variação mês a mês", f"{indicador} — {filial}: leitura da evolução mensal e dos movimentos entre meses.")
    df_mom = calcular_mom(df, indicador).sort_values("MÊS_ORDEM").copy()

    if eh_moto_margem(indicador):
        linhas_finais = []

        for ano in sorted(df_mom["ANO"].dropna().unique()):
            base_ano = df_mom[df_mom["ANO"] == ano].copy()

            # Pneus Velhos Moto: anos anteriores a 2026 não têm meta.
            if int(ano) < 2026:
                base_ano["META"] = pd.NA

            linhas_finais.append(
                base_ano[["ANO", "MÊS", "Mês", "META", "Compra", "Realizado", "Margem Bruta", "Ating.", "Acumulado"]]
            )

            resumo_total = resumo_moto_por_grupo(df[df["ANO"] == ano])
            meta_total = resumo_total["Meta %"] if int(ano) >= 2026 else pd.NA

            linhas_finais.append(pd.DataFrame([{
                "ANO": ano,
                "MÊS": None,
                "Mês": f"TOTAL {ano}",
                "META": meta_total,
                "Compra": resumo_total["Compra"],
                "Realizado": resumo_total["Faturamento"],
                "Margem Bruta": resumo_total["Margem Bruta"],
                "Ating.": resumo_total["Tx. Sucesso"],
                "Acumulado": resumo_total["Margem Bruta"],
            }]))

        df_mom_tela = pd.concat(linhas_finais, ignore_index=True)

        def destacar_total(row):
            if str(row["Mês"]).startswith("TOTAL"):
                return ["background-color: #FFF3E8; font-weight: bold; border-top: 2px solid #F26522;" for _ in row]
            return ["" for _ in row]

        st.dataframe(
            df_mom_tela.rename(columns={
                "META": "Meta %",
                "Realizado": "Faturamento",
                "Ating.": "Tx. Sucesso"
            }).style
            .apply(destacar_total, axis=1)
            .format({
                "ANO": lambda v: f"{int(v)}" if pd.notna(v) else "",
                "MÊS": lambda v: f"{int(v)}" if pd.notna(v) else "",
                "Meta %": lambda v: fmt_pct(v) if pd.notna(v) else "—",
                "Compra": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                "Faturamento": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                "Margem Bruta": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                "Tx. Sucesso": lambda v: fmt_pct(v) if pd.notna(v) else "—",
                "Acumulado": lambda v: fmt_brl(v) if pd.notna(v) else "—",
            })
            .apply(cor_tx_sucesso_moto_por_linha, axis=1),
            use_container_width=True,
            hide_index=True,
        )

    elif eh_qualidade(indicador):
        linhas_finais_qualidade = []

        for ano_q in sorted(df_mom["ANO"].dropna().unique()):
            base_ano_q = df_mom[df_mom["ANO"] == ano_q].copy()

            linhas_finais_qualidade.append(
                base_ano_q[[
                    "ANO", "MÊS", "Mês", "Meta", "Qtd. Coletas", "Qtd. Sucesso",
                    "Diferença", "Resultado %", "Acumulado"
                ]]
            )

            resumo_total = resumo_qualidade_por_grupo(df[df["ANO"] == ano_q], indicador)

            linhas_finais_qualidade.append(pd.DataFrame([{
                "ANO": ano_q,
                "MÊS": None,
                "Mês": f"TOTAL {ano_q}",
                "Meta": resumo_total["Meta"],
                "Qtd. Coletas": resumo_total["Qtd. Coletas"],
                "Qtd. Sucesso": resumo_total["Qtd. Sucesso"],
                "Diferença": resumo_total["Diferença"],
                "Resultado %": resumo_total["Resultado %"],
                "Acumulado": resumo_total["Diferença"],
                            }]))

        df_mom_qualidade_tela = pd.concat(linhas_finais_qualidade, ignore_index=True)

        def destacar_total(row):
            if str(row["Mês"]).startswith("TOTAL"):
                return ["background-color: #FFF3E8; font-weight: bold; border-top: 2px solid #F26522;" for _ in row]
            return ["" for _ in row]

        rot = rotulos_qualidade(indicador)
        col_qtd_sucesso = rot["qtd_sucesso"]
        df_mom_qualidade_tela = preparar_tabela_qualidade_exibicao(df_mom_qualidade_tela, indicador)
        cols_exibir = [c for c in colunas_qualidade_exibicao(indicador, incluir_ano=True, incluir_mom=True, incluir_acumulado=False) if c in df_mom_qualidade_tela.columns]
        styler_mom_q = (
            df_mom_qualidade_tela[cols_exibir].style
            .apply(destacar_total, axis=1)
            .format({
                "ANO": lambda v: f"{int(v)}" if pd.notna(v) else "",
                "MÊS": lambda v: f"{int(v)}" if pd.notna(v) else "",
                "Meta": lambda v: fmt_num(v) if modelo_qualidade(indicador) == "parametro_coleta_critico" and pd.notna(v) else (fmt_pct(v) if pd.notna(v) else "—"),
                "Qtd. Coletas": lambda v: fmt_num(v) if pd.notna(v) else "—",
                col_qtd_sucesso: lambda v: fmt_num(v) if pd.notna(v) else "—",
                "Diferença": lambda v: fmt_num(v) if pd.notna(v) else "—",
                "Resultado": lambda v: fmt_num(v) if pd.notna(v) else "—",
                "Resultado %": lambda v: fmt_pct(v) if pd.notna(v) else "—",
                "Acumulado": lambda v: fmt_num(v) if pd.notna(v) else "—",
            })
            .apply(lambda row: cor_resultado_qualidade_por_linha(row, indicador), axis=1)
        )
        styler_mom_q = aplicar_estilo_diferenca_qualidade(styler_mom_q, indicador, subset=["Diferença", "Acumulado"])
        st.dataframe(
            styler_mom_q,
            use_container_width=True,
            hide_index=True,
        )

    elif eh_tecfil(indicador):
        linhas_finais_tecfil = []

        for ano_tecfil in sorted(df_mom["ANO"].dropna().unique()):
            base_ano_tecfil = df_mom[df_mom["ANO"] == ano_tecfil].copy()

            linhas_finais_tecfil.append(
                base_ano_tecfil[[
                    "ANO", "MÊS", "Mês", "Meta KG", "Meta R$", "Realizado KG", "Realizado R$",
                    "% Dif. KG", "% Dif. R$", "Dif. KG", "Dif. R$", "Acum. KG", "Acum. R$"
                ]]
            )

            resumo_total = resumo_tecfil_por_grupo(df[df["ANO"] == ano_tecfil])

            linhas_finais_tecfil.append(pd.DataFrame([{
                "ANO": ano_tecfil,
                "MÊS": None,
                "Mês": f"TOTAL {ano_tecfil}",
                "Meta KG": resumo_total["Meta KG"],
                "Meta R$": resumo_total["Meta R$"],
                "Realizado KG": resumo_total["Realizado KG"],
                "Realizado R$": resumo_total["Realizado R$"],
                "% Dif. KG": resumo_total["% Dif. KG"],
                "% Dif. R$": resumo_total["% Dif. R$"],
                "Dif. KG": resumo_total["Dif. KG"],
                "Dif. R$": resumo_total["Dif. R$"],
                "Acum. KG": resumo_total["Dif. KG"],
                "Acum. R$": resumo_total["Dif. R$"],
                            }]))

        df_mom_tecfil_tela = pd.concat(linhas_finais_tecfil, ignore_index=True)

        def destacar_total(row):
            if str(row["Mês"]).startswith("TOTAL"):
                return ["background-color: #FFF3E8; font-weight: bold; border-top: 2px solid #F26522;" for _ in row]
            return ["" for _ in row]

        st.dataframe(
            df_mom_tecfil_tela.style
            .apply(destacar_total, axis=1)
            .format({
                "ANO": lambda v: f"{int(v)}" if pd.notna(v) else "",
                "MÊS": lambda v: f"{int(v)}" if pd.notna(v) else "",
                "Meta KG": lambda v: fmt_num(v) if pd.notna(v) else "—",
                "Meta R$": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                "Realizado KG": lambda v: fmt_num(v) if pd.notna(v) else "—",
                "Realizado R$": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                "% Dif. KG": lambda v: fmt_pct(v) if pd.notna(v) else "—",
                "% Dif. R$": lambda v: fmt_pct(v) if pd.notna(v) else "—",
                "Dif. KG": lambda v: fmt_num(v) if pd.notna(v) else "—",
                "Dif. R$": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                "Acum. KG": lambda v: fmt_num(v) if pd.notna(v) else "—",
                "Acum. R$": lambda v: fmt_brl(v) if pd.notna(v) else "—",
            })
            .map(cor_tecfil_resultado, subset=["% Dif. KG", "% Dif. R$", "Dif. KG", "Dif. R$", "Acum. KG", "Acum. R$"]),
            use_container_width=True,
            hide_index=True,
        )

    elif eh_resultado_financeiro(indicador):
        linhas_finais_rf = []

        for ano_rf in sorted(df_mom["ANO"].dropna().unique()):
            base_ano_rf = df_mom[df_mom["ANO"] == ano_rf].copy()

            linhas_finais_rf.append(
                base_ano_rf[[
                    "ANO", "MÊS", "Mês", "Meta %", "Despesa", "Receita",
                    "Resultado R$", "Resultado %", "Acumulado"
                ]]
            )

            resumo_total = resumo_resultado_financeiro_por_grupo(df[df["ANO"] == ano_rf])

            linhas_finais_rf.append(pd.DataFrame([{
                "ANO": ano_rf,
                "MÊS": None,
                "Mês": f"TOTAL {ano_rf}",
                "Meta %": resumo_total["Meta %"],
                "Despesa": resumo_total["Despesa"],
                "Receita": resumo_total["Receita"],
                "Resultado R$": resumo_total["Resultado R$"],
                "Resultado %": resumo_total["Resultado %"],
                "Acumulado": resumo_total["Resultado R$"],
                            }]))

        df_mom_rf_tela = pd.concat(linhas_finais_rf, ignore_index=True)

        def destacar_total(row):
            if str(row["Mês"]).startswith("TOTAL"):
                return ["background-color: #FFF3E8; font-weight: bold; border-top: 2px solid #F26522;" for _ in row]
            return ["" for _ in row]

        st.dataframe(
            df_mom_rf_tela.style
            .apply(destacar_total, axis=1)
            .format({
                "ANO": lambda v: f"{int(v)}" if pd.notna(v) else "",
                "MÊS": lambda v: f"{int(v)}" if pd.notna(v) else "",
                "Meta %": lambda v: fmt_pct(v) if pd.notna(v) else "—",
                "Despesa": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                "Receita": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                "Resultado R$": lambda v: f"R$ {v:+,.0f}".replace(",", ".") if pd.notna(v) else "—",
                "Resultado %": lambda v: f"{v:.1%}" if pd.notna(v) else "—",
                "Acumulado": lambda v: fmt_brl(v) if pd.notna(v) else "—",
            })
            .apply(cor_resultado_financeiro_por_linha, axis=1),
            use_container_width=True,
            hide_index=True,
        )

    elif eh_despesa_geral(indicador):
        linhas_finais_geral = []

        for ano_geral in sorted(df_mom["ANO"].dropna().unique()):
            base_ano_geral = df_mom[df_mom["ANO"] == ano_geral].copy()

            linhas_finais_geral.append(
                base_ano_geral[[
                    "ANO", "MÊS", "Mês", "Limite %", "Despesa", "Receita",
                    "Resultado R$", "Tx. Sucesso", "Acumulado"
                ]]
            )

            resumo_total = resumo_despesa_geral_por_grupo(df[df["ANO"] == ano_geral])

            linhas_finais_geral.append(pd.DataFrame([{
                "ANO": ano_geral,
                "MÊS": None,
                "Mês": f"TOTAL {ano_geral}",
                "Limite %": resumo_total["Limite %"],
                "Despesa": resumo_total["Despesa"],
                "Receita": resumo_total["Receita"],
                "Resultado R$": resumo_total["Resultado R$"],
                "Tx. Sucesso": resumo_total["Tx. Sucesso"],
                "Acumulado": resumo_total["Resultado R$"],
                            }]))

        df_mom_geral_tela = pd.concat(linhas_finais_geral, ignore_index=True)

        def destacar_total(row):
            if str(row["Mês"]).startswith("TOTAL"):
                return ["background-color: #FFF3E8; font-weight: bold; border-top: 2px solid #F26522;" for _ in row]
            return ["" for _ in row]

        st.dataframe(
            df_mom_geral_tela.style
            .apply(destacar_total, axis=1)
            .format({
                "ANO": lambda v: f"{int(v)}" if pd.notna(v) else "",
                "MÊS": lambda v: f"{int(v)}" if pd.notna(v) else "",
                "Limite %": lambda v: fmt_pct(v) if pd.notna(v) else "—",
                "Despesa": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                "Receita": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                "Resultado R$": lambda v: f"R$ {v:+,.0f}".replace(",", ".") if pd.notna(v) else "—",
                "Tx. Sucesso": lambda v: f"{v:.1%}" if pd.notna(v) else "—",
                "Acumulado": lambda v: fmt_brl(v) if pd.notna(v) else "—",
            })
            .map(lambda v: cor_gap_valor(v, False), subset=["Resultado R$"])
            .apply(cor_tx_sucesso_despesa_geral_por_linha, axis=1),
            use_container_width=True,
            hide_index=True,
        )

    elif eh_despesa_hora_extra(indicador):
        linhas_finais_he = []

        for ano_he in sorted(df_mom["ANO"].dropna().unique()):
            base_ano_he = df_mom[df_mom["ANO"] == ano_he].copy()

            linhas_finais_he.append(
                base_ano_he[[
                    "ANO", "MÊS", "Mês", "Limite", "Pago em Hora Extra",
                    "Salário", "HE da Folha", "Saldo do Limite", "Resultado %"
                ]]
            )

            total_limite = base_ano_he["Limite"].sum()
            total_pago = base_ano_he["Pago em Hora Extra"].sum()
            total_salario = base_ano_he["Salário"].sum()
            total_he_folha = total_pago / total_salario if total_salario > 0 else None
            total_saldo = total_limite - total_pago
            # Fórmula conforme planilha:
            # Result.% = (Pago em Hora Extra / Limite) - 1
            total_resultado = (total_pago / total_limite - 1) if total_limite > 0 else None

            linhas_finais_he.append(pd.DataFrame([{
                "ANO": ano_he,
                "MÊS": None,
                "Mês": f"TOTAL {ano_he}",
                "Limite": total_limite,
                "Pago em Hora Extra": total_pago,
                "Salário": total_salario,
                "HE da Folha": total_he_folha,
                "Saldo do Limite": total_saldo,
                "Resultado %": total_resultado,
                            }]))

        df_mom_he_tela = pd.concat(linhas_finais_he, ignore_index=True)

        def destacar_total(row):
            if str(row["Mês"]).startswith("TOTAL"):
                return ["background-color: #FFF3E8; font-weight: bold; border-top: 2px solid #F26522;" for _ in row]
            return ["" for _ in row]

        st.dataframe(
            df_mom_he_tela.style
            .apply(destacar_total, axis=1)
            .format({
                "ANO": lambda v: f"{int(v)}" if pd.notna(v) else "",
                "MÊS": lambda v: f"{int(v)}" if pd.notna(v) else "",
                "Limite": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                "Pago em Hora Extra": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                "Salário": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                "HE da Folha": lambda v: f"{v:.1%}" if pd.notna(v) else "—",
                "Saldo do Limite": lambda v: f"R$ {v:+,.0f}".replace(",", ".") if pd.notna(v) else "—",
                "Resultado %": lambda v: f"{v:.0%}" if pd.notna(v) else "—",
            })
            .map(lambda v: cor_gap_valor(v, False), subset=["Saldo do Limite"])
            .map(cor_despesa_manutencao, subset=["Resultado %"]),
            use_container_width=True,
            hide_index=True,
        )

    else:
        linhas_finais = []

        for ano in sorted(df_mom["ANO"].dropna().unique()):
            base_ano = df_mom[df_mom["ANO"] == ano].copy()

            linhas_finais.append(base_ano[["ANO", "MÊS", "Mês", "META", "Realizado", "Gap", "Ating."]])

            total_meta = base_ano["META"].sum()
            total_realizado = base_ano["Realizado"].sum()

            if eh_despesa_manutencao(indicador):
                # Despesa Manutenção:
                # Resultado R$ = Limite - Despesa
                # Resultado % = 1 - (Despesa / Limite)
                total_gap = total_meta - total_realizado
                total_ating = 1 - (total_realizado / total_meta) if total_meta > 0 else None

            elif eh_despesa(indicador):
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
                            }]))

        df_mom_tela = pd.concat(linhas_finais, ignore_index=True)

        def destacar_total(row):
            if str(row["Mês"]).startswith("TOTAL"):
                return ["background-color: #FFF3E8; font-weight: bold; border-top: 2px solid #F26522;" for _ in row]
            return ["" for _ in row]

        if eh_despesa_manutencao(indicador):
            df_mom_tela_exibir = df_mom_tela.rename(columns={
                "META": "Limite",
                "Realizado": "Despesa",
                "Gap": "Resultado R$",
                "Ating.": "Resultado %",
            })

            st.dataframe(
                df_mom_tela_exibir.style
                .apply(destacar_total, axis=1)
                .format({
                    "ANO": lambda v: f"{int(v)}" if pd.notna(v) else "",
                    "MÊS": lambda v: f"{int(v)}" if pd.notna(v) else "",
                    "Limite": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                    "Despesa": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                    "Resultado R$": lambda v: f"R$ {v:+,.0f}".replace(",", ".") if pd.notna(v) else "—",
                    "Resultado %": lambda v: f"{v:.1%}" if pd.notna(v) else "—",
                    })
                .map(lambda v: cor_gap_valor(v, False), subset=["Resultado R$"])
                .map(lambda v: f"color: {COR_VERDE}; font-weight:bold" if pd.notna(v) and v >= 0 else (f"color: {COR_LARANJA}; font-weight:bold" if pd.notna(v) else ""), subset=["Resultado %"]),
                use_container_width=True,
                hide_index=True,
            )

        else:
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
                    })
                .map(lambda v: cor_gap_valor(v, eh_despesa(indicador)), subset=["Gap"])
                .map(cor_atingimento, subset=["Ating."]),
                use_container_width=True,
                hide_index=True,
            )



# =========================
# ABA YOY
# =========================
with tab3:
    titulo_secao("YoY — Comparativo ano a ano", f"{indicador} — {filial}: visão histórica consolidada e comparativo por ano.")
    df_yoy = calcular_yoy(df, indicador)

    if eh_moto_margem(indicador):
        st.dataframe(
            df_yoy[["ANO", "Faturamento", "Compra", "Margem Bruta", "Meta %", "Meta Margem (R$)", "Tx. Sucesso"]].style
            .format({
                "Faturamento": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                "Compra": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                "Margem Bruta": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                "Meta %": lambda v: fmt_pct(v) if pd.notna(v) else "—",
                "Meta Margem (R$)": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                "Tx. Sucesso": lambda v: fmt_pct(v) if pd.notna(v) else "—",
            })
            .apply(cor_tx_sucesso_moto_por_linha, axis=1),
            use_container_width=True,
            hide_index=True,
        )

        if eh_moto_margem(indicador):
            titulo_secao("Mesmo período x ano anterior", "Comparativo acumulado de Faturamento, Compra, Margem Bruta e Tx. Sucesso contra o mesmo período do ano anterior.")
        elif eh_resultado_financeiro(indicador):
            titulo_secao("Mesmo período x ano anterior", "Comparativo acumulado do Resultado Financeiro em R$, Receita, Despesa, Resultado % e Meta % contra o mesmo período do ano anterior.")
        elif eh_despesa_geral(indicador):
            titulo_secao("Mesmo período x ano anterior", "Comparativo acumulado do resultado operacional, receita, despesa e taxa de sucesso contra o mesmo período do ano anterior.")
        else:
            titulo_secao("Mesmo período x ano anterior", "Comparativo acumulado do período atual contra o mesmo período do ano anterior.")
        df_periodo_tela = comparar_mesmo_periodo(df, indicador)
        cards_comparativo_periodo(df_periodo_tela, indicador)

        if not df_periodo_tela.empty:
            if eh_moto_margem(indicador):
                # Após a limpeza do comparativo de Pneus Moto, algumas colunas como
                # "Meta Margem (R$)" e "Gap (R$)" podem não existir mais.
                # Por isso, exibimos apenas as colunas disponíveis.
                cols_periodo_moto = [
                    "Ano",
                    "Período",
                    "Faturamento",
                    "Compra",
                    "Margem Bruta",
                    "Meta %",
                    "Tx. Sucesso",
                    "Variação Faturamento",
                    "Variação Margem",
                ]
                cols_periodo_moto = [c for c in cols_periodo_moto if c in df_periodo_tela.columns]

                fmt_periodo_moto = {
                    "Faturamento": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                    "Compra": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                    "Margem Bruta": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                    "Meta %": lambda v: fmt_pct(v) if pd.notna(v) else "—",
                    "Tx. Sucesso": lambda v: fmt_pct(v) if pd.notna(v) else "—",
                    "Variação Faturamento": lambda v: fmt_var_pct_seguro(v) if pd.notna(v) else "—",
                    "Variação Margem": lambda v: fmt_var_pct_seguro(v) if pd.notna(v) else "—",
                }
                fmt_periodo_moto = {k: v for k, v in fmt_periodo_moto.items() if k in cols_periodo_moto}

                st.dataframe(
                    df_periodo_tela[cols_periodo_moto].style
                    .format(fmt_periodo_moto)
                    .apply(cor_tx_sucesso_moto_por_linha, axis=1),
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.dataframe(
                    df_periodo_tela.style
                    .format({
                        "Realizado": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                        "Meta": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                        "Gap (R$)": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                        "Atingimento": lambda v: fmt_pct(v) if pd.notna(v) else "—",
                        "Variação Realizado": lambda v: fmt_var_pct_seguro(v) if pd.notna(v) else "—",
                        "Variação Meta": lambda v: fmt_var_pct_seguro(v) if pd.notna(v) else "—",
                        "Despesa": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                        "Receita": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                        "Resultado R$": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                        "Resultado %": lambda v: fmt_pct(v) if pd.notna(v) else "—",
                        "Meta %": lambda v: fmt_pct(v) if pd.notna(v) else "—",
                    }),
                    use_container_width=True,
                    hide_index=True,
                )
        else:
            st.info("Sem dados suficientes para comparar o mesmo período.")

        if filial == "Geral":
            st.divider()
            anos_filial = (
                sorted(df_comparativo_filiais["ANO"].dropna().unique())
                if not df_comparativo_filiais.empty and "ANO" in df_comparativo_filiais.columns
                else []
            )
            if not anos_filial:
                st.info("Sem dados das filiais reais para montar o comparativo entre filiais.")
                st.stop()

            ano_base_filial = st.selectbox("Ano do comparativo", anos_filial, index=len(anos_filial) - 1, key="ano_filiais")
            titulo_secao(
                titulo_comparativo_filiais(indicador, ano_base_filial),
                f"Ano base: {ano_base_filial}. Comparação adaptada à estrutura do indicador selecionado."
            )

            comp_filiais = comparativo_filiais(df_comparativo_filiais, ano_base_filial, indicador)
            st.dataframe(
                comp_filiais[["FILIAL", "Faturamento", "Compra", "Margem Bruta", "Meta %", "Meta Margem (R$)", "Tx. Sucesso"]].style
                .format({
                    "Faturamento": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                    "Compra": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                    "Margem Bruta": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                    "Meta %": lambda v: fmt_pct(v) if pd.notna(v) else "—",
                    "Meta Margem (R$)": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                    "Tx. Sucesso": lambda v: fmt_pct(v) if pd.notna(v) else "—",
                })
                .apply(cor_tx_sucesso_moto_por_linha, axis=1),
                use_container_width=True,
                hide_index=True,
            )

            fig_filiais = go.Figure()

            cores_tx = [
                COR_VERDE if pd.notna(tx) and pd.notna(meta) and tx >= meta else COR_LARANJA
                for tx, meta in zip(comp_filiais["Tx. Sucesso"], comp_filiais["Meta %"])
            ]

            fig_filiais.add_bar(
                x=comp_filiais["FILIAL"],
                y=comp_filiais["Tx. Sucesso"],
                name="Tx. Sucesso",
                marker_color=cores_tx,
                text=[fmt_pct(v) for v in comp_filiais["Tx. Sucesso"]],
                textposition="outside",
            )

            fig_filiais.add_scatter(
                x=comp_filiais["FILIAL"],
                y=comp_filiais["Meta %"],
                name="Meta %",
                mode="lines+markers",
                line=dict(color=COR_AZUL, width=3, dash="dot"),
            )

            fig_filiais.update_layout(
                height=420,
                margin=dict(t=30, b=20, l=20, r=20),
                legend=dict(orientation="h", y=-0.15),
                yaxis_title="%",
                yaxis_tickformat=".0%",
            )

            st.plotly_chart(fig_filiais, use_container_width=True, key="grafico_filiais_moto")

    else:
        if eh_qualidade(indicador):
            st.dataframe(
                df_yoy[["ANO", "Meta", "Qtd. Coletas", "Qtd. Sucesso", "Diferença", "Resultado %"]].style
                .format({
                    "Meta": lambda v: fmt_num(v) if modelo_qualidade(indicador) == "parametro_coleta_critico" and pd.notna(v) else (fmt_pct(v) if pd.notna(v) else "—"),
                    "Qtd. Coletas": lambda v: fmt_num(v) if pd.notna(v) else "—",
                    "Qtd. Sucesso": lambda v: fmt_num(v) if pd.notna(v) else "—",
                    "Diferença": lambda v: fmt_num(v) if pd.notna(v) else "—",
                "Resultado": lambda v: fmt_num(v) if pd.notna(v) else "—",
                    "Resultado %": lambda v: fmt_pct(v) if pd.notna(v) else "—",
                    })
                .apply(lambda row: cor_resultado_qualidade_por_linha(row, indicador), axis=1)
                .map(cor_diferenca_qualidade, subset=["Diferença"]),
                use_container_width=True,
                hide_index=True,
            )

        elif eh_tecfil(indicador):
            st.dataframe(
                df_yoy[["ANO", "Meta KG", "Meta R$", "Realizado KG", "Realizado R$", "% Dif. KG", "% Dif. R$", "Dif. KG", "Dif. R$"]].style
                .format({
                    "Meta KG": lambda v: fmt_num(v) if pd.notna(v) else "—",
                    "Meta R$": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                    "Realizado KG": lambda v: fmt_num(v) if pd.notna(v) else "—",
                    "Realizado R$": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                    "% Dif. KG": lambda v: fmt_pct(v) if pd.notna(v) else "—",
                    "% Dif. R$": lambda v: fmt_pct(v) if pd.notna(v) else "—",
                    "Dif. KG": lambda v: fmt_num(v) if pd.notna(v) else "—",
                    "Dif. R$": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                    })
                .map(cor_tecfil_resultado, subset=["% Dif. KG", "% Dif. R$", "Dif. KG", "Dif. R$"]),
                use_container_width=True,
                hide_index=True,
            )

        elif eh_resultado_financeiro(indicador):
            st.dataframe(
                df_yoy[["ANO", "Meta %", "Despesa", "Receita", "Resultado R$", "Resultado %"]].style
                .format({
                    "Meta %": lambda v: fmt_pct(v) if pd.notna(v) else "—",
                    "Despesa": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                    "Receita": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                    "Resultado R$": lambda v: f"R$ {v:+,.0f}".replace(",", ".") if pd.notna(v) else "—",
                    "Resultado %": lambda v: f"{v:.1%}" if pd.notna(v) else "—",
                    })
                .apply(cor_resultado_financeiro_por_linha, axis=1),
                use_container_width=True,
                hide_index=True,
            )

        elif eh_despesa_geral(indicador):
            st.dataframe(
                df_yoy[["ANO", "Limite %", "Despesa", "Receita", "Resultado R$", "Tx. Sucesso"]].style
                .format({
                    "Limite %": lambda v: fmt_pct(v) if pd.notna(v) else "—",
                    "Despesa": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                    "Receita": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                    "Resultado R$": lambda v: f"R$ {v:+,.0f}".replace(",", ".") if pd.notna(v) else "—",
                    "Tx. Sucesso": lambda v: f"{v:.1%}" if pd.notna(v) else "—",
                    })
                .map(lambda v: cor_gap_valor(v, False), subset=["Resultado R$"])
                .apply(cor_tx_sucesso_despesa_geral_por_linha, axis=1),
                use_container_width=True,
                hide_index=True,
            )

        elif eh_despesa_hora_extra(indicador):
            df_yoy_exibir = df_yoy[[
                "ANO",
                "Pago em Hora Extra",
                "Limite",
                "Salário",
                "HE da Folha",
                "Saldo do Limite",
                "Resultado %",
            ]].copy()

            st.dataframe(
                df_yoy_exibir.style
                .format({
                    "Pago em Hora Extra": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                    "Limite": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                    "Salário": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                        "HE da Folha": lambda v: f"{v:.1%}" if pd.notna(v) else "—",
                    "Saldo do Limite": lambda v: f"R$ {v:+,.0f}".replace(",", ".") if pd.notna(v) else "—",
                    "Resultado %": lambda v: f"{v:.1%}" if pd.notna(v) else "—",
                    })
                .map(lambda v: cor_gap_valor(v, False), subset=["Saldo do Limite"])
                .map(cor_despesa_manutencao, subset=["Resultado %"]),
                use_container_width=True,
                hide_index=True,
            )

        elif eh_despesa_manutencao(indicador):
            df_yoy_exibir = df_yoy.rename(columns={
                "Realizado": "Despesa",
                "Meta": "Limite",
                "Atingimento": "Uso do Limite",
            })[[
                "ANO",
                "Limite",
                "Despesa",
                "Uso do Limite",
            ]]
            st.dataframe(
                df_yoy_exibir.style
                .format({
                    "Limite": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                    "Despesa": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                    "Uso do Limite": lambda v: fmt_pct(v) if pd.notna(v) else "—",
                        })
                .map(cor_despesa_manutencao, subset=["Uso do Limite"]),
                use_container_width=True,
                hide_index=True,
            )
        else:
            df_yoy_exibir = df_yoy[[
                "ANO",
                "Meta",
                "Realizado",
                "Atingimento",
            ]].copy()

            st.dataframe(
                df_yoy_exibir.style
                .format({
                    "Meta": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                    "Realizado": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                    "Atingimento": lambda v: fmt_pct(v) if pd.notna(v) else "—",
                        })
                .map(cor_atingimento, subset=["Atingimento"]),
                use_container_width=True,
                hide_index=True,
            )

        if eh_moto_margem(indicador):
            titulo_secao("Mesmo período x ano anterior", "Comparativo acumulado de Faturamento, Compra, Margem Bruta e Tx. Sucesso contra o mesmo período do ano anterior.")
        else:
            titulo_secao("Mesmo período x ano anterior", "Comparativo acumulado do período atual contra o mesmo período do ano anterior.")
        df_periodo_tela = comparar_mesmo_periodo(df, indicador)
        cards_comparativo_periodo(df_periodo_tela, indicador)

        if not df_periodo_tela.empty:
            if eh_qualidade(indicador):
                st.dataframe(
                    df_periodo_tela[["Ano", "Período", "Meta", "Qtd. Coletas", "Qtd. Sucesso", "Diferença", "Resultado %", "Variação Resultado"]].style
                    .format({
                        "Meta": lambda v: fmt_num(v) if modelo_qualidade(indicador) == "parametro_coleta_critico" and pd.notna(v) else (fmt_pct(v) if pd.notna(v) else "—"),
                        "Qtd. Coletas": lambda v: fmt_num(v) if pd.notna(v) else "—",
                        "Qtd. Sucesso": lambda v: fmt_num(v) if pd.notna(v) else "—",
                        "Diferença": lambda v: fmt_num(v) if pd.notna(v) else "—",
                "Resultado": lambda v: fmt_num(v) if pd.notna(v) else "—",
                        "Resultado %": lambda v: fmt_pct(v) if pd.notna(v) else "—",
                        "Variação Resultado": fmt_var_pct_seguro,
                    })
                    .apply(lambda row: cor_resultado_qualidade_por_linha(row, indicador), axis=1)
                    .map(cor_diferenca_qualidade, subset=["Diferença"])
                    .map(cor_variacao, subset=["Variação Resultado"]),
                    use_container_width=True,
                    hide_index=True,
                )
            elif eh_tecfil(indicador):
                st.dataframe(
                    df_periodo_tela[["Ano", "Período", "Meta KG", "Meta R$", "Realizado KG", "Realizado R$", "% Dif. KG", "% Dif. R$", "Dif. KG", "Dif. R$", "Variação KG", "Variação R$"]].style
                    .format({
                        "Meta KG": lambda v: fmt_num(v) if pd.notna(v) else "—",
                        "Meta R$": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                        "Realizado KG": lambda v: fmt_num(v) if pd.notna(v) else "—",
                        "Realizado R$": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                        "% Dif. KG": lambda v: fmt_pct(v) if pd.notna(v) else "—",
                        "% Dif. R$": lambda v: fmt_pct(v) if pd.notna(v) else "—",
                        "Dif. KG": lambda v: fmt_num(v) if pd.notna(v) else "—",
                        "Dif. R$": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                        "Variação KG": fmt_var_pct_seguro,
                        "Variação R$": fmt_var_pct_seguro,
                    })
                    .map(cor_tecfil_resultado, subset=["% Dif. KG", "% Dif. R$", "Dif. KG", "Dif. R$"])
                    .map(cor_variacao, subset=["Variação KG", "Variação R$"]),
                    use_container_width=True,
                    hide_index=True,
                )
            elif eh_resultado_financeiro(indicador):
                st.dataframe(
                    df_periodo_tela[["Ano", "Período", "Meta %", "Despesa", "Receita", "Resultado R$", "Resultado %", "Variação Resultado", "Variação Receita"]].style
                    .format({
                        "Meta %": lambda v: fmt_pct(v) if pd.notna(v) else "—",
                        "Despesa": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                        "Receita": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                        "Resultado R$": lambda v: f"R$ {v:+,.0f}".replace(",", ".") if pd.notna(v) else "—",
                        "Resultado %": lambda v: f"{v:.1%}" if pd.notna(v) else "—",
                        "Variação Resultado": fmt_var_pct_seguro,
                        "Variação Receita": lambda v: f"{v:+.1%}" if pd.notna(v) else "—",
                    })
                    .apply(cor_resultado_financeiro_por_linha, axis=1)
                    .map(cor_variacao, subset=["Variação Resultado", "Variação Receita"]),
                    use_container_width=True,
                    hide_index=True,
                )
            elif eh_despesa_geral(indicador):
                st.dataframe(
                    df_periodo_tela[["Ano", "Período", "Limite %", "Despesa", "Receita", "Resultado R$", "Tx. Sucesso", "Variação Despesa", "Variação Receita"]].style
                    .format({
                        "Limite %": lambda v: fmt_pct(v) if pd.notna(v) else "—",
                        "Despesa": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                        "Receita": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                        "Resultado R$": lambda v: f"R$ {v:+,.0f}".replace(",", ".") if pd.notna(v) else "—",
                        "Tx. Sucesso": lambda v: f"{v:.1%}" if pd.notna(v) else "—",
                        "Variação Despesa": lambda v: f"{v:+.1%}" if pd.notna(v) else "—",
                        "Variação Receita": lambda v: f"{v:+.1%}" if pd.notna(v) else "—",
                    })
                    .map(lambda v: cor_gap_valor(v, False), subset=["Resultado R$"])
                    .apply(cor_tx_sucesso_despesa_geral_por_linha, axis=1)
                    .map(cor_variacao, subset=["Variação Despesa", "Variação Receita"]),
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.dataframe(
                    formatar_df_periodo_tela_seguro(df_periodo_tela),
                    use_container_width=True,
                    hide_index=True,
                )
        else:
            st.info("Sem dados suficientes para comparar o mesmo período.")

        if filial == "Geral":
            st.divider()
            anos_filial = (
                sorted(df_comparativo_filiais["ANO"].dropna().unique())
                if not df_comparativo_filiais.empty and "ANO" in df_comparativo_filiais.columns
                else []
            )
            if not anos_filial:
                st.info("Sem dados das filiais reais para montar o comparativo entre filiais.")
                st.stop()

            ano_base_filial = st.selectbox("Ano do comparativo", anos_filial, index=len(anos_filial) - 1, key="ano_filiais")
            titulo_secao(
                titulo_comparativo_filiais(indicador, ano_base_filial),
                f"Ano base: {ano_base_filial}. Comparação adaptada à estrutura do indicador selecionado."
            )

            comp_filiais = comparativo_filiais(df_comparativo_filiais, ano_base_filial, indicador)

            if eh_qualidade(indicador):
                st.dataframe(
                    comp_filiais[["FILIAL", "Meta", "Qtd. Coletas", "Qtd. Sucesso", "Diferença", "Resultado %"]].style
                    .format({
                        "Meta": lambda v: fmt_num(v) if modelo_qualidade(indicador) == "parametro_coleta_critico" and pd.notna(v) else (fmt_pct(v) if pd.notna(v) else "—"),
                        "Qtd. Coletas": lambda v: fmt_num(v) if pd.notna(v) else "—",
                        "Qtd. Sucesso": lambda v: fmt_num(v) if pd.notna(v) else "—",
                        "Diferença": lambda v: fmt_num(v) if pd.notna(v) else "—",
                "Resultado": lambda v: fmt_num(v) if pd.notna(v) else "—",
                        "Resultado %": lambda v: fmt_pct(v) if pd.notna(v) else "—",
                    })
                    .apply(lambda row: cor_resultado_qualidade_por_linha(row, indicador), axis=1)
                    .map(cor_diferenca_qualidade, subset=["Diferença"]),
                    use_container_width=True,
                    hide_index=True,
                )

                fig_filiais = go.Figure()
                cores = []
                for _, row in comp_filiais.iterrows():
                    if modelo_qualidade(indicador) == "parametro_coleta_critico":
                        cores.append(COR_VERDE if pd.notna(row["Resultado %"]) and row["Resultado %"] <= 0.10 else COR_LARANJA)
                    else:
                        cores.append(COR_VERDE if pd.notna(row["Resultado %"]) and pd.notna(row["Meta"]) and row["Resultado %"] >= row["Meta"] else COR_LARANJA)

                fig_filiais.add_bar(
                    x=comp_filiais["FILIAL"],
                    y=comp_filiais["Resultado %"],
                    name="Resultado %",
                    marker_color=cores,
                    marker_cornerradius=4,
                    text=[fmt_pct(v) for v in comp_filiais["Resultado %"]],
                    textposition="outside",
                )
                fig_filiais.update_layout(height=420, margin=dict(t=30, b=20, l=20, r=20), legend=dict(orientation="h", y=-0.15), yaxis_tickformat=".0%")
                st.plotly_chart(fig_filiais, use_container_width=True, key="grafico_filiais_qualidade")

            elif eh_tecfil(indicador):
                st.dataframe(
                    comp_filiais[["FILIAL", "Meta KG", "Meta R$", "Realizado KG", "Realizado R$", "% Dif. KG", "% Dif. R$", "Dif. KG", "Dif. R$"]].style
                    .format({
                        "Meta KG": lambda v: fmt_num(v) if pd.notna(v) else "—",
                        "Meta R$": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                        "Realizado KG": lambda v: fmt_num(v) if pd.notna(v) else "—",
                        "Realizado R$": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                        "% Dif. KG": lambda v: fmt_pct(v) if pd.notna(v) else "—",
                        "% Dif. R$": lambda v: fmt_pct(v) if pd.notna(v) else "—",
                        "Dif. KG": lambda v: fmt_num(v) if pd.notna(v) else "—",
                        "Dif. R$": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                    })
                    .map(cor_tecfil_resultado, subset=["% Dif. KG", "% Dif. R$", "Dif. KG", "Dif. R$"]),
                    use_container_width=True,
                    hide_index=True,
                )

                fig_filiais = go.Figure()
                cores = [
                    COR_VERDE if pd.notna(r) and pd.notna(m) and r >= m else COR_LARANJA
                    for r, m in zip(comp_filiais["Realizado R$"], comp_filiais["Meta R$"])
                ]
                texto_pct_filiais_tecfil = []
                for realizado_valor, meta_valor in zip(comp_filiais["Realizado R$"], comp_filiais["Meta R$"]):
                    if pd.notna(realizado_valor) and pd.notna(meta_valor) and meta_valor != 0:
                        texto_pct_filiais_tecfil.append(fmt_pct(realizado_valor / meta_valor))
                    else:
                        texto_pct_filiais_tecfil.append("")

                fig_filiais.add_bar(
                    x=comp_filiais["FILIAL"],
                    y=comp_filiais["Realizado R$"],
                    name="Realizado R$",
                    marker_color=cores,
                    marker_cornerradius=4,
                    text=texto_pct_filiais_tecfil,
                    textposition="inside",
                    insidetextanchor="middle",
                    textfont=dict(size=12, color="white"),
                    cliponaxis=False,
                )
                fig_filiais.add_scatter(
                    x=comp_filiais["FILIAL"],
                    y=comp_filiais["Meta R$"],
                    name="Meta R$",
                    mode="lines+markers+text",
                    line=dict(color=COR_AZUL, width=3, dash="dot"),
                    marker=dict(size=7),
                    text=[fmt_brl(v) for v in comp_filiais["Meta R$"]],
                    textposition="top center",
                    textfont=dict(size=10, color=COR_AZUL),
                    cliponaxis=False,
                )
                fig_filiais.update_layout(
                    height=470,
                    margin=dict(t=100, b=35, l=20, r=20),
                    legend=dict(orientation="h", y=1.10, x=0.5, xanchor="center"),
                    yaxis_title="R$",
                    uniformtext_minsize=8,
                    uniformtext_mode="show",
                )
                st.plotly_chart(fig_filiais, use_container_width=True, key="grafico_filiais_tecfil")

            elif eh_resultado_financeiro(indicador):
                st.dataframe(
                    comp_filiais[["FILIAL", "Meta %", "Despesa", "Receita", "Resultado R$", "Resultado %"]].style
                    .format({
                        "Meta %": lambda v: fmt_pct(v) if pd.notna(v) else "—",
                        "Despesa": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                        "Receita": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                        "Resultado R$": lambda v: f"R$ {v:+,.0f}".replace(",", ".") if pd.notna(v) else "—",
                        "Resultado %": lambda v: f"{v:.1%}" if pd.notna(v) else "—",
                    })
                    .apply(cor_resultado_financeiro_por_linha, axis=1),
                    use_container_width=True,
                    hide_index=True,
                )

                cores_receita_filiais = [
                    COR_LARANJA if pd.notna(receita) and pd.notna(despesa) and receita < despesa else COR_VERDE
                    for receita, despesa in zip(comp_filiais["Receita"], comp_filiais["Despesa"])
                ]

                fig_filiais = go.Figure()
                fig_filiais.add_bar(
                    x=comp_filiais["FILIAL"],
                    y=comp_filiais["Receita"],
                    name="Receita",
                    marker_color=cores_receita_filiais,
                    marker_cornerradius=4,
                    text=[fmt_brl(v) for v in comp_filiais["Receita"]],
                    textposition="outside",
                )
                fig_filiais.add_scatter(
                    x=comp_filiais["FILIAL"],
                    y=comp_filiais["Despesa"],
                    name="Despesa",
                    mode="lines+markers",
                    line=dict(color=COR_LARANJA, width=3),
                )
                fig_filiais.update_layout(height=420, margin=dict(t=30, b=20, l=20, r=20), legend=dict(orientation="h", y=-0.15))
                st.plotly_chart(fig_filiais, use_container_width=True, key="grafico_filiais_resultado_financeiro")

            elif eh_despesa_geral(indicador):
                st.dataframe(
                    comp_filiais[["FILIAL", "Limite %", "Despesa", "Receita", "Resultado R$", "Tx. Sucesso"]].style
                    .format({
                        "Limite %": lambda v: fmt_pct(v) if pd.notna(v) else "—",
                        "Despesa": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                        "Receita": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                        "Resultado R$": lambda v: f"R$ {v:+,.0f}".replace(",", ".") if pd.notna(v) else "—",
                        "Tx. Sucesso": lambda v: f"{v:.1%}" if pd.notna(v) else "—",
                    })
                    .map(lambda v: cor_gap_valor(v, False), subset=["Resultado R$"])
                    .apply(cor_tx_sucesso_despesa_geral_por_linha, axis=1),
                    use_container_width=True,
                    hide_index=True,
                )

                fig_filiais = go.Figure()
                cores = [
                    COR_VERDE if pd.notna(tx) and pd.notna(limite) and tx <= limite else COR_LARANJA
                    for tx, limite in zip(comp_filiais["Tx. Sucesso"], comp_filiais["Limite %"])
                ]
                fig_filiais.add_bar(
                    x=comp_filiais["FILIAL"],
                    y=comp_filiais["Tx. Sucesso"],
                    name="Tx. Sucesso",
                    marker_color=cores,
                    marker_cornerradius=4,
                    text=[fmt_pct(v) for v in comp_filiais["Tx. Sucesso"]],
                    textposition="inside",
                    insidetextanchor="middle",
                    textfont=dict(size=12, color="white"),
                    cliponaxis=False,
                )
                fig_filiais.add_scatter(
                    x=comp_filiais["FILIAL"],
                    y=comp_filiais["Limite %"],
                    name="Meta Limite",
                    mode="lines+markers+text",
                    line=dict(color=COR_AZUL, width=3, dash="dot"),
                    marker=dict(size=7),
                    text=[fmt_pct(v) for v in comp_filiais["Limite %"]],
                    textposition="top center",
                    textfont=dict(size=10, color=COR_AZUL),
                    cliponaxis=False,
                )
                fig_filiais.update_layout(
                    height=430,
                    margin=dict(t=85, b=35, l=20, r=20),
                    legend=dict(orientation="h", y=1.08, x=0.5, xanchor="center"),
                    yaxis_title="%",
                    yaxis_tickformat=".0%",
                    bargap=0.24,
                    uniformtext_minsize=8,
                    uniformtext_mode="show",
                )
                st.plotly_chart(fig_filiais, use_container_width=True, key="grafico_filiais_despesa_geral")

            else:
                st.dataframe(
                    comp_filiais.style
                    .format({
                        "Realizado": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                        "Meta": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                        "Gap": "R$ {:,.0f}",
                        "Atingimento": lambda v: fmt_pct(v) if pd.notna(v) else "—",
                    })
                    .map(lambda v: cor_gap_valor(v, eh_despesa(indicador)), subset=["Gap"])
                    .map(cor_atingimento, subset=["Atingimento"]),
                    use_container_width=True,
                    hide_index=True,
                )

                texto_pct_filiais = []
                for realizado_valor, meta_valor in zip(comp_filiais["Realizado"], comp_filiais["Meta"]):
                    if pd.notna(realizado_valor) and pd.notna(meta_valor) and meta_valor != 0:
                        texto_pct_filiais.append(fmt_pct(realizado_valor / meta_valor))
                    else:
                        texto_pct_filiais.append("")

                cores_filiais = [
                    COR_VERDE if pd.notna(r) and pd.notna(m) and r >= m else COR_LARANJA
                    for r, m in zip(comp_filiais["Realizado"], comp_filiais["Meta"])
                ]

                fig_filiais = go.Figure()
                fig_filiais.add_bar(
                    x=comp_filiais["FILIAL"],
                    y=comp_filiais["Realizado"],
                    name="Realizado",
                    marker_color=cores_filiais,
                    marker_cornerradius=4,
                    text=texto_pct_filiais,
                    textposition="inside",
                    insidetextanchor="middle",
                    textfont=dict(size=12, color="white"),
                    cliponaxis=False,
                    hovertemplate="<b>%{x}</b><br>Realizado: R$ %{y:,.0f}<extra></extra>",
                )
                fig_filiais.add_scatter(
                    x=comp_filiais["FILIAL"],
                    y=comp_filiais["Meta"],
                    name="Meta",
                    mode="lines+markers+text",
                    line=dict(color=COR_AZUL, width=3, dash="dot"),
                    marker=dict(size=7),
                    text=[fmt_brl(v) for v in comp_filiais["Meta"]],
                    textposition="top center",
                    textfont=dict(size=10, color=COR_AZUL),
                    cliponaxis=False,
                    hovertemplate="<b>%{x}</b><br>Meta: R$ %{y:,.0f}<extra></extra>",
                )
                fig_filiais.update_layout(
                    height=470,
                    margin=dict(t=100, b=35, l=20, r=20),
                    legend=dict(orientation="h", y=1.10, x=0.5, xanchor="center"),
                    yaxis_title="R$",
                    uniformtext_minsize=8,
                    uniformtext_mode="show",
                )
                st.plotly_chart(fig_filiais, use_container_width=True, key="grafico_filiais")


# =========================
# ABA MAPA — ANÁLISE GEOGRÁFICA
# =========================
with tab_geo:
    titulo_secao(
        "Mapa — Análise geográfica",
        f"{indicador} — {filial}: distribuição geográfica do faturamento por filial, com estados pintados por participação."
    )

    anos_mapa = sorted(df["ANO"].dropna().unique())

    if not eh_faturamento_normal_geografico(indicador) and not eh_moto_margem(indicador):
        st.info(
            "A análise geográfica está disponível apenas para indicadores de faturamento normal. "
            "Indicadores especiais, despesas, qualidade, Tecfil e Resultado Financeiro não usam este mapa."
        )
    elif not anos_mapa:
        st.info("Não há ano disponível para montar a análise geográfica.")
    else:
        ano_mapa = st.selectbox(
            "Selecione o ano do mapa",
            anos_mapa,
            index=len(anos_mapa) - 1,
            key=f"ano_mapa_{indicador}_{filial}",
        )

        st.caption(f"Mapa referente ao ano de {ano_mapa}.")
        if eh_moto_margem(indicador):
            renderizar_analise_geografica_moto(df_comparativo_filiais, indicador, int(ano_mapa))
        else:
            renderizar_analise_geografica_faturamento(df_comparativo_filiais, indicador, int(ano_mapa))




# =========================
# ABA OPÇÕES — ADMIN E GABRIEL
# =========================
if (eh_admin() or eh_gabriel()) and tab6 is not None:
    with tab6:
        titulo_secao("Opções", f"{indicador} — {filial}: PDF, e-mail e administração da base.")

        if eh_admin():
            with st.expander("🔐 Administração da base", expanded=False):
                st.caption("Área exclusiva do perfil Administrador.")

                fonte_atual = st.session_state.get("fonte_dados_admin", "Google Drive")
                nova_fonte = st.radio(
                    "Fonte da base",
                    ["Google Drive", "Upload manual"],
                    index=0 if fonte_atual == "Google Drive" else 1,
                    horizontal=True,
                    key="opcoes_fonte_dados_admin",
                )

                novo_nome_drive = st.text_input(
                    "Nome do arquivo no Drive",
                    value=st.session_state.get("nome_arquivo_drive_admin", "BaseSistema.xlsx"),
                    key="opcoes_nome_arquivo_drive_admin",
                )

                if nova_fonte == "Upload manual":
                    arquivo_admin = st.file_uploader(
                        "Carregar planilha manualmente (.xlsx)",
                        type=["xlsx"],
                        key="opcoes_upload_manual_admin",
                    )

                    if arquivo_admin is not None:
                        st.session_state["arquivo_manual_bytes_admin"] = arquivo_admin.getvalue()
                        st.session_state["arquivo_manual_nome_admin"] = arquivo_admin.name
                        st.success(f"Arquivo manual carregado: {arquivo_admin.name}")

                    if st.session_state.get("arquivo_manual_nome_admin"):
                        st.info(f"Arquivo manual atual: {st.session_state.get('arquivo_manual_nome_admin')}")

                col_admin_1, col_admin_2 = st.columns(2)

                with col_admin_1:
                    if st.button("Salvar configuração da base", use_container_width=True):
                        st.session_state["fonte_dados_admin"] = nova_fonte
                        st.session_state["nome_arquivo_drive_admin"] = str(novo_nome_drive).strip() or "BaseSistema.xlsx"
                        st.cache_data.clear()
                        st.success("Configuração salva. Recarregando dados...")
                        st.rerun()

                with col_admin_2:
                    if st.button("Voltar para Google Drive padrão", use_container_width=True):
                        st.session_state["fonte_dados_admin"] = "Google Drive"
                        st.session_state["nome_arquivo_drive_admin"] = "BaseSistema.xlsx"
                        st.session_state.pop("arquivo_manual_bytes_admin", None)
                        st.session_state.pop("arquivo_manual_nome_admin", None)
                        st.cache_data.clear()
                        st.success("Fonte restaurada para Google Drive.")
                        st.rerun()

                st.info("Usuários comuns não veem essa área e ficam travados na importação pelo Google Drive.")

        if not eh_faturamento_simples(indicador) and not eh_moto_margem(indicador):
            st.info("O relatório em PDF está disponível apenas para indicadores de Faturamento.")
        else:
            ano_pdf_dashboard = int(df["ANO"].max())
            nome_pdf_dashboard = f"relatorio_{indicador}_{filial}_{ano_pdf_dashboard}.pdf".replace(" ", "_").replace("/", "-")

            with st.spinner("Gerando PDF..."):
                pdf_bytes_dashboard = gerar_pdf(df, df_todas_unidades, indicador, filial, ano_pdf_dashboard, df_filiais_comp=df_comparativo_filiais)

            st.markdown("### Arquivos e envio")
            col_op1, col_op2 = st.columns(2)

            with col_op1:
                st.download_button(
                    label="📄 Baixar PDF",
                    data=pdf_bytes_dashboard,
                    file_name=nome_pdf_dashboard,
                    mime="application/pdf",
                    use_container_width=True,
                    key=f"baixar_pdf_dashboard_{indicador}_{filial}_{ano_pdf_dashboard}",
                )

            with col_op2:
                st.info("Use a opção abaixo para enviar o relatório diretamente por e-mail.")

            with st.expander("✉️ Enviar por e-mail", expanded=True):
                _hora_agora = agora_br().hour
                if _hora_agora < 12:
                    _saudacao = "Bom dia"
                elif _hora_agora < 18:
                    _saudacao = "Boa tarde"
                else:
                    _saudacao = "Boa noite"
                with st.form(key=f"form_email_relatorio_{indicador}_{filial}_{ano_pdf_dashboard}"):
                    email_destino = st.text_input("E-mail do destinatário")
                    assunto_email = st.text_input(
                        "Assunto",
                        value=f"Relatório de Indicadores - {indicador} | {filial} | {ano_pdf_dashboard}"
                    )
                    corpo_email = st.text_area(
                        "Mensagem",
                        value=(
                            f"{_saudacao}, Prezados, estimo que estejam bem!\n\n"
                            f"Segue o relatório de {indicador} com os resultados de {filial} para o ano de {ano_pdf_dashboard}.\n\n"
                            f"Principais informações do relatório:\n"
                            f"- Desempenho mensal\n"
                            f"- Variação mês a mês (MoM)\n"
                            f"- Comparativo ano a ano (YoY)\n\n"
                            f"Atenciosamente."
                        ),
                        height=140
                    )

                    enviar_email = st.form_submit_button("Enviar e-mail", use_container_width=True)

                    if enviar_email:
                        if not email_destino or "@" not in email_destino:
                            st.warning("Informe um e-mail válido.")
                        else:
                            with st.spinner("Enviando e-mail..."):
                                ok, msg_envio = enviar_email_relatorio(
                                    destinatario=email_destino,
                                    assunto=assunto_email,
                                    corpo=corpo_email,
                                    nome_arquivo=nome_pdf_dashboard,
                                    pdf_bytes=pdf_bytes_dashboard,
                                )

                            if ok:
                                st.success(msg_envio)
                            else:
                                st.error(msg_envio)
