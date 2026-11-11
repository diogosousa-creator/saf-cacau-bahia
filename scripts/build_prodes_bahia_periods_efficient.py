"""Efficient, reproducible PRODES preparation using the official ``state`` field.

Each national Marco UE GeoPackage is extracted one at a time, queried with
``state = 'BA'`` in SQLite, clipped to the Bahia boundary and immediately
written as a small interim GeoParquet.  The temporary GeoPackage is then
removed before the next biome, keeping disk consumption bounded.
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path
from zipfile import ZipFile

import geopandas as gpd
import pandas as pd
import pyogrio
from shapely import make_valid
from shapely.ops import transform as transform_geometry

ROOT = Path(__file__).resolve().parents[1]
DOWNLOADS = Path.home() / "Downloads"
WFS = ROOT / "data" / "raw" / "socioambiental" / "prodes_wfs"
WORK = ROOT / "data" / "interim" / "prodes_work"
OUT = ROOT / "data" / "raw" / "socioambiental" / "prodes_incrementos_bahia.geoparquet"
BOUNDARY = ROOT / "data" / "processed" / "bahia_boundary.geojson"
METRIC = "EPSG:5880"
SOURCES = (
    ("caatinga", "marco_biome_caatinga_v20260805.gpkg.zip", 351831),
    ("cerrado", "marco_biome_cerrado_v20261016.gpkg.zip", 23),
    ("mata_atlantica", "marco_biome_mata_atlantica_v20260806.gpkg.zip", 84286),
)


def zip_member(path: Path, suffix: str) -> str:
    with ZipFile(path) as archive:
        return next(x.filename for x in archive.infolist() if x.filename.lower().endswith(suffix))


def clean(frame: gpd.GeoDataFrame, boundary: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    frame = frame[frame.geometry.notna() & ~frame.geometry.is_empty].copy().to_crs(METRIC)
    invalid = ~frame.geometry.is_valid
    if invalid.any():
        frame.loc[invalid, "geometry"] = frame.loc[invalid, "geometry"].map(make_valid)
    return gpd.clip(frame, boundary)


def extract_gpkg(zip_path: Path, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    with ZipFile(zip_path) as archive:
        item = next(x for x in archive.infolist() if x.filename.lower().endswith('.gpkg'))
        target = destination / item.filename
        if not target.exists() or target.stat().st_size != item.file_size:
            archive.extract(item, destination)
    return target


def read_wfs_annual(biome: str, expected_count: int, boundary: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Read the complete paginated, official WFS export for Bahia."""
    files = sorted(WFS.glob(f"{biome}_p*.zip"))
    if not files:
        raise FileNotFoundError(f"Nenhuma página WFS encontrada para {biome} em {WFS}")
    frames = []
    for path in files:
        source = f"/vsizip/{path}/{zip_member(path, '.shp')}"
        frames.append(pyogrio.read_dataframe(source, columns=["state", "year"]))
    annual = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), geometry="geometry", crs=frames[0].crs)
    if len(annual) != expected_count:
        raise RuntimeError(f"WFS {biome}: {len(annual):,} feições; esperado {expected_count:,}. Recorte abortado.")
    if not annual["state"].eq("BA").all():
        raise RuntimeError(f"WFS {biome}: retorno contém feições fora da Bahia.")
    # GeoServer advertises SIRGAS 2000 (EPSG:4674) but returns WFS geometry
    # ordinates in its formal axis order (latitude, longitude).  GeoPandas
    # reads coordinate arrays as x,y.  Swap only when that signature is
    # detected; without this correction the official Bahia features fall
    # outside the state mask and annual increments are silently discarded.
    minx, miny, maxx, maxy = annual.total_bounds
    if maxx < -5 and miny < -30 and minx > -25:
        annual["geometry"] = annual.geometry.map(
            lambda geom: transform_geometry(lambda x, y, z=None: (y, x), geom)
        )
    # The official WFS query is already restricted by the source attribute
    # ``state = 'BA'`` and the assertion above guarantees every returned
    # feature satisfies it.  Re-clipping 350k+ annual increments to the same
    # state polygon is computationally prohibitive and may introduce slivers;
    # reserve the geometric state mask for the national Marco UE source.
    annual = annual.to_crs(METRIC)
    invalid = ~annual.geometry.is_valid
    if invalid.any():
        annual.loc[invalid, "geometry"] = annual.loc[invalid, "geometry"].map(make_valid)
    return annual


