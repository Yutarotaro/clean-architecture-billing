package sqlite

import (
	"context"
	"database/sql"
	"errors"

	"github.com/Yutarotaro/clean-architecture-billing/go/internal/domain"
)

const invoiceColumns = `id, customer_id, subscription_id, status, currency,
	issued_at, paid_at, idempotency_key, version`

type invoiceRepo struct {
	tx       *sql.Tx
	versions *versionTracker
}

func (r *invoiceRepo) Get(ctx context.Context, id domain.InvoiceID) (*domain.Invoice, error) {
	row := r.tx.QueryRowContext(ctx,
		`SELECT `+invoiceColumns+` FROM invoices WHERE id = ?`, string(id))
	invoice, err := r.scan(ctx, row)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return invoice, err
}

func (r *invoiceRepo) Add(ctx context.Context, invoice *domain.Invoice) error {
	args := append(invoiceArgs(invoice), 1)
	if _, err := r.tx.ExecContext(ctx,
		`INSERT INTO invoices (`+invoiceColumns+`) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		args...); err != nil {
		// idempotency_key の UNIQUE 制約に当たった場合もここに来る。
		return asDuplicate(err, "invoice %q or idempotency key %q already exists",
			invoice.ID, invoice.IdempotencyKey)
	}

	for position, line := range invoice.Lines {
		if _, err := r.tx.ExecContext(ctx,
			`INSERT INTO invoice_lines (invoice_id, position, description, amount, currency)
			 VALUES (?, ?, ?, ?, ?)`,
			string(invoice.ID), position, line.Description,
			line.Amount.Amount, line.Amount.Currency); err != nil {
			return err
		}
	}
	r.versions.remember(string(invoice.ID), 1)
	return nil
}

func (r *invoiceRepo) Save(ctx context.Context, invoice *domain.Invoice) error {
	// 明細は発行時に確定し、以後変化しない（Invoice に明細を足すメソッドはない）。
	// だから save では見出し行だけを更新する。明細が可変になったら、ここは全削除して
	// 入れ直す形に変える必要がある。
	id := string(invoice.ID)
	expected, err := r.versions.expected(id)
	if err != nil {
		return err
	}
	args := append(invoiceArgs(invoice), expected+1, id, expected)
	result, err := r.tx.ExecContext(ctx,
		`UPDATE invoices SET
			id = ?, customer_id = ?, subscription_id = ?, status = ?, currency = ?,
			issued_at = ?, paid_at = ?, idempotency_key = ?, version = ?
		 WHERE id = ? AND version = ?`, args...)
	if err != nil {
		return asConflict(err, "invoice", id)
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return err
	}
	if affected != 1 {
		return r.versions.conflict(id)
	}
	r.versions.bump(id)
	return nil
}

func (r *invoiceRepo) FindByIdempotencyKey(
	ctx context.Context, key string,
) (*domain.Invoice, error) {
	row := r.tx.QueryRowContext(ctx,
		`SELECT `+invoiceColumns+` FROM invoices WHERE idempotency_key = ?`, key)
	invoice, err := r.scan(ctx, row)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return invoice, err
}

func (r *invoiceRepo) ListForCustomer(
	ctx context.Context, id domain.CustomerID,
) ([]*domain.Invoice, error) {
	rows, err := r.tx.QueryContext(ctx,
		`SELECT `+invoiceColumns+` FROM invoices WHERE customer_id = ? ORDER BY issued_at, id`,
		string(id))
	if err != nil {
		return nil, err
	}
	defer func() { _ = rows.Close() }()

	var found []*domain.Invoice
	for rows.Next() {
		invoice, err := r.scan(ctx, rows)
		if err != nil {
			return nil, err
		}
		found = append(found, invoice)
	}
	return found, rows.Err()
}

func (r *invoiceRepo) scan(ctx context.Context, row scanner) (*domain.Invoice, error) {
	var raw invoiceRow
	if err := row.Scan(
		&raw.id, &raw.customerID, &raw.subscriptionID, &raw.status, &raw.currency,
		&raw.issuedAt, &raw.paidAt, &raw.idempotencyKey, &raw.version,
	); err != nil {
		return nil, err
	}
	lines, err := r.loadLines(ctx, raw.id)
	if err != nil {
		return nil, err
	}
	r.versions.remember(raw.id, raw.version)
	return raw.toDomain(lines)
}

func (r *invoiceRepo) loadLines(ctx context.Context, invoiceID string) ([]domain.InvoiceLine, error) {
	rows, err := r.tx.QueryContext(ctx,
		`SELECT description, amount, currency FROM invoice_lines
		 WHERE invoice_id = ? ORDER BY position`, invoiceID)
	if err != nil {
		return nil, err
	}
	defer func() { _ = rows.Close() }()

	var lines []domain.InvoiceLine
	for rows.Next() {
		var (
			description, currency string
			amount                int64
		)
		if err := rows.Scan(&description, &amount, &currency); err != nil {
			return nil, err
		}
		money, err := domain.NewMoney(amount, currency)
		if err != nil {
			return nil, err
		}
		lines = append(lines, domain.InvoiceLine{Description: description, Amount: money})
	}
	return lines, rows.Err()
}
