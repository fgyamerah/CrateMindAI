.PHONY: inventory docs-audit clean-generated

inventory:
	python3 tools/static_analysis/generate_repo_inventory.py

docs-audit: inventory
	@echo "Generated docs are in docs/generated/"
	@ls -lh docs/generated/

clean-generated:
	rm -f docs/generated/*.md
