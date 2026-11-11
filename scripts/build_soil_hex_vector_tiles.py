"""Build zoom-dependent vector tiles for the MapBiomas soil hex layer."""

from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import mapbox_vector_tile
import mercantile
import pandas as pd
from shapely.geometry import box, mapping


SCORE_CLASS = {"Muito baixa": 1, "Baixa": 2, "Média": 3, "Alta": 4, "Muito alta": 5,
               "Muito restrita": 1, "Restrita": 2, "Condicionada": 3, "Favorável": 4, "Muito favorável": 5}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hex", type=Path, default=Path("data/processed/bahia_hex_5km.geojson"))
    parser.add_argument("--assessments", type=Path, default=Path("data/processed/assessments.parquet"))
    parser.add_argument("--output", type=Path, default=Path("static/hex_soil_tiles"))
    parser.add_argument("--score-field", default="tipo_solo")
    parser.add_argument("--class-field", default="classe_tipo_solo")
    parser.add_argument("--score-mode", default="score")
    parser.add_argument("--minzoom", type=int, default=4)
    parser.add_argument("--maxzoom", type=int, default=9)
    args = parser.parse_args()

    hexes = gpd.read_file(args.hex).to_crs("EPSG:4326")
    stats = pd.read_parquet(args.assessments)[["id", args.score_field, args.class_field, "solo_textura_dominante", "solo_textura_dominante_codigo", "solo_textura_favoravel_pct"]].rename(columns={args.score_field: "tipo_solo", args.class_field: "classe_tipo_solo"})
    hexes = hexes.merge(stats, left_on="id_hex", right_on="id", how="left", validate="one_to_one")
    hexes["score_class"] = hexes["classe_tipo_solo"].map(SCORE_CLASS).fillna(0).astype(int)
    hexes["raw_class"] = hexes["solo_textura_dominante_codigo"].fillna(0).astype(int)
    index = hexes.sindex
    args.output.mkdir(parents=True, exist_ok=True)
    built = 0
    for zoom in range(args.minzoom, args.maxzoom + 1):
        tolerance_m = 78_271.5 / (2**zoom)
        for tile in mercantile.tiles(*hexes.total_bounds, zooms=[zoom]):
            bounds = mercantile.bounds(tile)
            positions = list(index.query(box(bounds.west, bounds.south, bounds.east, bounds.north), predicate="intersects"))
            if not positions:
                continue
            subset = hexes.iloc[positions].copy()
            subset["geometry"] = subset.geometry.intersection(box(bounds.west, bounds.south, bounds.east, bounds.north))
            subset = subset[~subset.geometry.is_empty].to_crs("EPSG:3857")
            subset["geometry"] = subset.geometry.simplify(tolerance_m, preserve_topology=True)
            subset = subset.to_crs("EPSG:4326")
            base_features = [{
                "geometry": mapping(row.geometry),
                "properties": {
                    "referencia_exibicao": "Análise territorial",
                    "nota_mapa": None if pd.isna(row.tipo_solo) else round(float(row.tipo_solo), 1),
                    "classe_mapa": row.classe_tipo_solo if pd.notna(row.classe_tipo_solo) else "Sem dados",
                    "solo_textura_dominante": row.solo_textura_dominante if pd.notna(row.solo_textura_dominante) else "Sem dados",
                    "solo_textura_favoravel_pct": None if pd.isna(row.solo_textura_favoravel_pct) else round(float(row.solo_textura_favoravel_pct), 1),
                    "score_class": int(row.score_class),
                    "raw_class": int(row.raw_class),
                },
            } for row in subset.itertuples(index=False)]
            for mode, field, classes in ((args.score_mode, "score_class", range(1, 6)), ("raw", "raw_class", range(1, 7))):
                for category in classes:
                    features = [feature for feature in base_features if feature["properties"][field] == category]
                    if not features:
                        continue
                    encoded = mapbox_vector_tile.encode(
                        {"name": "solo_hex", "features": features},
                        default_options={"quantize_bounds": (bounds.west, bounds.south, bounds.east, bounds.north)},
                    )
                    destination = args.output / mode / str(category) / str(zoom) / str(tile.x)
                    destination.mkdir(parents=True, exist_ok=True)
                    (destination / f"{tile.y}.pbf").write_bytes(encoded)
                    built += 1
    print(f"Tiles criados: {built:,}")


if __name__ == "__main__":
    main()
