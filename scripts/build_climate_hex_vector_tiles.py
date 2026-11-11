"""Build lightweight thematic MVTs for the climate and irrigation score."""

from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import mapbox_vector_tile
import mercantile
import pandas as pd
from shapely.geometry import box, mapping


def score_class(value: float | None) -> int:
    if value is None or pd.isna(value):
        return 0
    if value < 35:
        return 1
    if value < 50:
        return 2
    if value < 65:
        return 3
    if value < 80:
        return 4
    return 5


def rounded(value: object, decimals: int = 1) -> float | None:
    return None if value is None or pd.isna(value) else round(float(value), decimals)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hex", type=Path, default=Path("data/processed/bahia_hex_5km.geojson"))
    parser.add_argument("--assessments", type=Path, default=Path("data/processed/assessments.parquet"))
    parser.add_argument("--output", type=Path, default=Path("static/hex_climate_tiles_v1"))
    parser.add_argument("--minzoom", type=int, default=4)
    parser.add_argument("--maxzoom", type=int, default=7)
    args = parser.parse_args()

    climate_fields = [
        "id", "clima_irrigacao", "clima_tmean_c", "clima_tmax_p95_c",
        "clima_demanda_irrigacao_p80_mm", "clima_dryseq_months", "clima_pe_mm",
    ]
    hexes = gpd.read_file(args.hex).to_crs("EPSG:4326")
    stats = pd.read_parquet(args.assessments, columns=climate_fields)
    hexes = hexes.merge(stats, left_on="id_hex", right_on="id", how="left", validate="one_to_one")
    hexes["score_class"] = hexes["clima_irrigacao"].map(score_class)
    index = hexes.sindex
    built = 0

    for zoom in range(args.minzoom, args.maxzoom + 1):
        tolerance_m = 78_271.5 / (2**zoom)
        for tile in mercantile.tiles(*hexes.total_bounds, zooms=[zoom]):
            bounds = mercantile.bounds(tile)
            tile_box = box(bounds.west, bounds.south, bounds.east, bounds.north)
            positions = list(index.query(tile_box, predicate="intersects"))
            if not positions:
                continue
            subset = hexes.iloc[positions].copy()
            subset["geometry"] = subset.geometry.intersection(tile_box)
            subset = subset[~subset.geometry.is_empty].to_crs("EPSG:3857")
            subset["geometry"] = subset.geometry.simplify(tolerance_m, preserve_topology=True)
            subset = subset.to_crs("EPSG:4326")
            for category in range(1, 6):
                features = []
                for row in subset[subset["score_class"] == category].itertuples(index=False):
                    features.append({
                        "geometry": mapping(row.geometry),
                        "properties": {
                            "referencia_exibicao": "Análise territorial",
                            "nota_mapa": rounded(row.clima_irrigacao),
                            "classe_mapa": ("Muito baixa", "Baixa", "Média", "Alta", "Muito alta")[category - 1],
                            "clima_tmean_c": rounded(row.clima_tmean_c, 2),
                            "clima_tmax_p95_c": rounded(row.clima_tmax_p95_c, 2),
                            "clima_demanda_irrigacao_p80_mm": rounded(row.clima_demanda_irrigacao_p80_mm),
                            "clima_dryseq_months": rounded(row.clima_dryseq_months, 1),
                            "clima_pe_mm": rounded(row.clima_pe_mm),
                        },
                    })
                if not features:
                    continue
                encoded = mapbox_vector_tile.encode(
                    {"name": "clima_hex", "features": features},
                    default_options={"quantize_bounds": (bounds.west, bounds.south, bounds.east, bounds.north)},
                )
                destination = args.output / "score" / str(category) / str(zoom) / str(tile.x)
                destination.mkdir(parents=True, exist_ok=True)
                (destination / f"{tile.y}.pbf").write_bytes(encoded)
                built += 1
    print(f"Tiles climáticos criados: {built:,}")


if __name__ == "__main__":
    main()
