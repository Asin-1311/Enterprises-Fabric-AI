"""Full adapters: the specified server stack behind the ports (deploy/compose.yaml).

Each module imports its client library lazily, so the lite profile and the default test
run never need requirements-full.txt.
"""
