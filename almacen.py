"""Almacenamiento del Asistente HYS: usuarios, códigos de invitación, estado de cada persona y opiniones.

Usa SQLite (viene con Python, no hay que instalar nada). El archivo de la base tiene que estar en un volumen
persistente (en Railway: Volume montado en /data); si no, se borra con cada despliegue.
"""
import os
import time
import sqlite3
import secrets
import logging
from contextlib import contextmanager

log = logging.getLogger("almacen")

_RUTA = None
ALFABETO = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sin 0/O ni 1/I para evitar confusiones al copiar el código

ESQUEMA = """
CREATE TABLE IF NOT EXISTS usuarios (
    id INTEGER PRIMARY KEY,
    nombre TEXT,
    alta INTEGER,
    activo INTEGER NOT NULL DEFAULT 1,
    via TEXT
);
CREATE TABLE IF NOT EXISTS codigos (
    codigo TEXT PRIMARY KEY,
    creador INTEGER,
    creado INTEGER,
    vence INTEGER,
    usos_max INTEGER NOT NULL DEFAULT 1,
    usos INTEGER NOT NULL DEFAULT 0,
    activo INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS estado (
    id INTEGER PRIMARY KEY,
    modo TEXT,
    tema INTEGER
);
CREATE TABLE IF NOT EXISTS respuestas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid INTEGER,
    ts INTEGER,
    modo TEXT,
    tema INTEGER,
    pregunta TEXT,
    respuesta TEXT,
    voto INTEGER,
    motivo INTEGER
);
CREATE INDEX IF NOT EXISTS ix_respuestas_ts ON respuestas (ts);
CREATE TABLE IF NOT EXISTS ciclos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid INTEGER,
    inicio INTEGER
);
CREATE INDEX IF NOT EXISTS ix_ciclos_uid ON ciclos (uid, inicio);
CREATE TABLE IF NOT EXISTS kpi_dia (
    uid INTEGER,
    ciclo INTEGER,
    dia INTEGER,
    kpi INTEGER,
    cant INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (uid, ciclo, dia, kpi)
);
CREATE TABLE IF NOT EXISTS turno_ajustes (
    uid INTEGER PRIMARY KEY,
    hora INTEGER NOT NULL DEFAULT 19,
    silenciado INTEGER NOT NULL DEFAULT 0,
    aviso INTEGER
);
CREATE TABLE IF NOT EXISTS intentos (
    id INTEGER PRIMARY KEY,
    nombre TEXT,
    primero INTEGER,
    ultimo INTEGER,
    veces INTEGER NOT NULL DEFAULT 1
);
"""


@contextmanager
def _con():
    c = sqlite3.connect(_RUTA, timeout=10)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def iniciar(ruta):
    global _RUTA
    carpeta = os.path.dirname(os.path.abspath(ruta))
    os.makedirs(carpeta, exist_ok=True)
    _RUTA = ruta
    with _con() as c:
        c.executescript(ESQUEMA)
    log.info("Base de datos lista en %s", ruta)


def ruta():
    return _RUTA


# ---------------------------------------------------------------- usuarios

def usuario_activo(uid):
    with _con() as c:
        f = c.execute("SELECT activo FROM usuarios WHERE id = ?", (uid,)).fetchone()
    return bool(f and f["activo"])


def alta_usuario(uid, nombre="", via="manual"):
    with _con() as c:
        c.execute(
            "INSERT INTO usuarios (id, nombre, alta, activo, via) VALUES (?, ?, ?, 1, ?) "
            "ON CONFLICT(id) DO UPDATE SET activo = 1, nombre = CASE WHEN excluded.nombre != '' "
            "THEN excluded.nombre ELSE usuarios.nombre END",
            (uid, nombre or "", int(time.time()), via),
        )


def baja_usuario(uid):
    with _con() as c:
        n = c.execute("UPDATE usuarios SET activo = 0 WHERE id = ?", (uid,)).rowcount
    return n > 0


def listar_usuarios():
    with _con() as c:
        return [dict(f) for f in c.execute("SELECT * FROM usuarios ORDER BY alta")]


