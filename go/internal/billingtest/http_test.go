package billingtest

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"

	adapterhttp "github.com/Yutarotaro/clean-architecture-billing/go/internal/adapter/http"
	"github.com/Yutarotaro/clean-architecture-billing/go/internal/app"
	"github.com/Yutarotaro/clean-architecture-billing/go/internal/infra/clock"
	"github.com/Yutarotaro/clean-architecture-billing/go/internal/infra/ids"
	"github.com/Yutarotaro/clean-architecture-billing/go/internal/infra/payment"
	"github.com/Yutarotaro/clean-architecture-billing/go/internal/usecase"
)

// ここで確かめるのは「HTTP という形式との対応づけ」であって、ビジネスルールではない。
// 日割りが正しいかはユースケース層のテストで済んでいるので、この層では
// 「ドメインのエラーが正しいステータスコードに翻訳されるか」「必須ヘッダが効くか」
// といった、この層にしかない関心だけを見る。

type apiFixture struct {
	server  *httptest.Server
	clock   *clock.Fixed
	gateway *payment.Fake
}

func newAPI(t *testing.T, factory usecase.UnitOfWorkFactory) *apiFixture {
	t.Helper()
	fixed := clock.NewFixed(jan)
	gateway := payment.NewFake()

	// 本番と同じ app.Build を通す。差し替えるのは時計・採番・決済代行・永続化だけで、
	// 配線そのものは本番と 1 行も違わない。
	container, err := app.Build(context.Background(), app.Config{SeedPlans: true},
		app.WithUnitOfWorkFactory(factory),
		app.WithClock(fixed),
		app.WithIDs(ids.NewSequential("id")),
		app.WithGateway(gateway),
	)
	if err != nil {
		t.Fatalf("app.Build: %v", err)
	}
	t.Cleanup(func() { _ = container.Close() })

	server := httptest.NewServer(adapterhttp.NewServer(container.Handlers))
	t.Cleanup(server.Close)
	return &apiFixture{server: server, clock: fixed, gateway: gateway}
}

func (a *apiFixture) do(
	t *testing.T, method, path string, body any, headers map[string]string,
) (int, []byte) {
	t.Helper()
	var reader io.Reader
	if body != nil {
		encoded, err := json.Marshal(body)
		if err != nil {
			t.Fatalf("marshal: %v", err)
		}
		reader = bytes.NewReader(encoded)
	}
	req, err := http.NewRequest(method, a.server.URL+path, reader)
	if err != nil {
		t.Fatalf("new request: %v", err)
	}
	for key, value := range headers {
		req.Header.Set(key, value)
	}
	resp, err := a.server.Client().Do(req)
	if err != nil {
		t.Fatalf("do: %v", err)
	}
	defer func() { _ = resp.Body.Close() }()
	raw, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatalf("read body: %v", err)
	}
	return resp.StatusCode, raw
}

func decodeInto[T any](t *testing.T, raw []byte) T {
	t.Helper()
	var value T
	if err := json.Unmarshal(raw, &value); err != nil {
		t.Fatalf("unmarshal %s: %v", raw, err)
	}
	return value
}

type subscribeBody struct {
	Subscription struct {
		ID     string `json:"id"`
		Status string `json:"status"`
		PlanID string `json:"plan_id"`
	} `json:"subscription"`
	Invoice *struct {
		ID     string `json:"id"`
		Status string `json:"status"`
		Total  struct {
			Amount int64 `json:"amount"`
		} `json:"total"`
	} `json:"invoice"`
	PaymentFailed bool `json:"payment_failed"`
}

func (a *apiFixture) subscribe(t *testing.T, planID string) subscribeBody {
	t.Helper()
	status, raw := a.do(t, http.MethodPost, "/subscriptions",
		map[string]any{"customer_id": "cus-1", "plan_id": planID}, nil)
	if status != http.StatusCreated {
		t.Fatalf("subscribe status = %d, want 201; body = %s", status, raw)
	}
	return decodeInto[subscribeBody](t, raw)
}

