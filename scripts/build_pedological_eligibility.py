"""Build the preliminary cacao pedological eligibility from official sources.

MapBiomas Solo supplies the 0-30 cm texture score already calculated for each
unit. Embrapa/PronaSolos agricultural aptitude and IBGE Pedologia 1:250,000
are independent multipliers, never replacements for the measured texture.
This is regional screening, never a farm soil survey.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd


ANALYSIS_CRS = "EPSG:5880"
HARD_EMBRAPA_REGEX = r"GLEISSOLO|ORGANOSSOLO|AFLORAMENTO|LIT[ÓO]LICO|ROCHOS[AO]|PEDREGOS[AO] FORTE|CORPOS D.ÁGUA"
HARD_IBGE_ORDERS = {"AFLORAMENTOS DE ROCHAS", "DUNAS", "ORGANOSSOLO"}
IBGE_ORDER_MULTIPLIER = {
    "LATOSSOLO": 1.00, "NITOSSOLO": 0.95, "ARGISSOLO": 0.82,
    "CHERNOSSOLO": 0.80, "LUVISSOLO": 0.65, "CAMBISSOLO": 0.60,
    "VERTISSOLO": 0.55, "PLANOSSOLO": 0.30, "PLINTOSSOLO": 0.38,
    "GLEISSOLO": 0.08, "ORGANOSSOLO": 0.08, "ESPODOSSOLO": 0.40,
    "NEOSSOLO": 0.45, "AFLORAMENTOS DE ROCHAS": 0.15, "DUNAS": 0.15,
}


def aptitude_multiplier(code: object) -> float:
    """Conservative regional multiplier based on the Embrapa crop-use class."""
    value = str(code or "").strip()
    if value.startswith("1"):
        return 1.00  # boa para lavouras
    if value.startswith("2"):
        return 0.75  # regular para lavouras
    if value.startswith("3"):
        return 0.45  # restrita para lavouras
    return 0.00      # classes 4, 5 and 6: not suitable for crops


def load_aptitude(raw_dir: Path, output: Path) -> gpd.GeoDataFrame:
    pages = sorted(raw_dir.glob("page_*.geojson"))
    if not pages:
        raise FileNotFoundError(f"Nenhuma página GeoJSON em {raw_dir}")
    columns = ["cd_fcim", "legenda_ap", "solo_comp1", "apt_comp1", "lima_comp1", "limb_comp1", "limc_comp1", "eros_comp1", "mec_comp1", "geometry"]
    parts = [gpd.read_file(page)[columns] for page in pages]
    frame = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs=parts[0].crs).drop_duplicates(subset=["cd_fcim", "geometry"])
    frame = frame[frame.geometry.notna() & ~frame.geometry.is_empty].copy().to_crs(ANALYSIS_CRS)
    # The source scale is 1:500,000.  Simplification removes delivery noise,
    # while retaining a resolution finer than the source's intended use.
    frame["geometry"] = frame.geometry.make_valid().simplify(100, preserve_topology=True)
    text = frame[["solo_comp1", "legenda_ap", "apt_comp1"]].fillna("").agg(" ".join, axis=1)
    frame["aptidao_multiplicador"] = frame["apt_comp1"].map(aptitude_multiplier)
    frame["restricao_critica_embrapa"] = (frame["aptidao_multiplicador"].eq(0) | text.str.contains(HARD_EMBRAPA_REGEX, case=False, regex=True))
    frame.loc[frame["restricao_critica_embrapa"], "aptidao_multiplicador"] = 0.0
    output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_crs("EPSG:4326").to_parquet(output, index=False)
    return frame


def load_ibge_pedology(raw: Path, output: Path) -> gpd.GeoDataFrame:
    """Prepare the official IBGE 1:250,000 pedology polygons for Bahia."""
    columns = ["id1", "ordem", "textura", "erosao", "pedregosid", "rochosidad", "relevo", "legenda", "geometry"]
    frame = gpd.read_file(raw)[columns]
    frame = frame[frame.geometry.notna() & ~frame.geometry.is_empty].copy().to_crs(ANALYSIS_CRS)
    # This is a 1:250,000 source; 50 m removes geometry noise without inventing detail.
    frame["geometry"] = frame.geometry.make_valid().simplify(50, preserve_topology=True)
    text = frame[["ordem", "textura", "erosao", "pedregosid", "rochosidad", "relevo", "legenda"]].fillna("").agg(" ".join, axis=1).str.upper()
    frame["ibge_pedologia_multiplicador"] = frame["ordem"].str.upper().map(IBGE_ORDER_MULTIPLIER).fillna(0.60)
    # Gates apply to the intersecting fraction, not to the whole hexagon/farm.
    frame["critica_rocha"] = text.str.contains(r"AFLORAMENTO|MUITO PEDREGOSA|MUITO ROCHOSA", regex=True)
    frame["critica_escarpa"] = text.str.contains(r"ESCARPADO", regex=True)
    frame["critica_hidromorfia"] = frame["ordem"].str.upper().isin({"GLEISSOLO", "ORGANOSSOLO"}) | text.str.contains(r"HIDROM[OÓ]RF", regex=True)
    frame["restricao_critica_ibge"] = frame[["critica_rocha", "critica_escarpa", "critica_hidromorfia"]].any(axis=1)
    # The IBGE attribute generally says only "pedregosa" or "rochosa", with
    # no intensity. Treat it as a penalty, not a blanket exclusion.
    frame.loc[text.str.contains(r"\bPEDREGOSA\b", regex=True), "ibge_pedologia_multiplicador"] *= 0.75
    frame.loc[text.str.contains(r"\bROCHOSA\b", regex=True), "ibge_pedologia_multiplicador"] *= 0.60
    frame.loc[text.str.contains(r"FORTE\s+ONDULADO", regex=True), "ibge_pedologia_multiplicador"] *= 0.70
    frame.loc[text.str.contains(r"\bONDULADO\b", regex=True) & ~text.str.contains(r"FORTE\s+ONDULADO", regex=True), "ibge_pedologia_multiplicador"] *= 0.90
    output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_crs("EPSG:4326").to_parquet(output, index=False)
    return frame


def zonal_aptitude(units: gpd.GeoDataFrame, aptitude: gpd.GeoDataFrame, id_field: str) -> pd.DataFrame:
    source = aptitude[["aptidao_multiplicador", "restricao_critica_embrapa", "geometry"]]
    overlap = gpd.overlay(units[[id_field, "geometry"]], source, how="intersection", keep_geom_type=False, make_valid=False)
    overlap["area_m2"] = overlap.geometry.area
    grouped = overlap.groupby(id_field, as_index=False).agg(
        area_intersecao_m2=("area_m2", "sum"),
        aptidao_multiplicador=("aptidao_multiplicador", lambda values: np.average(values, weights=overlap.loc[values.index, "area_m2"])),
    )
    hard = overlap[overlap["restricao_critica_embrapa"]].groupby(id_field)["area_m2"].sum().rename("area_restricao_critica_m2")
    grouped = grouped.join(hard, on=id_field).fillna({"area_restricao_critica_m2": 0})
    areas = units.set_index(id_field).geometry.area.rename("area_unidade_m2")
    grouped = grouped.join(areas, on=id_field)
    grouped["cobertura_aptidao_pct"] = (grouped.area_intersecao_m2 / grouped.area_unidade_m2 * 100).clip(upper=100)
    grouped["restricao_critica_embrapa_pct"] = (grouped.area_restricao_critica_m2 / grouped.area_unidade_m2 * 100).clip(upper=100)
    return grouped.drop(columns=["area_intersecao_m2", "area_restricao_critica_m2", "area_unidade_m2"])


def zonal_pedology(units: gpd.GeoDataFrame, pedology: gpd.GeoDataFrame, id_field: str) -> pd.DataFrame:
    source = pedology[["ibge_pedologia_multiplicador", "restricao_critica_ibge", "critica_rocha", "critica_escarpa", "critica_hidromorfia", "geometry"]]
    overlap = gpd.overlay(units[[id_field, "geometry"]], source, how="intersection", keep_geom_type=False, make_valid=False)
    overlap["area_m2"] = overlap.geometry.area
    grouped = overlap.groupby(id_field, as_index=False).agg(
        area_intersecao_m2=("area_m2", "sum"),
        ibge_pedologia_multiplicador=("ibge_pedologia_multiplicador", lambda values: np.average(values, weights=overlap.loc[values.index, "area_m2"])),
    )
    hard = overlap[overlap["restricao_critica_ibge"]].groupby(id_field)["area_m2"].sum().rename("area_restricao_critica_ibge_m2")
    grouped = grouped.join(hard, on=id_field).fillna({"area_restricao_critica_ibge_m2": 0})
    for flag, output in (("critica_rocha", "area_critica_rocha_m2"), ("critica_escarpa", "area_critica_escarpa_m2"), ("critica_hidromorfia", "area_critica_hidromorfia_m2")):
        areas_flag = overlap[overlap[flag]].groupby(id_field)["area_m2"].sum().rename(output)
        grouped = grouped.join(areas_flag, on=id_field).fillna({output: 0})
    areas = units.set_index(id_field).geometry.area.rename("area_unidade_m2")
    grouped = grouped.join(areas, on=id_field)
    grouped["cobertura_ibge_pedologia_pct"] = (grouped.area_intersecao_m2 / grouped.area_unidade_m2 * 100).clip(upper=100)
    grouped["restricao_critica_ibge_pct"] = (grouped.area_restricao_critica_ibge_m2 / grouped.area_unidade_m2 * 100).clip(upper=100)
    grouped["critica_rocha_pct"] = (grouped.area_critica_rocha_m2 / grouped.area_unidade_m2 * 100).clip(upper=100)
    grouped["critica_escarpa_pct"] = (grouped.area_critica_escarpa_m2 / grouped.area_unidade_m2 * 100).clip(upper=100)
    grouped["critica_hidromorfia_pct"] = (grouped.area_critica_hidromorfia_m2 / grouped.area_unidade_m2 * 100).clip(upper=100)
    return grouped.drop(columns=["area_intersecao_m2", "area_restricao_critica_ibge_m2", "area_critica_rocha_m2", "area_critica_escarpa_m2", "area_critica_hidromorfia_m2", "area_unidade_m2"])


def classify(score: object) -> str:
    if pd.isna(score): return "Sem dados"
    if score < 35: return "Muito baixa"
    if score < 50: return "Baixa"
    if score < 65: return "Média"
    if score < 80: return "Alta"
    return "Muito alta"


def apply(base: pd.DataFrame, aptitude_zonal: pd.DataFrame, pedology_zonal: pd.DataFrame, id_field: str, texture: pd.DataFrame | None = None) -> pd.DataFrame:
    table = base.copy()
    if texture is None:
        table["solo_textura_score"] = table["tipo_solo"]
    else:
        raw = texture[[id_field, "tipo_solo"]].rename(columns={"tipo_solo": "solo_textura_score"})
        table = table.drop(columns=["solo_textura_score"], errors="ignore").merge(raw, on=id_field, how="left", validate="one_to_one")
    drop_columns = ["aptidao_multiplicador", "aptidao_agricola_multiplicador", "restricao_critica_embrapa_pct", "restricao_critica_mapbiomas_pct", "cobertura_aptidao_pct", "ibge_pedologia_multiplicador", "restricao_critica_ibge_pct", "cobertura_ibge_pedologia_pct", "critica_rocha_pct", "critica_escarpa_pct", "critica_hidromorfia_pct", "penalidade_critica_pts", "area_restritiva_pedologica_pct", "elegibilidade_pedologica_status"]
    table = table.drop(columns=[column for column in drop_columns if column in table.columns])
    table = table.merge(aptitude_zonal, on=id_field, how="left", validate="one_to_one")
    table = table.merge(pedology_zonal, on=id_field, how="left", validate="one_to_one")
    table["restricao_critica_mapbiomas_pct"] = table["solo_textura_1_pct"].fillna(0)
    texture_weights = {1: 15, 2: 80, 3: 90, 4: 62.5, 5: 47.5, 6: 100}
    textura_score = sum(table[f"solo_textura_{code}_pct"].fillna(0) * weight / 100 for code, weight in texture_weights.items())
    textura_score = textura_score.where(table["solo_textura_validos_px"].fillna(0).gt(0))
    ibge_score = 100 * table["ibge_pedologia_multiplicador"]
    score_base = 0.50 * textura_score + 0.50 * ibge_score
    rock_pct = pd.concat([table["restricao_critica_mapbiomas_pct"], table["critica_rocha_pct"].fillna(0)], axis=1).max(axis=1)
    penalty = (rock_pct + table["critica_escarpa_pct"].fillna(0) + table["critica_hidromorfia_pct"].fillna(0)).clip(upper=25)
    table["penalidade_critica_pts"] = penalty.round(1)
    table["tipo_solo"] = (score_base - penalty).clip(lower=20).round(1)
    table.loc[textura_score.isna() | ibge_score.isna(), "tipo_solo"] = np.nan
    table["solo_textura_score"] = textura_score.round(1)
    table["classe_tipo_solo"] = table["tipo_solo"].map(classify)
    table["area_restritiva_pedologica_pct"] = (rock_pct + table["critica_escarpa_pct"].fillna(0) + table["critica_hidromorfia_pct"].fillna(0)).clip(upper=100).round(1)
    gate = table["area_restritiva_pedologica_pct"].ge(10)
    table["elegibilidade_pedologica_status"] = np.select(
        [table["tipo_solo"].isna(), gate, table["tipo_solo"].ge(65)],
        ["SEM_COBERTURA", "ÁREA_CRÍTICA_REQUER_VALIDAÇÃO", "ELEGÍVEL_PRELIMINAR"],
        default="CONDICIONADA",
    )
    table["fonte_tipo_solo"] = "MapBiomas Solo C3 0–30 cm + IBGE Pedologia 1:250.000"
    table["versao_tipo_solo"] = "QUALIDADE_FISICA_SOLO_v2"
    return table


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path, default=Path("data/raw/embrapa_aptidao_bahia"))
    parser.add_argument("--ibge-raw", type=Path, default=Path("data/raw/ibge_pedologia_bahia/ibge_pedologia_250k_bahia.geojson"))
    parser.add_argument("--hex-texture", type=Path, default=Path("data/interim/solo_textura_hex_0_30cm.parquet"))
    parser.add_argument("--processed", type=Path, default=Path("data/processed"))
    args = parser.parse_args()
    aptitude = load_aptitude(args.raw, args.processed / "embrapa_aptidao_agricola_bahia.geoparquet")
    pedology = load_ibge_pedology(args.ibge_raw, args.processed / "ibge_pedologia_250k_bahia.geoparquet")

    hexes = gpd.read_file(args.processed / "bahia_hex_5km.geojson").to_crs(ANALYSIS_CRS)
    aptitude_stats = zonal_aptitude(hexes, aptitude, "id_hex")
    pedology_stats = zonal_pedology(hexes, pedology, "id_hex")
    aptitude_stats.to_parquet(args.processed / "aptidao_agricola_hex.parquet", index=False)
    pedology_stats.to_parquet(args.processed / "ibge_pedologia_hex.parquet", index=False)
    assessments_path = args.processed / "assessments.parquet"
    texture = pd.read_parquet(args.hex_texture).rename(columns={"id_hex": "id"})
    updated = apply(pd.read_parquet(assessments_path), aptitude_stats.rename(columns={"id_hex": "id"}), pedology_stats.rename(columns={"id_hex": "id"}), "id", texture)
    updated.to_parquet(assessments_path, index=False)
    print(f"Aptidão Embrapa: {len(aptitude):,}; pedologia IBGE: {len(pedology):,}; hexágonos com cobertura: {len(pedology_stats):,}")


if __name__ == "__main__":
    main()
