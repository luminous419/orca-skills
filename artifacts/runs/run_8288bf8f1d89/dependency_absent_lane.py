"""Dependency-absent lane: block `langgraph` at import time and run the engine modules."""
import sys, unittest

class Blocker:
    def find_module(self, name, path=None): return self.find_spec(name, path)
    def find_spec(self, name, path=None, target=None):
        if name == "langgraph" or name.startswith("langgraph."):
            raise ImportError(f"blocked: {name}")
        return None

for mod in [m for m in list(sys.modules) if m == "langgraph" or m.startswith("langgraph.")]:
    del sys.modules[mod]
sys.meta_path.insert(0, Blocker())
sys.path.insert(0, ".")

MODULES = [
    "scripts.test_deterministic_workflow_adapters",
    "scripts.test_deterministic_workflow_contracts",
    "scripts.test_deterministic_workflow_graph",
    "scripts.test_deterministic_workflow_launcher",
    "scripts.test_deterministic_workflow_malformed",
    "scripts.test_deterministic_workflow_recovery",
    "scripts.test_deterministic_workflow_ownership",
    "scripts.test_deterministic_workflow_round2",
    "scripts.test_workflow_control_plane",
]
def main():
    suite = unittest.TestSuite()
    loader = unittest.TestLoader()
    for name in MODULES:
        suite.addTests(loader.loadTestsFromName(name))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    print(f"LANE errors={len(result.errors)} failures={len(result.failures)} skipped={len(result.skipped)}")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
