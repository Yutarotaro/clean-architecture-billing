package memory

import (
	"context"
	"fmt"
	"sort"
	"time"

	"github.com/Yutarotaro/clean-architecture-billing/go/internal/domain"
	"github.com/Yutarotaro/clean-architecture-billing/go/internal/infra/storage"
	"github.com/Yutarotaro/clean-architecture-billing/go/internal/usecase"
)

// staging は 1 つの UnitOfWork の中で溜めた変更。
//
// commit されるまで Database には触れない。読み取りはまずここを見るので、同じ
// トランザクションの中では自分の書き込みが見える（read-your-writes）。
type staging struct {
	plans         map[domain.PlanID]domain.Plan
	subscriptions map[domain.SubscriptionID]*domain.Subscription
	invoices      map[domain.InvoiceID]*domain.Invoice
	versions      map[string]int
}

func newStaging() *staging {
	return &staging{
		plans:         map[domain.PlanID]domain.Plan{},
		subscriptions: map[domain.SubscriptionID]*domain.Subscription{},
		invoices:      map[domain.InvoiceID]*domain.Invoice{},
		versions:      map[string]int{},
	}
}

// versionTracker は読み出した集約のバージョンを覚えておく。
//
// ドメインオブジェクトに version フィールドを持たせない代わりに、リポジトリが
// 「この UnitOfWork の中で、この ID をバージョン幾つで読んだか」を記録する。
// ドメインモデルは楽観ロックの存在を最後まで知らない。
type versionTracker struct {
	entity   string
	versions map[string]int
}

func newVersionTracker(entity string) *versionTracker {
	return &versionTracker{entity: entity, versions: map[string]int{}}
}

func (t *versionTracker) remember(id string, version int) {
	t.versions[id] = version
}

func (t *versionTracker) expected(id string) (int, error) {
	version, ok := t.versions[id]
	if !ok {
		// 読まずに更新しようとしている。この状態では「誰も触っていないこと」を
		// 保証できないので、上書きせずに落とす。
		return 0, usecase.ConcurrencyConflict(t.entity, id)
	}
	return version, nil
}

func (t *versionTracker) bump(id string) int {
	t.versions[id]++
	return t.versions[id]
}

func (t *versionTracker) conflict(id string) error {
	return usecase.ConcurrencyConflict(t.entity, id)
}

type planRepo struct {
	db      *Database
	staging *staging
}

func (r *planRepo) Get(_ context.Context, id domain.PlanID) (*domain.Plan, error) {
	if plan, ok := r.staging.plans[id]; ok {
		return &plan, nil
	}
	r.db.mu.RLock()
	defer r.db.mu.RUnlock()
	plan, ok := r.db.plans[id]
	if !ok {
		return nil, nil
	}
	return &plan, nil
}

func (r *planRepo) ListAll(_ context.Context) ([]domain.Plan, error) {
	r.db.mu.RLock()
	merged := map[domain.PlanID]domain.Plan{}
	for id, plan := range r.db.plans {
		merged[id] = plan
	}
	r.db.mu.RUnlock()
	for id, plan := range r.staging.plans {
		merged[id] = plan
	}

	plans := make([]domain.Plan, 0, len(merged))
	for _, plan := range merged {
		plans = append(plans, plan)
	}
	sort.Slice(plans, func(i, j int) bool { return plans[i].ID < plans[j].ID })
	return plans, nil
}

func (r *planRepo) Add(_ context.Context, plan domain.Plan) error {
	r.staging.plans[plan.ID] = plan
	return nil
}

type subscriptionRepo struct {
	db       *Database
	staging  *staging
	versions *versionTracker
}

func (r *subscriptionRepo) Get(
	_ context.Context, id domain.SubscriptionID,
) (*domain.Subscription, error) {
	if staged, ok := r.staging.subscriptions[id]; ok {
		r.versions.remember(string(id), r.staging.versions[string(id)])
		return cloneSubscription(staged), nil
	}
	r.db.mu.RLock()
	defer r.db.mu.RUnlock()
	stored, ok := r.db.subscriptions[id]
	if !ok {
		return nil, nil
	}
	r.versions.remember(string(id), r.db.versions[string(id)])
	return cloneSubscription(stored), nil
}

