"""Build display-only tiles for the six raw climate components.

The rasters and 30-year time series remain outside the WebGIS.  Each MVT holds
the exact zonal statistic already stored in ``assessments.parquet`` for a 5 km
hexagon, grouped only to make browser rendering light.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import mapbox_vector_tile
import mercantile
import pandas as pd
from shapely.geometry import box, mapping


ROOT = Path(__file__).resolve().parents[1]
METRICS = {
    "precipitacao_anual": ("clima_p_mm", [600, 800, 1_000, 1_300]),
    "precipitacao_efetiva": ("clima_pe_mm", [450, 600, 750, 900]),
    "sequencia_seca": ("clima_dryseq_months", [3, 6, 8, 10]),
    "temperatura_media": ("clima_tmean_c", [21, 23, 25, 27]),
    "tmax_p95": ("clima_tmax_p95_c", [30, 32, 34, 36]),
    "demanda_p80": ("clima_demanda_irrigacao_p80_mm", [700, 1_100, 1_450, 1_800]),
}


def category(value: object, breaks: list[float]) -> int:
    if value is None or pd.isna(value):
        return 0
    for index, limit in enumerate(breaks, start=1):
        if float(value) <= limit:
            return index
    return 5


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metric", choices=sorted(METRICS), help="Gera somente uma referência climática")
    args = parser.parse_args()
    hexes = gpd.read_file(ROOT / "data" / "processed" / "bahia_hex_5km.geojson").to_crs("EPSG:4326")
    chosen = {args.metric: METRICS[args.metric]} if args.metric else METRICS
    fields = ["id", *[field for field, _ in chosen.values()]]
    # Attributes for the tooltip are only required when all metrics are built
    # together; a single-source layer remains compact and self-descriptive.
    stats = pd.read_parquet(ROOT / "data" / "processed" / "assessments.parquet", columns=fields)
    frame = hexes.merge(stats, left_on="id_hex", right_on="id", how="left", validate="one_to_one")
    index = frame.sindex
    output = ROOT / "static" / "hex_climate_referencias_tiles_v1"
    built = 0
    # The reference statistics are aggregated to 5 km hexagons; z4–z6 is
    # their honest display scale.  The score map remains available through z7.
    for zoom in range(4, 7):
        tolerance_m = 78_271.5 / (2 ** zoom)
        for tile in mercantile.tiles(*frame.total_bounds, zooms=[zoom]):
            bounds = mercantile.bounds(tile)
            tile_box = box(bounds.west, bounds.south, bounds.east, bounds.north)
            positions = list(index.query(tile_box, predicate="intersects"))
            if not positions:
                continue
            subset = frame.iloc[positions].copy()
            subset["geometry"] = subset.geometry.intersection(tile_box)
            subset = subset[subset.geometry.notna() & ~subset.geometry.is_empty].to_crs("EPSG:3857")
            subset["geometry"] = subset.geometry.simplify(tolerance_m, preserve_topology=True)
            subset = subset.to_crs("EPSG:4326")
            for metric, (field, breaks) in chosen.items():
                classes = subset[field].map(lambda value: category(value, breaks))
                for klass in range(1, 6):
                    features = []
                    for row in subset[classes.eq(klass)].itertuples(index=False):
                        props = {
                            "referencia_exibicao": "Análise territorial",
                            "valor": round(float(getattr(row, field)), 2),
                            "campo": field,
                        }
                        features.append({"geometry": mapping(row.geometry), "properties": props})
                    if not features:
                        continue
                    target = output / metric / str(klass) / str(zoom) / str(tile.x)
                    target.mkdir(parents=True, exist_ok=True)
                    encoded = mapbox_vector_tile.encode(
                        {"name": metric, "features": features},
                        default_options={"quantize_bounds": (bounds.west, bounds.south, bounds.east, bounds.north)},
                    )
                    (target / f"{tile.y}.pbf").write_bytes(encoded)
                    built += 1
    print(f"Tiles climáticos de referência: {built:,}")


if __name__ == "__main__":
    main()
