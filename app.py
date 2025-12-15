import os, time, glob, subprocess, sys, shutil, json, unicodedata
from pathlib import Path
from datetime import datetime

import streamlit as st
from streamlit_autorefresh import st_autorefresh
import pandas as pd
import requests

# ==========================
# CONFIG GENERAL COMÚN
# ==========================

RUNTIME = Path.cwd() / "runtime"
RUNTIME.mkdir(parents=True, exist_ok=True)
os.environ["RUNNER_TEMP"] = str(RUNTIME)

st.set_page_config(page_title="Ambientador IA (Falabella)", layout="wide")
st.title("Herramientas IA Falabella")

base_tmp = Path(os.environ.get("RUNNER_TEMP", ".")) / "imagenes_falabella"
carpeta_ambientada = base_tmp / "ambientada"
carpeta_rebuild = base_tmp / "imagen JPG"
path_metadata_csv = base_tmp / "metadata.csv"

# === Lee la API key desde Secrets (Streamlit Cloud) o env ===
OPENAI_API_KEY = ""
try:
    OPENAI_API_KEY = st.secrets.get("OPENAI_API_KEY", "")
except Exception:
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

if OPENAI_API_KEY:
    os.environ["OPENAI_API_KEY"] = OPENAI_API_KEY  # para que el worker la reciba

# ==========================
# HELPERS GENERALES
# ==========================

def empty_dir(p: Path):
    if p.exists():
        for x in p.iterdir():
            if x.is_file():
                try:
                    x.unlink(missing_ok=True)
                except Exception:
                    pass
            elif x.is_dir():
                shutil.rmtree(x, ignore_errors=True)

def _norm(text):
    if text is None:
        return ""
    s = str(text).strip()
    if not s:
        return ""
    s = "".join(
        ch
        for ch in unicodedata.normalize("NFD", s)
        if unicodedata.category(ch) != "Mn"
    )
    return s.upper()

# ==========================
# IA: EXTRACTOR DE MEDIDAS
# ==========================

OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_MODEL = "gpt-4.1-mini"

def extraer_medidas_con_ia(texto_specs: str):
    if not texto_specs or not texto_specs.strip():
        return None

    if not OPENAI_API_KEY:
        st.warning("No se encontró OPENAI_API_KEY en Secrets/entorno. No se podrán extraer medidas.")
        return None

    prompt = f"""
Eres un asistente experto en analizar especificaciones de productos en español.

Te doy el texto de especificaciones de un producto. Debes extraer, SOLO si es posible:

- "dimensiones": texto general como "60 x 120 x 80 cm"
- "ancho"
- "largo"
- "alto"
- "Diametro"

Reglas:
- No inventes medidas.
- Usa unidades exactamente como aparecen.
- Si no hay NINGUNA medida, responde EXACTAMENTE: NO_HAY_MEDIDAS
- Si encuentras al menos una, responde SOLO un JSON en una línea, por ejemplo:
{{"dimensiones":"60 x 120 x 80 cm","ancho":"60 cm","largo":"120 cm","alto":"80 cm", "Diametro":"12 cm"}}

Texto:
\"\"\"{texto_specs}\"\"\"
"""

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    body = {
        "model": OPENAI_MODEL,
        "temperature": 0,
        "messages": [{"role": "user", "content": prompt}],
    }

    try:
        resp = requests.post(OPENAI_API_URL, headers=headers, json=body, timeout=60)
    except Exception as e:
        st.error(f"Error llamando a OpenAI: {e}")
        return None

    if resp.status_code != 200:
        try:
            err_json = resp.json()
        except Exception:
            err_json = {}
        err_obj = (err_json or {}).get("error") or {}
        st.error(f"Error HTTP OpenAI: {resp.status_code} type={err_obj.get('type') or err_obj.get('code')} msg={err_obj.get('message')}")
        return None

    try:
        content = resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        st.error(f"Respuesta inesperada de OpenAI: {e}")
        return None

    if _norm(content) == "NO_HAY_MEDIDAS":
        return None

    if "{" in content and "}" in content:
        content = content[content.find("{"): content.rfind("}") + 1]

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        st.error(f"No se pudo parsear JSON devuelto por OpenAI. Texto: {content}")
        return None

    def clean(x):
        if x is None:
            return None
        s = str(x).strip()
        return s if s else None

    return {
        "dimensiones": clean(parsed.get("dimensiones")),
        "ancho": clean(parsed.get("ancho")),
        "largo": clean(parsed.get("largo")),
        "alto": clean(parsed.get("alto")),
        "Diametro": clean(parsed.get("Diametro")),
    }

def construir_cadena_medidas(medidas):
    if not medidas:
        return ""
    partes = []
    if medidas.get("dimensiones"):
        partes.append(f"Dimensiones: {medidas['dimensiones']}")
    if medidas.get("ancho"):
        partes.append(f"Ancho: {medidas['ancho']}")
    if medidas.get("largo"):
        partes.append(f"Largo: {medidas['largo']}")
    if medidas.get("alto"):
        partes.append(f"Alto: {medidas['alto']}")
    if medidas.get("Diametro"):
        partes.append(f"Diametro: {medidas['Diametro']}")
    return " | ".join(partes)