func (r *subscriptionRepo) Add(_ context.Context, s *domain.Subscription) error {
	id := string(s.ID)
	if r.exists(s.ID) {
		return fmt.Errorf("%w: subscription %q already exists", storage.ErrDuplicate, id)
	}
	r.staging.subscriptions[s.ID] = cloneSubscription(s)
	r.staging.versions[id] = 1
	r.versions.remember(id, 1)
	return nil
}

func (r *subscriptionRepo) Save(_ context.Context, s *domain.Subscription) error {
	id := string(s.ID)
	if !r.exists(s.ID) {
		return fmt.Errorf("%w: subscription %q does not exist", storage.ErrUnknown, id)
	}
	expected, err := r.versions.expected(id)
	if err != nil {
		return err
	}
	// 楽観ロックの判定は、staging ではなくコミット済みの実体に対して行う。staging だけを
	// 見ていると、他のトランザクションが先に更新したことに永遠に気づけない。
	if baseline, ok := r.committedVersion(id); ok && baseline != expected {
		return r.versions.conflict(id)
	}
	r.staging.subscriptions[s.ID] = cloneSubscription(s)
	r.staging.versions[id] = r.versions.bump(id)
	return nil
}

func (r *subscriptionRepo) ListDue(
	_ context.Context, at time.Time, limit int,
) ([]*domain.Subscription, error) {
	found := r.selectSubscriptions(func(s *domain.Subscription) bool { return s.IsDue(at) })
	sort.Slice(found, func(i, j int) bool {
		return found[i].CurrentPeriod.End.Before(found[j].CurrentPeriod.End)
	})
	return r.take(found, limit), nil
}

func (r *subscriptionRepo) ListPastDue(
	_ context.Context, limit int,
) ([]*domain.Subscription, error) {
	found := r.selectSubscriptions(func(s *domain.Subscription) bool {
		return s.Status == domain.StatusPastDue
	})
	sort.Slice(found, func(i, j int) bool {
		left, right := found[i].PastDueSince, found[j].PastDueSince
		if left == nil || right == nil {
			return found[i].ID < found[j].ID
		}
		return left.Before(*right)
	})
	return r.take(found, limit), nil
}

func (r *subscriptionRepo) selectSubscriptions(
	keep func(*domain.Subscription) bool,
) []*domain.Subscription {
	r.db.mu.RLock()
	merged := map[domain.SubscriptionID]*domain.Subscription{}
	for id, s := range r.db.subscriptions {
		merged[id] = s
	}
	r.db.mu.RUnlock()
	for id, s := range r.staging.subscriptions {
		merged[id] = s
	}

	var found []*domain.Subscription
	for _, s := range merged {
		if keep(s) {
			found = append(found, cloneSubscription(s))
		}
	}
	return found
}

func (r *subscriptionRepo) take(all []*domain.Subscription, limit int) []*domain.Subscription {
	if limit > 0 && len(all) > limit {
		all = all[:limit]
	}
	for _, s := range all {
		id := string(s.ID)
		if version, ok := r.staging.versions[id]; ok {
			r.versions.remember(id, version)
			continue
		}
		if version, ok := r.committedVersion(id); ok {
			r.versions.remember(id, version)
		}
	}
	return all
}

func (r *subscriptionRepo) exists(id domain.SubscriptionID) bool {
	if _, ok := r.staging.subscriptions[id]; ok {
		return true
	}
	r.db.mu.RLock()
	defer r.db.mu.RUnlock()
	_, ok := r.db.subscriptions[id]
	return ok
}

func (r *subscriptionRepo) committedVersion(id string) (int, bool) {
	r.db.mu.RLock()
	defer r.db.mu.RUnlock()
	version, ok := r.db.versions[id]
	return version, ok
}
