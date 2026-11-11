"""Vínculo espacial hexágono/imóvel → município para o ranking do WebGIS.

Grava somente relações geométricas, que não dependem dos pesos da barra
lateral.  A nota de cada município é recalculada no aplicativo, a cada mudança
de peso, a partir das notas dos hexágonos e dos imóveis.

Saídas (``data/processed``)
--------------------------
* ``municipios_ranking_base.parquet``  — código, nome, área, retângulo e
  contorno simplificado (GeoJSON) de cada município, para o enquadramento.
* ``municipios_hex_pesos.parquet``     — área (ha) de cada hexágono dentro de
  cada município; pesos da média territorial.
* ``municipios_imoveis.parquet``       — município de cada parcela de
  apresentação: o que contém a maior parte da sua área.
"""
from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
import shapely
from shapely.geometry import mapping

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
MUNICIPIOS = PROCESSED / "regiao_nordeste_bahia" / "municipios_bahia_ibge.geojson"
NOMES = PROCESSED / "regiao_nordeste_bahia" / "municipios_bahia_nomes.json"
METRIC = "EPSG:5880"


def main() -> None:
    names = {int(item["id"]): item["nome"] for item in json.loads(NOMES.read_text(encoding="utf-8"))}
    mun = gpd.read_file(MUNICIPIOS)
    mun["cod_mun"] = mun["codarea"].astype(int)
    mun["municipio"] = mun["cod_mun"].map(names)
    if mun["municipio"].isna().any():
        raise RuntimeError("Município sem nome na tabela IBGE.")
    mun["geometry"] = mun.geometry.make_valid()
    metric = mun.to_crs(METRIC)

    # Contorno leve (≈100 m) só para desenhar a borda selecionada no mapa.
    outline = metric.geometry.simplify(100, preserve_topology=True).to_crs("EPSG:4326")
    bounds = mun.geometry.bounds
    base = pd.DataFrame({
        "cod_mun": mun["cod_mun"],
        "municipio": mun["municipio"],
        "area_municipio_ha": (metric.geometry.area / 10_000).round(1),
        "oeste": bounds["minx"].round(5), "sul": bounds["miny"].round(5),
        "leste": bounds["maxx"].round(5), "norte": bounds["maxy"].round(5),
        "contorno_geojson": [json.dumps(mapping(shapely.set_precision(g, 1e-5)), separators=(",", ":")) for g in outline],
    })
    base.to_parquet(PROCESSED / "municipios_ranking_base.parquet", index=False)

    hexes = gpd.read_file(PROCESSED / "bahia_hex_5km.geojson")[["id_hex", "geometry"]].to_crs(METRIC)
    hexes["geometry"] = hexes.geometry.make_valid()
    pieces = gpd.overlay(hexes, metric[["cod_mun", "geometry"]], how="intersection", keep_geom_type=True)
    pieces["area_ha"] = pieces.geometry.area / 10_000
    hex_weights = pieces.groupby(["id_hex", "cod_mun"], as_index=False)["area_ha"].sum()
    hex_weights = hex_weights[hex_weights["area_ha"] > 0.01].rename(columns={"id_hex": "id"})
    hex_weights["area_ha"] = hex_weights["area_ha"].round(3)
    hex_weights.to_parquet(PROCESSED / "municipios_hex_pesos.parquet", index=False)

    farms = gpd.read_parquet(PROCESSED / "fazendas_apresentacao_parcelas_visual.geoparquet", columns=["id_fazenda", "geometry"]).to_crs(METRIC)
    farms["geometry"] = farms.geometry.make_valid()
    farm_pieces = gpd.overlay(farms, metric[["cod_mun", "geometry"]], how="intersection", keep_geom_type=True)
    farm_pieces["area_ha"] = farm_pieces.geometry.area / 10_000
    best = (farm_pieces.groupby(["id_fazenda", "cod_mun"], as_index=False)["area_ha"].sum()
            .sort_values("area_ha", ascending=False).drop_duplicates("id_fazenda"))
    missing = set(farms["id_fazenda"]) - set(best["id_fazenda"])
    if missing:
        # Parcels touching only the outer state edge: nearest municipality.
        rest = farms[farms["id_fazenda"].isin(missing)].copy()
        rest["geometry"] = rest.geometry.representative_point()
        near = gpd.sjoin_nearest(rest, metric[["cod_mun", "geometry"]], how="left")[["id_fazenda", "cod_mun"]]
        best = pd.concat([best[["id_fazenda", "cod_mun"]], near.drop_duplicates("id_fazenda")], ignore_index=True)
    best[["id_fazenda", "cod_mun"]].to_parquet(PROCESSED / "municipios_imoveis.parquet", index=False)

    print(f"Municípios: {len(base)} | vínculos hexágono–município: {len(hex_weights):,} | "
          f"parcelas: {best['id_fazenda'].nunique():,} (sem interseção: {len(missing)})")


if __name__ == "__main__":
    main()
