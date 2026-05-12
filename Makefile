.PHONY: build deploy restart teardown dev observe observe-teardown

HELM_MONITORING_NS := monitoring

build: ## Build all three app images into minikube's docker daemon
	eval $$(minikube docker-env) && \
	docker build -t edu-oe/backend-ts:latest -f apps/backend-ts/Dockerfile . && \
	docker build -t edu-oe/backend-py:latest apps/backend-py && \
	docker build -t edu-oe/frontend:latest -f apps/frontend/Dockerfile .

restart: ## Force rollout restart of all app deployments (use after make build)
	kubectl rollout restart deployment/backend-ts deployment/backend-py deployment/frontend -n edu-oe

deploy: ## Apply all k8s manifests
	kubectl apply -f k8s/namespace.yaml
	kubectl apply -f k8s/

teardown: ## Remove all k8s resources
	kubectl delete -f k8s/

dev: ## Local dev (docker-compose infra + pnpm dev)
	docker compose up -d && pnpm dev

observe: ## Deploy Prometheus, Grafana, and Loki into the monitoring namespace
	helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
	helm repo add grafana https://grafana.github.io/helm-charts
	helm repo update
	kubectl create namespace $(HELM_MONITORING_NS) --dry-run=client -o yaml | kubectl apply -f -
	kubectl apply -f k8s/grafana-dashboard.yaml
	kubectl apply -f k8s/loki-datasource.yaml
	helm upgrade --install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
		--namespace $(HELM_MONITORING_NS) \
		--values k8s/helm/kube-prometheus-stack-values.yaml \
		--wait
	helm upgrade --install loki grafana/loki \
		--namespace $(HELM_MONITORING_NS) \
		--values k8s/helm/loki-values.yaml \
		--wait
	helm upgrade --install alloy grafana/alloy \
		--namespace $(HELM_MONITORING_NS) \
		--values k8s/helm/alloy-values.yaml \
		--wait
	kubectl apply -f k8s/monitoring.yaml

observe-teardown: ## Remove Prometheus, Grafana, and Loki
	helm uninstall kube-prometheus-stack --namespace $(HELM_MONITORING_NS) || true
	helm uninstall loki --namespace $(HELM_MONITORING_NS) || true
	helm uninstall alloy --namespace $(HELM_MONITORING_NS) || true
	helm uninstall promtail --namespace $(HELM_MONITORING_NS) || true
	helm uninstall loki-stack --namespace $(HELM_MONITORING_NS) || true
	kubectl delete -f k8s/monitoring.yaml || true
	kubectl delete -f k8s/grafana-dashboard.yaml || true
	kubectl delete -f k8s/loki-datasource.yaml || true