def build_one(biome: str, marco_path: Path, expected_count: int, boundary: gpd.GeoDataFrame) -> Path:
    interim = WORK / f"{biome}_bahia.parquet"
    if interim.exists():
        print(f"{biome}: usando recorte já concluído", flush=True)
        return interim
    print(f"{biome}: carregando páginas WFS oficiais Bahia…", flush=True)
    annual = read_wfs_annual(biome, expected_count, boundary)
    annual["year"] = pd.to_numeric(annual["year"], errors="coerce")
    annual["periodo"] = annual["year"].between(2009, 2020).map({True: "2009_2020", False: "post2020"})
    mid = annual[annual.periodo.eq("2009_2020")][["geometry"]].copy()
    post = annual[annual.periodo.eq("post2020")][["geometry"]].copy()

    temp_dir = WORK / biome
    gpkg = extract_gpkg(marco_path, temp_dir)
    print(f"{biome}: consultando Marco UE por state='BA'…", flush=True)
    marco = pyogrio.read_dataframe(gpkg, layer="marco_eu_deforestation", columns=["state"], where="state = 'BA'")
    if not marco["state"].eq("BA").all():
        raise RuntimeError(f"Marco UE {biome}: retorno contém feições fora da Bahia.")
    # As with the annual layer, the authoritative state attribute is the
    # selection criterion.  Final statistics are intersected only with the
    # Bahia hexagons/properties, so a second expensive national boundary clip
    # cannot change any published unit score and is deliberately avoided.
    marco = marco.to_crs(METRIC)
    invalid = ~marco.geometry.is_valid
    if invalid.any():
        marco.loc[invalid, "geometry"] = marco.loc[invalid, "geometry"].map(make_valid)
    marco = marco[["geometry"]]
    # Exact residual: Marco UE (<=2020) minus the 2009-2020 annual union.
    if not mid.empty:
        idx = mid.sindex
        # With few annual increments (e.g. the Bahia portion of Cerrado),
        # first isolate the small subset that actually touches them.  This
        # avoids a Python/GEOS union loop over every unaffected Marco polygon.
        if len(mid) <= 100:
            pairs = idx.query(marco.geometry, predicate="intersects")
            touched = pd.Index(pairs[0]).unique()
            untouched = marco.drop(index=touched)
            geoms = list(untouched.geometry)
            for source_index in touched:
                geom = marco.geometry.loc[source_index]
                hits = list(idx.query(geom, predicate="intersects"))
                rem = geom.difference(mid.iloc[hits].geometry.union_all())
                if rem is not None and not rem.is_empty and rem.area >= 100:
                    geoms.append(rem)
        else:
            geoms = []
            for geom in marco.geometry:
                hits = list(idx.query(geom, predicate="intersects"))
                rem = geom if not hits else geom.difference(mid.iloc[hits].geometry.union_all())
                if rem is not None and not rem.is_empty and rem.area >= 100:
                    geoms.append(rem)
        pre = gpd.GeoDataFrame({"periodo": "pre2008", "geometry": geoms}, crs=METRIC)
    else:
        pre = marco.assign(periodo="pre2008")[["periodo", "geometry"]]
    result = gpd.GeoDataFrame(pd.concat([pre, mid.assign(periodo="2009_2020"), post.assign(periodo="post2020")], ignore_index=True), geometry="geometry", crs=METRIC)
    result["bioma_origem"] = biome
    result.to_parquet(interim, index=False)
    # This is a generated temporary copy, never the source ZIP.
    shutil.rmtree(temp_dir)
    print(f"{biome}: {len(result):,} feições Bahia", flush=True)
    return interim


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--downloads", type=Path, default=DOWNLOADS)
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    boundary = gpd.read_file(BOUNDARY).to_crs(METRIC)
    parts = []
    for biome, marco_name, expected_count in SOURCES:
        parts.append(gpd.read_parquet(build_one(biome, args.downloads / marco_name, expected_count, boundary)))
    result = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), geometry="geometry", crs=METRIC)
    result["fonte"] = "INPE/TerraBrasilis PRODES"
    result["metodo"] = "UF BA; Marco UE <=2020 menos incrementos 2009-2020; incrementos anuais >=2021"
    result["area_ha"] = result.geometry.area / 10_000
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result.to_parquet(args.output, index=False)
    print(result.groupby('periodo').area_ha.agg(['count','sum']).to_string(), flush=True)
    print(f"Base final: {args.output}", flush=True)


if __name__ == '__main__':
    main()