# ---------------------------------------------------------------- intentos de personas sin acceso

def registrar_intento(uid, nombre=""):
    """Anota que alguien sin acceso escribió al bot. Devuelve (es_la_primera_vez, avisos_en_la_última_hora)."""
    ahora = int(time.time())
    with _con() as c:
        f = c.execute("SELECT id FROM intentos WHERE id = ?", (uid,)).fetchone()
        if f:
            c.execute("UPDATE intentos SET ultimo = ?, veces = veces + 1 WHERE id = ?", (ahora, uid))
            return False, 0
        c.execute("INSERT INTO intentos (id, nombre, primero, ultimo, veces) VALUES (?, ?, ?, ?, 1)",
                  (uid, nombre or "", ahora, ahora))
        n = c.execute("SELECT COUNT(*) n FROM intentos WHERE primero >= ?", (ahora - 3600,)).fetchone()["n"]
        return True, n


def ultimos_intentos(n=10):
    with _con() as c:
        return [dict(f) for f in c.execute("SELECT * FROM intentos ORDER BY ultimo DESC LIMIT ?", (n,))]


# ---------------------------------------------------------------- códigos de invitación

def crear_codigo(creador, usos_max=1, dias=7):
    ahora = int(time.time())
    codigo = "".join(secrets.choice(ALFABETO) for _ in range(8))
    with _con() as c:
        c.execute(
            "INSERT INTO codigos (codigo, creador, creado, vence, usos_max, usos, activo) VALUES (?, ?, ?, ?, ?, 0, 1)",
            (codigo, creador, ahora, ahora + int(dias) * 86400, int(usos_max)),
        )
    return codigo


def canjear_codigo(codigo, uid, nombre=""):
    """Devuelve (ok, motivo, creador). motivo: ok | no_existe | vencido | agotado."""
    codigo = (codigo or "").strip().upper()
    ahora = int(time.time())
    with _con() as c:
        f = c.execute("SELECT * FROM codigos WHERE codigo = ? AND activo = 1", (codigo,)).fetchone()
        if not f:
            return False, "no_existe", None
        if f["vence"] and f["vence"] < ahora:
            return False, "vencido", None
        if f["usos"] >= f["usos_max"]:
            return False, "agotado", None
        n = c.execute(
            "UPDATE codigos SET usos = usos + 1 WHERE codigo = ? AND usos < usos_max", (codigo,)
        ).rowcount
        if n == 0:
            return False, "agotado", None
        c.execute(
            "INSERT INTO usuarios (id, nombre, alta, activo, via) VALUES (?, ?, ?, 1, ?) "
            "ON CONFLICT(id) DO UPDATE SET activo = 1",
            (uid, nombre or "", ahora, "código " + codigo),
        )
        return True, "ok", f["creador"]


# ---------------------------------------------------------------- estado de cada persona

def estado_get(uid):
    with _con() as c:
        f = c.execute("SELECT modo, tema FROM estado WHERE id = ?", (uid,)).fetchone()
    return (f["modo"], f["tema"]) if f else (None, None)


def estado_set(uid, modo, tema):
    with _con() as c:
        c.execute(
            "INSERT INTO estado (id, modo, tema) VALUES (?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET modo = excluded.modo, tema = excluded.tema",
            (uid, modo, tema),
        )


# ---------------------------------------------------------------- respuestas y opiniones

def registrar_respuesta(uid, modo, tema, pregunta, respuesta, guardar_textos=False):
    with _con() as c:
        cur = c.execute(
            "INSERT INTO respuestas (uid, ts, modo, tema, pregunta, respuesta) VALUES (?, ?, ?, ?, ?, ?)",
            (
                uid, int(time.time()), modo, tema,
                (pregunta or "")[:2000] if guardar_textos else None,
                (respuesta or "")[:8000] if guardar_textos else None,
            ),
        )
        return cur.lastrowid


def votar(rid, uid, voto):
    """voto: 1 (me sirvió) o -1 (no me sirvió). Solo vota quien recibió la respuesta."""
    with _con() as c:
        n = c.execute(
            "UPDATE respuestas SET voto = ? WHERE id = ? AND uid = ?", (int(voto), int(rid), uid)
        ).rowcount
    return n > 0


