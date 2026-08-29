package arch

import (
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// クリーンアーキテクチャの唯一のルールは「依存は内側にしか向かない」であり、それは
// レビューで守るものではなく、テストで守るものである。図をいくら描いても、締切前に
// ドメイン層から SQL ドライバを import する誰かは必ず現れる。
//
// このテストが落ちるときは、たいてい設計上の判断が必要になっている。import を消して
// 通せば済む場合もあれば、インターフェースを 1 つ足すべき場合もある。

// allowedInternal は各層が import してよい内部パッケージ。自分自身は常に許可される。
var allowedInternal = map[string][]string{
	"domain":  {},
	"usecase": {"domain"},
	"infra":   {"domain", "usecase"},
	"adapter": {"domain", "usecase"},
	// 合成ルートだけがすべてを知っている。
	"app": {"domain", "usecase", "infra", "adapter"},
}

// allowedExternal は各層が import してよい外部モジュール。ここにないものは禁止。
var allowedExternal = map[string][]string{
	// 標準ライブラリだけ。ドメインは「ただの Go」でなければならない。
	"domain": {},
	// ユースケース層も同じ。ポート越しにしか外の世界に触れない。
	"usecase": {},
	"infra":   {"modernc.org/sqlite"},
	"adapter": {},
	"app":     {},
}

const modulePrefix = "github.com/Yutarotaro/clean-architecture-billing/go/internal/"

func TestLayersDoNotImportOuterLayers(t *testing.T) {
	for layer, allowed := range allowedInternal {
		t.Run(layer, func(t *testing.T) {
			permitted := append([]string{layer}, allowed...)
			var violations []string

			forEachImport(t, layer, func(file, path string) {
				if !strings.HasPrefix(path, modulePrefix) {
					return
				}
				imported := strings.SplitN(strings.TrimPrefix(path, modulePrefix), "/", 2)[0]
				if !contains(permitted, imported) {
					violations = append(violations, file+" imports "+path)
				}
			})

			if len(violations) > 0 {
				t.Errorf("依存が外側を向いている:\n  %s", strings.Join(violations, "\n  "))
			}
		})
	}
}

func TestLayersDoNotImportUnexpectedModules(t *testing.T) {
	for layer, allowed := range allowedExternal {
		t.Run(layer, func(t *testing.T) {
			var violations []string

			forEachImport(t, layer, func(file, path string) {
				if !isThirdParty(path) {
					return
				}
				for _, prefix := range allowed {
					if strings.HasPrefix(path, prefix) {
						return
					}
				}
				violations = append(violations, file+" imports "+path)
			})

			if len(violations) > 0 {
				t.Errorf("%s 層が許可されていない外部モジュールに依存している:\n  %s",
					layer, strings.Join(violations, "\n  "))
			}
		})
	}
}

// このテストが通るかぎり、ドメインのビジネスルールはデータベースもフレームワークも
// ネットワークもない場所で実行でき、テストは常にミリ秒で終わる。
func TestDomainIsPlainGo(t *testing.T) {
	var external []string
	forEachImport(t, "domain", func(file, path string) {
		if isThirdParty(path) || strings.HasPrefix(path, modulePrefix) {
			external = append(external, file+" imports "+path)
		}
	})
	if len(external) > 0 {
		t.Errorf("domain 層がパッケージ外に依存している:\n  %s", strings.Join(external, "\n  "))
	}
}

// forEachImport は layer 配下のすべての .go ファイルの import を走査する。
func forEachImport(t *testing.T, layer string, visit func(file, path string)) {
	t.Helper()
	root := filepath.Join(repoRoot(t), "internal", layer)

	err := filepath.WalkDir(root, func(path string, entry os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if entry.IsDir() || !strings.HasSuffix(path, ".go") {
			return nil
		}
		// テストファイルは対象外。テストが本番より広く import するのは正常である。
		if strings.HasSuffix(path, "_test.go") {
			return nil
		}
		parsed, err := parser.ParseFile(token.NewFileSet(), path, nil, parser.ImportsOnly)
		if err != nil {
			return err
		}
		relative, err := filepath.Rel(repoRoot(t), path)
		if err != nil {
			return err
		}
		for _, spec := range parsed.Imports {
			visit(relative, strings.Trim(spec.Path.Value, `"`))
		}
		return nil
	})
	if err != nil {
		t.Fatalf("walk %s: %v", root, err)
	}
}

// isThirdParty は標準ライブラリ以外かを判定する。
//
// Go の標準ライブラリの import パスにはドットが含まれない（"net/http"）のに対し、
// 外部モジュールはホスト名で始まる（"modernc.org/sqlite"）。この単純な規則で足りる。
func isThirdParty(path string) bool {
	if strings.HasPrefix(path, modulePrefix) {
		return false
	}
	first := strings.SplitN(path, "/", 2)[0]
	return strings.Contains(first, ".")
}

func repoRoot(t *testing.T) string {
	t.Helper()
	// このファイルは <root>/internal/arch にある。
	wd, err := os.Getwd()
	if err != nil {
		t.Fatalf("getwd: %v", err)
	}
	return filepath.Dir(filepath.Dir(wd))
}

func contains(values []string, want string) bool {
	for _, value := range values {
		if value == want {
			return true
		}
	}
	return false
}
