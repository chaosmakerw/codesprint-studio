/* Original UI themes. No remote resources or third-party theme assets. */
(() => {
  'use strict';
  const themes = [
    {id:'studio',name:'Studio',label:'黑白工作台',description:'直线、留白与清晰的信息层级。',sample:'工作台 / 01'},
    {id:'paper',name:'Paper',label:'书页编辑室',description:'米白纸页、衬线标题与安静的阅读节奏。',sample:'书页 · Notes'},
    {id:'terminal',name:'Terminal',label:'深绿终端',description:'等宽文字、细线与克制的终端光感。',sample:'> recall_ready'},
    {id:'dusk',name:'Dusk',label:'冷色夜读',description:'柔和圆角与低亮度，留给夜间专注。',sample:'夜读 / Quiet mode'}
  ];
  let current = 'studio';
  try { const saved=localStorage.getItem('codesprint-studio:theme'); if(themes.some(t=>t.id===saved)) current=saved; } catch (_) {}
  function apply(id, persist=true) {
    if(!themes.some(t=>t.id===id))return;
    current=id;document.documentElement.dataset.theme=id;
    document.documentElement.style.colorScheme=['terminal','dusk'].includes(id)?'dark':'light';
    if(persist)try{localStorage.setItem('codesprint-studio:theme',id);}catch(_){}
    document.querySelectorAll('[data-theme-id]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.themeId===id)));
  }
  apply(current,false);
  function mount() {
    const dialog=document.createElement('dialog');dialog.className='theme-dialog';dialog.id='themeDialog';dialog.setAttribute('aria-labelledby','themeDialogTitle');
    const heading=document.createElement('div');heading.className='dialog-heading';
    const eyebrow=document.createElement('span');eyebrow.className='eyebrow';eyebrow.textContent='APPEARANCE / A PLACE TO FOCUS';
    const close=document.createElement('button');close.className='dialog-close';close.type='button';close.textContent='×';close.setAttribute('aria-label','关闭外观选择');close.addEventListener('click',()=>dialog.close());heading.append(eyebrow,close);
    const title=document.createElement('h2');title.id='themeDialogTitle';title.textContent='选择你的学习空间。';
    const description=document.createElement('p');description.textContent='主题会保存在当前浏览器，资料与练习记录保持不变。';
    const grid=document.createElement('div');grid.className='theme-grid';
    for(const theme of themes) {
      const button=document.createElement('button');button.type='button';button.className='theme-card';button.dataset.themeId=theme.id;button.setAttribute('aria-pressed',String(theme.id===current));
      const sample=document.createElement('span');sample.className='theme-sample';sample.dataset.previewTheme=theme.id;
      const sampleTitle=document.createElement('b');sampleTitle.textContent=theme.sample;const lines=document.createElement('span');lines.className='sample-lines';lines.setAttribute('aria-hidden','true');sample.append(sampleTitle,lines);
      const label=document.createElement('strong');label.textContent=theme.name+' / '+theme.label;
      const detail=document.createElement('small');detail.textContent=theme.description;button.append(sample,label,detail);button.addEventListener('click',()=>apply(theme.id));grid.append(button);
    }
    const done=document.createElement('button');done.type='button';done.className='button';done.textContent='开始专注 →';done.addEventListener('click',()=>dialog.close());dialog.append(heading,title,description,grid,done);document.body.append(dialog);
    document.querySelectorAll('[data-theme-open]').forEach(button=>button.addEventListener('click',()=>dialog.showModal()));
    dialog.addEventListener('click',event=>{if(event.target===dialog){const rect=dialog.getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)dialog.close();}});
  }
  window.CodeSprintThemes={apply,current:()=>current};
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',mount,{once:true});else mount();
})();
