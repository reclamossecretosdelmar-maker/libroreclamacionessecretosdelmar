from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import requests
import sqlite3
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Optional

from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas


# =========================================================
# RUTAS BASE
# =========================================================
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "libro_reclamaciones.db"


# =========================================================
# DATOS EMPRESA
# =========================================================
EMPRESA_RUC = "10095322455"
EMPRESA_RAZON_SOCIAL = "CHUMBIAUCA GOMEZ OSCAR ALBERTO"
EMPRESA_DIRECCION = "LAS BAHIAS S/N INTERIOR 3 SAN BARTOLO - LIMA - PERU"
EMPRESA_EMAIL = "reclamosbodegaoscar@gmail.com"


# =========================================================
# =========================================================
# RESEND API
# =========================================================
RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "onboarding@resend.dev")


# =========================================================
# CONFIG GENERAL
# =========================================================
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key")
TOKEN_SECRET = os.environ.get("TOKEN_SECRET", "dev-token-secret")
PUBLIC_LIST_TOKEN = os.environ.get("PUBLIC_LIST_TOKEN", "dev-public-list-token")

app = Flask(__name__)
app.secret_key = SECRET_KEY




# =========================================================
# DB
# =========================================================
def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_conn()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS reclamos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            numero TEXT NOT NULL UNIQUE,
            fecha_registro TEXT NOT NULL,

            nombres TEXT NOT NULL,
            apellidos TEXT NOT NULL,
            tipo_doc TEXT NOT NULL,
            num_doc TEXT NOT NULL,
            telefono TEXT,
            email_seguimiento TEXT,
            email_cliente TEXT,
            direccion TEXT,

            menor_edad INTEGER NOT NULL DEFAULT 0,
            padre_madre_tutor TEXT,

            bien_contratado TEXT NOT NULL,
            monto_reclamado REAL,
            descripcion_bien TEXT,

            tipo_solicitud TEXT NOT NULL,      -- Reclamo / Queja
            detalle TEXT NOT NULL,
            pedido TEXT NOT NULL,

            acepta_notificacion_email INTEGER NOT NULL DEFAULT 0,

            estado TEXT NOT NULL DEFAULT 'Pendiente',
            respuesta TEXT,
            fecha_respuesta TEXT,

            token_estado TEXT NOT NULL UNIQUE
        )
    """)

    columnas = [row["name"] for row in conn.execute("PRAGMA table_info(reclamos)").fetchall()]

    if "email_seguimiento" not in columnas:
        conn.execute("ALTER TABLE reclamos ADD COLUMN email_seguimiento TEXT")

    if "email_cliente" not in columnas:
        conn.execute("ALTER TABLE reclamos ADD COLUMN email_cliente TEXT")

    conn.commit()
    conn.close()
init_db()

# =========================================================
# HELPERS
# =========================================================
def generar_numero(conn: sqlite3.Connection) -> str:
    anio = datetime.now().year
    prefijo = f"LR-{anio}-"

    row = conn.execute(
        "SELECT numero FROM reclamos WHERE numero LIKE ? ORDER BY id DESC LIMIT 1",
        (f"{prefijo}%",)
    ).fetchone()

    correlativo = 1
    if row:
        ultimo = row["numero"]
        try:
            correlativo = int(ultimo.split("-")[-1]) + 1
        except Exception:
            correlativo = 1

    return f"{prefijo}{correlativo:06d}"


def dividir_texto(texto: str, max_len: int) -> list[str]:
    palabras = (texto or "").split()
    if not palabras:
        return [""]

    lineas = []
    actual = ""

    for palabra in palabras:
        test = f"{actual} {palabra}".strip()
        if len(test) <= max_len:
            actual = test
        else:
            if actual:
                lineas.append(actual)
            actual = palabra

    if actual:
        lineas.append(actual)

    return lineas


def generar_token_estado() -> str:
    return secrets.token_urlsafe(24)


def make_signed_action_token(token_estado: str, accion: str) -> str:
    payload = f"{token_estado}|{accion}"
    firma = hmac.new(
        TOKEN_SECRET.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    return f"{payload}|{firma}"


def verify_signed_action_token(signed_token: str) -> tuple[bool, Optional[str], Optional[str]]:
    try:
        token_estado, accion, firma = signed_token.split("|", 2)
    except ValueError:
        return False, None, None

    payload = f"{token_estado}|{accion}"
    firma_esperada = hmac.new(
        TOKEN_SECRET.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(firma, firma_esperada):
        return False, None, None

    if accion not in ("en_proceso", "atendido", "cerrado", "anulado"):
        return False, None, None

    return True, token_estado, accion


def accion_a_estado(accion: str) -> str:
    mapa = {
        "en_proceso": "En proceso",
        "atendido": "Atendido",
        "cerrado": "Cerrado",
        "anulado": "Anulado",
    }
    return mapa.get(accion, "Pendiente")


# =========================================================
# PDF
# =========================================================
def generar_pdf_bytes(reclamo: sqlite3.Row) -> BytesIO:
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    y = height - 18 * mm

    def draw_label_value(label: str, value: str = ""):
        nonlocal y
        c.setFont("Helvetica-Bold", 10)
        c.drawString(15 * mm, y, label)
        c.setFont("Helvetica", 10)
        c.drawString(60 * mm, y, str(value or ""))
        y -= 6.5 * mm

    c.setTitle(f"Libro de Reclamaciones - {reclamo['numero']}")

    c.setFont("Helvetica-Bold", 15)
    c.drawString(15 * mm, y, "LIBRO DE RECLAMACIONES")
    y -= 9 * mm

    c.setFont("Helvetica", 10)
    c.drawString(15 * mm, y, f"RUC: {EMPRESA_RUC}")
    y -= 5.5 * mm
    c.drawString(15 * mm, y, f"Razón Social: {EMPRESA_RAZON_SOCIAL}")
    y -= 5.5 * mm
    c.drawString(15 * mm, y, f"Dirección: {EMPRESA_DIRECCION}")
    y -= 9 * mm

    draw_label_value("Número:", reclamo["numero"])
    draw_label_value("Fecha registro:", reclamo["fecha_registro"])
    draw_label_value("Nombres:", reclamo["nombres"])
    draw_label_value("Apellidos:", reclamo["apellidos"])
    draw_label_value("Tipo documento:", reclamo["tipo_doc"])
    draw_label_value("N° documento:", reclamo["num_doc"])
    draw_label_value("Teléfono:", reclamo["telefono"])
    draw_label_value("Correo seguimiento:", reclamo["email_seguimiento"])
    draw_label_value("Correo cliente:", reclamo["email_cliente"])
    draw_label_value("Dirección consumidor:", reclamo["direccion"])
    draw_label_value("Menor de edad:", "Sí" if int(reclamo["menor_edad"] or 0) else "No")
    draw_label_value("Padre/Madre/Tutor:", reclamo["padre_madre_tutor"])
    draw_label_value("Bien contratado:", reclamo["bien_contratado"])
    draw_label_value("Monto reclamado:", reclamo["monto_reclamado"])
    draw_label_value("Descripción bien:", reclamo["descripcion_bien"])
    draw_label_value("Tipo solicitud:", reclamo["tipo_solicitud"])
    draw_label_value("Estado:", reclamo["estado"])

    c.setFont("Helvetica-Bold", 10)
    c.drawString(15 * mm, y, "Detalle:")
    y -= 5 * mm

    txt = c.beginText(15 * mm, y)
    txt.setFont("Helvetica", 10)
    lineas_detalle = dividir_texto(str(reclamo["detalle"] or ""), 105)
    for linea in lineas_detalle:
        txt.textLine(linea)
    c.drawText(txt)
    y -= max(12 * mm, len(lineas_detalle) * 4.8 * mm)

    c.setFont("Helvetica-Bold", 10)
    c.drawString(15 * mm, y, "Pedido del consumidor:")
    y -= 5 * mm

    txt = c.beginText(15 * mm, y)
    txt.setFont("Helvetica", 10)
    lineas_pedido = dividir_texto(str(reclamo["pedido"] or ""), 105)
    for linea in lineas_pedido:
        txt.textLine(linea)
    c.drawText(txt)
    y -= max(12 * mm, len(lineas_pedido) * 4.8 * mm)

    if reclamo["respuesta"]:
        c.setFont("Helvetica-Bold", 10)
        c.drawString(15 * mm, y, "Respuesta de la empresa:")
        y -= 5 * mm

        txt = c.beginText(15 * mm, y)
        txt.setFont("Helvetica", 10)
        lineas_resp = dividir_texto(str(reclamo["respuesta"] or ""), 105)
        for linea in lineas_resp:
            txt.textLine(linea)
        c.drawText(txt)

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer


# =========================================================
# CORREO
# =========================================================
def enviar_correo(
    destinatario: str,
    asunto: str,
    cuerpo_texto: str,
    pdf_bytes: Optional[bytes] = None,
    pdf_nombre: str = "constancia.pdf"
) -> None:
    if not destinatario:
        return

    if not RESEND_API_KEY:
        raise RuntimeError("Falta configurar RESEND_API_KEY")

    payload = {
        "from": FROM_EMAIL,
        "to": [destinatario],
        "subject": asunto,
        "text": cuerpo_texto,
    }

    if pdf_bytes:
        import base64
        payload["attachments"] = [
            {
                "filename": pdf_nombre,
                "content": base64.b64encode(pdf_bytes).decode("utf-8"),
            }
        ]

    response = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )

    if response.status_code >= 400:
        raise RuntimeError(f"Error Resend: {response.status_code} - {response.text}")

def enviar_correos_reclamo(reclamo: sqlite3.Row) -> tuple[bool, str]:
    try:
        pdf_buffer = generar_pdf_bytes(reclamo)
        pdf_data = pdf_buffer.getvalue()
        pdf_nombre = f"{reclamo['numero']}.pdf"

        asunto = f"Libro de Reclamaciones - {reclamo['numero']}"

        cuerpo_empresa = f"""
