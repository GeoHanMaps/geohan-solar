import pytest
from unittest.mock import patch, MagicMock
from app.services import access, cache


@pytest.fixture(autouse=True)
def no_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "_CACHE_DIR", tmp_path)


def _make_mock_graph(node_lon, node_lat):
    """networkx graph simülatörü — tek düğüm."""
    mock_G = MagicMock()
    mock_G.nodes.return_value = [(0, {"x": node_lon, "y": node_lat})]
    mock_G.nodes.__iter__ = lambda self: iter([(0, {"x": node_lon, "y": node_lat})])
    # G.nodes(data=True) için
    mock_G.nodes.return_value = iter([(0, {"x": node_lon, "y": node_lat})])
    mock_G.nodes = MagicMock()
    mock_G.nodes.return_value = [(0, {"x": node_lon, "y": node_lat})]

    class FakeNodes:
        def __init__(self, data):
            self._data = data

        def __iter__(self):
            return iter(self._data)

        def __call__(self, **kwargs):
            return self

    mock_G.nodes = FakeNodes([(0, {"x": node_lon, "y": node_lat})])
    return mock_G


class TestNearestRoadKm:
    def test_returns_float(self):
        G = _make_mock_graph(32.4932, 37.8746)
        with patch("app.services.access.ox.graph_from_point", return_value=G):
            d = access.nearest_road_km(37.8746, 32.4932)
        assert isinstance(d, float)

    def test_nearby_road_small_distance(self):
        # Yol neredeyse aynı koordinatta
        G = _make_mock_graph(32.49325, 37.87465)
        with patch("app.services.access.ox.graph_from_point", return_value=G):
            d = access.nearest_road_km(37.8746, 32.4932)
        assert d < 0.5

    def test_returns_99_on_error(self):
        with patch("app.services.access.ox.graph_from_point",
                   side_effect=Exception("no road")):
            d = access.nearest_road_km(37.87, 32.49)
        assert d == 99.0

    def test_southern_hemisphere(self):
        G = _make_mock_graph(25.0, -34.0)
        with patch("app.services.access.ox.graph_from_point", return_value=G):
            d = access.nearest_road_km(-34.0, 25.0)
        assert isinstance(d, float)