def procesar_excel_medidas(input_path: Path) -> Path:
    df = pd.read_excel(input_path)
    cols_norm = {_norm(c): c for c in df.columns}
    if _norm("PRODUCT_ID") not in cols_norm or _norm("SPECIFICATIONS") not in cols_norm:
        raise ValueError(f"No se encontraron columnas PRODUCT_ID y SPECIFICATIONS. Columnas detectadas: {list(df.columns)}")

    col_pid = cols_norm[_norm("PRODUCT_ID")]
    col_specs = cols_norm[_norm("SPECIFICATIONS")]

    out_rows = []
    for _, row in df.iterrows():
        pid_raw = row.get(col_pid, None)
        specs_raw = row.get(col_specs, None)

        if pd.isna(pid_raw) or str(pid_raw).strip() == "":
            continue

        if isinstance(pid_raw, (int, float)) and not isinstance(pid_raw, bool):
            estilo = int(pid_raw) if float(pid_raw).is_integer() else pid_raw
        else:
            s = str(pid_raw).strip()
            estilo = int(s) if s.isdigit() else s

        atributo_web = "" if pd.isna(specs_raw) else str(specs_raw).strip()

        medidas_texto = ""
        if atributo_web:
            try:
                medidas = extraer_medidas_con_ia(atributo_web)
                medidas_texto = construir_cadena_medidas(medidas)
            except Exception as e:
                st.warning(f"Error procesando PRODUCT_ID={estilo}: {e}")
                medidas_texto = ""

        out_rows.append({"Estilo": estilo, "ATRIBUTO_WEB": atributo_web, "Medidas": medidas_texto})

    df_out = pd.DataFrame(out_rows, columns=["Estilo", "ATRIBUTO_WEB", "Medidas"])
    out_path = input_path.with_name(input_path.stem + "_medidas.xlsx")
    df_out.to_excel(out_path, index=False)
    return out_path

# ==========================
# ESTADO DE LA SESIÓN
# ==========================

if "worker_proc" not in st.session_state:
    st.session_state.worker_proc = None
if "uploaded_excel_path" not in st.session_state:
    st.session_state.uploaded_excel_path = None
if "zip_ready_path" not in st.session_state:
    st.session_state.zip_ready_path = None

if "measures_input_path" not in st.session_state:
    st.session_state.measures_input_path = None
if "measures_output_path" not in st.session_state:
    st.session_state.measures_output_path = None

# ==========================
# SIDEBAR (Ambientador)
# ==========================

