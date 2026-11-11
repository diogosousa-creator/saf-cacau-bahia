"""Create 'Fazendas georeferenciadas' from official SIGEF and SNCI extracts.

SIGEF takes spatial precedence. SNCI contributes only its residual footprint
outside the SIGEF union, preventing double counting in later analyses.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.ops import unary_union


ANALYSIS_CRS = "EPSG:5880"
WEB_CRS = "EPSG:4326"
MIN_REMAINDER_HA = 1.0
WEB_SIMPLIFY_TOLERANCE_M = 30
ID_CANDIDATES = ("codimovel", "codigoimovel", "codigoimo", "idimovel", "codparcela", "parcela", "id", "codigo", "matricula")
ANALYTICAL_FLOAT_FIELDS = (
    "precipitacao_media_mm_ano", "precipitacao_cv_pct", "eto_media_mm_ano", "etc_cacau_mm_ano",
    "irrigacao_bruta_media_mm_ano", "irrigacao_bruta_p90_mm_ano", "temperatura_media_c",
    "declividade_media_pct", "solo_favoravel_pct", "boa_drenagem_pct", "seguranca_hidrica_indice",
    "distancia_ilheus_km", "tempo_ilheus_h", "frete_referencia_rs_t", "terra_referencia_rs_ha",
    "mao_obra_infra_indice", "prodes_pos_2020_pct", "embargos_oficiais_qtd", "clima_irrigacao",
    "tipo_solo", "relevo_drenagem", "seguranca_hidrica", "logistica_ilheus", "custo_terra",
    "mao_obra_infra", "risco_socioambiental", "nota_geral",
)


def normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def find_identifier_column(columns: list[str]) -> str | None:
    lookup = {normalized(column): column for column in columns}
    return next((lookup[candidate] for candidate in ID_CANDIDATES if candidate in lookup), None)


def read_source(path: Path, source: str, bahia: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    frame = gpd.read_file(path)
    if frame.crs is None:
        raise ValueError(f"{path.name} não possui CRS definido.")
    frame = frame.to_crs(ANALYSIS_CRS)
    frame = frame[frame.geometry.notna()].copy()
    frame["geometry"] = frame.geometry.make_valid()
    frame = frame[~frame.geometry.is_empty].copy()
    # The official extracts are already UF-specific.  Use the state envelope as
    # a fast guard instead of clipping every geometry against a detailed border.
    state_bounds = bahia.total_bounds
    frame = frame.cx[state_bounds[0] : state_bounds[2], state_bounds[1] : state_bounds[3]].copy()
    id_column = find_identifier_column(frame.columns.tolist())
    frame["identificador_incra"] = frame[id_column].astype("string") if id_column else pd.Series(pd.NA, index=frame.index, dtype="string")
    frame["fonte_geometria"] = source
    return frame[["identificador_incra", "fonte_geometria", "geometry"]]


def enrich(frame: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    frame = frame.copy()
    frame["id_fazenda"] = [f"FG-{position:08d}" for position in range(1, len(frame) + 1)]
    frame["area_ha"] = (frame.to_crs("EPSG:6933").geometry.area / 10_000).round(2)
    frame["tipo"] = "Fazenda georeferenciada"
    frame["nome"] = frame["identificador_incra"].fillna(frame["id_fazenda"])
    frame["status_calculo"] = "AGUARDANDO_FONTES"
    for field in ANALYTICAL_FLOAT_FIELDS:
        frame[field] = pd.Series(pd.NA, index=frame.index, dtype="Float64")
    frame["classe_potencial"] = pd.Series("PENDENTE", index=frame.index, dtype="string")
    return frame


def snci_residuals(snci: gpd.GeoDataFrame, sigef: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Remove only SIGEF parcels that actually intersect each SNCI geometry."""
    spatial_index = sigef.sindex

    def residual(geometry):
        positions = list(spatial_index.query(geometry, predicate="intersects"))
        if not positions:
            return geometry
        return geometry.difference(unary_union(sigef.geometry.iloc[positions]))

    result = snci.copy()
    result["geometry"] = result.geometry.apply(residual)
    result = result[~result.geometry.is_empty].copy()
    result["area_analitica_ha"] = result.to_crs("EPSG:6933").geometry.area / 10_000
    return result[result["area_analitica_ha"] >= MIN_REMAINDER_HA].copy()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bahia", type=Path, required=True)
    parser.add_argument("--sigef", type=Path, required=True)
    parser.add_argument("--snci", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("data/processed"))
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    bahia = gpd.read_file(args.bahia).to_crs(ANALYSIS_CRS)
    sigef = read_source(args.sigef, "SIGEF", bahia)
    snci = read_source(args.snci, "SNCI complementar", bahia)

    sigef["area_analitica_ha"] = sigef.to_crs("EPSG:6933").geometry.area / 10_000
    snci = snci_residuals(snci, sigef)

    farms = enrich(gpd.GeoDataFrame(pd.concat([sigef, snci], ignore_index=True), geometry="geometry", crs=ANALYSIS_CRS)).to_crs(WEB_CRS)
    farms.to_parquet(args.output / "fazendas_georeferenciadas.geoparquet")
    # The browser needs only rendering and tooltip attributes.  Keep every
    # analytical field in GeoParquet and avoid repeating empty values in JSON.
    web_farms = farms[["id_fazenda", "identificador_incra", "fonte_geometria", "area_ha", "geometry"]].to_crs(ANALYSIS_CRS).copy()
    web_farms["geometry"] = web_farms.geometry.simplify(WEB_SIMPLIFY_TOLERANCE_M, preserve_topology=True)
    web_farms.to_crs(WEB_CRS).to_file(args.output / "fazendas_georeferenciadas_web.geojson", driver="GeoJSON")
    print(f"SIGEF: {len(sigef):,}; SNCI complementar: {len(snci):,}; camada final: {len(farms):,}")


if __name__ == "__main__":
    main()
