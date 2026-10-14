# -*- coding: utf-8 -*-
"""配置加载:config.yaml + /home/czq/ai_news/.env(秘密)。

同时把配置喂给复用模块依赖的 ConfigLoader 单例(原 rss_fetcher 通过它读取
fetching.network_timeout / fetching.hf_daily_top_n),因此 config.yaml 保留了
fetching 段。

.env 定位顺序:
  1. 环境变量 AI_NEWS_ENV_PATH
  2. <项目根>/../../.env  (即 /home/czq/ai_news/.env)
  3. <项目根>/.env
"""
import os
import yaml
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def find_env_path():
    env_var = os.environ.get('AI_NEWS_ENV_PATH')
    if env_var:
        return env_var
    # 项目根上两级: /home/czq/ai_news/.env(code/ai-news-mvp → ai_news)
    candidates = [
        os.path.join(os.path.dirname(os.path.dirname(PROJECT_ROOT)), '.env'),
        os.path.join(PROJECT_ROOT, '.env'),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


def normalize_proxy_env():
    """显式处理 SOCKS 代理:环境里的 ALL_PROXY 是 socks:// 前缀,而 httpx 只认
    socks5://(阶段01审计发现的问题)。进程内归一化,修复复用模块的 httpx trust_env 路径。"""
    for k in ('ALL_PROXY', 'all_proxy'):
        v = os.environ.get(k)
        if v and v.startswith('socks://'):
            os.environ[k] = 'socks5://' + v[len('socks://'):]


def load_config(config_path=None):
    """加载 config.yaml 与 .env;返回配置 dict(附 _project_root 键)。"""
    config_path = config_path or os.path.join(PROJECT_ROOT, 'config.yaml')
    with open(config_path, 'r', encoding='utf-8') as f:
        cfg = yaml.safe_load(f) or {}

    env_path = find_env_path()
    if env_path:
        load_dotenv(env_path, override=True)
    normalize_proxy_env()

    # 复用模块通过 ConfigLoader 单例读取的 fetching 键必须存在
    cfg.setdefault('fetching', {})
    cfg['fetching'].setdefault('network_timeout', cfg.get('rss', {}).get('timeout', 90))
    cfg['fetching'].setdefault('hf_daily_top_n', 5)

    cfg['_project_root'] = PROJECT_ROOT
    if config_path:
        cfg['_config_path'] = os.path.abspath(config_path)

    # 喂给原项目 ConfigLoader 单例(仅当原项目 src 在 sys.path 时)
    try:
        from config_loader import ConfigLoader
        loader = ConfigLoader.get_instance()
        loader.load_config(os.path.abspath(config_path))
    except Exception:
        pass  # 不可用时,由 src/rss.py 的 ensure_reuse_path 给出明确错误
    return cfg
