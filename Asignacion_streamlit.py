"""Consulta de asignación de empresas — Ergonomía (Maestro de Adherentes).

Login compartido (usuario/contraseña en st.secrets) y luego cada profesional
elige su nombre para ver las empresas / centros de trabajo que tiene asignados.
El maestro se lee desde el parquet que sube procesar_basesdedatos.py a Drive.
"""
import hmac
import io
import re
import unicodedata
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="Asignación Ergonomía", page_icon="📋", layout="wide")

MAESTRO_URL = st.secrets.get("MAESTRO_URL", None) or (
    "https://drive.usercontent.google.com/download"
    "?id=1Qgo-AcKDGm7_p3qxxQ5NqsR9sTULufxb&export=download&confirm=t"
)
# Respaldo solo para uso local si Drive no responde.
MAESTRO_LOCAL = Path(r"C:\EspecialidadesTecnicas\02_PlanAnual_SUSESO\2026\InsumosSeguimiento\maestro_adherentes.parquet")

COL_RM, COL_REG = "Ergónomo RM", "Ergonomo Región"
ROLES = {
    "Ergónomo que atiende el CT (Región)": [COL_REG],
    "Ergónomo RM (responsable de la empresa)": [COL_RM],
    "Ambos": [COL_REG, COL_RM],
}
COLS_EMPRESA = ["Rut Empresa", "Razón Social", "Nombre Fantasia", "Holding", "Criticidad",
                "Tipo Adherente", "Estado contrato", "Fec ini contrato", "N Trab Empr",
                "Grupo Act. Económica"]
COLS_FECHA = ["Fec ini contrato", "Fc Ter Contrato", "Fec ini suc", "Fec Ter Suc", "Ult Mes Cotizado"]


