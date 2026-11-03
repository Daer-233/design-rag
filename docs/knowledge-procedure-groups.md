# 工作知识助手的流程分块

新草稿在保存时按明确标题或 Word 标题样式生成待核对分组。编辑窗口可调整分组归属、从某一段拆分流程、修改流程名称、调整段落顺序、分割或合并同页相邻段落，并分别维护步骤图片和章节图片。保存后须逐组核对，再从资料页进入发布前预览。确认过的流程内容再次编辑后需重新核对。

每个分组写入一个父块，子块逐段建立向量和稀疏索引。命中子块后读取所属父块，单个父块超过 20 段时只在该父块内部翻页；跨流程的命中分别返回。`context_page` 用于显式续页，未指定时从命中位置开始。旧版已发布记录没有分组字段，继续按原有 20 段批次读取；不会自动改写或重建这些资料。

旧的 `PUT /entries/{id}/steps` 请求仍可使用，未提交分组时生成待确认草稿；新请求可传 `groups`（id、title、start、end、confirmed）。保存后使用 `GET /entries/{id}` 取得实际分组。已存在的章节图片字段应随步骤保存。新分组未确认时发布接口拒绝发布。

页码只记录文档实际提供或人工填写的信息；本功能不推断图片含义、不发送内容到外部模型。PDF/PPT 逐页渲染和钉钉图片投递没有随此阶段接入。真实课程文件尚未入库。

检查：`pytest apps/backend/tests/test_manual_groups.py apps/backend/tests/test_manual_library.py apps/backend/tests/test_manual_hybrid.py apps/backend/tests/test_langbot_knowledge.py -q`；服务启动后运行 `node scripts/check-manual-groups-ui.cjs`（页面使用内存模拟接口，不写入资料库）。
