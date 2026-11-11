/*
 * SAF CACAU BAHIA — FATOR CLIMÁTICO E DEMANDA DE IRRIGAÇÃO
 * Período: 01/01/1996–31/12/2025 (30 anos completos)
 * Versão metodológica: CLIMA_IRRIGACAO_V1_1996_2025
 *
 * COMO USAR (em duas execuções deliberadamente leves e auditáveis)
 *
 * 1) Defina ASSET_ROOT com o caminho de uma ImageCollection sua no Earth Engine.
 * 2) Com MODE = 'ANNUAL', clique Run. Serão criadas 30 tarefas, uma por ano.
 *    Execute-as. Cada tarefa produz bandas anuais na grade CHIRPS de 0,05°.
 * 3) Quando todas concluírem, mude MODE para 'FINAL' e execute novamente.
 *    Será criada UMA tarefa com os indicadores e a nota climática finais.
 *
 * Esta separação evita um grafo único com 10.958 dias e torna o processo
 * reexecutável por ano. Nenhuma estatística de hexágonos ou fazendas é feita no
 * Earth Engine: os rasters finais serão cruzados localmente uma única vez.
 *
 * FONTES
 * - CHIRPS v3 DAILY_RNL: UCSB-CHC/CHIRPS/V3/DAILY_RNL, 0,05°; chuva final.
 * - ERA5-Land DAILY_AGGR: ECMWF/ERA5_LAND/DAILY_AGGR, ~0,1°; atmosfera/ET0.
 *
 * CENÁRIO AGRONÔMICO PADRÃO (versionado; não é oferta de água)
 * - cacau adulto em SAF sombreado: Kc = 1,10
 * - irrigação por gotejamento: eficiência = 0,90
 * - chuva efetiva mensal: USDA-SCS; sem armazenamento de água no solo
 * - P80: percentil 80 das 30 lâminas anuais brutas
 *
 * IMPORTANTE: o resultado é triagem climática regional. Não substitui estação
 * local, projeto de captação/outorga ou dimensionamento hidráulico.
 */

// ------------------------- CONFIGURAÇÃO EDITÁVEL -------------------------
var MODE = 'ANNUAL'; // 'ANNUAL' primeiro; depois 'FINAL'.
// ImageCollection criada no painel Assets antes da etapa ANNUAL.
var ASSET_ROOT = 'projects/ee-diogossous/assets/saf_cacau_clima_1996_2025_annual';
var EXPORT_FOLDER = 'Relatorio cacau'; // usado somente se preferir Drive.
var EXPORT_TO_DRIVE = false; // recomendado false: Asset facilita a consolidação.
var START_YEAR = 1996;
var END_YEAR = 2025;
var KC = 1.10;
var IRRIGATION_EFFICIENCY = 0.90;
var DRY_MONTH_THRESHOLD_MM = 60;
// A exportação usa 0,05° (~5,56 km): mantém a chuva CHIRPS na sua maior
// resolução disponível. Variáveis ERA5-Land continuam tendo suporte físico
// nativo de ~0,1° (~11 km), mesmo quando alinhadas à grade comum.
var EXPORT_SCALE_M = 5566;
var EXPORT_CRS = 'EPSG:4326';
var VERSION = 'CLIMA_IRRIGACAO_V1_1996_2025';

// Limite estadual oficial de referência no GEE.
var bahia = ee.FeatureCollection('FAO/GAUL/2015/level1')
  .filter(ee.Filter.eq('ADM0_NAME', 'Brazil'))
  .filter(ee.Filter.eq('ADM1_NAME', 'Bahia'))
  .geometry();

var periodStart = ee.Date.fromYMD(START_YEAR, 1, 1);
var periodEnd = ee.Date.fromYMD(END_YEAR + 1, 1, 1);
var years = ee.List.sequence(START_YEAR, END_YEAR);

// Apenas produtos finais. DAILY_RNL reparte os totais CHIRPS finais de pentada
// usando ERA5, mas preserva o total CHIRPS v3 em cada pentada.
var chirps = ee.ImageCollection('UCSB-CHC/CHIRPS/V3/DAILY_RNL')
  .filterDate(periodStart, periodEnd)
  .select('precipitation');
