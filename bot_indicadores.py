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


def _verificar_senha(senha_digitada, senha_armazenada):
    """
    Verifica se a senha digitada bate com a armazenada nos Secrets.

    Suporta dois formatos:
    1. Hash bcrypt (recomendado): começa com $2a$, $2b$ ou $2y$.
    2. Texto puro (legado): comparação timing-safe via hmac.compare_digest.

    Para gerar um hash bcrypt, rode no Python online:

        import bcrypt
        senha = "MinhaSenhaForte2026!"
        print(bcrypt.hashpw(senha.encode(), bcrypt.gensalt(rounds=12)).decode())

    Cole o hash no Secrets em [usuarios].
    """
    if senha_armazenada is None:
        return False

    senha_armazenada_str = str(senha_armazenada).strip()
    senha_digitada_str = str(senha_digitada).strip()

    # Detecta se é hash bcrypt
    if BCRYPT_DISPONIVEL and senha_armazenada_str.startswith(("$2a$", "$2b$", "$2y$")):
        try:
            return bcrypt.checkpw(
                senha_digitada_str.encode("utf-8"),
                senha_armazenada_str.encode("utf-8"),
            )
        except Exception:
            return False

    # Fallback: comparação timing-safe em texto puro
    return hmac.compare_digest(senha_digitada_str, senha_armazenada_str)


