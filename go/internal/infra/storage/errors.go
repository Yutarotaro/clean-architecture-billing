// Package storage は永続化実装が共有する規約を持つ。
//
// 実装ごとに別のエラーを返すと、「memory では通るが SQLite では落ちる」テストが
// 生まれる。3 実装で同じ契約テストを回すために、失敗の表現をここに集約している。
package storage

import "errors"

var (
	// ErrDuplicate はすでに存在する ID、またはすでに使われた冪等キーで作ろうとしたことを表す。
	//
	// ユースケースが捕まえることは想定していない。捕まえるべきもの（楽観ロックの衝突）は
	// usecase.ErrConcurrencyConflict として定義してある。
	ErrDuplicate = errors.New("duplicate entity")
	// ErrUnknown は存在しない集約を save しようとしたことを表す。
	ErrUnknown = errors.New("unknown entity")
)
