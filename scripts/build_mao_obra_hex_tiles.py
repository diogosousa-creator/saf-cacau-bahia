from pathlib import Path
import geopandas as gpd, pandas as pd, mercantile, mapbox_vector_tile
from shapely.geometry import box, mapping
def c(v): return 0 if pd.isna(v) else (5 if v>=80 else 4 if v>=65 else 3 if v>=50 else 2 if v>=35 else 1)
def n(v,d=1): return None if pd.isna(v) else round(float(v),d)
lab={0:'Sem dados',1:'Muito baixa',2:'Baixa',3:'Média',4:'Alta',5:'Muito alta'}; cols=['id','mao_obra_score','mao_obra_agro_ocupados_2017','mao_obra_pop_2022','mao_obra_municipio_dominante','mao_obra_municipio_area_dominante_pct','mao_obra_confidence','mao_obra_alertas']
g=gpd.read_file('data/processed/bahia_hex_5km.geojson').merge(pd.read_parquet('data/processed/assessments.parquet',columns=cols),left_on='id_hex',right_on='id',how='left').to_crs(4326); themes={'indice':'mao_obra_score','pessoal_rural':'mao_obra_agro_ocupados_2017','populacao':'mao_obra_pop_2022'}
for k,f in themes.items():
 q=g[f].quantile([.1,.9]); g[k]=g[f].map(lambda v:c(100*(v-q.iloc[0])/(q.iloc[1]-q.iloc[0]) if k!='indice' and pd.notna(v) else v))
ix=g.sindex; out=Path('static/hex_mao_obra_tiles_v1')
for z in range(4,8):
 for t in mercantile.tiles(*g.total_bounds,zooms=[z]):
  b=mercantile.bounds(t); x=g.iloc[list(ix.query(box(*b),predicate='intersects'))].copy(); x.geometry=x.geometry.intersection(box(*b)); x=x[~x.geometry.is_empty].to_crs(3857); x.geometry=x.geometry.simplify(78271.5/2**z,preserve_topology=True); x=x.to_crs(4326)
  for theme in themes:
   for q in range(6):
    fs=[{'geometry':mapping(r.geometry),'properties':{'classe_mapa':lab[q],'nota_mapa':n(r.mao_obra_score),'pessoal_rural_2017':n(r.mao_obra_agro_ocupados_2017,0),'populacao_2022':n(r.mao_obra_pop_2022,0),'municipio':str(r.mao_obra_municipio_dominante),'cobertura_pct':n(r.mao_obra_municipio_area_dominante_pct),'confianca':str(r.mao_obra_confidence),'alertas':str(r.mao_obra_alertas)}} for r in x[x[theme].eq(q)].itertuples(index=False)]
    if fs:
     d=out/theme/str(q)/str(z)/str(t.x); d.mkdir(parents=True,exist_ok=True); (d/f'{t.y}.pbf').write_bytes(mapbox_vector_tile.encode({'name':'mao_obra','features':fs},default_options={'quantize_bounds':(b.west,b.south,b.east,b.north)}))
