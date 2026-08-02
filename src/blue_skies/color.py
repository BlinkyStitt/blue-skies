from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

FloatImage = NDArray[np.float32]


def srgb_to_linear(image: FloatImage) -> FloatImage:
    """Convert normalized sRGB values to linear-light RGB."""
    image = np.asarray(image, dtype=np.float32)
    return np.where(
        image <= 0.04045,
        image / 12.92,
        ((image + 0.055) / 1.055) ** 2.4,
    ).astype(np.float32, copy=False)


def linear_to_srgb(image: FloatImage) -> FloatImage:
    """Convert linear-light RGB values to normalized sRGB."""
    image = np.maximum(np.asarray(image, dtype=np.float32), 0.0)
    return np.where(
        image <= 0.0031308,
        image * 12.92,
        1.055 * np.power(image, 1.0 / 2.4) - 0.055,
    ).astype(np.float32, copy=False)


def linear_to_bt709(image: FloatImage) -> FloatImage:
    """Apply the BT.709 opto-electronic transfer function to linear RGB."""
    image = np.maximum(np.asarray(image, dtype=np.float32), 0.0)
    return np.where(
        image < 0.018,
        image * 4.5,
        1.099 * np.power(image, 0.45) - 0.099,
    ).astype(np.float32, copy=False)


def linear_rgb_to_oklab(image: FloatImage) -> FloatImage:
    """Convert linear sRGB to OKLab using Bjorn Ottosson's reference matrices."""
    rgb = np.asarray(image, dtype=np.float32)
    red, green, blue = np.moveaxis(rgb, -1, 0)

    light = 0.4122214708 * red + 0.5363325363 * green + 0.0514459929 * blue
    medium = 0.2119034982 * red + 0.6806995451 * green + 0.1073969566 * blue
    short = 0.0883024619 * red + 0.2817188376 * green + 0.6299787005 * blue

    light_root = np.cbrt(light)
    medium_root = np.cbrt(medium)
    short_root = np.cbrt(short)

    result = np.empty_like(rgb)
    result[..., 0] = (
        0.2104542553 * light_root + 0.7936177850 * medium_root - 0.0040720468 * short_root
    )
    result[..., 1] = (
        1.9779984951 * light_root - 2.4285922050 * medium_root + 0.4505937099 * short_root
    )
    result[..., 2] = (
        0.0259040371 * light_root + 0.7827717662 * medium_root - 0.8086757660 * short_root
    )
    return result


def oklab_to_linear_rgb(image: FloatImage) -> FloatImage:
    """Convert OKLab to linear sRGB using Bjorn Ottosson's reference matrices."""
    lab = np.asarray(image, dtype=np.float32)
    lightness, green_red, blue_yellow = np.moveaxis(lab, -1, 0)

    light_root = lightness + 0.3963377774 * green_red + 0.2158037573 * blue_yellow
    medium_root = lightness - 0.1055613458 * green_red - 0.0638541728 * blue_yellow
    short_root = lightness - 0.0894841775 * green_red - 1.2914855480 * blue_yellow

    light = light_root**3
    medium = medium_root**3
    short = short_root**3

    result = np.empty_like(lab)
    result[..., 0] = 4.0767416621 * light - 3.3077115913 * medium + 0.2309699292 * short
    result[..., 1] = -1.2684380046 * light + 2.6097574011 * medium - 0.3413193965 * short
    result[..., 2] = -0.0041960863 * light - 0.7034186147 * medium + 1.7076147010 * short
    return result


def smootherstep(value: float | FloatImage) -> float | FloatImage:
    """Fifth-order easing with zero first and second derivatives at both ends."""
    clipped = np.clip(value, 0.0, 1.0)
    result = clipped * clipped * clipped * (clipped * (clipped * 6.0 - 15.0) + 10.0)
    if np.isscalar(value):
        return float(result)
    return np.asarray(result, dtype=np.float32)