var era = ee.ImageCollection('ECMWF/ERA5_LAND/DAILY_AGGR')
  .filterDate(periodStart, periodEnd);

// Pré-flight: deve mostrar 30 anos completos, 360 meses e as bandas exigidas.
print('Versão / período', VERSION, periodStart.format('YYYY-MM-dd'), periodEnd.advance(-1, 'day').format('YYYY-MM-dd'));
print('Dias CHIRPS v3 no período (esperado 10.958)', chirps.size());
print('Dias ERA5-Land no período (esperado 10.958)', era.size());
print('Bandas ERA5-Land disponíveis', era.first().bandNames());
print('Limite Bahia usado', bahia);

// A grade final é a do CHIRPS (0,05°), pois a precipitação é o dado de maior
// detalhe. As variáveis atmosféricas ERA5-Land são reamostradas somente para
// alinhamento geométrico; isto não aumenta sua resolução física nativa.
var eraProjection = ee.Image(era.first()).select('temperature_2m').projection();
var demEra = ee.ImageCollection('COPERNICUS/DEM/GLO30').select('DEM').mosaic()
  .reduceResolution({reducer: ee.Reducer.mean(), maxPixels: 4096})
  .reproject({crs: eraProjection})
  .rename('elevation_m')
  .clip(bahia);
var latitudeRad = ee.Image.pixelLonLat().select('latitude')
  .multiply(Math.PI / 180)
  .reproject({crs: eraProjection});

function saturationVaporPressure(tempC) {
  return ee.Image(0.6108).multiply(tempC.multiply(17.27).divide(tempC.add(237.3)).exp());
}

// FAO-56 Penman–Monteith diário. A radiação líquida é derivada de Rs, Tmax,
// Tmin, ponto de orvalho, latitude e altitude; não usamos potential_evaporation
// do ERA5, pois ela possui convenções/ressalvas próprias.
function etoFao56Daily(image) {
  var tmean = image.select('temperature_2m').subtract(273.15);
  var tmax = image.select('temperature_2m_max').subtract(273.15);
  var tmin = image.select('temperature_2m_min').subtract(273.15);
  var tdew = image.select('dewpoint_temperature_2m').subtract(273.15);
  var pressureKpa = image.select('surface_pressure').divide(1000);
  var wind10 = image.select('u_component_of_wind_10m').pow(2)
    .add(image.select('v_component_of_wind_10m').pow(2)).sqrt();
  var wind2 = wind10.multiply(4.87 / Math.log(67.8 * 10 - 5.42));

  // Rs chega em J m-2 d-1; FAO-56 usa MJ m-2 d-1. Valores negativos conhecidos
  // de bandas acumuladas são anulados e contabilizados no controle de qualidade.
  var rs = image.select('surface_solar_radiation_downwards_sum').max(0).divide(1e6);
  // Mantido inteiramente no servidor: não usar getInfo() dentro de map().
  var doy = ee.Image.constant(ee.Number(ee.Date(image.get('system:time_start'))
    .getRelative('day', 'year')).add(1));
  var seasonalAngle = doy.multiply(2 * Math.PI / 365);
  var dr = seasonalAngle.cos().multiply(0.033).add(1);
  var solarDeclination = seasonalAngle.subtract(1.39).sin().multiply(0.409);
  var ws = latitudeRad.tan().multiply(solarDeclination.tan()).multiply(-1).acos();
  var ra = ws.multiply(24 * 60 / Math.PI).multiply(0.0820)
    .multiply(dr.multiply(latitudeRad.sin()).multiply(solarDeclination.sin())
      .add(latitudeRad.cos().multiply(solarDeclination.cos()).multiply(ws.sin())));
  var rso = ra.multiply(demEra.multiply(0.00002).add(0.75));
  var rns = rs.multiply(0.77);
  var ea = saturationVaporPressure(tdew);
  var rnl = tmax.add(273.16).pow(4).add(tmin.add(273.16).pow(4)).divide(2)
    .multiply(4.903e-9)
    .multiply(ee.Image(0.34).subtract(ea.sqrt().multiply(0.14)))
    .multiply(rs.divide(rso.max(0.001)).min(1.0).multiply(1.35).subtract(0.35));
  var rn = rns.subtract(rnl);
  var delta = saturationVaporPressure(tmean).multiply(4098).divide(tmean.add(237.3).pow(2));
  var gamma = pressureKpa.multiply(0.000665);
  var numerator = delta.multiply(0.408).multiply(rn)
    .add(gamma.multiply(900).divide(tmean.add(273)).multiply(wind2).multiply(saturationVaporPressure(tmax).add(saturationVaporPressure(tmin)).divide(2).subtract(ea)));
  var denominator = delta.add(gamma.multiply(ee.Image(1).add(wind2.multiply(0.34))));
  return numerator.divide(denominator).max(0).rename('eto_mm_d')
    .copyProperties(image, ['system:time_start']);
}

