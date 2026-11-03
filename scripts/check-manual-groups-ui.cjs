// Exercise the real page with an in-memory API; no library records are written.
const { chromium } = require('C:/Users/liwj/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright')

;(async () => {
  const browser = await chromium.launch({channel:'msedge',headless:true})
  const page = await browser.newPage({viewport:{width:1440,height:900}})
  const errors=[]
  page.on('pageerror',e=>errors.push(e.message))
  let published=false
  let entry={id:'test',title:'验收资料',category_id:null,status:'draft',processing_status:'ready',index_status:'pending',assets:[],source_name:'示例.docx',source_url:null,groups:null,steps:[
    {order:1,instruction:'入职提交资料',asset_ids:[],section_asset_ids:[],page:1},
    {order:2,instruction:'请假填写申请',asset_ids:[],section_asset_ids:[],page:2},
    {order:3,instruction:'提交审批',asset_ids:[],section_asset_ids:[],page:2}]}
  await page.route('**/api/v1/manuals/**',async route=>{
    const req=route.request(),url=new URL(req.url()),path=url.pathname,method=req.method()
    let data
    if(path.endsWith('/categories'))data=[]
    else if(path.endsWith('/entries')&&method==='GET')data=[entry]
    else if(path.endsWith('/entries/test')&&method==='GET')data=entry
    else if(path.endsWith('/entries/test/steps')&&method==='PUT'){
      const body=req.postDataJSON();entry={...entry,steps:body.steps,groups:body.groups};data=entry
    } else if(path.endsWith('/entries/test/publish')&&method==='POST'){published=true;entry={...entry,status:'published'};data=entry}
    else return route.fulfill({status:404,json:{detail:'unexpected mock route'}})
    await route.fulfill({status:200,json:data})
  })
  try {
    await page.goto('http://127.0.0.1:8060/knowledge/')
    await page.locator('[data-entry="test"]').click()
    await page.locator('[data-edit="test"]').click()
    await page.setViewportSize({width:560,height:900})
    const overflow=await page.locator('#editor').evaluate(el=>el.scrollWidth>el.clientWidth+1)
    if(overflow)throw Error('Editor overflows on narrow screens')
    await page.locator('[data-split-group="1"]').click()
    await page.locator('.edit-step').nth(1).locator('[name=group-title]').fill('请假流程')
    await page.locator('.edit-step').nth(0).locator('[name=group-confirmed]').check()
    await page.locator('.edit-step').nth(1).locator('[name=group-confirmed]').check()
    await page.locator('#save-steps').click()
    await page.locator('[data-publish="test"]').waitFor()
    if(entry.groups.length!==2||entry.groups[0].end!==1||entry.groups[1].start!==1||!entry.groups.every(g=>g.confirmed))throw Error('Incorrect saved group boundaries')
    await page.locator('[data-publish="test"]').click()
    if(!(await page.locator('#reading').innerText()).includes('发布前分块预览'))throw Error('Missing preview')
    if(!(await page.locator('#reading').innerText()).includes('请假流程'))throw Error('Missing procedure')
    if(published)throw Error('Published before confirmation')
    await page.locator('[data-confirm-publish="test"]').click()
    if(!published||errors.length)throw Error(JSON.stringify({published,errors}))
    console.log(JSON.stringify({status:'passed',groups:entry.groups.length,preview:true,errors}))
  } finally {await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1})
