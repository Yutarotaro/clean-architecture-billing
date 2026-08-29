package domain

// 識別子。string のまま引き回すと、顧客 ID を渡すべき場所にプラン ID を渡しても
// コンパイルが通ってしまう。名前付き型にしておけば型検査で取り違えを弾ける。
type (
	CustomerID     string
	PlanID         string
	SubscriptionID string
	InvoiceID      string
)