def motivo(rid, uid, codigo):
    with _con() as c:
        n = c.execute(
            "UPDATE respuestas SET motivo = ? WHERE id = ? AND uid = ? AND voto = -1", (int(codigo), int(rid), uid)
        ).rowcount
    return n > 0


def estadisticas(dias=7):
    desde = int(time.time()) - dias * 86400
    with _con() as c:
        q = lambda sql, *a: c.execute(sql, a).fetchall()
        usuarios = q("SELECT COUNT(*) n FROM usuarios WHERE activo = 1")[0]["n"]
        total = q("SELECT COUNT(*) n FROM respuestas")[0]["n"]
        recientes = q("SELECT COUNT(*) n FROM respuestas WHERE ts >= ?", desde)[0]["n"]
        activos = q("SELECT COUNT(DISTINCT uid) n FROM respuestas WHERE ts >= ?", desde)[0]["n"]
        por_modo = {f["modo"]: f["n"] for f in q(
            "SELECT modo, COUNT(*) n FROM respuestas WHERE ts >= ? GROUP BY modo ORDER BY n DESC", desde)}
        por_tema = {f["tema"]: f["n"] for f in q(
            "SELECT tema, COUNT(*) n FROM respuestas WHERE ts >= ? AND tema IS NOT NULL GROUP BY tema ORDER BY n DESC",
            desde)}
        pos = q("SELECT COUNT(*) n FROM respuestas WHERE ts >= ? AND voto = 1", desde)[0]["n"]
        neg = q("SELECT COUNT(*) n FROM respuestas WHERE ts >= ? AND voto = -1", desde)[0]["n"]
        motivos = {f["motivo"]: f["n"] for f in q(
            "SELECT motivo, COUNT(*) n FROM respuestas WHERE ts >= ? AND voto = -1 AND motivo IS NOT NULL "
            "GROUP BY motivo ORDER BY n DESC", desde)}
    return {"usuarios": usuarios, "total": total, "recientes": recientes, "activos": activos,
            "por_modo": por_modo, "por_tema": por_tema, "positivos": pos, "negativos": neg, "motivos": motivos}


def ultimos_negativos(n=10):
    with _con() as c:
        return [dict(f) for f in c.execute(
            "SELECT * FROM respuestas WHERE voto = -1 ORDER BY ts DESC LIMIT ?", (n,))]


