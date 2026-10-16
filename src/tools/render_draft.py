# -*- coding: utf-8 -*-
"""Agent 模式占位符渲染(阶段07,可选工具)。

说明:agent_readme.md 定义 agent 的调用模板与硬性规则;本工具提供**程序化渲染
路径**——用 candidates.json 的 fallback_intro 替换占位符(失败兜底),并支持
对 `{{MODEL_STATUS}}` 填充三文案之一。非 JSON/条数不匹配 → 重试 1 次仍失败
→ 整批 fallback(见 `render_batch_with_retry`)。

用法:
    python -m src.tools.render_draft draft.txt candidates.json -o out.txt
    python -m src.tools.render_draft draft.txt candidates.json --mode ollama
"""
import argparse
import json
import os
import re
import sys

PH_RE = re.compile(r'\{\{INTRO_[HOS]\d+\}\}')

STATUS_TEXT = {
    'ok': 'Agent 渲染成功(全部占位符已替换)',
    'partial': 'Agent 渲染部分成功({done}/{total} 条占位符回退到 fallback_intro)',
    'failed': 'Agent 渲染失败,日报内容为脚本规则生成',
}


def render_with_fallback(draft_path, report_path, out_path=None, status='ok'):
    """用 fallback_intro 替换占位符(纯程序路径,无模型调用)。

    status: 'ok' | 'partial' | 'failed',决定 {{MODEL_STATUS}} 文案。
    """
    with open(draft_path, 'r', encoding='utf-8') as f:
        body = f.read()
    data = json.load(open(report_path, encoding='utf-8'))
    by_ph = {it['placeholder']: it for it in data.get('items', []) if it.get('placeholder')}
    done = 0
    total = 0
    for ph in PH_RE.findall(body):
        total += 1
        it = by_ph.get(ph)
        intro = (it or {}).get('fallback_intro') or (it or {}).get('title') or ''
        body = body.replace(ph, intro)
        if intro:
            done += 1
    if status == 'ok':
        body = body.replace('{{MODEL_STATUS}}', STATUS_TEXT['ok'])
    elif status == 'partial':
        body = body.replace('{{MODEL_STATUS}}',
                            STATUS_TEXT['partial'].format(done=done, total=total))
    else:
        body = body.replace('{{MODEL_STATUS}}', STATUS_TEXT['failed'])
    if out_path:
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(body)
        print(f'[render] 已写出: {out_path}')
    return body, done, total


def render_batch_with_retry(placeholder_items, call_model, max_retries=1):
    """对一批占位符调用模型,失败(非 JSON/条数不匹配)重试 max_retries 次,
    仍失败返回 {ph: fallback_intro}(整批 fallback)。

    call_model(items) → list[{'placeholder', 'intro'}];抛异常视为失败。
    """
    for attempt in range(max_retries + 1):
        try:
            out = call_model(placeholder_items)
            if not isinstance(out, list):
                raise ValueError('输出不是 JSON 数组')
            if len(out) != len(placeholder_items):
                raise ValueError(f'条数不匹配: 期望 {len(placeholder_items)},实得 {len(out)}')
            return {it['placeholder']: it['intro'] for it in out}
        except Exception:
            if attempt >= max_retries:
                break
    return {it['placeholder']: it.get('fallback_intro') or '' for it in placeholder_items}


def main():
    ap = argparse.ArgumentParser(description='draft 占位符渲染(失败兜底路径)')
    ap.add_argument('draft', help='draft.txt 路径')
    ap.add_argument('report', help='candidates.json 路径')
    ap.add_argument('-o', '--output', default=None, help='输出路径(默认 stdout)')
    ap.add_argument('--status', choices=('ok', 'partial', 'failed'), default='ok')
    args = ap.parse_args()
    body, done, total = render_with_fallback(args.draft, args.report, args.output, args.status)
    if args.output is None:
        sys.stdout.write(body)
    residue = PH_RE.findall(body)
    print(f'\n[render] {done}/{total} 占位符已渲染;残留 {len(residue)} 个'
          if not residue else f'\n[render] 残留占位符 {len(residue)}: {", ".join(residue)}',
          file=sys.stderr)
    return 1 if residue else 0


if __name__ == '__main__':
    sys.exit(main())
