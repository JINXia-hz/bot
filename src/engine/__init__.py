"""计算引擎 - 纯函数，负责翻译、执行、事件管理。

引擎层架构：
  translator.py   → LLM 翻译（自然语言 ↔ 格式语言）
  executor.py     → 行动执行（dpⁿ + fl → dpᵐ + dlʷ + log）
  event_manager.py → 事件生命周期管理
"""

from src.engine.translator import Translator
from src.engine.executor import Executor
from src.engine.event_manager import EventManager

__all__ = ["Translator", "Executor", "EventManager"]