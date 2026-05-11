import io
import os
import hmac
import time
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload
except ModuleNotFoundError:
    service_account = None
    build = None
    MediaIoBaseDownload = None

try:
    import bcrypt
    BCRYPT_DISPONIVEL = True
except ModuleNotFoundError:
    bcrypt = None
    BCRYPT_DISPONIVEL = False

from maz_config import (
    FILIAIS_REAIS, FILIAL_CODIGO_NOME,
    LIMITE_SESSAO_MINUTOS, LIMITE_TENTATIVAS, TEMPO_BLOQUEIO_MINUTOS,
    LOGO_ARQUIVO,
)


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


def _registrar_acesso(login, filiais):
    """Grava uma linha de log na aba LogAcessos (requer LOG_SPREADSHEET_ID no Secrets)."""
    try:
        from googleapiclient.discovery import build as _build
        from google.oauth2 import service_account as _sa

        credentials = _sa.Credentials.from_service_account_info(
            st.secrets["gcp_service_account"],
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        sheets = _build("sheets", "v4", credentials=credentials)

        spreadsheet_id = str(st.secrets.get("LOG_SPREADSHEET_ID", "")).strip()
        if not spreadsheet_id:
            return

        agora = datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%d/%m/%Y %H:%M:%S")
        filiais_str = ", ".join(filiais) if filiais else "Geral"

        sheets.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range="LogAcessos!A:C",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [[agora, login, filiais_str]]},
        ).execute()
    except Exception:
        pass


