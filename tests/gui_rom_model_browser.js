/* Split regions for the same model retain a single drawer target and merge references. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const asset={kind:'3d model',media:'model',preview_settings:{format:'auto'},offset:64,size:128,label:'Synthetic',vertices:4,triangles:2,meshes:[{}]};
const context=vm.createContext({report:{regions:[
  {id:'bank',kind:'image',detected_asset:{kind:'image'},evidence:[asset],direct_function_refs:['reader']},
  {id:'split',kind:'3d model',detected_asset:asset,traced_function_refs:['loader']},
  {id:'code',kind:'code',detected_asset:asset},
]}});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/rom-assets.js'),'utf8'),context);
vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/decompnet/app/static/js/rom-model-browser.js'),'utf8'),context);
const rows=vm.runInContext('romModelRows(report)',context);
assert.equal(rows.length,1);
assert.equal(rows[0].refs,2);
assert.equal(rows[0].region,'bank');
assert.equal(rows[0].assetView,'3d model');
