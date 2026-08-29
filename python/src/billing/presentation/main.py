"""``uvicorn billing.presentation.main:app`` の入口。"""

from __future__ import annotations

from billing.presentation.app import create_app

app = create_app()
