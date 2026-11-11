"""Build compact MVTs for the real road-distance logistics map."""
from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import mapbox_vector_tile
import mercantile
import numpy as np
import pandas as pd
from shapely.geometry import box, mapping


def category(score: float | None) -> int:
    """Five visual bands from the immutable continuous score, never km bins."""
    if score is None or pd.isna(score) or not np.isfinite(float(score)): return 0
    if score >= 80: return 5
    if score >= 65: return 4
    if score >= 50: return 3
    if score >= 35: return 2
    return 1


CLASS = {
    0: "Sem rota OSM – validação necessária",
    1: "Muito desfavorável",
    2: "Desfavorável",
    3: "Intermediária",
    4: "Favorável",
    5: "Muito favorável",
}


def value(item, digits=1):
    return None if item is None or pd.isna(item) or not np.isfinite(float(item)) else round(float(item), digits)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hex", type=Path, default=Path("data/processed/bahia_hex_5km.geojson"))
    parser.add_argument("--assessments", type=Path, default=Path("data/processed/assessments.parquet"))
    parser.add_argument("--output", type=Path, default=Path("static/hex_logistica_ilheus_tiles_v1"))
    parser.add_argument("--minzoom", type=int, default=4)
    parser.add_argument("--maxzoom", type=int, default=7)
    args = parser.parse_args()
    fields = ["id", "logistica_distancia_rota_km", "logistica_distancia_snap_km", "logistica_ilheus", "logistica_classe_km", "logistica_status_rota", "logistica_destino", "logistica_ilheus_confidence"]
    units = gpd.read_file(args.hex).to_crs(4326).merge(
        pd.read_parquet(args.assessments, columns=fields), left_on="id_hex", right_on="id", how="left", validate="one_to_one"
    )
    units["category"] = units["logistica_ilheus"].map(category)
    index = units.sindex
    count = 0
    for zoom in range(args.minzoom, args.maxzoom + 1):
        tolerance_m = 78_271.5 / (2 ** zoom)
        for tile in mercantile.tiles(*units.total_bounds, zooms=[zoom]):
            bounds = mercantile.bounds(tile)
            clip = box(bounds.west, bounds.south, bounds.east, bounds.north)
            subset = units.iloc[list(index.query(clip, predicate="intersects"))].copy()
            subset["geometry"] = subset.geometry.intersection(clip)
            subset = subset[~subset.geometry.is_empty].to_crs(3857)
            subset["geometry"] = subset.geometry.simplify(tolerance_m, preserve_topology=True)
            subset = subset.to_crs(4326)
            for code in range(0, 6):
                features = []
                for row in subset[subset.category.eq(code)].itertuples(index=False):
                    features.append({"geometry": mapping(row.geometry), "properties": {
                        "referencia_exibicao": "Análise territorial", "classe_mapa": CLASS[code],
                        "distancia_rota_km": value(row.logistica_distancia_rota_km),
                        "nota_mapa": value(row.logistica_ilheus), "distancia_snap_km": value(row.logistica_distancia_snap_km, 2),
                        "status_rota": str(row.logistica_status_rota), "confianca": str(row.logistica_ilheus_confidence),
                        "destino": str(row.logistica_destino),
                    }})
                if not features: continue
                tile_bytes = mapbox_vector_tile.encode({"name": "logistica_hex", "features": features}, default_options={"quantize_bounds": (bounds.west, bounds.south, bounds.east, bounds.north)})
                destination = args.output / "distance" / str(code) / str(zoom) / str(tile.x)
                destination.mkdir(parents=True, exist_ok=True)
                (destination / f"{tile.y}.pbf").write_bytes(tile_bytes)
                count += 1
    print(f"Tiles de logística criados: {count:,}")


if __name__ == "__main__":
    main()
