package domain_test

import (
	"errors"
	"testing"
	"time"

	"github.com/Yutarotaro/clean-architecture-billing/go/internal/domain"
)

var (
	basic = domain.Plan{ID: "basic", Name: "Basic", Price: domain.JPY(1_000), Interval: domain.IntervalMonthly}
	pro   = domain.Plan{ID: "pro", Name: "Pro", Price: domain.JPY(3_000), Interval: domain.IntervalMonthly}
)

func subscribe(t *testing.T, plan domain.Plan, at time.Time, trial time.Duration) *domain.Subscription {
	t.Helper()
	s, err := domain.Subscribe("sub-1", "cus-1", plan, at, trial)
	if err != nil {
		t.Fatalf("Subscribe: %v", err)
	}
	return s
}

func TestSubscribeWithoutTrialStartsActive(t *testing.T) {
	s := subscribe(t, basic, jan, 0)

	if s.Status != domain.StatusActive {
		t.Errorf("Status = %s, want active", s.Status)
	}
	if !s.CurrentPeriod.Start.Equal(jan) || !s.CurrentPeriod.End.Equal(feb) {
		t.Errorf("period = [%s, %s), want [jan, feb)", s.CurrentPeriod.Start, s.CurrentPeriod.End)
	}
	if s.TrialEnd != nil {
		t.Errorf("TrialEnd = %v, want nil", s.TrialEnd)
	}
}

func TestSubscribeWithTrialStartsTrialing(t *testing.T) {
	s := subscribe(t, basic, jan, 14*24*time.Hour)

	if s.Status != domain.StatusTrialing {
		t.Errorf("Status = %s, want trialing", s.Status)
	}
	want := jan.AddDate(0, 0, 14)
	if !s.CurrentPeriod.End.Equal(want) {
		t.Errorf("period end = %s, want %s", s.CurrentPeriod.End, want)
	}
}

// 試用中はまだ請求していないので、返すものも取るものもない。
func TestChangePlanDuringTrialCostsNothing(t *testing.T) {
	s := subscribe(t, basic, jan, 14*24*time.Hour)

	proration, err := s.ChangePlan(basic, pro, jan.AddDate(0, 0, 3))
	if err != nil {
		t.Fatalf("ChangePlan: %v", err)
	}
	if !proration.IsNoop() {
		t.Errorf("proration = %+v, want no-op", proration)
	}
	if s.PlanID != pro.ID {
		t.Errorf("PlanID = %s, want pro", s.PlanID)
	}
}

func TestChangePlanMidPeriodProrates(t *testing.T) {
	s := subscribe(t, basic, jan, 0)

	proration, err := s.ChangePlan(basic, pro, midJanuary)
	if err != nil {
		t.Fatalf("ChangePlan: %v", err)
	}
	net, err := proration.Net()
	if err != nil {
		t.Fatalf("Net: %v", err)
	}
	if net != domain.JPY(1_000) {
		t.Errorf("net = %s, want 1000 JPY", net)
	}
}

func TestChangePlanRejectsSamePlanAndWrongCurrentPlan(t *testing.T) {
	s := subscribe(t, basic, jan, 0)

	if _, err := s.ChangePlan(basic, basic, jan); !errors.Is(err, domain.ErrInvariantViolation) {
		t.Errorf("same plan = %v, want ErrInvariantViolation", err)
	}
	// 呼び出し側が別の契約のプランを渡してきたら落とす。
	if _, err := s.ChangePlan(pro, basic, jan); !errors.Is(err, domain.ErrInvariantViolation) {
		t.Errorf("wrong current plan = %v, want ErrInvariantViolation", err)
	}
}

func TestCancelAtPeriodEndKeepsSubscriptionUsable(t *testing.T) {
	s := subscribe(t, basic, jan, 0)

	if err := s.Cancel(jan.AddDate(0, 0, 9), false); err != nil {
		t.Fatalf("Cancel: %v", err)
	}
	if s.Status != domain.StatusActive {
		t.Errorf("Status = %s, want active", s.Status)
	}
	if !s.CancelAtPeriodEnd {
		t.Error("CancelAtPeriodEnd should be true")
	}
}

func TestScheduledCancellationTakesEffectAtRenewal(t *testing.T) {
	s := subscribe(t, basic, jan, 0)
	if err := s.Cancel(jan.AddDate(0, 0, 9), false); err != nil {
		t.Fatalf("Cancel: %v", err)
	}

	needsCharge, err := s.Renew(basic, feb)
	if err != nil {
		t.Fatalf("Renew: %v", err)
	}
	if needsCharge {
		t.Error("a canceled subscription should not be charged")
	}
	if s.Status != domain.StatusCanceled {
		t.Errorf("Status = %s, want canceled", s.Status)
	}
}

