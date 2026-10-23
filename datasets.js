export const DATASETS = Object.freeze({
  original: {label:'Original demo', root:'./data/league/'},
  'enriched-v1': {label:'Enriched analysis', root:'./data/league/', analysisRoot:'./data/league-enriched-v1/'}
});
export function datasetFrom(value){return Object.hasOwn(DATASETS,value)?value:'enriched-v1';}
