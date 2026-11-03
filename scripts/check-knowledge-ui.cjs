// Read-only browser check of the deployed work knowledge assistant.
const {chromium}=require('C:/Users/liwj/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const path=require('path');const cp=require('child_process');
const python='C:/Users/liwj/.conda/envs/design-rag/python.exe';
const hybrid=process.argv.includes('--hybrid');
const output=path.resolve(hybrid?'artifacts/knowledge-hybrid-20261029':'artifacts/langbot-work-knowledge-20261029');
function write(name,data,binary=false){
 const result=cp.spawnSync(python,['-c',
  'import sys,base64;from pathlib import Path;p=Path(sys.argv[1]);p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(base64.b64decode(sys.stdin.buffer.read()) if sys.argv[2]=="1" else sys.stdin.buffer.read())',
  path.join(output,name),binary?'1':'0'],{input:binary?data.toString('base64'):data,encoding:'utf8'});
 if(result.status)throw new Error(result.stderr);
}
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:900}});const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:8060/knowledge/');
  await page.locator('#categories .category').first().waitFor();
  if(await page.title()!=='工作知识助手')throw new Error('Unexpected application name');
  await page.locator('#new').click();
  await page.locator('#create-form').waitFor();
  if(!(await page.locator('#editor-title').textContent()).includes('知识资料'))throw new Error('Old editor label');
  await page.locator('#editor .dialog-top button').click();
  let sources=[];
  if(hybrid){
   await page.locator('#question').fill('请结合日常手册库使用手册和图文操作条目演示，说明新建、添加截图和发布资料的流程。');
   await page.locator('#ask-button').click();
   await page.locator('.source-group').nth(1).waitFor({timeout:240000});
   sources=await page.locator('.source-group>h3').allTextContents();
   if(sources.length!==2)throw new Error('Expected two independently attributed sources');
   if(await page.locator('#use-model').count())throw new Error('Web preview must not invoke another answer model');
  }
  const layouts=[];
  for(const width of [1440,1024,800,560]){
   await page.setViewportSize({width,height:900});
   layouts.push(await page.evaluate(()=>({width:innerWidth,bodyScroll:document.body.scrollHeight>innerHeight,
    horizontal:document.documentElement.scrollWidth>innerWidth,composerVisible:document.querySelector('.composer').getBoundingClientRect().bottom<=innerHeight+1})));
   if(width===1440||width===560)write('knowledge-'+width+'.png',await page.screenshot(),true);
  }
  await page.goto('http://127.0.0.1:8060/manuals/');
  await page.locator('#categories .category').first().waitFor();
  if(await page.title()!=='工作知识助手')throw new Error('Legacy entry is broken');
  if(errors.length||layouts.some(x=>x.bodyScroll||x.horizontal||!x.composerVisible))throw new Error(JSON.stringify({errors,layouts}));
  const result={status:'passed',errors,layouts,new_entry:'/knowledge/',legacy_entry:'/manuals/',sources};
  write('browser-check.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
