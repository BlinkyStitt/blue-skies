import numpy as np
import pytest

from blue_skies.color import linear_rgb_to_oklab
from blue_skies.render import (
    PreparedFrame,
    RenderSettings,
    duration_frames,
    interpolate,
    spline_weights,
)


def _solid(value: float) -> PreparedFrame:
    image = np.full((4, 4, 3), value, dtype=np.float32)
    zero = np.zeros_like(image)
    return PreparedFrame(base_lab=linear_rgb_to_oklab(image), mid=zero, fine=zero)


def test_spline_weights_are_normalized_and_nonnegative() -> None:
    for position in np.linspace(0.0, 1.0, 21):
        weights = spline_weights(float(position))
        assert all(weight >= 0.0 for weight in weights)
        assert sum(weights) == pytest.approx(1.0)


def test_interpolation_is_continuous_and_keeps_moving_through_knot() -> None:
    left_controls = (_solid(0.1), _solid(0.2), _solid(0.3), _solid(0.4))
    right_controls = (_solid(0.2), _solid(0.3), _solid(0.4), _solid(0.5))

    knot_from_left = interpolate(left_controls, 1.0)
    knot_from_right = interpolate(right_controls, 0.0)
    np.testing.assert_allclose(knot_from_left, knot_from_right, atol=2e-5)

    before = interpolate(left_controls, 0.999)
    after = interpolate(right_controls, 0.001)
    assert float(np.mean(knot_from_left - before)) > 0.0
    assert float(np.mean(after - knot_from_right)) > 0.0
    np.testing.assert_allclose(
        knot_from_left - before,
        after - knot_from_right,
        rtol=0.02,
        atol=2e-6,
    )


def test_adaptive_duration_is_bounded_and_monotonic() -> None:
    settings = RenderSettings(fps=60, min_duration=0.5, max_duration=1.0)
    counts = duration_frames((0.0, 1.0, 2.0, 3.0), settings)
    assert counts == tuple(sorted(counts))
    assert counts[0] == 30
    assert counts[-1] == 60
