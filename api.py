"""API de predicción de precio de inmuebles.

Ejecutar desde la raíz del proyecto:
    uvicorn api:app --reload
Docs interactivas en http://127.0.0.1:8000/docs
"""
import os
from pathlib import Path
from typing import Literal

import joblib
import pandas as pd
from fastapi import FastAPI
from pydantic import BaseModel, Field

MODEL_PATH = Path(os.getenv("MODEL_PATH", Path(__file__).resolve().parent / "modelo_precio_inmuebles.pkl"))

bundle = joblib.load(MODEL_PATH)
pipe = bundle["pipeline"]
FEATURES = bundle["features"]
CATS = bundle["categorias"]


class Inmueble(BaseModel):
    # Categorías abiertas: valores no vistos en entrenamiento se mapean a "Otra"
    region: str
    ciudad: str
    distrito: str | None = None
    # Categorías cerradas
    tipo_inmueble: Literal["casa", "departamento"]
    tipo_aviso: Literal["individual", "unidad_proyecto"]
    tipo_conjunto: Literal["condominio", "edificio", "independiente"]
    areas_comunes: Literal["si", "no"]
    etapa: Literal["construido", "en_construccion", "en_planos", "estreno"]
    # Rangos de los filtros aplicados en entrenamiento
    area_m2: float = Field(ge=30, le=500)
    dormitorios: int = Field(ge=0, le=7)
    banos: int = Field(ge=0, le=7)
    estacionamientos: int = Field(default=0, ge=0, le=4)

    model_config = {
        "json_schema_extra": {
            "example": {
                "region": "Lima", "ciudad": "Lima", "distrito": "Miraflores",
                "tipo_inmueble": "departamento", "tipo_aviso": "individual",
                "tipo_conjunto": "edificio", "areas_comunes": "si", "etapa": "construido",
                "area_m2": 95, "dormitorios": 2, "banos": 2, "estacionamientos": 1,
            }
        }
    }


class Prediccion(BaseModel):
    precio_pen: float


def preparar(inmuebles: list[Inmueble]) -> pd.DataFrame:
    X = pd.DataFrame([i.model_dump() for i in inmuebles])
    X["distrito"] = X["distrito"].fillna("-")
    for col, validas in CATS.items():
        X.loc[~X[col].isin(validas), col] = "Otra"
    return X[FEATURES]


app = FastAPI(title="Predicción precio inmuebles")


@app.get("/health")
def health():
    return {"status": "ok", "sklearn_version": bundle["sklearn_version"], "metricas_test": bundle["metricas_test"]}


@app.post("/predict", response_model=Prediccion | list[Prediccion])
def predict(payload: Inmueble | list[Inmueble]):
    inmuebles = payload if isinstance(payload, list) else [payload]
    preds = [Prediccion(precio_pen=round(float(p), 2)) for p in pipe.predict(preparar(inmuebles))]
    return preds if isinstance(payload, list) else preds[0]
