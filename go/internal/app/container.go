// Package app は合成ルート（composition root）。
//
// DI コンテナライブラリは使っていない。使うほどの規模ではないというのもあるが、
// それ以上に「何がどこに注入されているか」がこのファイルを読むだけで分かる状態を
// 保ちたいため。DI コンテナは配線を隠す道具であって、なくす道具ではない。
//
// 依存の向きがこの 1 ファイルに集中している点に注目してほしい。ユースケースは具体的な
// 実装の名前を 1 つも知らず、それを知っているのはここだけである。
package app

import (
	"context"
	"database/sql"
	"fmt"
	"log/slog"

	adapterhttp "github.com/Yutarotaro/clean-architecture-billing/go/internal/adapter/http"
	"github.com/Yutarotaro/clean-architecture-billing/go/internal/domain"
	"github.com/Yutarotaro/clean-architecture-billing/go/internal/infra/clock"
	"github.com/Yutarotaro/clean-architecture-billing/go/internal/infra/ids"
	"github.com/Yutarotaro/clean-architecture-billing/go/internal/infra/memory"
	"github.com/Yutarotaro/clean-architecture-billing/go/internal/infra/payment"
	"github.com/Yutarotaro/clean-architecture-billing/go/internal/infra/sqlite"
	"github.com/Yutarotaro/clean-architecture-billing/go/internal/usecase"
)

// Persistence はどの永続化実装を使うか。
type Persistence string

const (
	// PersistenceMemory はプロセス内の map を使う。
	PersistenceMemory Persistence = "memory"
	// PersistenceSQLite は SQLite を使う。
	PersistenceSQLite Persistence = "sqlite"
)

// Config は実行時の設定。
//
// どの永続化実装を使うかは設定 1 つで決まる。コードを 1 行も変えずに差し替えられる
// ことが、レイヤー分割が機能している何よりの証拠になる。
type Config struct {
	Persistence Persistence
	DatabaseDSN string
	Addr        string
	SeedPlans   bool
}

// DefaultConfig はサンプルをすぐ動かせる設定を返す。
func DefaultConfig() Config {
	return Config{
		Persistence: PersistenceMemory,
		DatabaseDSN: "file:billing.db",
		Addr:        ":8080",
		SeedPlans:   true,
	}
}

// DefaultPlans はサンプルを起動してすぐ触れるようにするための初期データ。
func DefaultPlans() []domain.Plan {
	return []domain.Plan{
		{ID: "basic", Name: "Basic", Price: domain.JPY(1_000), Interval: domain.IntervalMonthly},
		{ID: "pro", Name: "Pro", Price: domain.JPY(3_000), Interval: domain.IntervalMonthly},
		{ID: "pro-yearly", Name: "Pro (yearly)", Price: domain.JPY(30_000), Interval: domain.IntervalYearly},
	}
}

// Container はアプリケーション全体の配線。
type Container struct {
	Factory  usecase.UnitOfWorkFactory
	Clock    usecase.Clock
	IDs      usecase.IDGenerator
	Gateway  usecase.PaymentGateway
	Handlers adapterhttp.Handlers

	closers []func() error
}

// Option は配線の一部を差し替える。テストから時計と決済代行を置き換えるために使う。
type Option func(*options)

type options struct {
	clock   usecase.Clock
	ids     usecase.IDGenerator
	gateway usecase.PaymentGateway
	factory usecase.UnitOfWorkFactory
	logger  *slog.Logger
}

// WithClock は時計を差し替える。
func WithClock(c usecase.Clock) Option { return func(o *options) { o.clock = c } }

// WithIDs は採番器を差し替える。
func WithIDs(g usecase.IDGenerator) Option { return func(o *options) { o.ids = g } }

// WithGateway は決済代行を差し替える。
func WithGateway(g usecase.PaymentGateway) Option { return func(o *options) { o.gateway = g } }

// WithUnitOfWorkFactory は永続化実装そのものを差し替える。
func WithUnitOfWorkFactory(f usecase.UnitOfWorkFactory) Option {
	return func(o *options) { o.factory = f }
}