_CSS_LOGIN = """
<style>
    [data-testid="stSidebar"] { display: none; }

    .block-container {
        padding-top: 3rem !important;
        padding-bottom: 2rem !important;
        max-width: 100% !important;
        background:
            radial-gradient(circle at top left, rgba(242,101,34,0.07), transparent 24%),
            radial-gradient(circle at bottom right, rgba(0,163,80,0.07), transparent 26%),
            linear-gradient(135deg, #FFFFFF 0%, #FAFAFA 100%);
    }

    .login-bg { min-height: auto; display: block; padding: 0; background: transparent; border-radius: 0; }

    .login-logo-wrap { display: flex; align-items: center; justify-content: center; margin-bottom: 12px; }

    .login-badge {
        display: inline-flex; align-items: center; justify-content: center;
        gap: 8px; padding: 7px 12px; border-radius: 999px;
        background: rgba(242,101,34,0.09); color: #F26522;
        font-size: 12px; font-weight: 850; margin: 0 auto 12px auto;
        border: 1px solid rgba(242,101,34,0.18);
    }

    .login-title { text-align: center; font-size: 34px; color: #111827; font-weight: 950; margin: 4px 0 8px 0; line-height: 1.08; }
    .login-subtitle { text-align: center; font-size: 14.5px; color: #6B7280; font-weight: 600; line-height: 1.45; margin-bottom: 22px; }

    .login-security {
        display: flex; align-items: flex-start; gap: 8px; margin-top: 16px;
        padding: 11px 12px; border-radius: 14px; background: #F9FAFB;
        border: 1px solid #E5E7EB; color: #6B7280; font-size: 12px; font-weight: 650; line-height: 1.35;
    }

    div[data-testid="stForm"] { border: none !important; padding: 0 !important; box-shadow: none !important; background: transparent !important; }
    div[data-testid="stTextInput"] label p { font-weight: 800 !important; font-size: 12px !important; color: #374151 !important; }
    div[data-testid="stTextInput"] [data-baseweb="input"] { border: 1.4px solid #D1D5DB !important; border-radius: 14px !important; background: #FFFFFF !important; box-shadow: none !important; outline: none !important; transition: border-color .12s ease !important; }
    div[data-testid="stTextInput"] [data-baseweb="input"]:focus-within { border: 1.4px solid #F26522 !important; box-shadow: none !important; outline: none !important; }
    div[data-testid="stTextInput"] [data-baseweb="input"] > div { background: transparent !important; box-shadow: none !important; outline: none !important; border: none !important; }
    div[data-testid="stTextInput"] input { border: none !important; border-radius: 14px !important; background: transparent !important; min-height: 45px !important; font-size: 14px !important; font-weight: 600 !important; padding-left: 12px !important; box-shadow: none !important; outline: none !important; background-image: none !important; -webkit-appearance: none !important; }
    div[data-testid="stTextInput"] input:focus, div[data-testid="stTextInput"] input:active, div[data-testid="stTextInput"] input:focus-visible { border: none !important; box-shadow: none !important; outline: none !important; background-image: none !important; }
    div[data-testid="InputInstructions"] { display: none !important; }
    div[data-testid="stTextInput"] { margin-bottom: 12px !important; }
    div[data-testid="stTextInput"] label { margin-bottom: 4px !important; }

    div[data-testid="stFormSubmitButton"] button {
        background: linear-gradient(90deg, #F26522, #F47B3E) !important;
        color: #FFFFFF !important; border: none !important; border-radius: 14px !important;
        min-height: 46px !important; font-weight: 900 !important; font-size: 15px !important;
        box-shadow: 0 10px 22px rgba(242,101,34,0.24) !important; transition: all .15s ease !important;
    }
    div[data-testid="stFormSubmitButton"] button:hover { transform: translateY(-1px); box-shadow: 0 13px 26px rgba(242,101,34,0.30) !important; filter: brightness(1.02); }

    .login-footer { text-align: center; color: #9CA3AF; font-size: 11px; margin-top: 12px; font-weight: 600; }

    .login-blocked-card { width: min(520px, 100%); background: #FFFFFF; border: 1px solid #E5E7EB; border-radius: 22px; padding: 34px 36px; box-shadow: 0 18px 48px rgba(17,24,39,0.10); text-align: center; }

    .login-alert { background: rgba(242,101,34,0.10); color: #9A3412; border-left: 5px solid #F26522; border-radius: 14px; padding: 13px 14px; font-size: 14px; font-weight: 700; text-align: left; }

    div[data-testid="column"] .kpi-card { width:100% !important; height:118px !important; min-height:118px !important; max-height:118px !important; }
    div[data-testid="stMetric"] { background:#FFFFFF !important; border:1px solid #E5E7EB !important; border-radius:16px !important; padding:16px 16px 14px 16px !important; height:118px !important; min-height:118px !important; max-height:118px !important; box-sizing:border-box !important; box-shadow:0 4px 14px rgba(17,24,39,0.07) !important; position:relative !important; overflow:hidden !important; }
    div[data-testid="stMetric"]::before { content:""; position:absolute; left:0; top:0; height:5px; width:100%; background:linear-gradient(90deg, #F26522 0%, #00A350 100%) !important; }
    div[data-testid="stMetricLabel"] { min-height:28px !important; max-height:28px !important; }
    div[data-testid="stMetricLabel"] p { font-size:11px !important; color:#6B7280 !important; font-weight:850 !important; text-transform:uppercase !important; letter-spacing:.02em !important; line-height:1.15 !important; margin:0 !important; overflow:hidden !important; display:-webkit-box !important; -webkit-line-clamp:2 !important; -webkit-box-orient:vertical !important; }
    div[data-testid="stMetricValue"] div { font-size:clamp(17px, 1.35vw, 22px) !important; font-weight:900 !important; color:#111827 !important; white-space:nowrap !important; overflow:hidden !important; text-overflow:ellipsis !important; font-variant-numeric:tabular-nums !important; }
    div[data-testid="stMetricDelta"] { min-height:20px !important; max-height:20px !important; font-size:11px !important; font-weight:850 !important; }
    .kpi-note { position:absolute; left:16px; right:16px; bottom:10px; font-size:11px; font-weight:800; line-height:1.15; color:#6B7280; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .kpi-note.good { color:#00A350; }
    .kpi-note.warn { color:#F26522; }
    .kpi-note.info { color:#6B7280; }
    .kpi-card.has-note .kpi-delta { display:none; }
</style>
"""


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
    if "bloqueado_ate" not in st.session_state:
        st.session_state["bloqueado_ate"] = 0

    st.markdown(_CSS_LOGIN, unsafe_allow_html=True)

    # ---- Verifica se está bloqueado por tempo ----
    if time.time() < st.session_state["bloqueado_ate"]:
        minutos_restantes = int((st.session_state["bloqueado_ate"] - time.time()) / 60) + 1
        st.markdown(
            f"""
            <div class="login-bg">
                <div class="login-blocked-card">
                    <div style="font-size: 44px;">🚫</div>
                    <div class="login-title">Acesso temporariamente bloqueado</div>
                    <div class="login-subtitle">Houve excesso de tentativas de login.</div>
                    <div class="login-alert">Tente novamente em aproximadamente {minutos_restantes} minuto(s).</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.stop()

    # ---- Estrutura visual da tela de login ----
    col_esq, col_centro, col_dir = st.columns([1.35, 1.0, 1.35])

    with col_centro:
        st.markdown(
            """
            <div style="height:5px;width:100%;border-radius:999px;background:linear-gradient(90deg,#F26522,#00A350);margin:8px 0 18px 0;"></div>
            """,
            unsafe_allow_html=True,
        )

        logo_renderizado = False
        if os.path.exists(LOGO_ARQUIVO):
            try:
                col_logo_1, col_logo_2, col_logo_3 = st.columns([1, 1.15, 1])
                with col_logo_2:
                    st.image(LOGO_ARQUIVO, use_container_width=True)
                logo_renderizado = True
            except Exception:
                logo_renderizado = False

        if not logo_renderizado:
            st.markdown('<div class="login-logo-wrap"><div style="font-size:46px;">♻️</div></div>', unsafe_allow_html=True)

        st.markdown(
            """
            <div style="text-align:center;margin-top:8px;">
                <div class="login-badge">🔐 Ambiente seguro</div>
            </div>
            <div class="login-title">Acesso ao Painel</div>
            <div class="login-subtitle">Informe suas credenciais para acessar o painel gerencial de indicadores.</div>
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
            codigo_filial = dados_usuario["filial"]
            is_adm = dados_usuario["adm"].startswith("S")
            if is_adm or codigo_filial in ("T", ""):
                filiais_perm = ["Geral"] + FILIAIS_REAIS
            elif codigo_filial in FILIAL_CODIGO_NOME:
                filiais_perm = [FILIAL_CODIGO_NOME[codigo_filial]]
            else:
                filiais_perm = ["Geral"] + FILIAIS_REAIS

            chave_tries = f"tries_{usuario_digitado_limpo.lower()}"
            st.session_state["acesso_liberado"] = True
            st.session_state["usuario_logado"] = dados_usuario["login"]
            st.session_state["usuario_adm"] = is_adm
            st.session_state["filiais_permitidas"] = filiais_perm
            st.session_state[chave_tries] = 0
            st.session_state["bloqueado_ate"] = 0
            st.session_state["ultimo_acesso"] = time.time()
            _registrar_acesso(dados_usuario["login"], filiais_perm)
            st.rerun()
        else:
            chave_tries = f"tries_{usuario_digitado_limpo.lower()}"
            st.session_state[chave_tries] = st.session_state.get(chave_tries, 0) + 1

            if st.session_state[chave_tries] >= LIMITE_TENTATIVAS:
                st.session_state["bloqueado_ate"] = time.time() + TEMPO_BLOQUEIO_MINUTOS * 60
                st.error(f"🚫 Usuário '{usuario_digitado_limpo}' bloqueado por {TEMPO_BLOQUEIO_MINUTOS} minutos por excesso de tentativas.")
                st.stop()

            restantes = max(0, LIMITE_TENTATIVAS - st.session_state[chave_tries])
            st.error(f"❌ Usuário ou senha incorretos. Tentativas restantes: {restantes}")

    return False
