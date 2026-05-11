import io
import logging
import smtplib
from email.message import EmailMessage

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

from maz_config import SCOPES_DRIVE


@st.cache_data(ttl=300, show_spinner=False)
def baixar_planilha_drive(nome_arquivo="BaseSistema.xlsx"):
    """Busca a planilha no Google Drive. Cache de 5 minutos."""
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

        query_partes = [f"name = '{nome_query}'", "trashed = false"]
        if pasta_id:
            query_partes.append(f"'{pasta_id}' in parents")

        resultado = service.files().list(
            q=" and ".join(query_partes),
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

        request = service.files().get_media(fileId=arquivos[0]["id"])
        arquivo_bytes = io.BytesIO()
        downloader = MediaIoBaseDownload(arquivo_bytes, request)

        done = False
        while not done:
            _, done = downloader.next_chunk()

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
    Envia o relatório em PDF por e-mail via SMTP.

    Secrets necessários: EMAIL_HOST, EMAIL_PORT, EMAIL_USER, EMAIL_PASSWORD.
    Opcionais: EMAIL_FROM, EMAIL_USE_TLS.
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
        msg.add_attachment(pdf_bytes, maintype="application", subtype="pdf", filename=nome_arquivo)

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
