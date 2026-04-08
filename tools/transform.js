const fs = require("fs");
let html = fs.readFileSync("index.html", "utf8");
// Old D-letter: D_old = diff1*4 + diff2+1 (diff2 uses -1=yoon,0,+1=dak,+2=handak)
// New D: (3*d1+d2+6)%6 where d1=unicode diff1, d2=unicode diff2
// Encoding: chr(48 + D*6 + (tier-1) [+ 36 if on-yomi])
const oldToNewD = { 0: 5, 2: 1, 4: 2, 5: 3, 6: 4 };

// Only transform inside the data array, not the entire file
const dataMatch = html.match(/"data":\[\n([\s\S]*?)\n\]/);
if (!dataMatch) { console.error("Could not find data array"); process.exit(1); }

const dataStart = dataMatch.index + '"data":[\n'.length;
const dataEnd = dataStart + dataMatch[1].length;
let dataSection = dataMatch[1];

let saved = 0;
dataSection = dataSection.replace(/([1-6])([a-lA-L])/g, (m, digit, letter) => {
  const tier = parseInt(digit);
  const lc = letter.charCodeAt(0);
  const isOn = lc >= 65 && lc <= 76;
  const oldD = isOn ? lc - 65 : lc - 97;
  if (!(oldD in oldToNewD)) return m;
  const newD = oldToNewD[oldD];
  const idx = newD * 6 + (tier - 1);
  const ch = String.fromCharCode(48 + (isOn ? idx + 36 : idx));
  saved++;
  return ch === '\\' ? '\\\\' : ch;
});

html = html.substring(0, dataStart) + dataSection + html.substring(dataEnd);
fs.writeFileSync("index.html", html);
console.log("Saved:", saved, "chars");

// Verify
const h2k = s => [...s].map(c => { var p = c.charCodeAt(0); return p >= 0x3041 && p <= 0x3096 ? String.fromCharCode(p + 0x60) : c }).join("");
const kana = "あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわん";
const hd = ["", ...kana], rw = [...kana].slice(0, -1);

function Xold(s,pf){var pk=h2k(pf),r=[],p=0;while(p<s.length){
var i=p;while(i<s.length&&s.charCodeAt(i)>=0x3400)i++;
var d=i;while(d<s.length){var dc=s.charCodeAt(d);
if(dc>=0x3400||(dc>47&&dc<58)||(dc>64&&dc<71)||(dc>96&&dc<103))break;d++}
var dc=d<s.length?s.charCodeAt(d):0,rd=s.substring(i,d),e,t;
if(dc>64&&dc<71){e=d+1;while(e<s.length&&s.charCodeAt(e)<0x3400)e++;
t=pk+rd+(dc-64)+s.substring(d+1,e)}
else if(dc>96&&dc<103){e=d+1;while(e<s.length&&s.charCodeAt(e)<0x3400)e++;
t=pf+rd+(dc-96)+s.substring(d+1,e)}
else if(dc>47&&dc<58){var dl=d+1<s.length?s.charCodeAt(d+1):0,dv=-1;
if(dl>96&&dl<109)dv=dl-97;else if(dl>64&&dl<77)dv=dl-65;
if(dv>=0){var on=dl>64&&dl<77?1:0,d1=(dv/4|0),d2=dv%4-1,b=on?pk:pf,pr='';
for(var ci=0;ci<b.length;ci++){var dd=ci===0?d1:d2;
pr+=dd===0?b[ci]:String.fromCharCode(b.charCodeAt(ci)+dd)}
e=d+2;while(e<s.length&&s.charCodeAt(e)<0x3400)e++;
t=pr+rd+(dc-48)+s.substring(d+2,e)}else{e=d+1;
while(e<s.length&&s.charCodeAt(e)<0x3400)e++;t=rd+s.substring(d,e)}}
else{e=d+1;while(e<s.length&&s.charCodeAt(e)<0x3400)e++;t=rd+s.substring(d,e)}
for(var k=p;k<i;k++)r.push(s[k]+t);p=e}return r}

function Xnew(s,pf){
var pk=h2k(pf),r=[],p=0;while(p<s.length){
var i=p;while(i<s.length&&s.charCodeAt(i)>=0x3400)i++;
var d=i;while(d<s.length&&s.charCodeAt(d)>=128)d++;
var dc=d<s.length?s.charCodeAt(d):0,rd=s.substring(i,d),e,t;
var ti=dc>=48&&dc<120?dc-48:-1;
if(ti>=0){var on=ti>=36?1:0,idx=ti%36,Dv=(idx/6|0),tr=idx%6+1,d2=(Dv+1)%3-1,d1=(Dv-d2)/3%2;
var b=on?pk:pf,pr='';
for(var ci=0;ci<b.length;ci++)pr+=String.fromCharCode(b.charCodeAt(ci)+(ci?d2:d1));
e=d+1;while(e<s.length&&s.charCodeAt(e)>=128&&s.charCodeAt(e)<0x3400)e++;
t=pr+rd+tr+s.substring(d+1,e)}
else{e=d+1;while(e<s.length&&s.charCodeAt(e)>=128&&s.charCodeAt(e)<0x3400)e++;
t=rd+s.substring(d,e)}
for(var k=p;k<i;k++)r.push(s[k]+t);p=e}return r}

const {execSync} = require("child_process");
const oldHtml = execSync("git show HEAD:index.html").toString();
const oldData = JSON.parse("[" + oldHtml.match(/"data":\[([\s\S]*?)\]/)[1] + "]");
const newData = JSON.parse("[" + html.match(/"data":\[([\s\S]*?)\]/)[1] + "]");

let diffs = 0;
rw.forEach((rl, ri) => {
  const oc = (oldData[ri]||"").replace(/,(\d+)/g,(_,n)=>",".repeat(+n+1)).split(",");
  const nc = (newData[ri]||"").replace(/,(\d+)/g,(_,n)=>",".repeat(+n+1)).split(",");
  hd.forEach((cl, ci) => {
    const pf = rl + cl;
    const o = Xold(oc[ci]||"", pf).sort().join("|");
    const n = Xnew(nc[ci]||"", pf).sort().join("|");
    if (o !== n) {
      diffs++;
      if (diffs <= 5) console.log("DIFF", pf, "\nO:", o.substring(0,150), "\nN:", n.substring(0,150));
    }
  });
});
console.log("Diffs:", diffs);
