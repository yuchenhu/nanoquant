"""全局配置：从 .env 加载敏感信息，提供 dataclass 访问。

用法：
    from config.settings import settings
    print(settings.tushare_token)
    print(settings.db_url)

约定：
- 敏感信息一律 os.getenv('KEY', '')，默认留空
- .env 不进 git，.env.example 进 git
- 本模块 import 时自动 load_dotenv()
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# 项目根目录（config/ 的父目录）
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 加载 .env（找不到不报错，用环境变量）
load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    """全局配置（不可变）。"""

    # ===== Tushare =====
    tushare_token: str = field(default_factory=lambda: os.getenv("TUSHARE_TOKEN", ""))
    tushare_base_url: str = field(
        default_factory=lambda: os.getenv("TUSHARE_BASE_URL", "http://api.tushare.pro")
    )

    # ===== MySQL =====
    db_host: str = field(default_factory=lambda: os.getenv("DB_HOST", "localhost"))
    db_port: int = field(default_factory=lambda: int(os.getenv("DB_PORT", "3306")))
    db_user: str = field(default_factory=lambda: os.getenv("DB_USER", "root"))
    db_password: str = field(default_factory=lambda: os.getenv("DB_PASSWORD", ""))
    db_database: str = field(default_factory=lambda: os.getenv("DB_DATABASE", "stock"))
    db_charset: str = "utf8mb4"

    # ===== 日志 =====
    log_dir: Path = field(
        default_factory=lambda: Path(os.getenv("LOG_DIR", PROJECT_ROOT / "logs"))
    )

    @property
    def db_url(self) -> str:
        """SQLAlchemy 数据库 URL。"""
        return (
            f"mysql+pymysql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_database}"
            f"?charset={self.db_charset}"
        )

    def validate(self) -> list[str]:
        """返回缺失必要配置的警告列表（空列表 = 配置齐全）。"""
        warnings: list[str] = []
        if not self.tushare_token:
            warnings.append("TUSHARE_TOKEN 未设置，接入层无法拉数")
        if not self.db_password:
            warnings.append("DB_PASSWORD 未设置，数据库连接会失败")
        return warnings


# 全局单例
settings = Settings()
