"""Kaggle cell — rotas OSM até Ilhéus com consumo de memória controlado.

Substitui NetworkX por uma matriz esparsa SciPy. O algoritmo continua sendo
Dijkstra reverso único e, portanto, calcula a mesma menor distância viária
até o destino para todos os centros de hexágonos e imóveis.
"""
from pathlib import Path
import gc
import json

import geopandas as gpd
import numpy as np
import pandas as pd
from pyproj import Transformer
from pyrosm import OSM
from scipy.spatial import cKDTree
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

# Dataset privado anexado ao notebook Kaggle e fonte OSM de referência.
INPUT = Path('/kaggle/input/datasets/diogosousa9111/bases-privadas-logstica-saf-cacau-bahia')
PBF = Path('/kaggle/working/nordeste-latest.osm.pbf')
OUT = Path('/kaggle/working')

# Ponto operacional de Iguape, Ilhéus. Pode ser trocado posteriormente pela
# coordenada confirmada do portão da unidade, sem mudar o método.
DEST_LAT, DEST_LON = -14.7469433, -39.0698612
DESTINO = 'Cargill Cacau — Distrito Iguape, Ilhéus/BA (ponto operacional)'

# Recorte da Bahia com margem de 30 km para preservar conexões de saída.
BBOX_BAHIA_BUFFER = [-46.85, -18.95, -36.65, -7.75]
ANALYSIS_CRS = 'EPSG:5880'


def origins(path: Path, identifier: str, output_identifier: str) -> pd.DataFrame:
    """Extrai somente centroide e ID; libera as geometrias antes da rede."""
    frame = gpd.read_file(path) if path.suffix.lower() in {'.geojson', '.gpkg'} else gpd.read_parquet(path)
    if not frame[identifier].is_unique:
        raise ValueError(f'Identificador não único: {identifier}')
    centres = frame.to_crs(ANALYSIS_CRS).geometry.centroid
    to_wgs84 = Transformer.from_crs(ANALYSIS_CRS, 4326, always_xy=True)
    lon, lat = to_wgs84.transform(centres.x.to_numpy(), centres.y.to_numpy())
    result = pd.DataFrame({
        output_identifier: frame[identifier].astype(str).to_numpy(),
        'logistica_origem_lon': lon,
        'logistica_origem_lat': lat,
    })
    del frame, centres
    gc.collect()
    return result


hex_origins = origins(INPUT / 'bahia_hex_5km.geojson', 'id_hex', 'id_hex')
farm_origins = origins(INPUT / 'fazendas_georeferenciadas.geoparquet', 'id_fazenda', 'id_fazenda')
print(f'Origens preparadas: {len(hex_origins):,} hexágonos; {len(farm_origins):,} imóveis.')

# Pyrosm limita a extração à caixa da Bahia: o PBF regional é apenas a fonte.
osm = OSM(str(PBF), bounding_box=BBOX_BAHIA_BUFFER)
nodes, edges = osm.get_network(network_type='driving', nodes=True)
node_id_col = next(c for c in ('id', 'node_id', 'osmid') if c in nodes.columns)

node_ids = nodes[node_id_col].astype('int64').to_numpy()
node_lookup = pd.Index(node_ids)
edge_u = node_lookup.get_indexer(edges['u'].astype('int64'))
edge_v = node_lookup.get_indexer(edges['v'].astype('int64'))
valid = (edge_u >= 0) & (edge_v >= 0) & edges['length'].notna().to_numpy()

# A matriz é criada já invertida: Dijkstra parte de Ilhéus e retorna a
# distância de todos os nós até Ilhéus, sem duplicar grafos Python em memória.
reverse_network = csr_matrix(
    (edges.loc[valid, 'length'].to_numpy(dtype='float32'), (edge_v[valid], edge_u[valid])),
    shape=(len(nodes), len(nodes)),
)
print(f'Rede esparsa: {len(nodes):,} nós; {int(valid.sum()):,} arcos.')
del edges, edge_u, edge_v, valid, node_lookup
gc.collect()

# Associação dos centros à rede no mesmo CRS métrico.
to_metric = Transformer.from_crs(4326, ANALYSIS_CRS, always_xy=True)
node_x, node_y = to_metric.transform(nodes['lon'].to_numpy(), nodes['lat'].to_numpy())
tree = cKDTree(np.c_[node_x, node_y])
dest_x, dest_y = to_metric.transform(DEST_LON, DEST_LAT)
_, destination_index = tree.query([dest_x, dest_y])

# Dijkstra reverso único, executado sobre a matriz compacta.
distance_to_ilheus_m = dijkstra(reverse_network, indices=int(destination_index), directed=True)
del reverse_network
gc.collect()
print('Dijkstra concluído.')


