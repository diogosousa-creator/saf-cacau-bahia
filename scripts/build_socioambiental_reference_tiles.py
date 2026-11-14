"""Build lightweight reference MVTs for the individual socio-environment map."""
from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import mapbox_vector_tile
import mercantile
from shapely.geometry import box, mapping


ROOT = Path(__file__).resolve().parents[1]


def write_layer(name: str, frame: gpd.GeoDataFrame, output: Path, minzoom: int, maxzoom: int) -> int:
    if frame.empty:
        return 0
    frame = frame.to_crs("EPSG:4326")
    index = frame.sindex
    made = 0
    for zoom in range(minzoom, maxzoom + 1):
        tolerance_m = 78_271.5 / (2 ** zoom)
        for tile in mercantile.tiles(*frame.total_bounds, zooms=[zoom]):
            bounds = mercantile.bounds(tile)
            clip = box(bounds.west, bounds.south, bounds.east, bounds.north)
            pos = list(index.query(clip, predicate="intersects"))
            if not pos:
                continue
            subset = frame.iloc[pos].copy()
            subset["geometry"] = subset.geometry.intersection(clip)
            subset = subset[subset.geometry.notna() & ~subset.geometry.is_empty].to_crs("EPSG:3857")
            subset["geometry"] = subset.geometry.simplify(tolerance_m, preserve_topology=True)
            subset = subset.to_crs("EPSG:4326")
            # Clipping or topology repair can yield GeometryCollections, which
            # are not supported by the MVT encoder.  Retain their polygonal
            # components: these are the only relevant components for PRODES,
            # embargoes, UCs and TIs.
            subset["geometry"] = subset.geometry.make_valid()
            subset = subset.explode(index_parts=False)
            subset = subset[subset.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
            features = [{"geometry": mapping(row.geometry), "properties": {"referencia": name}} for row in subset.itertuples(index=False)]
            if not features:
                continue
            dest = output / name / str(zoom) / str(tile.x)
            dest.mkdir(parents=True, exist_ok=True)
            encoded = mapbox_vector_tile.encode({"name": name, "features": features}, default_options={"quantize_bounds": (bounds.west, bounds.south, bounds.east, bounds.north)})
            (dest / f"{tile.y}.pbf").write_bytes(encoded)
            made += 1
    return made


def prodes_from_raster(path: Path) -> gpd.GeoDataFrame:
    """Polygonize the 240 m dominant-period raster (visual reference only)."""
    import rasterio
    from rasterio.features import shapes, sieve
    from shapely.geometry import shape

    with rasterio.open(path) as src:
        data = src.read(1)
        # Remove isolated single cells: they are not legible at the map scale
        # and the analytical areas are computed separately from the 30 m raster.
        data = sieve(data, size=2, connectivity=8)
        periods = {1: "pre2008", 2: "2009_2014", 3: "2015_2020", 4: "post2020"}
        records = [
            {"periodo": periods[int(value)], "geometry": shape(geom)}
            for geom, value in shapes(data, mask=data > 0, transform=src.transform, connectivity=8)
            if int(value) in periods
        ]
        return gpd.GeoDataFrame(records, geometry="geometry", crs=src.crs)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path, default=ROOT / "data" / "raw" / "socioambiental")
    parser.add_argument("--output", type=Path, default=ROOT / "static" / "socioambiental_referencias_tiles_v1")
    parser.add_argument("--minzoom", type=int, default=7)
    parser.add_argument("--maxzoom", type=int, default=9)
    parser.add_argument("--prodes-raster", type=Path, default=ROOT / "data" / "interim" / "prodes_periodo_bahia_240m.tif",
                        help="Raster de período dominante (240 m) gerado por build_prodes_raster_stats.py.")
    parser.add_argument("--only-prodes", action="store_true")
    parser.add_argument("--no-prodes", action="store_true")
    args = parser.parse_args()
    layers = {}
    if not args.no_prodes:
        prodes = prodes_from_raster(args.prodes_raster)
        layers.update({
            "prodes_ate_2008": prodes[prodes.periodo.eq("pre2008")],
            "prodes_2009_2014": prodes[prodes.periodo.eq("2009_2014")],
            "prodes_2015_2020": prodes[prodes.periodo.eq("2015_2020")],
            "prodes_pos_2020": prodes[prodes.periodo.eq("post2020")],
        })
    if not args.only_prodes:
        layers.update({
        "embargos_ibama": gpd.read_parquet(args.raw / "embargos_ibama_poligonal_vigente_bahia.geoparquet"),
        "unidades_conservacao": gpd.read_parquet(args.raw / "unidades_conservacao_cnuc_bahia.geoparquet"),
        "terras_indigenas": gpd.read_parquet(args.raw / "terras_indigenas_funai_bahia.geoparquet"),
        })
    for name, frame in layers.items():
        print(f"{name}: {write_layer(name, frame, args.output, args.minzoom, args.maxzoom):,} tiles", flush=True)


if __name__ == "__main__":
    main()
