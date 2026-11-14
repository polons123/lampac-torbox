const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync(require('path').join(__dirname,'../../Modules/TorBox/plugin.js'),'utf8');
const block=source.slice(source.indexOf('  function openSeasonUpdates(back) {'),source.indexOf('  function Library(object) {'));
let menus=[],activity={},forgot=[],deleted=false,calls=[];
const watch={id:'A'.repeat(64),title:'Mock Show',season:1,episode:2,selection:'720p'};
const ctx={generation:1,begin:()=>1,keyIdentity:()=> 'fixture',escaped:s=>s,Lampa:{Activity:{active:()=>activity},Noty:{show:()=>{}}},
 request:(path,body,success,failure)=>{
  calls.push(path);
  if(path==='series/followed')success({items:deleted?[]:[watch]});
  else if(path.startsWith('series/updates?'))failure();
  else if(path==='series/forget'){forgot.push(body.id);deleted=true;success({success:true});}
  else throw Error('unexpected request '+path);
 },choose:(title,items)=>menus.push(items)};
vm.createContext(ctx);vm.runInContext(block,ctx);
ctx.openSeasonUpdates(()=>{});
assert(menus[0].some(i=>i.title==='Убрать из списка обновлений'));
assert(!calls.some(p=>p.startsWith('series/updates')));
console.log('PASS removal is available before JacRed lookup');
menus[0][0].run();
const action=menus[menus.length-1].find(i=>i.title==='Убрать из списка обновлений');assert(action);
console.log('PASS failed update lookup retains removal action');
action.run();assert.deepStrictEqual(forgot,[watch.id]);
console.log('PASS removal sends correct season ID and refreshes list');
