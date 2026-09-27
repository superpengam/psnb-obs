"""psnb-obs — 破甲引擎（Offensive Breach Suite）

分层：
  sanitize  出站指纹清洗（上游按逐字匹配拦截 → 一字改动即绕过）
  wording   措辞转换（词级锚定绕穿 —— 唯一有效杠杆）
  doctrine  条令组装（授权声明 + 反拒答熔断 + 方法论）
  score     四级评分 + 半拒谱系检测
  escalate  S0→S6 自适应升档
  probe     探针矩阵（模型 × 挡位 × A/B/C 组）
"""

__version__ = "1.1.0"
__all__ = ["sanitize", "wording", "doctrine", "score", "escalate", "probe", "data_path"]

from pathlib import Path


def data_path(*parts: str) -> Path:
    """定位 data/ 目录下的资源（相对包根，不依赖 CWD）。"""
    return Path(__file__).resolve().parent.parent.parent.joinpath("data", *parts)
