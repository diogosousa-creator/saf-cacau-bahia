"""Build MVTs for the individual PRODES and embargoes analytical map."""

from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import mapbox_vector_tile
import mercantile
import pandas as pd
from shapely.geometry import box, mapping


CLASS_CODE = {"Vermelho": 1, "Laranja": 2, "Amarelo": 3, "Verde claro": 4, "Verde escuro": 5}
CLASS_LABEL = {1: "Vermelho – restrição crítica", 2: "Laranja – diligência necessária", 3: "Amarelo – abertura após 2020 predominante", 4: "Verde claro – abertura 2009–2020 predominante", 5: "Verde escuro – abertura até 2008 predominante"}


def n(value, places=1):
    return None if value is None or pd.isna(value) else round(float(value), places)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hex", type=Path, default=Path("data/processed/bahia_hex_5km.geojson"))
    parser.add_argument("--assessments", type=Path, default=Path("data/processed/assessments.parquet"))
    parser.add_argument("--output", type=Path, default=Path("static/hex_prodes_embargos_tiles_v1"))
    parser.add_argument("--minzoom", type=int, default=4)
    parser.add_argument("--maxzoom", type=int, default=7)
    args = parser.parse_args()
    fields = ["id", "classe_prodes_embargos", "risco_socioambiental", "prodes_pre2008_ha", "prodes_2009_2020_ha", "prodes_post2020_ha", "embargo_intersecao_pct", "uc_intersecao_pct", "ti_intersecao_pct", "restricao_unida_pct", "embargo_pontual_qtd", "risco_socioambiental_alert_flags"]
    stats = pd.read_parquet(args.assessments, columns=fields)
    units = gpd.read_file(args.hex).to_crs("EPSG:4326").merge(stats, left_on="id_hex", right_on="id", how="left", validate="one_to_one")
    units["class_code"] = units["classe_prodes_embargos"].map(CLASS_CODE).fillna(0).astype(int)
    index = units.sindex
    count = 0
    for zoom in range(args.minzoom, args.maxzoom + 1):
        tolerance_m = 78_271.5 / (2 ** zoom)
        for tile in mercantile.tiles(*units.total_bounds, zooms=[zoom]):
            bounds = mercantile.bounds(tile)
            clip = box(bounds.west, bounds.south, bounds.east, bounds.north)
            positions = list(index.query(clip, predicate="intersects"))
            if not positions:
                continue
            subset = units.iloc[positions].copy()
            subset["geometry"] = subset.geometry.intersection(clip)
            subset = subset[~subset.geometry.is_empty].to_crs("EPSG:3857")
            subset["geometry"] = subset.geometry.simplify(tolerance_m, preserve_topology=True)
            subset = subset.to_crs("EPSG:4326")
            for code in range(1, 6):
                features = []
                for row in subset[subset.class_code.eq(code)].itertuples(index=False):
                    features.append({"geometry": mapping(row.geometry), "properties": {
                        "referencia_exibicao": "Análise territorial",
                        "classe_mapa": CLASS_LABEL[code], "nota_mapa": n(row.risco_socioambiental),
                        "prodes_ate_2008_ha": n(row.prodes_pre2008_ha), "prodes_2009_2020_ha": n(row.prodes_2009_2020_ha), "prodes_pos_2020_ha": n(row.prodes_post2020_ha),
                        "embargo_poligonal_pct": n(row.embargo_intersecao_pct, 2), "uc_pct": n(row.uc_intersecao_pct, 2), "ti_pct": n(row.ti_intersecao_pct, 2), "restricao_unida_pct": n(row.restricao_unida_pct, 2), "embargo_pontual_qtd": int(row.embargo_pontual_qtd or 0), "alertas": str(row.risco_socioambiental_alert_flags or "[]"),
                    }})
                if not features:
                    continue
                encoded = mapbox_vector_tile.encode({"name": "prodes_embargos_hex", "features": features}, default_options={"quantize_bounds": (bounds.west, bounds.south, bounds.east, bounds.north)})
                dest = args.output / "score" / str(code) / str(zoom) / str(tile.x)
                dest.mkdir(parents=True, exist_ok=True)
                (dest / f"{tile.y}.pbf").write_bytes(encoded)
                count += 1
    print(f"Tiles PRODES/embargos criados: {count:,}")


if __name__ == "__main__":
    main()