Se registró un nuevo reclamo en el Libro de Reclamaciones.

Número: {reclamo['numero']}
Fecha: {reclamo['fecha_registro']}
Cliente: {reclamo['nombres']} {reclamo['apellidos']}
Documento: {reclamo['tipo_doc']} {reclamo['num_doc']}
Teléfono: {reclamo['telefono']}
Correo seguimiento: {reclamo['email_seguimiento']}
Correo cliente (contacto): {reclamo['email_cliente']}
Tipo: {reclamo['tipo_solicitud']}
Bien contratado: {reclamo['bien_contratado']}
Monto reclamado: {reclamo['monto_reclamado']}
Estado: {reclamo['estado']}

Detalle:
{reclamo['detalle']}

Pedido:
{reclamo['pedido']}

Cambiar estado desde enlaces seguros:

En proceso:
{request.url_root.rstrip('/')}{url_for('cambiar_estado', signed_token=make_signed_action_token(reclamo['token_estado'], 'en_proceso'))}

Atendido:
{request.url_root.rstrip('/')}{url_for('cambiar_estado', signed_token=make_signed_action_token(reclamo['token_estado'], 'atendido'))}

Cerrado:
{request.url_root.rstrip('/')}{url_for('cambiar_estado', signed_token=make_signed_action_token(reclamo['token_estado'], 'cerrado'))}

