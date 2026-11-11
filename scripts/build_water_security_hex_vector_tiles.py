"""Build lightweight MVTs for the preliminary water-security evidence map."""
from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import mapbox_vector_tile
import mercantile
import pandas as pd
from shapely.geometry import box, mapping


def score_class(value: float | None) -> int:
    if value is None or pd.isna(value): return 0
    if value < 35: return 1
    if value < 50: return 2
    if value < 65: return 3
    if value < 80: return 4
    return 5


def rounded(value: object, decimals: int = 1) -> float | None:
    return None if value is None or pd.isna(value) else round(float(value), decimals)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hex", type=Path, default=Path("data/processed/bahia_hex_5km.geojson"))
    parser.add_argument("--assessments", type=Path, default=Path("data/processed/assessments.parquet"))
    parser.add_argument("--output", type=Path, default=Path("static/hex_water_security_tiles_v1"))
    parser.add_argument("--minzoom", type=int, default=4)
    parser.add_argument("--maxzoom", type=int, default=7)
    args = parser.parse_args()
    fields = [
        "id", "seguranca_hidrica", "agua_persistente_area_pct", "agua_frequencia_recente_pct",
        "bho_trecho_intersecta", "bho_distancia_min_m", "bho_strahler_max_intersectado", "bho_strahler_mais_proximo", "bho_area_montante_mais_proxima_km2", "siagas_pocos_no_imovel",
        "siagas_pocos_2km", "siagas_pocos_5km", "seguranca_hidrica_confidence",
        "outorga_superficial_ativa_no_imovel", "outorga_superficial_ativa_qtd", "outorga_superficial_curso_intersecta_qtd", "outorga_superficial_distancia_curso_m", "nota_evidencia_outorga_superficial", "nota_evidencia_subterranea", "outorga_subterranea_ativa_no_imovel_qtd", "outorga_subterranea_inativa_no_imovel_qtd", "siagas_pocos_ativos_teste_no_imovel", "siagas_pocos_ativos_teste_2km", "siagas_pocos_incertos_no_imovel",
    ]
    hexes = gpd.read_file(args.hex).to_crs("EPSG:4326")
    stats = pd.read_parquet(args.assessments, columns=fields)
    hexes = hexes.merge(stats, left_on="id_hex", right_on="id", how="left", validate="one_to_one")
    hexes["score_class"] = hexes["seguranca_hidrica"].map(score_class)
    index = hexes.sindex
    built = 0
    labels = ("Muito baixa", "Baixa", "Média", "Alta", "Muito alta")
    for zoom in range(args.minzoom, args.maxzoom + 1):
        tolerance_m = 78_271.5 / (2 ** zoom)
        for tile in mercantile.tiles(*hexes.total_bounds, zooms=[zoom]):
            bounds = mercantile.bounds(tile)
            clip = box(bounds.west, bounds.south, bounds.east, bounds.north)
            positions = list(index.query(clip, predicate="intersects"))
            if not positions: continue
            subset = hexes.iloc[positions].copy()
            subset["geometry"] = subset.geometry.intersection(clip)
            subset = subset[~subset.geometry.is_empty].to_crs("EPSG:3857")
            subset["geometry"] = subset.geometry.simplify(tolerance_m, preserve_topology=True)
            subset = subset.to_crs("EPSG:4326")
            for category in range(1, 6):
                features = []
                for row in subset[subset.score_class.eq(category)].itertuples(index=False):
                    features.append({"geometry": mapping(row.geometry), "properties": {
                        "referencia_exibicao": "Análise territorial",
                        "nota_mapa": rounded(row.seguranca_hidrica), "classe_mapa": labels[category - 1],
                        "agua_persistente_area_pct": rounded(row.agua_persistente_area_pct, 3),
                        "agua_frequencia_recente_pct": rounded(row.agua_frequencia_recente_pct, 3),
                        "bho_trecho_intersecta": bool(row.bho_trecho_intersecta) if pd.notna(row.bho_trecho_intersecta) else False,
                        "bho_distancia_min_m": rounded(row.bho_distancia_min_m, 0),
                        "bho_strahler_max_intersectado": rounded(row.bho_strahler_max_intersectado, 0),
                        "bho_strahler_mais_proximo": rounded(row.bho_strahler_mais_proximo, 0),
                        "bho_area_montante_mais_proxima_km2": rounded(row.bho_area_montante_mais_proxima_km2, 1),
                        "outorga_ativa_no_imovel": bool(row.outorga_superficial_ativa_no_imovel) if pd.notna(row.outorga_superficial_ativa_no_imovel) else False,
                        "outorga_ativa_qtd": int(row.outorga_superficial_ativa_qtd or 0),
                        "curso_outorgado_intersecta_qtd": int(row.outorga_superficial_curso_intersecta_qtd or 0),
                        "distancia_curso_outorgado_m": rounded(row.outorga_superficial_distancia_curso_m, 0),
                        "nota_evidencia_outorga": rounded(row.nota_evidencia_outorga_superficial),
                        "nota_evidencia_subterranea": rounded(row.nota_evidencia_subterranea),
                        "outorga_subterranea_ativa_qtd": int(row.outorga_subterranea_ativa_no_imovel_qtd or 0),
                        "outorga_subterranea_inativa_qtd": int(row.outorga_subterranea_inativa_no_imovel_qtd or 0),
                        "pocos_ativos_testados_no_imovel": int(row.siagas_pocos_ativos_teste_no_imovel or 0),
                        "pocos_ativos_testados_2km": int(row.siagas_pocos_ativos_teste_2km or 0),
                        "pocos_incertos_no_imovel": int(row.siagas_pocos_incertos_no_imovel or 0),
                        "siagas_pocos_no_imovel": int(row.siagas_pocos_no_imovel or 0),
                        "siagas_pocos_2km": int(row.siagas_pocos_2km or 0),
                        "siagas_pocos_5km": int(row.siagas_pocos_5km or 0),
                        "confianca": str(row.seguranca_hidrica_confidence or "unknown"),
                    }})
                if not features: continue
                encoded = mapbox_vector_tile.encode({"name": "seguranca_hidrica_hex", "features": features}, default_options={"quantize_bounds": (bounds.west, bounds.south, bounds.east, bounds.north)})
                dest = args.output / "score" / str(category) / str(zoom) / str(tile.x)
                dest.mkdir(parents=True, exist_ok=True)
                (dest / f"{tile.y}.pbf").write_bytes(encoded)
                built += 1
    print(f"Tiles de segurança hídrica criados: {built:,}")


if __name__ == "__main__":
    main()
