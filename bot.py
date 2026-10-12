import os
import io
import time
import base64
import asyncio
import logging

import requests
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, ContextTypes, filters

load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Modelo principal y de respaldo. Verificá el nombre del respaldo en tu cuenta de Gemini.
MODELOS = [m.strip() for m in os.getenv("MODELOS", "").split(",") if m.strip()] or [
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.1-flash-lite",
]
TIMEOUT_TEXTO = 30     # consultas de texto
TIMEOUT_ARCHIVO = 60   # charlas armadas desde archivos (respuesta más larga)
REINTENTOS_503 = 3
MAX_TELEGRAM = 4000
MAX_BYTES = 15 * 1024 * 1024  # tope razonable para archivos

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASE_DIR, "contexto.txt"), "r", encoding="utf-8") as f:
    CONTEXTO = f.read()

SYSTEM_INSTRUCTION = (
    "Sos el Asistente HYS, un asistente para técnicos en Seguridad e Higiene de Argentina. "
    "Respondé siempre en español rioplatense, en texto corrido, sin títulos, sin listas con símbolos "
    "y sin markdown (nada de asteriscos, numerales ni guiones decorativos). "
    "Apoyate en el siguiente material, que es la base de tu criterio:\n\n" + CONTEXTO
)

PROMPT_CHARLA = (
    "Con el material adjunto, armá una charla de seguridad de 5 minutos para dar de pie frente al equipo, "
    "lista para leer en voz alta (unas 600 a 700 palabras). Arrancá con un gancho que capte la atención, "
    "explicá lo importante del material con ejemplos concretos del trabajo diario, incluí qué hacer y qué "
    "no hacer, y cerrá con una pregunta o compromiso para el grupo. Hablá en rioplatense, texto corrido, "
    "sin títulos ni markdown."
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("asistente_hys")
logging.getLogger("httpx").setLevel(logging.WARNING)


class ErrorIA(Exception):
    pass


def llamar_ia(prompt_usuario, partes_extra=None, timeout=TIMEOUT_TEXTO):
    """Llama a Gemini. 503: reintenta en el mismo modelo. 429: pasa al siguiente modelo sin reintentar."""
    partes = [{"text": prompt_usuario}]
    if partes_extra:
        partes.extend(partes_extra)
    cuerpo = {
        "system_instruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
        "contents": [{"role": "user", "parts": partes}],
    }
    headers = {"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY}
    ultimo_error = "sin detalle"

    for modelo in MODELOS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
        for intento in range(1, REINTENTOS_503 + 1):
            try:
                r = requests.post(url, headers=headers, json=cuerpo, timeout=timeout)
            except requests.exceptions.RequestException as e:
                ultimo_error = f"{modelo}: {e}"
                log.warning("Error de red con %s: %s", modelo, e)
                break  # pasa al siguiente modelo

            if r.status_code == 200:
                try:
                    texto = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                    return texto.strip()
                except (KeyError, IndexError, ValueError):
                    ultimo_error = f"{modelo}: respuesta vacía o bloqueada"
                    log.warning(ultimo_error)
                    break

            if r.status_code == 429:
                ultimo_error = f"{modelo}: cuota agotada (429)"
                log.warning(ultimo_error)
                break  # no reintenta, siguiente modelo

            if r.status_code == 503:
                ultimo_error = f"{modelo}: sobrecargado (503)"
                log.warning("%s, intento %d/%d", ultimo_error, intento, REINTENTOS_503)
                if intento < REINTENTOS_503:
                    time.sleep(2 * intento)
                    continue
                break

            ultimo_error = f"{modelo}: HTTP {r.status_code} {r.text[:200]}"
            log.error(ultimo_error)
            break

    raise ErrorIA(ultimo_error)


def partir_mensaje(texto, limite=MAX_TELEGRAM):
    """Parte el texto en trozos de hasta `limite`, cortando en saltos de línea o espacios."""
    trozos = []
    while len(texto) > limite:
        corte = texto.rfind("\n", 0, limite)
        if corte < limite // 2:
            corte = texto.rfind(" ", 0, limite)
        if corte <= 0:
            corte = limite
        trozos.append(texto[:corte].strip())
        texto = texto[corte:].strip()
    if texto:
        trozos.append(texto)
    return trozos


async def responder_largo(update, texto):
    for trozo in partir_mensaje(texto):
        await update.message.reply_text(trozo)


def texto_a_docx(texto, titulo="Charla de seguridad de 5 minutos"):
    """Genera un .docx en memoria. Si python-docx no está, devuelve (bytes txt, 'txt')."""
    try:
        from docx import Document
    except ImportError:
        return texto.encode("utf-8"), "txt"
    doc = Document()
    doc.add_heading(titulo, level=1)
    for parrafo in texto.split("\n"):
        if parrafo.strip():
            doc.add_paragraph(parrafo.strip())
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue(), "docx"


def extraer_docx(datos):
    from docx import Document
    doc = Document(io.BytesIO(datos))
    texto = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    for tabla in doc.tables:
        for fila in tabla.rows:
            texto += "\n" + " | ".join(c.text.strip() for c in fila.cells)
    return texto


def preparar_archivo(nombre, mime, datos):
    """Devuelve (texto_o_None, partes_extra). Para PDF e imagen usa inline_data de Gemini."""
    n = (nombre or "").lower()
    mime = mime or ""
    if n.endswith(".docx"):
        return extraer_docx(datos), None
    if n.endswith(".txt") or mime.startswith("text/"):
        return datos.decode("utf-8", errors="replace"), None
    if n.endswith(".pdf") or mime == "application/pdf":
        parte = {"inline_data": {"mime_type": "application/pdf", "data": base64.b64encode(datos).decode()}}
        return None, [parte]
    if mime.startswith("image/") or n.endswith((".jpg", ".jpeg", ".png", ".webp")):
        mt = mime if mime.startswith("image/") else "image/jpeg"
        parte = {"inline_data": {"mime_type": mt, "data": base64.b64encode(datos).decode()}}
        return None, [parte]
    return None, None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hola, soy el Asistente HYS. Escribime una consulta, o mandame un Word, PDF, texto o foto "
        "y te armo una charla de seguridad de 5 minutos con ese material."
    )


async def manejar_texto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    consulta = update.message.text
    await update.message.chat.send_action("typing")
    try:
        respuesta = await asyncio.to_thread(llamar_ia, consulta)
    except ErrorIA as e:
        log.error("Falló la IA: %s", e)
        await update.message.reply_text("Ahora no pude consultar a la IA. Probá de nuevo en un ratito.")
        return
    await responder_largo(update, respuesta)


async def manejar_archivo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg.document:
        nombre, mime, tam = msg.document.file_name, msg.document.mime_type, msg.document.file_size
        archivo_id = msg.document.file_id
    else:
        foto = msg.photo[-1]
        nombre, mime, tam = "foto.jpg", "image/jpeg", foto.file_size
        archivo_id = foto.file_id

    if tam and tam > MAX_BYTES:
        await msg.reply_text("El archivo es muy pesado. Mandame uno de menos de 15 MB.")
        return

    await msg.reply_text("Recibido, estoy armando la charla. Dame unos segundos.")
    await msg.chat.send_action("typing")

    try:
        tg_file = await context.bot.get_file(archivo_id)
        datos = bytes(await tg_file.download_as_bytearray())
        texto, partes = await asyncio.to_thread(preparar_archivo, nombre, mime, datos)
    except Exception as e:
        log.exception("No pude leer el archivo: %s", e)
        await msg.reply_text("No pude leer ese archivo. Probá con un Word (.docx), PDF, texto o imagen.")
        return

    if texto is None and partes is None:
        await msg.reply_text("Ese formato no lo manejo. Mandame Word (.docx), PDF, texto o imagen.")
        return

    prompt = PROMPT_CHARLA
    if msg.caption:
        prompt += "\n\nIndicación extra de quien lo envía: " + msg.caption
    if texto:
        prompt += "\n\nMATERIAL:\n" + texto[:60000]

    try:
        charla = await asyncio.to_thread(llamar_ia, prompt, partes, TIMEOUT_ARCHIVO)
    except ErrorIA as e:
        log.error("Falló la IA: %s", e)
        await msg.reply_text("Ahora no pude generar la charla. Probá de nuevo en un ratito.")
        return

    await responder_largo(update, charla)

    contenido, ext = await asyncio.to_thread(texto_a_docx, charla)
    bio = io.BytesIO(contenido)
    bio.name = f"charla_5_minutos.{ext}"
    await msg.reply_document(document=bio, caption="Acá tenés la charla para descargar.")


def main():
    if not TELEGRAM_BOT_TOKEN or not GEMINI_API_KEY:
        raise SystemExit("Faltan TELEGRAM_BOT_TOKEN o GEMINI_API_KEY en el .env")
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, manejar_archivo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, manejar_texto))
    log.info("Bot en marcha")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()