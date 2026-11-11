"""Pre-compute municipal labour-context score for hexagons and certified farms.

RAIS is intentionally optional: without its official municipal extract the
result remains a visibly flagged preliminary index, never a claim that labour
is available for hire.
"""
from __future__ import annotations
import argparse, json, math, unicodedata, re
from pathlib import Path
import geopandas as gpd
import numpy as np
import pandas as pd
from shapely import make_valid

def norm(x):
    return re.sub(r"[^A-Z0-9]", "", unicodedata.normalize("NFKD", str(x)).encode("ascii", "ignore").decode().upper())

def sidra_total(path, variable):
    rows=json.loads(path.read_text()); d=pd.DataFrame(rows[1:]); d=d[(d.D3C.astype(str)==str(variable)) & (d.D4N=='Total') & (d.D5N=='Total') & (d.D6N=='Total')]
    return d[['D1C','D1N','V']].rename(columns={'D1C':'cod_mun','D1N':'municipio','V':variable}).assign(**{variable:lambda x:pd.to_numeric(x[variable].str.replace('.','',regex=False).str.replace(',','.',regex=False),errors='coerce')})

def qscore(x):
    z=np.log1p(x); lo,hi=z.quantile(.1),z.quantile(.9); return (100*(z-lo)/(hi-lo)).clip(0,100)

def unit_stats(units, municipal, id_col):
    left=units[[id_col,'geometry']].to_crs(5880).copy(); right=municipal.to_crs(5880).copy()
    left.geometry=left.geometry.map(make_valid); right.geometry=right.geometry.map(make_valid); ix=right.sindex
    out=[]
    for row in left.itertuples(index=False):
        candidates=list(ix.query(row.geometry,predicate='intersects')); p=right.iloc[candidates].copy()
        if p.empty: continue
        areas=np.asarray([row.geometry.intersection(g).area for g in p.geometry]); p=p[areas>0].copy(); areas=areas[areas>0]
        w=areas/areas.sum(); dominant=p.iloc[int(np.argmax(w))]
        out.append({id_col:getattr(row,id_col),'mao_obra_infra':float(np.dot(w,p.score_mao_obra)), 'mao_obra_score':float(np.dot(w,p.score_mao_obra)),
                    'mao_obra_agro_ocupados_2017':float(np.dot(w,p.agro_ocupados_2017)), 'mao_obra_pop_2022':float(np.dot(w,p.pop_2022)),
                    'mao_obra_rais_2024':np.nan if p.rais_2024.isna().all() else float(np.nansum(w*np.nan_to_num(p.rais_2024))),
                    'mao_obra_municipio_dominante':dominant.municipio,'mao_obra_municipio_area_dominante_pct':round(float(w.max()*100),1),
                    'mao_obra_municipios_intersectados':len(p),'mao_obra_confidence':'baixa' if p.rais_2024.isna().all() else 'média',
                    'mao_obra_alertas':'["RAIS_2024_PENDENTE","CENSO_AGRO_2017_DEFASADO"]' if p.rais_2024.isna().all() else '["CENSO_AGRO_2017_DEFASADO","RAIS_NAO_MEDE_INFORMALIDADE"]'})
    return pd.DataFrame(out)

