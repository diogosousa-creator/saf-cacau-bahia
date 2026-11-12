"""Build the small attribute-only lookup used by the property side panel."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd


ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"

DETAIL_COLUMNS = [
    "id_fazenda", "referencia_exibicao", "area_ha", "tipo_solo",
    "relevo_drenagem", "clima_irrigacao", "seguranca_hidrica",
    "risco_socioambiental", "logistica_ilheus", "custo_terra",
    "mao_obra_infra", "uso_solo",
]


def main() -> None:
    source = PROCESSED / "fazendas_georeferenciadas.geoparquet"
    target = PROCESSED / "fazendas_detalhe_lookup.parquet"
    farms = gpd.read_parquet(source, columns=[*DETAIL_COLUMNS, "geometry"])
    # EPSG:5880 keeps the representative point calculation metric and stable;
    # the returned coordinate is only a navigation target, never an analysis.
    points = farms.to_crs(5880).representative_point().to_crs(4326)
    lookup = farms[DETAIL_COLUMNS].copy()
    geographic = farms.to_crs(4326)
    bounds = geographic.bounds
    lookup["centroide_longitude"] = points.x.round(7)
    lookup["centroide_latitude"] = points.y.round(7)
    # Bounds are navigation metadata only. They preserve the true footprint's
    # complete extent so the map can fit a selected parcel without loading it.
    lookup["limite_oeste"] = bounds.minx.round(7)
    lookup["limite_sul"] = bounds.miny.round(7)
    lookup["limite_leste"] = bounds.maxx.round(7)
    lookup["limite_norte"] = bounds.maxy.round(7)
    lookup.drop_duplicates("id_fazenda", keep="first").to_parquet(target, index=False)
    print(f"Lookup de detalhe: {len(lookup):,} imóveis → {target}")


if __name__ == "__main__":
    main()
