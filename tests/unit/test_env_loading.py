"""验证 .env 加载机制。"""

import os
import tempfile
from pathlib import Path

import pytest
from dotenv import load_dotenv


class TestDotenvLoading:
    """确保 load_dotenv 能把 .env 写入 os.environ，供业务代码使用。"""

    def test_load_dotenv_populates_os_environ(self, tmp_path: Path):
        """创建临时 .env，验证 load_dotenv 后 os.getenv 能读到值。"""
        env_file = tmp_path / ".env"
        env_file.write_text(
            "KUZU_DB_PATH=D:/test/bot.kuzu\n"
            "LLM_API_KEY=sk-test123\n"
            "SCHEDULED_CHECK_INTERVAL=60\n",
            encoding="utf-8",
        )

        # 清理可能存在的环境变量
        for key in ("KUZU_DB_PATH", "LLM_API_KEY", "SCHEDULED_CHECK_INTERVAL"):
            os.environ.pop(key, None)

        load_dotenv(dotenv_path=env_file, override=True)

        assert os.getenv("KUZU_DB_PATH") == "D:/test/bot.kuzu"
        assert os.getenv("LLM_API_KEY") == "sk-test123"
        assert os.getenv("SCHEDULED_CHECK_INTERVAL") == "60"
