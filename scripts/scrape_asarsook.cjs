// Collects asarsook's public web preview into posts.jsonl: id, date, text, photo urls.
const fs=require('fs');
const out=fs.createWriteStream('posts.jsonl');
const clean=s=>s.replace(/<br\s*\/?>/g,'\n').replace(/<[^>]+>/g,'').replace(/&nbsp;/g,' ').replace(/&#33;/g,'!').replace(/&quot;/g,'"').replace(/&amp;/g,'&').replace(/&#39;/g,"'").trim();
(async()=>{
  let before='', n=0;
  for(;;){
    const r=await fetch('https://t.me/s/asarsook'+(before?'?before='+before:''),{headers:{'User-Agent':'Mozilla/5.0'}});
    const h=await r.text();
    const blocks=h.split('class="tgme_widget_message_wrap').slice(1);
    if(!blocks.length)break;
    const ids=[];
    for(const b of blocks){
      const id=+(b.match(/data-post="asarsook\/(\d+)"/)||[])[1]; if(!id)continue; ids.push(id);
      const t=b.match(/tgme_widget_message_text[^>]*>([\s\S]*?)<\/div>/);
      const date=(b.match(/datetime="([^"]+)"/)||[])[1];
      const photos=[...b.matchAll(/tgme_widget_message_photo_wrap[^>]*background-image:url\('([^']+)'/g)].map(m=>m[1]);
      out.write(JSON.stringify({id,date,text:t?clean(t[1]):'',photos})+'\n'); n++;
    }
    const min=Math.min(...ids); if(!isFinite(min)||min<=1||String(min)===before)break;
    before=String(min);
    await new Promise(r=>setTimeout(r,1500));
  }
  out.end(); console.log('posts',n);
})();
