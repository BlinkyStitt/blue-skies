from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from .color import linear_rgb_to_oklab
from .media import Source, decode_square, resize_float

FloatMatrix = NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class Tour:
    indices: tuple[int, ...]
    distances_to_next: tuple[float, ...]

    @property
    def total_distance(self) -> float:
        return sum(self.distances_to_next)


def descriptor_for(source: Source) -> NDArray[np.float64]:
    linear = decode_square(source.path, 64)
    lab = linear_rgb_to_oklab(linear)
    spatial = resize_float(lab, 4).astype(np.float64)
    spatial[..., 0] *= 1.4
    spatial[..., 1:] *= 3.0

    lightness = lab[..., 0].astype(np.float64)
    mean = lab.mean(axis=(0, 1), dtype=np.float64) * np.array([2.0, 4.0, 4.0])
    spread = lab.std(axis=(0, 1), dtype=np.float64) * np.array([2.0, 3.0, 3.0])
    dx = np.diff(lightness, axis=1)
    dy = np.diff(lightness, axis=0)
    texture = np.array(
        [
            np.sqrt(np.mean(dx * dx) + np.mean(dy * dy)) * 5.0,
            np.std(lightness) * 2.0,
        ],
        dtype=np.float64,
    )
    return np.concatenate((spatial.ravel(), mean, spread, texture))


def pairwise_distances(descriptors: NDArray[np.float64]) -> FloatMatrix:
    differences = descriptors[:, None, :] - descriptors[None, :, :]
    return np.sqrt(np.sum(differences * differences, axis=2, dtype=np.float64))


def cycle_length(order: list[int] | tuple[int, ...], distances: FloatMatrix) -> float:
    return float(sum(distances[order[i], order[(i + 1) % len(order)]] for i in range(len(order))))


def _nearest_neighbor(start: int, distances: FloatMatrix) -> list[int]:
    count = distances.shape[0]
    remaining = set(range(count))
    remaining.remove(start)
    order = [start]
    while remaining:
        current = order[-1]
        next_index = min(
            remaining, key=lambda candidate: (distances[current, candidate], candidate)
        )
        order.append(next_index)
        remaining.remove(next_index)
    return order


def _two_opt(order: list[int], distances: FloatMatrix) -> list[int]:
    count = len(order)
    current_length = cycle_length(order, distances)
    while True:
        best_length = current_length
        best_order: list[int] | None = None
        for left in range(count - 1):
            for right in range(left + 1, count):
                if left == 0 and right == count - 1:
                    continue
                candidate = order.copy()
                candidate[left : right + 1] = reversed(candidate[left : right + 1])
                candidate_length = cycle_length(candidate, distances)
                if candidate_length < best_length - 1e-12:
                    best_length = candidate_length
                    best_order = candidate
        if best_order is None:
            return order
        order = best_order
        current_length = best_length


def optimize_cycle(descriptors: NDArray[np.float64], anchor: int = 0) -> Tour:
    distances = pairwise_distances(descriptors)
    candidates: list[list[int]] = []
    for start in range(len(descriptors)):
        candidates.append(_nearest_neighbor(start, distances))
    order = min(candidates, key=lambda candidate: cycle_length(candidate, distances))
    order = _two_opt(order, distances)

    anchor_position = order.index(anchor)
    order = order[anchor_position:] + order[:anchor_position]
    if len(order) > 2 and order[1] > order[-1]:
        order = [order[0], *reversed(order[1:])]

    edge_distances = tuple(
        float(distances[order[index], order[(index + 1) % len(order)]])
        for index in range(len(order))
    )
    return Tour(indices=tuple(order), distances_to_next=edge_distances)


def build_tour(sources: list[Source]) -> tuple[Tour, NDArray[np.float64]]:
    descriptors = np.stack([descriptor_for(source) for source in sources])
    return optimize_cycle(descriptors), descriptors
