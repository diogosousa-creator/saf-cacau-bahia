"""Data loading with an explicit demonstration fallback."""

from __future__ import annotations

from pathlib import Path
import math
import json

import pandas as pd
import streamlit as st


ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"

# The property detail panel deliberately uses a narrow, non-spatial projection
# of the certified-farms master layer. Geometry stays in MVT/GeoParquet and is
# never sent again merely to explain a clicked property.
FARM_DETAIL_COLUMNS = (
    "id_fazenda",
    "referencia_exibicao",
    "area_ha",
    "tipo_solo",
    "relevo_drenagem",
    "clima_irrigacao",
    "seguranca_hidrica",
    "risco_socioambiental",
    "logistica_ilheus",
    "custo_terra",
    "mao_obra_infra",
    "uso_solo",
    "centroide_longitude",
    "centroide_latitude",
    "limite_oeste",
    "limite_sul",
    "limite_leste",
    "limite_norte",
)


def _hexagon(longitude: float, latitude: float, radius: float = 0.055) -> list[list[float]]:
    """Demo geometry only. Production geometries come from GeoParquet."""
    return [
        [longitude + radius * math.cos(math.radians(60 * position)), latitude + radius * math.sin(math.radians(60 * position))]
        for position in range(7)
    ]


def _demo_records() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"id": "HEX-001", "tipo": "Hexágono 5 km", "fonte_geometria": "Índice territorial", "nome": "Exemplo de célula territorial", "latitude": -12.96, "longitude": -39.05, "coordinates": _hexagon(-39.05, -12.96, 0.20), "area_ha": 2165, "clima_irrigacao": 72, "tipo_solo": 69, "relevo_drenagem": 69, "seguranca_hidrica": 62, "logistica_ilheus": 58, "custo_terra": 64, "mao_obra_infra": 70, "risco_socioambiental": 90, "demanda_mm_ano": 850, "frete_rs_t": 245, "prodes_pos_2020_pct": 0.0, "embargo_oficial": "Não identificado"},
            {"id": "SIGEF-EX-01", "tipo": "Imóvel certificado", "fonte_geometria": "SIGEF", "nome": "Exemplo SIGEF", "latitude": -12.88, "longitude": -38.89, "coordinates": _hexagon(-38.89, -12.88, 0.08), "area_ha": 428, "clima_irrigacao": 81, "tipo_solo": 76, "relevo_drenagem": 76, "seguranca_hidrica": 67, "logistica_ilheus": 63, "custo_terra": 56, "mao_obra_infra": 75, "risco_socioambiental": 92, "demanda_mm_ano": 690, "frete_rs_t": 225, "prodes_pos_2020_pct": 0.0, "embargo_oficial": "Não identificado"},
            {"id": "SNCI-EX-01", "tipo": "Imóvel certificado", "fonte_geometria": "SNCI complementar", "nome": "Exemplo SNCI complementar", "latitude": -11.94, "longitude": -39.36, "coordinates": _hexagon(-39.36, -11.94, 0.08), "area_ha": 311, "clima_irrigacao": 59, "tipo_solo": 63, "relevo_drenagem": 63, "seguranca_hidrica": 49, "logistica_ilheus": 47, "custo_terra": 72, "mao_obra_infra": 52, "risco_socioambiental": 68, "demanda_mm_ano": 1090, "frete_rs_t": 321, "prodes_pos_2020_pct": 4.8, "embargo_oficial": "Não identificado"},
        ]
    )


def _file_stamp(*paths: Path) -> tuple:
    """Cache key tied to the data files: a deploy that replaces a parquet
    invalidates the cached frame even when Streamlit Cloud reuses the process."""
    return tuple((p.name, p.stat().st_size, p.stat().st_mtime_ns) if p.exists() else (p.name, None) for p in paths)


def load_assessments() -> tuple[pd.DataFrame, bool]:
    """Load the immutable analytical table once per data version.

    The returned frame is the master-table snapshot; scoring remains virtual in
    the browser and therefore changing a sidebar weight never changes this
    source or triggers a new spatial calculation.
    """
    return _load_assessments(_file_stamp(PROCESSED / "assessments.parquet"))


@st.cache_data(show_spinner=False)
def _load_assessments(stamp: tuple) -> tuple[pd.DataFrame, bool]:
    path = PROCESSED / "assessments.parquet"
    if path.exists():
        return pd.read_parquet(path), False
    return _demo_records(), True