function chirpsMonthlyNative(start, end) {
  // Não agregar CHIRPS à grade ERA5: cada pixel mensal preserva 0,05°.
  return chirps.filterDate(start, end).sum().rename('p_mm');
}

// USDA-SCS mensal. Mantido explícito como parâmetro metodológico para que o
// futuro módulo de segurança hídrica possa substituir por balanço de solo/CAD.
function effectiveRainUSDA(pMm) {
  var low = pMm.multiply(ee.Image(125).subtract(pMm.multiply(0.2))).divide(125);
  var high = pMm.multiply(0.1).add(125);
  return low.where(pMm.gt(250), high).max(0).min(pMm).rename('pe_mm');
}

function annualClimate(year) {
  year = ee.Number(year);
  var start = ee.Date.fromYMD(year, 1, 1);
  var end = start.advance(1, 'year');
  var eraYear = era.filterDate(start, end);
  var etoDaily = eraYear.map(etoFao56Daily);
  var months = ee.List.sequence(1, 12);

  var monthList = months.map(function(month) {
    month = ee.Number(month);
    var ms = start.advance(month.subtract(1), 'month');
    var me = ms.advance(1, 'month');
    var eto = etoDaily.filterDate(ms, me).sum().rename('eto_mm');
    var p = chirpsMonthlyNative(ms, me);
    var pe = effectiveRainUSDA(p);
    var irr = eto.multiply(KC).subtract(pe).max(0).divide(IRRIGATION_EFFICIENCY).rename('irr_bruta_mm');
    return ee.Image.cat([eto, p, pe, irr]).set('month', month);
  });
  var monthly = ee.ImageCollection.fromImages(monthList);

  // Maior sequência mensal com P < 60 mm; calculada por pixel e por ano.
  // A sequência seca é calculada na própria malha CHIRPS.
  var zero = ee.Image.constant(0).reproject({crs: chirps.first().projection()}).clip(bahia);
  var dryState = ee.Image(monthList.iterate(function(monthImage, state) {
    monthImage = ee.Image(monthImage);
    state = ee.Image(state);
    var current = state.select('dry_run').add(1)
      .where(monthImage.select('p_mm').gte(DRY_MONTH_THRESHOLD_MM), 0)
      .rename('dry_run');
    var maximum = state.select('dry_max').max(current).rename('dry_max');
    return current.addBands(maximum);
  }, zero.rename('dry_run').addBands(zero.rename('dry_max'))));

  var tmean = eraYear.select('temperature_2m').mean().subtract(273.15).rename('tmean_c');
  return ee.Image.cat([
      monthly.select('eto_mm').sum().rename('eto_mm'),
      monthly.select('p_mm').sum().rename('p_mm'),
      monthly.select('pe_mm').sum().rename('pe_mm'),
      monthly.select('irr_bruta_mm').sum().rename('irr_bruta_mm'),
      dryState.select('dry_max').rename('dryseq_months'),
      tmean
    ])
    .clip(bahia)
    .set({
      year: year,
      period_start: start.format('YYYY-MM-dd'),
      period_end: end.advance(-1, 'day').format('YYYY-MM-dd'),
      kc: KC,
      irrigation_efficiency: IRRIGATION_EFFICIENCY,
      dry_month_threshold_mm: DRY_MONTH_THRESHOLD_MM,
      method_version: VERSION
    });
}

