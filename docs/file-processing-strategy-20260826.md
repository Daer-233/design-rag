# Design_RAG 五个设计项目文件处理方案

日期：2026-08-26

## 1. 本轮文件盘点

共 5 个项目、50 个文件，约 864.89 MiB：

- JPG：44 个，均为高分辨率设计效果图/成图，约 5.62–63.92 MP。
- PDF：4 个，合计 204 页，均未加密；属于图片密集型设计方案文档。
- PPTX：1 个，39 页、306.85 MiB、92 个嵌入媒体，超过 MinerU 200 MiB 限制。
- DOC：1 个，59.86 MiB，旧版 OLE `.doc` 主材物料书。

## 2. 总体原则

1. PostgreSQL 保存原始文件、派生文件、文档版本、块、图片资产、模型版本和来源关系，是权威数据源。
2. Qdrant 只保存可重建的 Dense/Sparse 检索索引，通过 VectorStoreProvider 访问。
3. 原始文件首先本地归档并计算 SHA-256；所有外发动作必须显式批准并可审计。
4. 文档解析优先使用 MinerU vLLM；普通效果图使用 GPT 视觉分析；带大量文字的图片使用 MinerU OCR + GPT 视觉理解。
5. 图片二进制不写入 Qdrant，只把 OCR 文本、视觉描述和必要元数据向量化。
6. 设计方案以“页/幻灯片”为主要父块，文本、表格、OCR、视觉描述为子块。
7. `document_text`、`ocr_extracted`、`vision_generated` 必须区分，事实检索优先原文和 OCR。

## 3. 正式文件路由

### PDF

- 原文件小于 200 MiB且页数不长：直接提交 MinerU vLLM。
- 超过 40–60 页或图片密集：本地按 10–20 页拆分后提交，降低失败重试成本。
- MinerU 返回后必须读取结构化 JSON，而不应只读取 Markdown。
- 保存页码、块类型、bbox、图片路径、标题、表格和原始顺序。
- 对低文本、高图片占比页面生成本地页面渲染图，显式授权后做整页视觉分析。
- 小图标、Logo、装饰纹理不进入视觉分析和检索。

### PPT/PPTX

- 原始 PPTX 不外发。
- LibreOffice 本地导出为 PDF。
- 建议固定每 5–10 页一段，并同时确保每段小于 200 MiB；不要只在超限时才拆分。
- 保存“原 PPTX → 导出 PDF → 分段 PDF → 原幻灯片页码范围”的完整关系和 SHA-256。
- 派生 PDF 送 MinerU vLLM；低文本设计页按整页视觉分析处理。

### DOC/DOCX

- DOC/DOCX 首选直接提交 MinerU vLLM。
- 旧 `.doc`、加密、损坏或 MinerU 失败时，本地使用 LibreOffice 转为 PDF，再按 PDF 路由处理。
- 主材物料书优先识别表格、材料编号、品牌、型号、规格、颜色、空间、数量和备注。
- 表格以“表/章节”为父块，以“材料行/条目”为子块，避免按固定字符切碎一行材料信息。

### JPG/PNG/WEBP

- 原图本地保存，计算 SHA-256；创建分析派生图，不修改原图。
- 普通设计效果图：GPT 视觉分析，不送 MinerU。
- 文字密集设计图：先 MinerU OCR，再 GPT 视觉理解。
- 视觉模型外发前：自动纠正方向、移除 EXIF、最长边缩放到约 2048–2560 px；细节图按需切片。
- 超大图不直接原图外发，保留原图与派生图映射。
- 生成结构化字段：空间、视角、风格、颜色、材料、家具、灯光、构造特征、可见文字、限制和置信度。

## 4. PDF/PPTX 内图片策略

采用“整页优先、图片资产补充”的策略：

