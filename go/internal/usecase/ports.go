package usecase

import (
	"context"
	"time"

	"github.com/Yutarotaro/clean-architecture-billing/go/internal/domain"
)

// Clock は現在時刻の供給源。
//
// time.Now() を直接呼ぶコードはテストできない。「猶予期間を 14 日過ぎたら解約される」
// を検証するのに 14 日待つわけにはいかない。
type Clock interface {
	Now() time.Time
}

// IDGenerator は識別子の採番。ドメインが uuid を直接呼ぶと、同じ入力から同じ結果が
// 出なくなる。
type IDGenerator interface {
	NewID() string
}

// PaymentResult は決済代行からの応答。
type PaymentResult struct {
	Succeeded         bool
	ProviderReference string
	FailureReason     string
}

// ChargeRequest は決済の要求。
type ChargeRequest struct {
	CustomerID domain.CustomerID
	Amount     domain.Money
	// IdempotencyKey は必須。ネットワークが不安定なときの再送で二重に課金しないための
	// 鍵であり、任意にすると必ず渡し忘れる。
	IdempotencyKey string
	Description    string
}

// PaymentGateway は決済代行。Stripe でも PAY.JP でも、この形に合わせて実装を書く。
//
// 通信自体に失敗したときは ErrPaymentGateway を包んだエラーを返すこと。
// adapter の責任で、HTTP クライアントのエラーをこれに翻訳する。
type PaymentGateway interface {
	Charge(ctx context.Context, req ChargeRequest) (PaymentResult, error)
}

// PlanRepository はプランの出し入れ。
type PlanRepository interface {
	Get(ctx context.Context, id domain.PlanID) (*domain.Plan, error)
	ListAll(ctx context.Context) ([]domain.Plan, error)
	Add(ctx context.Context, plan domain.Plan) error
}

// SubscriptionRepository は契約の出し入れ。
//
// 見つからない場合は (nil, nil) を返す。「存在しない」は多くの場面で正常系であり、
// それをエラーにすると呼び出し側が errors.Is だらけになる。
type SubscriptionRepository interface {
	Get(ctx context.Context, id domain.SubscriptionID) (*domain.Subscription, error)
	Add(ctx context.Context, s *domain.Subscription) error
	Save(ctx context.Context, s *domain.Subscription) error
	// ListDue は at 時点で請求期間が満了している契約を返す。
	//
	// 「全件返して呼び出し側で絞る」にしないのは、契約が 100 万件あっても動く形を
	// 最初から強制するため。絞り込みの条件はドメインの言葉で書かれており、SQL の
	// 話は実装側に閉じている。
	ListDue(ctx context.Context, at time.Time, limit int) ([]*domain.Subscription, error)
	ListPastDue(ctx context.Context, limit int) ([]*domain.Subscription, error)
}

// InvoiceRepository は請求書の出し入れ。
type InvoiceRepository interface {
	Get(ctx context.Context, id domain.InvoiceID) (*domain.Invoice, error)
	Add(ctx context.Context, invoice *domain.Invoice) error
	Save(ctx context.Context, invoice *domain.Invoice) error
	FindByIdempotencyKey(ctx context.Context, key string) (*domain.Invoice, error)
	// ListUnsettled は issuedBefore より前に発行され、まだ決着していない請求書を返す。
	//
	// 決済 API の呼び出しはトランザクションの外で行うため（ADR-0005）、「請求書は
	// 発行できたが結果を反映する前にプロセスが落ちる」窓がある。決済代行との通信自体に
	// 失敗したときも同じ状態になる。それを後から拾い直すための入口である。
	//
	// issuedBefore を取るのは、いま決済中かもしれない請求書を掴まないため。
	ListUnsettled(ctx context.Context, issuedBefore time.Time, limit int) ([]*domain.Invoice, error)
	ListForCustomer(ctx context.Context, id domain.CustomerID) ([]*domain.Invoice, error)
}

// UnitOfWork はトランザクション境界。
//
// 1 つのユースケースが「全部成功したか、全部なかったことになるか」のどちらかで
// 終わることを保証する。使う側は必ず defer で Rollback を予約し、成功したときだけ
// Commit を呼ぶ。Commit 後の Rollback は何もしない。
type UnitOfWork interface {
	Subscriptions() SubscriptionRepository
	Invoices() InvoiceRepository
	Plans() PlanRepository
	Commit() error
	Rollback() error
}

// UnitOfWorkFactory はトランザクションを 1 つ開く。
//
// 関数型にしているのは、ユースケースが「トランザクションを 2 回に分けたい」場面
// （外部 API 呼び出しを挟むとき）があるため。
type UnitOfWorkFactory func(ctx context.Context) (UnitOfWork, error)
