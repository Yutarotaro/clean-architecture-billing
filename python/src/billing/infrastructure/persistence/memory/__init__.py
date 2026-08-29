"""インメモリ実装。

テストを速くするためだけのものではない。「リポジトリのインターフェースが本当に
永続化技術から独立しているか」を確かめる装置でもある。dict で実装できないメソッドが
出てきたら、それは SQL の都合がインターフェースに漏れている証拠になる。
"""

from billing.infrastructure.persistence.memory.database import MemoryDatabase
from billing.infrastructure.persistence.memory.uow import InMemoryUnitOfWork

__all__ = ["MemoryDatabase", "InMemoryUnitOfWork"]
