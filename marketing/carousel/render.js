const { chromium } = require('playwright');
const http = require('http');
const fs = require('fs');
const html = fs.readFileSync('carousel.html','utf8');
const server = http.createServer((req,res)=>{res.writeHead(200,{'Content-Type':'text/html'});res.end(html);}).listen(8920);
(async()=>{
  const b = await chromium.launch({executablePath:'/opt/pw-browsers/chromium'});
  const p = await b.newPage({viewport:{width:1080,height:1350},deviceScaleFactor:2});
  const errs=[]; p.on('pageerror',e=>errs.push(e.message));
  await p.goto('http://127.0.0.1:8920/');
  await p.waitForTimeout(2500);   // let webfont land
  const fontOK = await p.evaluate(()=>document.fonts.check('900 100px Inter'));
  for (let i=1;i<=7;i++){
    const el = await p.$('#s'+i);
    await el.screenshot({path:`slide${i}.png`});
  }
  // overflow audit: anything escaping the 120px safe margin?
  const bad = await p.evaluate(()=>{
    const out=[];
    document.querySelectorAll('.slide').forEach((s,idx)=>{
      const sb=s.getBoundingClientRect();
      s.querySelectorAll('.inner *').forEach(el=>{
        const r=el.getBoundingClientRect();
        const L=r.left-sb.left, R=sb.right-r.right, T=r.top-sb.top, B=sb.bottom-r.bottom;
        if(L<118||R<118||T<114||B<86) out.push(`s${idx+1}: ${el.className||el.tagName} L${Math.round(L)} R${Math.round(R)} T${Math.round(T)} B${Math.round(B)}`);
      });
    });
    return out.slice(0,12);
  });
  await b.close(); server.close();
  console.log('Inter loaded:', fontOK);
  console.log(errs.length?('ERRORS '+errs.join('|')):'no page errors');
  console.log(bad.length?('MARGIN VIOLATIONS:\n'+bad.join('\n')):'all content inside safe margins');
})();