// WithLogger はロガーを差し替える。
func WithLogger(l *slog.Logger) Option { return func(o *options) { o.logger = l } }

// Build は設定から実装を選んで組み立てる。
func Build(ctx context.Context, cfg Config, opts ...Option) (*Container, error) {
	resolved := options{}
	for _, opt := range opts {
		opt(&resolved)
	}

	container := &Container{
		Clock: or[usecase.Clock](resolved.clock, clock.System{}),
		IDs:   or[usecase.IDGenerator](resolved.ids, ids.Random{}),
		// 本物の決済代行の実装はこのサンプルには含まれていない。実装するときは
		// usecase.PaymentGateway を満たす型を infra/payment に足し、差し替えるのは
		// この 1 行だけになる。
		Gateway: or[usecase.PaymentGateway](resolved.gateway, payment.NewFake()),
	}

	if resolved.factory != nil {
		container.Factory = resolved.factory
	} else {
		factory, closer, err := buildFactory(ctx, cfg)
		if err != nil {
			return nil, err
		}
		container.Factory = factory
		if closer != nil {
			container.closers = append(container.closers, closer)
		}
	}

	container.Handlers = adapterhttp.Handlers{
		Subscribe: usecase.NewSubscribeToPlan(
			container.Factory, container.Clock, container.IDs, container.Gateway),
		ChangePlan: usecase.NewChangePlan(
			container.Factory, container.Clock, container.IDs, container.Gateway),
		Cancel:      usecase.NewCancelSubscription(container.Factory, container.Clock),
		RecordPayme: usecase.NewRecordPaymentResult(container.Factory, container.Clock),
		Renew: usecase.NewRenewDueSubscriptions(
			container.Factory, container.Clock, container.IDs, container.Gateway),
		Settle: usecase.NewSettleUnpaidInvoices(
			container.Factory, container.Clock, container.Gateway),
		Queries: usecase.NewQueries(container.Factory),
		Logger:  resolved.logger,
	}

	if cfg.SeedPlans {
		if err := container.SeedPlans(ctx, DefaultPlans()); err != nil {
			_ = container.Close()
			return nil, err
		}
	}
	return container, nil
}

// SeedPlans は初期プランを投入する。すでにあるものは触らない。
func (c *Container) SeedPlans(ctx context.Context, plans []domain.Plan) error {
	uow, err := c.Factory(ctx)
	if err != nil {
		return err
	}
	defer func() { _ = uow.Rollback() }()

	existing, err := uow.Plans().ListAll(ctx)
	if err != nil {
		return err
	}
	known := map[domain.PlanID]bool{}
	for _, plan := range existing {
		known[plan.ID] = true
	}
	for _, plan := range plans {
		if known[plan.ID] {
			continue
		}
		if err := uow.Plans().Add(ctx, plan); err != nil {
			return err
		}
	}
	return uow.Commit()
}

// Close は開いた資源を閉じる。
func (c *Container) Close() error {
	var firstErr error
	for _, closer := range c.closers {
		if err := closer(); err != nil && firstErr == nil {
			firstErr = err
		}
	}
	return firstErr
}

func buildFactory(
	ctx context.Context, cfg Config,
) (usecase.UnitOfWorkFactory, func() error, error) {
	switch cfg.Persistence {
	case PersistenceMemory:
		return memory.NewUnitOfWorkFactory(memory.NewDatabase()), nil, nil

	case PersistenceSQLite:
		db, err := sqlite.Open(cfg.DatabaseDSN)
		if err != nil {
			return nil, nil, err
		}
		if err := sqlite.CreateSchema(ctx, db); err != nil {
			_ = db.Close()
			return nil, nil, err
		}
		return sqlite.NewUnitOfWorkFactory(db), func() error { return closeDB(db) }, nil

	default:
		return nil, nil, fmt.Errorf("app: unknown persistence %q", cfg.Persistence)
	}
}

func closeDB(db *sql.DB) error { return db.Close() }

func or[T any](value T, fallback T) T {
	var zero T
	if any(value) == any(zero) {
		return fallback
	}
	return value
}
