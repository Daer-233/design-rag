"""Create an authored acceptance manual about the actual V1 interface, not user project data.
Run using the bundled Python with reportlab. Output stays in ignored artifacts.
"""
from pathlib import Path
import json
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/manual-library-20261029'
OUT.mkdir(parents=True,exist_ok=True)
pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
instructions=[
    ('创建草稿','点击左侧“新建手册”，填写手册标题，再点击“创建并整理内容”。'),
    ('创建分类','点击左侧“资料分类”旁的加号，输入名称即可创建分类。'),
    ('记录版本','新建时填写“软件 / 设备”和“适用版本”；同系列新版沿用同一个系列标识。'),
    ('上传文档','在编辑窗口点击“上传文档 / 截图”，选择 PDF、Word 或 PPT 文件。上传仅本地归档。'),
    ('本地提取','对于 PDF、DOCX、PPTX，上传后点击“本地提取”，检查提取出的原文与配图。扫描件需 OCR。'),
    ('添加截图','在编辑窗口上传 PNG、JPG 或 WEBP；每张截图不超过 20MiB。'),
    ('关联步骤','为每段填写文字说明并勾选对应截图。序号全部留空表示原文片段，不自动推断操作顺序。'),
    ('调整顺序','在步骤编辑区使用“上移”和“下移”调整顺序；明确的步骤序号不能重复。'),
    ('保存发布','点击“保存内容”，检查原文和配图后点击“发布手册”。草稿默认不进入问答。'),
    ('查阅来源','在查询结果中查看每步配图和文件页码，点击原文件链接核对；长流程点击“继续阅读”。'),
]
pdf=OUT/'manual-library-v1-acceptance.pdf'
c=canvas.Canvas(str(pdf),pagesize=(595,842))
for i,(topic,instruction) in enumerate(instructions,1):
    c.setFont('STSong-Light',20);c.drawString(48,780,'日常手册库使用手册（验收示例）')
    c.setFont('STSong-Light',14);c.drawString(48,735,f'{i}. {topic}')
    c.setFont('STSong-Light',12)
    for line,start in enumerate(range(0,len(instruction),32)):
        c.drawString(48,700-line*24,instruction[start:start+32])
    shot=OUT/('02-create.png' if i<=3 else '03-editor.png')
    if shot.exists():
        c.drawImage(str(shot),48,220,width=499,height=347,preserveAspectRatio=True)
        c.setFont('STSong-Light',10);c.drawString(48,195,'实际界面截图，仅作为章节配图，不推断逐步对应关系。')
    c.setFont('STSong-Light',10);c.drawString(48,70,f'验收用自编说明 · 来源为本机 V1 界面 · 第 {i} 页')
    c.showPage()
c.save()
(OUT/'sample-facts.json').write_text(json.dumps({'title':'日常手册库使用手册（验收示例）',
    'authorship':'本次实现编写的验收用手册，不是用户提供的业务资料',
    'facts':[{'page':i,'topic':topic,'instruction':text} for i,(topic,text) in enumerate(instructions,1)]},ensure_ascii=False,indent=2),encoding='utf-8')
print('Created authored 10-page manual with real interface screenshots.')
