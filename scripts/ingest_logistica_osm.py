"""Attach Colab OSM logistics outputs to the two analytical master tables.

This script intentionally does not score logistics: distance is retained as
an auditable raw field and ``logistica_ilheus`` remains available for the
business scoring rule defined later.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
LOG_FIELDS = [
    "logistica_origem_lon", "logistica_origem_lat", "logistica_distancia_rota_km",
    "logistica_distancia_snap_km", "logistica_status_rota", "logistica_fonte_rota",
    "logistica_destino", "logistica_processado_em",
]


def attach(table: pd.DataFrame, results: pd.DataFrame, table_id: str, result_id: str) -> pd.DataFrame:
    if results[result_id].duplicated().any():
        raise ValueError(f"Resultado contém {result_id} duplicado.")
    result = table.drop(columns=[field for field in LOG_FIELDS if field in table.columns], errors="ignore")
    result = result.merge(results, left_on=table_id, right_on=result_id, how="left", validate="one_to_one")
    if result_id != table_id:
        result = result.drop(columns=result_id)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hex-results", type=Path, required=True)
    parser.add_argument("--farm-results", type=Path, required=True)
    parser.add_argument("--processed", type=Path, default=ROOT / "data" / "processed")
    args = parser.parse_args()
    hex_results = pd.read_parquet(args.hex_results)
    farm_results = pd.read_parquet(args.farm_results)
    timestamp = datetime.now(timezone.utc).isoformat()
    for frame in (hex_results, farm_results):
        frame["logistica_processado_em"] = timestamp

    assessments = pd.read_parquet(args.processed / "assessments.parquet")
    farms = gpd.read_parquet(args.processed / "fazendas_georeferenciadas.geoparquet")
    assessments = attach(assessments, hex_results, "id", "id_hex")
    farms = attach(farms, farm_results, "id_fazenda", "id_fazenda")
    assessments.to_parquet(args.processed / "assessments.parquet", index=False)
    farms.to_parquet(args.processed / "fazendas_georeferenciadas.geoparquet", index=False)
    print(f"Logística anexada: {len(hex_results):,} hexágonos; {len(farm_results):,} imóveis.")


if __name__ == "__main__":
    main()