def consultas_hoy(uid):
    """Cantidad de respuestas que recibió esta persona hoy (el día cambia a las 00:00 de Argentina, UTC-3)."""
    ahora = int(time.time())
    inicio = ((ahora - 10800) // 86400) * 86400 + 10800
    with _con() as c:
        return c.execute("SELECT COUNT(*) n FROM respuestas WHERE uid = ? AND ts >= ?", (uid, inicio)).fetchone()["n"]


# ---------------------------------------------------------------- Mi turno (ciclos y KPI)

def hoy_ar():
    """Día de hoy como entero (días desde 1970, hora de Argentina, UTC-3)."""
    return int((time.time() - 10800) // 86400)


def hora_ar():
    return int(((time.time() - 10800) % 86400) // 3600)


def ciclo_iniciar(uid, inicio):
    with _con() as c:
        return c.execute("INSERT INTO ciclos (uid, inicio) VALUES (?, ?)", (uid, int(inicio))).lastrowid


def ciclo_ultimo(uid):
    """(id, inicio) del ciclo más reciente, o None."""
    with _con() as c:
        f = c.execute("SELECT id, inicio FROM ciclos WHERE uid = ? ORDER BY inicio DESC, id DESC LIMIT 1", (uid,)).fetchone()
    return (f["id"], f["inicio"]) if f else None


def ciclos_de(uid):
    """[(id, inicio)] del más viejo al más nuevo."""
    with _con() as c:
        return [(f["id"], f["inicio"]) for f in c.execute(
            "SELECT id, inicio FROM ciclos WHERE uid = ? ORDER BY inicio, id", (uid,))]


def kpi_cambiar(uid, ciclo, dia, kpi, delta, tope=10):
    """Suma o resta en un KPI de un día (entre 0 y el tope). Devuelve la cantidad que quedó."""
    with _con() as c:
        c.execute(
            "INSERT INTO kpi_dia (uid, ciclo, dia, kpi, cant) VALUES (?, ?, ?, ?, MAX(0, MIN(?, ?))) "
            "ON CONFLICT(uid, ciclo, dia, kpi) DO UPDATE SET cant = MAX(0, MIN(?, cant + ?))",
            (uid, ciclo, int(dia), int(kpi), tope, delta, tope, delta))
        return c.execute("SELECT cant FROM kpi_dia WHERE uid = ? AND ciclo = ? AND dia = ? AND kpi = ?",
                         (uid, ciclo, int(dia), int(kpi))).fetchone()["cant"]


def kpi_del_dia(uid, ciclo, dia):
    with _con() as c:
        return {f["kpi"]: f["cant"] for f in c.execute(
            "SELECT kpi, cant FROM kpi_dia WHERE uid = ? AND ciclo = ? AND dia = ?", (uid, ciclo, int(dia)))}


def kpi_totales(uid, ciclo):
    with _con() as c:
        return {f["kpi"]: f["t"] for f in c.execute(
            "SELECT kpi, SUM(cant) t FROM kpi_dia WHERE uid = ? AND ciclo = ? GROUP BY kpi", (uid, ciclo))}


def dias_con_carga(uid, ciclo):
    with _con() as c:
        return {f["dia"] for f in c.execute(
            "SELECT dia FROM kpi_dia WHERE uid = ? AND ciclo = ? GROUP BY dia HAVING SUM(cant) > 0", (uid, ciclo))}


def ajustes_get(uid):
    """(hora del recordatorio, silenciado). Por defecto 19:00 y activado."""
    with _con() as c:
        f = c.execute("SELECT hora, silenciado FROM turno_ajustes WHERE uid = ?", (uid,)).fetchone()
    return (f["hora"], bool(f["silenciado"])) if f else (19, False)


def ajustes_set(uid, hora=None, silenciado=None):
    h, s = ajustes_get(uid)
    h = h if hora is None else int(hora)
    s = s if silenciado is None else bool(silenciado)
    with _con() as c:
        c.execute(
            "INSERT INTO turno_ajustes (uid, hora, silenciado) VALUES (?, ?, ?) "
            "ON CONFLICT(uid) DO UPDATE SET hora = excluded.hora, silenciado = excluded.silenciado",
            (uid, h, int(s)))


def avisos_pendientes(hoy, hora):
    """[(uid, ciclo, inicio)] de quienes tienen recordatorio a esta hora, no lo silenciaron y no lo recibieron hoy.
    Mira solo el ciclo más reciente de cada persona."""
    with _con() as c:
        filas = c.execute(
            "SELECT c.uid uid, c.id ciclo, c.inicio inicio FROM ciclos c "
            "LEFT JOIN turno_ajustes a ON a.uid = c.uid "
            "WHERE c.inicio = (SELECT MAX(inicio) FROM ciclos WHERE uid = c.uid) "
            "AND COALESCE(a.silenciado, 0) = 0 AND COALESCE(a.hora, 19) = ? "
            "AND (a.aviso IS NULL OR a.aviso != ?)", (int(hora), int(hoy))).fetchall()
    return [(f["uid"], f["ciclo"], f["inicio"]) for f in filas]


def aviso_marcar(uid, hoy):
    with _con() as c:
        c.execute(
            "INSERT INTO turno_ajustes (uid, aviso) VALUES (?, ?) "
            "ON CONFLICT(uid) DO UPDATE SET aviso = excluded.aviso", (uid, int(hoy)))


def borrar_turno(uid):
    with _con() as c:
        c.execute("DELETE FROM kpi_dia WHERE uid = ?", (uid,))
        c.execute("DELETE FROM ciclos WHERE uid = ?", (uid,))
        c.execute("DELETE FROM turno_ajustes WHERE uid = ?", (uid,))
