package domain

import "time"

// EnsureUTC は時刻を UTC に正規化する。
//
// 内部で扱う時刻はすべて UTC に揃える。ローカル時刻のまま計算すると、夏時間の
// 切り替え日に請求期間が 23 時間や 25 時間になる。Go の time.Time は常に位置情報を
// 持つので、Python 版のような naive/aware の区別は不要だが、UTC への正規化は要る。
func EnsureUTC(t time.Time) time.Time {
	return t.UTC()
}

// AddMonths は月を加算する。
//
// 1/31 の 1 か月後は 2/28（閏年なら 2/29）とする。標準の AddDate は 2/31 を 3/3 に
// 繰り上げてしまい、課金サイクルとしては誤りになる。月末の扱いは仕様そのものなので、
// ライブラリ任せにせずここに書いて明示する。
func AddMonths(t time.Time, months int) time.Time {
	t = t.UTC()
	year, month, day := t.Date()
	total := int(month) - 1 + months
	year += total / 12
	newMonth := time.Month(total%12 + 1)
	if total%12 < 0 {
		year--
		newMonth = time.Month(total%12 + 13)
	}
	if last := daysIn(year, newMonth); day > last {
		day = last
	}
	return time.Date(year, newMonth, day, t.Hour(), t.Minute(), t.Second(), t.Nanosecond(), time.UTC)
}

func daysIn(year int, month time.Month) int {
	return time.Date(year, month+1, 0, 0, 0, 0, 0, time.UTC).Day()
}