Anulado:
{request.url_root.rstrip('/')}{url_for('cambiar_estado', signed_token=make_signed_action_token(reclamo['token_estado'], 'anulado'))}
        """.strip()

        enviar_correo(
            destinatario=EMPRESA_EMAIL,
            asunto=asunto,
            cuerpo_texto=cuerpo_empresa,
            pdf_bytes=pdf_data,
            pdf_nombre=pdf_nombre
        )

        return True, "Correos enviados correctamente."
    except Exception as e:
        return False, f"No se pudo enviar el correo: {e}"


# =========================================================
# RUTAS
# =========================================================
@app.route("/", methods=["GET", "POST"])
def libro_reclamaciones():
    if request.method == "POST":
        data = {
            "nombres": request.form.get("nombres", "").strip(),
            "apellidos": request.form.get("apellidos", "").strip(),
            "tipo_doc": request.form.get("tipo_doc", "").strip(),
            "num_doc": request.form.get("num_doc", "").strip(),
            "telefono": request.form.get("telefono", "").strip(),
            "email_seguimiento": "reclamosbodegaoscar@gmail.com",
            "email_cliente": request.form.get("email_cliente", "").strip(),
            "direccion": request.form.get("direccion", "").strip(),
            "menor_edad": 1 if request.form.get("menor_edad") == "on" else 0,
            "padre_madre_tutor": request.form.get("padre_madre_tutor", "").strip(),
            "bien_contratado": request.form.get("bien_contratado", "").strip(),
            "monto_reclamado": request.form.get("monto_reclamado", "").strip(),
            "descripcion_bien": request.form.get("descripcion_bien", "").strip(),
            "tipo_solicitud": request.form.get("tipo_solicitud", "").strip(),
            "detalle": request.form.get("detalle", "").strip(),
            "pedido": request.form.get("pedido", "").strip(),
            "acepta_notificacion_email": 1 if request.form.get("acepta_notificacion_email") == "on" else 0,
        }
        if not data["acepta_notificacion_email"]:
            data["email_cliente"] = ""
            
        obligatorios = [
            "nombres", "apellidos", "tipo_doc", "num_doc",
            "telefono", "direccion",
            "bien_contratado", "tipo_solicitud", "detalle", "pedido"
        ]
        faltantes = [campo for campo in obligatorios if not data[campo]]
        if faltantes:
            flash("Completa todos los campos obligatorios.", "error")
            return render_template("formulario.html", form_data=data, empresa_email=EMPRESA_EMAIL)

        monto = None
        if data["monto_reclamado"]:
            try:
                monto = float(data["monto_reclamado"])
            except ValueError:
                flash("El monto reclamado debe ser numérico.", "error")
                return render_template("formulario.html", form_data=data, empresa_email=EMPRESA_EMAIL)

        conn = get_conn()
        numero = generar_numero(conn)
        fecha_registro = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        token_estado = generar_token_estado()

        conn.execute("""
            INSERT INTO reclamos (
                numero, fecha_registro,
                nombres, apellidos, tipo_doc, num_doc, telefono, email_seguimiento, email_cliente, direccion,
                menor_edad, padre_madre_tutor,
                bien_contratado, monto_reclamado, descripcion_bien,
                tipo_solicitud, detalle, pedido,
                acepta_notificacion_email, estado, token_estado
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            numero, fecha_registro,
            data["nombres"], data["apellidos"], data["tipo_doc"], data["num_doc"],
            data["telefono"], data["email_seguimiento"], data["email_cliente"], data["direccion"],
            data["menor_edad"], data["padre_madre_tutor"],
            data["bien_contratado"], monto, data["descripcion_bien"],
            data["tipo_solicitud"], data["detalle"], data["pedido"],
            data["acepta_notificacion_email"], "Pendiente", token_estado
        ))
        conn.commit()

        reclamo = conn.execute(
            "SELECT * FROM reclamos WHERE numero=?",
            (numero,)
        ).fetchone()
        conn.close()

        ok_correo, msg_correo = enviar_correos_reclamo(reclamo)

        return render_template(
            "exito.html",
            reclamo=reclamo,
            ok_correo=ok_correo,
            msg_correo=msg_correo
        )

    return render_template("formulario.html", form_data={}, empresa_email=EMPRESA_EMAIL)


