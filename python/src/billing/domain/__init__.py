"""ドメイン層。

このパッケージは billing の他のどのパッケージにも、また FastAPI や SQLAlchemy と
いった外部ライブラリにも依存しない。import しているのは標準ライブラリだけである。
この制約は tests/unit/test_layer_dependencies.py で機械的に検査している。
"""
