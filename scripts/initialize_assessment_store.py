"""Initialize the canonical per-hexagon analytical store.

Every later processing stage updates this table by ``id_hex``.  Raw indicators
are stored as area-weighted means/percentages; thematic scores are only filled
after the corresponding source has been processed and quality-checked.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import pandas as pd


FLOAT_COLUMNS = [
    "precipitacao_media_mm_ano",
    "precipitacao_cv_pct",
    "eto_media_mm_ano",
    "etc_cacau_mm_ano",
    "irrigacao_bruta_media_mm_ano",
    "irrigacao_bruta_p90_mm_ano",
    "temperatura_media_c",
    "declividade_media_pct",
    "solo_favoravel_pct",
    "boa_drenagem_pct",
    "seguranca_hidrica_indice",
    "distancia_ilheus_km",
    "tempo_ilheus_h",
    "frete_referencia_rs_t",
    "terra_referencia_rs_ha",
    "mao_obra_infra_indice",
    "prodes_pos_2020_pct",
    "embargos_oficiais_qtd",
    "clima_irrigacao",
    "tipo_solo",
    "relevo_drenagem",
    "seguranca_hidrica",
    "logistica_ilheus",
    "custo_terra",
    "mao_obra_infra",
    "risco_socioambiental",
    "nota_geral",
]

STRING_COLUMNS = [
    "classe_potencial",
    "status_calculo",
    "versao_fontes",
    "data_atualizacao",
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hex-grid", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("data/processed/assessments.parquet"))
    args = parser.parse_args()

    hex_grid = gpd.read_file(args.hex_grid)
    if "id_hex" not in hex_grid:
        raise ValueError("A malha deve conter a coluna id_hex.")
    if hex_grid["id_hex"].duplicated().any():
        raise ValueError("A malha possui identificadores de hexágono duplicados.")

    points = hex_grid.to_crs("EPSG:4326").geometry.representative_point()
    store = pd.DataFrame(
        {
            "id": hex_grid["id_hex"].astype("string"),
            "tipo": "Hexágono 5 km",
            "fonte_geometria": "Índice territorial",
            "nome": hex_grid["id_hex"].astype("string"),
            "area_ha": hex_grid["area_bahia_ha"].astype("Float64"),
            "latitude": points.y,
            "longitude": points.x,
        }
    )
    for column in FLOAT_COLUMNS:
        store[column] = pd.Series(pd.NA, dtype="Float64")
    for column in STRING_COLUMNS:
        store[column] = pd.Series("PENDENTE", dtype="string")
    store["status_calculo"] = "AGUARDANDO_FONTES"
    store["versao_fontes"] = "BASE_VAZIA_V1"
    store["data_atualizacao"] = pd.Timestamp.now(tz="America/Bahia").isoformat()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    store.to_parquet(args.output, index=False)
    print(f"Base analítica criada: {len(store):,} hexágonos")


if __name__ == "__main__":
    main()
