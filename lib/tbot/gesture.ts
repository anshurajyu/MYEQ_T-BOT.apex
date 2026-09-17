import type {NormalizedLandmark} from '@mediapipe/tasks-vision';
export function classifyHand(lm:NormalizedLandmark[]|undefined):{label:string;linear:number;angular:number}{
 if(!lm)return {label:'No hand — stopped',linear:0,angular:0};
 const up=[8,12,16,20].map(i=>lm[i].y<lm[i-2].y);
 if(up.every(Boolean))return {label:'Open palm — stopped',linear:0,angular:0};
 if(up[0]&&!up[1]&&!up[2]&&!up[3]){const x=1-lm[9].x;return x<.38?{label:'Turn left',linear:0,angular:.35}:x>.62?{label:'Turn right',linear:0,angular:-.35}:{label:'Forward',linear:.07,angular:0}}
 if(up[0]&&up[1]&&!up[2]&&!up[3])return {label:'Backward',linear:-.05,angular:0};
 return {label:'Unknown gesture — stopped',linear:0,angular:0};
}