func TestCanceledSubscriptionRejectsEverything(t *testing.T) {
	s := subscribe(t, basic, jan, 0)
	if err := s.Cancel(jan, true); err != nil {
		t.Fatalf("Cancel: %v", err)
	}

	if err := s.Cancel(feb, false); !errors.Is(err, domain.ErrIllegalTransition) {
		t.Errorf("Cancel = %v, want ErrIllegalTransition", err)
	}
	if _, err := s.ChangePlan(basic, pro, feb); !errors.Is(err, domain.ErrIllegalTransition) {
		t.Errorf("ChangePlan = %v, want ErrIllegalTransition", err)
	}
	if _, err := s.Renew(basic, feb); !errors.Is(err, domain.ErrIllegalTransition) {
		t.Errorf("Renew = %v, want ErrIllegalTransition", err)
	}
}

// バッチが遅れて動いても請求日がずれない。
func TestRenewalStartsFromPreviousPeriodEnd(t *testing.T) {
	s := subscribe(t, basic, jan, 0)

	if _, err := s.Renew(basic, feb.Add(5*time.Hour)); err != nil {
		t.Fatalf("Renew: %v", err)
	}
	if !s.CurrentPeriod.Start.Equal(feb) {
		t.Errorf("period start = %s, want %s", s.CurrentPeriod.Start, feb)
	}
	want := time.Date(2026, 3, 1, 0, 0, 0, 0, time.UTC)
	if !s.CurrentPeriod.End.Equal(want) {
		t.Errorf("period end = %s, want %s", s.CurrentPeriod.End, want)
	}
}

func TestRenewalBeforePeriodEndsIsRejected(t *testing.T) {
	s := subscribe(t, basic, jan, 0)
	if _, err := s.Renew(basic, jan.AddDate(0, 0, 19)); !errors.Is(err, domain.ErrInvariantViolation) {
		t.Errorf("Renew = %v, want ErrInvariantViolation", err)
	}
}

func TestTrialEndsByRenewingIntoActivePeriod(t *testing.T) {
	s := subscribe(t, basic, jan, 14*24*time.Hour)
	trialEnd := jan.AddDate(0, 0, 14)

	needsCharge, err := s.Renew(basic, trialEnd)
	if err != nil {
		t.Fatalf("Renew: %v", err)
	}
	if !needsCharge {
		t.Error("the first paid period should be charged")
	}
	if s.Status != domain.StatusActive {
		t.Errorf("Status = %s, want active", s.Status)
	}
}

// 再試行のたびに猶予が延びると、永遠に解約されない契約ができる。
func TestRepeatedFailuresDoNotExtendGracePeriod(t *testing.T) {
	s := subscribe(t, basic, jan, 0)
	if err := s.MarkPaymentFailed(feb); err != nil {
		t.Fatalf("MarkPaymentFailed: %v", err)
	}
	if err := s.MarkPaymentFailed(feb.AddDate(0, 0, 10)); err != nil {
		t.Fatalf("MarkPaymentFailed: %v", err)
	}

	if s.PastDueSince == nil || !s.PastDueSince.Equal(feb) {
		t.Errorf("PastDueSince = %v, want %s", s.PastDueSince, feb)
	}
}

func TestGracePeriodBoundary(t *testing.T) {
	s := subscribe(t, basic, jan, 0)
	if err := s.MarkPaymentFailed(feb); err != nil {
		t.Fatalf("MarkPaymentFailed: %v", err)
	}

	if s.ExpireIfGraceOver(feb.Add(domain.GracePeriod-time.Second), domain.GracePeriod) {
		t.Error("should not expire one second before the grace period ends")
	}
	if !s.ExpireIfGraceOver(feb.Add(domain.GracePeriod), domain.GracePeriod) {
		t.Error("should expire exactly at the end of the grace period")
	}
	if s.Status != domain.StatusCanceled {
		t.Errorf("Status = %s, want canceled", s.Status)
	}
}

func TestSuccessfulPaymentRecoversFromPastDue(t *testing.T) {
	s := subscribe(t, basic, jan, 0)
	if err := s.MarkPaymentFailed(feb); err != nil {
		t.Fatalf("MarkPaymentFailed: %v", err)
	}

	if err := s.MarkPaymentSucceeded(feb.AddDate(0, 0, 1)); err != nil {
		t.Fatalf("MarkPaymentSucceeded: %v", err)
	}
	if s.Status != domain.StatusActive {
		t.Errorf("Status = %s, want active", s.Status)
	}
	if s.PastDueSince != nil {
		t.Errorf("PastDueSince = %v, want nil", s.PastDueSince)
	}
}

func TestEventsAreRecordedAndDrained(t *testing.T) {
	s := subscribe(t, basic, jan, 0)
	if err := s.Cancel(jan, true); err != nil {
		t.Fatalf("Cancel: %v", err)
	}

	events := s.PullEvents()
	var names []string
	for _, event := range events {
		names = append(names, event.Name)
	}
	want := []string{"subscription.created", "subscription.canceled"}
	if len(names) != len(want) {
		t.Fatalf("events = %v, want %v", names, want)
	}
	for i := range want {
		if names[i] != want[i] {
			t.Errorf("events = %v, want %v", names, want)
			break
		}
	}
	if len(s.PullEvents()) != 0 {
		t.Error("events should be drained")
	}
}