// Validação síncrona e pequena (30 contagens de metadados), executada ANTES de
// criar as tarefas. Se faltar um dia, a execução para: não se exporta média
// incompleta como se fosse normal climática de 30 anos.
function preflightOrThrow() {
  var requiredBands = [
    'temperature_2m', 'temperature_2m_min', 'temperature_2m_max',
    'dewpoint_temperature_2m', 'u_component_of_wind_10m',
    'v_component_of_wind_10m', 'surface_pressure',
    'surface_solar_radiation_downwards_sum'
  ];
  var availableBands = era.first().bandNames().getInfo();
  var missingBands = requiredBands.filter(function(name) {
    return availableBands.indexOf(name) === -1;
  });
  if (missingBands.length) {
    throw new Error('Pré-flight interrompido: bandas ERA5 ausentes: ' + missingBands.join(', '));
  }
  var coverage = ee.FeatureCollection(years.map(function(year) {
    var start = ee.Date.fromYMD(year, 1, 1);
    var end = start.advance(1, 'year');
    return ee.Feature(null, {
      year: year,
      chirps_days: chirps.filterDate(start, end).size(),
      era_days: era.filterDate(start, end).size()
    });
  })).getInfo();
  var gaps = coverage.features.filter(function(feature) {
    var year = feature.properties.year;
    var expected = (year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0)) ? 366 : 365;
    return feature.properties.chirps_days !== expected || feature.properties.era_days !== expected;
  });
  print('Pré-flight anual', coverage);
  if (gaps.length) {
    throw new Error('Pré-flight interrompido: cobertura diária incompleta em ' + JSON.stringify(gaps));
  }
  print('Pré-flight aprovado: 30 anos completos e bandas ERA5 confirmadas.');
}

function exportAnnualTasks() {
  preflightOrThrow();
  years.getInfo().forEach(function(year) {
    var image = annualClimate(year).toFloat();
    var description = 'saf_cacau_bahia_clima_anual_' + year + '_v1';
    var common = {
      image: image,
      description: description,
      region: bahia,
      scale: EXPORT_SCALE_M,
      crs: EXPORT_CRS,
      maxPixels: 1e10
    };
    if (EXPORT_TO_DRIVE) {
      Export.image.toDrive(Object.assign(common, {
        folder: EXPORT_FOLDER,
        fileNamePrefix: description,
        fileFormat: 'GeoTIFF',
        formatOptions: {cloudOptimized: true}
      }));
    } else {
      Export.image.toAsset(Object.assign(common, {
        assetId: ASSET_ROOT + '/annual_' + year
      }));
    }
  });
  print('Foram criadas 30 tarefas anuais. Só execute a etapa FINAL após todas ficarem COMPLETED.');
}

function scorePiecewise(x, xs, ys, lowScore, highScore) {
  var out = ee.Image.constant(lowScore);
  for (var i = 0; i < xs.length - 1; i++) {
    var slope = (ys[i + 1] - ys[i]) / (xs[i + 1] - xs[i]);
    var segment = x.subtract(xs[i]).multiply(slope).add(ys[i]);
    out = out.where(x.gte(xs[i]).and(x.lt(xs[i + 1])), segment);
  }
  return out.where(x.gte(xs[xs.length - 1]), highScore).clamp(0, 100);
}

function scoreSeasonality(x) {
  return ee.Image.constant(10)
    .where(x.lte(5), 30).where(x.lte(4), 50).where(x.lte(3), 70)
    .where(x.lte(2), 85).where(x.lte(1), 100)
    .rename('score_sazonalidade');
}

