package memory

import (
	"context"
	"fmt"
	"sort"

	"github.com/Yutarotaro/clean-architecture-billing/go/internal/domain"
	"github.com/Yutarotaro/clean-architecture-billing/go/internal/infra/storage"
)

type invoiceRepo struct {
	db       *Database
	staging  *staging
	versions *versionTracker
}

func (r *invoiceRepo) Get(_ context.Context, id domain.InvoiceID) (*domain.Invoice, error) {
	if staged, ok := r.staging.invoices[id]; ok {
		r.versions.remember(string(id), r.staging.versions[string(id)])
		return cloneInvoice(staged), nil
	}
	r.db.mu.RLock()
	defer r.db.mu.RUnlock()
	stored, ok := r.db.invoices[id]
	if !ok {
		return nil, nil
	}
	r.versions.remember(string(id), r.db.versions[string(id)])
	return cloneInvoice(stored), nil
}

func (r *invoiceRepo) Add(ctx context.Context, invoice *domain.Invoice) error {
	id := string(invoice.ID)
	if r.exists(invoice.ID) {
		return fmt.Errorf("%w: invoice %q already exists", storage.ErrDuplicate, id)
	}
	if invoice.IdempotencyKey != "" {
		// SQL の UNIQUE 制約、DynamoDB の条件付き書き込みに相当する検査。
		existing, err := r.FindByIdempotencyKey(ctx, invoice.IdempotencyKey)
		if err != nil {
			return err
		}
		if existing != nil {
			return fmt.Errorf("%w: idempotency key %q already used",
				storage.ErrDuplicate, invoice.IdempotencyKey)
		}
	}
	r.staging.invoices[invoice.ID] = cloneInvoice(invoice)
	r.staging.versions[id] = 1
	r.versions.remember(id, 1)
	return nil
}

func (r *invoiceRepo) Save(_ context.Context, invoice *domain.Invoice) error {
	id := string(invoice.ID)
	if !r.exists(invoice.ID) {
		return fmt.Errorf("%w: invoice %q does not exist", storage.ErrUnknown, id)
	}
	expected, err := r.versions.expected(id)
	if err != nil {
		return err
	}
	if baseline, ok := r.committedVersion(id); ok && baseline != expected {
		return r.versions.conflict(id)
	}
	r.staging.invoices[invoice.ID] = cloneInvoice(invoice)
	r.staging.versions[id] = r.versions.bump(id)
	return nil
}

func (r *invoiceRepo) FindByIdempotencyKey(
	_ context.Context, key string,
) (*domain.Invoice, error) {
	for _, invoice := range r.merged() {
		if invoice.IdempotencyKey == key {
			r.rememberVersion(string(invoice.ID))
			return cloneInvoice(invoice), nil
		}
	}
	return nil, nil
}

func (r *invoiceRepo) ListForCustomer(
	_ context.Context, id domain.CustomerID,
) ([]*domain.Invoice, error) {
	var found []*domain.Invoice
	for _, invoice := range r.merged() {
		if invoice.CustomerID == id {
			r.rememberVersion(string(invoice.ID))
			found = append(found, cloneInvoice(invoice))
		}
	}
	sort.Slice(found, func(i, j int) bool {
		left, right := found[i].IssuedAt, found[j].IssuedAt
		if left == nil || right == nil {
			return found[i].ID < found[j].ID
		}
		if left.Equal(*right) {
			return found[i].ID < found[j].ID
		}
		return left.Before(*right)
	})
	return found, nil
}

func (r *invoiceRepo) merged() map[domain.InvoiceID]*domain.Invoice {
	r.db.mu.RLock()
	merged := make(map[domain.InvoiceID]*domain.Invoice, len(r.db.invoices))
	for id, invoice := range r.db.invoices {
		merged[id] = invoice
	}
	r.db.mu.RUnlock()
	for id, invoice := range r.staging.invoices {
		merged[id] = invoice
	}
	return merged
}

func (r *invoiceRepo) rememberVersion(id string) {
	if version, ok := r.staging.versions[id]; ok {
		r.versions.remember(id, version)
		return
	}
	if version, ok := r.committedVersion(id); ok {
		r.versions.remember(id, version)
	}
}

func (r *invoiceRepo) exists(id domain.InvoiceID) bool {
	if _, ok := r.staging.invoices[id]; ok {
		return true
	}
	r.db.mu.RLock()
	defer r.db.mu.RUnlock()
	_, ok := r.db.invoices[id]
	return ok
}

func (r *invoiceRepo) committedVersion(id string) (int, bool) {
	r.db.mu.RLock()
	defer r.db.mu.RUnlock()
	version, ok := r.db.versions[id]
	return version, ok
}