# ---------------------------------------------------------------- utilidades
def _sin_tildes(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def _clave(nombre: str) -> str:
    return re.sub(r"\s+", " ", _sin_tildes(nombre).casefold()).strip()


def _desdoblar(nombre: str) -> str:
    """'Rolando Pérez Rolando Pérez' -> 'Rolando Pérez' (dato sucio del maestro)."""
    p = nombre.split()
    h = len(p) // 2
    return " ".join(p[:h]) if len(p) % 2 == 0 and p[:h] == p[h:] else nombre


def _nombres(valor) -> list[str]:
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return []
    partes = []
    for p in str(valor).split("/"):
        p = _desdoblar(re.sub(r"\s+", " ", p).strip())
        if p:
            partes.append(p)
    return partes


def _norm_rut(s: str) -> str:
    return re.sub(r"[^0-9kK]", "", str(s)).lower().lstrip("0")


def _fmt_fechas(df: pd.DataFrame) -> pd.DataFrame:
    """Fechas siempre dd-mm-aaaa para el usuario."""
    df = df.copy()
    for c in COLS_FECHA:
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce").dt.strftime("%d-%m-%Y").fillna("")
    return df


def _excel(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as w:
        df.to_excel(w, index=False, sheet_name="Asignación")
    return buf.getvalue()


# ---------------------------------------------------------------- login
def _login() -> bool:
    if st.session_state.get("auth"):
        return True
    try:
        user_ok, pass_ok = st.secrets["AUTH_USER"], st.secrets["AUTH_PASSWORD"]
    except Exception:
        st.error("Faltan AUTH_USER / AUTH_PASSWORD en los secrets de la aplicación.")
        return False

    def igual(a: str, b: str) -> bool:
        a, b = unicodedata.normalize("NFC", a.strip()), unicodedata.normalize("NFC", b.strip())
        return hmac.compare_digest(a.encode(), b.encode())

    _, centro, _ = st.columns([1, 1.2, 1])
    with centro:
        st.title("📋 Asignación Ergonomía")
        st.caption("Consulta tu asignación de empresas en el Maestro de Adherentes.")
        with st.form("login"):
            u = st.text_input("Usuario")
            p = st.text_input("Contraseña", type="password")
            if st.form_submit_button("Ingresar", use_container_width=True):
                if igual(u, str(user_ok)) and igual(p, str(pass_ok)):
                    st.session_state["auth"] = True
                    st.rerun()
                st.error("Usuario o contraseña incorrectos.")
    return False


# ---------------------------------------------------------------- datos
@st.cache_data(ttl=3600, show_spinner="Cargando Maestro de Adherentes…")
def cargar_maestro() -> tuple[pd.DataFrame, str]:
    origen = "Drive"
    try:
        r = requests.get(MAESTRO_URL, timeout=90, allow_redirects=True)
        r.raise_for_status()
        if "text/html" in r.headers.get("Content-Type", ""):
            raise ValueError("Drive devolvió HTML; revisa que el archivo sea público.")
        df = pd.read_parquet(io.BytesIO(r.content))
    except Exception as e:
        if not MAESTRO_LOCAL.exists():
            raise RuntimeError(f"No se pudo descargar el maestro: {e}") from e
        df, origen = pd.read_parquet(MAESTRO_LOCAL), f"archivo local (Drive falló: {e})"

    df.columns = df.columns.str.strip()
    for c in (COL_RM, COL_REG):
        df[c] = df[c].fillna("").astype(str).str.strip()
    # Claves "|a|b|" para filtrar por profesional sin recorrer filas en Python.
    for c in (COL_RM, COL_REG):
        df[f"_k_{c}"] = df[c].map(lambda v: "|" + "|".join(_clave(n) for n in _nombres(v)) + "|")
    df["Región"] = pd.to_numeric(df["Region Sucursal"], errors="coerce").astype("Int64")
    return df, origen


def lista_profesionales(df: pd.DataFrame) -> dict[str, str]:
    """clave -> nombre para mostrar (la grafía más frecuente)."""
    cuenta: dict[str, dict[str, int]] = {}
    for c in (COL_RM, COL_REG):
        for v, n in df[c].value_counts().items():
            for nom in _nombres(v):
                d = cuenta.setdefault(_clave(nom), {})
                d[nom] = d.get(nom, 0) + n
    bonito = {k: max(v, key=lambda x: (x != x.upper(), v[x])) for k, v in cuenta.items()}
    return dict(sorted(bonito.items(), key=lambda kv: _sin_tildes(kv[1]).casefold()))


# ---------------------------------------------------------------- app
def main():
    if not _login():
        return
    try:
        df, origen = cargar_maestro()
    except Exception as e:
        st.error(str(e))
        return

    profs = lista_profesionales(df)
    with st.sidebar:
        st.header("Mi asignación")
        clave = st.selectbox("Profesional", list(profs), format_func=profs.get, index=None,
                             placeholder="Elige tu nombre")
        rol = st.radio("Mostrar donde figuro como", list(ROLES))
        solo_vig = st.checkbox("Solo contrato y sucursal vigentes", value=True)
        st.divider()
        if st.button("🔄 Recargar maestro"):
            cargar_maestro.clear()
            st.rerun()
        st.caption(f"Fuente: {origen} · {len(df):,} filas".replace(",", "."))
        if st.button("Cerrar sesión"):
            st.session_state.clear()
            st.rerun()

    st.title("📋 Asignación Ergonomía")
    tab_mi, tab_bus = st.tabs(["Mi asignación", "Buscar en el maestro"])

    base = df
    if solo_vig:
        base = base[(base["Estado contrato"] == "Si") & (base["Est Sucursal"] == "Si")]

    with tab_mi:
        if not clave:
            st.info("Elige tu nombre en el panel lateral.")
        else:
            mask = pd.Series(False, index=base.index)
            for c in ROLES[rol]:
                mask |= base[f"_k_{c}"].str.contains(f"|{clave}|", regex=False)
            mio = base[mask]
            _mostrar(mio, f"{profs[clave]}", key="mi")

    with tab_bus:
        q = st.text_input("RUT, razón social, nombre de fantasía o ID-CT")
        if q.strip():
            qn, qr = _clave(q), _norm_rut(q)
            campos = base[["Razón Social", "Nombre Fantasia", "ID-CT", "C.GLS_NOM_SUC"]].fillna("").astype(str)
            txt = campos.apply(lambda s: s.map(_clave)).agg(" ".join, axis=1)
            m = txt.str.contains(re.escape(qn), regex=True)
            if qr and len(qr) >= 4:
                m |= base["Rut Empresa"].map(_norm_rut).str.contains(qr, regex=False)
            _mostrar(base[m], "Resultado de búsqueda", key="bus")
        else:
            st.caption("Busca en todo el maestro (respeta el filtro de vigencia del panel lateral).")


def _mostrar(d: pd.DataFrame, titulo: str, key: str):
    if d.empty:
        st.warning("Sin resultados.")
        return
    c1, c2, c3 = st.columns(3)
    c1.metric("Empresas", f"{d['Rut Empresa'].nunique():,}".replace(",", "."))
    c2.metric("Centros de trabajo", f"{d['ID-CT'].nunique():,}".replace(",", "."))
    c3.metric("Regiones", d["Región"].nunique())

    regiones = sorted(d["Región"].dropna().unique())
    sel = st.multiselect("Filtrar por región", regiones, key=f"reg_{key}")
    if sel:
        d = d[d["Región"].isin(sel)]

    vista = st.radio("Vista", ["Por empresa", "Por centro de trabajo"], horizontal=True, key=f"v_{key}")
    ocultas = [c for c in d.columns if c.startswith("_k_")] + ["Región"]
    if vista == "Por empresa":
        emp = (d.groupby("Rut Empresa", sort=False)
                .agg(**{"Razón Social": ("Razón Social", "first"),
                        "Holding": ("Holding", "first"),
                        "Criticidad": ("Criticidad", "first"),
                        "Centros de trabajo": ("ID-CT", "nunique"),
                        "Regiones": ("Región", lambda s: ", ".join(map(str, sorted(s.dropna().unique())))),
                        COL_RM: (COL_RM, lambda s: " · ".join(sorted({x for x in s if x}))),
                        COL_REG: (COL_REG, lambda s: " · ".join(sorted({x for x in s if x})))})
                .reset_index()
                .sort_values("Razón Social"))
        out = emp
    else:
        out = _fmt_fechas(d.drop(columns=ocultas)).sort_values(["Razón Social", "NUM SUC"])
    st.dataframe(out, use_container_width=True, hide_index=True, height=520)
    st.download_button("⬇ Descargar Excel", _excel(out), file_name=f"asignacion_{key}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


main()
