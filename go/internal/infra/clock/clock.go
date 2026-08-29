// Package clock は usecase.Clock の実装を持つ。
package clock

import (
	"sync"
	"time"
)

// System は OS の時計を UTC で返す。本番用。
type System struct{}

// Now は現在時刻を返す。
func (System) Now() time.Time { return time.Now().UTC() }

// Fixed は時刻を止めたり任意に進めたりできる時計。テスト用。
//
// テスト専用のコードを internal/infra に置いているのは、これが「Clock の実装の一つ」
// であってテストの都合ではないため。CLI から特定日時のバッチを再現するときにも使える。
type Fixed struct {
	mu  sync.RWMutex
	now time.Time
}

// NewFixed は時刻を止めた時計を作る。
func NewFixed(at time.Time) *Fixed {
	return &Fixed{now: at.UTC()}
}

// Now は固定された時刻を返す。
func (f *Fixed) Now() time.Time {
	f.mu.RLock()
	defer f.mu.RUnlock()
	return f.now
}

// Set は時刻を設定する。
func (f *Fixed) Set(at time.Time) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.now = at.UTC()
}

// Advance は時刻を進める。
func (f *Fixed) Advance(d time.Duration) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.now = f.now.Add(d)
}
