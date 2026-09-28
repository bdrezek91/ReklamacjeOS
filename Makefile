.PHONY: check test build

check:
	docker compose config --quiet
	docker compose run --rm --no-deps backend python -m compileall -q app alembic
	docker compose run --rm --no-deps whatsapp npm run check

test:
	cd backend && pytest -q

build:
	docker compose build
