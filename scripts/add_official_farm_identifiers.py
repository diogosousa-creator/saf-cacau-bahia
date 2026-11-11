"""Restore official, polygon-level identifiers for the certified-farm layer.

The WebGIS initially retained only the property code (which may represent
several certified parcels).  This migration spatially reconciles each final
polygon with the supplied official extract and stores one visible identifier:
SIGEF ``parcela_co`` or SNCI ``num_certif``.  The internal ``FG-`` key is
kept only as a deterministic fallback and is never the preferred label.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely


METRIC_CRS = "EPSG:5880"


def best_matches(target: gpd.GeoDataFrame, source: gpd.GeoDataFrame, official_field: str, source_property_field: str, containment: bool) -> pd.DataFrame:
    """Assign the official source polygon with the strongest geometric match."""
    left = target[["id_fazenda", "identificador_incra", "geometry"]].copy()
    left["geometry"] = left.geometry.make_valid()
    left["_target_row"] = left.index
    left["_target_area"] = left.geometry.area
    right = source[[official_field, source_property_field, "geometry"]].copy().reset_index(drop=True)
    right["geometry"] = right.geometry.make_valid()
    right["_source_area"] = right.geometry.area
    joined = left.sjoin(right, how="left", predicate="intersects")
    joined = joined.dropna(subset=["index_right"]).copy()
    joined["index_right"] = joined["index_right"].astype(int)
    source_geom = right.geometry.iloc[joined["index_right"].to_numpy()].array
    intersection = shapely.area(shapely.intersection(joined.geometry.array, source_geom))
    joined["_intersection"] = intersection
    if containment:
        # SNCI residuals are intentionally cut by SIGEF; coverage of the
        # retained target is the correct evidence of its source certificate.
        joined["_match_score"] = joined["_intersection"] / joined["_target_area"]
    else:
        union = joined["_target_area"].to_numpy() + right["_source_area"].iloc[joined["index_right"].to_numpy()].to_numpy() - intersection
        joined["_match_score"] = intersection / union
    joined["_same_property"] = (
        joined["identificador_incra"].astype("string").fillna("").to_numpy()
        == joined[source_property_field].astype("string").fillna("").to_numpy()
    )
    # The source property-code comparison resolves rare overlapping parcels;
    # geometry score resolves parcels from the same property.
    joined = joined.sort_values(["_same_property", "_match_score", official_field], ascending=[False, False, True])
    best = joined.drop_duplicates("_target_row", keep="first")[["_target_row", official_field, "_match_score"]].copy()
    return best


def main() -> None:
    processed = Path("data/processed/fazendas_georeferenciadas.geoparquet")
    farms = gpd.read_parquet(processed).to_crs(METRIC_CRS)
    sigef = gpd.read_file("data/raw/sigef_ba/Sigef Brasil_BA.shp").to_crs(METRIC_CRS)
    snci = gpd.read_file("data/raw/snci_ba/Imóvel certificado SNCI Brasil_BA.shp").to_crs(METRIC_CRS)

    farms["identificador_oficial_poligono"] = pd.Series(pd.NA, index=farms.index, dtype="string")
    farms["tipo_identificador_oficial"] = pd.Series(pd.NA, index=farms.index, dtype="string")
    farms["confianca_identificador_oficial"] = pd.Series("fallback", index=farms.index, dtype="string")

    sigef_mask = farms["fonte_geometria"].eq("SIGEF")
    sigef_best = best_matches(farms.loc[sigef_mask], sigef, "parcela_co", "codigo_imo", containment=False)
    sigef_ok = sigef_best["_match_score"].ge(0.995)
    farms.loc[sigef_best.loc[sigef_ok, "_target_row"], "identificador_oficial_poligono"] = sigef_best.loc[sigef_ok, "parcela_co"].astype("string").to_numpy()
    farms.loc[sigef_best.loc[sigef_ok, "_target_row"], "tipo_identificador_oficial"] = "Parcela SIGEF"
    farms.loc[sigef_best.loc[sigef_ok, "_target_row"], "confianca_identificador_oficial"] = "official_geometry_match"

    snci_mask = farms["fonte_geometria"].eq("SNCI complementar")
    snci_best = best_matches(farms.loc[snci_mask], snci, "num_certif", "cod_imovel", containment=True)
    snci_ok = snci_best["_match_score"].ge(0.995)
    farms.loc[snci_best.loc[snci_ok, "_target_row"], "identificador_oficial_poligono"] = snci_best.loc[snci_ok, "num_certif"].astype("string").to_numpy()
    farms.loc[snci_best.loc[snci_ok, "_target_row"], "tipo_identificador_oficial"] = "Certificado SNCI"
    farms.loc[snci_best.loc[snci_ok, "_target_row"], "confianca_identificador_oficial"] = "official_geometry_match"

    missing = farms["identificador_oficial_poligono"].isna()
    farms.loc[missing, "identificador_oficial_poligono"] = farms.loc[missing, "id_fazenda"].astype("string")
    farms.loc[missing, "tipo_identificador_oficial"] = "Referência interna"
    # Only use an official identifier as visual label when it is unique in the
    # final layer. Any anomalous duplicate falls back to the unique internal key.
    duplicate = farms["identificador_oficial_poligono"].duplicated(keep=False) & ~missing
    farms.loc[duplicate, "identificador_oficial_poligono"] = farms.loc[duplicate, "id_fazenda"].astype("string")
    farms.loc[duplicate, "tipo_identificador_oficial"] = "Referência interna"
    farms["referencia_exibicao"] = farms["tipo_identificador_oficial"] + ": " + farms["identificador_oficial_poligono"]

    farms.to_crs("EPSG:4326").to_parquet(processed, index=False)
    print(f"SIGEF oficial: {int((farms.tipo_identificador_oficial == 'Parcela SIGEF').sum()):,}")
    print(f"SNCI oficial: {int((farms.tipo_identificador_oficial == 'Certificado SNCI').sum()):,}")
    print(f"Fallback interno: {int((farms.tipo_identificador_oficial == 'Referência interna').sum()):,}")
    print(f"Referências únicas: {farms.referencia_exibicao.nunique():,} de {len(farms):,}")


if __name__ == "__main__":
    main()
