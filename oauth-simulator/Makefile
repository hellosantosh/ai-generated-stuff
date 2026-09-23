# OAuth 2.1 + PKCE Simulator
#
#   make up        start everything in Docker   (make down to stop)
#   make local     start everything with plain Node, no Docker
#   make demo      run the whole flow on the command line
#
# `make help` lists everything.

SHELL := /bin/bash
COMPOSE := docker compose
CLIENT := http://localhost:9020
AS := http://localhost:9000
API := http://localhost:9010

.DEFAULT_GOAL := help
.PHONY: help up down restart rebuild build logs logs-as logs-api logs-client ps status \
        local local-stop local-restart local-status local-logs \
        demo demo-attack demo-replay demo-refresh test open urls policy reset guide \
        guide-pkce guides cover clean

## ---------------------------------------------------------------- Docker

up: ## Build and start all three services in Docker (detached)
	$(COMPOSE) up --build -d
	@$(MAKE) --no-print-directory wait
	@$(MAKE) --no-print-directory urls

down: ## Stop and remove the containers
	$(COMPOSE) down

restart: ## Restart the containers (this also wipes all state and rotates keys)
	$(COMPOSE) restart
	@$(MAKE) --no-print-directory wait

rebuild: ## Rebuild images from scratch and start
	$(COMPOSE) build --no-cache
	$(COMPOSE) up -d
	@$(MAKE) --no-print-directory wait

build: ## Build the images without starting them
	$(COMPOSE) build

logs: ## Follow the logs of all services (this is where you watch the protocol run)
	$(COMPOSE) logs -f

logs-as: ## Follow only the authorization server
	$(COMPOSE) logs -f authorization-server

logs-api: ## Follow only the resource server
	$(COMPOSE) logs -f resource-server

logs-client: ## Follow only the client app
	$(COMPOSE) logs -f client-app

ps status: ## Show container and health status
	@$(COMPOSE) ps

wait:
	@printf "  waiting for services"
	@for url in $(AS)/healthz $(API)/healthz $(CLIENT)/healthz; do \
	  curl -s --retry 40 --retry-delay 1 --retry-connrefused --max-time 45 $$url > /dev/null \
	    && printf "." || { printf "\n  timed out waiting for %s\n" $$url; exit 1; }; \
	done; echo " ready"

## ---------------------------------------------------------------- No Docker

local: ## Start everything with plain Node, no Docker needed (Node 18+)
	@./scripts/run-local.sh start

local-stop: ## Stop the plain-Node services
	@./scripts/run-local.sh stop

local-restart: ## Restart the plain-Node services
	@./scripts/run-local.sh restart

local-status: ## Show the plain-Node service status
	@./scripts/run-local.sh status

local-logs: ## Follow the plain-Node logs
	@./scripts/run-local.sh logs

## ---------------------------------------------------------------- Drive it

demo: ## Walk the whole Authorization Code + PKCE flow on the command line
	@./scripts/demo-flow.sh --happy

demo-attack: ## Command-line demo: a stolen authorization code, blocked by PKCE
	@./scripts/demo-flow.sh --attack

demo-replay: ## Command-line demo: replaying an already-used code
	@./scripts/demo-flow.sh --replay

demo-refresh: ## Command-line demo: refresh rotation and reuse detection
	@./scripts/demo-flow.sh --refresh

test: ## Run every command-line scenario as a smoke test
	@./scripts/demo-flow.sh --all

open: ## Open the simulator in your browser
	@(command -v open > /dev/null && open $(CLIENT)) || \
	 (command -v xdg-open > /dev/null && xdg-open $(CLIENT)) || \
	 echo "Open $(CLIENT)"

policy: ## Open the page where you turn OAuth 2.1 safety rules off on purpose
	@(command -v open > /dev/null && open $(AS)/policy) || echo "Open $(AS)/policy"

reset: ## Wipe all authorization-server state (sessions, codes, tokens, consent)
	@curl -s -o /dev/null -X POST $(AS)/state/reset && echo "  authorization server state wiped"
	@curl -s -o /dev/null -X POST $(AS)/policy/reset && echo "  policy restored to safe OAuth 2.1 defaults"

urls:
	@printf "\n  \033[1mOAuth 2.1 Simulator is up\033[0m\n\n"
	@printf "    \033[1mStart here\033[0m      $(CLIENT)        the client app and its 13 labs\n"
	@printf "    IdP + AS        $(AS)        login, consent, tokens, policy switches\n"
	@printf "    Resource API    $(API)        the protected data\n"
	@printf "    Live trace      $(AS)/trace  watch the protocol decide\n\n"
	@printf "    Sign in as      \033[1malice / wonderland\033[0m   or   bob / builder\n"
	@printf "    Developer guide dev-guide/dev-guide.pdf\n\n"

## ---------------------------------------------------------------- Docs

guide: ## Rebuild dev-guide/dev-guide.pdf from its HTML source
	@./dev-guide/build-guide.sh

guide-pkce: ## Rebuild dev-guide/dev-guide-pkce.pdf, the beginner's PKCE companion
	@./dev-guide/build-pkce-guide.sh

guides: guide guide-pkce ## Rebuild both books

cover: ## Re-export dev-guide/cover.png (the cover as a standalone image)
	@./dev-guide/export-cover.py

clean: ## Stop everything and remove local run artifacts
	-$(COMPOSE) down --remove-orphans
	-./scripts/run-local.sh stop
	rm -rf .run

help: ## Show this help
	@printf "\n  \033[1mOAuth 2.1 + PKCE Simulator\033[0m\n\n"
	@grep -hE '^[a-z][a-z-]*( [a-z-]+)?:.*?## ' $(MAKEFILE_LIST) \
	  | sort \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "    \033[36m%-16s\033[0m %s\n", $$1, $$2}'
	@printf "\n  \033[2mNo Docker? Use 'make local'. Everything runs on Node core modules with zero npm dependencies.\033[0m\n\n"
