package domain

import "time"

// SubscriptionStatus は契約の状態。
type SubscriptionStatus string

const (
	// StatusTrialing は無料試用中。期間は進むが請求は起きない。
	StatusTrialing SubscriptionStatus = "trialing"
	// StatusActive は正常に課金されている状態。
	StatusActive SubscriptionStatus = "active"
	// StatusPastDue は支払いに失敗した状態。猶予期間のあいだはサービスを止めず再試行する。
	StatusPastDue SubscriptionStatus = "past_due"
	// StatusCanceled は解約済み。終端状態であり、ここから戻ることはない。
	StatusCanceled SubscriptionStatus = "canceled"
)

// GracePeriod は支払い失敗から解約に至るまでの猶予。
const GracePeriod = 14 * 24 * time.Hour

// Event は契約に起きた出来事。
//
// 「メールを送る」「Slack に通知する」といった副作用をドメインに書かないための逃げ道。
// ドメインは起きたことを記録するだけで、それを何に使うかは外側が決める。
type Event struct {
	Name           string
	SubscriptionID SubscriptionID
	OccurredAt     time.Time
}

// Subscription は契約 1 件を表す集約。守る不変条件は「状態遷移が仕様どおりであること」。
//
// プランは PlanID で参照するだけで Plan を抱え込まない。Plan は別の集約であり、
// ここに実体を持たせると「契約を 1 件読むためにプランも必ず読む」という結合が生まれる。
// Plan の中身が必要な操作では、ユースケースが読んで引数で渡す。
type Subscription struct {
	ID                SubscriptionID
	CustomerID        CustomerID
	PlanID            PlanID
	Status            SubscriptionStatus
	CurrentPeriod     BillingPeriod
	CancelAtPeriodEnd bool
	PastDueSince      *time.Time
	CanceledAt        *time.Time
	TrialEnd          *time.Time

	events []Event
}

// Subscribe は新規契約を開始する。trial が 0 なら試用期間なし。
func Subscribe(id SubscriptionID, customerID CustomerID, plan Plan, at time.Time, trial time.Duration) (*Subscription, error) {
	at = EnsureUTC(at)
	if trial < 0 {
		return nil, Invalid("trial must not be negative, got %s", trial)
	}

	var (
		period   BillingPeriod
		status   SubscriptionStatus
		trialEnd *time.Time
		err      error
	)
	if trial == 0 {
		period, err = PeriodStartingAt(at, plan.Interval)
		status = StatusActive
	} else {
		// 試用期間そのものを 1 つの請求期間として扱う。試用が終わった瞬間に更新が走り、
		// そこで初めて有料の期間が始まる。
		period, err = NewBillingPeriod(at, at.Add(trial))
		status = StatusTrialing
		end := period.End
		trialEnd = &end
	}
	if err != nil {
		return nil, err
	}

	s := &Subscription{
		ID:            id,
		CustomerID:    customerID,
		PlanID:        plan.ID,
		Status:        status,
		CurrentPeriod: period,
		TrialEnd:      trialEnd,
	}
	s.record("subscription.created", at)
	return s, nil
}

// IsTerminated は終端状態かを返す。
func (s *Subscription) IsTerminated() bool {
	return s.Status == StatusCanceled
}

// IsDue は更新処理の対象かを返す。解約済みは対象外。
func (s *Subscription) IsDue(at time.Time) bool {
	return !s.IsTerminated() && s.CurrentPeriod.IsDue(at)
}

// ChangePlan はプランを変更し、その期間ぶんの差額を返す。
//
// 差額の請求書を作るのはこの集約の仕事ではない。返すのは「いくらか」という事実だけで、
// それを請求書にするか次回請求に繰り越すかは呼び出し側が決める。
func (s *Subscription) ChangePlan(currentPlan, newPlan Plan, at time.Time) (Proration, error) {
	at = EnsureUTC(at)
	if s.IsTerminated() {
		return Proration{}, IllegalTransition("subscription", string(s.Status), "change plan of")
	}
	if currentPlan.ID != s.PlanID {
		return Proration{}, Invalid("current plan %q does not match subscription plan %q",
			currentPlan.ID, s.PlanID)
	}
	if newPlan.ID == s.PlanID {
		return Proration{}, Invalid("already on plan %q", newPlan.ID)
	}
	if newPlan.Price.Currency != currentPlan.Price.Currency {
		return Proration{}, Invalid("cannot change to a plan in a different currency")
	}

	var (
		proration Proration
		err       error
	)
	if s.Status == StatusTrialing {
		// 試用中はまだ 1 円も請求していないので、返すものも取るものもない。
		zero := currentPlan.Price.Zero()
		proration = Proration{Credit: zero, Charge: zero}
	} else {
		proration, err = Prorate(s.CurrentPeriod, at, currentPlan.Price, newPlan.Price)
		if err != nil {
			return Proration{}, err
		}
	}

	s.PlanID = newPlan.ID
	s.record("subscription.plan_changed", at)
	return proration, nil
}

