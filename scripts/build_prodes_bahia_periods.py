"""Create the compact, non-overlapping PRODES temporal layer for Bahia.

Inputs are the official TerraBrasilis downloads for Caatinga, Cerrado and
Mata Atlantica.  The Marco UE layer records deforestation up to 31-Dec-2020;
therefore historical deforestation is recovered as Marco UE minus the union
of the 2009--2020 annual increments.  This avoids the 2001--2003 coverage gap
that would be introduced by relying on older accumulated products alone.

The source ZIPs are read in place through GDAL's /vsizip/ driver.  Only the
Bahia subset is persisted, which keeps the operational WebGIS pipeline light.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio
from shapely import make_valid


ROOT = Path(__file__).resolve().parents[1]
DOWNLOADS = Path.home() / "Downloads"
OUT = ROOT / "data" / "raw" / "socioambiental" / "prodes_incrementos_bahia.geoparquet"
BOUNDARY = ROOT / "data" / "processed" / "bahia_boundary.geojson"
SOURCE_CRS = "EPSG:4674"
METRIC_CRS = "EPSG:5880"
MIN_AREA_M2 = 100.0  # below PRODES mapping-unit scale; removes topology slivers only

SOURCES = (
    ("caatinga", "yearly_deforestation_biome_caatinga_v20260805.zip", "marco_biome_caatinga_v20260805.gpkg.zip"),
    ("cerrado", "yearly_deforestation_biome_cerrado_v20261016.zip", "marco_biome_cerrado_v20261016.gpkg.zip"),
    ("mata_atlantica", "yearly_deforestation_biome_mata_atlantica_v20260806.zip", "marco_biome_mata_atlantica_v20260806.gpkg.zip"),
)


def _vsi_member(zip_path: Path, suffix: str) -> str:
    from zipfile import ZipFile
    with ZipFile(zip_path) as archive:
        member = next(name for name in archive.namelist() if name.lower().endswith(suffix))
    return f"/vsizip/{zip_path}/{member}"


def _clean(frame: gpd.GeoDataFrame, boundary: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    if frame.empty:
        return frame
    frame = frame[frame.geometry.notna() & ~frame.geometry.is_empty].copy()
    frame = frame.to_crs(METRIC_CRS)
    # ``make_valid`` is expensive on national-scale PRODES archives.  Apply
    # it only to the exceptional invalid feature instead of every polygon.
    invalid = ~frame.geometry.is_valid
    if invalid.any():
        frame.loc[invalid, "geometry"] = frame.loc[invalid, "geometry"].map(make_valid)
    # Precise clip after the source-side bbox selection.
    frame = gpd.clip(frame, boundary.to_crs(METRIC_CRS))
    frame = frame[frame.geometry.notna() & ~frame.geometry.is_empty].copy()
    frame = frame[frame.geometry.area >= MIN_AREA_M2].copy()
    return frame


def _read_annual(path: Path, boundary: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    source = _vsi_member(path, ".shp")
    bbox = tuple(boundary.to_crs(SOURCE_CRS).total_bounds)
    # We only need the two newer annual intervals; annual records before 2009
    # are represented by the Marco UE residual after the 2009--2020 subtraction.
    raw = pyogrio.read_dataframe(source, columns=["year"], where="year >= 2009", bbox=bbox)
    raw = _clean(raw, boundary)
    raw["year"] = pd.to_numeric(raw["year"], errors="coerce")
    raw = raw[raw["year"].notna()].copy()
    raw["periodo"] = raw["year"].between(2009, 2020).map({True: "2009_2020", False: "post2020"})
    return raw[["periodo", "geometry"]]


def _read_marco(path: Path, boundary: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    source = _vsi_member(path, ".gpkg")
    layers = [name for name, _ in pyogrio.list_layers(source)]
    preferred = next((layer for layer in layers if layer == "marco_eu_deforestation"), None)
    if preferred is None:
        raise RuntimeError(f"Camada marco_eu_deforestation não encontrada em {path.name}: {layers}")
    bbox = tuple(boundary.to_crs(SOURCE_CRS).total_bounds)
    return _clean(pyogrio.read_dataframe(source, layer=preferred, bbox=bbox), boundary)[["geometry"]]


def _subtract_mid_from_marco(marco: gpd.GeoDataFrame, mid: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Return A≤2008 exactly as Marco UE <=2020 minus A2009--2020.

    Work per source geometry with the spatial index.  It avoids a statewide
    dissolve and therefore keeps both memory use and topology artefacts low.
    """
    if marco.empty:
        return marco
    if mid.empty:
        out = marco.copy()
        out["periodo"] = "pre2008"
        return out[["periodo", "geometry"]]
    index = mid.sindex
    rows = []
    for geom in marco.geometry:
        hits = list(index.query(geom, predicate="intersects"))
        remainder = geom if not hits else geom.difference(mid.iloc[hits].geometry.union_all())
        if remainder is not None and not remainder.is_empty and remainder.area >= MIN_AREA_M2:
            rows.append(remainder)
    return gpd.GeoDataFrame({"periodo": "pre2008", "geometry": rows}, crs=METRIC_CRS)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--downloads", type=Path, default=DOWNLOADS)
    parser.add_argument("--boundary", type=Path, default=BOUNDARY)
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()

    boundary = gpd.read_file(args.boundary).to_crs(METRIC_CRS)
    all_periods: list[gpd.GeoDataFrame] = []
    manifest: list[str] = []
    for biome, annual_name, marco_name in SOURCES:
        annual_path = args.downloads / annual_name
        marco_path = args.downloads / marco_name
        for path in (annual_path, marco_path):
            if not path.exists():
                raise FileNotFoundError(f"Download PRODES ausente: {path}")
        print(f"Lendo {biome}: incrementos anuais…", flush=True)
        annual = _read_annual(annual_path, boundary)
        mid = annual[annual["periodo"].eq("2009_2020")].copy()
        post = annual[annual["periodo"].eq("post2020")].copy()
        print(f"Lendo {biome}: Marco UE…", flush=True)
        marco = _read_marco(marco_path, boundary)
        pre = _subtract_mid_from_marco(marco, mid)
        all_periods.extend([pre, mid, post])
        manifest.extend([annual_name, marco_name])

    result = pd.concat(all_periods, ignore_index=True)
    result = gpd.GeoDataFrame(result, geometry="geometry", crs=METRIC_CRS)
    result = result[result.geometry.notna() & ~result.geometry.is_empty].copy()
    result["fonte"] = "INPE/TerraBrasilis PRODES"
    result["metodo"] = "Marco UE ate 2020 menos incrementos 2009-2020; incrementos anuais >=2021"
    result["area_ha"] = result.geometry.area / 10_000
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result.to_parquet(args.output, index=False)
    summary = result.groupby("periodo").area_ha.agg(["count", "sum"])
    print(summary.to_string(), flush=True)
    print(f"Escrito: {args.output} ({args.output.stat().st_size / 1024**2:.1f} MB)", flush=True)
    print("Fontes: " + "; ".join(manifest), flush=True)


if __name__ == "__main__":
    main()