@app.route("/pdf/<numero>")
def descargar_pdf(numero: str):
    conn = get_conn()
    reclamo = conn.execute(
        "SELECT * FROM reclamos WHERE numero=?",
        (numero,)
    ).fetchone()
    conn.close()

    if not reclamo:
        return "Reclamo no encontrado.", 404

    pdf_buffer = generar_pdf_bytes(reclamo)
    return send_file(
        pdf_buffer,
        as_attachment=True,
        download_name=f"{numero}.pdf",
        mimetype="application/pdf"
    )


@app.route("/estado/<signed_token>")
def cambiar_estado(signed_token: str):
    ok, token_estado, accion = verify_signed_action_token(signed_token)
    if not ok or not token_estado or not accion:
        return "Token inválido.", 403

    nuevo_estado = accion_a_estado(accion)

    conn = get_conn()
    reclamo = conn.execute(
        "SELECT * FROM reclamos WHERE token_estado=?",
        (token_estado,)
    ).fetchone()

    if not reclamo:
        conn.close()
        return "Reclamo no encontrado.", 404

    conn.execute(
        "UPDATE reclamos SET estado=? WHERE token_estado=?",
        (nuevo_estado, token_estado)
    )
    conn.commit()

    reclamo_actualizado = conn.execute(
        "SELECT * FROM reclamos WHERE token_estado=?",
        (token_estado,)
    ).fetchone()
    conn.close()

    return render_template("detalle.html", reclamo=reclamo_actualizado, mensaje=f"Estado actualizado a: {nuevo_estado}")


