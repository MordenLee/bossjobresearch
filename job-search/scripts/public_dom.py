"""Read visible ordinary DOM and open shadow roots; never enter closed roots."""
ROOTS_JS=r'''
const roots=[document],seen=new Set(roots);
for(let i=0;i<roots.length&&roots.length<128;i++){
 for(const e of roots[i].querySelectorAll('*'))if(e.shadowRoot&&!seen.has(e.shadowRoot)){seen.add(e.shadowRoot);roots.push(e.shadowRoot);}
}
const visible=e=>!!e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden'&&getComputedStyle(e).display!=='none';
const queryAll=s=>roots.flatMap(r=>[...r.querySelectorAll(s)]);
const shadowText=r=>[...r.children].filter(e=>!['STYLE','SCRIPT','NOSCRIPT'].includes(e.tagName)&&visible(e)).map(e=>e.innerText||'').join('\n');
const light=document.body?.innerText||'';
const text=[light,...roots.slice(1).map(shadowText)].filter(Boolean).join('\n');
const loginDialogs=queryAll('[role="dialog"],dialog[open]').filter(e=>visible(e)&&[...e.querySelectorAll('input[type="password"]')].some(visible)).map(e=>e.innerText);
'''
PUBLIC_READINESS_JS=ROOTS_JS+r'''return {url:location.href,title:document.title,text,ready:document.readyState,open_shadow_roots:roots.length-1};'''
LOGIN_DIALOG_JS=ROOTS_JS+r'''return loginDialogs.join('\n');'''
PUBLIC_SNAPSHOT_JS=ROOTS_JS+r'''return {
 url:location.href,title:document.title,text,light_dom_text:light,html:document.body?.outerHTML||'',
 open_shadow_roots:roots.length-1,shadow_scan_limited:roots.length>=128,
 shadow_dom:roots.slice(1).map(r=>({host:r.host.tagName,text:shadowText(r),html:r.innerHTML})),
 component_hosts:[...new Set(queryAll('*').map(e=>e.tagName.toLowerCase()).filter(t=>t.includes('-')))].slice(0,30),
 scripts:[...document.scripts].map(e=>e.src).filter(Boolean),
 resources:performance.getEntriesByType('resource').map(e=>e.name).filter(u=>/\.(js|css)(\?|$)/i.test(u)).map(u=>u.split('?')[0]).slice(0,200),
 frames:queryAll('iframe[src]').map(e=>e.src),
 inputs:queryAll('input').map(e=>({html:e.outerHTML,visible:visible(e)})),
 buttons:queryAll('button').map(e=>({text:e.innerText,html:e.outerHTML.slice(0,1000)})),
 links:queryAll('a[href]').map(e=>({text:e.innerText,url:e.href,raw_href:e.getAttribute('href'),visible:visible(e),in_shadow:e.getRootNode()!==document})),
 nav:queryAll('li,span,div').filter(e=>e.children.length===0&&/^(社会招聘|职位|全部职位)$/.test(e.innerText.trim())).map(e=>e.outerHTML),
 login_dialogs:loginDialogs
};'''
