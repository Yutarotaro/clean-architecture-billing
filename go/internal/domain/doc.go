// Package domain はビジネスルールだけを持つ。
//
// このパッケージが import してよいのは Go の標準ライブラリだけである。データベースも
// HTTP もここには現れない。制約は internal/domain/layers_test.go で機械的に検査している。
//
// Python 版との違いが 1 つある。リポジトリのインターフェースをこのパッケージではなく
// usecase パッケージに置いていることで、これは Go の慣習
// （インターフェースは実装側ではなく利用側で定義する）に従った結果である。
// 詳細は docs/adr/0002-where-interfaces-live.md を参照。
package domain
