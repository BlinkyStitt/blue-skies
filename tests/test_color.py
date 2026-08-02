import numpy as np

from blue_skies.color import (
    FloatImage,
    linear_rgb_to_oklab,
    linear_to_bt709,
    linear_to_srgb,
    oklab_to_linear_rgb,
    smootherstep,
    srgb_to_linear,
)


def test_srgb_round_trip() -> None:
    source = np.linspace(0.0, 1.0, 301, dtype=np.float32).reshape(1, 301, 1)
    reconstructed = linear_to_srgb(srgb_to_linear(source))
    np.testing.assert_allclose(reconstructed, source, atol=2e-6)


def test_oklab_round_trip() -> None:
    rng = np.random.default_rng(7)
    source: FloatImage = np.asarray(rng.random((11, 13, 3)), dtype=np.float32)
    reconstructed = oklab_to_linear_rgb(linear_rgb_to_oklab(source))
    np.testing.assert_allclose(reconstructed, source, atol=2e-5)


def test_smootherstep_endpoints_and_midpoint() -> None:
    assert smootherstep(0.0) == 0.0
    assert smootherstep(0.5) == 0.5
    assert smootherstep(1.0) == 1.0
    assert smootherstep(-1.0) == 0.0
    assert smootherstep(2.0) == 1.0


def test_bt709_transfer_endpoints_and_breakpoint() -> None:
    source: FloatImage = np.asarray([0.0, 0.018, 1.0], dtype=np.float32)
    encoded = linear_to_bt709(source)
    np.testing.assert_allclose(encoded, [0.0, 0.08124795, 1.0], atol=2e-6)
