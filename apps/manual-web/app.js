const $ = (s) => document.querySelector(s)
const h = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))
const state = {entries:[], categories:[], category:null, current:null, editing:null, editSteps:[], answer:null, question:''}
async function api(path, method='GET', body) {
  const headers = {'X-Manual-Request':'1'}
  if (body && !(body instanceof FormData)) { headers['Content-Type']='application/json'; body=JSON.stringify(body) }
  const response=await fetch('/api/v1/manuals'+path,{method,headers,body})
  const data=await response.json().catch(()=>({detail:'服务响应异常'}))
  if (!response.ok) throw new Error(Array.isArray(data.detail)?data.detail.map(e=>e.msg).join('；'):(data.detail || '请求失败'))
  return data
}
function notify(text) { $('#notice').textContent=text || ''; $('#notice').hidden=!text }
async function action(task,button) { if(button) button.disabled=true; try { await task() } catch(e){notify(e.message)} finally{if(button)button.disabled=false} }
const statusName = (e) => ({draft:'草稿',published:'已发布',superseded:'历史版本'}[e.status] || e.status)
const processName = (s) => ({new:'待补充内容',uploaded:'已归档，待提取',processing:'本地提取中',submitted:'MinerU 处理中',ready:'内容就绪',failed:'处理失败'}[s] || s)
const indexName = (s) => ({pending:'待建立',queued:'排队中',indexing:'建立中',indexed:'已建立',failed:'暂不可用，可重试'}[s] || s)
async function load() {
  [state.categories,state.entries]=await Promise.all([api('/categories'),api('/entries')])
  renderCatalog()
}
function renderCatalog() {
  $('#categories').innerHTML=[{id:null,name:'全部资料'},...state.categories].map(c=>`<button class="category ${state.category===c.id?'active':''}" data-category="${h(c.id||'')}">${h(c.name)}</button>`).join('')
  const q=$('#filter').value.trim().toLowerCase()
  const items=state.entries.filter(e=>(!state.category||e.category_id===state.category)&&(!q||`${e.title} ${e.product} ${e.version}`.toLowerCase().includes(q)))
  $('#count').textContent=`${items.length} 份`
  $('#list-label').textContent=state.categories.find(c=>c.id===state.category)?.name || '全部资料'
  $('#entries').innerHTML=items.map(e=>`<button class="entry-card ${state.current?.id===e.id?'active':''}" data-entry="${e.id}"><span class="tag ${e.status==='draft'?'draft':''}">${statusName(e)}</span><h3>${h(e.title)}</h3><div class="meta">${h(e.product||'通用知识')}${e.version?' · '+h(e.version):''}<br>${h(processName(e.processing_status))}</div></button>`).join('') || '<p class="empty">这里还没有知识资料。<br>新建一份，开始收集工作知识。</p>'
}
function imagesHtml(images,label) {
  if(!images?.length) return ''
  return `<div class="step-images">${images.map(a=>`<button data-image="${h(a.preview_url)}"><img src="${h(a.thumbnail_url)}" alt="${h(a.name)}" loading="lazy"><small>${h(label)} · ${h(a.name)}</small></button>`).join('')}</div>`
}
function stepHtml(step,index) {
  return `<article class="step"><div class="step-header">${step.order!=null?'STEP '+String(step.order).padStart(2,'0'):'原文片段 '+((step.step_index??index)+1)}${step.is_match?' · 检索命中':''}</div><p>${h(step.instruction)}</p>${imagesHtml(step.images,'步骤配图')}${imagesHtml(step.section_images,'章节配图 · 未确认逐步对应')}<div class="source">${step.source?.url?`<a href="${h(step.source.url)}" target="_blank" rel="noopener">${h(step.source.file_name)}</a>`:h(step.source?.file_name||'图文条目')}${step.source?.page?' · 第 '+step.source.page+' 页':''}</div></article>`
}
function sourceHtml(source) {
  const e=source.entry
  return `<section class="source-group"><h3>${h(e.title)}${source.group_title?' · '+h(source.group_title):''}</h3><p class="note">${h(e.product||'通用')} · 版本 ${h(e.version||'未标注')} · 每段来源分别保留</p>${source.steps.map((step,i)=>stepHtml(step,step.step_index??i)).join('')}${source.omitted_context_count?`<p class="note">另有 ${source.omitted_context_count} 段上下文未展开，可在资料目录查看完整原文。</p>`:''}<div class="pager"><button data-answer-page="${source.previous_page||1}" data-answer-entry="${e.id}" ${!source.previous_page?'disabled':''}>查看前文</button><span>资料第 ${source.page} 批 · 共 ${source.total_steps} 段原文</span><button data-answer-page="${source.page+1}" data-answer-entry="${e.id}" ${source.has_more?'':'disabled'}>查看后文</button></div></section>`
}
function previewGroups(e) {
  const assets=Object.fromEntries((e.assets||[]).map(a=>[a.asset_id,a]))
  const groups=e.groups||((e.steps||[]).length?[{title:'原资料内容',start:0,end:e.steps.length,confirmed:false}]:[])
  return groups.map(g=>`<details open><summary>父块：${h(g.title)} · ${g.confirmed?'已核对':'待核对'}</summary>${e.steps.slice(g.start,g.end).map((s,i)=>stepHtml({...s,images:(s.asset_ids||[]).map(id=>assets[id]).filter(Boolean),section_images:(s.section_asset_ids||[]).map(id=>assets[id]).filter(Boolean),source:{file_name:e.source_name||e.title,page:s.page,url:e.source_url}},g.start+i)).join('')}</details>`).join('')
}
async function openEntry(id) {
  notify(''); state.answer=null
  state.current=await api('/entries/'+id)
  renderCatalog(); renderEntry()
  $('#app').classList.remove('show-catalog')
}
function renderEntry(page=1) {
  const e=state.current; if(!e)return
  const assets=Object.fromEntries((e.assets||[]).map(a=>[a.asset_id,a]))
  const selected=(e.steps||[]).slice((page-1)*20,page*20)
  $('#reading').innerHTML=`<span class="tag ${e.status==='draft'?'draft':''}">${statusName(e)}</span><h2 class="entry-title">${h(e.title)}</h2><div class="statusline">${h(e.product||'通用知识')} ${h(e.version)} · ${h(processName(e.processing_status))}<br>检索准备：${h(indexName(e.index_status))}（可按标题和原文查找）</div><div class="toolbar">${e.status==='draft'?`<button data-edit="${e.id}">编辑图文步骤</button><button data-publish="${e.id}" ${e.processing_status==='ready'?'':'disabled'}>发布资料</button>`:`<button data-revision="${e.id}">创建新版</button>`}${e.source_url?`<a href="${h(e.source_url)}" target="_blank" rel="noopener">查看原文件 ↗</a>`:''}<button data-index="${e.id}" ${e.processing_status==='ready'?'':'disabled'}>建立 / 重试语义索引</button><button data-refresh="${e.id}">刷新状态</button></div>${e.error_message?`<p class="note">${h(e.error_message)}</p>`:''}${e.groups?previewGroups(e):selected.map((s,i)=>stepHtml({...s,images:(s.asset_ids||[]).map(id=>assets[id]).filter(Boolean),section_images:(s.section_asset_ids||[]).map(id=>assets[id]).filter(Boolean),source:{file_name:e.source_name||e.title,page:s.page,url:e.source_url}},i+(page-1)*20)).join('')||'<p class="empty">添加截图和文字说明，或上传资料后提取内容。资料整理完成后即可发布查询。</p>'}<div class="pager" style="${e.groups?'display:none':''}"><button data-entry-page="${page-1}" ${page===1?'disabled':''}>上一页</button><span>共 ${e.steps?.length||0} 段 · 第 ${page} 页</span><button data-entry-page="${page+1}" ${page*20>=(e.steps?.length||0)?'disabled':''}>下一页</button></div>`
  $('#reading').scrollTop=0
}
async function ask(page=1, entryId=null, continuation=false) {
  const question=$('#question').value.trim(); if(!question)return
  const selected=entryId||($('#only-current').checked?state.current?.id:null)
  if($('#only-current').checked&&!selected)throw new Error('请先选择一份已发布资料')
  const body={question,category_id:state.category,product:$('#product-filter').value.trim()||null,version:$('#version-filter').value.trim()||null,entry_id:selected||null,page,context_page:continuation?page:null,source_only:true}
  notify('正在查找已发布的相关资料…')
  const answer=await api('/ask','POST',body)
  state.answer=answer; state.question=question; notify('')
  if(answer.status==='choose_version') {
    $('#reading').innerHTML=`<h2 class="entry-title">请选择适用版本</h2><p class="note">为避免把不同版本的操作混在一起，请先选择。</p>${answer.options.map(e=>`<button class="entry-card" data-answer-version="${e.id}"><h3>${h(e.title)}</h3><span class="meta">${h(e.product)} · ${h(e.version||'未标注版本')}</span></button>`).join('')}`
    return
  }
  $('#reading').innerHTML=`<span class="eyebrow">${answer.generated?'依据资料整理':'原文与配图'}</span><h2 class="entry-title">${h(answer.source_count>1?'来自 '+answer.source_count+' 份资料的证据':(answer.entry?.title||'没有足够证据'))}</h2><div class="summary">${h(answer.summary)}</div>${(answer.limitations||[]).map(t=>`<p class="note">${h(t)}</p>`).join('')}${(answer.sources||[]).map(source=>sourceHtml(source)).join('')||((answer.steps||[]).map((s,i)=>stepHtml(s,i)).join(''))}`
  $('#reading').scrollTop=0
}
function newDialog(seed={}) {
  state.editing=null; $('#editor-title').textContent=seed.id?'创建新版':'新建知识资料'
  $('#editor-body').innerHTML=`<form id="create-form"><div class="form-grid"><label class="field wide">资料标题<input name="title" required maxlength="300" value="${h(seed.title)}" placeholder="例如：新员工账号与 VPN 申请指南"></label><label class="field">分类<select name="category_id"><option value="">未分类</option>${state.categories.map(c=>`<option value="${c.id}" ${c.id===(seed.category_id||state.category)?'selected':''}>${h(c.name)}</option>`).join('')}</select></label><label class="field">适用系统 / 对象<input name="product" value="${h(seed.product)}" placeholder="例如：新员工 / 公司 VPN"></label><label class="field">适用版本<input name="version" value="${h(seed.version)}" placeholder="例如：专业版 7.0"></label><label class="field">系列标识（选填）<input name="family_key" value="${h(seed.family_key)}" placeholder="默认使用标题；新版沿用同一标识"></label></div><p class="note">先创建草稿，再上传文档或按顺序添加截图。发布同系列、同适用对象、同版本的新稿会将旧稿保留为历史版本。</p><div class="editor-actions"><span></span><button class="primary" type="submit">创建并整理内容 →</button></div></form>`
  $('#editor').showModal()
}
async function editDialog(id) {
  state.editing=await api('/entries/'+id)
  state.editSteps=structuredClone(state.editing.steps||[])
  if(!state.editSteps.length)state.editSteps=[{order:1,instruction:'',asset_ids:[]}]
  const groups=state.editing.groups || [{id:crypto.randomUUID(),title:state.editing.title,start:0,end:state.editSteps.length,confirmed:false}]
  for(const g of groups) for(let i=g.start;i<g.end;i++) Object.assign(state.editSteps[i],{group_id:g.id,group_title:g.title,group_confirmed:g.confirmed})
  $('#editor-title').textContent=state.editing.title
  renderEditor()
  if(!$('#editor').open)$('#editor').showModal()
}
function captureSteps() {
  state.editSteps=[...document.querySelectorAll('.edit-step')].map((row,i)=>({
    order:row.querySelector('[name=order]').value?Number(row.querySelector('[name=order]').value):null,
    instruction:row.querySelector('textarea').value,title:state.editSteps[i]?.title||'',page:row.querySelector('[name=page]').value?Number(row.querySelector('[name=page]').value):null,
    group_id:row.querySelector('[name=group]').value,group_title:row.querySelector('[name=group-title]').value,group_confirmed:row.querySelector('[name=group-confirmed]').checked,
    asset_ids:[...row.querySelectorAll('[name=asset]:checked')].map(c=>c.value),
    section_asset_ids:[...row.querySelectorAll('[name=section-asset]:checked')].map(c=>c.value)}))
}
function prepareGroups() {
  const groups=[]
  for(const [i,s] of state.editSteps.entries()) {
    const last=groups.at(-1)
    if(!last || last.id!==s.group_id) groups.push({id:s.group_id,title:s.group_title.trim(),start:i,end:i+1,confirmed:s.group_confirmed})
    else {last.end=i+1;last.confirmed=last.confirmed&&s.group_confirmed; if(last.title!==s.group_title.trim())throw Error('同一流程的标题必须一致')}
  }
  return groups
}
function renderEditor() {
  const e=state.editing
  const groupChoices=[...new Map(state.editSteps.map(s=>[s.group_id,s.group_title])).entries()]
  $('#editor-body').innerHTML=`<div class="toolbar"><label><button type="button" id="upload-trigger">上传文档 / 截图</button><input id="upload" type="file" multiple hidden accept=".pdf,.doc,.docx,.ppt,.pptx,.png,.jpg,.jpeg,.webp"></label>${e.source_name?`<button id="parse-local">本地提取</button><button id="parse-cloud">提交 MinerU</button>`:''}</div><div class="statusline">${h(e.source_name||'支持文档或截图')} · ${h(processName(e.processing_status))}</div><p class="note">逐项检查流程名称、顺序、页码和图片。使用“从这里拆分流程”划分父块；选择同一流程可合并相邻内容。确认后才能发布。</p><div id="edit-steps">${state.editSteps.map((s,i)=>`<section class="edit-step"><div class="edit-step-head"><label>所属流程 <select name="group">${groupChoices.map(([id,title])=>`<option value="${h(id)}" ${id===s.group_id?'selected':''}>${h(title||'未命名')}</option>`).join('')}</select></label><label>流程名称 <input name="group-title" value="${h(s.group_title)}"></label><label><input type="checkbox" name="group-confirmed" ${s.group_confirmed?'checked':''}> 已核对</label></div><div class="edit-step-head"><label>序号 <input name="order" type="number" min="1" value="${s.order??''}"></label><label>原文件页码 <input name="page" type="number" min="1" value="${s.page??''}"></label><button data-split-group="${i}" type="button" ${i===0?'disabled':''}>从这里拆分流程</button><button data-move="${i}" data-direction="-1" type="button">上移</button><button data-move="${i}" data-direction="1" type="button">下移</button><button data-split-step="${i}" type="button">在光标处分割</button><button data-merge-step="${i}" type="button" ${i===0?'disabled':''}>与上一段合并</button><button data-remove="${i}" type="button">移除</button></div><textarea aria-label="第${i+1}段文字说明" placeholder="填写原文说明">${h(s.instruction)}</textarea><div>步骤对应图片</div><div class="asset-pick">${e.assets.map(a=>`<label><input name="asset" type="checkbox" value="${a.asset_id}" ${(s.asset_ids||[]).includes(a.asset_id)?'checked':''}><img src="${h(a.thumbnail_url)}" alt="${h(a.name)}">${h(a.name)}</label>`).join('')||'<small>暂无图片</small>'}</div><div>章节图片（尚未确认逐步对应）</div><div class="asset-pick">${e.assets.map(a=>`<label><input name="section-asset" type="checkbox" value="${a.asset_id}" ${(s.section_asset_ids||[]).includes(a.asset_id)?'checked':''}><img src="${h(a.thumbnail_url)}" alt="${h(a.name)}">${h(a.name)}</label>`).join('')}</div></section>`).join('')}</div><div class="editor-actions"><button id="add-step" type="button">＋ 添加一段 / 一步</button><button id="save-steps" class="primary" type="button">保存分块草稿</button></div>`

}
document.addEventListener('submit',e=>{
  if(e.target.id==='ask'){e.preventDefault();action(()=>ask(),$('#ask-button'))}
  if(e.target.id==='create-form'){
    e.preventDefault();const data=Object.fromEntries(new FormData(e.target));data.category_id=data.category_id||null;data.family_key=data.family_key||null
    action(async()=>{const created=await api('/entries','POST',data);await load();await editDialog(created.id)},e.target.querySelector('button[type=submit]'))
  }
})
document.addEventListener('click',e=>{
  const button=e.target.closest('button');if(!button)return
  const d=button.dataset
  if(d.category!==undefined){state.category=d.category||null;renderCatalog()}
  if(d.entry)action(()=>openEntry(d.entry),button)
  if(d.edit)action(()=>editDialog(d.edit),button)
  if(d.revision)newDialog(state.current)
  if(d.refresh)action(()=>openEntry(d.refresh),button)
  if(d.publish)action(async()=>{const item=await api('/entries/'+d.publish);if(item.groups?.some(g=>!g.confirmed))throw Error('请先编辑并核对所有流程分块');$('#reading').innerHTML=`<h2>发布前分块预览</h2><p class="note">请检查每个流程的内容、页码和图片；确认后发布。</p>${previewGroups(item)}<button class="primary" data-confirm-publish="${h(item.id)}">确认预览并发布</button><button data-entry="${h(item.id)}">返回</button>`},button)
  if(d.confirmPublish)action(async()=>{await api(`/entries/${d.confirmPublish}/publish`,'POST');await load();await openEntry(d.confirmPublish)},button)
  if(d.index)action(async()=>{await api(`/entries/${d.index}/index`,'POST');notify('已提交本地语义索引任务，请稍后刷新状态。关键词检索可继续使用。')},button)
  if(d.entryPage)renderEntry(Number(d.entryPage))
  if(d.answerPage)action(()=>ask(Number(d.answerPage),d.answerEntry||state.answer.entry.id,true),button)
  if(d.answerVersion)action(()=>ask(1,d.answerVersion),button)
  if(d.image){$('#large-image').src=d.image;$('#image-dialog').showModal()}
  if(d.question){$('#question').value=d.question;$('#question').focus()}
  if(d.move!==undefined){captureSteps();state.editSteps.forEach(s=>s.group_confirmed=false);const i=Number(d.move),j=i+Number(d.direction);if(j>=0&&j<state.editSteps.length){[state.editSteps[i],state.editSteps[j]]=[state.editSteps[j],state.editSteps[i]];if(state.editSteps.every(s=>s.order!=null))state.editSteps.forEach((s,i)=>s.order=i+1);renderEditor()}}
  if(d.remove!==undefined){captureSteps();state.editSteps.forEach(s=>s.group_confirmed=false);state.editSteps.splice(Number(d.remove),1);renderEditor()}
  if(d.splitStep!==undefined){const i=Number(d.splitStep),row=button.closest('.edit-step'),ta=row.querySelector('textarea'),pos=ta.selectionStart;captureSteps();const step=state.editSteps[i];if(pos<=0||pos>=step.instruction.length){notify('请先将光标放在需要分割的正文位置。');return}const right=structuredClone(step);right.instruction=step.instruction.slice(pos).trim();step.instruction=step.instruction.slice(0,pos).trim();step.group_confirmed=right.group_confirmed=false;state.editSteps.splice(i+1,0,right);if(state.editSteps.every(s=>s.order!=null))state.editSteps.forEach((s,j)=>s.order=j+1);renderEditor()}
  if(d.mergeStep!==undefined){captureSteps();const i=Number(d.mergeStep),a=state.editSteps[i-1],b=state.editSteps[i];if(!a||a.group_id!==b.group_id){notify('只能合并同一流程内相邻的段落。');return}if(a.page!==b.page){notify('不同原文页码的段落请保持分开，以保留来源。');return}a.instruction+='\n\n'+b.instruction;a.asset_ids=[...new Set([...a.asset_ids,...b.asset_ids])];a.section_asset_ids=[...new Set([...a.section_asset_ids,...b.section_asset_ids])];a.group_confirmed=false;state.editSteps.splice(i,1);if(state.editSteps.every(s=>s.order!=null))state.editSteps.forEach((s,j)=>s.order=j+1);renderEditor()}
  if(d.splitGroup!==undefined){captureSteps();const i=Number(d.splitGroup), old=state.editSteps[i].group_id, gid=crypto.randomUUID();for(let j=i;j<state.editSteps.length&&state.editSteps[j].group_id===old;j++)Object.assign(state.editSteps[j],{group_id:gid,group_title:'新流程',group_confirmed:false});renderEditor()}
  if(button.id==='new')newDialog()
  if(button.id==='scope')$('#app').classList.toggle('show-catalog')
  if(button.id==='add-category'){const name=prompt('分类名称');if(name?.trim())action(async()=>{await api('/categories','POST',{name:name.trim()});await load()},button)}
  if(button.id==='upload-trigger')$('#upload').click()
  if(button.id==='add-step'){captureSteps();const ordered=state.editSteps.every(s=>s.order!=null);const last=state.editSteps.at(-1);state.editSteps.push({order:ordered?state.editSteps.length+1:null,instruction:'',asset_ids:[],section_asset_ids:[],group_id:last?.group_id||crypto.randomUUID(),group_title:last?.group_title||state.editing.title,group_confirmed:false});renderEditor()}
  if(button.id==='save-steps')action(async()=>{captureSteps();const groups=prepareGroups();const steps=state.editSteps.map(({group_id,group_title,group_confirmed,...step})=>step);state.editing=await api(`/entries/${state.editing.id}/steps`,'PUT',{steps,groups});$('#editor').close();await load();await openEntry(state.editing.id)},button)
  if(button.id==='parse-local')action(async()=>{notify('正在本地提取原文…');await api(`/entries/${state.editing.id}/parse-local`,'POST');await editDialog(state.editing.id);await load();notify('本地提取完成，请检查内容和配图后发布。')},button)
  if(button.id==='parse-cloud'&&confirm('确认将这份原文档提交给已配置的 MinerU 服务？上传归档本身不会外发。'))action(async()=>{await api(`/entries/${state.editing.id}/submit-mineru`,'POST',{explicit_external_export_approved:true});notify('已明确提交 MinerU，可关闭窗口后刷新处理状态。')},button)
  if(d.understand&&confirm('确认将这张截图提交给已配置的视觉模型识别？识别结果不会自动变成操作步骤。'))action(async()=>{captureSteps();await api(`/entries/${state.editing.id}/assets/${d.understand}/understand`,'POST',{explicit_external_export_approved:true});state.editing=await api('/entries/'+state.editing.id);renderEditor()},button)
})
document.addEventListener('change',e=>{
  if(e.target.matches('[name=group-title],[name=group-confirmed]')){const row=e.target.closest('.edit-step'), group=row.querySelector('[name=group]').value;for(const item of document.querySelectorAll('.edit-step'))if(item.querySelector('[name=group]').value===group){const target=item.querySelector(`[name=${e.target.name}]`);if(e.target.type==='checkbox')target.checked=e.target.checked;else target.value=e.target.value}}
  if(e.target.matches('[name=group]')){const row=e.target.closest('.edit-step'), choice=e.target.selectedOptions[0];row.querySelector('[name=group-title]').value=choice.textContent;row.querySelector('[name=group-confirmed]').checked=false}
  if(e.target.id==='upload')action(async()=>{captureSteps();for(const file of e.target.files){const data=new FormData();data.append('file',file);await api(`/entries/${state.editing.id}/files`,'POST',data)}state.editing=await api('/entries/'+state.editing.id);renderEditor();await load();notify('文件已保存在知识库本地；尚未发送到外部服务。')})
})
document.addEventListener('input',e=>{
  const row=e.target.closest('.edit-step');if(!row || e.target.matches('[name=group-confirmed]'))return
  const id=row.querySelector('[name=group]')?.value
  for(const item of document.querySelectorAll('.edit-step'))if(item.querySelector('[name=group]')?.value===id)item.querySelector('[name=group-confirmed]').checked=false
})
$('#filter').addEventListener('input',renderCatalog)
document.addEventListener('error',e=>{if(e.target instanceof HTMLImageElement){e.target.hidden=true;const note=document.createElement('small');note.textContent='图片暂不可用，文字与来源仍保留。';e.target.parentElement.append(note)}},true)
action(load)
