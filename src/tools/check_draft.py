# -*- coding: utf-8 -*-
"""draft 自检工具(阶段07,可选):验证占位符渲染后无 `{{` 残留。

用法:
    python -m src.tools.check_draft draft.txt
    python -m src.tools.check_draft draft.txt --report /path/to/candidates.json

- 无 `{{` / `}}` 残留 → 退出码 0,输出「OK」;
- 有残留 → 退出码 1,列出残留占位符(供 agent 补写或整批回退)。
"""
import argparse
import json
import os
import re
import sys

PH_RE = re.compile(r'\{\{[A-Za-z_0-9]+\}\}')


def check_draft(draft_path, report_path=None):
    """检查 draft 是否有 `{{` 残留;返回 (ok, residue_list, missing_list)。

    residue: 仍留在正文里的占位符(未渲染完成 → 问题)。
    missing: 与 candidates.json 对照,report 里声明但正文里既没有渲染结果
    (占位符已被替换)也没有残留的占位符 —— 实际上渲染后占位符必然不在正文,
    所以 missing 恒为空,仅用于区分"未渲染(残留)"与"渲染完成"两种状态。
    """
    if not os.path.exists(draft_path):
        return False, [], [f'draft 文件不存在: {draft_path}']
    with open(draft_path, 'r', encoding='utf-8') as f:
        body = f.read()
    residue = sorted(set(PH_RE.findall(body)))
    # 双重花括号残留(未闭合/残留大括号)
    braces = body.count('{{') + body.count('}}')
    missing = []
    if report_path and os.path.exists(report_path):
        try:
            data = json.load(open(report_path, encoding='utf-8'))
            declared = set()
            for it in data.get('items', []):
                if it.get('placeholder'):
                    declared.add(it['placeholder'])
            if '{{MODEL_STATUS}}' in declared:
                declared.discard('{{MODEL_STATUS}}')
            # 声明了但正文里既无残留也(理论上)已替换 → 视为已渲染;
            # 只有仍残留在正文里的才算未渲染(见 residue)
            missing = [ph for ph in sorted(declared) if ph in body]
        except (OSError, ValueError) as e:
            missing.append(f'candidates.json 读取失败: {e}')
    ok = not residue and braces == 0 and not missing
    return ok, residue, missing


def main():
    ap = argparse.ArgumentParser(description='draft 占位符自检(无 {{ 残留)')
    ap.add_argument('draft', help='渲染后的 draft 文件路径')
    ap.add_argument('--report', default=None, help='candidates.json(校验占位符一一对应)')
    args = ap.parse_args()

    ok, residue, missing = check_draft(args.draft, args.report)
    if ok:
        print('[OK] draft 无 {{ 残留,占位符全部渲染完成')
        return 0
    if residue:
        print(f'[FAIL] 残留占位符({len(residue)}): {", ".join(residue)}')
    if missing:
        print(f'[FAIL] 缺失占位符({len(missing)}): {", ".join(missing)}')
    if not residue and not missing:
        print('[FAIL] draft 存在未闭合大括号({{ 或 }})')
    return 1


if __name__ == '__main__':
    sys.exit(main())
