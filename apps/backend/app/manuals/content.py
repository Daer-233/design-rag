"""Local extraction preserves source order; it does not invent operating steps."""
from io import BytesIO
from pathlib import Path, PurePosixPath
import re
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from PIL import Image, ImageOps, UnidentifiedImageError
from pypdf import PdfReader


def save_image(binary, folder, name):
    from uuid import uuid4
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (str(uuid4()) + '.png')
    try:
        original_image = Image.open(BytesIO(binary))
    except (UnidentifiedImageError, Image.DecompressionBombError) as error:
        raise ValueError('文件不是有效图片，或图片尺寸过大') from error
    with original_image as original:
        if original.width * original.height > 40_000_000:
            raise ValueError('图片尺寸过大')
        image = ImageOps.exif_transpose(original).convert('RGB')
        image.save(target, format='PNG')
        image.thumbnail((640,640))
        thumbnail = target.with_suffix('.thumb.jpg')
        image.save(thumbnail, format='JPEG', quality=86)
    return target, thumbnail


def source_steps(blocks):
    """Only unambiguous source numbering is treated as ordered instructions."""
    result = []
    for text, page, asset_ids in blocks:
        if not text.strip():
            continue
        for paragraph in re.split(r'\n\s*\n', text.strip()):
            match = re.match(r'^\s*(?:第\s*(\d+)\s*步[：:、.\s]*|(\d+)[.、)）]\s*)(.+)', paragraph, re.S)
            result.append({'order': int(match.group(1) or match.group(2)) if match else None,
                           'title': '', 'instruction': paragraph, 'page': page,
                           'asset_ids': [], 'section_asset_ids': asset_ids,
                           'evidence_type': 'document_confirmed'})
    numbers = [step['order'] for step in result]
    if not numbers or any(n is None for n in numbers) or numbers != sorted(set(numbers)):
        for step in result:
            step['order'] = None
    return result


def extract_local(path, register_image):
    path = Path(path)
    blocks = []
    heading_texts = set()
    if path.suffix.lower() == '.pdf':
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise ValueError('请先解除 PDF 密码')
        for page_number, page in enumerate(reader.pages, 1):
            assets = []
            for image in page.images:
                try:
                    assets.append(register_image(image.data, image.name, page_number))
                except (ValueError, OSError):
                    continue
            blocks.append((page.extract_text() or '', page_number, assets))
    elif path.suffix.lower() in {'.docx','.pptx'}:
        with ZipFile(path) as archive:
            if sum(info.file_size for info in archive.infolist()) > 256 * 1024 * 1024:
                raise ValueError('Office 解压体积过大')
            is_word = path.suffix.lower() == '.docx'
            names = ['word/document.xml'] if is_word else sorted(
                [n for n in archive.namelist() if re.fullmatch(r'ppt/slides/slide\d+\.xml',n)],
                key=lambda n: int(re.search(r'(\d+)\.xml$',n).group(1)))
            for index, name in enumerate(names,1):
                xml = ET.fromstring(archive.read(name))
                ns = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main' if is_word else 'http://schemas.openxmlformats.org/drawingml/2006/main'
                paragraphs = [''.join(t.text or '' for t in p.iter('{'+ns+'}t'))
                              for p in xml.iter('{'+ns+'}p')]
                if is_word:
                    for paragraph in xml.iter('{'+ns+'}p'):
                        style = paragraph.find('./{'+ns+'}pPr/{'+ns+'}pStyle')
                        name = style.get('{'+ns+'}val','') if style is not None else ''
                        if re.match(r'^(?:heading|标题)[1-9]$', name, re.I):
                            heading_texts.add(''.join(t.text or '' for t in paragraph.iter('{'+ns+'}t')).strip())
                assets = []
                relname = str(PurePosixPath(name).parent / '_rels' / (PurePosixPath(name).name+'.rels'))
                if relname in archive.namelist():
                    import posixpath
                    for rel in ET.fromstring(archive.read(relname)):
                        if rel.attrib.get('TargetMode') == 'External' or not rel.attrib.get('Type','').endswith('/image'):
                            continue
                        target = posixpath.normpath(posixpath.join(str(PurePosixPath(name).parent),rel.attrib['Target']))
                        if target in archive.namelist():
                            try:
                                assets.append(register_image(archive.read(target),PurePosixPath(target).name,None if is_word else index))
                            except (ValueError, OSError):
                                continue
                blocks.append(('\n\n'.join(p for p in paragraphs if p.strip()),None if is_word else index,assets))
    else:
        raise ValueError('本地提取支持 PDF/DOCX/PPTX；扫描件或 DOC/PPT 请明确提交 MinerU 处理')
    steps = source_steps(blocks)
    for step in steps:
        if step['instruction'].strip() in heading_texts:
            step['heading_hint'] = True
    if not steps:
        raise ValueError('未提取到文字；请使用 MinerU/OCR 或手动填写说明，不会用空白结果替代原文')
    return steps
