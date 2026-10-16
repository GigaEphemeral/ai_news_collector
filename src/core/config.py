# -*- coding: utf-8 -*-
"""配置加载:config.yaml + .env(秘密,默认取项目根上两级目录的 .env)。

(阶段02 拆分:config 归入 core 领域;复用模块依赖的 ConfigLoader 已 vendoring
到 src/core/vendor/,本模块通过包内相对 import 喂给该单例,不再依赖外部目录。)

同时把配置喂给复用模块依赖的 ConfigLoader 单例(原 rss_fetcher 通过它读取
fetching.network_timeout / fetching.hf_daily_top_n),因此 config.yaml 保留了
fetching 段。

.env 定位顺序:
  1. 环境变量 AI_NEWS_ENV_PATH
  2. <项目根>/../../.env  (项目根上两级目录的 .env)
  3. <项目根>/.env
"""
import os
import yaml
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def find_env_path():
    env_var = os.environ.get('AI_NEWS_ENV_PATH')
    if env_var:
        return env_var
    # 项目根上两级目录的 .env(项目结构 code/ai-news-mvp → code → 工作区根)
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

    # 喂给 vendored ConfigLoader 单例(rss_fetcher / content_processor 通过它读
    # fetching.network_timeout / fetching.hf_daily_top_n;包内相对 import)
    try:
        from .vendor.config_loader import ConfigLoader
        loader = ConfigLoader.get_instance()
        loader.load_config(os.path.abspath(config_path))
    except Exception:
        pass  # 单例不可用时,由使用方(collectors/rss)给出明确错误
    return cfg
