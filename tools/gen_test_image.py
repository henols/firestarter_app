"""
Deterministic full-size pseudo-random image generator for bench validation.

Replaces the non-deterministic /dev/urandom approach from write_test.sh with a
reproducible random.Random(seed) equivalent. A fixed (size_bytes, seed) pair
always produces byte-identical output, enabling a trustworthy SHA-256 oracle for
the A->B write-cycle proof (Phase 82 D-03/D-04).

CLI usage:
    python tools/gen_test_image.py <size_bytes> <seed> <output_path>

    size_bytes  -- exact number of bytes to generate (from chip electrical.size_bytes)
    seed        -- integer seed (1 = image A, 2 = image B by convention)
    output_path -- path to write the binary image

Prints the SHA-256 hex digest of the generated image to stdout.  This is the
oracle value to record in EVIDENCE.json as sha256_image_A / sha256_image_B.

Storage convention (D-04):
    /tmp/firestarter_bench_p82/<chip>_img_A.bin  (seed=1)
    /tmp/firestarter_bench_p82/<chip>_img_B.bin  (seed=2)
"""

import hashlib
import random
import sys
from pathlib import Path


def generate_image(size_bytes: int, seed: int) -> bytes:
    """Return exactly size_bytes of deterministic pseudo-random data.

    The same (size_bytes, seed) pair always produces byte-identical output.
    Distinct seeds over the same size produce different output, so image A
    (seed=1) != image B (seed=2) — making the A->B SHA oracle non-vacuous.

    The output is guaranteed to be non-trivial: neither all-0xFF nor all-0x00
    for any realistic size (the birthday probability of a uniform 256-symbol
    stream being constant over >=128 bytes is negligible).

    Args:
        size_bytes: Number of bytes to generate.
        seed: Integer seed for the RNG.

    Returns:
        Bytes of length size_bytes.
    """
    rng = random.Random(seed)
    return bytes(rng.randint(0, 255) for _ in range(size_bytes))


def main() -> None:
    """CLI entry point: size_bytes seed output_path -> writes file, prints SHA-256."""
    if len(sys.argv) != 4:
        print(
            f"Usage: {sys.argv[0]} <size_bytes> <seed> <output_path>",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        size_bytes = int(sys.argv[1])
        seed = int(sys.argv[2])
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    output_path = Path(sys.argv[3])
    output_path.parent.mkdir(parents=True, exist_ok=True)

    data = generate_image(size_bytes, seed)
    output_path.write_bytes(data)

    digest = hashlib.sha256(data).hexdigest()
    print(digest)


if __name__ == "__main__":
    main()
