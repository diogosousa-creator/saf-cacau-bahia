"""Browser-side parcel map with stable ID-based picking.

Unlike ``st.pydeck_chart`` selection, this component never uses an MVT
feature's tile-local index.  Click state is always the ``farm_id`` property.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit.components.v1 as components


_component = components.declare_component(
    "parcel_map",
    path=str(Path(__file__).resolve().parents[1] / "components" / "parcel_map"),
)


def parcel_map(*, weights: dict[str, int], active_id: str | None, view_state: dict[str, float] | None,
               bahia_mask: dict[str, Any] | None, bahia_boundary: dict[str, Any] | None, key: str) -> dict[str, Any] | None:
    """Render the main WebGIS map and return an exact browser-side click."""
    return _component(
        weights=weights,
        active_id=active_id or "",
        view_state=view_state or {"latitude": -12.6, "longitude": -41.7, "zoom": 5.15, "bearing": 0, "pitch": 0},
        bahia_mask=bahia_mask,
        bahia_boundary=bahia_boundary,
        hex_tiles="/app/static/hex_base_tiles_v16/{z}/{x}/{y}.pbf",
        parcel_tiles="/app/static/imoveis_apresentacao_parcelas_tiles_v24/{z}/{x}/{y}.pbf",
        default=None,
        key=key,
    )
