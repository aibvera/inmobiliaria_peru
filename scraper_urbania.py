"""
Scraper de muestras de precios de casas y departamentos en venta en urbania.pe.

Flujo por cada URL (listado de búsqueda):
  1. Abre el navegador (perfil persistente para conservar cookies entre aperturas).
  2. Entra a la URL y espera un tiempo aleatorio entre 5 y 10 segundos.
  3. Hace scroll hacia abajo un poco.
  4. Copia la información de las tarjetas. Si una tarjeta es un proyecto
     (varias unidades con precios distintos), entra a la ficha del proyecto y
     extrae cada unidad.
  5. Cierra el navegador y pasa a la siguiente URL.

Cobertura:
  - Lima Metropolitana (provincia de Lima): ~500 inmuebles.
  - Cada una de las demás provincias del país con avisos en Urbania: ~200
    inmuebles (o los que haya, si son menos).

Uso:
  .venv\\Scripts\\python.exe scraper_urbania.py                # corrida completa (reanuda si se cortó)
  .venv\\Scripts\\python.exe scraper_urbania.py --solo lima--lima --limite-capital 60
  .venv\\Scripts\\python.exe scraper_urbania.py --reiniciar    # borra progreso y CSV
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import re
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, sync_playwright

BASE = "https://urbania.pe"
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
CSV_PATH = DATA_DIR / "urbania_inmuebles.csv"
PROGRESS_PATH = DATA_DIR / "progreso_urbania.json"
PROVINCIAS_PATH = DATA_DIR / "provincias_urbania.json"
PROFILE_DIR = ROOT / ".browser_profile"

CARDS_PER_PAGE = 30
CAPITAL_SLUG = "lima--lima"

# slug de Urbania -> nombre oficial del departamento (región)
REGIONES = {
    "amazonas": "Amazonas",
    "ancash": "Áncash",
    "apurimac": "Apurímac",
    "arequipa": "Arequipa",
    "ayacucho": "Ayacucho",
    "cajamarca": "Cajamarca",
    "callao": "Callao",
    "cusco": "Cusco",
    "huancavelica": "Huancavelica",
    "huanuco": "Huánuco",
    "ica": "Ica",
    "junin": "Junín",
    "la-libertad": "La Libertad",
    "lambayeque": "Lambayeque",
    "lima": "Lima",
    "loreto": "Loreto",
    "madre-de-dios": "Madre de Dios",
    "moquegua": "Moquegua",
    "pasco": "Pasco",
    "piura": "Piura",
    "puno": "Puno",
    "san-martin": "San Martín",
    "tacna": "Tacna",
    "tumbes": "Tumbes",
    "ucayali": "Ucayali",
}

# 43 distritos oficiales de la provincia de Lima (Lima Metropolitana)
DISTRITOS_LIMA = [
    "Ancón", "Ate", "Barranco", "Breña", "Carabayllo", "Chaclacayo", "Chorrillos",
    "Cieneguilla", "Comas", "El Agustino", "Independencia", "Jesús María", "La Molina",
    "La Victoria", "Lima", "Lince", "Los Olivos", "Lurigancho", "Lurín",
    "Magdalena del Mar", "Miraflores", "Pachacámac", "Pucusana", "Pueblo Libre",
    "Puente Piedra", "Punta Hermosa", "Punta Negra", "Rímac", "San Bartolo", "San Borja",
    "San Isidro", "San Juan de Lurigancho", "San Juan de Miraflores", "San Luis",
    "San Martín de Porres", "San Miguel", "Santa Anita", "Santa María del Mar",
    "Santa Rosa", "Santiago de Surco", "Surquillo", "Villa El Salvador",
    "Villa María del Triunfo",
]

# Urbania escribe algunas provincias sin tilde; nombre oficial
CIUDADES_OFICIALES = {
    "Huanuco": "Huánuco", "Jaen": "Jaén", "Ferrenafe": "Ferreñafe", "San Roman": "San Román",
    "Viru": "Virú", "Morropon": "Morropón", "San Martin": "San Martín", "Nazca": "Nasca",
    "Huarochiri": "Huarochirí", "Canete": "Cañete", "Camana": "Camaná",
}

# Precios por debajo de esto son marcadores ("consultar") o errores de carga
PRECIO_MINIMO = 1000

ALIAS_DISTRITOS = {
    "lima cercado": "Lima",
    "cercado de lima": "Lima",
    "cercado": "Lima",
    "magdalena": "Magdalena del Mar",
    "surco": "Santiago de Surco",
    "ate vitarte": "Ate",
    "lurigancho chosica": "Lurigancho",
    "chosica": "Lurigancho",
    "smp": "San Martín de Porres",
    "sjl": "San Juan de Lurigancho",
    "sjm": "San Juan de Miraflores",
    "vmt": "Villa María del Triunfo",
    "ves": "Villa El Salvador",
}

AMENIDADES = {
    "piscina": r"piscina",
    "gimnasio": r"gimnasio|\bgym\b",
    "sum": r"\bsum\b|sal[oó]n de usos m[uú]ltiples|sala de usos m[uú]ltiples",
    "parrilla": r"parrilla",
    "juegos_infantiles": r"juegos infantiles|zona de juegos|sala de juegos|play ?ground",
    "areas_verdes": r"[aá]reas? verdes?|jard[ií]n(es)? com[uú]n",
    "coworking": r"cowork",
    "club_house": r"club ?house",
    "terraza_comun": r"rooftop|terraza com[uú]n|azotea com[uú]n",
    "lobby": r"\blobby\b",
    "pet_zone": r"pet ?(zone|care|park)",
    "areas_comunes": r"[aá]reas? com[uú]n(es)?|zonas? com[uú]n(es)?",
    "seguridad": r"vigilancia|seguridad privada|guardian[ií]a|seguridad 24",
}
# Amenidades que implican áreas comunes (la seguridad sola no cuenta)
AMENIDADES_COMUNES = set(AMENIDADES) - {"seguridad"}

CAMPOS = [
    "id", "region", "ciudad", "distrito", "area_m2", "dormitorios", "banos",
    "precio", "moneda", "precio_pen", "precio_usd",
    "tipo_inmueble", "tipo_aviso", "etapa", "tipo_conjunto", "areas_comunes",
    "amenidades", "estacionamientos", "mantenimiento", "ubicacion", "url",
    "fecha_scraping",
]

# ---------------------------------------------------------------- utilidades


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def norm(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto or "")
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", texto.lower()).strip()


_DISTRITOS_NORM = {norm(d): d for d in DISTRITOS_LIMA}


def distrito_oficial(nombre: str) -> str:
    n = norm(nombre)
    return _DISTRITOS_NORM.get(n) or ALIAS_DISTRITOS.get(n, "")


def num(texto: str) -> float | None:
    texto = (texto or "").replace(",", "")
    m = re.search(r"\d+(?:\.\d+)?", texto)
    return float(m.group()) if m else None


def entero(v):
    return int(v) if isinstance(v, float) and v.is_integer() else v


def parse_json(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


MONEDAS = {"S/": "PEN", "S/.": "PEN", "USD": "USD", "US$": "USD", "$": "USD", "€": "EUR"}


def parse_precios(texto: str) -> list[tuple[str, float]]:
    """'S/ 2,412,000 · USD 720,000' -> [('PEN', 2412000.0), ('USD', 720000.0)]"""
    out = []
    for simbolo, monto in re.findall(r"(S/\.?|USD|US\$|\$|€)\s*([\d][\d,\.]*)", texto or ""):
        valor = num(monto)
        if valor:
            out.append((MONEDAS[simbolo], valor))
    return out


def moneda_original(precios: list[tuple[str, float]]) -> tuple[str, float] | tuple[None, None]:
    """Urbania muestra el precio publicado y su conversión (p.ej. 'S/ 1,686,168 · USD 500,000').
    El publicado es el más 'redondo' (menos dígitos significativos); si empatan
    (el anunciante cargó ambos), se prefiere USD, la convención en venta."""
    if not precios:
        return None, None

    def significativos(v: float) -> int:
        return len(str(int(v)).rstrip("0"))

    return min(precios, key=lambda p: (significativos(p[1]), p[0] != "USD"))


def area_m2(texto: str) -> float | None:
    """'174 m² tot.' | '41 a 65 m² tot.' | '1000 ha tot.' -> m² (primer valor)."""
    m = re.search(r"([\d][\d,\.]*)\s*(?:a\s*[\d,\.]+\s*)?(m²|m2|ha)", texto or "")
    if not m:
        return None
    valor = num(m.group(1))
    if valor is None:
        return None
    # Muchos anunciantes eligen "ha" por error (p.ej. "120 ha" en una casa de USD 100k):
    # sólo se convierte cuando el valor es plausible como hectáreas.
    return valor * 10000 if m.group(2) == "ha" and valor < 10 else valor


def extraer_feature(features: str, patron: str) -> float | None:
    m = re.search(r"([\d]+)(?:\s*a\s*\d+)?\s*" + patron, features or "", re.I)
    return float(m.group(1)) if m else None


def detectar_amenidades(texto: str) -> list[str]:
    t = (texto or "").lower()
    return [k for k, pat in AMENIDADES.items() if re.search(pat, t)]


def detectar_etapa(texto: str, por_defecto: str = "construido") -> str:
    t = norm(texto)
    if re.search(r"en planos?|preventa|pre venta", t):
        return "en_planos"
    if re.search(r"en construccion", t):
        return "en_construccion"
    if re.search(r"estreno|estrenar|terminado|entrega inmediata", t):
        return "estreno"
    if re.search(r"\d+\s*anos?|antiguedad", t):
        return "construido"
    return por_defecto


def tipo_desde_url(url: str) -> str:
    m = re.search(r"venta-de-([a-z-]+?)-en-", url or "")
    if not m:
        return ""
    t = m.group(1)
    if "departamento" in t or "duplex" in t or "penthouse" in t or "flat" in t:
        return "departamento"
    if "casa" in t:
        return "casa"
    return t


def tipo_conjunto(tipo_inmueble: str, texto: str) -> str:
    """condominio | edificio | independiente"""
    if re.search(r"condominio|conjunto residencial|residencial cerrad|club ?house", (texto or "").lower()):
        return "condominio"
    if tipo_inmueble == "departamento":
        return "edificio"
    return "independiente" if tipo_inmueble else ""


def distrito_desde_ubicacion(ubicacion: str) -> str:
    """Tarjeta: '<barrio o distrito>, <distrito o ciudad>' (p.ej. 'Barranco, Lima',
    'Los Rosales, Santiago de Surco', 'Santa Beatriz, Lima Cercado')."""
    partes = [p.strip() for p in (ubicacion or "").split(",") if p.strip()]
    if not partes:
        return ""
    candidatos = partes[:1] if len(partes) > 1 and norm(partes[-1]) == "lima" else partes[::-1]
    for c in candidatos:
        d = distrito_oficial(c)
        if d:
            return d
    return ""


def distrito_desde_location(loc: dict | None) -> str:
    """Recorre la jerarquía de ubicación de Urbania (SUBZONA > ZONA > CIUDAD ...)."""
    nodo = loc
    while isinstance(nodo, dict):
        if nodo.get("label") == "ZONA":
            return distrito_oficial(nodo.get("name", ""))
        nodo = nodo.get("parent")
    return ""


# ---------------------------------------------------------------- navegador

JS_LISTADO = """
() => {
  const q = (c, k) => (c.querySelector(`[data-qa="${k}"]`)?.innerText || '').replace(/\\s*\\n\\s*/g, ' | ').trim();
  const cards = [...document.querySelectorAll('[data-qa^="posting "]')]
    .filter(c => !c.closest('[class*="carrousel"]') && !c.closest('[class*="recommended"]')
                 && !c.closest('[class*="thin-postings"]'));
  return {
    h1: document.querySelector('h1')?.innerText || '',
    title: document.title,
    links: [...document.querySelectorAll('a[href^="/buscar/"]')]
      .map(a => [a.getAttribute('href'), a.innerText.trim()]),
    cards: cards.map(c => ({
      id: c.dataset.id, tipo: c.dataset.postingType, url: (c.dataset.toPosting || '').split('?')[0],
      galeria: q(c, 'POSTING_CARD_GALLERY'), precio: q(c, 'POSTING_CARD_PRICE'),
      expensas: q(c, 'expensas'), features: q(c, 'POSTING_CARD_FEATURES'),
      ubicacion: q(c, 'POSTING_CARD_LOCATION'), descripcion: q(c, 'POSTING_CARD_DESCRIPTION'),
    })),
  };
}
"""

JS_PROYECTO = """
() => {
  if (typeof avisoInfo === 'undefined') return null;
  const p = v => { if (typeof v === 'string') { try { return JSON.parse(v); } catch (e) { return null; } } return v; };
  const units = (p(avisoInfo.units) || []).map(u => ({
    postingId: u.postingId, url: u.url, title: u.title, generatedTitle: u.generatedTitle,
    description: u.descriptionNormalized || '', priceOperationTypes: u.priceOperationTypes,
    mainFeatures: p(u.mainFeatures), postingLocation: p(u.postingLocation),
    realEstateType: u.realEstateType,
  }));
  return {
    units, generalFeatures: p(avisoInfo.generalFeatures) || {}, flags: avisoInfo.flagsFeatures || [],
    location: avisoInfo.location || null, realEstateType: avisoInfo.realEstateType || null,
    title: avisoInfo.postingTitle || '', description: avisoInfo.description || '',
    expenses: avisoInfo.expenses || null,
  };
}
"""

CHALLENGE_TITLES = ("just a moment", "un momento", "attention required", "verificando")


class Navegador:
    def __init__(self, pw, canal: str | None, headless: bool):
        self.pw = pw
        self.canal = canal
        self.headless = headless

    def abrir(self) -> BrowserContext:
        PROFILE_DIR.mkdir(exist_ok=True)
        kwargs = dict(
            user_data_dir=str(PROFILE_DIR),
            headless=self.headless,
            locale="es-PE",
            timezone_id="America/Lima",
            viewport={"width": random.randint(1280, 1600), "height": random.randint(800, 1000)},
            args=["--disable-blink-features=AutomationControlled"],
        )
        if self.canal:
            kwargs["channel"] = self.canal
        return self.pw.chromium.launch_persistent_context(**kwargs)


def esperar_y_scrollear(page: Page) -> None:
    time.sleep(random.uniform(5, 10))
    esperar_challenge(page)
    for _ in range(random.randint(2, 4)):
        page.mouse.wheel(0, random.randint(300, 700))
        time.sleep(random.uniform(0.4, 1.2))


def esperar_challenge(page: Page, maximo: int = 60) -> None:
    """Si aparece la verificación de Cloudflare, espera a que se resuelva
    (en modo visible puedes completarla tú manualmente)."""
    def en_challenge() -> bool:
        return any(t in page.title().lower() for t in CHALLENGE_TITLES)

    if not en_challenge():
        return
    log("  Verificación anti-bot detectada; esperando a que se resuelva...")
    inicio = time.time()
    while en_challenge():
        if time.time() - inicio > maximo:
            raise RuntimeError("La verificación anti-bot no se resolvió a tiempo")
        time.sleep(2)


def ir(page: Page, url: str) -> None:
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    esperar_y_scrollear(page)


# ---------------------------------------------------------------- extracción


def fila_desde_tarjeta(card: dict, region: str, ciudad: str, es_capital: bool) -> dict | None:
    precios = parse_precios(card["precio"])
    moneda, precio = moneda_original(precios)
    if precio is None:
        return None
    texto = " ".join([card["galeria"], card["descripcion"], card["url"]])
    tipo = tipo_desde_url(card["url"])
    amen = detectar_amenidades(texto)
    pen = next((v for m, v in precios if m == "PEN"), None)
    usd = next((v for m, v in precios if m == "USD"), None)
    es_proyecto = card["tipo"] == "DEVELOPMENT"
    return {
        "id": card["id"],
        "region": region,
        "ciudad": ciudad,
        "distrito": distrito_desde_ubicacion(card["ubicacion"]) if es_capital else "",
        "area_m2": entero(area_m2(card["features"])),
        "dormitorios": entero(extraer_feature(card["features"], r"dorm")),
        "banos": entero(extraer_feature(card["features"], r"baño")),
        "precio": entero(precio),
        "moneda": moneda,
        "precio_pen": entero(pen),
        "precio_usd": entero(usd),
        "tipo_inmueble": tipo,
        "tipo_aviso": "proyecto_precio_desde" if es_proyecto else "individual",
        "etapa": detectar_etapa(card["galeria"] + " " + card["descripcion"],
                                "en_construccion" if es_proyecto else "construido"),
        "tipo_conjunto": tipo_conjunto(tipo, texto),
        "areas_comunes": "si" if (set(amen) & AMENIDADES_COMUNES) else "no",
        "amenidades": ";".join(amen),
        "estacionamientos": entero(extraer_feature(card["features"], r"estac")),
        "mantenimiento": card["expensas"].replace(" Mantenimiento", "").strip(),
        "ubicacion": card["ubicacion"],
        "url": BASE + card["url"],
    }


def filas_desde_proyecto(info: dict, card: dict, region: str, ciudad: str, es_capital: bool,
                         max_unidades: int = 0) -> list[dict]:
    amen_proy = []
    for grupo in (info.get("generalFeatures") or {}).values():
        if isinstance(grupo, dict):
            amen_proy += [f.get("label", "") for f in grupo.values() if isinstance(f, dict)]
    texto_proy = " ".join([info.get("title", ""), info.get("description", ""), card["url"],
                           card["galeria"], " ".join(amen_proy)])
    # El estado del proyecto (CFT200: "En planos", "En construcción"...) es más preciso
    # que la antigüedad de cada unidad, que suele venir genérica.
    flags = " ".join(f.get("label", "") for f in info.get("flags") or [])
    etapa_proy = detectar_etapa(flags + " " + card["galeria"], "")
    amen = detectar_amenidades(texto_proy)

    filas = []
    for u in info.get("units") or []:
        tipo_re = ((u.get("realEstateType") or {}).get("name") or "").lower()
        if re.search(r"oficina|local|estacionamiento|dep[oó]sito|terreno|cochera", tipo_re):
            continue
        precio_obj = None
        for op in u.get("priceOperationTypes") or []:
            if (op.get("operationType") or {}).get("name") == "venta" and op.get("prices"):
                precio_obj = op["prices"][0]
        if not precio_obj or not precio_obj.get("amount"):
            continue
        mf = u.get("mainFeatures") or {}
        if not isinstance(mf, dict):
            mf = {}

        def feat(*ids, label=None):
            for fid in ids:
                if fid in mf:
                    return num(str(mf[fid].get("value", "")))
            if label:
                for f in mf.values():
                    if re.search(label, f.get("label", ""), re.I):
                        return num(str(f.get("value", "")))
            return None

        antig = next((str(f.get("value", "")) for f in mf.values()
                      if "antig" in norm(f.get("label", ""))), "")
        tipo = tipo_desde_url(u.get("url", "")) or ("casa" if "casa" in tipo_re else "departamento")
        loc = (u.get("postingLocation") or {}).get("location") or info.get("location")
        moneda = precio_obj.get("isoCode") or "PEN"
        monto = float(precio_obj["amount"])
        filas.append({
            "id": u.get("postingId"),
            "region": region,
            "ciudad": ciudad,
            "distrito": (distrito_desde_location(loc) or distrito_desde_ubicacion(card["ubicacion"]))
            if es_capital else "",
            "area_m2": entero(feat("CFT100", label=r"^total") or feat("CFT101", label=r"techad|cubiert")),
            "dormitorios": entero(feat("CFT2", label=r"dormitorio")),
            "banos": entero(feat("CFT3", label=r"^baño")),
            "precio": entero(monto),
            "moneda": moneda,
            "precio_pen": entero(monto) if moneda == "PEN" else None,
            "precio_usd": entero(monto) if moneda == "USD" else None,
            "tipo_inmueble": tipo,
            "tipo_aviso": "unidad_proyecto",
            "etapa": etapa_proy or detectar_etapa(antig, "en_construccion"),
            "tipo_conjunto": tipo_conjunto(tipo, texto_proy),
            "areas_comunes": "si" if (set(amen) & AMENIDADES_COMUNES) else "no",
            "amenidades": ";".join(amen),
            "estacionamientos": entero(feat("CFT7", label=r"estacionamiento|cochera")),
            "mantenimiento": card["expensas"].replace(" Mantenimiento", "").strip(),
            "ubicacion": card["ubicacion"],
            "url": BASE + (u.get("url") or card["url"]),
        })
    if max_unidades and len(filas) > max_unidades:
        filas = random.sample(filas, max_unidades)
    return filas


def total_resultados(h1: str) -> int:
    m = re.match(r"\s*([\d,\.]+)", h1 or "")
    return int(re.sub(r"[^\d]", "", m.group(1))) if m else 0


# ---------------------------------------------------------------- persistencia


class Salida:
    def __init__(self):
        DATA_DIR.mkdir(exist_ok=True)
        self.ids: set[str] = set()
        if CSV_PATH.exists():
            with CSV_PATH.open(encoding="utf-8-sig", newline="") as f:
                self.ids = {r["id"] for r in csv.DictReader(f)}
        nuevo = not CSV_PATH.exists()
        self.f = CSV_PATH.open("a", encoding="utf-8-sig" if nuevo else "utf-8", newline="")
        self.w = csv.DictWriter(self.f, fieldnames=CAMPOS)
        if nuevo:
            self.w.writeheader()

    def escribir(self, fila: dict) -> bool:
        if str(fila["id"]) in self.ids:
            return False
        if (fila["precio"] or 0) < PRECIO_MINIMO or "alquiler-de-" in fila["url"]:
            return False
        fila["ciudad"] = CIUDADES_OFICIALES.get(fila["ciudad"], fila["ciudad"])
        fila["fecha_scraping"] = datetime.now().strftime("%Y-%m-%d")
        self.w.writerow(fila)
        self.f.flush()
        self.ids.add(str(fila["id"]))
        return True

    def cerrar(self):
        self.f.close()


def cargar_json(path: Path, defecto):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else defecto


def guardar_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- flujo


def visitar_listado(nav: Navegador, url: str):
    """Abre navegador -> URL -> espera -> scroll -> extrae -> cierra."""
    ctx = nav.abrir()
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        ir(page, url)
        return ctx, page, page.evaluate(JS_LISTADO)
    except Exception:
        ctx.close()
        raise


def descubrir_provincias(nav: Navegador) -> dict:
    """Para cada región visita los listados de casas y departamentos y toma los
    enlaces a sus provincias (sólo aparecen las que tienen avisos)."""
    provincias = cargar_json(PROVINCIAS_PATH, {})
    for slug_reg, nombre_reg in REGIONES.items():
        if slug_reg in provincias:
            continue
        encontradas: dict[str, str] = {}
        total = 0
        for tipo in ("departamentos", "casas"):
            url = f"{BASE}/buscar/venta-de-{tipo}-en-{slug_reg}"
            log(f"Descubriendo provincias: {url}")
            try:
                ctx, _, data = visitar_listado(nav, url)
                ctx.close()
            except Exception as e:
                log(f"  Error: {e}")
                continue
            total += total_resultados(data["h1"])
            patron = re.compile(rf"^/buscar/venta-de-{tipo}-en-([a-z0-9-]+)--{re.escape(slug_reg)}$")
            for href, texto in data["links"]:
                m = patron.match(href or "")
                if m and texto:
                    encontradas[f"{m.group(1)}--{slug_reg}"] = texto
                elif m:
                    encontradas.setdefault(f"{m.group(1)}--{slug_reg}", m.group(1).replace("-", " ").title())
        if total and not encontradas:
            # Región con avisos pero sin desglose por provincia: se raspa la región completa.
            encontradas[slug_reg] = ""
        provincias[slug_reg] = {"region": nombre_reg, "total": total, "provincias": encontradas}
        log(f"  {nombre_reg}: {total} avisos, provincias: {list(encontradas.values()) or '-'}")
        guardar_json(PROVINCIAS_PATH, provincias)
    return provincias


def raspar_provincia(nav: Navegador, salida: Salida, slug: str, region: str, ciudad: str,
                     limite: int, es_capital: bool, max_unidades: int = 0,
                     fraccion_proyectos: float = 1.0) -> int:
    base_url = f"{BASE}/buscar/venta-de-casas-o-departamentos-en-{slug}"
    limite_proyectos = math.ceil(limite * fraccion_proyectos)
    obtenidos = 0
    de_proyectos = 0
    pagina = 1
    max_paginas = None
    while obtenidos < limite and (max_paginas is None or pagina <= max_paginas):
        url = base_url if pagina == 1 else f"{base_url}?page={pagina}"
        log(f"[{ciudad or region}] página {pagina} -> {url}")
        ctx = None
        try:
            ctx, page, data = visitar_listado(nav, url)
            total = total_resultados(data["h1"])
            max_paginas = math.ceil(total / CARDS_PER_PAGE) if total else 0
            if pagina == 1:
                log(f"  {total} resultados ({max_paginas} páginas)")
            if not data["cards"]:
                break
            # Avisos individuales primero, para que los proyectos no copen la cuota.
            cards = sorted(data["cards"], key=lambda c: c["tipo"] == "DEVELOPMENT")
            for card in cards:
                if obtenidos >= limite:
                    break
                ciudad_fila = ciudad or (card["ubicacion"].split(",")[-1].strip())
                es_proyecto = card["tipo"] == "DEVELOPMENT"
                if es_proyecto and de_proyectos >= limite_proyectos:
                    continue
                if es_proyecto:
                    filas = []
                    try:
                        sub = ctx.new_page()
                        ir(sub, BASE + card["url"])
                        info = sub.evaluate(JS_PROYECTO)
                        sub.close()
                        if info:
                            filas = filas_desde_proyecto(info, card, region, ciudad_fila, es_capital,
                                                         max_unidades)
                        log(f"  Proyecto {card['id']}: {len(filas)} unidades")
                    except Exception as e:
                        log(f"  Error en proyecto {card['id']}: {e}")
                    if not filas:
                        fila = fila_desde_tarjeta(card, region, ciudad_fila, es_capital)
                        filas = [fila] if fila else []
                else:
                    fila = fila_desde_tarjeta(card, region, ciudad_fila, es_capital)
                    filas = [fila] if fila else []
                for fila in filas:
                    if obtenidos >= limite or (es_proyecto and de_proyectos >= limite_proyectos):
                        break
                    if salida.escribir(fila):
                        obtenidos += 1
                        de_proyectos += es_proyecto
            log(f"  Acumulado {ciudad or region}: {obtenidos}/{limite}")
        except Exception as e:
            log(f"  Error en {url}: {e}")
        finally:
            if ctx:
                ctx.close()  # cerrar el navegador antes de la siguiente URL
        pagina += 1
    return obtenidos


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limite-capital", type=int, default=500, help="inmuebles para Lima Metropolitana")
    ap.add_argument("--limite-provincia", type=int, default=200, help="inmuebles por provincia")
    ap.add_argument("--max-unidades-proyecto", type=int, default=5,
                    help="máximo de unidades a tomar de cada proyecto (0 = todas)")
    ap.add_argument("--fraccion-proyectos", type=float, default=0.5,
                    help="fracción máxima de la cuota que puede venir de proyectos (1 = sin tope)")
    ap.add_argument("--solo", nargs="*", help="slugs de provincia a procesar (p.ej. lima--lima arequipa--arequipa)")
    ap.add_argument("--navegador", default="msedge",
                    help="canal de Playwright: msedge, chrome o 'chromium' (el incluido)")
    ap.add_argument("--headless", action="store_true", help="sin ventana (más probable que Cloudflare bloquee)")
    ap.add_argument("--reiniciar", action="store_true", help="borra CSV y progreso antes de empezar")
    args = ap.parse_args()

    if args.reiniciar:
        for p in (CSV_PATH, PROGRESS_PATH):
            p.unlink(missing_ok=True)

    progreso = cargar_json(PROGRESS_PATH, {"hechas": {}})
    canal = None if args.navegador == "chromium" else args.navegador

    with sync_playwright() as pw:
        nav = Navegador(pw, canal, args.headless)

        if args.solo:
            objetivos = []
            for slug in args.solo:
                slug_reg = slug.split("--")[-1]
                nombre = slug.split("--")[0].replace("-", " ").title() if "--" in slug else ""
                objetivos.append((slug, REGIONES.get(slug_reg, slug_reg), nombre))
        else:
            provincias = descubrir_provincias(nav)
            objetivos = [(slug, info["region"], nombre)
                         for info in provincias.values()
                         for slug, nombre in info["provincias"].items()]
            # La capital primero
            objetivos.sort(key=lambda o: o[0] != CAPITAL_SLUG)

        salida = Salida()
        try:
            for slug, region, ciudad in objetivos:
                if slug in progreso["hechas"] and not args.solo:
                    log(f"Saltando {slug} (ya procesado: {progreso['hechas'][slug]} inmuebles)")
                    continue
                es_capital = slug == CAPITAL_SLUG
                if es_capital:
                    ciudad = "Lima"
                limite = args.limite_capital if es_capital else args.limite_provincia
                n = raspar_provincia(nav, salida, slug, region, ciudad, limite, es_capital,
                                     args.max_unidades_proyecto, args.fraccion_proyectos)
                if not args.solo:  # las corridas parciales de prueba no marcan progreso
                    progreso["hechas"][slug] = n
                    guardar_json(PROGRESS_PATH, progreso)
                log(f"== {region} / {ciudad or '(región completa)'}: {n} inmuebles ==")
        except KeyboardInterrupt:
            log("Interrumpido. Vuelve a ejecutar el script para continuar donde quedó.")
        finally:
            salida.cerrar()

    log(f"Listo. CSV: {CSV_PATH}")


if __name__ == "__main__":
    sys.exit(main())
