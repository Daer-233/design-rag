"""LangBot Protocol v1 Runner. No model calls or mutation of knowledge records."""
from __future__ import annotations

import base64
import hashlib
import time
from typing import AsyncGenerator
from uuid import UUID

from langbot_plugin.api.definition.components.runner import Runner, RunnerContext, RunnerResult
from langbot_plugin.api.entities.builtin.provider.message import Message, ContentElement

from .client import KnowledgeClient, KnowledgeError

PAGE_SIZE = 4
STATE_TTL = 1800


def text_parts(text, limit=1600):
    """Split without dropping any original characters."""
    for start in range(0, len(text), limit):
        yield ContentElement.from_text(text[start:start + limit])


def session_key(ctx):
    actor = getattr(ctx.actor, 'actor_id', None)
    if not actor:
        return None
    return 'knowledge_assistant.' + hashlib.sha256(str(actor).encode()).hexdigest()[:24]


class KnowledgeRunner(Runner):
    async def run(self, ctx: RunnerContext) -> AsyncGenerator[RunnerResult, None]:
        config = dict(ctx.config)
        question = (ctx.input.to_text() or '').strip()
        key = session_key(ctx)
        state_available = bool(key and ctx.conversation and ctx.context.available_apis.state)
        saved = {}
        if state_available:
            try:
                saved = (await ctx.state_get('conversation', key)).get('value') or {}
                if not isinstance(saved, dict) or time.time() - float(saved.get('time', 0)) > STATE_TTL:
                    saved = {}
            except Exception:
                state_available = False
        try:
            payload = {'question': question, 'source_only': True}
            for name in ('category_id', 'product', 'version'):
                value = str(config.get(name) or '').strip()
                if value:
                    payload[name] = str(UUID(value)) if name == 'category_id' else value
            page = 1
            if question in {'帮助', '/help', '/知识', '知识帮助'}:
                yield self.final(ctx, '工作知识助手：直接提问即可查询已发布资料，返回原文、图片与来源。'
                                 '\n有多个版本时回复“选择 1”；长内容回复“下一页”或“上一页”。'
                                 '\n不自动补写制度、操作步骤或审批结论；不上传聊天附件、不执行外部解析。')
                return
            if not question or len(question) > 4000:
                yield self.final(ctx, '请输入不超过 4000 字的工作知识问题；当前运行器只接收文字问题。')
                return
            if question in {'下一页', '继续阅读', '上一页'}:
                if not saved.get('entry_id') or not saved.get('question'):
                    yield self.final(ctx, '没有可继续阅读的查询，或会话已过期。请重新提问。')
                    return
                page = saved['page'] + (-1 if question == '上一页' else 1)
                if page < 1 or page > saved['pages']:
                    yield self.final(ctx, '已经到达首页或末页。可重新提问以查询其他资料。')
                    return
                payload.update(question=saved['question'], entry_id=saved['entry_id'])
            elif question.startswith('选择 '):
                try:
                    choice = int(question[3:]) - 1
                    options = saved.get('options', [])
                    if choice < 0 or choice >= len(options):
                        raise ValueError('invalid choice')
                    payload.update(question=saved['question'], entry_id=options[choice])
                except (ValueError, KeyError, TypeError):
                    yield self.final(ctx, '版本选择无效或已过期，请先重新提问，再按提示回复“选择 1”。')
                    return
            payload['page'] = (page - 1) // 5 + 1
            async with KnowledgeClient(config) as client:
                answer = await client.ask(payload)
                status = answer['status']
                new_state = {}
                if status == 'choose_version':
                    options = answer.get('options', [])
                    lines = ['找到多个适用版本，请选择后继续：']
                    for i, option in enumerate(options, 1):
                        lines.append(f"{i}. {option['title']} · {option.get('product') or '通用'} · {option.get('version') or '未标注版本'}")
                    lines.append('回复“选择 1”等编号。' if state_available else '当前会话不支持保存选择，请在问题中写明版本，或在运行器配置中限定版本。')
                    elements = [ContentElement.from_text('\n'.join(lines))]
                    new_state = {'question': payload['question'], 'options': [o['id'] for o in options]}
                elif status == 'insufficient_evidence':
                    elements = [ContentElement.from_text('当前已发布知识中没有足够证据。请补充适用系统、对象、版本或资料；未生成替代做法。')]
                else:
                    entry = answer['entry']
                    total = answer['total_steps']
                    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
                    if page > pages:
                        yield self.final(ctx, '资料内容已更新，请重新提问后继续阅读。')
                        return
                    offset = ((page - 1) % 5) * PAGE_SIZE
                    steps = answer['steps'][offset:offset + PAGE_SIZE]
                    elements = [ContentElement.from_text(f"{entry['title']}\n适用：{entry.get('product') or '通用'} · 版本：{entry.get('version') or '未标注'}\n以下保留资料原文与来源。")]
                    remaining_images = 8
                    for index, step in enumerate(steps, (page - 1) * PAGE_SIZE + 1):
                        source = step.get('source') or {}
                        heading = f"步骤 {step['order']}" if step.get('order') is not None else f'原文片段 {index}'
                        origin = source.get('file_name') or entry['title']
                        if source.get('page'):
                            origin += f" · 第 {source['page']} 页"
                        evidence = '、'.join(step.get('evidence_ids', []))
                        elements.extend(text_parts(f"\n{heading}\n{step['instruction']}\n来源：{origin}\n证据：{evidence}"))
                        for kind, label in [('images', '对应配图'), ('section_images', '章节配图（未确认逐步对应）')]:
                            pictures = step.get(kind) or []
                            omitted = 0
                            for asset in pictures:
                                if remaining_images <= 0:
                                    omitted += 1
                                    continue
                                remaining_images -= 1
                                image = await client.image(asset)
                                elements.append(ContentElement.from_text(f"{label}：{asset.get('name') or '图片'}"))
                                if image:
                                    mime, binary = image
                                    elements.append(ContentElement.from_image_base64(
                                        f'data:{mime};base64,' + base64.b64encode(binary).decode('ascii')))
                                else:
                                    elements.append(ContentElement.from_text('图片暂不可用，文字与来源仍保留。'))
                            if omitted:
                                elements.append(ContentElement.from_text(f'另有 {omitted} 张配图未在聊天中展开，可在本机知识助手查看。'))
                    footer = f'第 {page}/{pages} 页 · 共 {total} 段原文'
                    if state_available and pages > 1:
                        footer += '\n回复“下一页”或“上一页”继续阅读。'
                    elif pages > 1:
                        footer += '\n当前会话不支持分页状态，请在本机知识助手查看完整原文。'
                    elements.append(ContentElement.from_text(footer))
                    for limitation in answer.get('limitations', []):
                        elements.append(ContentElement.from_text(str(limitation)))
                    new_state = {'question': payload['question'], 'entry_id': entry['id'], 'page': page, 'pages': pages}
                if state_available:
                    try:
                        await ctx.state_set('conversation', key, {**new_state, 'time': time.time()})
                    except Exception:
                        elements.append(ContentElement.from_text('当前会话状态保存失败；继续阅读或选择版本时如无效，请重新提问。'))
                # One final message preserves text-image association; Host owns platform delivery.
                yield RunnerResult.run_completed(ctx.run_id, Message(role='assistant', content=elements))
        except (ValueError, TypeError) as error:
            yield self.final(ctx, '运行器配置或知识响应格式无效，请检查分类 ID 和服务配置。')
        except (KeyError, IndexError, AttributeError):
            yield self.final(ctx, '知识服务响应结构异常，未补写答案。请在本机知识助手核对资料。')
        except KnowledgeError as error:
            yield self.final(ctx, str(error))

    @staticmethod
    def final(ctx, text):
        return RunnerResult.run_completed(ctx.run_id, Message(role='assistant', content=text))
