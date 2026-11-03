// Actual Edge browser checks, using the already-bundled Playwright runtime.
const {chromium}=require('C:/Users/liwj/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('fs');const path=require('path');
const dir=path.resolve('artifacts/manual-library-20261029');fs.mkdirSync(dir,{recursive:true});
const cp=require('child_process');
let browser;
async function screenshot(page,name){
 const buffer=await page.screenshot();
 if(buffer.subarray(0,8).toString('hex')!=='89504e470d0a1a0a')throw new Error('Unexpected screenshot buffer signature');
 const result=cp.spawnSync('C:/Users/liwj/.conda/envs/design-rag/python.exe',
  ['-c','import sys,base64;from pathlib import Path;Path(sys.argv[1]).write_bytes(base64.b64decode(sys.stdin.buffer.read()))',path.join(dir,name)],
  {input:buffer.toString('base64'),encoding:'utf8'});
 if(result.status!==0)throw new Error('Screenshot write failed: '+result.stderr);
}
(async()=>{
 browser=await chromium.launch({channel:'msedge',headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1000},deviceScaleFactor:1});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:8060/manuals/');
 await page.locator('#categories .category').first().waitFor();
 await screenshot(page,'01-library.png');
 await page.locator('#new').click();
 await page.locator('#create-form').waitFor();
 await screenshot(page,'02-create.png');
 await page.locator('[name=title]').fill('图文操作条目演示（验收样例）');
 await page.locator('[name=product]').fill('日常手册库 V1');
 await page.locator('[name=version]').fill('1.0');
 await page.locator('#create-form button[type=submit]').click();
 await page.locator('#upload-trigger').waitFor();
 await screenshot(page,'03-editor.png');
 await page.locator('.edit-step textarea').fill('点击左侧“新建手册”，填写标题、分类和适用版本，再创建草稿。');
 await page.locator('#upload').setInputFiles([path.join(dir,'02-create.png'),path.join(dir,'03-editor.png')]);
 await page.locator('.asset-pick img').first().waitFor();
 await page.locator('.edit-step [name=asset]').first().check();
 await page.locator('#add-step').click();
 await page.locator('.edit-step textarea').nth(1).fill('在编辑窗口按顺序填写文字说明，勾选对应截图，点击“保存内容”，检查后点击“发布手册”。');
 await page.locator('.edit-step').nth(1).locator('[name=asset]').nth(1).check();
 await screenshot(page,'04-step-images.png');
 await page.locator('#save-steps').click();
 await page.waitForFunction(()=>!document.querySelector('#editor').open);
 await page.locator('[data-publish]').click();
 await page.locator('[data-revision]').waitFor();
 await page.locator('#question').fill('图文操作条目演示如何添加截图？');
 await page.locator('#ask-button').click();
 await page.locator('#reading .summary').waitFor();
 await page.locator('#reading .step-images img').first().waitFor();
 await screenshot(page,'05-answer.png');
 const entry=await page.locator('[data-refresh]').count()?await page.locator('[data-refresh]').getAttribute('data-refresh'):null;
 const layouts=[];
 for(const width of [1440,1024,800,560]){
   await page.setViewportSize({width,height:850});
   layouts.push(await page.evaluate(()=>({width:innerWidth,bodyScroll:document.body.scrollHeight>innerHeight,
      horizontal:document.documentElement.scrollWidth>innerWidth,composerVisible:document.querySelector('.composer').getBoundingClientRect().bottom<=innerHeight+1})));
 }
 await screenshot(page,'06-mobile.png');
 if(errors.length||layouts.some(l=>l.bodyScroll||l.horizontal||!l.composerVisible))throw new Error(JSON.stringify({errors,layouts}));
 fs.writeFileSync(path.join(dir,'browser-check.json'),JSON.stringify({errors,layouts,status:'passed'},null,2));
 await browser.close();console.log(JSON.stringify({status:'passed',errors,layouts}));
})().catch(async e=>{console.error(e);if(browser)await browser.close();process.exitCode=1});
