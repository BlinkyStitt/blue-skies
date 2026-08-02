import numpy as np

from blue_skies.ordering import cycle_length, optimize_cycle, pairwise_distances


def test_optimized_cycle_is_a_deterministic_permutation() -> None:
    descriptors = np.array(
        [[0.0, 0.0], [4.0, 0.0], [4.0, 4.0], [0.0, 4.0], [2.0, 0.1]],
        dtype=np.float64,
    )
    first = optimize_cycle(descriptors)
    second = optimize_cycle(descriptors)

    assert first == second
    assert first.indices[0] == 0
    assert sorted(first.indices) == list(range(len(descriptors)))


def test_optimized_cycle_does_not_exceed_input_order() -> None:
    descriptors = np.array(
        [[0.0], [10.0], [1.0], [9.0], [2.0], [8.0]],
        dtype=np.float64,
    )
    distances = pairwise_distances(descriptors)
    optimized = optimize_cycle(descriptors)
    assert optimized.total_distance <= cycle_length(list(range(len(descriptors))), distances)
