"""Download the official BHO6 drainage network clipped to Bahia.

The download is deliberately constrained to Strahler order >= 4.  MapBiomas
Água covers 30-m surface-water evidence, while BHO6 here supplies the
topologically-consistent regional drainage context without publishing hundreds
of thousands of tiny headwater segments in the WebGIS pipeline.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import geopandas as gpd
import requests


URL = (
    "https://portal1.snirh.gov.br/server/rest/services/Hosted/"
    "main_geoft_bho6_trecho_drenagem/FeatureServer/0/query"
)
BAHIA_BBOX = "-46.75,-18.50,-37.20,-8.20"
FIELDS = ",".join((
    "drn_pk", "cotrecho", "cocursodag", "cobacia", "nucomptrec",
    "nuareacont", "nuareamont", "nogenerico", "noespecif",
    "nuordemcda", "nustrahler", "dedominial", "dsversao",
))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=Path("data/raw/hidrica/bho6_bahia_ordem4mais.geoparquet"))
    parser.add_argument("--min-strahler", type=int, default=4)
    parser.add_argument("--page-size", type=int, default=2000)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)

    base = {
        "where": f"nustrahler >= {args.min_strahler}",
        "geometry": BAHIA_BBOX,
        "geometryType": "esriGeometryEnvelope",
        "spatialRel": "esriSpatialRelIntersects",
        "inSR": "4326",
        "outFields": FIELDS,
        "returnGeometry": "true",
        "outSR": "4326",
        "f": "geojson",
        "resultRecordCount": args.page_size,
        "orderByFields": "fid ASC",
    }
    session = requests.Session()
    features: list[dict] = []
    offset = 0
    while True:
        response = session.get(URL, params={**base, "resultOffset": offset}, timeout=120)
        response.raise_for_status()
        payload = response.json()
        if "error" in payload:
            raise RuntimeError(payload["error"])
        batch = payload.get("features", [])
        features.extend(batch)
        print(f"BHO6: {len(features):,} trechos baixados")
        if len(batch) < args.page_size:
            break
        offset += len(batch)
        time.sleep(0.15)

    network = gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")
    network.to_parquet(args.output, index=False)
    print(f"Arquivo salvo: {args.output} ({len(network):,} trechos)")


if __name__ == "__main__":
    main()
