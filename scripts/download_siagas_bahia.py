"""Download the current public SIAGAS/SGB points clipped to Bahia.

SIAGAS points are evidence of local hydrogeological investigation.  They do
not establish the yield, quality, legal availability or outorga of a proposed
farm well.  The retained fields deliberately preserve those distinctions.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import geopandas as gpd
import requests


URL = "https://geoportal.sgb.gov.br/server/rest/services/Hosted/siagas_web/FeatureServer/1/query"
BAHIA_BBOX = "-46.75,-18.50,-37.20,-8.20"
FIELDS = ",".join((
    "idt_ponto", "str_municipio", "str_uf", "str_local_ponto", "str_nome_ponto",
    "str_natureza_ponto", "str_tipo_situacao", "str_uso_agua", "str_aquifero",
    "num_profundidade", "num_condutividade_eletrica", "nd", "ne",
    "num_vazao_especifica", "data_perfuracao", "data_cadastro", "data_instalacao",
))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=Path("data/raw/hidrica/siagas_bahia_pocos.geoparquet"))
    parser.add_argument("--page-size", type=int, default=2000)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    base = {
        "where": "1=1",
        "geometry": BAHIA_BBOX,
        "geometryType": "esriGeometryEnvelope",
        "spatialRel": "esriSpatialRelIntersects",
        "inSR": "4326",
        "outFields": FIELDS,
        "returnGeometry": "true",
        "outSR": "4326",
        "f": "geojson",
        "resultRecordCount": args.page_size,
        "orderByFields": "objectid ASC",
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
        print(f"SIAGAS: {len(features):,} pontos baixados")
        if len(batch) < args.page_size:
            break
        offset += len(batch)
        time.sleep(0.15)
    points = gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")
    # The service has a national extent; retain the explicit source state as
    # an additional guard against a bounding-box edge record from another UF.
    if "str_uf" in points:
        points = points[points.str_uf.fillna("").str.upper().eq("BA")].copy()
    points.to_parquet(args.output, index=False)
    print(f"Arquivo salvo: {args.output} ({len(points):,} pontos BA)")


if __name__ == "__main__":
    main()