with st.sidebar:
    st.header("Ambientador de Imágenes - Control")
    st.markdown("### 1) Subir Excel de SKUs")
    excel = st.file_uploader("Archivo .xlsx", type=["xlsx"], key="ambientador_excel")
    if excel:
        tmp_dir = Path("uploaded")
        tmp_dir.mkdir(parents=True, exist_ok=True)
        excel_path = tmp_dir / ("skus_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".xlsx")
        with open(excel_path, "wb") as f:
            f.write(excel.getbuffer())
        st.session_state.uploaded_excel_path = str(excel_path.resolve())
        st.success(f"Excel cargado: {excel_path.name}")

    st.markdown("### 2) Control proceso Ambientador")
    start = st.button(
        "Generar imágenes ambientadas con IA",
        type="primary",
        disabled=(st.session_state.worker_proc is not None) or (st.session_state.uploaded_excel_path is None),
        key="btn_start_ambientador"
    )
    stop = st.button(
        "Detener generación",
        disabled=st.session_state.worker_proc is None,
        key="btn_stop_ambientador"
    )
    reset = st.button("Nueva ejecución", key="btn_reset_ambientador")

    if start:
        base_tmp.mkdir(parents=True, exist_ok=True)
        carpeta_ambientada.mkdir(parents=True, exist_ok=True)
        carpeta_rebuild.mkdir(parents=True, exist_ok=True)
        empty_dir(carpeta_ambientada)
        empty_dir(carpeta_rebuild)
        if path_metadata_csv.exists():
            path_metadata_csv.unlink(missing_ok=True)
        st.session_state.zip_ready_path = None

        env = os.environ.copy()
        env["RUTA_EXCEL_ARCHIVO"] = st.session_state.uploaded_excel_path
        env["RUNNER_TEMP"] = os.environ.get("RUNNER_TEMP", str(RUNTIME))

        if not env.get("OPENAI_API_KEY"):
            st.warning("No se detectó OPENAI_API_KEY en Secrets/entorno. La generación fallará sin la clave.")

        st.session_state.worker_proc = subprocess.Popen([sys.executable, "worker_script.py"], env=env)
        st.toast("Worker iniciado.", icon="✅")

    if stop and st.session_state.worker_proc is not None:
        try:
            st.session_state.worker_proc.terminate()
        except Exception:
            pass

        time.sleep(1.0)
        st.session_state.worker_proc = None
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        zip_base = base_tmp / f"ambientadas_interrumpidas_{stamp}"

        imgs_now = list(carpeta_ambientada.glob("*.jpg"))
        if imgs_now:
            zip_path = shutil.make_archive(str(zip_base), "zip", str(carpeta_ambientada))
            st.session_state.zip_ready_path = zip_path
            st.toast(f"Generación detenida. {len(imgs_now)} imagen(es) zipeadas.", icon="🛑")
        else:
            st.session_state.zip_ready_path = None
            st.warning("Generación detenida, pero no hay imágenes para comprimir.")

    if reset:
        st.session_state.uploaded_excel_path = None
        st.session_state.zip_ready_path = None
        st.session_state.worker_proc = None
        st.session_state.measures_input_path = None
        st.session_state.measures_output_path = None
        st.rerun()

# ==========================
# TABS PRINCIPALES
# ==========================

tab1, tab2 = st.tabs(["Ambientador de Imágenes", "Extractor de Medidas"])

with tab1:
    st.markdown("### Progreso (galería de imágenes ambientadas)")
    st.caption("Las imágenes aparecen a medida que se generan…")

    if st.session_state.worker_proc is not None:
        st_autorefresh(interval=2000, key="auto_refresh_while_running")

    imgs = sorted(glob.glob(str(carpeta_ambientada / "*.jpg")))
    if imgs:
        cols = st.columns(4)
        for i, path in enumerate(imgs):
            with cols[i % 4]:
                st.image(path, use_container_width=True, caption=Path(path).name)
    else:
        st.info("Aún no hay imágenes ambientadas.")

    done_flag = (base_tmp / "status.done").exists()
    proc_finished = (
        st.session_state.worker_proc is not None
        and st.session_state.worker_proc.poll() is not None
    )

    if proc_finished or done_flag:
        time.sleep(0.7)
        st.session_state.worker_proc = None

        imgs_now = list(carpeta_ambientada.glob("*.jpg"))
        if imgs_now:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            zip_base = base_tmp / f"ambientadas_{stamp}"
            zip_path = shutil.make_archive(str(zip_base), "zip", str(carpeta_ambientada))
            st.session_state.zip_ready_path = zip_path
            st.toast(f"Proceso finalizado. {len(imgs_now)} imagen(es). ZIP listo.", icon="🎉")
        else:
            st.warning("El proceso finalizó pero no se encontraron imágenes en la carpeta 'ambientada'.")

        try:
            (base_tmp / "status.done").unlink(missing_ok=True)
        except Exception:
            pass

    if st.session_state.zip_ready_path and os.path.exists(st.session_state.zip_ready_path):
        with open(st.session_state.zip_ready_path, "rb") as f:
            st.download_button(
                label=f"Descargar ZIP ({Path(st.session_state.zip_ready_path).name})",
                data=f.read(),
                file_name=Path(st.session_state.zip_ready_path).name,
                mime="application/zip",
                type="primary",
                use_container_width=True,
                key="btn_download_zip_ambientador"
            )

    st.divider()
    st.markdown("### Estado del proceso")
    if st.session_state.worker_proc is None:
        st.write("⏸️ Inactivo")
    else:
        st.write("▶️ Ejecutando… (PID:", st.session_state.worker_proc.pid, ")")

with tab2:
    st.header("Extractor de Medidas desde Excel")
    st.write("Sube un Excel que tenga las columnas **PRODUCT_ID** y **SPECIFICATIONS**.")

    up = st.file_uploader(
        "Archivo .xlsx con PRODUCT_ID y SPECIFICATIONS",
        type=["xlsx"],
        key="measures_excel_uploader"
    )

    if up is not None:
        tmp_dir = Path("uploaded_medidas")
        tmp_dir.mkdir(parents=True, exist_ok=True)
        input_path = tmp_dir / ("medidas_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".xlsx")
        with open(input_path, "wb") as f:
            f.write(up.getbuffer())
        st.session_state.measures_input_path = str(input_path.resolve())
        st.success(f"Excel de medidas cargado: {input_path.name}")

    if st.session_state.measures_input_path:
        st.info(f"Archivo a procesar: {Path(st.session_state.measures_input_path).name}")
        if st.button("Procesar medidas con IA", type="primary", key="btn_procesar_medidas"):
            try:
                with st.spinner("Procesando filas y llamando a la IA..."):
                    out_path = procesar_excel_medidas(Path(st.session_state.measures_input_path))
                st.session_state.measures_output_path = str(out_path)
                st.success("Procesamiento terminado. Puedes descargar el archivo resultante más abajo.")
            except Exception as e:
                st.error(f"Error procesando el Excel de medidas: {e}")

    if st.session_state.measures_output_path and os.path.exists(st.session_state.measures_output_path):
        with open(st.session_state.measures_output_path, "rb") as f:
            st.download_button(
                label=f"Descargar Excel con medidas ({Path(st.session_state.measures_output_path).name})",
                data=f.read(),
                file_name=Path(st.session_state.measures_output_path).name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
                use_container_width=True,
                key="btn_download_medidas"
            )
