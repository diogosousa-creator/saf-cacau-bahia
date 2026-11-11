"""Build property-level texture-score MVT tiles for certified farms.

Each tile set contains farms in one score class only.  This allows the browser
to use a fixed, opaque color for each layer without serializing full polygons
or borrowing the colour of a background hexagon.
"""

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
    parser.add_argument("--input", type=Path, default=Path("data/processed/fazendas_georeferenciadas.geoparquet"))
    parser.add_argument("--output", type=Path, default=Path("static/fazendas_solo_tiles"))
    parser.add_argument("--score-field", default="tipo_solo")
    parser.add_argument("--class-field", default="classe_tipo_solo")
    parser.add_argument("--minzoom", type=int, default=7)
    parser.add_argument("--maxzoom", type=int, default=9)
    parser.add_argument("--tile-x-min", type=int, default=None, help="Limite inclusivo de coluna XYZ para processamento em lotes.")
    parser.add_argument("--tile-x-max", type=int, default=None, help="Limite inclusivo de coluna XYZ para processamento em lotes.")
    args = parser.parse_args()

    columns = ["id_fazenda", "referencia_exibicao", "fonte_geometria", "area_ha", args.score_field, args.class_field, "solo_textura_dominante", "geometry"]
    farms = gpd.read_parquet(args.input, columns=columns).rename(columns={args.score_field: "tipo_solo", args.class_field: "classe_tipo_solo"}).to_crs("EPSG:4326")
    farms = farms[farms["tipo_solo"].notna()].copy()
    farms["score_class"] = farms["classe_tipo_solo"].map(SCORE_CLASS)
    farms = farms[farms["score_class"].notna()].copy()
    farms["score_class"] = farms["score_class"].astype(int)
    farms["geometry"] = farms.geometry.make_valid()
    farms = farms[farms.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].copy()
    index = farms.sindex
    built = 0

    for zoom in range(args.minzoom, args.maxzoom + 1):
        # Progressive disclosure: compact at regional view, near-original
        # boundaries when the user is examining a handful of properties.
        tolerance_m = {7: 80, 8: 35, 9: 12, 10: 4}.get(zoom, 4)
        for tile in mercantile.tiles(*farms.total_bounds, zooms=[zoom]):
            if args.tile_x_min is not None and tile.x < args.tile_x_min:
                continue
            if args.tile_x_max is not None and tile.x > args.tile_x_max:
                continue
            bounds = mercantile.bounds(tile)
            tile_box = box(bounds.west, bounds.south, bounds.east, bounds.north)
            positions = list(index.query(tile_box, predicate="intersects"))
            if not positions:
                continue
            subset = farms.iloc[positions].copy()
            subset["geometry"] = subset.geometry.intersection(tile_box)
            subset = subset[(~subset.geometry.is_empty) & subset.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].to_crs("EPSG:3857")
            subset["geometry"] = subset.geometry.simplify(tolerance_m, preserve_topology=True)
            subset = subset.to_crs("EPSG:4326")
            for category, part in subset.groupby("score_class"):
                features = [
                    {"geometry": mapping(row.geometry), "properties": {
                        "referencia_exibicao": row.referencia_exibicao if pd.notna(row.referencia_exibicao) else "Imóvel certificado",
                        "fonte_geometria": row.fonte_geometria,
                        "area_ha": float(row.area_ha),
                        "nota_mapa": round(float(row.tipo_solo), 1),
                        "classe_mapa": row.classe_tipo_solo,
                        "solo_textura_dominante": row.solo_textura_dominante if pd.notna(row.solo_textura_dominante) else "Sem dados",
                    }}
                    for row in part.itertuples(index=False)
                ]
                encoded = mapbox_vector_tile.encode({"name": "fazendas_solo", "features": features}, default_options={"quantize_bounds": (bounds.west, bounds.south, bounds.east, bounds.north)})
                destination = args.output / str(int(category)) / str(zoom) / str(tile.x)
                destination.mkdir(parents=True, exist_ok=True)
                (destination / f"{tile.y}.pbf").write_bytes(encoded)
                built += 1
    print(f"Tiles de solo por fazenda: {built:,}; imóveis com nota: {len(farms):,}")


if __name__ == "__main__":
    main()
