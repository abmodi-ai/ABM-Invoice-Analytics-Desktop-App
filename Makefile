# ABM Invoice Analytics — developer entry points. Windows CI runs the same targets.
.PHONY: setup test golden perf e2e lint typecheck license-check sbom openapi engine-dist app-build ai-eval demo dev clean

setup:
	uv sync
	cd apps/desktop && npm ci

test:            ## unit + property + fixtures + integration + AI-off/on + security + golden
	uv run pytest -o addopts="" -m "not perf" --timeout=1200 tests/unit tests/integration tests/golden
	cd apps/desktop && npx vitest run

golden:
	uv run pytest -o addopts="" tests/golden -q

perf:            ## performance budgets (writes tests/perf/out/*.json)
	uv run python tests/perf/perf_run.py --lines 250000
	uv run python tests/perf/perf_run.py --lines 1250000

e2e:             ## needs `make demo` data + running engine/vite (see docs/dev.md)
	cd apps/desktop && npx playwright test

lint:
	uv run ruff check engine tools tests
	uv run black --check engine tools
	cd apps/desktop && npx eslint src && npx tsc -b
	cd apps/desktop/src-tauri && cargo clippy -- -D warnings

typecheck:
	uv run mypy engine/invoice_analytics

license-check:   ## fails the build on copyleft runtime dependencies
	uv run python tools/license_check.py
	cd apps/desktop && npm run license-check

sbom:
	mkdir -p build/sbom
	uv run cyclonedx-py environment --of JSON -o build/sbom/engine.cdx.json
	cd apps/desktop && npx --yes @cyclonedx/cyclonedx-npm --output-file ../../build/sbom/desktop.cdx.json

openapi:         ## regenerate the UI contract
	IA_DATA_DIR=build/openapi IA_KEYSTORE=file uv run python -m invoice_analytics --openapi apps/desktop/openapi.json
	cd apps/desktop && npm run gen:api

engine-dist:
	cd engine && uv run pyinstaller invoice-analytics-engine.spec --noconfirm --distpath ../build/engine --workpath ../build/pyi-work
	rm -rf apps/desktop/src-tauri/resources/engine && mkdir -p apps/desktop/src-tauri/resources
	cp -R build/engine/invoice-analytics-engine apps/desktop/src-tauri/resources/engine

app-build: engine-dist
	cd apps/desktop && npx tauri build

ai-eval:         ## make ai-eval MODEL=/path/to/model.gguf
	uv run python tests/ai_eval/bakeoff.py --model $(MODEL)

demo:            ## synthetic data + sample reference data into .ia-dev/data
	IA_DATA_DIR=.ia-dev/data IA_KEYSTORE=file uv run python -m invoice_analytics --demo

dev:             ## desktop app in dev mode (engine via uv, UI via vite)
	cd apps/desktop && IA_DATA_DIR=$(CURDIR)/.ia-dev/data IA_KEYSTORE=file npx tauri dev

clean:
	rm -rf build apps/desktop/dist apps/desktop/src-tauri/target tests/*/out