def calculate_routes(frame: pd.DataFrame, output_id: str) -> pd.DataFrame:
    x, y = to_metric.transform(frame['logistica_origem_lon'].to_numpy(), frame['logistica_origem_lat'].to_numpy())
    snap_m, nearest = tree.query(np.c_[x, y])
    network_m = distance_to_ilheus_m[nearest]
    result = frame.copy()
    result['logistica_distancia_rota_km'] = (network_m + snap_m) / 1000.0
    result['logistica_distancia_snap_km'] = snap_m / 1000.0
    result['logistica_status_rota'] = np.where(np.isfinite(network_m), 'rota_osm_calculada', 'sem_rota_osm')
    result['logistica_fonte_rota'] = 'OpenStreetMap / Geofabrik Nordeste — driving; recorte Bahia + 30 km; Dijkstra esparso'
    result['logistica_destino'] = DESTINO
    return result


hex_out = calculate_routes(hex_origins, 'id_hex')
farm_out = calculate_routes(farm_origins, 'id_fazenda')


def haversine_km(lon, lat, lon_dest=DEST_LON, lat_dest=DEST_LAT):
    """Distância geodésica mínima: uma rota rodoviária nunca pode ser menor."""
    radius_km = 6371.0088
    phi1, phi2 = np.radians(lat), np.radians(lat_dest)
    dphi = phi2 - phi1
    dlambda = np.radians(lon_dest - lon)
    value = np.sin(dphi / 2) ** 2 + np.cos(phi1) * np.cos(phi2) * np.sin(dlambda / 2) ** 2
    return 2 * radius_km * np.arcsin(np.sqrt(value))


def quality_check(frame: pd.DataFrame, identifier: str, label: str) -> dict:
    """Gera evidência auditável e interrompe somente diante de erro estrutural."""
    if len(frame) == 0 or frame[identifier].duplicated().any():
        raise ValueError(f'{label}: IDs ausentes ou duplicados no resultado de logística.')
    valid = frame['logistica_status_rota'].eq('rota_osm_calculada')
    route = frame.loc[valid, 'logistica_distancia_rota_km'].to_numpy(dtype='float64')
    crow = haversine_km(
        frame.loc[valid, 'logistica_origem_lon'].to_numpy(dtype='float64'),
        frame.loc[valid, 'logistica_origem_lat'].to_numpy(dtype='float64'),
    )
    # Tolerância numérica de 100 m. Violação indica erro de direção/unidade.
    impossible = int(np.sum(route + 0.1 < crow))
    if impossible:
        raise ValueError(f'{label}: {impossible} rotas menores que a distância geodésica; resultado rejeitado.')
    snap = frame['logistica_distancia_snap_km'].to_numpy(dtype='float64')
    return {
        'unidades': int(len(frame)),
        'rotas_calculadas': int(valid.sum()),
        'sem_rota': int((~valid).sum()),
        'cobertura_rota_pct': round(float(valid.mean() * 100), 3),
        'snap_mediano_km': round(float(np.nanmedian(snap)), 4),
        'snap_p95_km': round(float(np.nanpercentile(snap, 95)), 4),
        'snap_maior_5km_qtd': int(np.sum(snap > 5)),
        'rota_menor_que_geodesica_qtd': impossible,
        'rota_km_p05': round(float(np.nanpercentile(route, 5)), 3) if len(route) else None,
        'rota_km_p50': round(float(np.nanpercentile(route, 50)), 3) if len(route) else None,
        'rota_km_p95': round(float(np.nanpercentile(route, 95)), 3) if len(route) else None,
    }


qa = {
    'metodo': 'OSM/Geofabrik driving; Dijkstra reverso em matriz esparsa',
    'destino': DESTINO,
    'destino_wgs84': {'lat': DEST_LAT, 'lon': DEST_LON},
    'hexagonos': quality_check(hex_out, 'id_hex', 'Hexágonos'),
    'imoveis_certificados': quality_check(farm_out, 'id_fazenda', 'Imóveis certificados'),
    'regra_de_uso': 'Linhas sem rota ou com snap elevado permanecem sinalizadas para validação e não são aceitas como distância confirmada.',
}
with open(OUT / 'logistica_qa_resumo.json', 'w', encoding='utf-8') as handle:
    json.dump(qa, handle, ensure_ascii=False, indent=2)

hex_out.to_parquet(OUT / 'logistica_hex_resultados.parquet', index=False)
farm_out.to_parquet(OUT / 'logistica_imoveis_resultados.parquet', index=False)

print('Hex:', hex_out.logistica_status_rota.value_counts().to_dict())
print('Imóveis:', farm_out.logistica_status_rota.value_counts().to_dict())
print(json.dumps(qa, ensure_ascii=False, indent=2))
print('Resultados prontos:', OUT / 'logistica_hex_resultados.parquet', OUT / 'logistica_imoveis_resultados.parquet', OUT / 'logistica_qa_resumo.json')