@st.cache_data(show_spinner=False)
def load_bahia_layers() -> tuple[dict | None, dict | None]:
    """Cache the small state boundary/mask used by every map view."""
    boundary_path = PROCESSED / "bahia_boundary.geojson"
    mask_path = PROCESSED / "bahia_exterior_mask.geojson"
    if not boundary_path.exists() or not mask_path.exists():
        return None, None
    return json.loads(boundary_path.read_text(encoding="utf-8")), json.loads(mask_path.read_text(encoding="utf-8"))


def load_hex_grid() -> dict | None:
    path = PROCESSED / "bahia_hex_5km.geojson"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


@st.cache_data(show_spinner=False)
def has_georeferenced_farms() -> bool:
    """Check the active MVT set without reading any property geometry.

    The previous recursive check inspected a legacy tile tree at every
    Streamlit rerun.  The active version is immutable and its directory is the
    availability contract; geometry remains loaded by viewport only in deck.gl.
    """
    tiles = ROOT / "static" / "imoveis_apresentacao_parcelas_tiles_v25"
    return tiles.is_dir() and any(tiles.iterdir())


def load_farm_detail_lookup() -> pd.DataFrame:
    return _load_farm_detail_lookup(_file_stamp(PROCESSED / "fazendas_apresentacao_parcelas_detalhe_lookup.parquet",
                                                PROCESSED / "fazendas_detalhe_lookup.parquet"))


@st.cache_data(show_spinner=False)
def _load_farm_detail_lookup(stamp: tuple) -> pd.DataFrame:
    """Return the cached attribute-only lookup used after a map click.

    This is intentionally lazy: the certified-property table is never loaded
    during normal map navigation. When an analyst selects a farm, only this
    concise projection is read and then retained server-side for the session.
    """
    # This sidecar is attribute-only. It avoids opening the 97k-property
    # geometry layer merely to centre a selected parcel or show its scores.
    # Prefer the non-overlapping presentation layer when it has been built.
    # The master lookup remains intact as a fallback and is never overwritten.
    presentation_path = PROCESSED / "fazendas_apresentacao_parcelas_detalhe_lookup.parquet"
    path = presentation_path if presentation_path.exists() else PROCESSED / "fazendas_detalhe_lookup.parquet"
    if not path.exists():
        return pd.DataFrame(columns=FARM_DETAIL_COLUMNS).set_index("id_fazenda")
    lookup = pd.read_parquet(path)
    for column in FARM_DETAIL_COLUMNS:
        if column not in lookup.columns:
            lookup[column] = pd.NA
    lookup = lookup[list(FARM_DETAIL_COLUMNS)]
    lookup = lookup.drop_duplicates(subset="id_fazenda", keep="first")
    return lookup.set_index("id_fazenda", drop=False)


@st.cache_data(show_spinner=False)
def load_selected_farm_feature(farm_id: str) -> dict | None:
    """Load one original certified-property geometry only after selection."""
    import geopandas as gpd

    path = PROCESSED / "fazendas_georeferenciadas.geoparquet"
    if not path.exists():
        return None
    selected = gpd.read_parquet(
        path,
        columns=["id_fazenda", "geometry"],
        filters=[("id_fazenda", "==", farm_id)],
    )
    if selected.empty:
        return None
    geometry = selected.to_crs(4326).geometry.iloc[0]
    return {
        "type": "Feature",
        "properties": {"id": str(farm_id)},
        "geometry": geometry.__geo_interface__,
    }


_RANKING_FILES = ("municipios_ranking_base.parquet", "municipios_hex_pesos.parquet", "municipios_imoveis.parquet")


def load_municipal_ranking_inputs() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame] | None:
    """Spatial links for the municipal ranking (independent of the weights).

    Loaded only when the ranking block runs; cached per data version.
    """
    return _load_municipal_ranking_inputs(_file_stamp(*(PROCESSED / name for name in _RANKING_FILES)))


@st.cache_data(show_spinner=False)
def _load_municipal_ranking_inputs(stamp: tuple) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame] | None:
    paths = [PROCESSED / name for name in _RANKING_FILES]
    if not all(path.exists() for path in paths):
        return None
    base, hex_weights, farm_municipio = (pd.read_parquet(path) for path in paths)
    return base, hex_weights, farm_municipio
