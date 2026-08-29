"""FastAPI の依存関係。

``Depends`` でユースケースそのものを組み立てず、合成ルートで組み立て済みの
コンテナから取り出すだけにしている。FastAPI の DI 機構に配線を書き始めると、
配線が HTTP 層に固定されてバッチや CLI から再利用できなくなる。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from billing.presentation.container import Container


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


ContainerDep = Annotated[Container, Depends(get_container)]
