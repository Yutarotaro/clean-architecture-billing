package sqlite

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"time"

	"github.com/Yutarotaro/clean-architecture-billing/go/internal/domain"
	"github.com/Yutarotaro/clean-architecture-billing/go/internal/infra/storage"
	"github.com/Yutarotaro/clean-architecture-billing/go/internal/usecase"
	sqlitedriver "modernc.org/sqlite"
)

// liveStatuses は更新バッチの対象になりうる状態。解約済みは二度と対象にならない。
var liveStatuses = []any{
	string(domain.StatusActive),
	string(domain.StatusTrialing),
	string(domain.StatusPastDue),
}

// versionTracker は読み出した集約のバージョンを覚えておく。memory 実装と同じ考え方。
type versionTracker struct {
	entity   string
	versions map[string]int
}

func newVersionTracker(entity string) *versionTracker {
	return &versionTracker{entity: entity, versions: map[string]int{}}
}

func (t *versionTracker) remember(id string, version int) { t.versions[id] = version }

func (t *versionTracker) expected(id string) (int, error) {
	version, ok := t.versions[id]
	if !ok {
		return 0, usecase.ConcurrencyConflict(t.entity, id)
	}
	return version, nil
}

func (t *versionTracker) bump(id string) { t.versions[id]++ }

func (t *versionTracker) conflict(id string) error {
	return usecase.ConcurrencyConflict(t.entity, id)
}

// SQLite の拡張エラーコード。
const (
	// sqliteConstraint は主キーや UNIQUE の衝突（SQLITE_CONSTRAINT）。
	sqliteConstraint = 19
	// sqliteBusySnapshot は SQLITE_BUSY_SNAPSHOT。
	//
	// WAL モードで、古いスナップショットを読んだままのトランザクションが書き込もうと
	// したときに返る。SQLite 自身が lost update を防いでいる状態であり、意味としては
	// 楽観ロックの衝突とまったく同じなので、そのように翻訳する。
	sqliteBusySnapshot = 517
)

// asConflict は SQLite の競合を usecase.ErrConcurrencyConflict に翻訳する。
//
// version 列の突き合わせで検出するより先に、データベースが弾いてくれることがある。
// どちらの経路で分かっても、上の層に届く言葉は同じでなければならない。
func asConflict(err error, entity, id string) error {
	if err == nil {
		return nil
	}
	var sqliteErr *sqlitedriver.Error
	if errors.As(err, &sqliteErr) && sqliteErr.Code() == sqliteBusySnapshot {
		return usecase.ConcurrencyConflict(entity, id)
	}
	return err
}

// asDuplicate は一意制約違反を storage.ErrDuplicate に翻訳する。
//
// ドライバ固有の例外をここで止める。上の層に modernc.org/sqlite の型が漏れると、
// ユースケースがドライバを import する羽目になる。
func asDuplicate(err error, format string, args ...any) error {
	if err == nil {
		return nil
	}
	var sqliteErr *sqlitedriver.Error
	if errors.As(err, &sqliteErr) {
		// 下位 8 ビットが SQLITE_CONSTRAINT(19) なら、主キーか UNIQUE の衝突。
		if sqliteErr.Code()&0xff == sqliteConstraint {
			return fmt.Errorf("%w: %s", storage.ErrDuplicate, fmt.Sprintf(format, args...))
		}
	}
	return err
}

type planRepo struct{ tx *sql.Tx }

func (r *planRepo) Get(ctx context.Context, id domain.PlanID) (*domain.Plan, error) {
	row := r.tx.QueryRowContext(ctx,
		`SELECT id, name, price_amount, price_currency, interval FROM plans WHERE id = ?`,
		string(id))
	plan, err := scanPlan(row)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return plan, err
}

func (r *planRepo) ListAll(ctx context.Context) ([]domain.Plan, error) {
	rows, err := r.tx.QueryContext(ctx,
		`SELECT id, name, price_amount, price_currency, interval FROM plans ORDER BY id`)
	if err != nil {
		return nil, err
	}
	defer func() { _ = rows.Close() }()

	var plans []domain.Plan
	for rows.Next() {
		plan, err := scanPlan(rows)
		if err != nil {
			return nil, err
		}
		plans = append(plans, *plan)
	}
	return plans, rows.Err()
}

func (r *planRepo) Add(ctx context.Context, plan domain.Plan) error {
	_, err := r.tx.ExecContext(ctx,
		`INSERT INTO plans (id, name, price_amount, price_currency, interval) VALUES (?, ?, ?, ?, ?)`,
		string(plan.ID), plan.Name, plan.Price.Amount, plan.Price.Currency, string(plan.Interval))
	return asDuplicate(err, "plan %q already exists", plan.ID)
}

