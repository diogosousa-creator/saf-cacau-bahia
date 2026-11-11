"""Build lightweight tiles for the real (R$/ha) VTN land-cost reference map."""
from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import mapbox_vector_tile
import mercantile
import numpy as np
import pandas as pd
from shapely.geometry import box, mapping


def category(score):
    if score is None or pd.isna(score) or not np.isfinite(float(score)): return 0
    if score >= 80: return 5
    if score >= 65: return 4
    if score >= 50: return 3
    if score >= 35: return 2
    return 1


CLASSES = {0: "Sem referência", 1: "Custo muito alto", 2: "Custo alto", 3: "Custo intermediário", 4: "Custo baixo", 5: "Custo muito baixo"}


def number(value, digits=0):
    return None if pd.isna(value) or not np.isfinite(float(value)) else round(float(value), digits)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hex", type=Path, default=Path("data/processed/bahia_hex_5km.geojson"))
    parser.add_argument("--assessments", type=Path, default=Path("data/processed/assessments.parquet"))
    parser.add_argument("--output", type=Path, default=Path("static/hex_custo_terra_tiles_v1"))
    parser.add_argument("--minzoom", type=int, default=4); parser.add_argument("--maxzoom", type=int, default=7)
    args = parser.parse_args()
    fields = ["id", "vtn_agricola_hibrido_rs_ha", "vtn_simet_mrt_rs_ha", "vtn_origem", "vtn_ano_base", "mrt_id", "vtn_confianca", "vtn_intervalo_80_pct", "custo_terra_score"]
    units = gpd.read_file(args.hex).to_crs(4326).merge(pd.read_parquet(args.assessments, columns=fields), left_on="id_hex", right_on="id", how="left", validate="one_to_one")
    units["category"] = units.custo_terra_score.map(category); index = units.sindex; count = 0
    for zoom in range(args.minzoom, args.maxzoom + 1):
        tolerance_m = 78_271.5 / (2 ** zoom)
        for tile in mercantile.tiles(*units.total_bounds, zooms=[zoom]):
            bounds = mercantile.bounds(tile); clip = box(bounds.west, bounds.south, bounds.east, bounds.north)
            subset = units.iloc[list(index.query(clip, predicate="intersects"))].copy()
            subset["geometry"] = subset.geometry.intersection(clip); subset = subset[~subset.geometry.is_empty].to_crs(3857)
            subset["geometry"] = subset.geometry.simplify(tolerance_m, preserve_topology=True); subset = subset.to_crs(4326)
            for code in range(6):
                features = [{"geometry": mapping(row.geometry), "properties": {
                    "referencia_exibicao": "Análise territorial", "classe_mapa": CLASSES[code],
                    "vtn_rs_ha": number(row.vtn_agricola_hibrido_rs_ha), "nota_mapa": number(row.custo_terra_score, 1),
                    "mrt_id": str(row.mrt_id), "origem": str(row.vtn_origem), "ano_base": str(row.vtn_ano_base),
                    "confianca": str(row.vtn_confianca), "intervalo_80_pct": number(row.vtn_intervalo_80_pct, 1),
                }} for row in subset[subset.category.eq(code)].itertuples(index=False)]
                if not features: continue
                encoded = mapbox_vector_tile.encode({"name": "custo_terra_hex", "features": features}, default_options={"quantize_bounds": (bounds.west, bounds.south, bounds.east, bounds.north)})
                destination = args.output / "score" / str(code) / str(zoom) / str(tile.x); destination.mkdir(parents=True, exist_ok=True)
                (destination / f"{tile.y}.pbf").write_bytes(encoded); count += 1
    print(f"Tiles de custo da terra criados: {count:,}")


if __name__ == "__main__": main()
