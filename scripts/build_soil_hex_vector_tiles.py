"""Build zoom-dependent vector tiles for the MapBiomas soil hex layer."""

from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import mapbox_vector_tile

from add_tooltip_text_tiles import tooltip_properties
import mercantile
import pandas as pd
from shapely.geometry import box, mapping


SCORE_CLASS = {"Muito baixa": 1, "Baixa": 2, "Média": 3, "Alta": 4, "Muito alta": 5,
               "Muito restrita": 1, "Restrita": 2, "Condicionada": 3, "Favorável": 4, "Muito favorável": 5}


def _num(value: object, places: int = 1) -> str:
    if value is None or pd.isna(value):
        return "sem dado"
    return f"{float(value):,.{places}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def tooltip_html(row, kind: str) -> str:
    """One ready-made tooltip per feature.

    The individual maps stack several sources (score, MapBiomas, IBGE, COPDEM)
    under a single pydeck tooltip template; each source therefore carries its
    own complete text in ``tt`` and the template is simply ``{tt}``.
    """
    classe = row.classe_tipo_solo if pd.notna(row.classe_tipo_solo) else "Sem dados"
    if kind == "relief":
        return ("<b>Declividade e Relevo</b>"
                f"<br/>Nota: {_num(row.tipo_solo)}/100 · {classe}"
                f"<br/>Declividade média: {_num(row.declividade_media_pct)}%"
                f"<br/>Área com declividade de 25–45%: {_num(row.declividade_25_45_pct)}%"
                f"<br/>Área com declividade acima de 45%: {_num(row.declividade_gt45_pct)}%")
    textura = row.solo_textura_dominante if pd.notna(row.solo_textura_dominante) else "sem dado"
    return ("<b>Qualidade física do solo</b>"
            f"<br/>Nota: {_num(row.tipo_solo)}/100 · {classe}"
            f"<br/>Textura dominante (MapBiomas): {textura}"
            f"<br/>Área com textura favorável: {_num(row.solo_textura_favoravel_pct)}%")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hex", type=Path, default=Path("data/processed/bahia_hex_5km.geojson"))
    parser.add_argument("--assessments", type=Path, default=Path("data/processed/assessments.parquet"))
    parser.add_argument("--output", type=Path, default=Path("static/hex_soil_tiles"))
    parser.add_argument("--score-field", default="tipo_solo")
    parser.add_argument("--class-field", default="classe_tipo_solo")
    parser.add_argument("--score-mode", default="score")
    parser.add_argument("--minzoom", type=int, default=4)
    parser.add_argument("--maxzoom", type=int, default=9)
    parser.add_argument("--tooltip", choices=("soil", "relief"), default="soil",
                        help="Texto pronto do tooltip gravado em cada hexágono (campo ``tt``).")
    args = parser.parse_args()

    hexes = gpd.read_file(args.hex).to_crs("EPSG:4326")
    extra = ["declividade_media_pct", "declividade_max_pct", "declividade_25_45_pct", "declividade_gt45_pct"] if args.tooltip == "relief" else []
    stats = pd.read_parquet(args.assessments)[["id", args.score_field, args.class_field, "solo_textura_dominante", "solo_textura_dominante_codigo", "solo_textura_favoravel_pct", *extra]].rename(columns={args.score_field: "tipo_solo", args.class_field: "classe_tipo_solo"})
    hexes = hexes.merge(stats, left_on="id_hex", right_on="id", how="left", validate="one_to_one")
    hexes["score_class"] = hexes["classe_tipo_solo"].map(SCORE_CLASS).fillna(0).astype(int)
    hexes["raw_class"] = hexes["solo_textura_dominante_codigo"].fillna(0).astype(int)
    hexes["tt"] = [tooltip_html(row, args.tooltip) for row in hexes.itertuples(index=False)]
    index = hexes.sindex
    args.output.mkdir(parents=True, exist_ok=True)
    built = 0
    for zoom in range(args.minzoom, args.maxzoom + 1):
        # Cap: a regular 5 km hexagon loses vertices above ~1 km tolerance.
        tolerance_m = min(78_271.5 / (2**zoom), 150)
        for tile in mercantile.tiles(*hexes.total_bounds, zooms=[zoom]):
            bounds = mercantile.bounds(tile)
            positions = list(index.query(box(bounds.west, bounds.south, bounds.east, bounds.north), predicate="intersects"))
            if not positions:
                continue
            subset = hexes.iloc[positions].copy()
            subset["geometry"] = subset.geometry.intersection(box(bounds.west, bounds.south, bounds.east, bounds.north))
            subset = subset[~subset.geometry.is_empty].to_crs("EPSG:3857")
            subset["geometry"] = subset.geometry.simplify(tolerance_m, preserve_topology=True)
            subset = subset.to_crs("EPSG:4326")
            base_features = [{
                "geometry": mapping(row.geometry),
                # Colours come from the category folder; the tile keeps only the
                # tooltip lines.  score_class/raw_class select the folder below.
                "properties": tooltip_properties(row.tt),
                "_score_class": int(row.score_class),
                "_raw_class": int(row.raw_class),
            } for row in subset.itertuples(index=False)]
            for mode, field, classes in ((args.score_mode, "score_class", range(1, 6)), ("raw", "raw_class", range(1, 7))):
                for category in classes:
                    features = [{"geometry": feature["geometry"], "properties": feature["properties"]}
                                for feature in base_features if feature["_" + field] == category]
                    if not features:
                        continue
                    encoded = mapbox_vector_tile.encode(
                        {"name": "solo_hex", "features": features},
                        default_options={"quantize_bounds": (bounds.west, bounds.south, bounds.east, bounds.north)},
                    )
                    destination = args.output / mode / str(category) / str(zoom) / str(tile.x)
                    destination.mkdir(parents=True, exist_ok=True)
                    (destination / f"{tile.y}.pbf").write_bytes(encoded)
                    built += 1
    print(f"Tiles criados: {built:,}")


if __name__ == "__main__":
    main()
