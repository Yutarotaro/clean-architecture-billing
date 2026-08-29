// Package ids は usecase.IDGenerator の実装を持つ。
package ids

import (
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"sync/atomic"
)

// Random は暗号論的乱数から ID を作る。本番用。
type Random struct{}

// NewID は 16 バイトの乱数を 16 進文字列にして返す。
func (Random) NewID() string {
	buf := make([]byte, 16)
	if _, err := rand.Read(buf); err != nil {
		// crypto/rand の失敗はプロセスが続行できない事態を意味する。
		// ここでデフォルト値を返すと、重複した ID が静かに発行されることになる。
		panic(fmt.Sprintf("ids: cannot read random bytes: %v", err))
	}
	return hex.EncodeToString(buf)
}

// Sequential は id-1, id-2 のように読める ID を返す。テスト用。
//
// テストが落ちたときに a3f1c8e2-... が並んでいても何も分からない。連番なら
// 「2 件目の請求書が作られていない」が一目で読める。
type Sequential struct {
	prefix  string
	counter atomic.Int64
}

// NewSequential は連番の採番器を作る。
func NewSequential(prefix string) *Sequential {
	return &Sequential{prefix: prefix}
}

// NewID は次の連番を返す。
func (s *Sequential) NewID() string {
	return fmt.Sprintf("%s-%d", s.prefix, s.counter.Add(1))
}
