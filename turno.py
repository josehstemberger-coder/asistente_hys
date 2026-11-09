"""Mi turno: lógica del tablero de KPI por ciclo (roster 14 x 14). Sin IA: solo cuentas y textos.

Un ciclo dura 14 días desde que el técnico toca «Empecé mi turno». Cada KPI tiene un mínimo de 5 por ciclo (30 en
total) y se puede hacer más. Los días se cuentan como números enteros (días desde 1970, hora de Argentina).
"""
import datetime

DIAS_TURNO = 14
DIAS_DESCANSO = 14
META = 5  # mínimo por ciclo de cada KPI que tiene meta
SIN_META = {3}  # Stop works: se cuentan los que se hagan (1 o más), sin meta ni ritmo
TOTAL_META = META * (6 - len(SIN_META))  # 25
TOPE_DIARIO = 10  # por KPI y por día: solo evita toques accidentales

# (número, nombre completo, nombre corto para el botón, emoji)
KPIS = [
    (1, "Control de documentación", "Documentación", "📋"),
    (2, "Gestión de riesgos críticos", "Riesgos críticos", "⚠️"),
    (3, "Stop works", "Stop works", "✋"),
    (4, "Abordaje de conductas inseguras", "Conducta insegura", "🔶"),
    (5, "Abordaje de conductas seguras", "Conducta segura", "✅"),
    (6, "Inspecciones", "Inspecciones", "🔍"),
]
NOMBRE = {k: n for k, n, _c, _e in KPIS}
CORTO = {k: c for k, _n, c, _e in KPIS}
EMOJI = {k: e for k, _n, _c, e in KPIS}


def fecha(dia):
    """Día entero -> 'dd/mm'."""
    return (datetime.date(1970, 1, 1) + datetime.timedelta(days=dia)).strftime("%d/%m")


def dia_ciclo(hoy, inicio):
    return hoy - inicio + 1


def activo(hoy, inicio):
    return 0 <= hoy - inicio < DIAS_TURNO


def esperado(dia):
    """Cuánto debería llevar cada KPI al día 'dia' del ciclo para llegar al mínimo (ritmo parejo)."""
    dia = max(1, min(DIAS_TURNO, dia))
    return min(META, -(-META * dia // DIAS_TURNO))


def atrasados(totales, dia):
    esp = esperado(dia)
    return [k for k in NOMBRE if k not in SIN_META and totales.get(k, 0) < esp]


def puntaje(totales):
    """Cumplimiento del ciclo: suma de min(total, 5) por KPI con meta (máximo 25). Lo extra no tapa a otro KPI."""
    return sum(min(totales.get(k, 0), META) for k in NOMBRE if k not in SIN_META)


def extras(totales):
    return sum(max(totales.get(k, 0) - META, 0) for k in NOMBRE if k not in SIN_META)


def racha(dias_con_carga, hoy, inicio):
    """Días seguidos con carga, terminando hoy (o ayer si hoy todavía no cargó). Se perdona un día sin carga."""
    d = hoy if hoy in dias_con_carga else hoy - 1
    gracia, n = 1, 0
    while d >= inicio:
        if d in dias_con_carga:
            n += 1
        elif gracia > 0:
            gracia -= 1
        else:
            break
        d -= 1
    return n


def texto_panel(dia, totales, del_dia, racha_n, etiqueta=""):
    lineas = ["Mi turno · Día %d de %d" % (dia, DIAS_TURNO)]
    if etiqueta:
        lineas.append(etiqueta)
    if racha_n >= 2:
        lineas.append("Racha: %d días seguidos con carga" % racha_n)
    lineas.append("")
    atr = set(atrasados(totales, dia))
    for k, nombre, _c, emoji in KPIS:
        t = totales.get(k, 0)
        if k in SIN_META:
            estado = "%d" % t
        elif t >= META:
            estado = "%d/%d ✔" % (t, META) + (" (+%d)" % (t - META) if t > META else "")
        else:
            estado = "%d/%d" % (t, META) + (" ⚠" if k in atr else "")
        lineas.append("%s %s: %s" % (emoji, nombre, estado))
    lineas.append("")
    lineas.append("Cargado ese día: %d" % sum(del_dia.values()))
    if atr:
        lineas.append("Atrasados según tu ritmo: " + ", ".join(CORTO[k] for k in sorted(atr)) + ".")
    else:
        lineas.append("Vas al día con todos los KPI.")
    return "\n".join(lineas)


def texto_cierre(totales, puntaje_anterior, mejor, n_ciclo):
    p = puntaje(totales)
    lineas = ["Cerraste tu ciclo %d: cumpliste %d de %d." % (n_ciclo, p, TOTAL_META)]
    for k, nombre, _c, emoji in KPIS:
        t = totales.get(k, 0)
        if k in SIN_META:
            lineas.append("%s %s: %d" % (emoji, nombre, t))
        else:
            lineas.append("%s %s: %d/%d" % (emoji, nombre, t, META) + (" ✔" if t >= META else ""))
    if extras(totales):
        lineas.append("Superaste la meta en %d." % extras(totales))
    if puntaje_anterior is not None:
        dif = p - puntaje_anterior
        if dif > 0:
            lineas.append("Mejoraste %d respecto del ciclo anterior (%d)." % (dif, puntaje_anterior))
        elif dif < 0:
            lineas.append("Quedaste %d por debajo del ciclo anterior (%d)." % (-dif, puntaje_anterior))
        else:
            lineas.append("Igual que el ciclo anterior (%d)." % puntaje_anterior)
    if mejor is not None and p >= mejor and n_ciclo > 1:
        lineas.append("Es tu mejor ciclo hasta ahora.")
    elif mejor is not None and n_ciclo > 1:
        lineas.append("Tu mejor ciclo fue de %d." % mejor)
    if p == TOTAL_META:
        lineas.append("Ciclo completo: %d de %d." % (TOTAL_META, TOTAL_META))
    return "\n".join(lineas)