def main():
    a=argparse.ArgumentParser(); a.add_argument('--processed',type=Path,default=Path('data/processed')); a.add_argument('--raw',type=Path,default=Path('data/raw/mao_obra')); x=a.parse_args()
    agro=sidra_total(x.raw/'censo_agro_pessoal_ocupado_bahia_2017.json','185').rename(columns={'185':'agro_ocupados_2017'})
    pop=sidra_total(x.raw/'censo_2022_populacao_bahia.json','93').rename(columns={'93':'pop_2022'})
    municipal=gpd.read_file(Path('data/raw/ibge_bahia_2024/BA_Municipios_2024.shp')); code=next(c for c in municipal if c.upper() in {'CD_MUN','CD_MUN7'}); name=next(c for c in municipal if c.upper() in {'NM_MUN','NM_MUNICIP'})
    municipal['cod_mun']=municipal[code].astype(str); municipal['municipio']=municipal[name]; municipal=municipal.merge(agro[['cod_mun','agro_ocupados_2017']],on='cod_mun',how='left').merge(pop[['cod_mun','pop_2022']],on='cod_mun',how='left')
    rais=x.raw/'rais_agro_bahia_2024.csv'; municipal['rais_2024']=np.nan
    if rais.exists():
        r=pd.read_csv(rais,dtype={'cod_mun':str}); municipal=municipal.drop(columns='rais_2024').merge(r[['cod_mun','rais_2024']],on='cod_mun',how='left')
    rho=municipal.agro_ocupados_2017.sum()/municipal.pop_2022.sum(); municipal['agro_1000_suavizado']=1000*(municipal.agro_ocupados_2017+rho*5000)/(municipal.pop_2022+5000); municipal['score_agro_base']=qscore(municipal.agro_1000_suavizado); municipal['score_pop_escala']=(100*(np.log(municipal.pop_2022.clip(lower=5000))-np.log(5000))/(np.log(100000)-np.log(5000))).clip(0,100)
    if municipal.rais_2024.notna().any():
        rr=municipal.rais_2024.sum()/municipal.pop_2022.sum(); municipal['rais_1000_suavizado']=1000*(municipal.rais_2024+rr*5000)/(municipal.pop_2022+5000); municipal['score_rais']=qscore(municipal.rais_1000_suavizado); municipal['score_mao_obra']=.45*municipal.score_agro_base+.35*municipal.score_rais+.20*municipal.score_pop_escala
    else: municipal['score_rais']=np.nan; municipal['score_mao_obra']=(.45*municipal.score_agro_base+.20*municipal.score_pop_escala)/.65
    municipal.to_parquet(x.processed/'mao_obra_municipios_preliminar.geoparquet',index=False)
    hexes=gpd.read_file(x.processed/'bahia_hex_5km.geojson').rename(columns={'id_hex':'id'}); farms=gpd.read_parquet(x.processed/'fazendas_georeferenciadas.geoparquet'); hs=unit_stats(hexes,municipal,'id'); fs=unit_stats(farms,municipal,'id_fazenda')
    master=pd.read_parquet(x.processed/'assessments.parquet').drop(columns=[c for c in hs if c!='id' and c in pd.read_parquet(x.processed/'assessments.parquet').columns],errors='ignore').merge(hs,on='id',how='left'); master['mao_obra_infra_score']=master.mao_obra_score; master['mao_obra_infra_coverage_pct']=100.; master['mao_obra_infra_confidence']=master.mao_obra_confidence; master['mao_obra_infra_status']='preliminary'; master['mao_obra_infra_source_key']='ibge_censo_agro_2017+ibge_censo_2022'; master['mao_obra_infra_method_version']='MAO_OBRA_MUNICIPAL_v1_PRELIM'; master['mao_obra_infra_alert_level']='warning'; master['mao_obra_infra_alert_flags']=master.mao_obra_alertas; master.to_parquet(x.processed/'assessments.parquet',index=False)
    farms=farms.drop(columns=[c for c in fs if c!='id_fazenda' and c in farms.columns],errors='ignore').merge(fs,on='id_fazenda',how='left'); farms['mao_obra_infra_score']=farms.mao_obra_score; farms['mao_obra_infra_coverage_pct']=100.; farms['mao_obra_infra_confidence']=farms.mao_obra_confidence; farms['mao_obra_infra_status']='preliminary'; farms['mao_obra_infra_source_key']='ibge_censo_agro_2017+ibge_censo_2022'; farms['mao_obra_infra_method_version']='MAO_OBRA_MUNICIPAL_v1_PRELIM'; farms['mao_obra_infra_alert_level']='warning'; farms['mao_obra_infra_alert_flags']=farms.mao_obra_alertas; farms.to_parquet(x.processed/'fazendas_georeferenciadas.geoparquet',index=False)
    hs.to_parquet(x.processed/'mao_obra_hex_stats.parquet',index=False); fs.to_parquet(x.processed/'mao_obra_fazendas_stats.parquet',index=False); print(f'Mão de obra preliminar: {len(hs):,} hexágonos; {len(fs):,} imóveis.')
if __name__=='__main__': main()
