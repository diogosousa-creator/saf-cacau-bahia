"""Prepare the two canonical WebGIS stores for all scoring criteria."""

from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import pandas as pd


CRITERIA = (
    "tipo_solo", "relevo_drenagem", "clima_irrigacao", "seguranca_hidrica",
    "custo_terra", "mao_obra_infra", "risco_socioambiental", "logistica_ilheus",
)


def prepare(frame: pd.DataFrame, unit_id: str, unit_type: str) -> pd.DataFrame:
    result = frame.copy()
    result["unit_id"] = result[unit_id].astype("string")
    result["unit_type"] = unit_type
    result["analysis_schema_version"] = "CANONICAL_V1"
    result["geometry_version"] = "BAHIA_2026_01"
    result["analysis_run_id"] = "SOLO_FISICO_V2"
    for criterion in CRITERIA:
        score = f"{criterion}_score"
        if score not in result:
            result[score] = result[criterion] if criterion in result else pd.NA
        result[score] = pd.to_numeric(result[score], errors="coerce").clip(0, 100)
        for suffix, default in (("coverage_pct", pd.NA), ("confidence", "unknown"), ("status", "unknown"), ("source_key", pd.NA), ("method_version", pd.NA), ("alert_level", "none"), ("alert_flags", "[]")):
            column = f"{criterion}_{suffix}"
            if column not in result:
                result[column] = default
        missing = result[score].isna()
        result.loc[missing, f"{criterion}_status"] = "unknown"
        result.loc[missing, f"{criterion}_confidence"] = "unknown"
    return result


def apply_soil_metadata(result: pd.DataFrame) -> pd.DataFrame:
    available = result["tipo_solo_score"].notna()
    texture_coverage = result.get("solo_textura_validos_px", pd.Series(0, index=result.index)).gt(0) * 100
    ibge_coverage = result.get("cobertura_ibge_pedologia_pct", pd.Series(pd.NA, index=result.index))
    result.loc[available, "tipo_solo_coverage_pct"] = pd.concat([texture_coverage, ibge_coverage], axis=1).min(axis=1)
    result.loc[available, "tipo_solo_confidence"] = "medium"
    result.loc[available, "tipo_solo_status"] = "complete"
    result.loc[available, "tipo_solo_source_key"] = "mapbiomas_solo_c3_0_30cm+ibge_pedologia_250k"
    result.loc[available, "tipo_solo_method_version"] = "QUALIDADE_FISICA_SOLO_v2"
    critical = result.get("area_restritiva_pedologica_pct", pd.Series(0, index=result.index)).fillna(0)
    result.loc[critical.ge(10), "tipo_solo_alert_level"] = "warning"
    result.loc[critical.ge(10), "tipo_solo_alert_flags"] = '["area_critica_requer_validacao"]'
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--processed", type=Path, default=Path("data/processed"))
    args = parser.parse_args()
    hex_path = args.processed / "assessments.parquet"
    farm_path = args.processed / "fazendas_georeferenciadas.geoparquet"
    hexes = apply_soil_metadata(prepare(pd.read_parquet(hex_path), "id", "hex_5km"))
    farms = apply_soil_metadata(prepare(gpd.read_parquet(farm_path), "id_fazenda", "imovel_certificado"))
    hexes.to_parquet(hex_path, index=False)
    farms.to_parquet(farm_path, index=False)
    print(f"Schema canônico pronto: {len(hexes):,} hexágonos e {len(farms):,} imóveis.")


if __name__ == "__main__":
    main()
