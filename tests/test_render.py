import numpy as np

from blue_skies.color import linear_rgb_to_oklab
from blue_skies.render import PreparedFrame, RenderSettings, duration_frames, interpolate


def _solid(value: float) -> PreparedFrame:
    image = np.full((4, 4, 3), value, dtype=np.float32)
    zero = np.zeros_like(image)
    return PreparedFrame(base_lab=linear_rgb_to_oklab(image), mid=zero, fine=zero)


def test_interpolation_preserves_endpoints() -> None:
    first = _solid(0.1)
    second = _solid(0.8)
    np.testing.assert_allclose(interpolate(first, second, 0.0), 0.1, atol=2e-5)
    np.testing.assert_allclose(interpolate(first, second, 1.0), 0.8, atol=2e-5)


def test_adaptive_duration_is_bounded_and_monotonic() -> None:
    settings = RenderSettings(fps=60, min_duration=0.5, max_duration=1.0)
    counts = duration_frames((0.0, 1.0, 2.0, 3.0), settings)
    assert counts == tuple(sorted(counts))
    assert counts[0] == 30
    assert counts[-1] == 60