// Cancel は解約する。immediately が false なら期末解約（支払い済みの期間は使い切れる）。
func (s *Subscription) Cancel(at time.Time, immediately bool) error {
	at = EnsureUTC(at)
	if s.IsTerminated() {
		return IllegalTransition("subscription", string(s.Status), "cancel")
	}
	if immediately {
		s.Status = StatusCanceled
		s.CanceledAt = &at
		s.CancelAtPeriodEnd = false
		s.record("subscription.canceled", at)
		return nil
	}
	s.CancelAtPeriodEnd = true
	s.record("subscription.cancel_scheduled", at)
	return nil
}

// MarkPaymentFailed は支払い失敗を記録する。
func (s *Subscription) MarkPaymentFailed(at time.Time) error {
	at = EnsureUTC(at)
	if s.Status != StatusActive && s.Status != StatusPastDue {
		return IllegalTransition("subscription", string(s.Status), "mark payment failed on")
	}
	if s.Status == StatusPastDue {
		// すでに延滞中。猶予の起点は最初の失敗のままにする。ここを更新すると、
		// 再試行のたびに猶予が延びて永遠に解約されない契約ができる。
		return nil
	}
	s.Status = StatusPastDue
	s.PastDueSince = &at
	s.record("subscription.payment_failed", at)
	return nil
}

// MarkPaymentSucceeded は支払い成功を記録する。
func (s *Subscription) MarkPaymentSucceeded(at time.Time) error {
	at = EnsureUTC(at)
	if s.IsTerminated() {
		return IllegalTransition("subscription", string(s.Status), "mark payment succeeded on")
	}
	s.Status = StatusActive
	s.PastDueSince = nil
	s.record("subscription.payment_succeeded", at)
	return nil
}

// ExpireIfGraceOver は延滞したまま猶予を過ぎていれば解約する。解約したら true。
func (s *Subscription) ExpireIfGraceOver(at time.Time, grace time.Duration) bool {
	at = EnsureUTC(at)
	if s.Status != StatusPastDue || s.PastDueSince == nil {
		return false
	}
	if at.Sub(*s.PastDueSince) < grace {
		return false
	}
	s.Status = StatusCanceled
	s.CanceledAt = &at
	s.record("subscription.canceled_for_nonpayment", at)
	return true
}

// Renew は請求期間を次に進める。次の期間で課金が必要なら true を返す。
//
// 期末解約が予約されていればここで終端に落とす。
func (s *Subscription) Renew(plan Plan, at time.Time) (bool, error) {
	at = EnsureUTC(at)
	if s.IsTerminated() {
		return false, IllegalTransition("subscription", string(s.Status), "renew")
	}
	if !s.CurrentPeriod.IsDue(at) {
		return false, Invalid("period ending %s is not due at %s",
			s.CurrentPeriod.End.Format(time.RFC3339), at.Format(time.RFC3339))
	}
	if plan.ID != s.PlanID {
		return false, Invalid("plan %q does not match subscription %q", plan.ID, s.PlanID)
	}

	if s.CancelAtPeriodEnd {
		s.Status = StatusCanceled
		end := s.CurrentPeriod.End
		s.CanceledAt = &end
		s.record("subscription.canceled", at)
		return false, nil
	}

	// 新しい期間の起点は「今」ではなく「前の期間の終わり」。バッチが数時間遅れて
	// 動いても請求日がずれないようにするための、地味だが重要な一行。
	next, err := PeriodStartingAt(s.CurrentPeriod.End, plan.Interval)
	if err != nil {
		return false, err
	}
	s.CurrentPeriod = next
	s.Status = StatusActive
	s.record("subscription.renewed", at)
	return true, nil
}

// PullEvents は溜まった出来事を取り出して空にする。
func (s *Subscription) PullEvents() []Event {
	drained := s.events
	s.events = nil
	return drained
}

func (s *Subscription) record(name string, at time.Time) {
	s.events = append(s.events, Event{Name: name, SubscriptionID: s.ID, OccurredAt: at})
}
