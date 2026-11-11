"""Build lightweight vector tiles for the certified-farms WebGIS layer.

The analytical GeoParquet remains the source of truth.  These MVT files carry
only the tooltip fields and geometry needed by the browser, so the statewide
map never serializes all farm polygons in one Streamlit message.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import mapbox_vector_tile
import mercantile
import pandas as pd
from shapely.geometry import box, mapping


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("data/processed/fazendas_georeferenciadas.geoparquet"))
    parser.add_argument("--output", type=Path, default=Path("static/fazendas_tiles"))
    parser.add_argument("--minzoom", type=int, default=7)
    parser.add_argument("--maxzoom", type=int, default=9)
    args = parser.parse_args()

    columns = ["id_fazenda", "identificador_incra", "fonte_geometria", "area_ha", "geometry"]
    farms = gpd.read_parquet(args.input, columns=columns).to_crs("EPSG:4326")
    farms["geometry"] = farms.geometry.make_valid()
    farms = farms[farms.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].copy()
    spatial_index = farms.sindex
    args.output.mkdir(parents=True, exist_ok=True)
    built = 0

    for zoom in range(args.minzoom, args.maxzoom + 1):
        # Approx. half pixel in Web Mercator: preserve readable parcel shape
        # while avoiding excessive vertices at statewide and regional scales.
        tolerance_m = 78_271.5 / (2**zoom)
        for tile in mercantile.tiles(*farms.total_bounds, zooms=[zoom]):
            bounds = mercantile.bounds(tile)
            tile_box = box(bounds.west, bounds.south, bounds.east, bounds.north)
            positions = list(spatial_index.query(tile_box, predicate="intersects"))
            if not positions:
                continue
            subset = farms.iloc[positions].copy()
            subset["geometry"] = subset.geometry.intersection(tile_box)
            subset = subset[
                (~subset.geometry.is_empty)
                & subset.geometry.geom_type.isin(["Polygon", "MultiPolygon"])
            ].to_crs("EPSG:3857")
            subset["geometry"] = subset.geometry.simplify(tolerance_m, preserve_topology=True)
            subset = subset.to_crs("EPSG:4326")
            features = [
                {
                    "geometry": mapping(row.geometry),
                    "properties": {
                        "id_fazenda": row.id_fazenda,
                        "identificador_incra": row.identificador_incra if pd.notna(row.identificador_incra) else "Não informado",
                        "fonte_geometria": row.fonte_geometria,
                        "area_ha": float(row.area_ha),
                    },
                }
                for row in subset.itertuples(index=False)
                if row.geometry is not None and not row.geometry.is_empty
            ]
            encoded = mapbox_vector_tile.encode(
                {"name": "fazendas", "features": features},
                default_options={"quantize_bounds": (bounds.west, bounds.south, bounds.east, bounds.north)},
            )
            destination = args.output / str(zoom) / str(tile.x)
            destination.mkdir(parents=True, exist_ok=True)
            (destination / f"{tile.y}.pbf").write_bytes(encoded)
            built += 1
    print(f"Tiles criados: {built:,}; zooms: {args.minzoom}–{args.maxzoom}")


if __name__ == "__main__":
    main()