def verificar_senha_acesso():
    """
    Libera o acesso ao painel usando usuários configurados no Streamlit Secrets.

    Mantém:
    - bcrypt;
    - bloqueio por tentativas;
    - timeout de sessão;
    - comparação segura de senha.

    Esta versão altera apenas o visual da página de login.
    """
    usuarios = dict(st.secrets.get("usuarios", {}))

    # Usuário especial para acesso restrito às abas Projeção e Análise.
    # Somente este perfil verá essas abas.
    usuarios.setdefault("gabriel", "Camelbak123-")

    if not usuarios:
        st.error("Nenhum usuário foi encontrado no Secrets do Streamlit.")
        st.info('No Secrets, adicione o bloco [usuarios]. Exemplo: ADMIN = "38818171", MAZOLA = "49929282" e gabriel = "Camelbak123-"')
        st.stop()

    # ---- Verifica se já está autenticado e se a sessão ainda é válida ----
    if st.session_state.get("acesso_liberado", False):
        ultimo_acesso = st.session_state.get("ultimo_acesso", 0)
        if time.time() - ultimo_acesso > LIMITE_SESSAO_MINUTOS * 60:
            for chave in ["acesso_liberado", "usuario_logado", "ultimo_acesso"]:
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

        usuario_encontrado = None
        senha_correta = None

        # Comparação de usuário case-insensitive
        for usuario_secrets, senha_secrets in usuarios.items():
            if str(usuario_secrets).strip().lower() == usuario_digitado_limpo.lower():
                usuario_encontrado = str(usuario_secrets).strip()
                senha_correta = senha_secrets
                break

        senha_ok = (
            usuario_encontrado is not None
            and senha_correta is not None
            and _verificar_senha(senha_digitada_limpa, senha_correta)
        )

        if senha_ok:
            st.session_state["acesso_liberado"] = True
            st.session_state["usuario_logado"] = usuario_encontrado
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
    """True apenas para perfil administrador."""
    return perfil_usuario() == "admin"


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
        return "—"
    try:
        return f"{float(v):+.1f}%"
    except Exception:
        return "—"


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

        if eh_despesa_hora_extra(indicador):
            df_completa_tela = df_completa_tela.rename(columns={
                "Meta": "Limite",
                "Realizado": "Pago em Hora Extra",
                "Gap": "Saldo do Limite",
                "Atingimento": "Resultado %",
            })

            colunas_exibir = [
                c for c in [
                    "Mês", "Limite", "Pago em Hora Extra", "Saldo do Limite", "Resultado %", "Acumulado"
                ] if c in df_completa_tela.columns
            ]

            st.dataframe(
                df_completa_tela[colunas_exibir].style
                .format({
                    "Limite": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                    "Pago em Hora Extra": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                    "Saldo do Limite": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                    "Resultado %": lambda v: fmt_pct(v) if pd.notna(v) else "—",
                    "Acumulado": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                })
                .map(lambda v: cor_gap_valor(v, False), subset=[c for c in ["Saldo do Limite"] if c in colunas_exibir])
                .map(cor_despesa_manutencao, subset=[c for c in ["Resultado %"] if c in colunas_exibir]),
                use_container_width=True,
                hide_index=True,
            )

        elif eh_despesa_manutencao(indicador):
            df_completa_tela = df_completa_tela.rename(columns={
                "Meta": "Limite",
                "Realizado": "Despesa",
                "Gap": "Result. R$",
                "Atingimento": "Result. %",
            })

            cols_manutencao = [
                c for c in ["Mês", "Limite", "Despesa", "Result. R$", "Result. %", "Acumulado"]
                if c in df_completa_tela.columns
            ]

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
                .map(lambda v: cor_gap_valor(v, False), subset=[c for c in ["Result. R$", "Acumulado"] if c in cols_manutencao])
                .map(
                    lambda v: f"color: {COR_VERDE}; font-weight:bold" if pd.notna(v) and v >= 0
                    else (f"color: {COR_LARANJA}; font-weight:bold" if pd.notna(v) else ""),
                    subset=[c for c in ["Result. %"] if c in cols_manutencao]
                ),
                use_container_width=True,
                hide_index=True,
            )

        else:
            if eh_faturamento_simples(indicador):
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
                coluna_gap = "Gap (R$)" if "Gap (R$)" in df_completa_tela.columns else "Gap"

                st.dataframe(
                    df_completa_tela.style
                    .format({
                        "Realizado": lambda v: fmt_brl(v) if pd.notna(v) else "",
                        "Meta": lambda v: fmt_brl(v) if pd.notna(v) else "",
                        coluna_gap: lambda v: f"R$ {v:+,.0f}".replace(",", ".") if pd.notna(v) else "",
                        "Atingimento": lambda v: f"{v:.0%}" if pd.notna(v) else "",
                    })
                    .map(lambda v: cor_gap_valor(v, eh_despesa(indicador)), subset=[coluna_gap])
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

    if not eh_faturamento_normal_geografico(indicador):
        st.info(
            "A análise geográfica está disponível apenas para indicadores de faturamento normal. "
            "Indicadores especiais, despesas, qualidade, Tecfil e Resultado Financeiro não usam este mapa."
        )
    elif filial != "Geral":
        st.info(
            "Para visualizar o mapa, selecione a filial como Geral. "
            "O mapa compara a participação das filiais no faturamento total."
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
        renderizar_analise_geografica_faturamento(df_comparativo_filiais, indicador, int(ano_mapa))


# =========================
# ABA PROJEÇÃO — SOMENTE GABRIEL
# =========================
if eh_gabriel() and tab4 is not None:
    with tab4:
        renderizar_projecao_executiva(df, indicador, filial, data_geracao_planilha)


# =========================
# ABA ANÁLISE — SOMENTE GABRIEL
# =========================
if eh_gabriel() and tab5 is not None:
    with tab5:
        renderizar_analise_executiva(df, indicador, filial, data_geracao_planilha, df_comparativo_filiais)


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

        ano_pdf_dashboard = int(df["ANO"].max())
        nome_pdf_dashboard = f"relatorio_{indicador}_{filial}_{ano_pdf_dashboard}.pdf".replace(" ", "_").replace("/", "-")

        with st.spinner("Gerando PDF..."):
            pdf_bytes_dashboard = gerar_pdf(df, df_todas_unidades, indicador, filial, ano_pdf_dashboard)

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
            with st.form(key=f"form_email_relatorio_{indicador}_{filial}_{ano_pdf_dashboard}"):
                email_destino = st.text_input("E-mail do destinatário")
                assunto_email = st.text_input(
                    "Assunto",
                    value=f"Relatório de Indicadores - {indicador} | {filial} | {ano_pdf_dashboard}"
                )
                corpo_email = st.text_area(
                    "Mensagem",
                    value=f"Olá,\\n\\nSegue em anexo o relatório de indicadores referente a {indicador}, base {filial}, ano {ano_pdf_dashboard}.\\n\\nAtenciosamente.",
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

