# Inmobiliaria Lima

Modelo de precio de casas y departamentos en venta en Perú, entrenado con avisos de [urbania.pe](https://urbania.pe) y expuesto como API.

```
scraper_urbania.py           1. Descarga de datos  -> data/urbania_inmuebles.csv
modelamiento.ipynb           2. EDA + modelo       -> modelo_precio_inmuebles.pkl
api.py                       3. API de predicción  <- modelo_precio_inmuebles.pkl
```

## Instalación

Requiere Python 3.11 o superior.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 1. Descarga de datos

`scraper_urbania.py` recorre los listados de venta de Urbania con Playwright y guarda los avisos en `data/urbania_inmuebles.csv`:

- Lima Metropolitana: ~500 inmuebles.
- Cada provincia con avisos: ~200 inmuebles (o los que haya).
- De los proyectos inmobiliarios entra a la ficha y toma hasta 5 unidades.

```powershell
python scraper_urbania.py                                         # corrida completa
python scraper_urbania.py --solo lima--lima --limite-capital 60   # prueba corta
python scraper_urbania.py --reiniciar                             # borra CSV y progreso
```

- Usa el Microsoft Edge instalado por defecto. Para usar el Chromium de Playwright: `playwright install chromium` y luego `--navegador chromium`.
- Abre una ventana visible; si aparece la verificación anti-bot puedes resolverla a mano.
- Es lento a propósito (esperas aleatorias). Si se corta, al volver a ejecutarlo continúa donde quedó (`data/progreso_urbania.json`).
- `python scraper_urbania.py --help` muestra todas las opciones.

## 2. Notebook de modelamiento

`modelamiento.ipynb` lee el CSV y:

1. Trata nulos, filtra outliers (precio fuera del percentil 5–95, área fuera de 30–500 m², etc.) y agrupa regiones, ciudades y distritos poco frecuentes en `"Otra"`.
2. Hace un EDA de precios por región y precio por m².
3. Entrena un `RandomForestRegressor` con búsqueda aleatoria de hiperparámetros (CV estratificada por región) y muestra métricas y gráficos de error.
4. Exporta el pipeline final (encoder + modelo, reentrenado con toda la data) a `modelo_precio_inmuebles.pkl`, junto con las features, las categorías válidas y las métricas de test.

Abrirlo con VS Code o Jupyter usando el kernel de `.venv` y ejecutar todas las celdas.

## 3. API

```powershell
uvicorn api:app --reload
```

Documentación interactiva en http://127.0.0.1:8000/docs.

| Método | Ruta       | Descripción                                          |
|--------|------------|------------------------------------------------------|
| GET    | `/health`  | Estado, versión de sklearn y métricas del modelo     |
| POST   | `/predict` | Precio estimado en soles (un inmueble o una lista)   |

Ejemplo de request:

```json
{
  "region": "Lima",
  "ciudad": "Lima",
  "distrito": "Miraflores",
  "tipo_inmueble": "departamento",
  "tipo_aviso": "individual",
  "tipo_conjunto": "edificio",
  "areas_comunes": "si",
  "etapa": "construido",
  "area_m2": 95,
  "dormitorios": 2,
  "banos": 2,
  "estacionamientos": 1
}
```

Respuesta: `{"precio_pen": 915320.24}`

- `distrito` y `estacionamientos` son opcionales.
- Regiones, ciudades o distritos no vistos en el entrenamiento se tratan como `"Otra"`.
- Valores fuera del rango de entrenamiento (área 30–500 m², hasta 7 dormitorios/baños, hasta 4 estacionamientos) devuelven 422.
- Al reexportar el modelo hay que reiniciar la API. Para usar otro archivo: variable de entorno `MODEL_PATH`.