type scanner interface {
	Scan(dest ...any) error
}

func scanPlan(row scanner) (*domain.Plan, error) {
	var (
		id, name, currency, interval string
		amount                       int64
	)
	if err := row.Scan(&id, &name, &amount, &currency, &interval); err != nil {
		return nil, err
	}
	money, err := domain.NewMoney(amount, currency)
	if err != nil {
		return nil, err
	}
	return &domain.Plan{
		ID:       domain.PlanID(id),
		Name:     name,
		Price:    money,
		Interval: domain.BillingInterval(interval),
	}, nil
}

const subscriptionColumns = `id, customer_id, plan_id, status, period_start, period_end,
	cancel_at_period_end, past_due_since, canceled_at, trial_end, version`

type subscriptionRepo struct {
	tx       *sql.Tx
	versions *versionTracker
}

func (r *subscriptionRepo) Get(
	ctx context.Context, id domain.SubscriptionID,
) (*domain.Subscription, error) {
	row := r.tx.QueryRowContext(ctx,
		`SELECT `+subscriptionColumns+` FROM subscriptions WHERE id = ?`, string(id))
	subscription, err := r.scan(row)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return subscription, err
}

func (r *subscriptionRepo) Add(ctx context.Context, s *domain.Subscription) error {
	args := append(subscriptionArgs(s), 1)
	_, err := r.tx.ExecContext(ctx,
		`INSERT INTO subscriptions (`+subscriptionColumns+`)
		 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`, args...)
	if err != nil {
		return asDuplicate(err, "subscription %q already exists", s.ID)
	}
	r.versions.remember(string(s.ID), 1)
	return nil
}

func (r *subscriptionRepo) Save(ctx context.Context, s *domain.Subscription) error {
	id := string(s.ID)
	expected, err := r.versions.expected(id)
	if err != nil {
		return err
	}
	args := append(subscriptionArgs(s), expected+1, id, expected)
	result, err := r.tx.ExecContext(ctx,
		`UPDATE subscriptions SET
			id = ?, customer_id = ?, plan_id = ?, status = ?, period_start = ?, period_end = ?,
			cancel_at_period_end = ?, past_due_since = ?, canceled_at = ?, trial_end = ?, version = ?
		 WHERE id = ? AND version = ?`, args...)
	if err != nil {
		return asConflict(err, "subscription", id)
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return err
	}
	if affected != 1 {
		// 誰かが先に更新した。ここで黙って上書きすると、相手の変更が消える。
		return r.versions.conflict(id)
	}
	r.versions.bump(id)
	return nil
}

func (r *subscriptionRepo) ListDue(
	ctx context.Context, at time.Time, limit int,
) ([]*domain.Subscription, error) {
	args := append(append([]any{}, liveStatuses...), storage.FormatTime(at), limit)
	rows, err := r.tx.QueryContext(ctx,
		`SELECT `+subscriptionColumns+` FROM subscriptions
		 WHERE status IN (?, ?, ?) AND period_end <= ?
		 ORDER BY period_end LIMIT ?`, args...)
	if err != nil {
		return nil, err
	}
	return r.collect(rows)
}

func (r *subscriptionRepo) ListPastDue(
	ctx context.Context, limit int,
) ([]*domain.Subscription, error) {
	rows, err := r.tx.QueryContext(ctx,
		`SELECT `+subscriptionColumns+` FROM subscriptions
		 WHERE status = ? ORDER BY past_due_since LIMIT ?`,
		string(domain.StatusPastDue), limit)
	if err != nil {
		return nil, err
	}
	return r.collect(rows)
}

func (r *subscriptionRepo) collect(rows *sql.Rows) ([]*domain.Subscription, error) {
	defer func() { _ = rows.Close() }()
	var found []*domain.Subscription
	for rows.Next() {
		subscription, err := r.scan(rows)
		if err != nil {
			return nil, err
		}
		found = append(found, subscription)
	}
	return found, rows.Err()
}

func (r *subscriptionRepo) scan(row scanner) (*domain.Subscription, error) {
	var raw subscriptionRow
	if err := row.Scan(
		&raw.id, &raw.customerID, &raw.planID, &raw.status, &raw.periodStart, &raw.periodEnd,
		&raw.cancelAtPeriodEnd, &raw.pastDueSince, &raw.canceledAt, &raw.trialEnd, &raw.version,
	); err != nil {
		return nil, err
	}
	r.versions.remember(raw.id, raw.version)
	return raw.toDomain()
}
