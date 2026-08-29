package storage

import (
	"fmt"
	"time"
)

// TimeLayout は永続化するときの時刻の表現。
//
// UTC の固定長 ISO 8601 にする。桁数を固定しているのは、文字列の辞書順と時刻の順序を
// 一致させるため。これが崩れると "WHERE period_end <= ?" が静かに嘘をつく。
const TimeLayout = "2006-01-02T15:04:05.000000Z"

// FormatTime は時刻を保存用の文字列にする。
func FormatTime(t time.Time) string {
	return t.UTC().Format(TimeLayout)
}

// FormatTimePtr は省略可能な時刻を文字列にする。nil なら nil を返す。
func FormatTimePtr(t *time.Time) any {
	if t == nil {
		return nil
	}
	return FormatTime(*t)
}

// ParseTime は保存された文字列を時刻に戻す。
func ParseTime(value string) (time.Time, error) {
	parsed, err := time.Parse(TimeLayout, value)
	if err != nil {
		return time.Time{}, fmt.Errorf("storage: cannot parse timestamp %q: %w", value, err)
	}
	return parsed.UTC(), nil
}

// ParseTimePtr は省略可能な文字列を時刻に戻す。
func ParseTimePtr(value *string) (*time.Time, error) {
	if value == nil {
		return nil, nil
	}
	parsed, err := ParseTime(*value)
	if err != nil {
		return nil, err
	}
	return &parsed, nil
}