@app.route("/ver-reclamos")
def ver_reclamos():
    token = request.args.get("token", "").strip()
    if token != PUBLIC_LIST_TOKEN:
        return "Acceso denegado.", 403

    conn = get_conn()
    reclamos = conn.execute(
        "SELECT * FROM reclamos ORDER BY id DESC"
    ).fetchall()
    conn.close()

    return render_template("lista.html", reclamos=reclamos)


@app.route("/reclamo/<numero>")
def ver_detalle(numero: str):
    token = request.args.get("token", "").strip()
    if token != PUBLIC_LIST_TOKEN:
        return "Acceso denegado.", 403

    conn = get_conn()
    reclamo = conn.execute(
        "SELECT * FROM reclamos WHERE numero=?",
        (numero,)
    ).fetchone()
    conn.close()

    if not reclamo:
        return "Reclamo no encontrado.", 404

    return render_template("detalle.html", reclamo=reclamo, mensaje=None)


@app.route("/responder/<numero>", methods=["POST"])
def responder_reclamo(numero: str):
    token = request.args.get("token", "").strip()
    if token != PUBLIC_LIST_TOKEN:
        return "Acceso denegado.", 403

    respuesta = request.form.get("respuesta", "").strip()
    if not respuesta:
        return "La respuesta no puede estar vacía.", 400

    conn = get_conn()
    reclamo = conn.execute(
        "SELECT * FROM reclamos WHERE numero=?",
        (numero,)
    ).fetchone()

    if not reclamo:
        conn.close()
        return "Reclamo no encontrado.", 404

    fecha_respuesta = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn.execute("""
        UPDATE reclamos
        SET respuesta=?, fecha_respuesta=?, estado='Atendido'
        WHERE numero=?
    """, (respuesta, fecha_respuesta, numero))
    conn.commit()

    reclamo_actualizado = conn.execute(
        "SELECT * FROM reclamos WHERE numero=?",
        (numero,)
    ).fetchone()
    conn.close()
    return render_template(
    "detalle.html",
    reclamo=reclamo_actualizado,
    mensaje="Respuesta guardada correctamente."
)
# =========================================================
# MAIN
# =========================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
