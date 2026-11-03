"""Observed Mengniu-style Beisen inline list. DOM reads only; no request API."""

INLINE_LIST_JS = r'''const list=document.querySelector('#__JobAdList__');
return {url:location.href,title:document.title,
 input:document.querySelector('#__keywords__')?.value??null,
 list_text:list?.innerText||'',
 rows:[...(list?.children||[])].filter(e=>e.matches('.item')).map((e,i)=>({
 index:i+1,title:e.querySelector('.top .left .t')?.innerText.trim()||'',
 uid:e.querySelector('a.apply[data-idx]')?.getAttribute('data-idx')||'',
 metadata:[...e.querySelectorAll('.top .left .b span')].map(s=>s.innerText.trim()).filter(Boolean),
 expanded:!!e.querySelector('.con')?.getClientRects().length,
 description:[...e.querySelectorAll('.con .block')].map(s=>s.innerText.trim()).filter(Boolean).join('\n\n'),
 expand_text:e.querySelector('.top .right .zk')?.innerText.trim()||''
 }))};'''