1. MinerU 负责文本、OCR、表格、公式和版面块。
2. 页面文字较少且大图占主要面积时，对整页渲染图做视觉分析，以保留标题、图片和标注之间的关系。
3. 只对重要且唯一的嵌入图片建立 image_asset；过滤页眉页脚、Logo、图标和小装饰图。
4. 使用 SHA-256 做精确去重，使用感知哈希做跨 PDF/PPTX/JPG 的近似重复聚类。
5. 图片视觉描述属于模型生成内容，必须带 `source_type=vision_generated` 和模型/提示词版本。

## 5. 父子分块

建议每一页或幻灯片形成一个父块：

- parent: page/slide context
- child:text: MinerU 原文
- child:table: 表格或材料清单
- child:image_ocr: 图片内标注和文字
- child:vision_analysis: 效果图/整页视觉描述
- child:material_item: 单条材料记录

每个块保存：project_id、document_id、document_version_id、chunk_id、parent_chunk_id、page_number/page_range、bbox、content_origin、source_sha256、derivative_sha256、parser/model/prompt version、confidence、review_status。

## 6. 五个项目的具体方案

### 01 绿城下沙

- 1 DOC + 1 PDF + 1 超大 PPTX + 5 JPG，是完整验收样本。
- DOC 主材书走 MinerU，重点结构化材料条目。
- 21 页 PDF 可整份 MinerU；低文本页面补整页视觉分析。
- 306.85 MiB PPTX 本地转 PDF并按 5–10 页拆段，不发送原 PPTX。
- 5 张效果图走 GPT 视觉分析；与 DOC/PDF/PPTX 按空间和材料标签关联。

### 02 华润顶层会所

- 8 张高分辨率 JPG，无文档。
- 全部按效果图视觉分析处理；微信文件名不能作为空间事实。
- 使用模型生成的空间类型，并把低置信结果标记为待人工复核。

### 03 上海静安里

- 130 页 PDF + 17 张 JPG，是图片密集、长文档样本。
- PDF 按 10–15 页拆段提交 MinerU。
- 页面级视觉分析必须做选择性过滤，不能把每个嵌入 XObject 都发送给 GPT。
- 使用感知哈希识别 PDF 中图片与 17 张独立 JPG 的重复关系。

### 04 杭州中海

- 16 页 PDF + 9 张 JPG。
- PDF 可整份 MinerU；平面、天花、地铺、材质标注页面重点 OCR。
- JPG 走视觉分析，并按主卧、卫生间等空间与 PDF 页建立关联。

### 05 海底捞

- 37 页概念方案 PDF + 5 张 JPG。
- PDF 直接 MinerU；将品牌叙事/设计概念与具体材料/空间事实分开建块。
- 文件夹名与 PDF 项目名存在差异，保存项目别名并进入人工复核，不能自动覆盖项目名称。

## 7. 当前代码需要补齐的关键点

1. 当前 `MinerUResultNormalizer` 只读取 Markdown并按标题切分，`source_locator` 为空；需要扩展为读取 `content_list.json`/`middle.json`，保留 page、bbox、block type 和图片引用。
2. 当前视觉分析链路主要面向独立上传图片；需要增加由 PDF/PPTX 派生的 page_image/image_asset 视觉任务。
3. 增加 `image_assets`（或统一 `document_assets`）表，保存原图/派生图、哈希、页码、bbox、处理状态和模型版本。
4. `normalize-pptx-for-mineru.py` 当前主要按 200 MiB 超限拆分；建议增加 `--max-pages-per-segment`，默认 5–10 页。
5. 增加低信息图片过滤、SHA-256 精确去重和 pHash 近似去重。
6. 检索粗排对原文/OCR事实证据加权高于视觉生成描述；视觉描述用于空间、风格、材料外观等语义召回。

## 8. 推荐实施顺序

1. 结构化 MinerU 结果解析与页码/bbox 保留。
2. document/image asset 数据模型与迁移。
3. PDF/PPTX 页面渲染和选择性视觉任务。
4. PPTX 固定页数拆分。
5. 图片去重、过滤与派生图生成。
6. 以 01 绿城下沙做全格式验收，再用 03 上海静安里做长文档与去重压力测试。