func TestHTTPHealth(t *testing.T) {
	eachBackend(t, func(t *testing.T, factory usecase.UnitOfWorkFactory) {
		api := newAPI(t, factory)
		status, raw := api.do(t, http.MethodGet, "/health", nil, nil)
		if status != http.StatusOK {
			t.Errorf("status = %d, want 200; body = %s", status, raw)
		}
	})
}

func TestHTTPListPlans(t *testing.T) {
	eachBackend(t, func(t *testing.T, factory usecase.UnitOfWorkFactory) {
		api := newAPI(t, factory)

		status, raw := api.do(t, http.MethodGet, "/plans", nil, nil)

		if status != http.StatusOK {
			t.Fatalf("status = %d, want 200; body = %s", status, raw)
		}
		plans := decodeInto[[]struct {
			ID    string `json:"id"`
			Price struct {
				Amount   int64  `json:"amount"`
				Currency string `json:"currency"`
			} `json:"price"`
		}](t, raw)
		if len(plans) != 3 {
			t.Fatalf("len(plans) = %d, want 3", len(plans))
		}
		if plans[0].ID != "basic" || plans[0].Price.Amount != 1_000 {
			t.Errorf("plans[0] = %+v, want basic/1000", plans[0])
		}
	})
}

func TestHTTPSubscribeReturns201(t *testing.T) {
	eachBackend(t, func(t *testing.T, factory usecase.UnitOfWorkFactory) {
		api := newAPI(t, factory)

		body := api.subscribe(t, "pro")

		if body.Subscription.Status != "active" {
			t.Errorf("status = %s, want active", body.Subscription.Status)
		}
		if body.Invoice == nil || body.Invoice.Total.Amount != 3_000 {
			t.Errorf("invoice = %+v, want total 3000", body.Invoice)
		}
	})
}

func TestHTTPUnknownSubscriptionIs404(t *testing.T) {
	eachBackend(t, func(t *testing.T, factory usecase.UnitOfWorkFactory) {
		api := newAPI(t, factory)

		status, raw := api.do(t, http.MethodGet, "/subscriptions/nope", nil, nil)

		if status != http.StatusNotFound {
			t.Fatalf("status = %d, want 404; body = %s", status, raw)
		}
		errorBody := decodeInto[struct {
			Error string `json:"error"`
		}](t, raw)
		if errorBody.Error != "not_found" {
			t.Errorf("error = %s, want not_found", errorBody.Error)
		}
	})
}

// 鍵を任意にすると、いつか誰かが渡し忘れて二重課金になる。
func TestHTTPChangePlanRequiresIdempotencyKey(t *testing.T) {
	eachBackend(t, func(t *testing.T, factory usecase.UnitOfWorkFactory) {
		api := newAPI(t, factory)
		subscribed := api.subscribe(t, "basic")

		status, _ := api.do(t, http.MethodPost,
			"/subscriptions/"+subscribed.Subscription.ID+"/plan-changes",
			map[string]any{"new_plan_id": "pro"}, nil)

		if status != http.StatusUnprocessableEntity {
			t.Errorf("status = %d, want 422", status)
		}
	})
}

func TestHTTPChangePlan(t *testing.T) {
	eachBackend(t, func(t *testing.T, factory usecase.UnitOfWorkFactory) {
		api := newAPI(t, factory)
		subscribed := api.subscribe(t, "basic")
		api.clock.Set(midJanuary)

		status, raw := api.do(t, http.MethodPost,
			"/subscriptions/"+subscribed.Subscription.ID+"/plan-changes",
			map[string]any{"new_plan_id": "pro"},
			map[string]string{"Idempotency-Key": "change-1"})

		if status != http.StatusOK {
			t.Fatalf("status = %d, want 200; body = %s", status, raw)
		}
		body := decodeInto[struct {
			Proration struct {
				Net struct {
					Amount int64 `json:"amount"`
				} `json:"net"`
			} `json:"proration"`
			Invoice *struct {
				Status string `json:"status"`
			} `json:"invoice"`
		}](t, raw)
		if body.Proration.Net.Amount != 1_000 {
			t.Errorf("net = %d, want 1000", body.Proration.Net.Amount)
		}
		if body.Invoice == nil || body.Invoice.Status != "paid" {
			t.Errorf("invoice = %+v, want paid", body.Invoice)
		}
	})
}

