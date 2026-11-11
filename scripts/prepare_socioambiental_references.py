"""Clip official Ibama, CNUC and Funai vectors to Bahia for the WebGIS.

The public Ibama download explicitly contains only currently valid embargo
terms.  Its official vector release is polygonal; the point output is kept as
an empty, schema-stable layer rather than inventing points or buffers.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from zipfile import ZipFile

import geopandas as gpd
import pyogrio


ROOT = Path(__file__).resolve().parents[1]
DOWNLOADS = Path.home() / "Downloads"
OUT = ROOT / "data" / "raw" / "socioambiental"
BOUNDARY = ROOT / "data" / "processed" / "bahia_boundary.geojson"
TARGET_CRS = "EPSG:4674"


def vsi(zip_path: Path, suffix: str, contains: str | None = None) -> str:
    with ZipFile(zip_path) as archive:
        members = [name for name in archive.namelist() if name.lower().endswith(suffix)]
        if contains:
            members = [name for name in members if contains.lower() in name.lower()]
        member = members[0]
    return f"/vsizip/{zip_path}/{member}"


def clean(frame: gpd.GeoDataFrame, boundary: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    frame = frame[frame.geometry.notna() & ~frame.geometry.is_empty].copy().to_crs(TARGET_CRS)
    return gpd.clip(frame, boundary)


def read_zip_shape(path: Path, boundary: gpd.GeoDataFrame, columns=None, contains: str | None = None) -> gpd.GeoDataFrame:
    return clean(pyogrio.read_dataframe(vsi(path, ".shp", contains), bbox=tuple(boundary.total_bounds), columns=columns), boundary)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--downloads", type=Path, default=DOWNLOADS)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--boundary", type=Path, default=BOUNDARY)
    args = parser.parse_args()
    boundary = gpd.read_file(args.boundary).to_crs(TARGET_CRS)
    args.out.mkdir(parents=True, exist_ok=True)

    embargo_path = args.downloads / "adm_embargos_ibama_a.zip"
    cnuc_path = args.downloads / "cnuc_2026_07.zip"
    funai_path = args.downloads / "tis_poligonais.zip"
    for source in (embargo_path, cnuc_path, funai_path):
        if not source.exists():
            raise FileNotFoundError(source)

    embargos = read_zip_shape(embargo_path, boundary)
    embargos["fonte"] = "Ibama/AIE – embargos vigentes"
    embargos["situacao_fonte"] = "vigente"
    embargos.to_parquet(args.out / "embargos_ibama_poligonal_vigente_bahia.geoparquet", index=False)

    # The official vector package is polygon-only.  Preserve the input schema
    # for the analytical rule without fabricating a coordinate buffer.
    empty_points = gpd.GeoDataFrame({"fonte": [], "situacao_fonte": []}, geometry=[], crs=TARGET_CRS)
    empty_points.to_parquet(args.out / "embargos_ibama_pontual_bahia.geoparquet", index=False)

    # The package also has a centroids layer; the named dataset below is the
    # authoritative polygon geometry, required for the 50% territorial rule.
    cnuc = read_zip_shape(cnuc_path, boundary, contains="cnuc_26_07_31")
    cnuc["fonte"] = "CNUC/MMA 2026_07"
    cnuc.to_parquet(args.out / "unidades_conservacao_cnuc_bahia.geoparquet", index=False)

    tis = read_zip_shape(funai_path, boundary)
    tis["fonte"] = "Funai – terras indígenas"
    tis.to_parquet(args.out / "terras_indigenas_funai_bahia.geoparquet", index=False)

    for name, frame in (("embargos_poligonos", embargos), ("uc", cnuc), ("ti", tis)):
        print(f"{name}: {len(frame):,} feições; {frame.to_crs('EPSG:5880').geometry.area.sum() / 1e4:,.1f} ha", flush=True)


if __name__ == "__main__":
    main()
