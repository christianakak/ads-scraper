"""Unit tests for VerticalRegistry."""

import pytest

from core.registry import VerticalRegistry


@pytest.fixture(autouse=True)
def clear_registry():
    """Ensure each test starts with a clean registry."""
    VerticalRegistry.clear()
    yield
    VerticalRegistry.clear()


class TestVerticalRegistry:
    def test_register_and_get(self):
        VerticalRegistry.register(
            vertical="test_vertical",
            collectors=[],
            analyzers=[],
            rules_path="some/path",
            rules_version="1.0.0",
        )
        config = VerticalRegistry.get("test_vertical")
        assert config["rules_version"] == "1.0.0"
        assert config["rules_path"] == "some/path"
        assert config["collectors"] == []
        assert config["analyzers"] == []

    def test_get_unregistered_raises(self):
        with pytest.raises(ValueError, match="not registered"):
            VerticalRegistry.get("nonexistent")

    def test_list_verticals(self):
        VerticalRegistry.register("alpha", [], [], "path/a", "1.0.0")
        VerticalRegistry.register("beta", [], [], "path/b", "1.0.0")
        verticals = VerticalRegistry.list_verticals()
        assert "alpha" in verticals
        assert "beta" in verticals
        assert len(verticals) == 2

    def test_re_register_overwrites(self):
        VerticalRegistry.register("v", [], [], "old_path", "1.0.0")
        VerticalRegistry.register("v", [], [], "new_path", "2.0.0")
        config = VerticalRegistry.get("v")
        assert config["rules_path"] == "new_path"
        assert config["rules_version"] == "2.0.0"

    def test_clear_removes_all(self):
        VerticalRegistry.register("v", [], [], "path", "1.0.0")
        VerticalRegistry.clear()
        assert VerticalRegistry.list_verticals() == []
