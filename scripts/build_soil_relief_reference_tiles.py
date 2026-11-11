"""Build display-only source layers for the soil and terrain individual maps.

The score layers stay untouched.  These tiles expose only the source summaries
used in their calculation: IBGE Pedology polygons and the 30 m Copernicus
GLO-30 slope summarized independently for every 5 km hexagon.
"""
from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import mapbox_vector_tile
import mercantile
import pandas as pd
from shapely.geometry import box, mapping


ROOT = Path(__file__).resolve().parents[1]


def _write(output: Path, group: str, tile: mercantile.Tile, features: list[dict], bounds) -> None:
    if not features:
        return
    destination = output / group / str(tile.z) / str(tile.x)
    destination.mkdir(parents=True, exist_ok=True)
    encoded = mapbox_vector_tile.encode(
        {"name": group, "features": features},
        default_options={"quantize_bounds": bounds, "extents": 4096},
    )
    (destination / f"{tile.y}.pbf").write_bytes(encoded)


def _clean_polygons(frame: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    result = frame[frame.geometry.notna() & ~frame.geometry.is_empty].copy()
    result["geometry"] = result.geometry.make_valid()
    result = result.explode(index_parts=False)
    return result[result.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].copy()


def pedology_group(legend: object) -> str:
    """IBGE technical-pedology convention, grouped by order/suborder color."""
    text = str(legend or "").casefold()
    if "latossolo vermelho-amarelo" in text: return "latossolo_vermelho_amarelo"
    if "latossolo vermelho" in text: return "latossolo_vermelho"
    if "latossolo amarelo" in text: return "latossolo_amarelo"
    if "latossolo bruno" in text: return "latossolo_bruno"
    if "argissolo vermelho-amarelo" in text: return "argissolo_vermelho_amarelo"
    if "argissolo vermelho" in text: return "argissolo_vermelho"
    if "argissolo amarelo" in text: return "argissolo_amarelo"
    if "argissolo" in text: return "argissolo_outro"
    if "cambissolo" in text: return "cambissolo"
    if "neossolo" in text: return "neossolo"
    if "luvissolo" in text: return "luvissolo"
    if "gleissolo" in text: return "gleissolo"
    if "planossolo" in text: return "planossolo"
    if "vertissolo" in text: return "vertissolo"
    if "chernossolo" in text: return "chernossolo"
    if "espodossolo" in text: return "espodossolo"
    if "organossolo" in text: return "organossolo"
    return "outras_unidades"


def build_pedology(output: Path) -> int:
    source = ROOT / "data" / "processed" / "ibge_pedologia_250k_bahia.geoparquet"
    frame = _clean_polygons(gpd.read_parquet(source).to_crs("EPSG:4326"))
    frame["pedology_group"] = frame["legenda"].map(pedology_group)
    index = frame.sindex
    made = 0
    # IBGE 1:250,000 is a regional reference.  Stopping at z7 preserves its
    # intended scale and avoids pretending it has parcel-scale precision.
    for zoom in range(5, 8):
        tolerance_m = {5: 1_100, 6: 520, 7: 180, 8: 65, 9: 22}[zoom]
        for tile in mercantile.tiles(*frame.total_bounds, zooms=[zoom]):
            bounds = mercantile.bounds(tile)
            clipped = box(bounds.west, bounds.south, bounds.east, bounds.north)
            positions = list(index.query(clipped, predicate="intersects"))
            if not positions:
                continue
            subset = frame.iloc[positions].copy()
            subset["geometry"] = subset.geometry.intersection(clipped)
            subset = _clean_polygons(subset).to_crs("EPSG:3857")
            subset["geometry"] = subset.geometry.simplify(tolerance_m, preserve_topology=True)
            subset = _clean_polygons(subset).to_crs("EPSG:4326")
            for group, part in subset.groupby("pedology_group", sort=False):
                features = []
                for row in part.itertuples(index=False):
                    multiplier = getattr(row, "ibge_pedologia_multiplicador", None)
                    features.append({"geometry": mapping(row.geometry), "properties": {
                        "legenda": str(getattr(row, "legenda", "") or "Unidade pedológica IBGE"),
                        "textura": str(getattr(row, "textura", "") or "Não informada"),
                        "pedregosidade": str(getattr(row, "pedregosid", "") or "Não informada"),
                        "rochosidade": str(getattr(row, "rochosidad", "") or "Não informada"),
                        "relevo": str(getattr(row, "relevo", "") or "Não informado"),
                        "multiplicador_fisico": None if pd.isna(multiplier) else round(float(multiplier), 3),
                        "restricao_critica": bool(getattr(row, "restricao_critica_ibge", False)),
                    }})
                _write(output, f"pedologia_ibge/{group}", tile, features, (bounds.west, bounds.south, bounds.east, bounds.north))
                made += 1
    return made


def slope_class(value: object) -> int:
    if value is None or pd.isna(value):
        return 0
    value = float(value)
    if value <= 3:
        return 1
    if value <= 8:
        return 2
    if value <= 15:
        return 3
    if value <= 25:
        return 4
    return 5


def build_slope(output: Path) -> int:
    hexes = gpd.read_file(ROOT / "data" / "processed" / "bahia_hex_5km.geojson").to_crs("EPSG:4326")
    columns = ["id", "declividade_media_pct", "declividade_max_pct", "declividade_25_45_pct", "declividade_gt45_pct"]
    stats = pd.read_parquet(ROOT / "data" / "processed" / "assessments.parquet", columns=columns)
    frame = hexes.merge(stats, left_on="id_hex", right_on="id", how="left", validate="one_to_one")
    frame["source_class"] = frame["declividade_media_pct"].map(slope_class)
    index = frame.sindex
    made = 0
    # The statistical slope layer is an overview reference.  z4–z6 matches
    # the 5 km unit scale; the score map remains available through z7.
    for zoom in range(4, 7):
        tolerance_m = 78_271.5 / (2 ** zoom)
        for tile in mercantile.tiles(*frame.total_bounds, zooms=[zoom]):
            bounds = mercantile.bounds(tile)
            clipped = box(bounds.west, bounds.south, bounds.east, bounds.north)
            positions = list(index.query(clipped, predicate="intersects"))
            if not positions:
                continue
            subset = frame.iloc[positions].copy()
            subset["geometry"] = subset.geometry.intersection(clipped)
            subset = subset[subset.geometry.notna() & ~subset.geometry.is_empty].to_crs("EPSG:3857")
            subset["geometry"] = subset.geometry.simplify(tolerance_m, preserve_topology=True)
            subset = subset.to_crs("EPSG:4326")
            for category in range(1, 6):
                features = []
                for row in subset[subset.source_class.eq(category)].itertuples(index=False):
                    features.append({"geometry": mapping(row.geometry), "properties": {
                        "declividade_media_pct": round(float(row.declividade_media_pct), 2),
                        "declividade_max_pct": round(float(row.declividade_max_pct), 2),
                        "area_25_45_pct": round(float(row.declividade_25_45_pct), 2),
                        "area_maior_45_pct": round(float(row.declividade_gt45_pct), 2),
                    }})
                _write(output, f"declividade_media/{category}", tile, features, (bounds.west, bounds.south, bounds.east, bounds.north))
                made += bool(features)
    return made


if __name__ == "__main__":
    target = ROOT / "static" / "solo_relevo_referencias_tiles_v1"
    print(f"Pedologia IBGE: {build_pedology(target):,} tiles", flush=True)
    print(f"Declividade COPDEM: {build_slope(target):,} tiles", flush=True)