// 状態と要求が両立しない。入力の誤り（422）ではなく競合（409）。
func TestHTTPChangingPlanOfCanceledSubscriptionIs409(t *testing.T) {
	eachBackend(t, func(t *testing.T, factory usecase.UnitOfWorkFactory) {
		api := newAPI(t, factory)
		subscribed := api.subscribe(t, "basic")
		if status, raw := api.do(t, http.MethodPost,
			"/subscriptions/"+subscribed.Subscription.ID+"/cancellation",
			map[string]any{"immediately": true}, nil); status != http.StatusOK {
			t.Fatalf("cancel status = %d; body = %s", status, raw)
		}

		status, raw := api.do(t, http.MethodPost,
			"/subscriptions/"+subscribed.Subscription.ID+"/plan-changes",
			map[string]any{"new_plan_id": "pro"},
			map[string]string{"Idempotency-Key": "change-1"})

		if status != http.StatusConflict {
			t.Fatalf("status = %d, want 409; body = %s", status, raw)
		}
		errorBody := decodeInto[struct {
			Error string `json:"error"`
		}](t, raw)
		if errorBody.Error != "illegal_state" {
			t.Errorf("error = %s, want illegal_state", errorBody.Error)
		}
	})
}

// 不変条件の違反。状態は正常なので 409 ではなく 422。
func TestHTTPChangingToTheSamePlanIs422(t *testing.T) {
	eachBackend(t, func(t *testing.T, factory usecase.UnitOfWorkFactory) {
		api := newAPI(t, factory)
		subscribed := api.subscribe(t, "basic")

		status, raw := api.do(t, http.MethodPost,
			"/subscriptions/"+subscribed.Subscription.ID+"/plan-changes",
			map[string]any{"new_plan_id": "basic"},
			map[string]string{"Idempotency-Key": "change-1"})

		if status != http.StatusUnprocessableEntity {
			t.Fatalf("status = %d, want 422; body = %s", status, raw)
		}
		errorBody := decodeInto[struct {
			Error string `json:"error"`
		}](t, raw)
		if errorBody.Error != "domain_rule_violated" {
			t.Errorf("error = %s, want domain_rule_violated", errorBody.Error)
		}
	})
}

func TestHTTPListInvoicesAndWebhookAndRenewals(t *testing.T) {
	eachBackend(t, func(t *testing.T, factory usecase.UnitOfWorkFactory) {
		api := newAPI(t, factory)
		subscribed := api.subscribe(t, "basic")

		status, raw := api.do(t, http.MethodGet, "/customers/cus-1/invoices", nil, nil)
		if status != http.StatusOK {
			t.Fatalf("invoices status = %d; body = %s", status, raw)
		}
		invoices := decodeInto[[]struct {
			Status string `json:"status"`
		}](t, raw)
		if len(invoices) != 1 || invoices[0].Status != "paid" {
			t.Errorf("invoices = %+v, want one paid invoice", invoices)
		}

		status, raw = api.do(t, http.MethodPost, "/webhooks/payments",
			map[string]any{"invoice_id": subscribed.Invoice.ID, "succeeded": true}, nil)
		if status != http.StatusOK {
			t.Fatalf("webhook status = %d; body = %s", status, raw)
		}

		api.clock.Set(feb)
		status, raw = api.do(t, http.MethodPost, "/admin/renewals", nil, nil)
		if status != http.StatusOK {
			t.Fatalf("renewals status = %d; body = %s", status, raw)
		}
		report := decodeInto[struct {
			Renewed  int `json:"renewed"`
			Invoiced int `json:"invoiced"`
		}](t, raw)
		if report.Renewed != 1 || report.Invoiced != 1 {
			t.Errorf("report = %+v, want renewed=1 invoiced=1", report)
		}
	})
}
