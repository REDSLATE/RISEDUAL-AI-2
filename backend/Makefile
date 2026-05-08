.PHONY: compression-baseline compression-dry-run

compression-baseline:
	@python -m scripts.compression_ci_gate \
		--baseline-tag $(BASE) \
		--candidate-tag $(CAND) \
		--window-days 30 \
		--fail-on-breach

compression-dry-run:
	@python -m scripts.compression_ci_gate --dry-run
