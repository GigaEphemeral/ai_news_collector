# -*- coding: utf-8 -*-
"""候选规则标签(阶段02 新增,归入 pipeline 领域)。

把日报候选的平台/热度/趋势标签从 report.py 抽到这里,保持候选构建与展示解耦:
  - github:    趋势状态(state)+「新项目」(创建 ≤ new_days),无则「GitHub 项目」
  - hackernews: 「HN <points> 分」
  - rss:        「官方博客」
  - hf_papers:  「HF 论文」
纯规则,不落库,不调 LLM。
"""


def rule_tags(cand, cfg, state=None):
    """返回候选的规则标签列表。

    cand:统一候选 dict(需含 platform / stars / created_days 等可选字段)。
    cfg: 完整配置(dict),用于读取 new_days 阈值。
    state: 可选趋势状态(仅 GitHub 使用,来自 trends 的 project.state)。
    """
    platform = cand.get('platform')
    tags = []
    if platform == 'github':
        if state:
            tags.append(state)
        created = cand.get('created_days')
        new_days = int(cfg.get('report', {}).get(
            'new_days', cfg.get('trend', {}).get('project', {}).get('young_days', 30)))
        if created is not None and created <= new_days:
            tags.append('新项目')
        if not tags:
            tags.append('GitHub 项目')
    elif platform == 'hackernews':
        pts = cand.get('stars')
        if pts:
            tags.append(f"HN {pts} 分")
    elif platform == 'hf_papers':
        tags.append('HF 论文')
    else:
        tags.append('官方博客')
    return tags
