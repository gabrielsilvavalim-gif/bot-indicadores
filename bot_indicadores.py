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

    Formato recomendado no Secrets (use senhas FORTES, com 12+ caracteres):

    [usuarios]
    Mazola = "$2b$12$KIXhash_bcrypt_aqui"   # recomendado: hash bcrypt
    valim = "senha_em_texto_puro_legado"     # também funciona, mas menos seguro

    [gcp_service_account]
    ...

    Funcionalidades de segurança:
    - Bloqueio de 30 minutos após 3 tentativas erradas (bloqueio por tempo, não por sessão)
    - Sessão expira após 60 minutos sem atividade
    - Comparação timing-safe (resistente a ataques de timing)
    - Suporte a hashes bcrypt (resistente a força bruta)
    """
    usuarios = st.secrets.get("usuarios", {})

    if not usuarios:
        st.error("Nenhum usuário foi encontrado no Secrets do Streamlit.")
        st.info('No Secrets, adicione o bloco [usuarios]. Exemplo: Mazola = "1234" e valim = "camelbak123-"')
        st.stop()

    # ---- Verifica se já está autenticado e se a sessão ainda é válida ----
    if st.session_state.get("acesso_liberado", False):
        ultimo_acesso = st.session_state.get("ultimo_acesso", 0)
        if time.time() - ultimo_acesso > LIMITE_SESSAO_MINUTOS * 60:
            # Sessão expirada — derruba o usuário
            for chave in ["acesso_liberado", "usuario_logado", "ultimo_acesso"]:
                st.session_state.pop(chave, None)
            st.warning("⏰ Sua sessão expirou por inatividade. Faça login novamente.")
            # cai para a tela de login abaixo
        else:
            # Sessão válida — atualiza timestamp de atividade
            st.session_state["ultimo_acesso"] = time.time()
            return True

    # ---- Inicializa controles de tentativas ----
    if "tentativas_login" not in st.session_state:
        st.session_state["tentativas_login"] = 0
    if "bloqueado_ate" not in st.session_state:
        st.session_state["bloqueado_ate"] = 0

    # ---- Verifica se está bloqueado por tempo ----
    if time.time() < st.session_state["bloqueado_ate"]:
        minutos_restantes = int((st.session_state["bloqueado_ate"] - time.time()) / 60) + 1
        st.error(
            f"🚫 Acesso bloqueado por excesso de tentativas. "
            f"Tente novamente em {minutos_restantes} minutos."
        )
        st.stop()

    # ---- Tela de login ----
    st.markdown(
        """
        <div style="max-width: 520px; margin: 80px auto 20px auto; text-align: center;">
            <h1>🔒 Acesso restrito</h1>
            <p style="color: #666; font-size: 16px;">
                Digite seu usuário e senha para acessar o painel de indicadores.
            </p>
        </div>
        """,
        unsafe_allow_html=True
    )

    with st.form("form_login_acesso"):
        usuario_digitado = st.text_input("Usuário")
        senha_digitada = st.text_input("Senha", type="password")
        entrar = st.form_submit_button("Entrar", use_container_width=True)

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

            # Bloqueia após 3 tentativas erradas
            if st.session_state["tentativas_login"] >= LIMITE_TENTATIVAS:
                st.session_state["bloqueado_ate"] = time.time() + TEMPO_BLOQUEIO_MINUTOS * 60
                st.error(
                    f"🚫 Bloqueado por {TEMPO_BLOQUEIO_MINUTOS} minutos por excesso de tentativas."
                )
                st.stop()

            restantes = max(0, LIMITE_TENTATIVAS - st.session_state["tentativas_login"])
            st.error(
                f"❌ Usuário ou senha incorretos. Tentativas restantes: {restantes}"
            )

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


def nome_perfil_exibicao():
    return "Administrador" if eh_admin() else "Usuário comum"




# =========================
# CONFIGURAÇÕES GERAIS
# =========================
COR_LARANJA = "#F26522"
COR_VERDE = "#00A350"
QUALQUER = "__ANY__"
LOGO_ARQUIVO = "MazolaCertificado.ico"
TZ_BR = ZoneInfo("America/Sao_Paulo")
LIMITE_DESPESA_GERAL_PADRAO = 0.71  # 71% - limite válido a partir de 2026 para Despesa Geral
META_RESULTADO_FINANCEIRO_PADRAO = 0.05  # 5% - meta padrão do Resultado Financeiro

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
    Para Despesa Manutenção e Despesa Hora Extra:
    verde quando despesa/pago <= limite, laranja quando passar do limite.
    Como o valor formatado é Despesa ou Pago / Limite, verde até 100%.
    """
    if pd.isna(v):
        return ""
    return f"color: {COR_VERDE}; font-weight:bold" if v <= 1 else f"color: {COR_LARANJA}; font-weight:bold"

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
    Renderiza o cabeçalho com a data acima do logo e o texto alinhado
    verticalmente ao centro do logo da Mazola.
    """
    col_logo, col_titulo = st.columns([1.25, 5], vertical_alignment="center", gap="small")

    with col_logo:
        st.markdown(
            f"""
            <div style="
                width: 160px;
                text-align: center;
                margin: 0 0 4px 0;
                padding: 0;
                line-height: 1.05;
            ">
                <div style="
                    font-size: 10px;
                    color: #6B7280;
                    font-weight: 600;
                    margin: 0 0 2px 0;
                    padding: 0;
                    white-space: nowrap;
                ">
                    Data de geração
                </div>
                <div style="
                    font-size: 12px;
                    color: #111827;
                    font-weight: 700;
                    margin: 0;
                    padding: 0;
                    white-space: nowrap;
                ">
                    {data_geracao_planilha}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        if os.path.exists(LOGO_ARQUIVO):
            st.image(LOGO_ARQUIVO, width=150)

    with col_titulo:
        st.markdown(
            """
            <div style="
                display: flex;
                flex-direction: column;
                justify-content: center;
                align-items: flex-start;
                min-height: 110px;
                margin: 48px 0 0 0;
                padding: 0;
                line-height: 1.08;
            ">
                <div style="
                    color: #F26522;
                    font-size: 17px;
                    font-weight: 700;
                    margin: 0 0 2px 0;
                    padding: 0;
                    line-height: 1.08;
                    white-space: nowrap;
                ">
                    Análise de Indicadores Mazola Ambiental
                </div>
                <div style="
                    color: #00A350;
                    font-size: 16px;
                    font-weight: 700;
                    margin: 0;
                    padding: 0;
                    line-height: 1.08;
                    white-space: nowrap;
                ">
                    Painel gerencial de acompanhamento de metas e resultados
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

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
        d["REALIZADO_CALC"] = d["VALOR1"]
        d["RESULTADO_RS"] = d["META_CALC"] - d["REALIZADO_CALC"]
        d["ATINGIMENTO_CALC"] = d["REALIZADO_CALC"] / d["META_CALC"].replace(0, pd.NA)

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
        tx_sucesso = None
    else:
        limite_rs = receita * limite_pct
        resultado = limite_rs - despesa

        # Fórmula solicitada:
        # Tx. Sucesso = (Receita x Limite %) / Despesa
        # Excel: =SEERRO((AD5*AB5)/AC5;"0")
        tx_sucesso = limite_rs / despesa if despesa > 0 else None

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
    TX. SUCESSO = (RECEITA x LIMITE %) / DESPESA

    Fórmula da Tx. Sucesso no Excel:
    =SEERRO((AD5*AB5)/AC5;"0")

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

    # Fórmula solicitada:
    # Tx. Sucesso = (Receita x Limite %) / Despesa
    d["TX_SUCESSO_CALC"] = d["LIMITE_RS_CALC"] / d["DESPESA_CALC"].replace(0, pd.NA)

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

    Conforme o modelo da planilha, a meta do Resultado Financeiro deve ser 5,0%.
    Portanto, a meta não deve variar por ano nem puxar percentuais antigos da base.
    """
    if grupo is None or grupo.empty:
        return None

    return META_RESULTADO_FINANCEIRO_PADRAO

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
    META % = 5,0%
    Resultado % = 1 - (Despesa / Receita)
    Resultado R$ = (Receita x Resultado %) - (Receita x Meta %)

    Observação:
    A meta do Resultado Financeiro não deve puxar 14%, 13%, etc. de anos anteriores.
    Para esse modelo, a meta correta é 5,0% em todos os anos.
    """
    d = df.copy()

    # Meta fixa correta do modelo Resultado Financeiro
    d["META_PERCENTUAL_CALC"] = META_RESULTADO_FINANCEIRO_PADRAO

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

    resumo_total = resumo_moto_por_grupo(base_ano) if not base_ano.empty else {
        "Meta %": None,
        "Compra": 0,
        "Faturamento": 0,
        "Margem Bruta": 0,
        "Tx. Sucesso": None
    }

    rows.append({
        "Mês": "TOTAL",
        "Meta": resumo_total["Meta %"],
        "Compra": resumo_total["Compra"],
        "Faturamento": resumo_total["Faturamento"],
        "Margem Bruta": resumo_total["Margem Bruta"],
        "Tx. Sucesso": resumo_total["Tx. Sucesso"],
        "Acumulado": resumo_total["Margem Bruta"],
    })

    tabela = pd.DataFrame(rows)

    # Pneus Velhos Moto:
    # 2023, 2024 e 2025 não possuem meta na planilha.
    # Portanto a meta deve ficar vazia em todos os meses e também no TOTAL.
    if int(ano) < 2026:
        tabela["Meta"] = pd.NA

    return tabela

def meta_ponderada_moto(grupo):
    """
    Meta total do ano/mês para Pneus Velhos Moto.
    Fórmula equivalente ao Excel:
    SOMARPRODUTO(FATURAMENTO; META %) / SOMA(FATURAMENTO)

    Exemplo:
    Meta Total = (Faturamento Jan x Meta Jan + Faturamento Fev x Meta Fev + ...) / Faturamento Total
    """
    faturamento = pd.to_numeric(grupo.get("FATURAMENTO_MOTO_CALC"), errors="coerce").fillna(0)
    meta_pct = pd.to_numeric(grupo.get("META_PERCENTUAL_CALC"), errors="coerce").fillna(0)
    total_faturamento = faturamento.sum()
    if total_faturamento > 0:
        return (faturamento * meta_pct).sum() / total_faturamento
    return meta_pct.mean() if len(meta_pct) else None


def resumo_moto_por_grupo(grupo):
    faturamento = pd.to_numeric(grupo.get("FATURAMENTO_MOTO_CALC"), errors="coerce").fillna(0).sum()
    compra = pd.to_numeric(grupo.get("COMPRA_CALC"), errors="coerce").fillna(0).sum()
    margem = faturamento - compra
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
    Tx. Sucesso = (Receita x Limite %) / Despesa

    Interpretação:
    - Verde quando Tx. Sucesso >= 100%.
    - Laranja quando Tx. Sucesso < 100%.
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

    if pd.notna(tx):
        idx = list(row.index).index("Tx. Sucesso")
        estilos[idx] = f"color: {COR_VERDE}; font-weight:bold" if tx >= 1 else f"color: {COR_LARANJA}; font-weight:bold"

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
                resultado_pct = pago_he / limite if limite > 0 else None
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
        total_resultado = total_pago / total_limite if total_limite > 0 else None

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
        base["Resultado %"] = base["REALIZADO_CALC"] / base["META_CALC"].replace(0, pd.NA)
        base["Gap"] = base["META_CALC"] - base["REALIZADO_CALC"]
        base["MoM_%"] = base["REALIZADO_CALC"].pct_change() * 100
        base = base.replace([float("inf"), float("-inf")], pd.NA)

        return base.rename(columns={
            "MÊS_NOME": "Mês",
            "REALIZADO_CALC": "Pago em Hora Extra",
            "META_CALC": "Limite",
            "SALARIO_CALC": "Salário",
            "Gap": "Saldo do Limite",
        })[["ANO", "MÊS", "Mês", "Limite", "Pago em Hora Extra", "Salário", "HE da Folha", "Saldo do Limite", "Resultado %", "MoM_%", "MÊS_ORDEM"]]

    base = (
        d.groupby(["ANO", "MÊS", "MÊS_NOME", "MÊS_ORDEM"], as_index=False)
        .agg({"META_CALC": "sum", "REALIZADO_CALC": "sum"})
        .sort_values("MÊS_ORDEM")
        .reset_index(drop=True)
    )

    if eh_despesa_manutencao(indicador):
        base["Ating."] = base["REALIZADO_CALC"] / base["META_CALC"].replace(0, pd.NA)
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
        comp = (
            d[d["ANO"] == ano]
            .groupby("FILIAL", as_index=False)
            .agg({"REALIZADO_CALC": "sum", "META_CALC": "sum", "SALARIO_CALC": "sum"})
            .rename(columns={
                "REALIZADO_CALC": "Pago em Hora Extra",
                "META_CALC": "Limite",
                "SALARIO_CALC": "Salário",
            })
            .pipe(lambda _df: ordenar_df_seguro(_df, "Pago em Hora Extra", ascending=False))
        )
        comp["HE da Folha"] = comp["Pago em Hora Extra"] / comp["Salário"].replace(0, pd.NA)
        comp["Saldo do Limite"] = comp["Limite"] - comp["Pago em Hora Extra"]
        comp["Resultado %"] = comp["Pago em Hora Extra"] / comp["Limite"].replace(0, pd.NA)

        # Compatibilidade com blocos antigos
        comp["Realizado"] = comp["Pago em Hora Extra"]
        comp["Meta"] = comp["Limite"]
        comp["Gap"] = comp["Saldo do Limite"]
        comp["Atingimento"] = comp["Resultado %"]

        return comp

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
                "Meta Margem (R$)": resumo_ant["Meta Margem (R$)"],
                "Gap (R$)": resumo_ant["Gap (R$)"],
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
                "Meta Margem (R$)": resumo_atual["Meta Margem (R$)"],
                "Gap (R$)": resumo_atual["Gap (R$)"],
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
            x=dados["Mês"],
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
                x=dados["Mês"],
                y=dados["Meta"],
                name=rot["meta"],
                mode="lines+markers",
                line=dict(color=COR_LARANJA, width=3, dash="dot"),
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
        return fig

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
            x=dados["Mês"],
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
            x=dados["Mês"],
            y=dados["Meta R$"],
            name="Meta R$",
            mode="lines+markers+text",
            line=dict(color=COR_LARANJA, width=3, dash="dot"),
            marker=dict(size=7),
            text=[fmt_brl(v) for v in dados["Meta R$"]],
            textposition="top center",
            textfont=dict(size=10, color=COR_LARANJA),
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
        return fig

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
            x=dados["Mês"],
            y=dados["Receita"],
            name="Receita",
            marker_color=cores_receita,
            marker_cornerradius=4,
            text=[fmt_brl(v) for v in dados["Receita"]],
            textposition="outside",
            textfont=dict(size=11),
        )
        fig.add_scatter(
            x=dados["Mês"],
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
        return fig

    if indicador and eh_despesa_geral(indicador):
        dados = df_completa[(df_completa["Mês"] != "TOTAL") & (df_completa["Despesa"].notna())].copy()
        if dados.empty:
            return None

        ultimo_mes = dados["Mês"].iloc[-1]
        titulo_final = titulo or f"Despesa Geral x Limite sobre Receita — até {ultimo_mes}/{ano}"

        limite_rs = dados["Receita"] * dados["Limite %"]
        cor_barras = [
            COR_VERDE if pd.notna(tx) and pd.notna(limite) and tx <= limite else COR_LARANJA
            for tx, limite in zip(dados["Tx. Sucesso"], dados["Limite %"])
        ]

        fig = go.Figure()
        fig.add_bar(
            x=dados["Mês"],
            y=dados["Despesa"],
            name="Despesa",
            marker_color=cor_barras,
            marker_cornerradius=4,
            text=[fmt_brl(v) for v in dados["Despesa"]],
            textposition="outside",
            textfont=dict(size=11),
            hovertemplate="<b>%{x}</b><br>Despesa: R$ %{y:,.0f}<extra></extra>",
        )
        fig.add_scatter(
            x=dados["Mês"],
            y=limite_rs,
            name="Limite R$",
            mode="lines+markers",
            line=dict(color=COR_LARANJA, width=3, dash="dot"),
            marker=dict(size=7),
            hovertemplate="<b>%{x}</b><br>Limite R$: R$ %{y:,.0f}<extra></extra>",
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
            x=dados["Mês"],
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
            x=dados["Mês"],
            y=dados["Limite"],
            name=nome_meta,
            mode="lines+markers",
            line=dict(color=COR_LARANJA, width=3, dash="dot"),
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
        return fig

    dados = df_completa[(df_completa["Mês"] != "TOTAL") & (df_completa["Realizado"].notna())].copy()
    if dados.empty:
        return None

    ultimo_mes = dados["Mês"].iloc[-1]

    if indicador and eh_despesa_manutencao(indicador):
        titulo_final = titulo or f"Despesa x Limite — até {ultimo_mes}/{ano}"
        nome_realizado = "Despesa"
        nome_meta = "Limite"
        cor_barras = [
            COR_VERDE if pd.notna(r) and pd.notna(m) and r <= m else COR_LARANJA
            for r, m in zip(dados["Realizado"], dados["Meta"])
        ]
    else:
        titulo_final = titulo or f"Realizado x Meta — até {ultimo_mes}/{ano}"
        nome_realizado = "Realizado"
        nome_meta = "Meta"
        cor_barras = [COR_VERDE if r >= m else COR_LARANJA for r, m in zip(dados["Realizado"], dados["Meta"])]

    texto_barras = []
    for realizado_valor, meta_valor in zip(dados["Realizado"], dados["Meta"]):
        if pd.notna(realizado_valor) and pd.notna(meta_valor) and meta_valor != 0:
            texto_barras.append(fmt_pct(realizado_valor / meta_valor))
        else:
            texto_barras.append("")

    fig = go.Figure()
    fig.add_bar(
        x=dados["Mês"],
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
        x=dados["Mês"],
        y=dados["Meta"],
        name=nome_meta,
        mode="lines+markers+text",
        line=dict(color=COR_LARANJA, width=3, dash="dot"),
        marker=dict(size=7),
        text=[fmt_brl(v) for v in dados["Meta"]],
        textposition="top center",
        textfont=dict(size=10, color=COR_LARANJA),
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
            headers = ["Mês", "Despesa", "Limite", "Saldo", "Uso"]
        else:
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
            mom_txt = f"{row['MoM_%']:+.1f}%" if pd.notna(row["MoM_%"]) else "-"
            self.cell(widths[0], 6, self.safe(str(row["Mês"])), border=1)
            self.cell(widths[1], 6, fmt_brl(row["Realizado"]), border=1, align="R")
            self.cell(widths[2], 6, fmt_brl(row["META"]), border=1, align="R")
            self.cell(widths[3], 6, fmt_brl(row["Gap"]), border=1, align="R")
            self.cell(widths[4], 6, fmt_pct(row["Ating."]), border=1, align="R")
            self.cell(widths[5], 6, mom_txt, border=1, align="R")
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
    pdf.tabela_por_ano(tabela_pneus_moto_ano(df, ano_selecionado) if eh_moto_margem(indicador) else tabela_completa_ano(df, ano_selecionado, indicador))

    pdf.add_page()
    pdf.secao("4. Variação Mês a Mês - Últimos 12 meses")
    df_mom = calcular_mom(df, indicador).sort_values("MÊS_ORDEM").tail(12).copy()
    if eh_moto_margem(indicador):
        pdf.tabela_mom(df_mom[["Mês", "META", "Compra", "Realizado", "Margem Bruta", "Gap", "Ating.", "Acumulado", "MoM_%"]])
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
        pdf.tabela_mom(df_mom[["Mês", "Meta KG", "Meta R$", "Realizado KG", "Realizado R$", "% Dif. KG", "% Dif. R$", "Dif. KG", "Dif. R$", "Acum. KG", "Acum. R$", "MoM_%"]])
    elif eh_resultado_financeiro(indicador):
        pdf.tabela_mom(df_mom[["Mês", "Meta %", "Despesa", "Receita", "Resultado R$", "Resultado %", "Acumulado", "MoM_%"]])
    elif eh_despesa_geral(indicador):
        pdf.tabela_mom(df_mom[["Mês", "Limite %", "Despesa", "Receita", "Resultado R$", "Tx. Sucesso", "Acumulado", "MoM_%"]])
    elif eh_despesa_hora_extra(indicador):
        pdf.tabela_mom(df_mom[["Mês", "Limite", "Pago em Hora Extra", "Salário", "HE da Folha", "Saldo do Limite", "Resultado %", "MoM_%"]])
    else:
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

# =========================
# SIDEBAR
# =========================
with st.sidebar:
    # Cabeçalho da sidebar com ícone personalizado alinhado
    # Arquivo esperado no GitHub: reciclar-simbolo.png
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
                margin: 0 0 18px 0;
                padding: 0;
                line-height: 1;
            ">
                <img src="data:image/png;base64,{img_base64}" style="
                    width: 22px;
                    height: 22px;
                    object-fit: contain;
                    display: block;
                    margin: 0;
                    padding: 0;
                ">
                <span style="
                    font-size: 1.45rem;
                    font-weight: 700;
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
                margin: 0 0 18px 0;
                line-height: 1;
            ">
                <span style="font-size: 22px; line-height: 1;">♻️</span>
                <span style="font-size: 1.45rem; font-weight: 700; line-height: 1;">Filtros</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # Usuário comum NÃO vê a origem da base.
    # Ele fica travado no Google Drive, com o arquivo padrão.
    if eh_admin():
        fonte_dados = st.session_state.get("fonte_dados_admin", "Google Drive")
        nome_arquivo_drive = st.session_state.get("nome_arquivo_drive_admin", "BaseSistema.xlsx")
        arquivo = None
        st.caption("🔐 Fonte da base: configuração disponível na aba Opções.")
    else:
        fonte_dados = "Google Drive"
        nome_arquivo_drive = "BaseSistema.xlsx"
        arquivo = None

    st.divider()
    indicador = selecionar_indicador_por_blocos()
    filial = st.selectbox("Filial", ["Geral"] + FILIAIS_REAIS)

    st.divider()
    usuario_logado = st.session_state.get("usuario_logado", "usuário")
    st.caption(f"👤 Logado como: **{usuario_logado}**")
    st.caption(f"Perfil: **{nome_perfil_exibicao()}**")

    # Mostra tempo restante de sessão (sutil)
    ultimo_acesso = st.session_state.get("ultimo_acesso", time.time())
    minutos_inativo = int((time.time() - ultimo_acesso) / 60)
    minutos_restantes = max(0, LIMITE_SESSAO_MINUTOS - minutos_inativo)
    if minutos_restantes <= 10:
        st.caption(f"⏰ Sessão expira em ~{minutos_restantes} min")

    if st.button("🔄 Atualizar dados", use_container_width=True, help="Força nova leitura da planilha (limpa o cache)"):
        st.cache_data.clear()
        st.success("Dados atualizados!")
        st.rerun()

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

    st.caption("v12.8 — Análise de Indicadores")


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


tab0, tab1, tab2, tab3, tab4 = st.tabs([
    label_aba_com_icone("caminhao-de-lixo (1).png", "Visão Geral", "📊"),
    "📅 Por Ano",
    "📈 MoM",
    "🔁 YoY",
    "📤 Opções"
])


# =========================
# ABA VISÃO GERAL
# =========================
with tab0:
    st.subheader(f"Visão Geral — {indicador} | {filial}")

    anos = sorted(df["ANO"].dropna().unique())
    ano_kpi = int(anos[-1])
    base_kpi = df[df["ANO"] == ano_kpi].copy()

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
            st.markdown(card_html("Faturamento Ano", fmt_brl(resumo_ano_moto["Faturamento"])), unsafe_allow_html=True)
        with c2:
            st.markdown(card_html("Compra Ano", fmt_brl(resumo_ano_moto["Compra"])), unsafe_allow_html=True)
        with c3:
            st.markdown(card_html("Margem Bruta Ano", fmt_brl(resumo_ano_moto["Margem Bruta"])), unsafe_allow_html=True)
        with c4:
            st.markdown(card_html("Tx. Sucesso", fmt_pct(resumo_ano_moto["Tx. Sucesso"])), unsafe_allow_html=True)
        with c5:
            st.markdown(card_html(f"YTD {periodo_label}", fmt_brl(ytd_valor) if ytd_valor is not None else "-", delta_ytd), unsafe_allow_html=True)

        st.divider()

        df_dashboard_ano = tabela_pneus_moto_ano(df, ano_kpi)
        dados_chart = df_dashboard_ano[(df_dashboard_ano["Mês"] != "TOTAL") & (df_dashboard_ano["Faturamento"].notna())]

        if not dados_chart.empty:
            fig_dash = go.Figure()

            cores_tx = [
                COR_VERDE if pd.notna(tx) and pd.notna(meta) and tx >= meta else COR_LARANJA
                for tx, meta in zip(dados_chart["Tx. Sucesso"], dados_chart["Meta"])
            ]

            fig_dash.add_bar(
                x=dados_chart["Mês"],
                y=dados_chart["Tx. Sucesso"],
                name="Tx. Sucesso",
                marker_color=cores_tx,
                text=[fmt_pct(v) for v in dados_chart["Tx. Sucesso"]],
                textposition="outside",
            )

            if ano_kpi >= 2026:
                fig_dash.add_scatter(
                    x=dados_chart["Mês"],
                    y=dados_chart["Meta"],
                    name="Meta %",
                    mode="lines+markers",
                    line=dict(color=COR_LARANJA, width=3, dash="dot"),
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

        st.subheader(f"{indicador} — {filial} · Comparativo Ano a Ano")
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
            st.markdown(card_html(rot["meta"], meta_valor), unsafe_allow_html=True)
        with c2:
            st.markdown(card_html(rot["qtd_total"], fmt_num(resumo_ano_qualidade["Qtd. Coletas"])), unsafe_allow_html=True)
        with c3:
            st.markdown(card_html(rot["qtd_sucesso"], fmt_num(resumo_ano_qualidade["Qtd. Sucesso"])), unsafe_allow_html=True)
        with c4:
            st.markdown(card_html(rot["diferenca"], fmt_num(resumo_ano_qualidade["Diferença"])), unsafe_allow_html=True)
        with c5:
            st.markdown(card_html(rot["resultado"], fmt_pct(resumo_ano_qualidade["Resultado %"])), unsafe_allow_html=True)

        st.caption(f"YTD {periodo_label}: {fmt_pct(ytd_valor) if ytd_valor is not None else '-'}")

        st.divider()

        df_dashboard_ano = tabela_completa_ano(df, ano_kpi, indicador)
        dados_chart = df_dashboard_ano[df_dashboard_ano["Mês"] != "TOTAL"].dropna(subset=["Resultado %"])

        if not dados_chart.empty:
            fig_dash = grafico_realizado_meta(df_dashboard_ano, ano_kpi, indicador=indicador)
            st.plotly_chart(fig_dash, use_container_width=True, key="grafico_dashboard_qualidade")

        st.subheader(f"{indicador} — {filial} · Comparativo Ano a Ano")
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
            st.markdown(card_html("Realizado KG Ano", fmt_num(resumo_ano_tecfil["Realizado KG"])), unsafe_allow_html=True)
        with c2:
            st.markdown(card_html("Meta KG Ano", fmt_num(resumo_ano_tecfil["Meta KG"])), unsafe_allow_html=True)
        with c3:
            st.markdown(card_html("Diferença KG", fmt_num(resumo_ano_tecfil["Dif. KG"])), unsafe_allow_html=True)
        with c4:
            st.markdown(card_html("Realizado R$ Ano", fmt_brl(resumo_ano_tecfil["Realizado R$"])), unsafe_allow_html=True)
        with c5:
            st.markdown(card_html(f"YTD {periodo_label}", fmt_brl(ytd_valor) if ytd_valor is not None else "-", delta_ytd), unsafe_allow_html=True)

        st.divider()

        df_dashboard_ano = tabela_completa_ano(df, ano_kpi, indicador)
        dados_chart = df_dashboard_ano[df_dashboard_ano["Mês"] != "TOTAL"].dropna(subset=["Realizado R$"])

        if not dados_chart.empty:
            fig_dash = grafico_realizado_meta(df_dashboard_ano, ano_kpi, indicador=indicador)
            st.plotly_chart(fig_dash, use_container_width=True, key="grafico_dashboard_tecfil")

        st.subheader(f"{indicador} — {filial} · Comparativo Ano a Ano")
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
            st.markdown(card_html("Despesa Ano", fmt_brl(resumo_ano_rf["Despesa"])), unsafe_allow_html=True)
        with c2:
            st.markdown(card_html("Receita Ano", fmt_brl(resumo_ano_rf["Receita"])), unsafe_allow_html=True)
        with c3:
            st.markdown(card_html("Resultado Ano", fmt_brl(resumo_ano_rf["Resultado R$"])), unsafe_allow_html=True)
        with c4:
            st.markdown(card_html("Resultado %", fmt_pct(resumo_ano_rf["Resultado %"])), unsafe_allow_html=True)
        with c5:
            st.markdown(card_html(f"YTD {periodo_label}", fmt_brl(ytd_valor) if ytd_valor is not None else "-", delta_ytd), unsafe_allow_html=True)

        st.divider()

        df_dashboard_ano = tabela_completa_ano(df, ano_kpi, indicador)
        dados_chart = df_dashboard_ano[df_dashboard_ano["Mês"] != "TOTAL"].dropna(subset=["Resultado R$"])

        if not dados_chart.empty:
            fig_dash = grafico_realizado_meta(df_dashboard_ano, ano_kpi, indicador=indicador)
            st.plotly_chart(fig_dash, use_container_width=True, key="grafico_dashboard_resultado_financeiro")

        st.subheader(f"{indicador} — {filial} · Comparativo Ano a Ano")
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
            st.markdown(card_html("Despesa Ano", fmt_brl(resumo_ano_geral["Despesa"])), unsafe_allow_html=True)
        with c2:
            st.markdown(card_html("Receita Ano", fmt_brl(resumo_ano_geral["Receita"])), unsafe_allow_html=True)
        with c3:
            st.markdown(card_html("Resultado Ano", fmt_brl(resumo_ano_geral["Resultado R$"])), unsafe_allow_html=True)
        with c4:
            st.markdown(card_html("Tx. Sucesso", fmt_pct(resumo_ano_geral["Tx. Sucesso"])), unsafe_allow_html=True)
        with c5:
            st.markdown(card_html(f"YTD {periodo_label}", fmt_brl(ytd_valor) if ytd_valor is not None else "-", delta_ytd), unsafe_allow_html=True)

        st.divider()

        df_dashboard_ano = tabela_completa_ano(df, ano_kpi, indicador)
        dados_chart = df_dashboard_ano[df_dashboard_ano["Mês"] != "TOTAL"].dropna(subset=["Despesa"])

        if not dados_chart.empty:
            fig_dash = grafico_realizado_meta(df_dashboard_ano, ano_kpi, indicador=indicador)
            st.plotly_chart(fig_dash, use_container_width=True, key="grafico_dashboard_despesa_geral")

        st.subheader(f"{indicador} — {filial} · Comparativo Ano a Ano")
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
            st.markdown(card_html(f"{rotulo_meta(indicador)} Ano", fmt_brl(meta_total)), unsafe_allow_html=True)
        with c2:
            st.markdown(card_html(f"{rotulo_realizado(indicador)} Ano", fmt_brl(realizado_total)), unsafe_allow_html=True)
        with c3:
            st.markdown(card_html(f"{rotulo_gap(indicador)} Ano", fmt_brl(gap_total)), unsafe_allow_html=True)
        with c4:
            st.markdown(card_html(("Uso do limite" if eh_despesa_manutencao(indicador) else "Atingimento da meta"), fmt_pct(ating_total)), unsafe_allow_html=True)
        with c5:
            st.markdown(card_html(f"YTD {periodo_label}", fmt_brl(realizado_ytd) if realizado_ytd is not None else "-", delta_ytd), unsafe_allow_html=True)

        st.divider()

        df_dashboard_ano = tabela_completa_ano(df, ano_kpi, indicador)

        if eh_despesa_hora_extra(indicador):
            dados_chart = df_dashboard_ano[df_dashboard_ano["Mês"] != "TOTAL"].dropna(subset=["Pago em Hora Extra"])
        else:
            dados_chart = (
            df_dashboard_ano[df_dashboard_ano["Mês"] != "TOTAL"].dropna(subset=["Pago em Hora Extra"])
            if eh_despesa_hora_extra(indicador)
            else df_dashboard_ano[df_dashboard_ano["Mês"] != "TOTAL"].dropna(subset=["Realizado"])
        )

        if not dados_chart.empty:
            ultimo_mes = dados_chart["Mês"].iloc[-1]
            fig_dash = grafico_realizado_meta(df_dashboard_ano, ano_kpi, indicador=indicador)
            st.plotly_chart(fig_dash, use_container_width=True, key="grafico_dashboard")

        st.subheader(f"{indicador} — {filial} · Comparativo Ano a Ano")
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
            df_completa_tela = df_completa_tela.rename(columns={
                "Realizado": "Despesa",
                "Meta": "Limite",
                "Gap (R$)": "Saldo do Limite",
                "Atingimento": "Uso do Limite",
            })
            st.dataframe(
                df_completa_tela.style
                .format({
                    "Despesa": lambda v: fmt_brl(v) if pd.notna(v) else "",
                    "Limite": lambda v: fmt_brl(v) if pd.notna(v) else "",
                    "Saldo do Limite": lambda v: f"R$ {v:+,.0f}".replace(",", ".") if pd.notna(v) else "",
                    "Uso do Limite": lambda v: f"{v:.0%}" if pd.notna(v) else "",
                })
                .map(lambda v: cor_gap_valor(v, False), subset=["Saldo do Limite"])
                .map(cor_despesa_manutencao, subset=["Uso do Limite"]),
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

    st.divider()
    if eh_moto_margem(indicador):
        dados_chart = df_completa[(df_completa["Mês"] != "TOTAL") & (df_completa["Faturamento"].notna())]
        if not dados_chart.empty:
            fig_ano = go.Figure()

            cores_tx = [
                COR_VERDE if pd.notna(tx) and pd.notna(meta) and tx >= meta else COR_LARANJA
                for tx, meta in zip(dados_chart["Tx. Sucesso"], dados_chart["Meta"])
            ]

            fig_ano.add_bar(
                x=dados_chart["Mês"],
                y=dados_chart["Tx. Sucesso"],
                name="Tx. Sucesso",
                marker_color=cores_tx,
                text=[fmt_pct(v) for v in dados_chart["Tx. Sucesso"]],
                textposition="outside",
            )

            if ano_selecionado >= 2026:
                fig_ano.add_scatter(
                    x=dados_chart["Mês"],
                    y=dados_chart["Meta"],
                    name="Meta %",
                    mode="lines+markers",
                    line=dict(color=COR_LARANJA, width=3, dash="dot"),
                    marker=dict(size=7),
                )

            fig_ano.update_layout(
                title=f"Pneus Moto — Taxa de Sucesso x Meta — {ano_selecionado}",
                height=420,
                legend=dict(orientation="h", y=-0.18),
                yaxis_title="%",
                yaxis_tickformat=".0%",
            )

            st.plotly_chart(fig_ano, use_container_width=True, key="grafico_por_ano_moto")
    else:
        fig_ano = grafico_realizado_meta(df_completa, ano_selecionado, indicador=indicador)
        if fig_ano is not None:
            st.plotly_chart(fig_ano, use_container_width=True, key="grafico_por_ano")


# =========================
# ABA MOM
# =========================

with tab2:
    st.subheader(f"{indicador} — {filial} · Variação Mês a Mês")
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
                    "Diferença", "Resultado %", "Acumulado", "MoM_%"
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
                "MoM_%": None,
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
                "MoM_%": fmt_mom_seguro,
            })
            .apply(lambda row: cor_resultado_qualidade_por_linha(row, indicador), axis=1)
            .map(cor_variacao, subset=["MoM_%"])
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
                    "% Dif. KG", "% Dif. R$", "Dif. KG", "Dif. R$", "Acum. KG", "Acum. R$", "MoM_%"
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
                "MoM_%": None,
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
                "MoM_%": fmt_mom_seguro,
            })
            .map(cor_tecfil_resultado, subset=["% Dif. KG", "% Dif. R$", "Dif. KG", "Dif. R$", "Acum. KG", "Acum. R$"])
            .map(cor_variacao, subset=["MoM_%"]),
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
                    "Resultado R$", "Resultado %", "Acumulado", "MoM_%"
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
                "MoM_%": None,
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
                "MoM_%": fmt_mom_seguro,
            })
            .apply(cor_resultado_financeiro_por_linha, axis=1)
            .map(cor_variacao, subset=["MoM_%"]),
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
                    "Resultado R$", "Tx. Sucesso", "Acumulado", "MoM_%"
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
                "MoM_%": None,
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
                "MoM_%": fmt_mom_seguro,
            })
            .map(lambda v: cor_gap_valor(v, False), subset=["Resultado R$"])
            .apply(cor_tx_sucesso_despesa_geral_por_linha, axis=1)
            .map(cor_variacao, subset=["MoM_%"]),
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
                    "Salário", "HE da Folha", "Saldo do Limite", "Resultado %", "MoM_%"
                ]]
            )

            total_limite = base_ano_he["Limite"].sum()
            total_pago = base_ano_he["Pago em Hora Extra"].sum()
            total_salario = base_ano_he["Salário"].sum()
            total_he_folha = total_pago / total_salario if total_salario > 0 else None
            total_saldo = total_limite - total_pago
            total_resultado = total_pago / total_limite if total_limite > 0 else None

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
                "MoM_%": None,
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
                "MoM_%": fmt_mom_seguro,
            })
            .map(lambda v: cor_gap_valor(v, False), subset=["Saldo do Limite"])
            .map(cor_despesa_manutencao, subset=["Resultado %"])
            .map(cor_variacao, subset=["MoM_%"]),
            use_container_width=True,
            hide_index=True,
        )

    else:
        linhas_finais = []

        for ano in sorted(df_mom["ANO"].dropna().unique()):
            base_ano = df_mom[df_mom["ANO"] == ano].copy()

            linhas_finais.append(base_ano[["ANO", "MÊS", "Mês", "META", "Realizado", "Gap", "Ating.", "MoM_%"]])

            total_meta = base_ano["META"].sum()
            total_realizado = base_ano["Realizado"].sum()

            if eh_despesa_manutencao(indicador):
                # Despesa Manutenção:
                # Limite é o máximo que pode gastar.
                # Uso do Limite = Despesa / Limite.
                total_gap = total_meta - total_realizado
                total_ating = total_realizado / total_meta if total_meta > 0 else None

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
                "MoM_%": None,
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
                "Gap": "Saldo do Limite",
                "Ating.": "Uso do Limite",
                "MoM_%": "MoM_%",
            })

            st.dataframe(
                df_mom_tela_exibir.style
                .apply(destacar_total, axis=1)
                .format({
                    "ANO": lambda v: f"{int(v)}" if pd.notna(v) else "",
                    "MÊS": lambda v: f"{int(v)}" if pd.notna(v) else "",
                    "Limite": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                    "Despesa": lambda v: fmt_brl(v) if pd.notna(v) else "—",
                    "Saldo do Limite": lambda v: f"R$ {v:+,.0f}".replace(",", ".") if pd.notna(v) else "—",
                    "Uso do Limite": lambda v: f"{v:.0%}" if pd.notna(v) else "—",
                    "MoM_%": fmt_mom_seguro,
                })
                .map(lambda v: cor_gap_valor(v, False), subset=["Saldo do Limite"])
                .map(cor_despesa_manutencao, subset=["Uso do Limite"])
                .map(cor_variacao, subset=["MoM_%"]),
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
                    "MoM_%": fmt_mom_seguro,
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

        st.divider()
        st.subheader("Mesmo período x ano anterior")
        df_periodo_tela = comparar_mesmo_periodo(df, indicador)

        if not df_periodo_tela.empty:
            st.dataframe(
                df_periodo_tela[["Ano", "Período", "Faturamento", "Compra", "Margem Bruta", "Meta %", "Meta Margem (R$)", "Tx. Sucesso"]].style
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

            ano_base_filial = st.selectbox("Ano do comparativo entre filiais", anos_filial, index=len(anos_filial) - 1, key="ano_filiais")
            st.subheader(f"Comparativo entre unidades — {ano_base_filial}" if eh_tecfil(indicador) else f"Comparativo entre filiais — {ano_base_filial}")

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
                line=dict(color=COR_LARANJA, width=3, dash="dot"),
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

        st.divider()
        st.subheader("Mesmo período x ano anterior")
        df_periodo_tela = comparar_mesmo_periodo(df, indicador)

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

            ano_base_filial = st.selectbox("Ano do comparativo entre filiais", anos_filial, index=len(anos_filial) - 1, key="ano_filiais")
            st.subheader(f"Comparativo entre unidades — {ano_base_filial}" if eh_tecfil(indicador) else f"Comparativo entre filiais — {ano_base_filial}")

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
                    line=dict(color=COR_LARANJA, width=3, dash="dot"),
                    marker=dict(size=7),
                    text=[fmt_brl(v) for v in comp_filiais["Meta R$"]],
                    textposition="top center",
                    textfont=dict(size=10, color=COR_LARANJA),
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
                limite_rs_filiais = comp_filiais["Receita"] * comp_filiais["Limite %"]
                cores = [
                    COR_VERDE if pd.notna(tx) and pd.notna(limite) and tx <= limite else COR_LARANJA
                    for tx, limite in zip(comp_filiais["Tx. Sucesso"], comp_filiais["Limite %"])
                ]
                fig_filiais.add_bar(
                    x=comp_filiais["FILIAL"],
                    y=comp_filiais["Despesa"],
                    name="Despesa",
                    marker_color=cores,
                    marker_cornerradius=4,
                    text=[fmt_brl(v) for v in comp_filiais["Despesa"]],
                    textposition="outside",
                )
                fig_filiais.add_scatter(
                    x=comp_filiais["FILIAL"],
                    y=limite_rs_filiais,
                    name="Limite R$",
                    mode="lines+markers",
                    line=dict(color=COR_LARANJA, width=3, dash="dot"),
                )
                fig_filiais.update_layout(height=420, margin=dict(t=30, b=20, l=20, r=20), legend=dict(orientation="h", y=-0.15))
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
                    line=dict(color=COR_LARANJA, width=3, dash="dot"),
                    marker=dict(size=7),
                    text=[fmt_brl(v) for v in comp_filiais["Meta"]],
                    textposition="top center",
                    textfont=dict(size=10, color=COR_LARANJA),
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
# ABA OPÇÕES
# =========================
with tab4:
    st.subheader(f"Opções — {indicador} | {filial}")
    st.caption("Nesta aba você pode baixar o PDF do relatório ou enviar o relatório por e-mail.")

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
