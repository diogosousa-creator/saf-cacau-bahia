"""Download official CNARH/SNIRH surface-water outorgas for Bahia.

The source exposes separate state and federal layers.  They are retained raw
with their publication date; eligibility is assessed later, never inferred from
the BHO6 line network alone.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

import geopandas as gpd
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "raw" / "hidrica" / "outorgas_superficiais_bahia_cnarh.geoparquet"
BASE = "https://www.snirh.gov.br/arcgis/rest/services/IG/Outorgas_CNARH/MapServer"
LAYERS = {"estadual": 1, "federal": 3}
PAGE_SIZE = 2_000


def get_page(layer: int, offset: int) -> dict:
    params = {
        "where": "ING_SG_UFMUNICIPIO='BA'",
        "outFields": "*",
        "returnGeometry": "true",
        "resultOffset": offset,
        "resultRecordCount": PAGE_SIZE,
        "outSR": "4326",
        "f": "geojson",
    }
    with urlopen(f"{BASE}/{layer}/query?{urlencode(params)}", timeout=120) as response:
        return json.load(response)


def download_layer(name: str, layer: int) -> gpd.GeoDataFrame:
    records: list[dict] = []
    offset = 0
    while True:
        page = get_page(layer, offset)
        batch = page.get("features", [])
        records.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        offset += len(batch)
    gdf = gpd.GeoDataFrame.from_features(records, crs="EPSG:4326")
    gdf["fonte_outorga"] = f"CNARH/SNIRH — {name} superficial"
    return gdf


def main() -> None:
    frames = [download_layer(name, layer) for name, layer in LAYERS.items()]
    data = pd.concat(frames, ignore_index=True)
    data = gpd.GeoDataFrame(data, geometry="geometry", crs="EPSG:4326")
    data["data_download"] = str(date.today())
    data = data[data.geometry.notna() & ~data.geometry.is_empty].copy()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    data.to_parquet(OUT, index=False)
    print(f"Outorgas superficiais BA: {len(data):,}; arquivo: {OUT}")
    for field in ("OUTORGA_VALIDA", "TSP_DS", "TSF_DS", "TIN_DS", "TFN_DS"):
        if field in data:
            print(field, data[field].fillna("<vazio>").astype(str).value_counts().head(20).to_dict())


if __name__ == "__main__":
    main()