function exportFinalTask() {
  var annual = ee.ImageCollection(ASSET_ROOT)
    .filter(ee.Filter.gte('year', START_YEAR))
    .filter(ee.Filter.lte('year', END_YEAR));
  print('Imagens anuais disponíveis (esperado 30)', annual.size());

  var tmean = annual.select('tmean_c').mean().rename('tmean_ann_c');
  var rain = annual.select('p_mm').mean().rename('rain_ann_mean_mm');
  var eto = annual.select('eto_mm').mean().rename('eto_ann_mean_mm');
  var irrigationMean = annual.select('irr_bruta_mm').mean().rename('irrigation_mean_mm');
  var irrigationP80 = annual.select('irr_bruta_mm').reduce(ee.Reducer.percentile([80]))
    .rename('irrigation_p80_mm');
  var drySeqP80 = annual.select('dryseq_months').reduce(ee.Reducer.percentile([80]))
    .rename('dryseq_p80_months');

  // P95 real da Tmax diária sobre os 30 anos. É uma tarefa simples porque não
  // carrega o cálculo de ET0; permanece na resolução nativa ERA5-Land.
  var tmaxP95 = era.select('temperature_2m_max').reduce(ee.Reducer.percentile([95]))
    .subtract(273.15).rename('tmax_p95_c');

  // VPD diário: es(T) - ea(Tdew), em kPa. Diagnóstico/alerta nesta V1.
  var vpdP90 = era.map(function(image) {
    var t = image.select('temperature_2m').subtract(273.15);
    var td = image.select('dewpoint_temperature_2m').subtract(273.15);
    return saturationVaporPressure(t).subtract(saturationVaporPressure(td)).max(0)
      .rename('vpd_kpa').copyProperties(image, ['system:time_start']);
  }).reduce(ee.Reducer.percentile([90])).rename('vpd_p90_kpa');

  var scoreThermal = scorePiecewise(tmean, [19, 20, 21, 23, 27, 29, 31, 32], [20, 40, 60, 100, 100, 80, 60, 40], 20, 20)
    .rename('score_termico');
  var scoreHeat = scorePiecewise(tmaxP95, [32, 34, 36, 38], [100, 75, 45, 20], 100, 20)
    .rename('score_carga_termica');
  var scoreDemand = scorePiecewise(irrigationP80, [700, 1000, 1300, 1600, 1900], [100, 80, 60, 40, 20], 100, 20)
    .rename('score_demanda_p80');
  var scoreSeason = scoreSeasonality(drySeqP80);
  var climateScore = scoreThermal.multiply(0.50).add(scoreHeat.multiply(0.20))
    .add(scoreDemand.multiply(0.20)).add(scoreSeason.multiply(0.10))
    .rename('clima_irrigacao_score').clamp(0, 100);
  var climateClass = ee.Image.constant(1)
    .where(climateScore.gte(35), 2).where(climateScore.gte(50), 3)
    .where(climateScore.gte(65), 4).where(climateScore.gte(80), 5)
    .rename('clima_classe_codigo').toByte();

  var result = ee.Image.cat([
      tmean, rain, eto, irrigationMean, irrigationP80, drySeqP80,
      tmaxP95, vpdP90, scoreThermal, scoreHeat, scoreDemand, scoreSeason,
      climateScore, climateClass
    ]).clip(bahia).set({
      method_version: VERSION,
      period_start: '1996-01-01',
      period_end: '2025-12-31',
      export_grid: 'CHIRPS v3 0.05 degree (~5.56 km)',
      era5_land_native_support: '~0.1 degree (~11.132 km); aligned only, not sharpened',
      precipitation_source: 'CHIRPS v3 DAILY_RNL 0.05 degree',
      kc: KC,
      irrigation_efficiency: IRRIGATION_EFFICIENCY,
      effective_rain_method: 'USDA-SCS monthly',
      score_formula: '0.50 thermal + 0.20 heat + 0.20 irrigation P80 + 0.10 seasonality',
      caveat: 'Climate screening only; water availability, outorga and CAD are excluded.'
    });

  Map.centerObject(bahia, 6);
  Map.addLayer(climateScore, {min: 0, max: 100, palette: ['#c73532', '#ee7d32', '#ffd54a', '#99bf55', '#2f874b']}, 'Nota climática (prévia)');
  Map.addLayer(irrigationP80, {min: 500, max: 2000, palette: ['#2f874b', '#ffd54a', '#ee7d32', '#c73532']}, 'Irrigação P80 (mm/ano)', false);
  print('Bandas finais', result.bandNames());

  var description = 'saf_cacau_bahia_clima_irrigacao_1996_2025_v1';
  Export.image.toAsset({
    image: result.toFloat(),
    description: description,
    assetId: ASSET_ROOT.replace('_annual', '_final'),
    region: bahia,
    scale: EXPORT_SCALE_M,
    crs: EXPORT_CRS,
    maxPixels: 1e10
  });
}

if (MODE === 'ANNUAL') {
  exportAnnualTasks();
} else if (MODE === 'FINAL') {
  exportFinalTask();
} else {
  throw new Error("MODE deve ser 'ANNUAL' ou 'FINAL'.");
}
