// Command api はサンプルの HTTP サーバを起動する。
package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	adapterhttp "github.com/Yutarotaro/clean-architecture-billing/go/internal/adapter/http"
	"github.com/Yutarotaro/clean-architecture-billing/go/internal/app"
)

func main() {
	logger := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	if err := run(logger); err != nil {
		logger.Error("server stopped", "error", err)
		os.Exit(1)
	}
}

func run(logger *slog.Logger) error {
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	cfg := configFromEnv()
	container, err := app.Build(ctx, cfg, app.WithLogger(logger))
	if err != nil {
		return err
	}
	defer func() { _ = container.Close() }()

	server := &http.Server{
		Addr:              cfg.Addr,
		Handler:           adapterhttp.NewServer(container.Handlers),
		ReadHeaderTimeout: 5 * time.Second,
	}

	errs := make(chan error, 1)
	go func() {
		logger.Info("listening", "addr", cfg.Addr, "persistence", string(cfg.Persistence))
		if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			errs <- err
		}
	}()

	select {
	case err := <-errs:
		return err
	case <-ctx.Done():
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		return server.Shutdown(shutdownCtx)
	}
}

func configFromEnv() app.Config {
	cfg := app.DefaultConfig()
	if value := os.Getenv("BILLING_PERSISTENCE"); value != "" {
		cfg.Persistence = app.Persistence(value)
	}
	if value := os.Getenv("BILLING_DATABASE_DSN"); value != "" {
		cfg.DatabaseDSN = value
	}
	if value := os.Getenv("BILLING_ADDR"); value != "" {
		cfg.Addr = value
	}
	if os.Getenv("BILLING_SEED_PLANS") == "0" {
		cfg.SeedPlans = false
	}
	return cfg
}
